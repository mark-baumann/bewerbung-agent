"""Selbstpruefung der Anschreiben: ehrlich, vollstaendig, ohne Floskeln.

Zwei Stufen:
1. Regeln (immer): Platzhalter, Laenge, Anrede, Grussformel, Floskeln und eine
   Belegpruefung - jede Zahl und jede Fachkompetenz aus der Anzeige, die das
   Anschreiben behauptet, muss in Lebenslauf, Kurzprofil oder Skills vorkommen.
2. KI-Gutachter (falls verfuegbar): prueft Aussage fuer Aussage gegen den
   Lebenslauf, ob die Muss-Anforderungen angesprochen werden, und vergibt eine
   Qualitaet von 0-100.

Ergebnis: Qualitaet + Bericht. Der Autopilot sendet nur bei hoher Qualitaet
und ohne unbelegte Aussagen; die Karte zeigt den Bericht.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Profil
from .dokumente import lebenslauf_text
from .dossier import Dossier
from .models import Job
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

FLOSKELN = [
    "hiermit bewerbe ich mich", "mit großem interesse", "mit grossem interesse", "hochmotiviert",
    "teamfähig", "belastbar", "flexibel und", "ihre stellenanzeige hat mich sofort",
    "auf ihre anzeige aufmerksam geworden", "ich bin der richtige", "perfekte kandidat",
    "i am writing to apply", "i am excited to apply",
]
_PLATZHALTER = re.compile(r"\[[^\]]{2,40}\]|\{[^}]{2,40}\}|\bXXX\b|\bTODO\b|<[A-ZÄÖÜ][^>]{2,30}>|\.\.\.\]")
_ZAHL = re.compile(r"\b(\d{4}|\d+(?:[.,]\d+)?\s*(?:Jahre?n?|years?|%|Prozent|Mitarbeiter|Projekte?n?|"
                   r"Kunden|Monate?n?))\b", re.IGNORECASE)
# Fachbegriffe: Woerter mit Grossbuchstaben/Ziffern/Sonderzeichen im Wort (SAP, SQL, C++, Node.js, S/4HANA)
_FACHWORT = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9]*(?:[A-Z0-9+#/.][A-Za-z0-9+#]*)+)(?![\w-])")
_KEIN_FACHWORT = {"IT", "AG", "GmbH", "KG", "SE", "UG", "EU", "CV", "PDF", "HR", "Dr", "Prof", "Co", "eV", "z.B",
                  "u.a", "d.h", "ca", "bzw", "inkl", "ggf", "usw", "etc", "m/w/d", "w/m/d", "B.Sc", "M.Sc"}

# Allgemeine Woerter aus Anforderungen, die keine pruefbare Fachkompetenz sind
GENERISCH = set("""
erfahrung erfahrungen kenntnisse kenntnis studium ausbildung abschluss abgeschlossenes abgeschlossene
vergleichbare vergleichbar qualifikation sehr gute guten gutes mehrjährige mehrjährig erste ersten
idealerweise wünschenswert vorteil bereich bereichen umgang sicherer sicher arbeitsweise selbstständige
selbstständig strukturierte strukturiert teamfähigkeit kommunikationsfähigkeit kommunikationsstärke
zuverlässigkeit engagement freude interesse bereitschaft motivation deutsch deutschkenntnisse englisch
englischkenntnisse wort schrift sowie oder und mit von der die das eine einen einer ihre ihr wir sie
team teams aufgaben kunden projekte projekten entwicklung mindestens jahre jahren berufserfahrung
experience knowledge skills strong good years degree team with and the for
""".split())

SCHEMA = {
    "type": "object",
    "properties": {
        "qualitaet": {"type": "integer", "description": "0-100: Gesamtqualitaet als Bewerbung fuer genau diese Stelle"},
        "unbelegt": {"type": "array", "items": {"type": "string"},
                     "description": "Aussagen ueber den Bewerber, die NICHT durch Lebenslauf/Profil belegt sind (woertlich zitiert)"},
        "fehlende_muss": {"type": "array", "items": {"type": "string"},
                          "description": "Muss-Anforderungen, die das Profil erfuellt, die das Anschreiben aber nicht anspricht"},
        "maengel": {"type": "array", "items": {"type": "string"},
                    "description": "Konkrete Schwaechen (Anrede, Floskeln, Laenge, Bezug zur Stelle, Sprache)"},
    },
    "required": ["qualitaet", "unbelegt", "fehlende_muss", "maengel"],
    "additionalProperties": False,
}

SYSTEM = """Du bist ein strenger Gutachter fuer Bewerbungsanschreiben. Pruefe das
Anschreiben gegen die Fakten des Bewerbers. Jede Aussage ueber Erfahrung,
Kenntnisse, Zahlen, Arbeitgeber, Abschluesse oder Erfolge muss sich aus
Lebenslauf, Kurzprofil oder Skills belegen lassen - sonst gehoert sie in
"unbelegt". Pruefe ausserdem, ob die Muss-Anforderungen, die der Bewerber
erfuellt, angesprochen werden, und ob Anrede, Ton und Laenge passen.

FAKTEN DES BEWERBERS
Kurzprofil: {kurzprofil}
Skills: {skills}
{lebenslauf}
Antworte ausschliesslich im vorgegebenen JSON-Schema."""

UEBERARBEITEN = """Ueberarbeite das Anschreiben anhand des Pruefberichts. Entferne oder
korrigiere jede unbelegte Aussage (nichts Neues erfinden!), sprich die genannten
Muss-Anforderungen an, sofern der Bewerber sie laut Fakten erfuellt, und behebe
die Maengel. Behalte Anrede, Sprache und Grussformel bei. Gib nur den neuen
Brieftext zurueck (ab Anrede bis Grussformel mit Name)."""


@dataclass
class Bericht:
    qualitaet: float
    unbelegt: list[str] = field(default_factory=list)
    fehlende_muss: list[str] = field(default_factory=list)
    maengel: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)   # leichte Punkte (Floskeln)
    quelle: str = "regeln"                              # regeln | llm
    ueberarbeitet: bool = False

    @property
    def sauber(self) -> bool:
        return not self.unbelegt and not self.maengel

    def als_dict(self) -> dict[str, Any]:
        return asdict(self)


def fakten(profil: Profil) -> str:
    teile = [profil.kurzprofil, ", ".join(profil.bewertung.skills), ", ".join(profil.bewertung.wunsch),
             lebenslauf_text(profil)]
    a = profil.angaben
    teile += [a.abschluss, a.sprachen, a.berufserfahrung_jahre, a.titel, a.fuehrerschein,
              profil.person.ort, profil.person.name]
    return "\n".join(t for t in teile if t)


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def regelpruefung(text: str, job: Job, profil: Profil, dossier: Dossier | None) -> Bericht:
    b = Bericht(qualitaet=100.0)
    inhalt = (text or "").strip()
    klein = _normal(inhalt)
    belege = _normal(fakten(profil))
    anzeige = _normal(f"{job.titel} {job.arbeitgeber} {job.beschreibung or ''}")
    if not inhalt:
        return Bericht(qualitaet=0, maengel=["Kein Anschreiben vorhanden."])
    if _PLATZHALTER.search(inhalt):
        b.maengel.append(f"Platzhalter im Text: {_PLATZHALTER.search(inhalt).group(0)}")
    woerter = len(re.findall(r"\w+", inhalt))
    if woerter < 120:
        b.maengel.append(f"Sehr kurz ({woerter} Wörter).")
    elif woerter > 480:
        b.maengel.append(f"Zu lang ({woerter} Wörter, besser höchstens eine Seite).")
    erwartet = dossier.anrede if dossier else "Sehr geehrte Damen und Herren,"
    erste = inhalt.splitlines()[0].strip() if inhalt.splitlines() else ""
    if dossier and dossier.ansprechpartner and erwartet.rstrip(",") not in inhalt:
        b.maengel.append(f"Anrede sollte „{erwartet}“ lauten (Ansprechpartner: {dossier.ansprechpartner}).")
    elif not re.match(r"(Sehr geehrte|Liebe|Hallo|Guten Tag|Dear|Hello)", erste):
        b.maengel.append("Keine übliche Anrede am Anfang.")
    nachname = (profil.person.nachname or profil.person.name or "").split()[-1:] or [""]
    if nachname[0] and nachname[0].lower() not in klein[-200:]:
        b.maengel.append("Grußformel mit Namen fehlt am Ende.")
    for f in FLOSKELN:
        if f in klein:
            b.hinweise.append(f"Floskel: „{f}“")

    # Belegpruefung: Zahlen ...
    for m in _ZAHL.finditer(inhalt):
        wert = m.group(1)
        kern = re.match(r"\d+(?:[.,]\d+)?", wert).group(0)
        if kern in anzeige and kern not in belege:
            continue  # z. B. Jahreszahl/Stundenzahl aus der Anzeige zitiert
        if kern not in belege:
            b.unbelegt.append(f"„{wert}“ steht nicht im Lebenslauf/Profil")
    # ... und Fachbegriffe aus der Anzeige, die das Anschreiben nennt, der Lebenslauf aber nicht
    for wort in sorted(_fachbegriffe(job, dossier)):
        if re.search(rf"(?<![\w-]){re.escape(wort)}(?![\w-])", klein) and wort not in belege \
                and wort not in _normal(job.arbeitgeber or ""):
            b.unbelegt.append(f"„{wort}“ wird beansprucht, steht aber nicht im Lebenslauf/Profil")
    if dossier:
        for anforderung in dossier.muss[:8]:
            schluessel = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9+#.]{4,}", anforderung)
                          if w.lower() in belege and w.lower() not in ("erfahrung", "kenntnisse", "sehr", "gute")]
            if schluessel and not any(w.lower() in klein for w in schluessel):
                b.fehlende_muss.append(anforderung)
    b.qualitaet = _punkte(b)
    return b


def _fachbegriffe(job: Job, dossier: Dossier | None) -> set[str]:
    """Pruefbare Fachbegriffe der Stelle (klein geschrieben)."""
    begriffe: set[str] = set()
    for m in _FACHWORT.finditer(job.beschreibung or ""):
        wort = m.group(1).rstrip(".")
        if wort not in _KEIN_FACHWORT and len(wort) >= 2:
            begriffe.add(wort.lower())
    for anforderung in ([*dossier.muss, *dossier.kann] if dossier else []):
        for wort in re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9+#.\-]{3,}", anforderung):
            wort = wort.lower().rstrip(".")
            if wort not in GENERISCH:
                begriffe.add(wort)
    return begriffe


def _punkte(b: Bericht) -> float:
    wert = 100 - 25 * len(b.unbelegt) - 12 * len(b.maengel) - 4 * len(b.fehlende_muss) - 3 * len(b.hinweise)
    return float(max(0, min(100, wert)))


def ki_pruefung(text: str, job: Job, profil: Profil, dossier: Dossier | None) -> Bericht:
    system = SYSTEM.format(
        kurzprofil=profil.kurzprofil or "-", skills=", ".join(profil.bewertung.skills) or "-",
        lebenslauf=llm_modul.lebenslauf_block(profil),
    )
    muss = "\n".join(f"- {m}" for m in (dossier.muss if dossier else [])) or "(nicht extrahiert)"
    nutzer = (f"STELLE: {job.titel} bei {job.arbeitgeber}\nMUSS-ANFORDERUNGEN:\n{muss}\n\n"
              f"ANSCHREIBEN:\n{text}")
    antwort = llm_modul.chat(profil.bewertung.modell, system=system, nutzer=nutzer, max_tokens=6000,
                             json_schema=SCHEMA, effort="medium")
    daten = llm_modul.json_aus_text(antwort)
    return Bericht(
        qualitaet=float(max(0, min(100, int(daten.get("qualitaet") or 0)))),
        unbelegt=[str(x) for x in daten.get("unbelegt") or []],
        fehlende_muss=[str(x) for x in daten.get("fehlende_muss") or []],
        maengel=[str(x) for x in daten.get("maengel") or []],
        quelle="llm",
    )


def pruefe(text: str, job: Job, profil: Profil, dossier: Dossier | None, *, mit_llm: bool = True) -> Bericht:
    """Regeln + (falls moeglich) KI-Gutachten; die strengere Sicht gewinnt."""
    regeln = regelpruefung(text, job, profil, dossier)
    if not (mit_llm and llm_modul.client_verfuegbar()):
        return regeln
    try:
        ki = ki_pruefung(text, job, profil, dossier)
    except Exception as e:
        log.info("KI-Prüfung fehlgeschlagen (%s) – nur Regeln.", e)
        regeln.hinweise.append(f"KI-Prüfung nicht möglich: {str(e)[:120]}")
        return regeln
    zusammen = Bericht(
        qualitaet=min(ki.qualitaet, regeln.qualitaet + 10),
        unbelegt=list(dict.fromkeys([*ki.unbelegt, *regeln.unbelegt])),
        fehlende_muss=list(dict.fromkeys([*ki.fehlende_muss, *regeln.fehlende_muss])),
        maengel=list(dict.fromkeys([*ki.maengel, *regeln.maengel])),
        hinweise=regeln.hinweise,
        quelle="llm",
    )
    if zusammen.unbelegt:
        zusammen.qualitaet = min(zusammen.qualitaet, 60.0)
    return zusammen


def ueberarbeite(text: str, bericht: Bericht, job: Job, profil: Profil) -> str:
    system = SYSTEM.format(
        kurzprofil=profil.kurzprofil or "-", skills=", ".join(profil.bewertung.skills) or "-",
        lebenslauf=llm_modul.lebenslauf_block(profil),
    ).replace("Antworte ausschliesslich im vorgegebenen JSON-Schema.", UEBERARBEITEN)
    nutzer = (f"STELLE: {job.titel} bei {job.arbeitgeber}\n\nPRUEFBERICHT:\n"
              + json.dumps({k: v for k, v in bericht.als_dict().items()
                            if k in ("unbelegt", "fehlende_muss", "maengel", "hinweise")}, ensure_ascii=False)
              + f"\n\nANSCHREIBEN:\n{text}")
    return llm_modul.chat(profil.bewertung.modell, system=system, nutzer=nutzer, max_tokens=12000,
                          effort="medium").strip()
