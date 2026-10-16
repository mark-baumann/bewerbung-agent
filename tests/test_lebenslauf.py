"""Lebenslauf als Quelle: Auslesen (KI/Heuristik), einmal je Inhalt, Uebernahme ins Profil."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bewerbungsagent import dokumente, lebenslauf
from bewerbungsagent.config import lade_profil, speichere_profil
from bewerbungsagent.scoring import llm

REPO = Path(__file__).resolve().parent.parent

TEXT = """Lebenslauf
Mark Beispiel
Leopoldstraße 12a
80802 München
Telefon: +49 151 2345 6789
E-Mail: Mark.Beispiel@example.de
Berufserfahrung
2019 – heute Softwareentwickler Python, Firma GmbH"""

KI_ANTWORT = {
    "kurzprofil": "Softwareentwickler mit 6 Jahren Python, Django und PostgreSQL.",
    "jobtitel": ["Softwareentwickler Python (m/w/d)", "Python Developer", "Backend-Entwickler", "python developer"],
    "ort": "80802 München",
    "faehigkeiten": ["Python", "Django", "PostgreSQL", "Docker"],
    "kontakt": {"vorname": "Mark", "nachname": "Beispiel", "email": "mark.beispiel@example.de",
                "telefon": "+49 151 2345 6789", "strasse": "Leopoldstraße 12a", "plz": "80802", "ort": "München"},
}


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "BEWERBUNGSAGENT_DB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    pfad = tmp_path / "config" / "profil.yaml"
    pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(pfad)
    datei = tmp_path / "unterlagen" / "lebenslauf.txt"
    datei.parent.mkdir()
    datei.write_text(TEXT, encoding="utf-8")
    profil.unterlagen.lebenslauf = str(datei)
    profil.person.telefon, profil.person.strasse = "", ""  # sollen aus dem Lebenslauf kommen
    profil.suche.wo = "Köln"
    speichere_profil(profil, pfad)
    return profil, pfad, datei


def _ki(monkeypatch, antwort=KI_ANTWORT):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    aufrufe = []

    def chat(modell, **kw):
        aufrufe.append(kw)
        if isinstance(antwort, Exception):
            raise antwort
        return json.dumps(antwort)

    monkeypatch.setattr(llm, "chat", chat)
    return aufrufe


def test_heuristik_liest_kontaktdaten():
    a = lebenslauf.heuristik(TEXT)
    assert a.kontakt == {"email": "mark.beispiel@example.de", "telefon": "+49 151 2345 6789", "plz": "80802",
                         "ort": "München", "strasse": "Leopoldstraße 12a", "vorname": "Mark", "nachname": "Beispiel"}
    assert a.ort == "München" and a.jobtitel == [] and a.quelle == "heuristik"


def test_ki_auswertung_wird_uebernommen_und_nur_einmal_ausgelesen(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    aufrufe = _ki(monkeypatch)
    vorher = (profil.person.vorname, profil.person.email)
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "ki" and len(aufrufe) == 1
    assert uebernommen == ["Kurzprofil", "Jobtitel", "Kontaktdaten", "Ort", "Fähigkeiten"]
    gespeichert = lade_profil(pfad)
    assert gespeichert.suche.was == ["Softwareentwickler Python", "Python Developer", "Backend-Entwickler"]
    assert gespeichert.suche.wo == "München" and gespeichert.bewertung.skills == ["Python", "Django", "PostgreSQL",
                                                                                   "Docker"]
    assert gespeichert.kurzprofil.startswith("Softwareentwickler mit 6 Jahren")
    # Kontaktdaten nur ergaenzt, vorhandene bleiben
    assert (gespeichert.person.vorname, gespeichert.person.email) == vorher
    assert gespeichert.person.telefon == "+49 151 2345 6789" and gespeichert.person.strasse == "Leopoldstraße 12a"

    # Gleicher Inhalt: keine neue Anfrage, nichts ueberschrieben (auch wenn der Nutzer die Suche geaendert hat)
    gespeichert.suche.was = ["Eigener Titel"]
    a2, uebernommen2 = lebenslauf.aktualisieren(gespeichert, pfad)
    assert len(aufrufe) == 1 and uebernommen2 == [] and gespeichert.suche.was == ["Eigener Titel"]
    # „Neu auslesen“
    lebenslauf.aktualisieren(gespeichert, pfad, erzwingen=True)
    assert len(aufrufe) == 2 and gespeichert.suche.was[0] == "Softwareentwickler Python"


def test_neuer_inhalt_wird_neu_ausgelesen(umgebung, monkeypatch):
    profil, pfad, datei = umgebung
    aufrufe = _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    datei.write_text(TEXT + "\n2025 Weiterbildung Data Science", encoding="utf-8")
    lebenslauf.aktualisieren(profil, pfad)
    assert len(aufrufe) == 2


def test_ohne_ki_nur_kontaktdaten(umgebung):
    profil, pfad, _ = umgebung
    was, skills = list(profil.suche.was), list(profil.bewertung.skills)
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "heuristik" and "Jobtitel" not in uebernommen
    assert profil.suche.was == was and profil.bewertung.skills == skills
    assert profil.person.telefon == "+49 151 2345 6789" and profil.suche.wo == "München"


def test_ki_fehler_faellt_auf_heuristik_zurueck_und_behaelt_alte_ki_auswertung(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    _ki(monkeypatch, llm.LLMNichtVerfuegbar("Error code: 429 insufficient_quota"))
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad, erzwingen=True)
    assert a.quelle == "ki" and uebernommen == [] and profil.suche.was[0] == "Softwareentwickler Python"


def test_nur_ohne_ki_gelesen_wird_spaeter_mit_ki_wiederholt(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    lebenslauf.aktualisieren(profil, pfad)  # ohne KI-Zugang
    aufrufe = _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    assert aufrufe == []  # nicht bei jedem Seitenaufruf
    alt = lebenslauf.gespeichert()
    alt.zeit = (datetime.now(timezone.utc) - timedelta(hours=lebenslauf.NEU_VERSUCHEN_STUNDEN + 1)).isoformat()
    lebenslauf._speichern(alt)
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert len(aufrufe) == 1 and a.quelle == "ki" and profil.suche.was[0] == "Softwareentwickler Python"


def test_ohne_lesbaren_text_keine_auswertung(umgebung):
    profil, pfad, datei = umgebung
    datei.write_text("   ", encoding="utf-8")
    assert lebenslauf.aktualisieren(profil, pfad) == (None, [])


def test_word_lebenslauf_ist_lesbar(umgebung):
    profil, _, datei = umgebung
    docx = datei.with_suffix(".docx")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Mark Beispiel</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>80802 M&#252;nchen</w:t></w:r></w:p></w:body></w:document>")
    profil.unterlagen.lebenslauf = str(docx)
    assert dokumente.lebenslauf_text(profil) == "Mark Beispiel\n80802 München"
