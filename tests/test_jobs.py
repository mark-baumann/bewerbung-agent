"""Seite „Jobs“: alle gefundenen Stellen mit Passung und Stand."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from bewerbungsagent.config import lade_profil, speichere_profil  # noqa: E402
from bewerbungsagent.db import Speicher  # noqa: E402
from bewerbungsagent.models import Application, Job, Score, Vorgang  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _stellen(db: Speicher) -> None:
    def job(ref, titel, arbeitgeber="Firma", quelle="arbeitsagentur", **kw):
        return Job(ref=ref, titel=titel, arbeitgeber=arbeitgeber, quelle=quelle, ort="München", **kw)

    db.speichere_jobs([
        job("offen", "Produktmanager", "Eins AG", externe_url="https://eins.de/job"),
        job("doppelt", "Produktmanager", "Eins AG", quelle="get-in-it"),  # dieselbe Stelle, andere Jobboerse
        job("heute", "IT-Projektmanager", "Zwei AG"),
        job("beworben", "Product Owner", "Drei AG"),
        job("nein", "Scrum Master", "Vier AG"),
        job("aus", "Zeitarbeit Entwickler", "Fünf AG"),
        job("neu", "Prozessmanager", "Sechs AG"),
        job("feed", "Barista", "Café", quelle="arbeitnow"),  # nur im Feed, von keiner Suche getroffen
    ])
    for ref, wert, aus in (("offen", 82, False), ("doppelt", 60, False), ("heute", 75, False),
                           ("beworben", 70, False), ("nein", 65, False), ("aus", 10, True)):
        db.speichere_score(Score(ref=ref, gesamt=wert, passung=wert, sentiment=50, ausgeschlossen=aus,
                                 ausschlussgrund="Zeitarbeit" if aus else None))
    db.setze_vorgang(Vorgang(ref="heute", status="wartet", kanal="email", anschreiben="Text"))
    db.setze_vorgang(Vorgang(ref="nein", status="abgelehnt"))
    db.speichere_bewerbung(Application(ref="beworben", status="abgeschickt", dry_run=False))
    feed = db.starte_abruf("arbeitnow", "(Feed)", {})
    db.merke_abruf_treffer(feed, ["feed"])


def test_gefundene_jobs_mit_stand_ohne_dubletten_und_feed(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as db:
        _stellen(db)
        jobs = {j["ref"]: j for j in db.gefundene_jobs()}
    assert set(jobs) == {"offen", "heute", "beworben", "nein", "aus", "neu"}
    assert {r: j["stand"] for r, j in jobs.items()} == {
        "offen": "offen", "heute": "heute", "beworben": "beworben", "nein": "abgelehnt", "aus": "ausgeschlossen",
        "neu": "unbewertet"}
    assert jobs["offen"]["anzeige"] == "https://eins.de/job"
    assert jobs["heute"]["anzeige"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/heute"


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
    profil.unterlagen.lebenslauf, profil.unterlagen.zeugnisse = "unterlagen/lebenslauf.txt", []
    speichere_profil(profil, pfad)
    return gestartet


def _seite() -> AppTest:
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=30).run()
    at.switch_page("seiten/jobs.py").run()
    return at


def test_seite_jobs_ohne_stellen(app):
    at = _seite()
    assert not at.exception and any("Noch keine Stellen" in i.value for i in at.info)


def test_seite_jobs_zeigt_alle_filtert_und_bereitet_vor(app):
    with Speicher() as db:
        _stellen(db)
    at = _seite()
    assert not at.exception
    assert at.title[0].value == "Gefundene Jobs" and "6 Stellen" in at.caption[0].value
    tabelle = at.dataframe[0].value
    assert len(tabelle) == 6 and set(tabelle["Stand"]) >= {"offen", "auf „Heute“", "beworben", "Nein gesagt"}
    # Filter „Beworben“
    at.pills(key="jobs_stand").set_value("beworben").run()
    assert list(at.dataframe[0].value["Stelle"]) == ["Product Owner"]
    # Suche nach Titel, dann zwei Zeilen waehlen – bearbeitete werden ausgelassen
    at.pills(key="jobs_stand").set_value("alle").run()
    at.text_input(key="jobs_text").input("manager").run()
    stellen = list(at.dataframe[0].value["Stelle"])
    assert set(stellen) == {"Produktmanager", "IT-Projektmanager", "Prozessmanager"}
    auswahl = {"selection": {"rows": [stellen.index("Produktmanager"), stellen.index("IT-Projektmanager")],
                             "columns": [], "cells": []}}
    at.session_state[at.session_state["jobs_tabelle_key"]] = auswahl
    at.run()
    knopf = at.button(key="jobs_vorbereiten")
    assert knopf.label == "Für 1 Stelle(n) Bewerbung vorbereiten"
    at.session_state[at.session_state["jobs_tabelle_key"]] = auswahl  # im Browser bleibt die Auswahl beim Klick
    knopf.click().run()
    assert not at.exception
    assert app[-1][:2] == ("vorbereiten", "offen")
    with Speicher() as db:
        assert db.vorgang("offen").status == "vorbereitung" and db.vorgang("heute").status == "wartet"


def test_auswahl_beginnt_neu_wenn_sich_die_liste_aendert(app):
    """Kommt zwischen Auswahl und Klick eine neue Stelle dazu, darf die Auswahl nicht auf eine andere rutschen."""
    with Speicher() as db:
        _stellen(db)
    at = _seite()
    stellen = list(at.dataframe[0].value["Stelle"])
    alter_key = at.session_state["jobs_tabelle_key"]
    at.session_state[alter_key] = {"selection": {"rows": [stellen.index("Prozessmanager")], "columns": [],
                                                 "cells": []}}
    with Speicher() as db:  # im Hintergrund gefunden – steht als neueste ganz oben
        db.speichere_jobs([Job(ref="spaet", titel="Data Analyst", arbeitgeber="Sieben AG", ort="München")])
    at.run()
    assert at.session_state["jobs_tabelle_key"] != alter_key
    assert not [b for b in at.button if b.key == "jobs_vorbereiten"] and not app  # nichts gewaehlt, nichts gestartet


def test_bereits_bearbeitete_stelle_wird_nie_neu_vorbereitet(app):
    from bewerbungsagent import ui
    from bewerbungsagent.config import lade_profil

    with Speicher() as db:
        _stellen(db)
        db.setze_vorgang(Vorgang(ref="neu", status="freigegeben", kanal="email", anschreiben="Text"))
    gestartet = ui.vorbereitung_starten(lade_profil("config/profil.yaml"), ["offen", "neu", "beworben", "heute"])
    with Speicher() as db:
        assert gestartet == ["offen"] and db.vorgang("neu").status == "freigegeben"
        assert db.vorgang("heute").status == "wartet" and db.vorgang("beworben") is None


def test_dublette_einer_beworbenen_stelle_ist_nicht_offen(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as db:
        db.speichere_jobs([
            Job(ref="cli", titel="Data Engineer", arbeitgeber="Acht AG", quelle="arbeitsagentur", ort="Köln"),
            Job(ref="kopie", titel="Data Engineer (m/w/d)", arbeitgeber="Acht AG", quelle="get-in-it", ort="Köln"),
            Job(ref="o1", titel="Sachbearbeiter", arbeitgeber="", quelle="arbeitsagentur", ort="Köln"),
            Job(ref="o2", titel="Sachbearbeiter", arbeitgeber="", quelle="arbeitsagentur", ort="Bonn"),
        ])
        db.speichere_score(Score(ref="cli", gesamt=60, passung=60, sentiment=50))
        db.speichere_score(Score(ref="kopie", gesamt=82, passung=82, sentiment=50))
        db.speichere_bewerbung(Application(ref="cli", status="abgeschickt", dry_run=False))  # per Kommandozeile
        jobs = {j["ref"]: j["stand"] for j in db.gefundene_jobs()}
    assert jobs == {"cli": "beworben", "o1": "unbewertet", "o2": "unbewertet"}
