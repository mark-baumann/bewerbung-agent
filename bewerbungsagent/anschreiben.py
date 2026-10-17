"""Anschreiben erzeugen - mit KI (Claude, ChatGPT, Ollama), sonst aus einer Vorlage.

`erzeuge_geprueft` ist der Weg der Automatik: Anschreiben auf Basis des
Stellen-Dossiers (persoenliche Anrede, Muss-Anforderungen), danach
Selbstpruefung gegen den Lebenslauf und bei Maengeln genau eine Ueberarbeitung.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any

from .config import Profil
from .dossier import Dossier, anrede_fuer
from .models import Job, Score
from .scoring.llm import chat, client_verfuegbar, ki_grund, lebenslauf_block

log = logging.getLogger(__name__)

SYSTEM = """Du schreibst Bewerbungsanschreiben fuer genau eine Person.

Bewerber: {name}
Kurzprofil: {kurzprofil}
Skills: {skills}
{lebenslauf}
Stil: {stil}

Regeln:
- Sprache: {sprache}. Anrede genau so: "{anrede}"
- Beziehe dich konkret auf zwei bis drei Anforderungen aus der Anzeige und belege
  sie mit Profil und Lebenslauf. Erfinde keine Erfahrungen, Zahlen, Titel oder Arbeitgeber.
  Was der Lebenslauf nicht hergibt, wird nicht behauptet (lieber Lernbereitschaft zeigen).
- Keine Floskeln wie "hiermit bewerbe ich mich" oder "mit grossem Interesse".
- Hoechstens eine Seite (etwa 200-350 Woerter).
- Nur der Brieftext ab der Anrede bis zur Grussformel mit dem Namen. Kein Betreff,
  kein Briefkopf, keine Platzhalter in eckigen Klammern.
"""

VORLAGE = """{anrede}

Ihre Ausschreibung „{titel}“ bei {arbeitgeber} hat mein Interesse geweckt, deshalb bewerbe ich mich um diese Stelle.

{werdegang}{profilsatz}

{trefferblock}

Ich bin {verfuegbar} verfügbar und freue mich auf ein persönliches Gespräch.

Mit freundlichen Grüßen
{name}
"""


def _werdegang(profil: Profil) -> str:
    """Satz zur letzten Station aus dem ausgelesenen Lebenslauf (nur Fakten von dort)."""
    from . import lebenslauf

    try:
        satz = lebenslauf.stationssatz(profil=profil)
    except Exception as e:  # die Vorlage darf nie scheitern
        log.info("Werdegang nicht lesbar: %s", e)
        return ""
    return f"{satz} " if satz else ""


def _vorlage(job: Job, profil: Profil, score: Score | None, dossier: Dossier | None = None) -> str:
    # Nur echte Skills nennen - Treffer koennen auch Wunschthemen sein, die nichts belegen
    skills = {k.casefold() for k in profil.bewertung.skills}
    treffer = [t for t in (score.treffer if score else []) if t.casefold() in skills] or profil.bewertung.skills[:5]
    trefferblock = (
        "Fachlich bringe ich unter anderem mit: " + ", ".join(treffer[:8]) + "."
        if treffer
        else "Meine Unterlagen mit allen Details finden Sie im Anhang."
    )
    return VORLAGE.format(
        # Die Vorlage ist deutsch - also auch bei englischen Anzeigen eine deutsche Anrede
        anrede=anrede_fuer(dossier.ansprechpartner if dossier else None, "de"),
        titel=job.titel,
        arbeitgeber=job.arbeitgeber or "Ihrem Unternehmen",
        werdegang=_werdegang(profil),
        profilsatz=profil.kurzprofil.strip() or "Mein Profil entnehmen Sie bitte dem beigefügten Lebenslauf.",
        trefferblock=trefferblock,
        verfuegbar=profil.angaben.verfuegbar_ab or "ab sofort",
        name=profil.person.name,
    )


def _nutzermeldung(job: Job, score: Score | None, dossier: Dossier | None) -> str:
    anzeige = textwrap.shorten(job.beschreibung or job.titel, width=10000, placeholder=" [...]")
    teile = [f"Stelle: {job.titel}", f"Arbeitgeber: {job.arbeitgeber}", f"Ort: {job.ort or '-'}"]
    if dossier:
        if dossier.muss:
            teile.append("Muss-Anforderungen:\n" + "\n".join(f"- {m}" for m in dossier.muss[:10]))
        if dossier.aufgaben:
            teile.append("Wichtigste Aufgaben:\n" + "\n".join(f"- {a}" for a in dossier.aufgaben[:5]))
        if dossier.eintritt_gefragt:
            teile.append("Die Anzeige fragt nach dem fruehesten Eintrittstermin.")
        if dossier.gehalt_gefragt:
            teile.append("Die Anzeige fragt nach der Gehaltsvorstellung.")
    if score and score.treffer:
        teile.append("Besonders relevante Uebereinstimmungen: " + ", ".join(score.treffer[:8]))
    teile.append(f"Anzeigentext:\n{anzeige}")
    return "\n\n".join(teile)


def _system(profil: Profil, dossier: Dossier | None) -> str:
    a = profil.angaben
    stil = profil.unterlagen.anschreiben_stil
    zusatz = []
    if dossier and dossier.eintritt_gefragt and a.verfuegbar_ab:
        zusatz.append(f"Fruehester Eintritt: {a.verfuegbar_ab}")
    if dossier and dossier.gehalt_gefragt and a.gehaltsvorstellung:
        zusatz.append(f"Gehaltsvorstellung: {a.gehaltsvorstellung}")
    return SYSTEM.format(
        name=profil.person.name,
        kurzprofil=profil.kurzprofil or "(kein Kurzprofil hinterlegt)",
        skills=", ".join(profil.bewertung.skills) or "-",
        lebenslauf=lebenslauf_block(profil) + ("\n".join(zusatz) + "\n" if zusatz else ""),
        stil=stil,
        sprache="Englisch" if dossier and dossier.sprache == "en" else "Deutsch",
        anrede=dossier.anrede if dossier else "Sehr geehrte Damen und Herren,",
    )


def _erzeuge(
    job: Job, profil: Profil, score: Score | None, mit_llm: bool, dossier: Dossier | None,
) -> tuple[str, str, str]:
    """(Text, Quelle llm|vorlage, warum die KI nicht geholfen hat – leer, wenn sie es nicht sollte)."""
    if not mit_llm or not client_verfuegbar():
        return _vorlage(job, profil, score, dossier), "vorlage", ""
    try:
        text = chat(
            profil.bewertung.modell,
            system=_system(profil, dossier),
            nutzer=_nutzermeldung(job, score, dossier),
            max_tokens=16000,
            effort="medium",
        ).strip()
    except Exception as e:
        grund = ki_grund(e)
        log.warning("Anschreiben per LLM fehlgeschlagen (%s: %s) - nutze Vorlage.", grund, str(e)[:300])
        return _vorlage(job, profil, score, dossier), "vorlage", grund
    if not text:
        return _vorlage(job, profil, score, dossier), "vorlage", "die KI hat keine verwertbare Antwort geliefert"
    return text, "llm", ""


def erzeuge(
    job: Job,
    profil: Profil,
    score: Score | None = None,
    mit_llm: bool = True,
    dossier: Dossier | None = None,
) -> str:
    """Erzeugt das Anschreiben. Faellt bei Problemen auf die Vorlage zurueck."""
    return _erzeuge(job, profil, score, mit_llm, dossier)[0]


@dataclass
class Ergebnis:
    text: str
    quelle: str                      # llm | vorlage
    qualitaet: float
    bericht: dict[str, Any] = field(default_factory=dict)


def erzeuge_geprueft(
    job: Job,
    profil: Profil,
    score: Score | None = None,
    *,
    mit_llm: bool = True,
    dossier: Dossier | None = None,
) -> Ergebnis:
    """Schreiben -> pruefen -> bei Maengeln einmal ueberarbeiten -> erneut pruefen."""
    from . import pruefung

    ki = mit_llm and client_verfuegbar()
    text, quelle, ki_fehler = _erzeuge(job, profil, score, ki, dossier)
    bericht = pruefung.pruefe(text, job, profil, dossier, mit_llm=quelle == "llm")
    if quelle == "llm" and (bericht.unbelegt or bericht.maengel or bericht.qualitaet < 80):
        try:
            neu = pruefung.ueberarbeite(text, bericht, job, profil)
            if neu:
                neuer_bericht = pruefung.pruefe(neu, job, profil, dossier, mit_llm=True)
                if neuer_bericht.qualitaet >= bericht.qualitaet:
                    text, bericht = neu, neuer_bericht
                    bericht.ueberarbeitet = True
        except Exception as e:
            log.info("Überarbeitung fehlgeschlagen (%s) – erste Fassung bleibt.", e)
    qualitaet = bericht.qualitaet if quelle == "llm" else min(bericht.qualitaet, 50.0)
    daten = bericht.als_dict()
    if ki_fehler:
        daten["ki_fehler"] = ki_fehler  # z. B. „das KI-Konto hat kein Guthaben mehr“ (zeigt die Karte)
    return Ergebnis(text=text, quelle=quelle, qualitaet=qualitaet, bericht=daten)
