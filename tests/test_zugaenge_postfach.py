"""Zugangsdaten aus der UI und Postfach-Auswertung (ohne Netz)."""

from __future__ import annotations

import os
import stat
from email.message import EmailMessage

import pytest

from bewerbungsagent import zugangsdaten
from bewerbungsagent.bewerben import postfach
from bewerbungsagent.db import Speicher
from bewerbungsagent.models import Job, Vorgang


@pytest.fixture
def datei(tmp_path, monkeypatch):
    for name in zugangsdaten.NAMEN:
        monkeypatch.delenv(name, raising=False)
    pfad = tmp_path / "zugangsdaten.json"
    yield pfad
    # Nichts in andere Tests durchsickern lassen
    zugangsdaten.anwenden(tmp_path / "gibt-es-nicht.json")


def test_speichern_mit_rechten_0600_und_vorrang(datei, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "aus-der-env.example")
    zugangsdaten.speichere({"SMTP_HOST": "smtp.ui.example", "SMTP_PASSWORT": "geheim1234"}, datei)
    assert stat.S_IMODE(datei.stat().st_mode) == 0o600
    assert os.environ["SMTP_HOST"] == "smtp.ui.example"
    assert zugangsdaten.herkunft("SMTP_HOST", datei) == "ui"
    # Loeschen in der UI -> wieder der Wert aus der Umgebung
    zugangsdaten.speichere({"SMTP_HOST": None}, datei)
    assert os.environ["SMTP_HOST"] == "aus-der-env.example"
    assert zugangsdaten.herkunft("SMTP_HOST", datei) == "umgebung"
    assert zugangsdaten.lade(datei) == {"SMTP_PASSWORT": "geheim1234"}


def test_unbekanntes_feld_und_maskierung(datei):
    with pytest.raises(ValueError):
        zugangsdaten.speichere({"PATH": "/tmp"}, datei)
    assert zugangsdaten.maskiert("sk-abcdefgh1234") == "••••1234"
    assert zugangsdaten.maskiert("kurz") == "••••"
    assert zugangsdaten.maskiert("") == ""


def test_anwenden_raeumt_entfernte_werte_auf(datei):
    zugangsdaten.speichere({"JOOBLE_API_KEY": "abc123456789"}, datei)
    assert os.environ["JOOBLE_API_KEY"] == "abc123456789"
    datei.unlink()
    zugangsdaten.anwenden(datei)
    assert "JOOBLE_API_KEY" not in os.environ


def test_imap_konfig_faellt_auf_smtp_zurueck(monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.example.de")
    monkeypatch.setenv("SMTP_BENUTZER", "ich@example.de")
    monkeypatch.setenv("SMTP_PASSWORT", "pw")
    monkeypatch.delenv("IMAP_BENUTZER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORT", raising=False)
    monkeypatch.delenv("IMAP_PORT", raising=False)
    k = postfach.imap_konfig()
    assert (k.host, k.port, k.benutzer, k.passwort) == ("imap.example.de", 993, "ich@example.de", "pw")


# --------------------------------------------------------------------------- #
# Postfach
# --------------------------------------------------------------------------- #

def _mail(betreff: str, text: str, von: str, bezug: str = "") -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["To"] = "ich@example.de"
    m["Subject"] = betreff
    m["Date"] = "Mon, 12 Oct 2026 09:00:00 +0200"
    m["Message-ID"] = f"<{abs(hash(betreff))}@example.de>"
    if bezug:
        m["In-Reply-To"] = bezug
    m.set_content(text)
    return bytes(m)


class FakeImap:
    def __init__(self, mails: list[bytes]):
        self.mails = mails

    def select(self, ordner, readonly=False):
        assert readonly, "Postfach darf nur lesend geoeffnet werden"
        return "OK", [str(len(self.mails)).encode()]

    def response(self, name):
        return "OK", [b"42"]

    def uid(self, befehl, *args):
        if befehl == "SEARCH":
            return "OK", [" ".join(str(i + 1) for i in range(len(self.mails))).encode()]
        uid, teil = args
        assert "PEEK" in teil, "Mails duerfen nicht als gelesen markiert werden"
        return "OK", [(b"1 (BODY[] {1}", self.mails[int(uid) - 1]), b")"]


@pytest.fixture
def db(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as speicher:
        speicher.speichere_jobs([
            Job(ref="a", titel="Python Entwickler (m/w/d)", arbeitgeber="Musterfirma GmbH",
                externe_url="https://karriere.musterfirma.de/jobs/1"),
            Job(ref="b", titel="Data Engineer", arbeitgeber="Beispiel AG", externe_url="https://jobs.lever.co/beispiel/1"),
            Job(ref="c", titel="Projektleiter", arbeitgeber="Dritte KG"),
        ])
        speicher.setze_vorgang(Vorgang(ref="a", status="abgeschickt", kanal="email",
                                       empfaenger="jobs@musterfirma.de", message_id="<bewerbung-a@example.de>"))
        speicher.setze_vorgang(Vorgang(ref="b", status="abgeschickt", kanal="formular",
                                       bewerbungs_url="https://jobs.lever.co/beispiel/1/apply"))
        speicher.setze_vorgang(Vorgang(ref="c", status="wartet", kanal="email", empfaenger="hr@dritte.de"))
        yield speicher


def test_klassifikation():
    assert postfach.klassifiziere("Einladung zum Vorstellungsgespräch", "")[0] == "einladung"
    assert postfach.klassifiziere("Ihre Bewerbung", "leider müssen wir Ihnen mitteilen ...")[0] == "absage"
    assert postfach.klassifiziere("Eingangsbestätigung", "vielen Dank für Ihre Bewerbung")[0] == "eingangsbestaetigung"
    assert postfach.klassifiziere("Re: Bewerbung", "leider können wir Sie nicht zum Interview einladen")[0] == "absage"
    assert postfach.klassifiziere("Newsletter", "Angebote der Woche")[0] == "sonstiges"


def test_postfach_ordnet_zu_und_aktualisiert_tracker(db):
    mails = [
        _mail("Re: Bewerbung als Python Entwickler", "Vielen Dank für Ihre Bewerbung, wir melden uns.",
              "HR <jobs@musterfirma.de>", bezug="<bewerbung-a@example.de>"),
        _mail("Einladung zum Kennenlerngespräch", "Wir möchten Sie gern kennenlernen.",
              "Personal <personal@musterfirma.de>"),
        _mail("Your application: Data Engineer", "Unfortunately we decided to move forward with other candidates.",
              "Beispiel AG Recruiting <no-reply@hire.lever.co>"),
        _mail("Rabatt-Aktion", "Nur heute!", "shop@werbung.example"),
        # Antwort auf eine noch nicht verschickte Bewerbung: keine Zuordnung
        _mail("Ihre Nachricht", "Eingangsbestätigung", "hr@dritte.de"),
    ]
    ergebnis = postfach.pruefe_postfach(db, verbindung=FakeImap(mails))
    assert ergebnis["gelesen"] == 5 and ergebnis["zugeordnet"] == 3
    assert db.vorgang("a").rueckmeldung == "einladung"       # Einladung schlaegt Eingangsbestaetigung
    assert db.vorgang("a").rueckmeldung_quelle == "postfach"
    assert db.vorgang("b").rueckmeldung == "absage"           # ueber Arbeitgeber + Titel, nicht ueber lever.co
    assert db.vorgang("c").rueckmeldung == ""
    # Fremde Mails werden nur als gelesen gemerkt, ohne Inhalt
    fremd = [n for n in db.nachrichten(limit=10) if n["ref"] is None]
    assert fremd and all(n["betreff"] is None for n in fremd)
    # Zweiter Lauf liest nichts doppelt
    assert postfach.pruefe_postfach(db, verbindung=FakeImap(mails))["gelesen"] == 0
