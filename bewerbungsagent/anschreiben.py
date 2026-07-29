"""Anschreiben erzeugen - mit Claude, sonst aus einer Vorlage."""

from __future__ import annotations

import logging
import textwrap

from .config import Profil
from .models import Job, Score
from .scoring.llm import LLMNichtVerfuegbar, client_verfuegbar

log = logging.getLogger(__name__)

SYSTEM = """Du schreibst Bewerbungsanschreiben fuer genau eine Person.

Bewerber: {name}
Kurzprofil: {kurzprofil}
Skills: {skills}

Stil: {stil}

Regeln:
- Deutsch, Anrede "Sehr geehrte Damen und Herren," wenn kein Ansprechpartner genannt ist.
- Beziehe dich konkret auf zwei bis drei Anforderungen aus der Anzeige und belege
  sie mit dem Profil. Erfinde keine Erfahrungen, Zahlen, Titel oder Arbeitgeber.
- Keine Floskeln wie "hiermit bewerbe ich mich" oder "mit grossem Interesse".
- Nur der Brieftext ab der Anrede bis zur Grussformel. Kein Betreff, kein
  Briefkopf, keine Platzhalter in eckigen Klammern.
"""

VORLAGE = """Sehr geehrte Damen und Herren,

auf Ihre Ausschreibung "{titel}" bei {arbeitgeber} bewerbe ich mich hiermit.

{profilsatz}

{trefferblock}

Ueber ein Gespraech freue ich mich.

Mit freundlichen Gruessen
{name}
"""


def _vorlage(job: Job, profil: Profil, score: Score | None) -> str:
    treffer = (score.treffer if score else []) or profil.bewertung.skills[:5]
    trefferblock = (
        "Fachlich decke ich unter anderem ab: " + ", ".join(treffer[:8]) + "."
        if treffer
        else "Meine Unterlagen mit allen Details finden Sie im Anhang."
    )
    return VORLAGE.format(
        titel=job.titel,
        arbeitgeber=job.arbeitgeber or "Ihrem Unternehmen",
        profilsatz=profil.kurzprofil or "Mein Profil entnehmen Sie bitte dem beigefuegten Lebenslauf.",
        trefferblock=trefferblock,
        name=profil.person.name,
    )


def erzeuge(
    job: Job,
    profil: Profil,
    score: Score | None = None,
    mit_llm: bool = True,
) -> str:
    """Erzeugt das Anschreiben. Faellt bei Problemen auf die Vorlage zurueck."""
    if not mit_llm or not client_verfuegbar():
        return _vorlage(job, profil, score)
    try:
        import anthropic

        client = anthropic.Anthropic()
        system = SYSTEM.format(
            name=profil.person.name,
            kurzprofil=profil.kurzprofil or "(kein Kurzprofil hinterlegt)",
            skills=", ".join(profil.bewertung.skills) or "-",
            stil=profil.unterlagen.anschreiben_stil,
        )
        anzeige = textwrap.shorten(
            job.beschreibung or job.titel, width=10000, placeholder=" [...]"
        )
        hinweis = ""
        if score and score.treffer:
            hinweis = "\n\nBesonders relevante Uebereinstimmungen: " + ", ".join(score.treffer[:8])

        antwort = client.messages.create(
            model=profil.bewertung.modell,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Stelle: {job.titel}\n"
                        f"Arbeitgeber: {job.arbeitgeber}\n"
                        f"Ort: {job.ort or '-'}\n\n"
                        f"Anzeigentext:\n{anzeige}{hinweis}"
                    ),
                }
            ],
        )
        if antwort.stop_reason == "refusal":
            raise LLMNichtVerfuegbar(str(antwort.stop_details))
        text = "\n".join(b.text for b in antwort.content if b.type == "text").strip()
        return text or _vorlage(job, profil, score)
    except Exception as e:
        log.warning("Anschreiben per LLM fehlgeschlagen (%s) - nutze Vorlage.", e)
        return _vorlage(job, profil, score)
