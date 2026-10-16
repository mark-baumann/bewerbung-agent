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
from pathlib import Path
from typing import Any

from . import dokumente, zeitplan
from .config import Profil, daten_dir, ermittle_profil_pfad, speichere_profil
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

# Hoeher setzen, wenn sich das Auslesen verbessert: aeltere Auswertungen werden dann einmal neu gelesen
VERSION = 2
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
    stationen: list[dict[str, str]] = field(default_factory=list)  # rolle, arbeitgeber, zeitraum (neueste zuerst)
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
_STRASSE = re.compile(
    r"^([A-ZÄÖÜ][\wäöüß.\-]*(?:[ -][\wäöüß.\-]+)*?[ -]?(?i:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer|"
    r"steig|pfad|chaussee))\s+(\d{1,3}\s?[a-zA-Z]?)\s*(?:,|$)",
    re.M,
)
_WORT = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?$")
_ZEITRAUM = re.compile(
    r"(?:\d{1,2}[./])?\d{4}\s*(?:-|–|—|bis)\s*(?:(?:\d{1,2}[./])?\d{4}|heute|jetzt|aktuell|laufend|today|present|now)",
    re.I,
)
_FIRMA = re.compile(r"\b(GmbH|AG|SE|KG|KGaA|e\.\s?V\.|Inc\.?|Ltd\.?|GbR|mbH|UG|OHG|Holding|Gruppe|Group|"
                    r"Universität|Hochschule|Klinik|Klinikum|Stadt|Landkreis|Bank)\b")
# Endungen, an denen man eine Berufsbezeichnung erkennt ("als Programmierer" statt "im Bereich ...")
_ROLLE = re.compile(r"(?:er|in|ent|ant|ist|or|eur|mann|frau|leiter|manager|owner|developer|engineer|consultant|"
                    r"analyst|architect|lead|berater|referent|assistent|assistenz|trainee|praktikant|werkstudent)$",
                    re.I)


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
            teil = re.sub(r"\s+", " ", teil).strip(" -–:.")
            if 1 <= len(teil) <= 40 and len(teil.split()) <= 4 and not re.search(r"\d{4}", teil):
                if teil.casefold() not in {e.casefold() for e in ergebnis}:
                    ergebnis.append(teil)
    return ergebnis[:15]


def _stationen(zeilen: list[str]) -> list[dict[str, str]]:
    """Stationen aus dem Abschnitt Erfahrung: Zeile mit Zeitraum (oft mit Arbeitgeber) und Rolle daneben."""
    stationen: list[dict[str, str]] = []
    for i, zeile in enumerate(zeilen):
        m = _ZEITRAUM.search(zeile)
        if not m:
            continue
        rest = (zeile[:m.start()] + zeile[m.end():]).strip(" ,|–-·")
        folgende = [z for z in zeilen[i + 1:i + 3] if not _ZEITRAUM.search(z) and len(z.split()) <= 8]
        rolle, arbeitgeber = "", ""
        if rest and folgende:
            arbeitgeber, rolle = rest, folgende[0]
            if _FIRMA.search(rolle) and not _FIRMA.search(arbeitgeber):
                arbeitgeber, rolle = rolle, arbeitgeber
        elif folgende:
            rolle = folgende[0]
            if len(folgende) > 1 and _FIRMA.search(folgende[1]):
                arbeitgeber = folgende[1]
        if rolle:
            stationen.append({"rolle": rolle, "arbeitgeber": arbeitgeber, "zeitraum": m.group(0)})
    return stationen[:8]


def ist_berufsbezeichnung(rolle: str) -> bool:
    letztes = (rolle.split() or [""])[-1]
    return bool(_ROLLE.search(letztes)) or bool(re.search(r"\b(manager|entwickler|kaufmann|kauffrau)\b", rolle, re.I))


def _titel_aus(stationen: list[dict[str, str]], faehigkeiten: list[str]) -> list[str]:
    """Suchbegriffe: neueste Berufsbezeichnung, dann aus Faehigkeiten („…management“ -> „…manager“), dann aeltere."""
    from .vorschlaege import titel_kern

    rollen = [titel_kern(s["rolle"]) for s in stationen if ist_berufsbezeichnung(s["rolle"])]
    aus_faehigkeiten = []
    for f in faehigkeiten:
        if re.search(r"management$", f, re.I):
            aus_faehigkeiten.append(re.sub(r"management$", "manager", f, flags=re.I))
        elif re.search(r"entwicklung$", f, re.I) and len(f.split()) <= 2:
            aus_faehigkeiten.append(re.sub(r"entwicklung$", "entwickler", f, flags=re.I))
    titel: list[str] = []
    for t in [*rollen[:1], *aus_faehigkeiten, *rollen[1:]]:
        if t and t.casefold() not in {x.casefold() for x in titel}:
            titel.append(t)
    return titel[:8]


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
    if m := _STRASSE.search(text):
        kontakt["strasse"] = f"{m.group(1).strip()} {m.group(2).strip()}"
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
    ort = _ortsname(daten.get("ort")) or kontakt.get("ort", "")
    return Ableitung(
        kurzprofil=kurzprofil, jobtitel=jobtitel, ort=ort, suchort=_ortsname(daten.get("suchort")),
        faehigkeiten=_liste(daten.get("faehigkeiten"), 15), themen=_liste(daten.get("themen"), 6),
        stationen=stationen[:8],
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
        ergebnis.stationen = ergebnis.stationen or ohne.stationen
    ergebnis = ergebnis or heuristik(text)
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
    """Neue Werte aus dem Lebenslauf zuerst, eigene Eintraege des Nutzers bleiben dahinter."""
    weg = {x.casefold() for x in frueher} - {x.casefold() for x in neu}
    ergebnis: list[str] = []
    for x in [*neu, *(a for a in aktuell if a.casefold() not in weg)]:
        if x.casefold() not in {e.casefold() for e in ergebnis}:
            ergebnis.append(x)
    return ergebnis[:grenze]


def uebernehmen(profil: Profil, a: Ableitung, alt: Ableitung | None = None, *, erzwingen: bool = False) -> list[str]:
    """Ins Profil uebernehmen (ohne zu speichern); Rueckgabe: was sich geaendert hat.

    Ersetzt wird nur, was leer ist oder noch dem zuletzt Uebernommenen entspricht (oder mit `erzwingen`
    beim Knopf „Neu auslesen“) – so ueberschreibt ein erneutes Auslesen keine Aenderungen des Nutzers.
    """
    vorher = _vorher(alt)
    uebernommen = dict(vorher)
    aenderungen: list[str] = []

    def frei(schluessel: str, aktuell: Any) -> bool:
        # Beim ersten Auslesen ist der Lebenslauf die Quelle; danach nur, was der Nutzer nicht geaendert hat
        return erzwingen or alt is None or not aktuell or (schluessel in vorher and aktuell == vorher[schluessel])

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
    # Ein automatisch aus den Kontaktdaten gesetzter Ort (Assistent) darf dem Suchort weichen
    if suchort and (frei("wo", profil.suche.wo) or profil.suche.wo == profil.person.ort):
        umkreis = UMKREIS_SUCHORT if a.suchort and a.quelle == "ki" else max(int(profil.suche.umkreis), UMKREIS_WOHNORT)
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
        if not nochmal or (not veraltet and _kuerzlich_versucht(alt)):
            return alt, []
    neu = ableiten(profil, text, mit_ki=mit_ki)
    if alt and alt.pruefsumme == neu.pruefsumme and alt.quelle == "ki" and neu.quelle != "ki":
        # KI gerade weg: die fruehere, bessere Auswertung behalten (und nicht bei jedem Aufruf neu versuchen)
        alt.zeit, alt.fehler, alt.version = neu.zeit, neu.fehler, VERSION
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
    if re.search(r"heute|jetzt|aktuell|laufend|today|present|now", zeitraum, re.I):
        return True
    ende = re.findall(r"(?:(\d{1,2})[./])?(\d{4})", zeitraum)
    if not ende:
        return False
    monat, jahr = ende[-1]
    heute = datetime.now(zeitplan.ZEITZONE)
    return (int(jahr), int(monat or 12)) >= (heute.year, heute.month)


def _als(station: dict[str, str]) -> str:
    rolle, arbeitgeber = station.get("rolle", ""), station.get("arbeitgeber", "")
    bei = f" bei {arbeitgeber}" if arbeitgeber else ""
    return f"als {rolle}{bei}" if ist_berufsbezeichnung(rolle) else f"{bei.strip()} im Bereich {rolle}".strip()


def stationssatz(ableitung: Ableitung | None = None) -> str:
    """Ein Satz zum Werdegang, nur aus dem Lebenslauf (leer, wenn nichts Belastbares da ist)."""
    a = ableitung if ableitung is not None else gespeichert()
    stationen = [s for s in (a.stationen if a else []) if s.get("rolle") and s.get("arbeitgeber")]
    if not stationen:
        return ""
    erste = stationen[0]
    aktuell = _laeuft_noch(erste.get("zeitraum", ""))
    satz = ("Derzeit bin ich " if aktuell else "Zuletzt war ich ") + _als(erste) + " tätig"
    if len(stationen) > 1:
        satz += (", davor war ich " if aktuell else ", davor ") + _als(stationen[1])
    return satz + "."
