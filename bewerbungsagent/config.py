"""Laden und Validieren des Bewerberprofils (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

STANDARD_PFAD = Path("config/profil.yaml")
BENUTZER_PFAD = Path.home() / ".config" / "bewerbungsagent" / "profil.yaml"


@dataclass
class Person:
    vorname: str = ""
    nachname: str = ""
    email: str = ""
    telefon: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "Deutschland"
    linkedin: str = ""
    github: str = ""
    website: str = ""
    geburtsdatum: str = ""

    @property
    def name(self) -> str:
        return f"{self.vorname} {self.nachname}".strip()


@dataclass
class Suche:
    was: list[str] = field(default_factory=list)
    wo: str = "München"
    umkreis: int = 25
    veroeffentlicht_seit_tagen: int = 30
    nur_vollzeit: bool = False
    nur_homeoffice: bool = False
    max_pro_query: int = 50
    # Namen aus bewerbungsagent.sources.QUELLEN (Standard: alle ohne API-Key)
    quellen: list[str] = field(default_factory=lambda: [
        "arbeitsagentur", "get-in-it", "arbeitnow", "service-bund", "interamt", "remote", "wunscharbeitgeber",
    ])
    remote: bool = True              # auch Remote-Stellen, die Bewerber aus Deutschland ansprechen
    ausland: list[str] = field(default_factory=list)  # EURES-Laender, z. B. ["at", "ch"]
    ausbildung: bool = False         # zusaetzlich Ausbildungsplaetze (Arbeitsagentur)


@dataclass
class Bewertung:
    skills: list[str] = field(default_factory=list)
    wunsch: list[str] = field(default_factory=list)
    ausschluss: list[str] = field(default_factory=list)
    min_score: float = 60.0
    modell: str = "claude-opus-5"
    effort: str = "medium"
    max_llm_pro_lauf: int = 40       # Kostenbremse: so viele KI-Bewertungen je Lauf
    vorfilter_min: float = 25.0      # unter diesem Stichwort-Score keine KI-Bewertung
    # Per "Nein - Arbeitgeber nie wieder vorschlagen" gefuellt
    ausschluss_arbeitgeber: list[str] = field(default_factory=list)


@dataclass
class Unterlagen:
    lebenslauf: str = ""
    zeugnisse: list[str] = field(default_factory=list)
    anschreiben_stil: str = (
        "sachlich, konkret, ohne Floskeln, maximal 250 Woerter, deutsche Anrede Sie"
    )


@dataclass
class Angaben:
    """Zusatzangaben, die Bewerbungsformulare haeufig als Pflichtfeld verlangen."""

    anrede: str = ""                 # Herr | Frau | divers
    verfuegbar_ab: str = "ab sofort"
    kuendigungsfrist: str = "keine"
    gehaltsvorstellung: str = ""     # z. B. "55.000 EUR brutto/Jahr"
    arbeitserlaubnis: str = ""       # z. B. "deutsche Staatsangehoerigkeit"
    fuehrerschein: str = ""          # z. B. "Klasse B"
    sprachen: str = ""               # z. B. "Deutsch (Muttersprache), Englisch (C1)"
    titel: str = ""                  # akademischer Titel, z. B. "Dr."
    nationalitaet: str = ""
    abschluss: str = ""              # hoechster Abschluss, z. B. "B.Sc. Informatik"
    berufserfahrung_jahre: str = ""
    umzugsbereitschaft: str = ""     # z. B. "nein" / "innerhalb Bayerns"
    # Weitere Antworten, die der Agent in Formularen gelernt hat (Frage -> Antwort)
    weitere: dict[str, str] = field(default_factory=dict)


MODI = ("automatisch", "nur_suchen", "aus")
AUTOPILOT = ("aus", "mit_frist", "sofort")


@dataclass
class Automatisierung:
    """Zeitplan und Grenzen fuer den automatischen Ablauf (Cron ruft `tick` auf)."""

    aktiv: bool = True
    # automatisch: suchen + bewerten + Bewerbungen vorbereiten (warten auf Ja/Nein)
    # nur_suchen:  suchen + bewerten, Auswahl manuell
    # aus:         kein Zeitplan, nur der manuelle Prozess
    modus: str = "automatisch"
    cron: str = "0 6 * * *"
    max_pro_lauf: int = 5       # so viele Bewerbungen bereitet ein Lauf hoechstens vor
    max_pro_tag: int = 10       # Schutz vor Versehen: hoechstens so viele Sendungen pro Tag
    probelauf_vorab: bool = True  # Online-Formular schon beim Vorbereiten testweise ausfuellen
    ziel_pro_woche: int = 5
    # Autopilot: Bewerbungen, die alle Pruefungen bestehen, gehen ohne Ja raus
    # aus | mit_frist (nach `autopilot_frist_stunden`, wenn kein Nein kommt) | sofort
    autopilot: str = "aus"
    autopilot_frist_stunden: int = 2
    autopilot_min_passung: float = 75.0
    autopilot_min_qualitaet: float = 80.0
    erkunden: bool = True            # neue Suchbegriffe selbststaendig ausprobieren
    erkundung_max_begriffe: int = 2  # pro Lauf
    postfach: bool = True            # Antworten per IMAP erkennen (wenn eingerichtet)
    nachfassen_tage: int = 14        # danach Nachfass-Mail vorschlagen (0 = aus)

    @property
    def effektiver_autopilot(self) -> str:
        return self.autopilot if self.autopilot in AUTOPILOT else "aus"

    @property
    def effektiver_modus(self) -> str:
        if not self.aktiv:
            return "aus"
        return self.modus if self.modus in MODI else "automatisch"


@dataclass
class Profil:
    person: Person = field(default_factory=Person)
    suche: Suche = field(default_factory=Suche)
    bewertung: Bewertung = field(default_factory=Bewertung)
    unterlagen: Unterlagen = field(default_factory=Unterlagen)
    automatisierung: Automatisierung = field(default_factory=Automatisierung)
    angaben: Angaben = field(default_factory=Angaben)
    kurzprofil: str = ""
    # Wunsch-Arbeitgeber: [{name, url, ats, kennung}] - Stellen direkt aus deren Bewerbermanagement
    zielfirmen: list[dict[str, str]] = field(default_factory=list)

    # --- Zugangsdaten kommen ausschliesslich aus der Umgebung, nie aus der YAML ---
    @property
    def ba_benutzer(self) -> str | None:
        return os.environ.get("BA_BENUTZER")

    @property
    def ba_passwort(self) -> str | None:
        return os.environ.get("BA_PASSWORT")

    def anhaenge(self) -> list[str]:
        pfade = [self.unterlagen.lebenslauf, *self.unterlagen.zeugnisse]
        return [str(Path(p).expanduser().resolve()) for p in pfade if p and Path(p).expanduser().exists()]

    def fehlende_anhaenge(self) -> list[str]:
        pfade = [self.unterlagen.lebenslauf, *self.unterlagen.zeugnisse]
        return [p for p in pfade if p and not Path(p).expanduser().exists()]


def _fill(cls, daten: dict[str, Any] | None):
    daten = daten or {}
    erlaubt = {f for f in cls.__dataclass_fields__}
    unbekannt = set(daten) - erlaubt
    if unbekannt:
        raise ValueError(f"Unbekannte Felder in {cls.__name__}: {sorted(unbekannt)}")
    return cls(**daten)


def lade_profil(pfad: str | Path | None = None) -> Profil:
    p = ermittle_profil_pfad(pfad)
    if not p.exists():
        raise FileNotFoundError(
            "Noch kein Profil angelegt. Öffne die Seite '👤 Profil' und lege dort "
            "dein Profil über die Oberfläche an."
        )
    roh = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    profil = Profil(
        person=_fill(Person, roh.get("person")),
        suche=_fill(Suche, roh.get("suche")),
        bewertung=_fill(Bewertung, roh.get("bewertung")),
        unterlagen=_fill(Unterlagen, roh.get("unterlagen")),
        automatisierung=_fill(Automatisierung, roh.get("automatisierung")),
        angaben=_fill(Angaben, roh.get("angaben")),
        kurzprofil=roh.get("kurzprofil", "") or "",
        zielfirmen=[dict(z) for z in (roh.get("zielfirmen") or []) if isinstance(z, dict)],
    )
    if profil.suche.quellen == ["arbeitsagentur", "get-in-it"]:
        # Unveraenderter alter Standard: neue Jobboersen automatisch mitnehmen
        profil.suche.quellen = Suche().quellen
    if not profil.suche.was:
        raise ValueError("suche.was ist leer - mindestens ein Suchbegriff noetig.")
    return profil


def ermittle_profil_pfad(pfad: str | Path | None = None) -> Path:
    """Liefert den expliziten oder den vorhandenen Standardpfad für das Profil."""
    if pfad is not None:
        return Path(pfad)
    if STANDARD_PFAD.exists():
        return STANDARD_PFAD
    if BENUTZER_PFAD.exists():
        return BENUTZER_PFAD
    return STANDARD_PFAD


def erstelle_profil(pfad: str | Path | None = None) -> Path:
    """Legt ein bearbeitbares Basisprofil für die UI an."""
    profil = Profil(suche=Suche(was=["Softwareentwickler Python"]))
    return speichere_profil(profil, ermittle_profil_pfad(pfad))


def _to_dict(obj: Any) -> dict[str, Any]:
    return {f: getattr(obj, f) for f in obj.__dataclass_fields__}


def speichere_profil(profil: Profil, pfad: str | Path | None = None) -> Path:
    p = Path(pfad or STANDARD_PFAD)
    daten = {
        "person": _to_dict(profil.person),
        "kurzprofil": profil.kurzprofil,
        "suche": _to_dict(profil.suche),
        "bewertung": _to_dict(profil.bewertung),
        "unterlagen": _to_dict(profil.unterlagen),
        "automatisierung": _to_dict(profil.automatisierung),
        "angaben": _to_dict(profil.angaben),
        "zielfirmen": profil.zielfirmen,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomar schreiben: UI, Hintergrund-Worker und Cron lesen parallel
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(yaml.safe_dump(daten, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def lade_env(pfad: str | Path = ".env") -> int:
    """Liest KEY=VALUE-Zeilen aus einer .env, ohne gesetzte Variablen zu ueberschreiben.

    Docker uebergibt die Umgebung direkt; lokal (CLI, Cron, UI) reicht so eine
    .env im Arbeitsverzeichnis. Liefert die Anzahl neu gesetzter Variablen.
    """
    p = Path(pfad)
    if not p.is_file():
        return 0
    gesetzt = 0
    for zeile in p.read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        if zeile.startswith("export "):
            zeile = zeile[7:].lstrip()
        name, _, wert = zeile.partition("=")
        name, wert = name.strip(), wert.strip()
        if wert[:1] in ("'", '"') and wert[-1:] == wert[:1] and len(wert) >= 2:
            wert = wert[1:-1]
        elif " #" in wert:
            wert = wert.split(" #", 1)[0].rstrip()
        if wert.endswith("..."):  # Platzhalter aus .env.example
            continue
        if name and wert and name not in os.environ:
            os.environ[name] = wert
            gesetzt += 1
    return gesetzt
