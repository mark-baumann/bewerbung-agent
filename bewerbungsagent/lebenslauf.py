"""Der Lebenslauf als Quelle: Kurzprofil, Jobtitel, Suchort, Faehigkeiten und Stationen daraus ableiten.

Einmal je neuem Inhalt (Pruefsumme des Texts in daten/lebenslauf_ableitung.json): mit KI, sonst per
Heuristik nach Abschnitten (Ueber mich, Faehigkeiten, Erfahrung ...). Das Ergebnis wird ins Profil
uebernommen, ohne Aenderungen des Nutzers zu zerstoeren: ein Feld wird nur ersetzt, wenn es leer ist
oder noch dem zuletzt uebernommenen Wert entspricht; eigene Jobtitel und Faehigkeiten bleiben dahinter
erhalten, Kontaktdaten werden nur ergaenzt. Jobtitel und Ort bleiben unter „Suchen“ jederzeit aenderbar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from itertools import takewhile
from pathlib import Path
from typing import Any

from . import dokumente, zeitplan
from .config import Profil, daten_dir, ermittle_profil_pfad, speichere_profil
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

# Hoeher setzen, wenn sich das Auslesen verbessert: aeltere Auswertungen werden dann einmal neu gelesen
VERSION = 3
KONTAKT_FELDER = ("vorname", "nachname", "email", "telefon", "strasse", "plz", "ort")
# Ohne KI ausgelesen: hoechstens so oft einen neuen KI-Versuch starten
NEU_VERSUCHEN_STUNDEN = 6
# Umkreis um den Suchort: um die naechste Grossstadt (KI) bzw. um den Wohnort (ohne KI, damit die Stadt drin ist)
UMKREIS_SUCHORT = 30
UMKREIS_WOHNORT = 50
MAX_JOBTITEL = 10
MAX_FAEHIGKEITEN = 20


@dataclass
class Ableitung:
    kurzprofil: str = ""
    jobtitel: list[str] = field(default_factory=list)
    ort: str = ""            # Wohnort laut Lebenslauf
    suchort: str = ""        # wo gesucht wird (bei kleinen Orten die naechste groessere Stadt)
    faehigkeiten: list[str] = field(default_factory=list)
    themen: list[str] = field(default_factory=list)  # Fachgebiete/Interessen fuer die Bewertung (Wunschthemen)
    # rolle, arbeitgeber, zeitraum, sicher (neueste zuerst). Nur sichere Stationen gehen in Anschreiben und Jobtitel
    stationen: list[dict[str, Any]] = field(default_factory=list)
    kontakt: dict[str, str] = field(default_factory=dict)
    quelle: str = "heuristik"  # ki | heuristik
    fehler: str = ""           # warum die KI nicht geholfen hat (leer: KI ok oder kein KI-Zugang)
    pruefsumme: str = ""
    zeit: str = ""             # letzter Versuch
    version: int = 0
    uebernommen: dict[str, Any] = field(default_factory=dict)  # was zuletzt ins Profil geschrieben wurde


_STATION = {
    "type": "object",
    "properties": {
        "rolle": {"type": "string", "description": "Rolle bzw. Taetigkeit genau wie im Lebenslauf"},
        "arbeitgeber": {"type": "string"},
        "zeitraum": {"type": "string", "description": "z. B. 03/2023 - 03/2026 oder 2024 - heute"},
    },
    "required": ["rolle", "arbeitgeber", "zeitraum"],
    "additionalProperties": False,
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "kurzprofil": {"type": "string", "description": "1-3 knappe Saetze, nur Fakten aus dem Lebenslauf"},
        "jobtitel": {"type": "array", "items": {"type": "string"},
                     "description": "3-8 Suchbegriffe fuer deutsche Jobboersen, je 1-4 Woerter, ohne (m/w/d)"},
        "ort": {"type": "string", "description": "Wohnort (nur die Stadt), leer wenn nicht erkennbar"},
        "suchort": {"type": "string", "description": "Ort fuer die Stellensuche (siehe Regeln)"},
        "faehigkeiten": {"type": "array", "items": {"type": "string"},
                         "description": "5-15 konkrete Faehigkeiten, die im Lebenslauf stehen"},
        "themen": {"type": "array", "items": {"type": "string"},
                   "description": "2-6 Fachgebiete, in denen die Person arbeiten will oder Erfahrung hat"},
        "stationen": {"type": "array", "items": _STATION, "description": "Berufsstationen, neueste zuerst"},
        "kontakt": {
            "type": "object",
            "properties": {f: {"type": "string"} for f in KONTAKT_FELDER},
            "required": list(KONTAKT_FELDER),
            "additionalProperties": False,
        },
    },
    "required": ["kurzprofil", "jobtitel", "ort", "suchort", "faehigkeiten", "themen", "stationen", "kontakt"],
    "additionalProperties": False,
}

_SYSTEM = """Du liest den Lebenslauf einer arbeitssuchenden Person und bereitest ihre Jobsuche vor.

- kurzprofil: 1-3 knappe Saetze in der Ich-Form ohne Floskeln (sie stehen so im Anschreiben), z. B. "Ich bin
  Bilanzbuchhalterin (IHK) mit acht Jahren Erfahrung im Mittelstand, DATEV und SAP." Nur, was im Lebenslauf
  steht - nichts erfinden.
- jobtitel: 3-8 gaengige Jobtitel, unter denen passende Stellen auf deutschen Jobboersen zu finden sind.
  Beruecksichtige ALLE Stationen, Ausbildung/Studium und Faehigkeiten (nicht nur die erste Faehigkeit), passend
  zum Niveau - z. B. Produktmanager, IT-Projektmanager, Prozessmanager Digitalisierung, Softwareentwickler Python.
  Je 1-4 Woerter, ohne (m/w/d).
- ort: der Wohnort (nur die Stadt), sonst leer.
- suchort: der Ort, um den Stellen gesucht werden: der Wohnort - ist das ein kleiner Ort, die naechste groessere
  Stadt im Pendelbereich (z. B. Giebing -> Muenchen, Garching -> Muenchen). Nur der Ortsname, sonst leer.
- faehigkeiten: 5-15 konkrete Kenntnisse (Werkzeuge, Fachgebiete, Sprachen), die im Lebenslauf stehen.
- themen: 2-6 Fachgebiete aus Werdegang und Profil, nach denen Stellen besonders gut passen (z. B.
  Digitalisierung, Prozessautomatisierung, Produktmanagement).
- stationen: die Berufsstationen, neueste zuerst: Rolle (wie im Lebenslauf), Arbeitgeber, Zeitraum.
- kontakt: Vorname, Nachname, E-Mail, Telefon, Strasse mit Hausnummer, PLZ, Ort - genau wie im Lebenslauf,
  fehlende Angaben leer lassen (ein Geburtsdatum ist keine Telefonnummer).
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
# Heuristik: Abschnitte und strenge Muster
# --------------------------------------------------------------------------- #

_UEBERSCHRIFTEN = {
    "profil": ("über mich", "ueber mich", "profil", "kurzprofil", "persönliches profil", "zusammenfassung",
               "about me", "summary", "profile"),
    "faehigkeiten": ("fähigkeiten", "faehigkeiten", "kenntnisse", "fachkenntnisse", "it-kenntnisse",
                     "edv-kenntnisse", "skills", "kompetenzen", "fachkompetenzen", "qualifikationen", "stärken"),
    "erfahrung": ("erfahrung", "berufserfahrung", "berufliche erfahrung", "beruflicher werdegang", "werdegang",
                  "praxiserfahrung", "praktische erfahrung", "berufspraxis", "experience", "work experience"),
    "ausbildung": ("ausbildung", "bildung", "bildungsweg", "schulbildung", "studium", "akademischer werdegang",
                   "education"),
    "persoenlich": ("persönliche infos", "persönliche daten", "persönliche angaben", "persönliches", "kontakt",
                    "kontaktdaten", "contact"),
    "sonst": ("sprachen", "sprachkenntnisse", "interessen", "hobbys", "hobbies", "zertifikate", "weiterbildung",
              "weiterbildungen", "referenzen", "ehrenamt", "projekte", "lebenslauf", "curriculum vitae",
              "languages", "interests"),
}
_UEBERSCHRIFT_ZU = {name: art for art, namen in _UEBERSCHRIFTEN.items() for name in namen}
_UEBERSCHRIFT_WOERTER = {w for name in _UEBERSCHRIFT_ZU for w in re.split(r"[ -]", name)}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TELEFON_LABEL = re.compile(r"(?:tel(?:efon)?|mobil(?:e)?|handy|phone|fon)\.?\s*:?\s*((?:\+|00|0)[\d ()/\-]{6,20}\d)",
                            re.I)
_TELEFON_OHNE = re.compile(r"(?<![\d/.])((?:\+49|0049)[\d ()/\-]{6,18}\d|01[5-7]\d[\d ()/\-]{5,14}\d)")
_DATUM = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}[./]\d{4}|\d{4}")
_PLZ_ORT = re.compile(r"(?:^|,\s*)(?:D-)?(\d{5})\s+([A-ZÄÖÜ][a-zäöüß]+(?:[ -](?:am|an|im|in|bei|der|ob|"
                      r"[A-ZÄÖÜ][a-zäöüß]+))*)\s*$", re.M)
_KEIN_ORT = {"euro", "eur", "mitarbeiter", "mitarbeitende", "kunden", "stunden", "teilnehmer", "nutzer", "user"}
# Strasse mit Hausnummer am Zeilenanfang. Die Woerter sind eindeutig getrennt (der Trenner gehoert nie zum Wort) und
# hoechstens sechs – so kann das Muster auch bei langen Strich- oder Punktlinien nicht haengen.
_STRASSE = re.compile(r"([A-ZÄÖÜ][\w.]*(?:[ -][\w.]+){0,5})\s+(\d{1,3}\s?[a-zA-Z]?)\s*(?:,|$)")
_STRASSEN_ENDE = re.compile(r"(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer|steig|pfad|chaussee)$",
                            re.I)
_WORT = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?$")
_MONATE = {
    "januar": 1, "january": 1, "jan": 1, "februar": 2, "february": 2, "feb": 2, "märz": 3, "maerz": 3, "march": 3,
    "mär": 3, "mar": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10, "october": 10,
    "okt": 10, "oct": 10, "november": 11, "nov": 11, "dezember": 12, "december": 12, "dez": 12, "dec": 12,
}
_MONAT = r"(?:" + "|".join(sorted(_MONATE, key=len, reverse=True)) + r")\.?"
# Ein Datum in einem Zeitraum: „Mai 2019“, „1. Oktober 2019“, „03/2019“, „01.10.2019“, „2019“ oder „03/20“
_DATUMSANGABE = (rf"(?:\d{{1,2}}\.\s?)?\b{_MONAT}\s+(?:19|20)\d{{2}}"
                 r"|(?:\d{1,2}[./]){1,2}(?:19|20)\d{2}"
                 r"|(?:19|20)\d{2}"
                 r"|\d{1,2}[./]\d{2}(?![\d./])")
# Ohne Ende („2019 –“) nur am Zeilenende – die Zeilen sind bereits ohne Leerzeichen am Rand
_ZEITRAUM = re.compile(
    rf"\b(?:seit|since|ab)\s+(?:{_DATUMSANGABE})"
    rf"|(?:{_DATUMSANGABE})\s*(?:-|–|—|bis)\s*(?:{_DATUMSANGABE}|bis\s+(?:dato|heute)|dato|heute|jetzt|aktuell|laufend|"
    r"today|present|now|$)",
    re.I,
)
# Woran man eine laufende Station erkennt („seit 2019“, „2019 – heute“, „03/2019 –“ ohne Ende)
_LAUFEND = re.compile(r"heute|jetzt|aktuell|laufend|dato|today|present|now|\b(?:seit|since|ab)\s|[-–—]\s*$|\bbis\s*$",
                      re.I)
_FIRMA = re.compile(r"\b(GmbH|AG|SE|KG|KGaA|e\.\s?V\.|Inc\.?|Ltd\.?|GbR|mbH|UG|OHG|Holding|Gruppe|Group|"
                    r"Universität|Hochschule|Klinik|Klinikum|Stadt|Landkreis|Bank)\b")
# „Rossmann, Hannover“: Arbeitgeber mit Ort (nur zur Ausrichtung, nie Beleg fuer eine sichere Station)
_FIRMA_ORT = re.compile(r"^[^,]{2,60},\s*[A-ZÄÖÜ][a-zäöüß]+(?:[ -][A-ZÄÖÜ][a-zäöüß]+)?$")
_KEIN_ORT_ZUSATZ = {"teilzeit", "vollzeit", "minijob", "remote", "homeoffice", "praktikum", "werkstudent",
                    "befristet", "unbefristet", "elternzeit", "nebenjob", "aushilfe"}
# Was in einer eindeutigen Berufsbezeichnung nicht vorkommt (Satzteile, Zahlen, Orte, Aufzaehlungen)
_KEINE_ROLLE = re.compile(r"\b(?:bei|für|fuer|von|mit|im|in|am|an|zur|zum|und|der|die|das|des|neuer|neue|at|for|of)\b"
                          r"|[,;:\d]", re.I)
# Endungen, an denen man eine Berufsbezeichnung erkennt ("als Programmierer" statt "im Bereich ...")
_ROLLE = re.compile(r"(?:er|in|ent|ant|ist|or|eur|mann|frau|leiter|manager|owner|developer|engineer|consultant|"
                    r"analyst|architect|lead|berater|referent|assistent|assistenz|trainee|praktikant|werkstudent)$",
                    re.I)


# Kontaktangaben („Telefon:“, „Familienstand: ledig“) – im Abschnitt Faehigkeiten beginnt damit der persoenliche Teil
_KONTAKT_WORT = (r"(?:tel(?:efon)?|mobil|handy|e-?mail|adresse|anschrift|geburtsdatum|geboren|geburtsort|"
                 r"nationalität|staatsangehörigkeit|familienstand|wohnort|führerschein)\.?")
_KONTAKT_LABEL = re.compile(rf"^{_KONTAKT_WORT}\s*:$", re.I)


def _abschnitte(text: str) -> dict[str, list[str]]:
    """Zeilen je Abschnitt (erkannt an Ueberschriften wie „Fähigkeiten“ oder „Berufserfahrung“)."""
    teile: dict[str, list[str]] = {}
    aktuell = "kopf"
    for zeile in (z.strip() for z in text.splitlines()):
        if not zeile:
            continue
        art = _UEBERSCHRIFT_ZU.get(zeile.lower().rstrip(":").strip())
        if art:
            aktuell = art
            continue
        if aktuell == "faehigkeiten" and zeile.endswith(":"):
            # „Software:“ ist eine Unterueberschrift, keine Faehigkeit; „Telefon:“ beendet den Abschnitt
            if _KONTAKT_LABEL.match(zeile):
                aktuell = "persoenlich"
            continue
        teile.setdefault(aktuell, []).append(zeile)
    return teile


def _ist_datum(text: str) -> bool:
    return bool(_DATUM.fullmatch(text.replace(" ", "")))


def _telefon(text: str) -> str:
    for muster in (_TELEFON_LABEL, _TELEFON_OHNE):
        for m in muster.finditer(text):
            nummer = re.sub(r"\s+", " ", m.group(1)).strip()
            if not _ist_datum(nummer) and not _ZEITRAUM.search(nummer) and len(re.sub(r"\D", "", nummer)) >= 7:
                return nummer
    return ""


def _name(zeilen: list[str], ausser: set[str]) -> tuple[str, str] | None:
    def ok(wort: str) -> bool:
        return bool(_WORT.match(wort)) and wort.lower() not in _UEBERSCHRIFT_WOERTER and wort.casefold() not in ausser

    rand = [*zeilen[:5], *zeilen[-5:]]
    for z in rand:
        teile = z.split()
        if len(teile) == 2 and ok(teile[0]) and ok(teile[1]):
            return teile[0], teile[1]
    for i in range(len(zeilen) - 1):  # z. B. am Seitenende "Mark" / "Beispiel" in zwei Zeilen
        if (i < 5 or i >= len(zeilen) - 6) and ok(zeilen[i]) and ok(zeilen[i + 1]):
            return zeilen[i], zeilen[i + 1]
    return None


def _faehigkeiten(zeilen: list[str]) -> list[str]:
    ergebnis: list[str] = []
    for zeile in zeilen:
        for teil in re.split(r"[,;•·|]", zeile):
            if ":" in teil:  # „Programmiersprachen: Python“ -> „Python“, „Familienstand: ledig“ -> nichts
                label, teil = teil.split(":", 1)
                if re.fullmatch(_KONTAKT_WORT, label.strip(), re.I):
                    continue
            teil = re.sub(r"\s+", " ", teil).strip(" -–:.")
            if "@" in teil or "://" in teil or teil.lower().startswith("www.") or _strasse(teil):
                continue
            if 1 <= len(teil) <= 40 and len(teil.split()) <= 4 and not re.search(r"\d{4}", teil):
                if teil.casefold() not in {e.casefold() for e in ergebnis}:
                    ergebnis.append(teil)
    return ergebnis[:15]


def _ist_firma(text: str) -> bool:
    """Sieht aus wie ein Arbeitgeber (Rechtsform oder „Firma, Stadt“) – nur zur Ausrichtung."""
    if _FIRMA.search(text):
        return True
    if not _FIRMA_ORT.match(text):
        return False
    name, ort = (t.strip() for t in text.rsplit(",", 1))
    return ort.casefold() not in _KEIN_ORT_ZUSATZ and not _sichere_rolle(name)


def _sichere_rolle(text: str) -> bool:
    """Eindeutig eine Berufsbezeichnung: kurz, ohne Satzteile, Zahlen oder Orte, kein Monat, kein Firmenname."""
    worte = text.split()
    return (1 <= len(worte) <= 4 and ist_berufsbezeichnung(text) and not _KEINE_ROLLE.search(text)
            and not _FIRMA.search(text) and not re.fullmatch(_MONAT, worte[-1], re.I)
            and not worte[0].casefold().endswith(("ung", "heit", "keit")))


def _bei(text: str) -> tuple[str, str] | None:
    """„Verkäuferin bei Müller GmbH“ -> („Verkäuferin“, „Müller GmbH“)."""
    m = re.fullmatch(r"(.+?)\s+(?:bei|at)\s+(.+)", text)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def _zuordnen(neben: str, darunter: str) -> tuple[str, str, bool]:
    """(Rolle, Arbeitgeber, sicher) aus der Angabe neben dem Zeitraum und der Zeile darunter.

    Sicher nur mit starken Belegen: eine Seite traegt eine Rechtsform („GmbH“, „AG“ …), die andere ist eindeutig
    eine Berufsbezeichnung. Sonst nur fuer die Anzeige ausgerichtet (unsicher).
    """
    if _FIRMA.search(neben) and _sichere_rolle(darunter):
        return darunter, neben, True
    if _FIRMA.search(darunter) and _sichere_rolle(neben):
        return neben, darunter, True
    if (_ist_firma(darunter) and not _ist_firma(neben)) or (_sichere_rolle(neben) and not _sichere_rolle(darunter)):
        return neben, darunter, False
    return darunter, neben, False


def _station(rolle: str, arbeitgeber: str, sicher: bool, zeitraum: str) -> dict[str, Any]:
    return {"rolle": rolle, "arbeitgeber": arbeitgeber, "zeitraum": zeitraum, "sicher": sicher}


def _stationen(zeilen: list[str]) -> list[dict[str, Any]]:
    """Stationen aus dem Abschnitt Erfahrung: Zeile mit Zeitraum (oft mit Arbeitgeber) und Rolle daneben."""
    stationen: list[dict[str, Any]] = []
    for i, zeile in enumerate(zeilen):
        m = _ZEITRAUM.search(zeile)
        if not m:
            continue
        rest = (zeile[:m.start()] + zeile[m.end():]).strip(" ,|–-·")
        folgende = [z for z in zeilen[i + 1:i + 3] if not _ZEITRAUM.search(z) and len(z.split()) <= 8]
        zeitraum = m.group(0)
        if rest and (paar := _bei(rest)):
            rolle, arbeitgeber = paar
            stationen.append(_station(rolle, arbeitgeber, _sichere_rolle(rolle) and bool(_FIRMA.search(arbeitgeber)),
                                      zeitraum))
        elif rest and folgende:
            stationen.append(_station(*_zuordnen(rest, folgende[0]), zeitraum))
        elif folgende and (paar := _bei(folgende[0])):
            rolle, arbeitgeber = paar
            stationen.append(_station(rolle, arbeitgeber, _sichere_rolle(rolle) and bool(_FIRMA.search(arbeitgeber)),
                                      zeitraum))
        elif len(folgende) > 1 and (_ist_firma(folgende[0]) or _ist_firma(folgende[1])):
            stationen.append(_station(*_zuordnen(folgende[0], folgende[1]), zeitraum))
        elif folgende:
            stationen.append(_station(folgende[0], "", False, zeitraum))
    return sortiert(stationen)[:8]


def _daten(text: str) -> list[tuple[int, int | None]]:
    """(Jahr, Monat oder None) aller Datumsangaben in einem Zeitraum."""
    ergebnis: list[tuple[int, int | None]] = []
    for m in re.finditer(_DATUMSANGABE, text or "", re.I):
        teil = m.group(0)
        if name := re.search(_MONAT, teil, re.I):
            monat: int | None = _MONATE.get(name.group(0).rstrip(".").casefold())
            ergebnis.append((int(re.search(r"\d{4}", teil).group(0)), monat))
        elif zahlen := re.fullmatch(r"(?:(\d{1,2})[./])?(?:(\d{1,2})[./])?(\d{4})", teil):
            monat_text = zahlen.group(2) or zahlen.group(1)
            ergebnis.append((int(zahlen.group(3)), int(monat_text) if monat_text else None))
        elif kurz := re.fullmatch(r"(\d{1,2})[./](\d{2})", teil):
            jahr = int(kurz.group(2))
            jahr += 2000 if jahr <= datetime.now(zeitplan.ZEITZONE).year % 100 + 1 else 1900
            ergebnis.append((jahr, int(kurz.group(1))))
    return ergebnis


def _datum_ende(zeitraum: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """((Jahr, Monat) des Endes, (Jahr, Monat) des Beginns); laufend endet jetzt, unbekannt = (0, 0)."""
    daten = _daten(zeitraum)
    beginn = (daten[0][0], daten[0][1] or 1) if daten else (0, 0)
    ende = (daten[-1][0], daten[-1][1] or 12) if daten else (0, 0)
    if _LAUFEND.search((zeitraum or "").strip()):
        heute = datetime.now(zeitplan.ZEITZONE)
        ende = max(ende if len(daten) > 1 else (0, 0), (heute.year, heute.month))
    return ende, beginn


def _ende_bekannt(stationen: list[dict[str, Any]]) -> bool:
    return all(_datum_ende(s.get("zeitraum", ""))[0] != (0, 0) for s in stationen)


def sortiert(stationen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Neueste zuerst – nach dem Ende (laufend = jetzt), nicht nach der Reihenfolge im Lebenslauf.

    Ist ein Ende unbekannt (fehlt oder unlesbar), bleibt die Reihenfolge, wie sie ist.
    """
    if not _ende_bekannt(stationen):
        return list(stationen)
    return sorted(stationen, key=lambda s: _datum_ende(s.get("zeitraum", "")), reverse=True)


def ist_berufsbezeichnung(rolle: str) -> bool:
    letztes = (rolle.split() or [""])[-1]
    return bool(_ROLLE.search(letztes)) or bool(re.search(r"\b(manager|entwickler|kaufmann|kauffrau)\b", rolle, re.I))


# „Zeitmanagement“ oder „Persönlichkeitsentwicklung“ sind Soft Skills, keine Berufe
_KEIN_BERUF = {"zeit", "selbst", "konflikt", "stress", "team", "change", "termin", "aufgaben", "beschwerde",
               "emotions", "energie", "persönlichkeits", "weiter", "prioritäten", "eigen", "fehler", "ressourcen",
               "beziehungs", "erwartungs", "time", "self", "conflict"}


def _beruf_aus(faehigkeit: str) -> str:
    """„IT-Projektmanagement“ -> „IT-Projektmanager“; Soft Skills und ein bloßes „Management“ nicht.

    Nur ein Wort (mit Bindestrichen): aus „Agiles Projektmanagement“ wird kein „Agiles Projektmanager“.
    """
    if len(faehigkeit.split()) != 1:
        return ""
    for endung, beruf in (("management", "manager"), ("entwicklung", "entwickler")):
        if not faehigkeit.casefold().endswith(endung):
            continue
        praefix = faehigkeit[:-len(endung)]
        kern = re.split(r"[\s-]", praefix.strip(" -"))[-1].casefold()
        if not kern or kern in _KEIN_BERUF:
            return ""
        return praefix + (beruf.capitalize() if faehigkeit[-len(endung)].isupper() else beruf)
    return ""


def _titel_aus(stationen: list[dict[str, Any]], faehigkeiten: list[str]) -> list[str]:
    """Suchbegriffe: neueste Berufsbezeichnung, dann aus Faehigkeiten („…management“ -> „…manager“), dann aeltere.

    Nur aus sicher zugeordneten Stationen – sonst koennte ein Arbeitgeber („Rossmann“) zum Jobtitel werden.
    """
    from .vorschlaege import titel_kern, zu_allgemein

    rollen = [titel_kern(s["rolle"]) for s in sortiert(stationen)
              if s.get("sicher") is True and ist_berufsbezeichnung(s["rolle"])]
    aus_faehigkeiten = [t for t in map(_beruf_aus, faehigkeiten) if t]
    titel: list[str] = []
    for t in [*rollen[:1], *aus_faehigkeiten, *rollen[1:]]:
        if t and not zu_allgemein(t) and t.casefold() not in {x.casefold() for x in titel}:
            titel.append(t)
    return titel[:8]


def _strasse(text: str) -> str:
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if len(zeile) <= 80 and (m := _STRASSE.match(zeile)) and _STRASSEN_ENDE.search(m.group(1)):
            return f"{m.group(1).strip()} {m.group(2).strip()}"
    return ""


def heuristik(text: str) -> Ableitung:
    """Ohne KI: Abschnitte lesen (Über mich, Fähigkeiten, Erfahrung) und Kontaktdaten mit strengen Mustern."""
    teile = _abschnitte(text)
    alle = [z for z in (z.strip() for z in text.splitlines()) if z]
    kontakt: dict[str, str] = {}
    if m := _EMAIL.search(text):
        kontakt["email"] = m.group(0).strip(".").lower()
    if telefon := _telefon(text):
        kontakt["telefon"] = telefon
    for m in _PLZ_ORT.finditer(text):
        if m.group(2).split()[0].lower() not in _KEIN_ORT:
            kontakt["plz"], kontakt["ort"] = m.group(1), m.group(2).strip()
            break
    if strasse := _strasse(text):
        kontakt["strasse"] = strasse
    faehigkeiten = _faehigkeiten(teile.get("faehigkeiten", []))
    stationen = _stationen(teile.get("erfahrung", []))
    # Kein Name aus Faehigkeiten oder Werdegang („Programmierer“ / „Python“)
    belegt = {f.casefold() for f in faehigkeiten}
    belegt |= {w.casefold() for s in stationen for w in (s["rolle"] + " " + s["arbeitgeber"]).split()}
    if name := _name(alle, belegt):
        kontakt["vorname"], kontakt["nachname"] = name
    profil_text = re.sub(r"\s+", " ", " ".join(teile.get("profil", []))).strip()
    if len(profil_text) > 600:
        profil_text = profil_text[:600].rsplit(". ", 1)[0].rstrip(".") + "."
    return Ableitung(
        kurzprofil=profil_text, jobtitel=_titel_aus(stationen, faehigkeiten), ort=kontakt.get("ort", ""),
        faehigkeiten=faehigkeiten, stationen=stationen, kontakt=kontakt, quelle="heuristik",
    )


# --------------------------------------------------------------------------- #
# Mit KI
# --------------------------------------------------------------------------- #

def _liste(werte: Any, grenze: int, *, titel: bool = False) -> list[str]:
    from .vorschlaege import titel_kern

    ergebnis: list[str] = []
    for wert in werte if isinstance(werte, list) else []:
        text = titel_kern(str(wert)) if titel else re.sub(r"\s+", " ", str(wert)).strip(" ,;.-")
        if text and text.casefold() not in {e.casefold() for e in ergebnis}:
            ergebnis.append(text[:80])
    return ergebnis[:grenze]


def _woerter(text: str) -> list[str]:
    # Ohne Zahlen: Zeitraeume zwischen Arbeitgeber und Rolle sollen den Abstand nicht vergroessern
    return [w for w in re.findall(r"\w+", text.casefold()) if not w.isdigit()]


def _vorkommen(wert: str, woerter: list[str]) -> list[int]:
    """Stellen, an denen `wert` Wort fuer Wort (ganze Woerter, in dieser Folge) im Text steht."""
    gesucht = _woerter(wert)
    n = len(gesucht)
    return [i for i in range(len(woerter) - n + 1) if n and woerter[i:i + n] == gesucht]


def _jahre_am_arbeitgeber(arbeitgeber: str, alle: list[str]) -> list[set[str]]:
    """Die Zahlen direkt vor und direkt nach jeder Nennung des Arbeitgebers (dort steht meist sein Zeitraum)."""
    gesucht = re.findall(r"\w+", arbeitgeber.casefold())
    n = len(gesucht)
    laeufe: list[set[str]] = []
    for i in range(len(alle) - n + 1):
        if n and alle[i:i + n] == gesucht:
            laeufe.append(set(takewhile(str.isdigit, alle[i + n:])))
            laeufe.append(set(takewhile(str.isdigit, reversed(alle[:i]))))
    return laeufe


def _ki_stationen_pruefen(stationen: list[dict[str, Any]], text: str) -> None:
    """Setzt `sicher` – nur, was sich im Lebenslauf belegen laesst (die KI darf nichts erfinden oder vermischen):

    - Rolle und Arbeitgeber stehen woertlich da (ganze Woerter, in dieser Folge),
    - die Rolle steht nicht naeher bei einem anderen Arbeitgeber,
    - die Jahre des Zeitraums stehen direkt beim Arbeitgeber (so faellt eine um eine Station verrutschte Rolle auf).
    """
    alle = re.findall(r"\w+", text.casefold())
    woerter = [w for w in alle if not w.isdigit()]
    orte = {s["arbeitgeber"].casefold(): _vorkommen(s["arbeitgeber"], woerter) for s in stationen if s["arbeitgeber"]}
    for s in stationen:
        rolle = _vorkommen(s["rolle"], woerter)
        eigene = orte.get(s["arbeitgeber"].casefold()) or []
        jahre = set(re.findall(r"(?:19|20)\d{2}", s.get("zeitraum", "")))
        if not (rolle and eigene and jahre):
            s["sicher"] = False
            continue

        def abstand(stellen: list[int]) -> int:
            return min(abs(r - a) for r in rolle for a in stellen)

        naechster = abstand(eigene)
        s["sicher"] = all(abstand(stellen) >= naechster for name, stellen in orte.items()
                          if stellen and name != s["arbeitgeber"].casefold()) and any(
            jahre <= lauf for lauf in _jahre_am_arbeitgeber(s["arbeitgeber"], alle))


def _ortsname(wert: Any) -> str:
    return re.sub(r"^\d{5}\s*", "", str(wert or "")).strip()


def per_ki(profil: Profil, text: str) -> Ableitung:
    """Mit KI auslesen; wirft LLMNichtVerfuegbar ohne KI-Zugang oder ohne verwertbare Antwort."""
    antwort = llm_modul.chat(
        profil.bewertung.modell, system=_SYSTEM, nutzer=f"Lebenslauf:\n{text[:12000]}",
        max_tokens=8000, json_schema=_SCHEMA, effort="low",
    )
    daten = llm_modul.json_aus_text(antwort)
    if not isinstance(daten, dict):
        raise llm_modul.LLMNichtVerfuegbar("Die KI hat keine verwertbare Antwort geliefert (kein JSON-Objekt).")
    jobtitel = _liste(daten.get("jobtitel"), 8, titel=True)
    kurzprofil = re.sub(r"\s+", " ", str(daten.get("kurzprofil") or "")).strip()[:600]
    if not (jobtitel or kurzprofil):
        raise llm_modul.LLMNichtVerfuegbar("Die KI hat keine verwertbare Antwort geliefert (leer).")
    roh_kontakt = daten.get("kontakt") if isinstance(daten.get("kontakt"), dict) else {}
    kontakt = {f: str(roh_kontakt.get(f) or "").strip() for f in KONTAKT_FELDER}
    if _ist_datum(kontakt.get("telefon", "")):
        kontakt["telefon"] = ""
    stationen = []
    for s in daten.get("stationen") if isinstance(daten.get("stationen"), list) else []:
        if isinstance(s, dict) and str(s.get("rolle") or "").strip():
            stationen.append({k: str(s.get(k) or "").strip()[:120] for k in ("rolle", "arbeitgeber", "zeitraum")})
    _ki_stationen_pruefen(stationen, text)
    ort = _ortsname(daten.get("ort")) or kontakt.get("ort", "")
    return Ableitung(
        kurzprofil=kurzprofil, jobtitel=jobtitel, ort=ort, suchort=_ortsname(daten.get("suchort")),
        faehigkeiten=_liste(daten.get("faehigkeiten"), 15), themen=_liste(daten.get("themen"), 6),
        stationen=sortiert(stationen)[:8],
        kontakt={k: v for k, v in kontakt.items() if v}, quelle="ki",
    )


def ableiten(profil: Profil, text: str, *, mit_ki: bool = True) -> Ableitung:
    """Mit KI, sonst (oder wenn sie nicht antwortet) per Heuristik; der KI-Grund steht dann in `fehler`."""
    ergebnis: Ableitung | None = None
    fehler = ""
    if mit_ki and llm_modul.client_verfuegbar():
        try:
            ergebnis = per_ki(profil, text)
        except Exception as e:  # KI weg, Guthaben leer, kaputte Antwort: dann ohne KI
            fehler = llm_modul.ki_grund(e)
            log.warning("Lebenslauf mit KI nicht auslesbar (%s): %s", fehler, str(e)[:300])
    if ergebnis is not None:
        ohne = heuristik(text)
        if ohne.kontakt.get("email"):
            ergebnis.kontakt.setdefault("email", ohne.kontakt["email"])  # sonst nichts mischen
    ergebnis = ergebnis or heuristik(text)
    # Der eigene Name ist nie eine Faehigkeit (zweispaltige PDFs: der Name rutscht unter „Fähigkeiten“)
    p = profil.person
    namen = {w.casefold() for teil in (p.vorname, p.nachname, ergebnis.kontakt.get("vorname"),
                                        ergebnis.kontakt.get("nachname")) for w in (teil or "").split()}
    ergebnis.faehigkeiten = [f for f in ergebnis.faehigkeiten
                             if not (f.split() and all(w.casefold() in namen for w in f.split()))]
    ergebnis.fehler = fehler
    ergebnis.pruefsumme = pruefsumme(text)
    ergebnis.zeit = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ergebnis.version = VERSION
    return ergebnis


# --------------------------------------------------------------------------- #
# Uebernehmen
# --------------------------------------------------------------------------- #

def _vorher(alt: Ableitung | None) -> dict[str, Any]:
    """Was zuletzt aus dem Lebenslauf uebernommen wurde (aeltere Dateien: die damaligen Werte)."""
    if alt is None:
        return {}
    if alt.uebernommen:
        return dict(alt.uebernommen)
    return {"kurzprofil": alt.kurzprofil, "was": list(alt.jobtitel), "wo": alt.suchort or alt.ort,
            "skills": list(alt.faehigkeiten)}


def _zusammen(neu: list[str], aktuell: list[str], frueher: list[str], grenze: int) -> list[str]:
    """Neue Werte aus dem Lebenslauf zuerst, eigene Eintraege des Nutzers bleiben dahinter.

    Begrenzt wird nur der Teil aus dem Lebenslauf – eigene Eintraege werden nie abgeschnitten.
    """
    aus_lebenslauf = neu[:grenze]
    schon = {x.casefold() for x in [*frueher, *aus_lebenslauf]}
    ergebnis: list[str] = []
    for x in [*aus_lebenslauf, *(a for a in aktuell if a.casefold() not in schon)]:
        if x.casefold() not in {e.casefold() for e in ergebnis}:
            ergebnis.append(x)
    return ergebnis


def uebernehmen(profil: Profil, a: Ableitung, alt: Ableitung | None = None, *, erzwingen: bool = False) -> list[str]:
    """Ins Profil uebernehmen (ohne zu speichern); Rueckgabe: was sich geaendert hat.

    Ersetzt wird nur, was leer ist oder noch dem zuletzt Uebernommenen entspricht (oder mit `erzwingen`
    beim Knopf „Neu auslesen“) – so ueberschreibt ein erneutes Auslesen keine Aenderungen des Nutzers.
    """
    vorher = _vorher(alt)
    uebernommen = dict(vorher)
    aenderungen: list[str] = []

    def frei(schluessel: str, aktuell: Any) -> bool:
        # Beim ersten Auslesen ist der Lebenslauf die Quelle; danach nur, was der Nutzer seitdem nicht geaendert
        # hat – auch ein leerer Ort (ganz Deutschland) oder der eigene Wohnort kann Absicht sein
        return erzwingen or alt is None or (schluessel in vorher and aktuell == vorher[schluessel])

    # Das Kurzprofil gehoert dem Lebenslauf (es ist in den Einstellungen nicht mehr einzeln bearbeitbar)
    if a.kurzprofil:
        uebernommen["kurzprofil"] = a.kurzprofil
        if a.kurzprofil != profil.kurzprofil:
            profil.kurzprofil = a.kurzprofil
            aenderungen.append("Kurzprofil")
    if a.jobtitel:
        was = _zusammen(a.jobtitel, profil.suche.was, vorher.get("was") or [], MAX_JOBTITEL)
        uebernommen["was"] = list(a.jobtitel)
        if was != profil.suche.was:
            profil.suche.was = was
            aenderungen.append("Jobtitel")
    if a.faehigkeiten:
        skills = _zusammen(a.faehigkeiten, profil.bewertung.skills, vorher.get("skills") or [], MAX_FAEHIGKEITEN)
        uebernommen["skills"] = list(a.faehigkeiten)
        if skills != profil.bewertung.skills:
            profil.bewertung.skills = skills
            aenderungen.append("Fähigkeiten")
    if a.themen:
        wunsch = _zusammen(a.themen, profil.bewertung.wunsch, vorher.get("wunsch") or [], MAX_FAEHIGKEITEN)
        uebernommen["wunsch"] = list(a.themen)
        if wunsch != profil.bewertung.wunsch:
            profil.bewertung.wunsch = wunsch
            aenderungen.append("Themen")
    suchort = a.suchort or a.ort  # nie person.ort als Ersatz: nur, was im Lebenslauf steht
    if suchort and frei("wo", profil.suche.wo):
        umkreis = int(profil.suche.umkreis)
        # Der Umkreis gehoert dem Nutzer, sobald er ihn geaendert hat. Aeltere Auswertungen merkten ihn nicht:
        # dann nur ersetzen, was das Auslesen selbst setzt (30 bzw. 50 km)
        if (frei("umkreis", umkreis) if "umkreis" in vorher
                else erzwingen or alt is None or umkreis in (UMKREIS_SUCHORT, UMKREIS_WOHNORT)):
            umkreis = UMKREIS_SUCHORT if a.suchort and a.quelle == "ki" else max(umkreis, UMKREIS_WOHNORT)
            uebernommen["umkreis"] = umkreis
        uebernommen["wo"] = suchort
        if (suchort, umkreis) != (profil.suche.wo, int(profil.suche.umkreis)):
            profil.suche.wo, profil.suche.umkreis = suchort, umkreis
            aenderungen.append("Ort")
    p = profil.person
    if _ist_datum(p.telefon or ""):
        p.telefon = ""  # frueher irrtuemlich als Telefonnummer gelesenes Datum (z. B. Geburtsdatum)
        aenderungen.append("Telefon korrigiert")
    ergaenzt = False
    for feld in KONTAKT_FELDER:
        wert = (a.kontakt.get(feld) or "").strip()
        if wert and not getattr(p, feld):
            setattr(p, feld, wert)
            ergaenzt = True
    if ergaenzt:
        aenderungen.append("Kontaktdaten")
    a.uebernommen = uebernommen
    return aenderungen


def _kuerzlich_versucht(alt: Ableitung) -> bool:
    zeit = zeitplan.als_datetime(alt.zeit)
    return bool(zeit) and datetime.now(timezone.utc) - zeit < timedelta(hours=NEU_VERSUCHEN_STUNDEN)


def _ki_bereit() -> bool:
    from .ablauf import ki_pausiert

    return llm_modul.client_verfuegbar() and not ki_pausiert()


def aktualisieren(
    profil: Profil, pfad: str | Path | None = None, *, erzwingen: bool = False, mit_ki: bool = True,
) -> tuple[Ableitung | None, list[str]]:
    """Bei neuem Lebenslauf-Inhalt auslesen und uebernehmen (speichert das Profil).

    (Ableitung oder None ohne lesbaren Text, Liste der uebernommenen Bereiche). Derselbe Inhalt wird nicht
    erneut ausgelesen – ausser mit `erzwingen`, nach einer Verbesserung des Auslesens (`VERSION`) oder wenn er
    nur ohne KI gelesen wurde und die KI wieder bereit ist (hoechstens alle 6 Stunden).
    """
    text = dokumente.lebenslauf_text(profil)
    if not text.strip():
        return None, []
    alt = gespeichert()
    if alt and alt.pruefsumme == pruefsumme(text) and not erzwingen:
        veraltet = alt.version < VERSION  # das Auslesen wurde verbessert: einmal sofort neu
        nochmal = veraltet or (mit_ki and alt.quelle != "ki" and _ki_bereit())
        # Nach einem gescheiterten Versuch (auch beim Neu-Lesen nach einer Verbesserung) erst nach 6 Stunden wieder
        if not nochmal or ((not veraltet or alt.fehler) and _kuerzlich_versucht(alt)):
            return alt, []
    neu = ableiten(profil, text, mit_ki=mit_ki)
    if alt and alt.pruefsumme == neu.pruefsumme and alt.quelle == "ki" and neu.quelle != "ki":
        # KI gerade weg: die fruehere, bessere Auswertung behalten (und nicht bei jedem Aufruf neu versuchen).
        # Die Version bleibt: nach einer Verbesserung des Auslesens kommt der neue Versuch spaeter (6 Stunden)
        alt.zeit, alt.fehler = neu.zeit, neu.fehler or "es ist kein KI-Zugang eingerichtet"
        _speichern(alt)
        return alt, []
    aenderungen = uebernehmen(profil, neu, alt, erzwingen=erzwingen)
    if aenderungen:
        speichere_profil(profil, pfad or ermittle_profil_pfad())
    _speichern(neu)
    return neu, aenderungen


# --------------------------------------------------------------------------- #
# Fuer das Anschreiben
# --------------------------------------------------------------------------- #

def _laeuft_noch(zeitraum: str) -> bool:
    if _LAUFEND.search(zeitraum.strip()):
        return True
    ende = re.findall(r"(?:(\d{1,2})[./])?(\d{4})", zeitraum)
    if not ende:
        return False
    monat, jahr = ende[-1]
    heute = datetime.now(zeitplan.ZEITZONE)
    return (int(jahr), int(monat or 12)) >= (heute.year, heute.month)


def _als(station: dict[str, Any]) -> str:
    rolle, arbeitgeber = station.get("rolle", ""), station.get("arbeitgeber", "")
    if _FIRMA_ORT.match(arbeitgeber) and arbeitgeber.rsplit(",", 1)[1].strip().casefold() not in _KEIN_ORT_ZUSATZ:
        arbeitgeber = " in ".join(t.strip() for t in arbeitgeber.rsplit(",", 1))  # „Rossmann GmbH in Hannover“
    bei = f" bei {arbeitgeber}" if arbeitgeber else ""
    return f"als {rolle}{bei}" if ist_berufsbezeichnung(rolle) else f"{bei.strip()} im Bereich {rolle}".strip()


def stationssatz(ableitung: Ableitung | None = None, *, profil: Profil | None = None) -> str:
    """Ein Satz zum Werdegang, nur aus dem Lebenslauf (leer, wenn nichts Belastbares da ist).

    Nur sicher zugeordnete Stationen. „Derzeit“/„Zuletzt“ nur, wenn es wirklich die neueste Station ist – sonst
    neutral „Unter anderem …“. Mit `profil` nur, wenn die Auswertung zum aktuellen Lebenslauf gehoert (ein neuer,
    unlesbarer Lebenslauf liefert keine Fakten aus dem alten).
    """
    a = ableitung if ableitung is not None else gespeichert()
    if a is None:
        return ""
    if profil is not None and a.pruefsumme != pruefsumme(dokumente.lebenslauf_text(profil)):
        return ""
    alle = [s for s in a.stationen if s.get("rolle")]
    bekannt = _ende_bekannt(alle)  # sonst laesst sich nicht sagen, welche Station die neueste ist
    alle = sortiert(alle)
    sicher = [s for s in alle if s.get("sicher") is True and s.get("arbeitgeber")]
    if not sicher:
        return ""
    erste = sicher[0]
    if not bekannt or erste is not alle[0]:
        return f"Unter anderem war ich {_als(erste)} tätig."
    aktuell = _laeuft_noch(erste.get("zeitraum", ""))
    satz = ("Derzeit bin ich " if aktuell else "Zuletzt war ich ") + _als(erste) + " tätig"
    if len(alle) > 1 and len(sicher) > 1 and alle[1] is sicher[1]:
        satz += (", davor war ich " if aktuell else ", davor ") + _als(sicher[1])
    return satz + "."
