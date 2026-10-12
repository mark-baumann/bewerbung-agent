"""Zugangsdaten, die in der Web-UI eingetragen werden (SMTP, IMAP, KI, Jobboersen).

Gespeichert in `daten/zugangsdaten.json` (nur fuer den Eigentuemer lesbar,
nicht in der Profil-YAML und nicht im Git). `anwenden()` uebertraegt die Werte
in `os.environ`; ein in der UI gespeicherter Wert hat Vorrang vor `.env` bzw.
der Docker-Umgebung. CLI, Hintergrund-Worker und Cron-Tick rufen `anwenden()`
bei jedem Start auf - Aenderungen greifen dadurch ohne Neustart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .db import STANDARD_DB

DATEI = STANDARD_DB.parent / "zugangsdaten.json"


@dataclass(frozen=True)
class Feld:
    name: str          # Umgebungsvariable
    gruppe: str
    label: str
    geheim: bool = False
    hilfe: str = ""


FELDER: list[Feld] = [
    Feld("SMTP_HOST", "smtp", "Server", hilfe="z. B. smtp.gmail.com, smtp.web.de, smtp.ionos.de"),
    Feld("SMTP_PORT", "smtp", "Port", hilfe="465 (SSL) oder 587 (STARTTLS)"),
    Feld("SMTP_BENUTZER", "smtp", "Benutzer / E-Mail-Adresse"),
    Feld("SMTP_PASSWORT", "smtp", "Passwort", geheim=True,
         hilfe="Bei Gmail/Outlook ein App-Passwort verwenden, nicht das normale Passwort."),
    Feld("SMTP_ABSENDER", "smtp", "Absenderadresse (optional)", hilfe="Standard: Benutzer"),
    Feld("IMAP_HOST", "imap", "Server", hilfe="z. B. imap.gmail.com – leer lassen, wenn Antworten nicht ausgewertet werden sollen"),
    Feld("IMAP_PORT", "imap", "Port", hilfe="Standard 993"),
    Feld("IMAP_BENUTZER", "imap", "Benutzer (optional)", hilfe="Standard: SMTP-Benutzer"),
    Feld("IMAP_PASSWORT", "imap", "Passwort (optional)", geheim=True, hilfe="Standard: SMTP-Passwort"),
    Feld("ANTHROPIC_API_KEY", "ki", "Anthropic (Claude) API-Key", geheim=True),
    Feld("OPENAI_API_KEY", "ki", "OpenAI (ChatGPT) API-Key", geheim=True),
    Feld("OLLAMA_BASE_URL", "ki", "Ollama-Server (URL)",
         hilfe="z. B. https://ollama.com oder http://192.168.1.10:11434 – leer = http://localhost:11434"),
    Feld("OLLAMA_API_KEY", "ki", "Ollama API-Key", geheim=True,
         hilfe="Für die Ollama-Cloud (ollama.com) oder einen geschützten eigenen Server."),
    Feld("BA_BENUTZER", "ba", "Benutzername Arbeitsagentur"),
    Feld("BA_PASSWORT", "ba", "Passwort Arbeitsagentur", geheim=True),
    Feld("ADZUNA_APP_ID", "quellen", "Adzuna app_id", hilfe="kostenlos unter developer.adzuna.com"),
    Feld("ADZUNA_APP_KEY", "quellen", "Adzuna app_key", geheim=True),
    Feld("JOOBLE_API_KEY", "quellen", "Jooble API-Key", geheim=True, hilfe="kostenlos anfragen unter jooble.org/api/about"),
]

GRUPPEN = {
    "smtp": "📧 E-Mail-Versand (SMTP)",
    "imap": "📥 Antworten erkennen (IMAP)",
    "ki": "🤖 KI-Zugang",
    "ba": "🏛️ Konto bei der Arbeitsagentur (optional)",
    "quellen": "🔎 Zusätzliche Jobbörsen",
}

NAMEN = {f.name for f in FELDER}

# Werte, die vor dem Setzen aus der UI in der Umgebung standen (.env/Docker):
# wird ein UI-Wert geloescht, gilt wieder dieser. Nur Variablen, die
# `anwenden()` selbst gesetzt hat, werden je wieder angefasst.
_vorher: dict[str, str | None] = {}


def lade(datei: Path | None = None) -> dict[str, str]:
    pfad = Path(datei or DATEI)
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(daten, dict):
        return {}
    return {k: str(v) for k, v in daten.items() if k in NAMEN and v not in (None, "")}


def speichere(aenderungen: dict[str, str | None], datei: Path | None = None) -> dict[str, str]:
    """Uebernimmt Aenderungen (leer/None = entfernen) und schreibt atomar mit Rechten 0600."""
    pfad = Path(datei or DATEI)
    werte = lade(pfad)
    for name, wert in aenderungen.items():
        if name not in NAMEN:
            raise ValueError(f"Unbekanntes Feld: {name}")
        wert = (wert or "").strip()
        if wert:
            werte[name] = wert
        else:
            werte.pop(name, None)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(werte, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, pfad)
    anwenden(pfad)
    return werte


def anwenden(datei: Path | None = None) -> int:
    """Setzt gespeicherte Werte in os.environ (UI vor Umgebung); liefert die Anzahl."""
    werte = lade(datei)
    for name, wert in werte.items():
        if name not in _vorher:
            _vorher[name] = os.environ.get(name)
        os.environ[name] = wert
    for name in [n for n in _vorher if n not in werte]:
        alt = _vorher.pop(name)
        if alt is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = alt
    return len(werte)


def herkunft(name: str, datei: Path | None = None) -> str | None:
    """'ui', 'umgebung' oder None (nicht gesetzt)."""
    if name in lade(datei):
        return "ui"
    basis = _vorher[name] if name in _vorher else os.environ.get(name)
    return "umgebung" if basis else None


def maskiert(wert: str | None) -> str:
    if not wert:
        return ""
    return "••••" + wert[-4:] if len(wert) > 8 else "••••"


def felder(gruppe: str) -> list[Feld]:
    return [f for f in FELDER if f.gruppe == gruppe]


# --------------------------------------------------------------------------- #
# Verbindungstests (fuer die Knoepfe in Profil -> Zugaenge)
# --------------------------------------------------------------------------- #

def teste_smtp() -> tuple[bool, str]:
    from .bewerben import mail

    konfig = mail.smtp_konfig()
    if konfig is None:
        return False, "Kein SMTP-Server eingetragen."
    try:
        server = mail._verbinde(konfig)
        server.quit()
    except Exception as e:
        return False, f"Anmeldung bei {konfig.host}:{konfig.port} fehlgeschlagen: {e}"
    return True, f"Anmeldung bei {konfig.host}:{konfig.port} als {konfig.benutzer or '(ohne Login)'} erfolgreich."


def teste_imap() -> tuple[bool, str]:
    from .bewerben import postfach

    konfig = postfach.imap_konfig()
    if konfig is None:
        return False, "Kein IMAP-Server eingetragen."
    try:
        with postfach.verbinde(konfig) as imap:
            anzahl = postfach.anzahl_posteingang(imap)
    except Exception as e:
        return False, f"Anmeldung bei {konfig.host} fehlgeschlagen: {e}"
    return True, f"Anmeldung bei {konfig.host} erfolgreich – {anzahl} Nachrichten im Posteingang."


def teste_ki(modell: str | None) -> tuple[bool, str]:
    from .scoring import llm

    anbieter = llm.provider(modell)
    if anbieter is None:
        return False, "Kein KI-Zugang eingetragen."
    modelle = llm.verfuegbare_modelle(anbieter, neu_laden=True)
    if not modelle and llm.modell_fehler.get(anbieter):
        return False, llm.modell_fehler[anbieter]
    ziel, hinweis = llm.pruefe_modell(anbieter, modell)
    try:
        antwort = llm.chat(ziel, system="Antworte nur mit OK.", nutzer="Test", max_tokens=200, effort="low")
    except Exception as e:
        return False, f"Testanfrage an {ziel} ({anbieter}) fehlgeschlagen: {str(e)[:300]}"
    text = f"{anbieter}: Modell „{ziel}“ antwortet ({antwort.strip()[:40]!r})."
    if modelle:
        text += f" {len(modelle)} Modelle verfügbar."
    if hinweis:
        text += " " + hinweis
    return True, text


def teste_quelle(name: str) -> tuple[bool, str]:
    from .sources import QUELLEN, client_fuer

    info = QUELLEN[name]
    if not info.verfuegbar():
        return False, f"Für {info.label} fehlt noch: {', '.join(info.schluessel)}."
    try:
        with client_fuer(name) as client:
            jobs = client.hole_jobs(was="Sachbearbeiter", wo="Berlin", umkreis=25, veroeffentlicht_seit_tagen=30,
                                    nur_vollzeit=False, max_treffer=3, mit_details=False)
    except Exception as e:
        return False, f"{info.label}: {str(e)[:300]}"
    return True, f"{info.label}: Zugang funktioniert ({len(jobs)} Probetreffer)."
