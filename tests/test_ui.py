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
SEITEN = ["seiten/schritt_fuer_schritt.py", "seiten/jobs.py", "seiten/lebenslauf.py", "seiten/profil.py"]


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
    # Die alte Fehlerkarte (j3) ist jetzt eine Ja/Nein-Karte „Selbst bewerben“ – keine Hilfe-/Startklar-Bereiche
    assert any("Deine Entscheidung (3)" in h.value for h in at.subheader)
    assert not [h for h in at.subheader if "Hilfe" in h.value]
    assert not [m for m in at.markdown if "Startklar" in m.value or "Automatik" in m.value]
    assert at.button(key="cockpit_selbst_j3").label == "Ja, erledigt"
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
    _ohne_fehler(at)
    # Eine Seite: Suchformular und Treffer zugleich, keine Schritte
    assert at.button(key="sfs_suchen") and not [b for b in at.button if (b.key or "").startswith("sfs_nav_")]
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

    assert any(s.value == "Lebenslauf" for s in at.subheader)  # Schritt 2
    at.button(key="assistent_w3").click().run()  # Lebenslauf spaeter
    at.text_area(key="assistent_was").input("Buchhalterin\nSachbearbeiterin")
    next(b for b in at.button if b.label == "Weiter").click().run()
    at.button(key="assistent_w4").click().run()  # E-Mail-Konto ueberspringen
    _ohne_fehler(at)
    at.button(key="assistent_fertig").click().run()
    _ohne_fehler(at)

    profil = lade_profil()
    assert profil.suche.was == ["Buchhalterin", "Sachbearbeiterin"] and profil.suche.wo == "Köln"
    assert profil.automatisierung.aktiv and profil.automatisierung.modus == "automatisch"
    assert gestartet[-1][0] == "automatik"
    assert any("Ohne Lebenslauf" in m.value for m in at.markdown)  # das Einzige, was noch fehlt


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
    assert any("Einladung zum Gespräch" in m.value for m in at.markdown)  # hervorgehoben
    # Der Nachweis fürs Amt wohnt jetzt unter Einstellungen → Über mich
    at.switch_page("seiten/profil.py").run()
    at.button(key="einstellungen_nachweis").click().run()
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


def test_alte_fehlerkarten_werden_auf_heute_umgeleitet(app, monkeypatch):
    neue_app, profil, _ = app
    _befuelle(profil)
    for name, wert in (("SMTP_HOST", "smtp.example.de"), ("SMTP_BENUTZER", "ich@example.de"),
                       ("SMTP_PASSWORT", "geheim")):
        monkeypatch.setenv(name, wert)
    with Speicher() as db:
        # j3: nur eine Service-Adresse -> nie per E-Mail, sondern Karte „Selbst bewerben“
        db.speichere_jobs([
            Job(ref="j3", titel="Data Engineer", arbeitgeber="Firma Drei", ort="München",
                beschreibung=("Kundenservice erreichen Sie unter vertrieb@drei.de. "
                              "Bitte nutzen Sie ausschließlich unser Online-Portal."), externe_url="https://drei.de/job"),
            Job(ref="j5", titel="Data Analyst", arbeitgeber="Firma Fünf", ort="München",
                beschreibung="Ihre Bewerbung senden Sie bitte an karriere@fuenf.de.", externe_url="https://fuenf.de/job"),
        ])
        db.setze_vorgang(Vorgang(ref="j5", status="fehlgeschlagen", kanal="formular", anschreiben="Text",
                                 hinweis="Keine Bewerbungs-URL hinterlegt."))
    at = neue_app().run()
    _ohne_fehler(at)
    with Speicher() as db:
        j3, j5 = db.vorgang("j3"), db.vorgang("j5")
    # j3 („Captcha“): unklar, ob schon raus – nachfragen statt erneut senden
    assert (j3.status, j3.kanal, j3.hinweis) == ("wartet", "manuell", ablauf.HINWEIS_VIELLEICHT)
    assert (j5.status, j5.kanal, j5.empfaenger) == ("freigegeben", "email", "karriere@fuenf.de")
    assert any("Data Analyst" in m.value and "geht gleich raus" in m.value for m in at.markdown)
    # „Ja, erledigt“ auf der Karte „Selbst bewerben“
    at.button(key="cockpit_selbst_j3").click().run()
    _ohne_fehler(at)
    with Speicher() as db:
        assert db.vorgang("j3").status == "selbst_beworben"


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
    _ohne_fehler(at)
    # Neue Sitzung (z. B. am Handy nach dem Datei-Dialog neu geladen): weiter bei Schritt 2 statt der Startseite
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


def test_ohne_lebenslauf_zeigt_heute_nur_den_weg_dorthin(app):
    neue_app, profil, _ = app
    Path("unterlagen/lebenslauf.txt").unlink()
    at = neue_app().run()
    _ohne_fehler(at)
    assert any("Ohne Lebenslauf" in m.value for m in at.markdown)
    at.switch_page("seiten/lebenslauf.py").run()
    _ohne_fehler(at)
    assert any("fehlt" in w.value for w in at.warning)


def test_lebenslauf_seite_zeigt_datei_und_uebernimmt_was_sie_liest(app, monkeypatch):
    import json

    from bewerbungsagent.scoring import llm

    neue_app, profil, _ = app
    Path("unterlagen/lebenslauf.txt").write_text("Erika Muster\n50667 Köln\nBuchhalterin seit 2015, DATEV",
                                                 encoding="utf-8")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    antwort = {"kurzprofil": "Buchhalterin seit 2015, DATEV.", "jobtitel": ["Buchhalterin", "Finanzbuchhalterin"],
               "ort": "Köln", "faehigkeiten": ["DATEV", "Buchhaltung"], "kontakt": {}}
    monkeypatch.setattr(llm, "chat", lambda modell, **kw: json.dumps(antwort))
    at = neue_app().run()
    at.switch_page("seiten/lebenslauf.py").run()
    _ohne_fehler(at)
    assert any("lebenslauf.txt" in m.value for m in at.markdown)
    assert any("Buchhalterin · Finanzbuchhalterin" in m.value for m in at.markdown)
    gespeichert = lade_profil()
    assert gespeichert.suche.was[:2] == ["Buchhalterin", "Finanzbuchhalterin"] and gespeichert.suche.wo == "Köln"
    assert gespeichert.kurzprofil == "Buchhalterin seit 2015, DATEV."


def test_ueber_mich_ohne_kurzprofil_gehalt_und_stil(app):
    neue_app, _, _ = app
    at = neue_app().run()
    at.switch_page("seiten/profil.py").run()
    _ohne_fehler(at)
    beschriftungen = [w.label for w in (*at.text_input, *at.text_area)]
    assert not [b for b in beschriftungen if "Gehalt" in b or "Kurz über dich" in b or b == "Stil"]
    assert at.button(key="einstellungen_nachweis")


def test_alter_assistenten_stand_blockiert_fertiges_profil_nicht(app):
    neue_app, profil, _ = app
    assert profil.automatisierung.aktiv
    datei = Path("daten/assistent.json")
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text('{"schritt": 2}', encoding="utf-8")
    at = neue_app().run()
    _ohne_fehler(at)
    assert not [t for t in at.title if t.value == "Willkommen!"]
    assert not datei.exists()


def test_suchen_zeigt_weniger_passende_statt_nichts(app):
    neue_app, profil, _ = app
    with Speicher() as db:
        db.speichere_jobs([Job(ref="j6", titel="Quereinstieg", arbeitgeber="Sechs AG", externe_url="https://sechs.de")])
        db.speichere_score(Score(ref="j6", gesamt=30, passung=30, sentiment=50))
    at = neue_app().run()
    at.switch_page("seiten/schritt_fuer_schritt.py").run()
    _ohne_fehler(at)
    assert any("Weniger passende" in h.value for h in at.subheader)
    assert at.checkbox(key="sfs_sel_j6")
    assert [e for e in at.expander if e.label == "Mehr Filter"]  # dort steht auch die Mindest-Passung


def test_vielleicht_karte_kann_nach_pruefung_automatisch_neu_bewerben(app):
    neue_app, profil, gestartet = app
    _befuelle(profil)  # j3: alte Fehlerkarte „Captcha“ -> Karte „vielleicht schon raus“
    at = neue_app().run()
    _ohne_fehler(at)
    assert any(ablauf.HINWEIS_VIELLEICHT in i.value for i in at.info)
    assert not [b for b in at.button if b.key == "cockpit_angaben_j3"]  # kein „Formular neu testen“
    at.button(key="cockpit_neu_bewerben_j3").click().run()
    _ohne_fehler(at)
    with Speicher() as db:
        v = db.vorgang("j3")
    assert (v.status, v.kanal) == ("freigegeben", "formular") and gestartet[-1][0] == "senden"


def test_lebenslauf_seite_zeigt_den_ki_grund_und_eine_probe(app, monkeypatch):
    from bewerbungsagent.scoring import llm

    neue_app, profil, _ = app
    _befuelle(profil)
    with Speicher() as db:  # eine offene Stelle ohne Karte – dafuer schreibt die Probe
        db.speichere_jobs([Job(ref="j7", titel="Finanzbuchhalterin", arbeitgeber="Sieben AG", ort="Köln",
                               beschreibung="DATEV, Excel", externe_url="https://sieben.de/job")])
        db.speichere_score(Score(ref="j7", gesamt=66, passung=66, sentiment=50, treffer=["DATEV"]))
    Path("unterlagen/lebenslauf.txt").write_text(
        "Über mich\nBuchhalterin mit DATEV-Erfahrung.\nFähigkeiten\nDATEV\nPython\nErfahrung\n"
        "Demo GmbH 01/2019 - 12/2023\nBuchhalterin\n50667 Köln", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")

    def kaputt(*a, **k):
        raise llm.LLMNichtVerfuegbar("Error code: 429 - insufficient_quota")

    monkeypatch.setattr(llm, "chat", kaputt)
    monkeypatch.setattr("bewerbungsagent.anschreiben.chat", kaputt)
    at = neue_app().run()
    at.switch_page("seiten/lebenslauf.py").run()
    _ohne_fehler(at)
    assert any("kein Guthaben" in i.value for i in at.info)  # statt „ohne KI“
    assert any("Buchhalterin" in m.value and "Jobtitel" in m.value for m in at.markdown)
    at.button(key="lebenslauf_probe_knopf").click().run()
    _ohne_fehler(at)
    probe = at.session_state["lebenslauf_probe"]
    assert probe["titel"] == "Finanzbuchhalterin" and probe["quelle"] == "vorlage" and "kein Guthaben" in probe["grund"]
    assert "Zuletzt war ich als Buchhalterin bei Demo GmbH tätig." in probe["text"]
