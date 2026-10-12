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
    # Jede Quelle setzt ihren Namen selbst (kein stiller Arbeitsagentur-Standard)
    quelle: str = ""
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
    strasse: str | None = None            # Anschrift des Arbeitgebers (Adressfeld im Anschreiben)
    bewerbungsfrist: str | None = None    # JJJJ-MM-TT oder Text ("baldmoeglichst")
    kontakt_email: str | None = None      # strukturiert von der Quelle geliefert
    ansprechpartner: str | None = None    # "Frau Dr. Erika Muster"
    bewerbung_hinweis: str | None = None  # z. B. "Online ueber interamt.de (Konto noetig)"
    dossier: str | None = None            # JSON aus bewerbungsagent.dossier (Anforderungen, Anrede ...)
    
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
    def anzeige_url(self) -> str | None:
        """Klickbarer Link zum Jobangebot fuer die UI-Anzeige."""
        url = self.bewerbungs_url
        if url:
            return url
        if self.ref and self.quelle == "arbeitsagentur":
            return f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{self.ref}"
        return None

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


# Status eines Bewerbungsvorgangs (eine Zeile pro Stelle in `vorgaenge`).
VORGANG_STATUS = (
    "vorbereitung",     # Anschreiben/Probelauf laufen gerade
    "wartet",           # bereit, wartet auf Ja/Nein des Nutzers
    "freigegeben",      # Ja gesagt, Versand steht aus
    "wird_gesendet",    # ein Worker sendet gerade
    "abgeschickt",
    "fehlgeschlagen",
    "abgelehnt",        # Nein gesagt / nicht interessant
    "selbst_beworben",  # Nutzer hat sich selbst beworben
)

KANAELE = ("email", "formular", "manuell")

RUECKMELDUNGEN = ("", "eingangsbestaetigung", "einladung", "absage", "zusage")


@dataclass
class Vorgang:
    """Aktueller Stand einer Bewerbung auf genau eine Stelle."""

    ref: str
    status: str = "vorbereitung"
    kanal: str = "manuell"
    empfaenger: str | None = None
    bewerbungs_url: str | None = None
    betreff: str | None = None
    anschreiben: str | None = None
    anschreiben_pdf: str | None = None
    email_text: str | None = None
    probelauf: str | None = None  # JSON des Probelauf-Ergebnisses
    hinweis: str | None = None
    herkunft: str = "manuell"  # automatik | manuell
    erstellt_am: str = field(default_factory=_now)
    aktualisiert_am: str = field(default_factory=_now)
    freigegeben_am: str | None = None
    gesendet_am: str | None = None
    rueckmeldung: str = ""
    rueckmeldung_am: str | None = None
    notiz: str | None = None
    # --- Qualitaet & Lernen ---
    anschreiben_original: str | None = None  # wie vom Agenten erzeugt (Aenderungen = Lernsignal)
    anschreiben_quelle: str | None = None    # llm | vorlage
    qualitaet: float | None = None           # 0-100 aus der Selbstpruefung
    pruefung: str | None = None              # JSON-Pruefbericht
    message_id: str | None = None            # SMTP-Message-ID (Antworten zuordnen)
    rueckmeldung_quelle: str | None = None   # manuell | postfach
    auto_senden_ab: str | None = None        # Autopilot: geht ab diesem Zeitpunkt ohne Ja raus

    @property
    def pruefung_daten(self) -> dict[str, Any]:
        try:
            daten = json.loads(self.pruefung or "{}")
        except json.JSONDecodeError:
            return {}
        return daten if isinstance(daten, dict) else {}

    @property
    def probelauf_daten(self) -> dict[str, Any]:
        if not self.probelauf:
            return {}
        try:
            daten = json.loads(self.probelauf)
        except json.JSONDecodeError:
            return {}
        return daten if isinstance(daten, dict) else {}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
