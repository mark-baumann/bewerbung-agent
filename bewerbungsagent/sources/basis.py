"""Gemeinsame Bausteine fuer Jobquellen.

Drei Arten von Quellen:
- "suche":       die API kennt Stichwort (und meist Ort) -> ein Aufruf je Suchbegriff
- "feed":        die API liefert einen Gesamtbestand ohne Suche -> hoechstens alle
                 `min_abstand_min` Minuten abrufen, danach lokal je Suchbegriff filtern
- "arbeitgeber": Stellen der Wunsch-Arbeitgeber direkt aus deren Bewerbermanagement
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from ..models import Job

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) bewerbungsagent/0.2 (+persoenliche Jobsuche)"

# Englische/alternative Schreibweisen grosser Staedte -> deutscher Name
STADT_ALIASE = {
    "munich": "muenchen", "munchen": "muenchen", "cologne": "koeln", "koln": "koeln",
    "nuremberg": "nuernberg", "nurnberg": "nuernberg", "hanover": "hannover",
    "dusseldorf": "duesseldorf", "brunswick": "braunschweig", "frankfurt am main": "frankfurt",
    "frankfurt/main": "frankfurt", "frankfurt a.m.": "frankfurt",
}

REMOTE_WOERTER = re.compile(
    r"\b(remote|homeoffice|home[- ]office|mobiles arbeiten|anywhere|worldwide|weltweit|"
    r"deutschlandweit|bundesweit)\b",
    re.IGNORECASE,
)
# Remote-Stellen, die (auch) Bewerber aus Deutschland ansprechen
REMOTE_FUER_DE = re.compile(
    r"germany|deutschland|europe|europa|\beu\b|emea|worldwide|anywhere|weltweit|dach|cet|cest",
    re.IGNORECASE,
)


class QuelleFehler(RuntimeError):
    """Abruf einer Quelle ist komplett fehlgeschlagen."""


def http_client(timeout: float = 30.0, transport=None, **kwargs: Any) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"}
    headers.update(kwargs.pop("headers", {}) or {})
    return httpx.Client(timeout=timeout, transport=transport, follow_redirects=True, headers=headers, **kwargs)


def ohne_html(text: Any) -> str | None:
    """HTML -> lesbarer Text mit Absaetzen."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = html.unescape(text)
    if "&lt;" in text:  # doppelt escaped (z. B. Greenhouse)
        text = html.unescape(text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>|</div>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    if re.search(r"&(?:[a-zA-Z]+|#\d+);", text):  # Reste mehrfach escapter Entitaeten
        text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(z.strip() for z in text.splitlines()).strip() or None


def normalisiert(text: str | None) -> str:
    """'München, Bayern' -> 'muenchen bayern' (fuer Vergleiche)."""
    text = (text or "").casefold()
    for alt, neu in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(alt, neu)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9/ .-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for alt, neu in STADT_ALIASE.items():
        text = re.sub(rf"\b{re.escape(alt)}\b", neu, text)
    return text


def suchworte(begriff: str) -> list[str]:
    return [normalisiert(w) for w in re.findall(r"[\w#+.-]+", begriff) if len(w) >= 2]


def _wort_passt(wort: str, text: str) -> bool:
    if wort in text:
        return True
    # Komposita: "Softwareentwickler" soll auch "Software Entwickler" finden
    return len(wort) >= 10 and wort[:6] in text


def begriff_passt(job: Job, begriff: str) -> bool:
    """Alle Woerter des Suchbegriffs muessen in Titel/Beruf vorkommen (Komposita tolerant)."""
    worte = suchworte(begriff)
    if not worte:
        return True
    text = normalisiert(" ".join(x for x in (job.titel, job.beruf or "") if x))
    return all(_wort_passt(w, text) for w in worte)


def ist_remote(job: Job) -> bool:
    return bool(job.homeoffice) or bool(REMOTE_WOERTER.search(job.ort or ""))


def ort_passt(job: Job, orte: Iterable[str], remote_ok: bool) -> bool:
    """Ort der Stelle gehoert zu den Orten im Umkreis (oder Remote ist erlaubt)."""
    orte = [o for o in orte if o]
    if not orte:
        return True
    if remote_ok and ist_remote(job):
        return True
    text = normalisiert(" ".join(x for x in (job.ort or "", job.plz or "", job.region or "") if x))
    return any(re.search(rf"\b{re.escape(o)}\b", text) for o in orte)


def iso_datum(wert: Any) -> str | None:
    """Unix-Zeit, ISO oder 'TT.MM.JJJJ' -> 'JJJJ-MM-TT'."""
    if wert in (None, ""):
        return None
    if isinstance(wert, (int, float)):
        sekunden = wert / 1000 if wert > 10_000_000_000 else wert
        return datetime.fromtimestamp(sekunden, tz=timezone.utc).date().isoformat()
    text = str(wert).strip()
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


class QuellenClient:
    """Basisklasse: HTTP-Client verwalten, Kontextmanager."""

    name = "basis"

    def __init__(self, timeout: float = 30.0, transport=None, profil=None, **_ignoriert: Any):
        self.profil = profil
        self.http = http_client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _json(self, url: str, **kwargs: Any) -> Any:
        antwort = self.http.get(url, **kwargs)
        antwort.raise_for_status()
        return antwort.json()

    def _text(self, url: str, **kwargs: Any) -> str:
        antwort = self.http.get(url, **kwargs)
        antwort.raise_for_status()
        return antwort.text

    # Feed-Quellen ueberschreiben hole_feed, Such-Quellen hole_jobs
    def hole_feed(self) -> list[Job]:  # pragma: no cover - Schnittstelle
        raise NotImplementedError

    def anreichern(self, job: Job) -> None:
        """Optional: Volltext/Details fuer eine lokal ausgewaehlte Feed-Stelle nachladen."""
        return None
