"""Der Lebenslauf als Quelle: Kurzprofil, Jobtitel, Ort und Faehigkeiten daraus ableiten.

Einmal je neuem Inhalt (Pruefsumme des Texts in daten/lebenslauf_ableitung.json): mit KI, ohne KI
per Heuristik (dann nur Kontaktdaten). Das Ergebnis wird ins Profil uebernommen - Kurzprofil,
Jobtitel der Suche, Suchort und Faehigkeiten werden ersetzt, Kontaktdaten nur ergaenzt.
Jobtitel und Ort bleiben unter „Suchen“ jederzeit aenderbar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import dokumente, zeitplan
from .config import Profil, daten_dir, ermittle_profil_pfad, speichere_profil
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

KONTAKT_FELDER = ("vorname", "nachname", "email", "telefon", "strasse", "plz", "ort")
# Ohne KI ausgelesen: spaeter (wenn die KI wieder antwortet) noch einmal versuchen
NEU_VERSUCHEN_STUNDEN = 6


@dataclass
class Ableitung:
    kurzprofil: str = ""
    jobtitel: list[str] = field(default_factory=list)
    ort: str = ""
    faehigkeiten: list[str] = field(default_factory=list)
    kontakt: dict[str, str] = field(default_factory=dict)
    quelle: str = "heuristik"  # ki | heuristik
    pruefsumme: str = ""
    zeit: str = ""


_SCHEMA = {
    "type": "object",
    "properties": {
        "kurzprofil": {"type": "string", "description": "1-3 knappe Saetze, nur Fakten aus dem Lebenslauf"},
        "jobtitel": {"type": "array", "items": {"type": "string"},
                     "description": "3-6 Suchbegriffe fuer deutsche Jobboersen, je 1-4 Woerter, ohne (m/w/d)"},
        "ort": {"type": "string", "description": "Wohnort (nur die Stadt), leer wenn nicht erkennbar"},
        "faehigkeiten": {"type": "array", "items": {"type": "string"},
                         "description": "5-15 konkrete Faehigkeiten, die im Lebenslauf stehen"},
        "kontakt": {
            "type": "object",
            "properties": {f: {"type": "string"} for f in KONTAKT_FELDER},
            "required": list(KONTAKT_FELDER),
            "additionalProperties": False,
        },
    },
    "required": ["kurzprofil", "jobtitel", "ort", "faehigkeiten", "kontakt"],
    "additionalProperties": False,
}

_SYSTEM = """Du liest den Lebenslauf einer arbeitssuchenden Person und bereitest ihre Jobsuche vor.

- kurzprofil: 1-3 knappe Saetze ohne Floskeln, z. B. "8 Jahre Buchhaltung im Mittelstand, DATEV und SAP,
  Bilanzbuchhalterin (IHK)". Nur, was im Lebenslauf steht - nichts erfinden.
- jobtitel: 3-6 gaengige Jobtitel, unter denen passende Stellen auf deutschen Jobboersen zu finden sind,
  passend zur juengsten Erfahrung und zum Niveau. Je 1-4 Woerter, ohne (m/w/d).
- ort: der Wohnort (nur die Stadt), sonst leer.
- faehigkeiten: 5-15 konkrete Kenntnisse (Werkzeuge, Fachgebiete, Sprachen), die im Lebenslauf stehen.
- kontakt: Vorname, Nachname, E-Mail, Telefon, Strasse mit Hausnummer, PLZ, Ort - genau wie im Lebenslauf,
  fehlende Angaben leer lassen.
Antworte nur im vorgegebenen JSON-Schema."""


def _datei() -> Path:
    return daten_dir() / "lebenslauf_ableitung.json"


def pruefsumme(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def gespeichert() -> Ableitung | None:
    """Zuletzt abgeleitete Angaben (auch nach einem Neustart)."""
    try:
        roh = json.loads(_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(roh, dict):
        return None
    bekannt = {f.name for f in fields(Ableitung)}
    return Ableitung(**{k: v for k, v in roh.items() if k in bekannt})


def _speichern(ableitung: Ableitung) -> None:
    try:
        _datei().parent.mkdir(parents=True, exist_ok=True)
        _datei().write_text(json.dumps(asdict(ableitung), ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("Lebenslauf-Auswertung nicht gespeichert: %s", e)


# --------------------------------------------------------------------------- #
# Auslesen
# --------------------------------------------------------------------------- #

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TELEFON = re.compile(r"(?:\+\d{2}|\b0)[\d ()/\-]{6,18}\d")
_PLZ_ORT = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][a-zäöüß]+(?:[ -](?:am|an|im|in|bei|der|[A-ZÄÖÜ][a-zäöüß]+))*)")
_STRASSE = re.compile(
    r"^([A-ZÄÖÜ][\wäöüß.\- ]{1,40}(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer)\s*\d+\s*[a-zA-Z]?)\b",
    re.I | re.M,
)
_NAME = re.compile(r"^([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜ][a-zäöüß]+)?)$")
_KEIN_NAME = {"lebenslauf", "curriculum", "persönliche", "personliche", "kontakt", "profil", "berufserfahrung"}


def heuristik(text: str) -> Ableitung:
    """Ohne KI: nur, was sich sicher erkennen laesst (E-Mail, Telefon, Adresse, Name)."""
    kontakt: dict[str, str] = {}
    if m := _EMAIL.search(text):
        kontakt["email"] = m.group(0).strip(".").lower()
    if m := _TELEFON.search(text):
        kontakt["telefon"] = re.sub(r"\s+", " ", m.group(0)).strip()
    if m := _PLZ_ORT.search(text):
        kontakt["plz"], kontakt["ort"] = m.group(1), m.group(2).strip()
    if m := _STRASSE.search(text):
        kontakt["strasse"] = m.group(1).strip()
    for zeile in text.splitlines()[:4]:
        m = _NAME.match(zeile.strip())
        if m and m.group(1).lower() not in _KEIN_NAME:
            kontakt["vorname"], kontakt["nachname"] = m.group(1), m.group(2)
            break
    return Ableitung(ort=kontakt.get("ort", ""), kontakt=kontakt, quelle="heuristik")


def _liste(werte: Any, grenze: int, *, titel: bool = False) -> list[str]:
    from .vorschlaege import titel_kern

    ergebnis: list[str] = []
    for wert in werte if isinstance(werte, list) else []:
        text = titel_kern(str(wert)) if titel else re.sub(r"\s+", " ", str(wert)).strip(" ,;.-")
        if text and text.casefold() not in {e.casefold() for e in ergebnis}:
            ergebnis.append(text[:80])
    return ergebnis[:grenze]


def per_ki(profil: Profil, text: str) -> Ableitung:
    """Mit KI auslesen; wirft LLMNichtVerfuegbar ohne KI-Zugang."""
    from .vorschlaege import _json_aus_text

    antwort = llm_modul.chat(
        profil.bewertung.modell, system=_SYSTEM, nutzer=f"Lebenslauf:\n{text[:12000]}",
        max_tokens=4000, json_schema=_SCHEMA, effort="low",
    )
    daten = _json_aus_text(antwort)
    roh_kontakt = daten.get("kontakt") if isinstance(daten.get("kontakt"), dict) else {}
    kontakt = {f: str(roh_kontakt.get(f) or "").strip() for f in KONTAKT_FELDER}
    ort = re.sub(r"^\d{5}\s*", "", str(daten.get("ort") or "")).strip()
    return Ableitung(
        kurzprofil=re.sub(r"\s+", " ", str(daten.get("kurzprofil") or "")).strip()[:600],
        jobtitel=_liste(daten.get("jobtitel"), 6, titel=True),
        ort=ort or kontakt.get("ort", ""),
        faehigkeiten=_liste(daten.get("faehigkeiten"), 15),
        kontakt={k: v for k, v in kontakt.items() if v},
        quelle="ki",
    )


def ableiten(profil: Profil, text: str, *, mit_ki: bool = True) -> Ableitung:
    """Mit KI, sonst (oder wenn sie nicht antwortet) per Heuristik."""
    ergebnis: Ableitung | None = None
    if mit_ki and llm_modul.client_verfuegbar():
        try:
            ergebnis = per_ki(profil, text)
        except Exception as e:  # KI weg, Guthaben leer, kaputte Antwort: Heuristik reicht fuer Kontaktdaten
            log.warning("Lebenslauf mit KI nicht auslesbar: %s", e)
        if ergebnis is not None:
            # Was die KI nicht sicher erkannt hat, ergaenzt die Heuristik
            for k, v in heuristik(text).kontakt.items():
                ergebnis.kontakt.setdefault(k, v)
    ergebnis = ergebnis or heuristik(text)
    ergebnis.pruefsumme = pruefsumme(text)
    ergebnis.zeit = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return ergebnis


# --------------------------------------------------------------------------- #
# Uebernehmen
# --------------------------------------------------------------------------- #

def uebernehmen(profil: Profil, a: Ableitung) -> list[str]:
    """Ins Profil uebernehmen (ohne zu speichern); Rueckgabe: was sich geaendert hat."""
    aenderungen: list[str] = []
    if a.kurzprofil and a.kurzprofil != profil.kurzprofil:
        profil.kurzprofil = a.kurzprofil
        aenderungen.append("Kurzprofil")
    if a.jobtitel and a.jobtitel != profil.suche.was:
        profil.suche.was = list(a.jobtitel)
        aenderungen.append("Jobtitel")
    p = profil.person
    ergaenzt = False
    for feld in KONTAKT_FELDER:
        wert = (a.kontakt.get(feld) or "").strip()
        if wert and not getattr(p, feld):
            setattr(p, feld, wert)
            ergaenzt = True
    if ergaenzt:
        aenderungen.append("Kontaktdaten")
    ort = a.ort or p.ort
    if ort and ort != profil.suche.wo:
        profil.suche.wo = ort
        aenderungen.append("Ort")
    if a.faehigkeiten and a.faehigkeiten != profil.bewertung.skills:
        profil.bewertung.skills = list(a.faehigkeiten)
        aenderungen.append("Fähigkeiten")
    return aenderungen


def _nochmal_mit_ki(alt: Ableitung) -> bool:
    if alt.quelle == "ki" or not llm_modul.client_verfuegbar():
        return False
    zeit = zeitplan.als_datetime(alt.zeit)
    return not zeit or datetime.now(timezone.utc) - zeit > timedelta(hours=NEU_VERSUCHEN_STUNDEN)


def aktualisieren(
    profil: Profil, pfad: str | Path | None = None, *, erzwingen: bool = False, mit_ki: bool = True,
) -> tuple[Ableitung | None, list[str]]:
    """Bei neuem Lebenslauf-Inhalt auslesen und uebernehmen (speichert das Profil).

    (Ableitung oder None ohne lesbaren Text, Liste der uebernommenen Bereiche). Derselbe Inhalt wird
    nicht erneut ausgelesen - ausser mit `erzwingen` oder wenn er nur ohne KI ausgelesen wurde.
    """
    text = dokumente.lebenslauf_text(profil)
    if not text.strip():
        return None, []
    alt = gespeichert()
    if alt and alt.pruefsumme == pruefsumme(text) and not erzwingen and not (mit_ki and _nochmal_mit_ki(alt)):
        return alt, []
    neu = ableiten(profil, text, mit_ki=mit_ki)
    if alt and alt.pruefsumme == neu.pruefsumme and alt.quelle == "ki" and neu.quelle != "ki":
        # KI gerade weg: die fruehere, bessere Auswertung behalten
        return alt, []
    aenderungen = uebernehmen(profil, neu)
    if aenderungen:
        speichere_profil(profil, pfad or ermittle_profil_pfad())
    _speichern(neu)
    return neu, aenderungen
