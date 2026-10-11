"""Bewerbungsweg je Stelle bestimmen: E-Mail, Online-Formular oder selbst.

Rangfolge:
1. Die Anzeige nennt ausdruecklich eine Bewerbungs-E-Mail -> E-Mail (am zuverlaessigsten).
2. Es gibt eine Bewerbungs-URL -> Online-Formular per Browser-Agent.
3. Irgendeine E-Mail-Adresse in der Anzeige -> E-Mail.
4. Sonst bleibt nur die Bewerbung durch den Nutzer selbst.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from ..models import Job

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_STICHWORTE = re.compile(
    r"bewerb|unterlagen|lebenslauf|application|apply|\bcv\b", re.IGNORECASE
)
# Adressen, an die man sich nicht bewirbt
_KEINE_BEWERBUNG = re.compile(
    r"^(datenschutz|privacy|dsb|noreply|no-reply|donotreply|abuse|webmaster|presse|press|"
    r"newsletter|support|rechnung|buchhaltung)@",
    re.IGNORECASE,
)
_UMKREIS = 160  # Zeichen um die Adresse, in denen ein Bewerbungs-Stichwort stehen muss


@dataclass
class KanalWahl:
    kanal: str  # email | formular | manuell
    empfaenger: str | None = None
    url: str | None = None
    grund: str = ""


def _entschleiern(text: str) -> str:
    """'bewerbung (at) firma (dot) de' -> 'bewerbung@firma.de'."""
    text = re.sub(r"\s*[\(\[\{]\s*(?:at|ät|@)\s*[\)\]\}]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\(\[\{]\s*(?:dot|punkt)\s*[\)\]\}]\s*", ".", text, flags=re.IGNORECASE)
    return text


def finde_bewerbungs_email(text: str | None) -> tuple[str | None, bool]:
    """(Adresse, sicher). `sicher` heisst: in der Naehe steht ein Bewerbungs-Stichwort."""
    if not text:
        return None, False
    text = _entschleiern(text)
    kandidaten: list[tuple[str, bool]] = []
    for m in _EMAIL.finditer(text):
        adresse = m.group(0).strip(".").lower()
        if _KEINE_BEWERBUNG.match(adresse):
            continue
        umfeld = text[max(0, m.start() - _UMKREIS): m.end() + _UMKREIS]
        kandidaten.append((adresse, bool(_STICHWORTE.search(umfeld))))
    for adresse, sicher in kandidaten:
        if sicher:
            return adresse, True
    if kandidaten:
        return kandidaten[0][0], False
    return None, False


def bestimme_kanal(job: Job, gefundene_email: str | None = None) -> KanalWahl:
    """Waehlt den Bewerbungsweg. `gefundene_email` kommt z. B. aus dem Probelauf."""
    if gefundene_email:
        return KanalWahl("email", empfaenger=gefundene_email.lower(),
                         grund="Bewerbungs-E-Mail auf der Bewerbungsseite gefunden")
    adresse, sicher = finde_bewerbungs_email(job.beschreibung)
    if adresse and sicher:
        return KanalWahl("email", empfaenger=adresse, grund="Anzeige nennt eine Bewerbungs-E-Mail")
    if job.bewerbungs_url:
        return KanalWahl("formular", url=job.bewerbungs_url, grund="Bewerbung über die Bewerbungsseite")
    if adresse:
        return KanalWahl("email", empfaenger=adresse, grund="einzige E-Mail-Adresse in der Anzeige")
    return KanalWahl("manuell", grund="kein Bewerbungsweg in der Anzeige gefunden")


# --------------------------------------------------------------------------- #
# Bewerbungslink aufloesen
# --------------------------------------------------------------------------- #

_GET_IN_IT_APPLY = re.compile(r'"applyUrl"\s*:\s*("(?:[^"\\]|\\.)*")')
_GET_IN_IT_BUTTON = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*data-tracking-action="Outclick: Jetzt bewerben"', re.IGNORECASE
)


def _get_in_it_bewerbungslink(html: str, basis: str) -> str | None:
    m = _GET_IN_IT_APPLY.search(html)
    if m:
        try:
            url = json.loads(m.group(1))
        except json.JSONDecodeError:
            url = None
        if url:
            return urljoin(basis, url)
    m = _GET_IN_IT_BUTTON.search(html)
    return urljoin(basis, m.group(1)) if m else None


def loese_bewerbungslink(job: Job, client: httpx.Client | None = None) -> str | None:
    """Endgueltige Bewerbungsseite: folgt Weiterleitungen, bei GET IN IT dem Bewerben-Link.

    Faellt bei Netzwerkfehlern auf die gespeicherte URL zurueck.
    """
    url = job.bewerbungs_url
    if not url:
        return None
    eigener = client is None
    client = client or httpx.Client(
        timeout=20.0, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (bewerbungsagent)"},
    )
    try:
        if job.quelle == "get-in-it" or "get-in-it.de" in (urlparse(url).hostname or ""):
            antwort = client.get(url)
            antwort.raise_for_status()
            ziel = _get_in_it_bewerbungslink(antwort.text, str(antwort.url))
            if not ziel:
                return url
            url = ziel
        antwort = client.get(url)
        return str(antwort.url) if antwort.status_code < 400 else url
    except httpx.HTTPError as e:
        log.info("Bewerbungslink %s nicht aufloesbar: %s", url, e)
        return url
    finally:
        if eigener:
            client.close()
