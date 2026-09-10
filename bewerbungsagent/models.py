"""Datenmodelle fuer Stellen, Bewertungen und Bewerbungen."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """Ein normalisiertes Stellenangebot, quellenunabhaengig."""

    ref: str
    titel: str
    arbeitgeber: str
    quelle: str = "arbeitsagentur"
    beruf: str | None = None
    ort: str | None = None
    plz: str | None = None
    region: str | None = None
    entfernung_km: float | None = None
    veroeffentlicht: str | None = None
    eintrittsdatum: str | None = None
    externe_url: str | None = None
    detail_url: str | None = None
    beschreibung: str | None = None
    homeoffice: bool | None = None
    vollzeit: bool | None = None
    teilzeit: bool | None = None
    befristung: str | None = None
    verguetung: str | None = None
    geholt_am: str = field(default_factory=_now)
    
    # --- Neue strukturierte Details (extrahiert vom LLM) ---
    anforderungen: str | None = None  # Skills, Erfahrung (JSON-String oder Text)
    team_info: str | None = None      # Team-Größe, Abteilung, Hierarchie
    tech_stack: str | None = None     # Verwendete Technologien (JSON-Array oder Text)
    aufgaben: str | None = None       # Top 3-5 Verantwortungen (JSON-Array oder Text)
    benefits: str | None = None       # Zusätzliche Benefits (JSON-Array oder Text)
    details_quelle: str = "keine"     # "keine", "heuristik", "llm"
    details_generiert_am: str = ""    # Zeitstempel der Detail-Extraktion

    @property
    def bewerbungs_url(self) -> str | None:
        """URL, auf der die Bewerbung tatsaechlich abgegeben wird."""
        return self.externe_url or self.detail_url

    @property
    def volltext(self) -> str:
        teile = [self.titel, self.beruf or "", self.arbeitgeber, self.beschreibung or ""]
        return "\n".join(t for t in teile if t)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Score:
    """Bewertung einer Stelle: fachliche Passung + Tonalitaet der Anzeige."""

    ref: str
    gesamt: float
    passung: float
    sentiment: float
    treffer: list[str] = field(default_factory=list)
    gruen: list[str] = field(default_factory=list)
    rot: list[str] = field(default_factory=list)
    begruendung: str = ""
    ausgeschlossen: bool = False
    ausschlussgrund: str | None = None
    bewerter: str = "heuristik"
    bewertet_am: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("treffer", "gruen", "rot"):
            d[k] = json.dumps(d[k], ensure_ascii=False)
        return d


@dataclass
class Application:
    """Protokoll eines Bewerbungsversuchs."""

    ref: str
    status: str  # vorbereitet | probelauf | abgeschickt | fehlgeschlagen | uebersprungen
    url: str | None = None
    anschreiben: str | None = None
    ergebnis: str | None = None
    schritte: int = 0
    dry_run: bool = True
    zeitpunkt: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
