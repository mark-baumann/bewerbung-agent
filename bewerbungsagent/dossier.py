"""Stellen-Dossier: was eine Anzeige wirklich verlangt und an wen die Bewerbung geht.

Mit KI: strukturierte Extraktion (Muss-/Kann-Anforderungen, Aufgaben,
Ansprechpartner und Anrede, Kennziffer, Frist, gewuenschte Unterlagen, ob
Gehaltsvorstellung/Eintrittstermin gefragt sind). Ohne KI oder bei Fehlern
fuellen Regeln die wichtigsten Felder. Strukturierte Angaben der Quelle
(Interamt-Ansprechpartner, Frist) haben immer Vorrang vor geratenen.

Das Ergebnis liegt als JSON in `jobs.dossier` und speist Anschreiben,
Pruefung und Formular-Agent.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import Profil
from .models import Job
from .scoring import llm as llm_modul
from .sources.basis import iso_datum

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "muss": {"type": "array", "items": {"type": "string"},
                 "description": "Zwingende Anforderungen (kurz, je max. 8 Woerter)"},
        "kann": {"type": "array", "items": {"type": "string"},
                 "description": "Wuenschenswerte Anforderungen (\"von Vorteil\", \"idealerweise\")"},
        "aufgaben": {"type": "array", "items": {"type": "string"}, "description": "Die 3-5 wichtigsten Aufgaben"},
        "ansprechpartner": {"type": ["string", "null"],
                            "description": "Name der Kontaktperson mit Anrede, z. B. 'Frau Dr. Erika Muster'"},
        "referenz": {"type": ["string", "null"], "description": "Kennziffer/Referenznummer des Arbeitgebers"},
        "frist": {"type": ["string", "null"], "description": "Bewerbungsfrist als JJJJ-MM-TT"},
        "unterlagen": {"type": "array", "items": {"type": "string"},
                       "description": "Ausdruecklich gewuenschte Unterlagen"},
        "gehalt_gefragt": {"type": "boolean", "description": "Wird nach der Gehaltsvorstellung gefragt?"},
        "eintritt_gefragt": {"type": "boolean", "description": "Wird nach dem fruehesten Eintrittstermin gefragt?"},
        "sprache": {"type": "string", "enum": ["de", "en"], "description": "Sprache der Anzeige"},
    },
    "required": ["muss", "kann", "aufgaben", "ansprechpartner", "referenz", "frist", "unterlagen",
                 "gehalt_gefragt", "eintritt_gefragt", "sprache"],
    "additionalProperties": False,
}

SYSTEM = """Du analysierst eine Stellenanzeige und extrahierst Fakten. Nur was in der
Anzeige steht - nichts ergaenzen oder vermuten. Fehlt etwas, null bzw. leere Liste.
Antworte ausschliesslich im vorgegebenen JSON-Schema."""


@dataclass
class Dossier:
    muss: list[str] = field(default_factory=list)
    kann: list[str] = field(default_factory=list)
    aufgaben: list[str] = field(default_factory=list)
    ansprechpartner: str | None = None
    referenz: str | None = None
    frist: str | None = None
    unterlagen: list[str] = field(default_factory=list)
    gehalt_gefragt: bool = False
    eintritt_gefragt: bool = False
    sprache: str = "de"
    quelle: str = "regeln"           # regeln | llm
    erstellt_am: str = ""

    @property
    def anrede(self) -> str:
        return anrede_fuer(self.ansprechpartner, self.sprache)

    def als_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def aus_json(cls, text: str | None) -> "Dossier | None":
        if not text:
            return None
        try:
            daten = json.loads(text)
        except json.JSONDecodeError:
            return None
        erlaubt = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in daten.items() if k in erlaubt})


# --------------------------------------------------------------------------- #
# Regeln (ohne KI)
# --------------------------------------------------------------------------- #

_PERSON = r"(Frau|Herrn?|Mr\.?|Ms\.?|Mrs\.?)\s+((?:(?:Prof\.|Dr\.)\s*)*)([A-ZÄÖÜ][\w\-]+(?:\s+(?:von|van|de|zu)\s+)?(?:\s*[A-ZÄÖÜ][\w\-]+)?)"
_ANSPRECHPARTNER = re.compile(
    r"(?:Ansprechpartner(?:in)?|Ansprechperson|Kontaktperson|Ihr(?:e)?\s+Kontakt|Fragen\s+beantwortet(?:\s+Ihnen)?|"
    r"für\s+Rückfragen|wenden\s+Sie\s+sich\s+(?:bitte\s+)?an|contact\s+person|Ihre\s+Ansprechpartnerin|"
    r"Ihr\s+Ansprechpartner)[^A-Za-zÄÖÜäöü]{0,20}(?:steht\s+Ihnen\s+)?(?:\w+\s+){0,3}?" + _PERSON,
    re.IGNORECASE,
)
_REFERENZ = re.compile(
    r"(?:Kennziffer|Kennzahl|Referenz(?:nummer|-?Nr\.?)?|Ref\.?-?Nr\.?|Stellen-?(?:ID|nummer)|Job-?ID|"
    r"Ausschreibungs(?:nummer|kennziffer)|Reference(?: number)?)\s*[:#]?\s*([A-Z0-9][A-Za-z0-9\-/._]{2,30})",
    re.IGNORECASE,
)
_FRIST = re.compile(
    r"(?:bis\s+(?:zum|spätestens(?:\s+zum)?)|Bewerbungsfrist(?:\s+endet)?(?:\s+am)?|Bewerbungsschluss|"
    r"spätestens\s+(?:bis\s+)?(?:zum\s+)?)\s*:?\s*(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4})",
    re.IGNORECASE,
)
_GEHALT = re.compile(r"Gehaltsvorstellung|Gehaltswunsch|Gehaltsvorstellungen|salary expectation", re.IGNORECASE)
_EINTRITT = re.compile(r"(frühestmöglichen|frühesten|möglichen)\s+Eintritts?termin|Verfügbarkeit|earliest (possible )?start",
                       re.IGNORECASE)
_UNTERLAGEN = ["Anschreiben", "Lebenslauf", "Zeugnisse", "Arbeitszeugnisse", "Motivationsschreiben",
               "Arbeitsproben", "Portfolio", "Referenzen", "Zertifikate", "Führungszeugnis", "Beurteilung"]
_KANN = re.compile(r"(wünschenswert|von Vorteil|idealerweise|nice to have|ein Plus|optional|gerne auch)", re.I)
_MUSS_KOPF = re.compile(r"(Ihr Profil|Das bringen Sie mit|Anforderungen|Was Sie mitbringen|Ihre Qualifikation|"
                        r"Voraussetzungen|Requirements|What you bring|Your profile|Qualifikationen)", re.I)
_AUFGABEN_KOPF = re.compile(r"(Ihre Aufgaben|Aufgaben|Das erwartet Sie|Tätigkeitsprofil|Your tasks|"
                            r"Responsibilities|What you.ll do|Ihr Aufgabengebiet)", re.I)
_ABSCHNITT_ENDE = re.compile(r"(Wir bieten|Unser Angebot|Das bieten wir|Benefits|What we offer|Kontakt|"
                             r"Haben wir Ihr Interesse|Bewerbung|Über uns|About us)", re.I)
_EN = re.compile(r"\b(the|and|you|your|with|we|our|for|will)\b", re.I)


def anrede_fuer(ansprechpartner: str | None, sprache: str = "de") -> str:
    """'Frau Dr. Erika Muster' -> 'Sehr geehrte Frau Dr. Muster,'."""
    if ansprechpartner:
        m = re.match(r"\s*(Frau|Herrn?|Mr\.?|Ms\.?|Mrs\.?)\s+(.*)$", ansprechpartner.strip(), re.IGNORECASE)
        if m:
            art = m.group(1).lower().rstrip(".")
            rest = m.group(2).split()
            titel = [t for t in rest if t.rstrip(".").lower() in ("dr", "prof")]
            name = [t for t in rest if t not in titel]
            nachname = name[-1] if name else ""
            if nachname:
                kette = " ".join([*titel, nachname])
                if sprache == "en":
                    return f"Dear {'Ms' if art in ('frau', 'ms', 'mrs') else 'Mr'} {kette},"
                if art == "frau":
                    return f"Sehr geehrte Frau {kette},"
                return f"Sehr geehrter Herr {kette},"
    return "Dear Hiring Team," if sprache == "en" else "Sehr geehrte Damen und Herren,"


def _abschnitt(text: str, kopf: re.Pattern[str]) -> list[str]:
    m = kopf.search(text)
    if not m:
        return []
    rest = text[m.end():]
    enden = [e.start() for e in (_ABSCHNITT_ENDE.search(rest, 20), _MUSS_KOPF.search(rest, 20),
                                 _AUFGABEN_KOPF.search(rest, 20)) if e]
    ende = min(enden) if enden else None
    teil = rest[:ende] if ende is not None else rest[:1500]
    zeilen = [z.strip(" -•*·\t:") for z in re.split(r"\n|•|·| - ", teil)]
    return [z for z in zeilen if 3 <= len(z) <= 160][:10]


def aus_regeln(job: Job) -> Dossier:
    text = job.beschreibung or ""
    d = Dossier(quelle="regeln")
    m = _ANSPRECHPARTNER.search(text)
    if m:
        art = "Herr" if m.group(1).lower().startswith("herr") or m.group(1).lower().startswith("mr") else (
            "Frau" if m.group(1).lower() in ("frau", "ms", "ms.", "mrs", "mrs.") else m.group(1))
        d.ansprechpartner = " ".join(x for x in (art, (m.group(2) or "").strip(), m.group(3).strip()) if x)
    m = _REFERENZ.search(text)
    if m and re.search(r"\d", m.group(1)):
        d.referenz = m.group(1).rstrip(".")
    m = _FRIST.search(text)
    if m:
        d.frist = iso_datum(re.sub(r"\s", "", m.group(1)))
    d.gehalt_gefragt = bool(_GEHALT.search(text))
    d.eintritt_gefragt = bool(_EINTRITT.search(text))
    d.unterlagen = [u for u in _UNTERLAGEN if re.search(rf"\b{u}\b", text)]
    anforderungen = _abschnitt(text, _MUSS_KOPF)
    d.kann = [a for a in anforderungen if _KANN.search(a)]
    d.muss = [a for a in anforderungen if a not in d.kann]
    d.aufgaben = _abschnitt(text, _AUFGABEN_KOPF)[:5]
    woerter = re.findall(r"\w+", text[:3000])
    if woerter and len(_EN.findall(text[:3000])) / len(woerter) > 0.06:
        d.sprache = "en"
    return d


# --------------------------------------------------------------------------- #
# KI
# --------------------------------------------------------------------------- #

def aus_llm(job: Job, profil: Profil) -> Dossier:
    anzeige = textwrap.shorten(job.beschreibung or job.titel, width=12000, placeholder=" [...]")
    text = llm_modul.chat(
        profil.bewertung.modell, system=SYSTEM,
        nutzer=f"Titel: {job.titel}\nArbeitgeber: {job.arbeitgeber}\n\nAnzeige:\n{anzeige}",
        max_tokens=4000, json_schema=SCHEMA, effort="low",
    )
    daten = llm_modul.json_aus_text(text)
    erlaubt = set(Dossier.__dataclass_fields__)
    d = Dossier(**{k: v for k, v in daten.items() if k in erlaubt})
    d.quelle = "llm"
    d.frist = iso_datum(d.frist) if d.frist else None
    return d


def erstelle(job: Job, profil: Profil, *, mit_llm: bool = True) -> Dossier:
    """Dossier mit KI (falls verfuegbar), ergaenzt um Regeln und strukturierte Quelldaten."""
    regeln = aus_regeln(job)
    d = regeln
    if mit_llm and llm_modul.client_verfuegbar() and job.beschreibung:
        try:
            d = aus_llm(job, profil)
            for feld in ("ansprechpartner", "referenz", "frist"):
                if not getattr(d, feld):
                    setattr(d, feld, getattr(regeln, feld))
            d.unterlagen = d.unterlagen or regeln.unterlagen
        except Exception as e:
            log.info("Dossier per KI fehlgeschlagen (%s) – Regeln.", e)
            d = regeln
    # Strukturierte Angaben der Quelle sind verlaesslicher als jede Extraktion
    if job.ansprechpartner:
        d.ansprechpartner = job.ansprechpartner
    if job.bewerbungsfrist and re.match(r"\d{4}-\d{2}-\d{2}", job.bewerbungsfrist):
        d.frist = job.bewerbungsfrist
    d.erstellt_am = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return d


def fuer_job(db: Any, job: Job, profil: Profil, *, mit_llm: bool = True, neu: bool = False) -> Dossier:
    """Gespeichertes Dossier nutzen oder neu erstellen und an der Stelle speichern."""
    vorhanden = None if neu else Dossier.aus_json(job.dossier)
    if vorhanden and (vorhanden.quelle == "llm" or not (mit_llm and llm_modul.client_verfuegbar())):
        return vorhanden
    d = erstelle(job, profil, mit_llm=mit_llm)
    job.dossier = d.als_json()
    if d.ansprechpartner and not job.ansprechpartner:
        job.ansprechpartner = d.ansprechpartner
    if d.frist and not job.bewerbungsfrist:
        job.bewerbungsfrist = d.frist
    if db is not None:
        db.speichere_jobs([job])
    return d
