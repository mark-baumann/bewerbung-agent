"""Deterministische Bewertung ohne LLM: Skill-Match + Sentiment + Ausschluesse.

Laeuft immer und ohne API-Key. Der LLM-Bewerter baut darauf auf.
"""

from __future__ import annotations

import re

from ..config import Profil
from ..models import Job, Score
from . import sentiment as sentiment_modul


def _wortgrenze(begriff: str) -> re.Pattern[str]:
    # Mehrwortbegriffe und Sonderzeichen (C++, .NET) robust behandeln
    teile = [re.escape(t) for t in begriff.strip().split()]
    kern = r"\s+".join(teile)
    vorn = r"(?<![\w+#.])" if begriff[:1].isalnum() else r""
    hinten = r"(?![\w+#])" if begriff[-1:].isalnum() else r""
    return re.compile(vorn + kern + hinten, re.IGNORECASE)


def finde_begriffe(text: str, begriffe: list[str]) -> list[str]:
    return [b for b in begriffe if b.strip() and _wortgrenze(b).search(text)]


def passungswert(text: str, skills: list[str], wunsch: list[str]) -> tuple[float, list[str]]:
    """0..100: Anteil getroffener Pflicht-Skills, plus Bonus fuer Wunschthemen."""
    if not skills:
        return 50.0, []
    treffer = finde_begriffe(text, skills)
    quote = len(treffer) / len(skills)
    # Wurzelkennlinie: die ersten Treffer zaehlen am meisten, sonst braucht man
    # bei 20 gelisteten Skills unrealistisch viele fuer einen brauchbaren Score.
    basis = 100.0 * quote**0.6

    wunschtreffer = finde_begriffe(text, wunsch) if wunsch else []
    bonus = min(15.0, 5.0 * len(wunschtreffer))
    return round(min(100.0, basis + bonus), 1), treffer + wunschtreffer


def bewerte(job: Job, profil: Profil) -> Score:
    text = job.volltext
    b = profil.bewertung

    ausschluss = finde_begriffe(text, b.ausschluss)
    passung, treffer = passungswert(text, b.skills, b.wunsch)
    sent = sentiment_modul.analysiere(job.beschreibung)

    # Passung dominiert, Tonalitaet korrigiert. Ohne Beschreibung ist das
    # Sentiment nur der Neutralwert und darf das Ergebnis nicht praegen.
    gewicht_sentiment = 0.3 if job.beschreibung else 0.1
    gesamt = passung * (1 - gewicht_sentiment) + sent.wert * gewicht_sentiment

    if profil.suche.nur_homeoffice and job.homeoffice is False:
        gesamt -= 15.0

    gruende = []
    if treffer:
        gruende.append(f"Treffer: {', '.join(treffer[:8])}")
    else:
        gruende.append("keine Profil-Skills im Text gefunden")
    gruende.append(f"Ton der Anzeige: {sent.label} ({sent.wert})")
    if sent.rot:
        gruende.append(f"Warnsignale: {', '.join(sent.rot[:5])}")

    return Score(
        ref=job.ref,
        gesamt=round(max(0.0, min(100.0, gesamt)), 1),
        passung=passung,
        sentiment=sent.wert,
        treffer=treffer,
        gruen=sent.gruen,
        rot=sent.rot,
        begruendung="; ".join(gruende),
        ausgeschlossen=bool(ausschluss),
        ausschlussgrund=f"Ausschlussbegriff: {', '.join(ausschluss)}" if ausschluss else None,
        bewerter="heuristik",
    )
