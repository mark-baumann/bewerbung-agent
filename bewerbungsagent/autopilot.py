"""Autopilot: die KI bewirbt sich selbst - nur wenn der Nutzer es einschaltet.

Standard ist „aus“: jede Bewerbung braucht ein Ja. Eingeschaltet gibt es zwei
Modi:

- ``mit_frist``: Die Bewerbung steht wie gewohnt im Cockpit, mit dem Hinweis
  „geht in X Stunden raus“. Ohne Stopp/Nein gibt der Tick sie nach Ablauf frei.
- ``sofort``: Die Bewerbung wird direkt nach der Vorbereitung freigegeben.

In beiden Faellen nur, wenn ALLE Schutzgrenzen halten (siehe `pruefe`). Alles
andere bleibt eine normale Ja/Nein-Karte - mit der Begruendung, warum der
Autopilot sie nicht allein verschickt. Jede Entscheidung wird protokolliert.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Profil
from .db import Speicher, dubletten_schluessel
from .models import Job, Score, Vorgang
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

ARBEITGEBER_SPERRTAGE = 30


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _arbeitgeber_kuerzlich(db: Speicher, job: Job, ref: str) -> bool:
    """Schon eine andere Bewerbung an denselben Arbeitgeber in den letzten 30 Tagen?"""
    name = dubletten_schluessel(job.arbeitgeber, "").split("|")[0]
    if not name:
        return False
    grenze = (_jetzt() - timedelta(days=ARBEITGEBER_SPERRTAGE)).isoformat(timespec="seconds")
    for v in db.vorgaenge(("freigegeben", "wird_gesendet", "abgeschickt", "selbst_beworben")):
        if v.ref == ref:
            continue
        anderer = db.job(v.ref)
        zeit = v.gesendet_am or v.freigegeben_am or v.aktualisiert_am or ""
        if anderer and dubletten_schluessel(anderer.arbeitgeber, "").split("|")[0] == name and zeit >= grenze:
            return True
    return False


def pruefe(db: Speicher, profil: Profil, v: Vorgang, job: Job, score: Score | None) -> list[str]:
    """Gruende, warum der Autopilot NICHT allein senden darf (leer = darf)."""
    a = profil.automatisierung
    gruende: list[str] = []
    if a.effektiver_autopilot == "aus":
        return ["Autopilot ist aus."]
    if not llm_modul.client_verfuegbar() or v.anschreiben_quelle != "llm":
        gruende.append("Anschreiben stammt aus der Vorlage, nicht von der KI.")
    if score is None or score.ausgeschlossen:
        gruende.append("Stelle ist nicht (passend) bewertet.")
    elif score.passung < a.autopilot_min_passung or score.gesamt < profil.bewertung.min_score:
        gruende.append(f"Passung {score.passung:.0f} unter {a.autopilot_min_passung:.0f}.")
    elif not score.bewerter.startswith("llm"):
        gruende.append("Nur per Stichworten bewertet.")
    bericht = v.pruefung_daten
    if (v.qualitaet or 0) < a.autopilot_min_qualitaet:
        gruende.append(f"Qualität des Anschreibens {v.qualitaet or 0:.0f} unter {a.autopilot_min_qualitaet:.0f}.")
    if bericht.get("unbelegt"):
        gruende.append("Anschreiben enthält nicht belegte Aussagen.")
    if v.kanal == "email":
        if not bericht.get("empfaenger_sicher"):
            gruende.append("E-Mail-Adresse ist nicht eindeutig als Bewerbungsadresse genannt.")
    elif v.kanal == "formular":
        probe = v.probelauf_daten
        if not probe:
            gruende.append("Kein Probelauf des Formulars.")
        elif probe.get("hindernis") or probe.get("fehlende_angaben") or probe.get("versehentlich_abgeschickt"):
            gruende.append("Probelauf war nicht vollständig erfolgreich.")
    else:
        gruende.append("Kein automatischer Bewerbungsweg.")
    from .ablauf import beworben_heute, versandweg_fehlt

    fehlt = versandweg_fehlt(v.kanal, profil)
    if fehlt:
        gruende.append(fehlt)
    if not profil.anhaenge():
        gruende.append("Kein Lebenslauf hinterlegt.")
    frist = (job.bewerbungsfrist or "")[:10]
    if re.match(r"\d{4}-\d{2}-\d{2}$", frist) and frist < _jetzt().date().isoformat():
        gruende.append("Bewerbungsfrist abgelaufen.")
    if _arbeitgeber_kuerzlich(db, job, v.ref):
        gruende.append(f"An {job.arbeitgeber} ging in den letzten {ARBEITGEBER_SPERRTAGE} Tagen schon eine Bewerbung.")
    if beworben_heute(db) >= a.max_pro_tag:
        gruende.append(f"Tageslimit von {a.max_pro_tag} Bewerbungen erreicht.")
    return gruende


def _merke(v: Vorgang, **daten: Any) -> None:
    bericht = v.pruefung_daten
    bericht["autopilot"] = {**bericht.get("autopilot", {}), **daten}
    v.pruefung = json.dumps(bericht, ensure_ascii=False)


def nach_vorbereitung(db: Speicher, profil: Profil, v: Vorgang) -> str:
    """Nach dem Vorbereiten: freigeben, Frist setzen oder als Karte stehen lassen.

    Liefert 'gesendet' | 'frist' | 'karte' | 'aus'.
    """
    modus = profil.automatisierung.effektiver_autopilot
    if modus == "aus" or v.status != "wartet":
        return "aus"
    job, score = db.job(v.ref), db.score(v.ref)
    if job is None:
        return "aus"
    gruende = pruefe(db, profil, v, job, score)
    if gruende:
        _merke(v, freigabe=False, gruende=gruende)
        db.aktualisiere_vorgang(v.ref, pruefung=v.pruefung)
        return "karte"
    if modus == "mit_frist":
        ab = _jetzt() + timedelta(hours=max(0.0, float(profil.automatisierung.autopilot_frist_stunden)))
        _merke(v, freigabe=True, gruende=[])
        db.aktualisiere_vorgang(v.ref, auto_senden_ab=ab.isoformat(timespec="seconds"), pruefung=v.pruefung)
        return "frist"
    return "gesendet" if _freigeben(db, profil, v.ref) else "karte"


def _freigeben(db: Speicher, profil: Profil, ref: str) -> bool:
    from . import ablauf

    try:
        ablauf.freigeben(db, profil, ref, durch="autopilot")
    except ValueError as e:
        log.info("Autopilot konnte %s nicht freigeben: %s", ref, e)
        return False
    return True


def faellige_freigeben(db: Speicher, profil: Profil, *, jetzt: datetime | None = None) -> int:
    """Tick: Bewerbungen, deren Einspruchsfrist abgelaufen ist, freigeben (nach erneuter Pruefung)."""
    if profil.automatisierung.effektiver_autopilot == "aus":
        return 0
    stichzeit = (jetzt or _jetzt()).isoformat(timespec="seconds")
    anzahl = 0
    for v in db.vorgaenge("wartet"):
        if not v.auto_senden_ab or v.auto_senden_ab > stichzeit:
            continue
        job, score = db.job(v.ref), db.score(v.ref)
        gruende = pruefe(db, profil, v, job, score) if job else ["Stelle fehlt."]
        if gruende:
            _merke(v, freigabe=False, gruende=gruende)
            db.aktualisiere_vorgang(v.ref, auto_senden_ab=None, pruefung=v.pruefung)
            continue
        if _freigeben(db, profil, v.ref):
            anzahl += 1
    return anzahl


def stoppen(db: Speicher, ref: str) -> None:
    """Einspruch: diese Bewerbung nicht automatisch senden (bleibt als Karte)."""
    v = db.vorgang(ref)
    if v is None:
        return
    _merke(v, freigabe=False, gruende=["Von dir angehalten."])
    db.aktualisiere_vorgang(ref, auto_senden_ab=None, pruefung=v.pruefung)
    db.protokolliere_entscheidung(ref, "autopilot_gestoppt")
