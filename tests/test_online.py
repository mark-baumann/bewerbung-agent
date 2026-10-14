"""Online-Formulare: Browser-Start, ehrliche Fehlertexte, Unterlagen im Datenordner."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bewerbungsagent import ablauf, config
from bewerbungsagent.bewerben import browser
from bewerbungsagent.bewerben.browser import BewerbungsLauf
from bewerbungsagent.config import lade_profil, speichere_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.models import Job, Vorgang

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "SMTP_HOST",
                 "BEWERBUNGSAGENT_DB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    pfad = tmp_path / "config" / "profil.yaml"
    pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(pfad)
    lebenslauf = tmp_path / "unterlagen" / "lebenslauf.txt"
    lebenslauf.parent.mkdir()
    lebenslauf.write_text("Max Mustermann\nPython seit 2019", encoding="utf-8")
    profil.unterlagen.lebenslauf, profil.unterlagen.zeugnisse = str(lebenslauf), []
    profil.automatisierung.probelauf_vorab = False
    speichere_profil(profil, pfad)
    db = Speicher(tmp_path / "daten" / "test.sqlite3")
    yield profil, db, pfad
    db.close()


def job(ref: str = "1") -> Job:
    return Job(ref=ref, titel="Python Entwickler", arbeitgeber="Firma GmbH", ort="München",
               beschreibung="Bewerben Sie sich online.", externe_url=f"https://karriere.firma.de/{ref}")


START_TIMEOUT = ("Event handler browser_use.browser.watchdog_base.BrowserSession.on_BrowserStartEvent#2608 "
                 "timed out after 30.0s and interrupted any processing of 1 child events")


class Browser:
    def __init__(self, *, schritte=0, rohdaten=None, abgeschickt=False, text="Kein Ergebnis", fehler=None):
        self.schritte, self.rohdaten, self.abgeschickt, self.text = schritte, rohdaten, abgeschickt, text
        self.fehler, self.aufrufe = fehler, 0

    async def bewerbe(self, job, anschreiben, dry_run=True, start_url=None, anschreiben_pdf=None):
        self.aufrufe += 1
        if self.fehler:
            raise RuntimeError(self.fehler)
        return BewerbungsLauf(ref=job.ref, dry_run=dry_run, abgeschickt=self.abgeschickt and not dry_run,
                              zusammenfassung=self.text, schritte=self.schritte, url=start_url,
                              rohdaten=self.rohdaten)


def _freigegeben(db, profil, ref="1"):
    db.speichere_jobs([job(ref)])
    ablauf.vorbereiten(db, profil, ref, mit_llm=False, link_aufloesen=False)
    ablauf.freigeben(db, profil, ref)


# --------------------------------------------------------------------------- #
# Browser-Start
# --------------------------------------------------------------------------- #

def test_startzeiten_werden_grosszuegig_gesetzt(monkeypatch):
    monkeypatch.delenv("TIMEOUT_BrowserStartEvent", raising=False)
    monkeypatch.setenv("TIMEOUT_BrowserLaunchEvent", "42")
    browser._browser_umgebung()
    assert os.environ["TIMEOUT_BrowserStartEvent"] == str(browser.START_TIMEOUT_S)
    assert os.environ["TIMEOUT_BrowserLaunchEvent"] == "42"  # eigene Vorgaben bleiben


def test_startfehler_werden_erkannt_und_verstaendlich():
    meldung = ("Event handler browser_use.browser.watchdog_base.BrowserSession.on_BrowserStartEvent#2608 "
               "timed out after 30.0s and interrupted any processing of 1 child events")
    assert browser.ist_startfehler(meldung)
    assert "zu langsam" in browser.startfehler_text(meldung)
    assert "kein Chromium" in browser.startfehler_text("No local Chrome/Chromium install found")
    assert not browser.ist_startfehler("Captcha auf der Seite")


def test_browser_pfad_aus_umgebung(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome"
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    monkeypatch.setenv("BROWSER_EXECUTABLE_PATH", str(chrome))
    assert browser.browser_pfad() == str(chrome)


# --------------------------------------------------------------------------- #
# Versand: ehrliche Zustaende
# --------------------------------------------------------------------------- #

def test_browserstart_fehlt_bleibt_freigegeben(umgebung):
    profil, db, _ = umgebung
    _freigegeben(db, profil)
    b = Browser(rohdaten={"browser_startfehler": True}, text="Der Browser startet zu langsam.")
    ergebnis = ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: b)
    v = db.vorgang("1")
    assert ergebnis["zurueckgestellt"] == 1 and v.status == "freigegeben"
    assert "Nicht sicher" not in v.hinweis and "Browser" in v.hinweis
    assert db.bewerbungen("1") == []  # kein Fehlversuch protokolliert


def test_fehler_vor_der_seite_ist_nicht_nicht_sicher(umgebung):
    profil, db, _ = umgebung
    _freigegeben(db, profil)
    ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: Browser(schritte=0, text="Seite nicht erreichbar"))
    v = db.vorgang("1")
    assert v.status == "fehlgeschlagen" and not v.hinweis.startswith("Nicht sicher")


def test_fehler_nach_arbeit_auf_der_seite_warnt(umgebung):
    profil, db, _ = umgebung
    _freigegeben(db, profil)
    ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: Browser(schritte=12, text="Captcha"))
    v = db.vorgang("1")
    assert v.status == "fehlgeschlagen" and v.hinweis.startswith("Nicht sicher abgeschickt")


def test_fehlender_lebenslauf_stellt_zurueck(umgebung):
    profil, db, _ = umgebung
    _freigegeben(db, profil)
    Path(profil.unterlagen.lebenslauf).unlink()
    b = Browser(schritte=5, abgeschickt=True)
    ergebnis = ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: b)
    v = db.vorgang("1")
    assert ergebnis["zurueckgestellt"] == 1 and v.status == "freigegeben" and b.aufrufe == 0
    assert "Lebenslauf-Datei fehlt" in v.hinweis


def test_gescheiterter_selbsttest_wird_30_minuten_gemerkt(umgebung, monkeypatch):
    profil, db, _ = umgebung
    monkeypatch.setattr(browser, "letzter_selbsttest", lambda: {
        "ok": False, "meldung": "Chromium stürzt ab", "zeit": datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr(browser, "selbsttest", lambda *a, **k: pytest.fail("darf nicht erneut testen"))
    assert ablauf._browser_bereit(None) == (False, "Chromium stürzt ab")
    assert ablauf._browser_bereit(lambda p: None) == (True, "")


# --------------------------------------------------------------------------- #
# Unterlagen und Profil im Datenordner
# --------------------------------------------------------------------------- #

def test_alter_pfad_wird_im_datenordner_gefunden(umgebung):
    profil, _, pfad = umgebung
    alt = Path(profil.unterlagen.lebenslauf)
    ziel = config.unterlagen_dir() / alt.name
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(alt.read_bytes())
    alt.unlink()  # wie nach einem Container-Update
    assert profil.lebenslauf_pfad() == ziel and profil.anhaenge() == [str(ziel.resolve())]


def test_unterlagen_sichern_und_profil_sicherung(umgebung):
    profil, _, pfad = umgebung
    assert config.unterlagen_sichern(profil, pfad)
    neu = Path(profil.unterlagen.lebenslauf)
    assert neu.parent == config.unterlagen_dir() and neu.exists()
    # Profil wird zusaetzlich im Datenordner gesichert und dort gefunden, wenn config/ fehlt
    assert config.daten_profil_pfad().exists()
    pfad.unlink()
    assert config.ermittle_profil_pfad() == config.daten_profil_pfad()
    assert lade_profil().unterlagen.lebenslauf == str(neu)


def test_neues_profil_entsteht_im_datenordner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BEWERBUNGSAGENT_DB", raising=False)
    monkeypatch.setattr(config, "BENUTZER_PFAD", tmp_path / "gibt-es-nicht.yaml")
    assert config.ermittle_profil_pfad() == Path("daten/profil.yaml")


def test_email_ausweg_findet_adresse():
    from bewerbungsagent import ui

    j = Job(ref="x", titel="T", arbeitgeber="A", beschreibung="Bitte Bewerbung an jobs@firma.de senden.")
    assert ui.bewerbungs_email(j, Vorgang(ref="x", kanal="formular")) == "jobs@firma.de"
    assert ui.bewerbungs_email(Job(ref="y", titel="T", arbeitgeber="A"), Vorgang(ref="y")) is None


def test_probelauf_ohne_browserstart_zaehlt_nicht(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job()])
    b = Browser(rohdaten={"browser_startfehler": True}, text="Der Browser startet zu langsam.")
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, probelauf=True, link_aufloesen=False,
                           browser_fabrik=lambda p: b)
    assert v.status == "wartet" and v.hinweis.startswith("Formular-Test war nicht möglich")
    assert db.bewerbungen("1") == []


def test_geworfener_start_timeout_bleibt_freigegeben(umgebung):
    """browser-use wirft den Start-Timeout auch nach den Wiederholungen: nichts ist passiert."""
    profil, db, _ = umgebung
    _freigegeben(db, profil)
    ergebnis = ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: Browser(fehler=START_TIMEOUT))
    v = db.vorgang("1")
    assert ergebnis["zurueckgestellt"] == 1 and v.status == "freigegeben"
    assert "zu langsam" in v.hinweis and "Nicht sicher" not in v.hinweis


def test_geworfener_start_timeout_im_probelauf(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job()])
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, probelauf=True, link_aufloesen=False,
                           browser_fabrik=lambda p: Browser(fehler=START_TIMEOUT))
    assert v.status == "wartet" and v.hinweis.startswith("Formular-Test war nicht möglich")
    assert db.bewerbungen("1") == []


def test_lebenslauf_vorab_auch_im_datenordner(umgebung):
    profil, _, _ = umgebung
    alt = Path(profil.unterlagen.lebenslauf)
    ziel = config.unterlagen_dir() / alt.name
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(alt.read_bytes())
    alt.unlink()
    assert browser._lebenslauf_vorhanden(profil)
