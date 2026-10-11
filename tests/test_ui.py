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
SEITEN = ["seiten/schritt_fuer_schritt.py", "seiten/bewerbungen.py", "seiten/profil.py"]


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
