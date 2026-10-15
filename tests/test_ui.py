"""Smoke-Tests der Streamlit-Seiten: laufen ohne Fehler, Ja/Nein wirken auf die Datenbank."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from bewerbungsagent import ablauf  # noqa: E402
from bewerbungsagent.config import lade_profil, speichere_profil  # noqa: E402
from bewerbungsagent.db import Speicher  # noqa: E402
from bewerbungsagent.models import Job, Score, Vorgang  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SEITEN = ["seiten/schritt_fuer_schritt.py", "seiten/profil.py"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "SMTP_HOST"):
        monkeypatch.delenv(name, raising=False)
    gestartet: list[tuple[str, ...]] = []
    monkeypatch.setattr("bewerbungsagent.hintergrund.starte", lambda *a: gestartet.append(a) or 0)

    (tmp_path / "config").mkdir()
    pfad = tmp_path / "config" / "profil.yaml"
    pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(pfad)
    (tmp_path / "unterlagen").mkdir()
    (tmp_path / "unterlagen" / "lebenslauf.txt").write_text("Lebenslauf", encoding="utf-8")
    profil.unterlagen.lebenslauf = "unterlagen/lebenslauf.txt"
    profil.unterlagen.zeugnisse = []
    speichere_profil(profil, pfad)

    def neue_app() -> AppTest:
        return AppTest.from_file(str(REPO / "app.py"), default_timeout=30)

    return neue_app, profil, gestartet


def _befuelle(profil) -> None:
    with Speicher() as db:
        db.speichere_jobs([
            Job(ref="j1", titel="Python Entwickler", arbeitgeber="Firma Eins", ort="München",
                beschreibung="Bewerbung an jobs@eins.de", externe_url="https://eins.de/job"),
            Job(ref="j2", titel="Backend Entwickler", arbeitgeber="Firma Zwei", ort="München",
                externe_url="https://zwei.de/job"),
            Job(ref="j3", titel="Data Engineer", arbeitgeber="Firma Drei", ort="München",
                externe_url="https://drei.de/job"),
        ])
        for ref, wert in (("j1", 80), ("j2", 75), ("j3", 70)):
            db.speichere_score(Score(ref=ref, gesamt=wert, passung=wert, sentiment=50, treffer=["Python"]))
        ablauf.vorbereiten(db, profil, "j1", mit_llm=False, probelauf=False, link_aufloesen=False)
        ablauf.vorbereiten(db, profil, "j2", mit_llm=False, probelauf=False, link_aufloesen=False)
        db.setze_vorgang(Vorgang(ref="j3", status="fehlgeschlagen", kanal="formular",
                                 anschreiben="Text", hinweis="Captcha"))


def _ohne_fehler(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]


def test_alle_seiten_ohne_daten(app):
    neue_app, _, _ = app
    at = neue_app().run()
    _ohne_fehler(at)
    for seite in SEITEN:
        at.switch_page(seite).run()
        _ohne_fehler(at)


def test_alle_seiten_mit_daten(app):
    neue_app, profil, _ = app
    _befuelle(profil)
    at = neue_app().run()
    _ohne_fehler(at)
    assert any("Deine Entscheidung (2)" in h.value for h in at.subheader)
    for seite in SEITEN:
        at.switch_page(seite).run()
        _ohne_fehler(at)


def test_ja_gibt_frei_und_startet_versand(app):
    neue_app, profil, gestartet = app
    _befuelle(profil)
    at = neue_app().run()
    at.button(key="cockpit_ja_j1").click().run()
    _ohne_fehler(at)
    with Speicher() as db:
        assert db.vorgang("j1").status == "freigegeben"
    assert gestartet and gestartet[-1][0] == "senden"


def test_nein_lehnt_ab(app):
    neue_app, profil, _ = app
    _befuelle(profil)
    at = neue_app().run()
    at.button(key="cockpit_nein_j2").click().run()
    _ohne_fehler(at)
    with Speicher() as db:
        assert db.vorgang("j2").status == "abgelehnt"


def test_schritt_fuer_schritt_auswahl_startet_vorbereitung(app):
    neue_app, profil, gestartet = app
    _befuelle(profil)
    with Speicher() as db:
        db.speichere_jobs([Job(ref="j4", titel="Python Dev", arbeitgeber="Vier GmbH", externe_url="https://vier.de")])
        db.speichere_score(Score(ref="j4", gesamt=90, passung=90, sentiment=50))
    at = neue_app().run()
    at.switch_page("seiten/schritt_fuer_schritt.py").run()
    at.session_state["sfs_schritt"] = 2
    at.session_state["sfs_umfang"] = "alle"
    at.run()
    _ohne_fehler(at)
    at.checkbox(key="sfs_sel_j4").check().run()
    weiter = next(b for b in at.button if b.label.startswith("Weiter: 1"))
    weiter.click().run()
    _ohne_fehler(at)
    assert gestartet[-1][:2] == ("vorbereiten", "j4")
    with Speicher() as db:
        assert db.vorgang("j4").status == "vorbereitung"


def test_assistent_fuehrt_durch_die_einrichtung(tmp_path, monkeypatch):
    from bewerbungsagent import config

    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "SMTP_HOST",
                 "BEWERBUNGSAGENT_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "BENUTZER_PFAD", tmp_path / "gibt-es-nicht.yaml")
    gestartet: list[tuple[str, ...]] = []
    monkeypatch.setattr("bewerbungsagent.hintergrund.starte", lambda *a: gestartet.append(a) or 0)

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=30).run()
    _ohne_fehler(at)
    assert at.title[0].value == "Willkommen!"
    at.text_input(key="assistent_vorname").input("Erika")
    at.text_input(key="assistent_nachname").input("Muster")
    at.text_input(key="assistent_email").input("erika@example.org")
    at.text_input(key="assistent_ort").input("Köln")
    next(b for b in at.button if b.label == "Weiter").click().run()
    _ohne_fehler(at)
    assert lade_profil(pruefen=False).person.vorname == "Erika"

    at.text_area(key="assistent_was").input("Buchhalterin\nSachbearbeiterin")
    next(b for b in at.button if b.label == "Weiter").click().run()
    at.button(key="assistent_w3").click().run()  # Lebenslauf spaeter
    at.button(key="assistent_w4").click().run()  # E-Mail-Konto ueberspringen
    _ohne_fehler(at)
    at.button(key="assistent_fertig").click().run()
    _ohne_fehler(at)

    profil = lade_profil()
    assert profil.suche.was == ["Buchhalterin", "Sachbearbeiterin"] and profil.suche.wo == "Köln"
    assert profil.automatisierung.aktiv and profil.automatisierung.modus == "automatisch"
    assert gestartet[-1][0] == "automatik"
    assert any("Startklar" in m.value for m in at.markdown)  # Lebenslauf fehlt noch


def test_rueckmeldung_auf_heute(app):
    neue_app, profil, _ = app
    _befuelle(profil)
    with Speicher() as db:
        db.aktualisiere_vorgang("j1", status="abgeschickt", gesendet_am="2026-10-01T08:00:00+00:00")
    at = neue_app().run()
    _ohne_fehler(at)
    at.pills(key="heute_rm_j1").set_value("einladung").run()
    _ohne_fehler(at)
    with Speicher() as db:
        assert db.vorgang("j1").rueckmeldung == "einladung"
    at.button(key="heute_nachweis").click().run()
    _ohne_fehler(at)


def test_suchbegriff_vorschlaege_gebuendelt(app):
    from bewerbungsagent import lernen

    neue_app, profil, _ = app
    with Speicher() as db:
        lernen.schlage_suchbegriff_vor(db, "Datenanalyst", 3)
        lernen.schlage_suchbegriff_vor(db, "BI-Entwickler", 2)
    at = neue_app().run()
    _ohne_fehler(at)
    at.pills(key="cockpit_begriffe").set_value(["Datenanalyst"]).run()
    at.button(key="cockpit_begriffe_ok").click().run()
    _ohne_fehler(at)
    assert "Datenanalyst" in lade_profil().suche.was and "BI-Entwickler" not in lade_profil().suche.was
    with Speicher() as db:
        assert db.vorschlaege("offen") == []


def test_verworfene_dialog_eingaben_werden_nicht_gesendet(app):
    """Lesen & bearbeiten öffnen, Text/Weg ändern, ohne Speichern schließen: Ja sendet das Gespeicherte."""
    neue_app, profil, gestartet = app
    _befuelle(profil)
    with Speicher() as db:
        vorher = db.vorgang("j1")
    at = neue_app().run()
    at.button(key="cockpit_lesen_j1").click().run()
    _ohne_fehler(at)
    # Wie im Browser: die Eingabe im (danach geschlossenen) Dialog reist mit dem Klick auf Ja mit
    at.text_area(key="cockpit_text_j1").input("NICHT GESPEICHERT")
    at.button(key="cockpit_ja_j1").click().run()
    _ohne_fehler(at)
    with Speicher() as db:
        v = db.vorgang("j1")
    assert v.status == "freigegeben" and v.anschreiben == vorher.anschreiben and v.kanal == vorher.kanal
    assert gestartet[-1][0] == "senden"


def test_rueckmeldung_folgt_dem_postfach(app):
    neue_app, profil, _ = app
    _befuelle(profil)
    with Speicher() as db:
        db.aktualisiere_vorgang("j1", status="abgeschickt", gesendet_am="2026-10-01T08:00:00+00:00",
                                rueckmeldung="eingangsbestaetigung")
    at = neue_app().run()
    assert at.pills(key="heute_rm_j1").value == "eingangsbestaetigung"
    with Speicher() as db:  # z. B. vom Postfach-Abgleich erkannt
        ablauf.setze_rueckmeldung(db, "j1", "einladung")
    at.run()
    _ohne_fehler(at)
    assert at.pills(key="heute_rm_j1").value == "einladung"


def test_ausweg_mail_nur_bei_sicherer_adresse(app, monkeypatch):
    neue_app, profil, _ = app
    _befuelle(profil)
    monkeypatch.setattr("bewerbungsagent.bewerben.mail.smtp_eingerichtet", lambda *a, **k: True)
    with Speicher() as db:
        db.speichere_jobs([Job(ref="j3", titel="Data Engineer", arbeitgeber="Firma Drei", ort="München",
                               beschreibung=("Kundenservice erreichen Sie unter vertrieb@drei.de. "
                                            "Bitte nutzen Sie ausschließlich unser Online-Portal."),
                               externe_url="https://drei.de/job")])
    at = neue_app().run()
    _ohne_fehler(at)
    assert not [b for b in at.button if b.key == "cockpit_mail_j3"]
    with Speicher() as db:
        db.speichere_jobs([Job(ref="j3", titel="Data Engineer", arbeitgeber="Firma Drei", ort="München",
                               beschreibung="Ihre Bewerbung senden Sie bitte an karriere@drei.de.",
                               externe_url="https://drei.de/job")])
    at.run()
    assert [b for b in at.button if b.key == "cockpit_mail_j3"]


def test_kaputtes_profil_zeigt_fehler_statt_assistent(app):
    neue_app, _, _ = app
    pfad = Path("config/profil.yaml")
    pfad.write_text(pfad.read_text(encoding="utf-8").replace("person:", "person:\n  telefonnummer: '0123'", 1),
                    encoding="utf-8")
    at = neue_app().run()
    assert not at.exception
    assert not [t for t in at.title if t.value == "Willkommen!"]
    assert any("telefonnummer" in e.value for e in at.error)


def test_assistent_geht_nach_neu_laden_weiter(tmp_path, monkeypatch):
    from bewerbungsagent import config

    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "SMTP_HOST",
                 "BEWERBUNGSAGENT_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "BENUTZER_PFAD", tmp_path / "gibt-es-nicht.yaml")
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=30).run()
    at.text_input(key="assistent_vorname").input("Erika")
    at.text_input(key="assistent_nachname").input("Muster")
    at.text_input(key="assistent_email").input("erika@example.org")
    next(b for b in at.button if b.label == "Weiter").click().run()
    at.text_area(key="assistent_was").input("Buchhalterin")
    next(b for b in at.button if b.label == "Weiter").click().run()
    _ohne_fehler(at)
    # Neue Sitzung (Seite neu geladen): weiter bei Schritt 3 statt der normalen Startseite
    neu = AppTest.from_file(str(REPO / "app.py"), default_timeout=30).run()
    _ohne_fehler(neu)
    assert neu.title[0].value == "Willkommen!" and any(s.value == "Lebenslauf" for s in neu.subheader)


def test_rueckmeldung_bleibt_nach_seitenwechsel(app):
    neue_app, profil, _ = app
    _befuelle(profil)
    with Speicher() as db:
        db.aktualisiere_vorgang("j1", status="abgeschickt", gesendet_am="2026-10-01T08:00:00+00:00",
                                rueckmeldung="absage")
    at = neue_app().run()
    assert at.pills(key="heute_rm_j1").value == "absage"
    at.switch_page("seiten/profil.py").run()
    at.switch_page("seiten/cockpit.py").run()
    _ohne_fehler(at)
    assert at.pills(key="heute_rm_j1").value == "absage"


def test_eintragen_oeffnet_ueber_mich(app):
    neue_app, profil, _ = app
    profil.person.telefon = ""  # Kontaktdaten unvollstaendig -> Startklar zeigt „Eintragen“
    speichere_profil(profil, Path("config/profil.yaml"))
    at = neue_app().run()
    at.switch_page("seiten/profil.py").run()
    at.session_state["tab"] = "Konten"
    at.run()
    at.switch_page("seiten/cockpit.py").run()
    at.button(key="startklar_daten").click().run()
    _ohne_fehler(at)
    assert at.session_state["tab"] == "Über mich"
