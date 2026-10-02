"""Laden und Validieren des Bewerberprofils (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

STANDARD_PFAD = Path("config/profil.yaml")
BENUTZER_PFAD = Path.home() / ".config" / "bewerbungsagent" / "profil.yaml"
STANDARD_SUCHORT = "München"


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
    wo: str = STANDARD_SUCHORT
    umkreis: int = 25
    veroeffentlicht_seit_tagen: int = 30
    nur_vollzeit: bool = False
    nur_homeoffice: bool = False
    max_pro_query: int = 50


@dataclass
class Bewertung:
    skills: list[str] = field(default_factory=list)
    wunsch: list[str] = field(default_factory=list)
    ausschluss: list[str] = field(default_factory=list)
    min_score: float = 60.0
    modell: str = "claude-opus-5"
    effort: str = "medium"


@dataclass
class Unterlagen:
    lebenslauf: str = ""
    zeugnisse: list[str] = field(default_factory=list)
    anschreiben_stil: str = (
        "sachlich, konkret, ohne Floskeln, maximal 250 Woerter, deutsche Anrede Sie"
    )


@dataclass
class SMTPSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    sender_email: str = ""
    starttls: bool = True
    use_ssl: bool = False


@dataclass
class Profil:
    person: Person = field(default_factory=Person)
    suche: Suche = field(default_factory=Suche)
    bewertung: Bewertung = field(default_factory=Bewertung)
    unterlagen: Unterlagen = field(default_factory=Unterlagen)
    kurzprofil: str = ""
    smtp: SMTPSettings = field(default_factory=SMTPSettings)

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
        kurzprofil=roh.get("kurzprofil", ""),
        smtp=_fill(SMTPSettings, roh.get("smtp")),
    )
    return profil


def profil_ist_beispiel(profil: Profil) -> bool:
    """Erkennt ausschliesslich das frueher automatisch angelegte Demo-Profil."""
    return (
        profil.person.vorname == "Anna"
        and profil.person.nachname == "Müller"
        and profil.person.email == "anna.mueller@example.de"
    )


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
    """Legt ein leeres, bearbeitbares Profil ohne erfundene Personendaten an."""
    profil = Profil()
    pfad = ermittle_profil_pfad(pfad)
    return speichere_profil(profil, pfad)


def ensure_seeded_profil(pfad: str | Path | None = None) -> Profil:
    """Sichert ein nutzbares Standardprofil, damit die App beim Start sofort einsatzbereit ist."""
    p = ermittle_profil_pfad(pfad)
    if not p.exists():
        erstelle_profil(p)
    return lade_profil(p)


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
        "smtp": _to_dict(profil.smtp),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(daten, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return p
