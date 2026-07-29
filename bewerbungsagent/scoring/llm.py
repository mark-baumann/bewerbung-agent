"""Semantische Bewertung mit Claude.

Die Heuristik sieht nur Stringtreffer. Der LLM-Bewerter beurteilt zusaetzlich,
ob die Rolle inhaltlich zum Profil passt (auch bei anderer Wortwahl) und wie
die Anzeige tonal einzuordnen ist. Ohne API-Key faellt alles auf die Heuristik
zurueck.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap

from ..config import Profil
from ..models import Job, Score
from . import heuristik

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "passung": {
            "type": "integer",
            "description": "0-100: fachliche Passung der Rolle zum Bewerberprofil",
        },
        "sentiment": {
            "type": "integer",
            "description": (
                "0-100: Tonalitaet und implizite Arbeitsbedingungen der Anzeige. "
                "50 = neutral, <40 = Warnsignale (Ueberstundenkultur, Floskeln, "
                "Zeitarbeit), >70 = konkrete gute Bedingungen"
            ),
        },
        "treffer": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Konkrete Anforderungen aus der Anzeige, die das Profil erfuellt",
        },
        "gruen": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Positive Signale der Anzeige",
        },
        "rot": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Warnsignale oder fehlende Anforderungen",
        },
        "ausgeschlossen": {
            "type": "boolean",
            "description": "true, wenn die Stelle ein Ausschlusskriterium des Profils verletzt",
        },
        "ausschlussgrund": {"type": ["string", "null"]},
        "begruendung": {
            "type": "string",
            "description": "Zwei bis drei Saetze, konkret, ohne Floskeln",
        },
    },
    "required": [
        "passung",
        "sentiment",
        "treffer",
        "gruen",
        "rot",
        "ausgeschlossen",
        "ausschlussgrund",
        "begruendung",
    ],
    "additionalProperties": False,
}

SYSTEM = """Du bewertest Stellenanzeigen fuer genau einen Bewerber.

Bewerberprofil:
{profil}

Pflicht-Skills: {skills}
Wunschthemen: {wunsch}
Ausschlusskriterien: {ausschluss}
Ort/Umkreis: {ort} / {umkreis} km

Bewerte streng und ehrlich. Eine Anzeige, die nur zufaellig ein Stichwort
enthaelt, ist keine Passung. Sprachlich euphorische Anzeigen ("junges
dynamisches Team", "Macher gesucht", "hohe Belastbarkeit") signalisieren
haeufig Ueberstundenkultur und hohe Fluktuation - werte das im Sentiment
negativ, unabhaengig davon wie positiv der Text klingt. Konkrete Angaben
(Gehaltsspanne, Tarifbindung, unbefristet, Gleitzeit) werte positiv.
Antworte ausschliesslich im vorgegebenen JSON-Schema."""


class LLMNichtVerfuegbar(RuntimeError):
    pass


def client_verfuegbar() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class LLMBewerter:
    def __init__(self, profil: Profil):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMNichtVerfuegbar("Paket 'anthropic' nicht installiert") from e
        if not client_verfuegbar():
            raise LLMNichtVerfuegbar(
                "ANTHROPIC_API_KEY nicht gesetzt - Bewertung laeuft nur heuristisch."
            )
        self.profil = profil
        self.client = anthropic.Anthropic()
        self.modell = profil.bewertung.modell
        self.effort = profil.bewertung.effort
        self._system = SYSTEM.format(
            profil=profil.kurzprofil or "(kein Kurzprofil hinterlegt)",
            skills=", ".join(profil.bewertung.skills) or "-",
            wunsch=", ".join(profil.bewertung.wunsch) or "-",
            ausschluss=", ".join(profil.bewertung.ausschluss) or "-",
            ort=profil.suche.wo or "egal",
            umkreis=profil.suche.umkreis,
        )

    def bewerte(self, job: Job) -> Score:
        anzeige = textwrap.shorten(
            job.beschreibung or "(keine Beschreibung verfuegbar)",
            width=12000,
            placeholder=" [...]",
        )
        inhalt = (
            f"Titel: {job.titel}\n"
            f"Arbeitgeber: {job.arbeitgeber}\n"
            f"Beruf: {job.beruf or '-'}\n"
            f"Ort: {job.ort or '-'} ({job.plz or '-'})"
            f"{f', {job.entfernung_km} km entfernt' if job.entfernung_km is not None else ''}\n"
            f"Homeoffice: {job.homeoffice}\n"
            f"Vertrag: {job.befristung or 'unbekannt'}\n"
            f"Verguetung: {job.verguetung or 'keine Angabe'}\n\n"
            f"Anzeigentext:\n{anzeige}"
        )

        antwort = self.client.messages.create(
            model=self.modell,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            system=[{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": inhalt}],
        )

        if antwort.stop_reason == "refusal":
            raise LLMNichtVerfuegbar(f"Anfrage abgelehnt: {antwort.stop_details}")

        text = next((b.text for b in antwort.content if b.type == "text"), None)
        if not text:
            raise LLMNichtVerfuegbar("Leere Antwort vom Modell")
        daten = json.loads(text)

        passung = float(daten["passung"])
        sent = float(daten["sentiment"])
        gewicht = 0.3 if job.beschreibung else 0.1
        return Score(
            ref=job.ref,
            gesamt=round(passung * (1 - gewicht) + sent * gewicht, 1),
            passung=passung,
            sentiment=sent,
            treffer=list(daten.get("treffer") or []),
            gruen=list(daten.get("gruen") or []),
            rot=list(daten.get("rot") or []),
            begruendung=daten.get("begruendung", ""),
            ausgeschlossen=bool(daten.get("ausgeschlossen")),
            ausschlussgrund=daten.get("ausschlussgrund"),
            bewerter=f"llm:{self.modell}",
        )


def bewerte_jobs(jobs: list[Job], profil: Profil, mit_llm: bool = True) -> list[Score]:
    """Bewertet alle Jobs. Faellt pro Job auf die Heuristik zurueck, wenn das LLM ausfaellt."""
    bewerter: LLMBewerter | None = None
    if mit_llm:
        try:
            bewerter = LLMBewerter(profil)
        except LLMNichtVerfuegbar as e:
            log.warning("LLM-Bewertung nicht aktiv (%s) - nutze Heuristik.", e)

    ergebnisse: list[Score] = []
    for job in jobs:
        # Harte Ausschluesse gelten immer und sparen einen LLM-Aufruf.
        basis = heuristik.bewerte(job, profil)
        if bewerter is None or basis.ausgeschlossen:
            ergebnisse.append(basis)
            continue
        try:
            score = bewerter.bewerte(job)
            # Regex-Warnsignale ergaenzen, die das Modell uebersehen hat.
            score.rot = sorted(set(score.rot) | set(basis.rot))
            ergebnisse.append(score)
        except Exception as e:  # Netzwerk, Rate-Limit, kaputtes JSON
            log.warning("LLM-Bewertung fuer %s fehlgeschlagen (%s) - Heuristik.", job.ref, e)
            ergebnisse.append(basis)
    return ergebnisse
