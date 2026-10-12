"""Suchvorschlaege: aehnliche Suchbegriffe finden und erkennen, was nichts bringt.

Drei Quellen:
- `aus_treffern`: Berufsbezeichnungen und Titel gut passender Stellen, nach
  denen noch nicht gesucht wird (kostenlos, ohne LLM).
- `per_llm`: Synonyme, Nachbarrollen, englische Titel und Quereinstiege aus
  Profil und Lebenslauf (nur auf Knopfdruck, kostet einen LLM-Aufruf).
- `such_tipps`: Hinweise aus der Suchstatistik, z. B. Begriffe ohne Treffer
  oder zu wenige passende Stellen -> Umkreis/Zeitraum erweitern.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .config import Profil
from .db import Speicher
from .scoring import llm as llm_modul
from .sources import QUELLEN


@dataclass
class Vorschlag:
    begriff: str
    grund: str
    quelle: str  # treffer | ki


@dataclass
class Tipp:
    text: str
    # Vorschlag fuer das Suchformular, z. B. {"umkreis": 50}; leer = nur Hinweis
    aenderung: dict[str, Any] = field(default_factory=dict)


_GENDER = re.compile(
    r"\((?:[mwdfxi]\s*/\s*)+[mwdfxi]\)|\(all genders\)|\(gn\)|\*in\b|:in\b|/-?in\b|/-?frau\b|\bm/w/d\b",
    re.IGNORECASE,
)
_TRENNER = re.compile(r"\s+[-–|•]\s+|[,;|(]")


def titel_kern(titel: str) -> str:
    """'Senior Python Entwickler (m/w/d) – Remote' -> 'Senior Python Entwickler'."""
    text = _GENDER.sub(" ", titel or "")
    text = _TRENNER.split(text)[0]
    text = re.sub(r"\s+", " ", text).strip(" -–/")
    worte = text.split()
    return " ".join(worte[:4])


def _schon_gesucht(begriff: str, vorhandene: list[str]) -> bool:
    b = begriff.casefold()
    return any(b in v.casefold() or v.casefold() in b for v in vorhandene if v.strip())


def aus_treffern(db: Speicher, profil: Profil, limit: int = 8) -> list[Vorschlag]:
    """Haeufige Berufe/Titel passender Stellen, nach denen noch nicht gesucht wird."""
    schwelle = profil.bewertung.min_score
    gewicht: Counter[str] = Counter()
    stellen: dict[str, set[str]] = {}
    anzeige: dict[str, str] = {}

    def zaehle(ref: str, text: str | None, faktor: int, max_worte: int) -> None:
        kern = titel_kern(text or "")
        if len(kern) < 4 or len(kern.split()) > max_worte:
            return
        schluessel = kern.casefold()
        gewicht[schluessel] += faktor
        stellen.setdefault(schluessel, set()).add(ref)
        anzeige.setdefault(schluessel, kern)

    gute = {j.ref: j for j, _ in db.bestenliste(min_score=schwelle, limit=300)}
    beworben = {v.ref for v in db.vorgaenge(
        ("wartet", "freigegeben", "wird_gesendet", "abgeschickt", "selbst_beworben")
    )}
    for ref in set(gute) | beworben:
        job = gute.get(ref) or db.job(ref)
        if not job:
            continue
        # Stellen, die der Nutzer gewaehlt hat, zaehlen doppelt
        faktor = 2 if ref in beworben else 1
        zaehle(ref, job.beruf, faktor, max_worte=4)   # BA-Berufsbezeichnung, meist sauber
        zaehle(ref, job.titel, faktor, max_worte=3)   # freie Titel nur, wenn kurz

    ergebnis: list[Vorschlag] = []
    for schluessel, _ in gewicht.most_common():
        begriff = anzeige[schluessel]
        if _schon_gesucht(begriff, profil.suche.was) or _schon_gesucht(
            begriff, [v.begriff for v in ergebnis]
        ):
            continue
        anzahl = len(stellen[schluessel])
        ergebnis.append(Vorschlag(
            begriff, f"aus {anzahl} passenden Stelle{'n' if anzahl != 1 else ''}", "treffer",
        ))
        if len(ergebnis) >= limit:
            break
    return ergebnis


_SCHEMA = {
    "type": "object",
    "properties": {
        "vorschlaege": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "begriff": {"type": "string", "description": "Suchbegriff fuer eine Jobboerse, 1-4 Woerter"},
                    "grund": {"type": "string", "description": "Ein kurzer Satz, warum der Begriff passt"},
                },
                "required": ["begriff", "grund"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["vorschlaege"],
    "additionalProperties": False,
}

_SYSTEM = """Du hilfst einer arbeitssuchenden Person, auf deutschen Jobboersen
(Bundesagentur fuer Arbeit, GET IN IT) mehr passende Stellen zu finden.

Schlage Suchbegriffe vor, nach denen sie noch NICHT sucht:
- gaengige Synonyme und alternative Jobtitel (auch englische, wenn ueblich),
- Nachbarrollen, fuer die Profil und Lebenslauf realistisch reichen,
- offizielle Berufsbezeichnungen der Bundesagentur, falls hilfreich,
- hoechstens zwei Quereinstiegs-Rollen.
Keine Fantasietitel, keine Rollen deutlich ueber oder unter dem Erfahrungsniveau.
Jeder Begriff 1-4 Woerter, ohne (m/w/d). Antworte nur im vorgegebenen JSON-Schema."""


def per_llm(profil: Profil, gute_titel: list[str] | None = None, limit: int = 8) -> list[Vorschlag]:
    """Vorschlaege vom LLM; wirft LLMNichtVerfuegbar ohne API-Key."""
    nutzer = (
        f"Kurzprofil: {profil.kurzprofil or '-'}\n"
        f"Skills: {', '.join(profil.bewertung.skills) or '-'}\n"
        f"Wunschthemen: {', '.join(profil.bewertung.wunsch) or '-'}\n"
        f"Ausschluesse: {', '.join(profil.bewertung.ausschluss) or '-'}\n"
        f"Ort: {profil.suche.wo or 'egal'} (+{profil.suche.umkreis} km)\n"
        f"Bisherige Suchbegriffe: {', '.join(profil.suche.was)}\n"
        f"Titel gut passender Stellen: {', '.join((gute_titel or [])[:15]) or '-'}\n"
        f"{llm_modul.lebenslauf_block(profil, max_zeichen=4000)}\n"
        f"Gib {limit} Vorschlaege."
    )
    text = llm_modul.chat(
        profil.bewertung.modell, system=_SYSTEM, nutzer=nutzer,
        max_tokens=8000, json_schema=_SCHEMA, effort="low",
    )
    daten = _json_aus_text(text)
    ergebnis: list[Vorschlag] = []
    for eintrag in daten.get("vorschlaege") or []:
        begriff = titel_kern(str(eintrag.get("begriff") or ""))
        if not begriff or _schon_gesucht(begriff, profil.suche.was):
            continue
        if _schon_gesucht(begriff, [v.begriff for v in ergebnis]):
            continue
        ergebnis.append(Vorschlag(begriff, str(eintrag.get("grund") or "").strip(), "ki"))
    return ergebnis[:limit]


def _json_aus_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        daten = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        daten = json.loads(m.group(0)) if m else {}
    return daten if isinstance(daten, dict) else {}


def such_tipps(db: Speicher, profil: Profil) -> list[Tipp]:
    """Konkrete Hinweise, wie die Suche mehr passende Stellen liefert."""
    s = profil.suche
    tipps: list[Tipp] = []
    statistik = {z["begriff"].casefold(): z for z in db.suchbegriff_statistik(profil.bewertung.min_score)}

    for begriff in s.was:
        z = statistik.get(begriff.casefold())
        if not z:
            continue
        if z["abrufe"] >= 2 and z["letzte_treffer"] == 0:
            tipps.append(Tipp(f"„{begriff}“ hat zuletzt keine Treffer geliefert – allgemeiner fassen oder ersetzen."))
        elif z["treffer"] >= 15 and z["passend"] == 0:
            tipps.append(Tipp(
                f"„{begriff}“ bringt {z['treffer']} Treffer, aber keinen passenden – eher streichen "
                "oder genauer formulieren."
            ))

    passend = len(db.bestenliste(min_score=profil.bewertung.min_score, limit=50))
    if passend < 5:
        if s.wo and s.umkreis < 50:
            tipps.append(Tipp(
                f"Bisher nur {passend} passende Stelle(n) – Umkreis von {s.umkreis} auf 50 km erweitern?",
                {"umkreis": 50},
            ))
        if s.veroeffentlicht_seit_tagen < 30:
            tipps.append(Tipp("Auch ältere Anzeigen (bis 30 Tage) einbeziehen?", {"tage": 30}))
        if s.nur_vollzeit:
            tipps.append(Tipp("Teilzeit-/flexible Stellen nicht ausschließen?", {"nur_vollzeit": False}))
        if profil.bewertung.min_score > 50:
            tipps.append(Tipp(
                f"Mindestscore {profil.bewertung.min_score:.0f} ist streng – im Profil auf 50 senken, "
                "um mehr Stellen zur Auswahl zu haben."
            ))
    fehlend = [
        name for name, info in QUELLEN.items()
        if name not in s.quellen and info.verfuegbar()
        and not (name == "wunscharbeitgeber" and not profil.zielfirmen)
        and not (name == "eures" and not s.ausland)
    ]
    if fehlend:
        namen = ", ".join(QUELLEN[q].label for q in fehlend)
        tipps.append(Tipp(f"Weitere Jobbörsen aktivieren: {namen}.", {"quellen": [*s.quellen, *fehlend]}))
    return tipps
