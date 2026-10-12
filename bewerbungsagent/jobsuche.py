"""Gemeinsamer, nachvollziehbar protokollierter Abruf von Jobboersen.

Jede Abfrage wird in `abrufe` protokolliert (Quelle, Suchbegriff, Parameter,
Ergebnis) und ihre Treffer in `abruf_treffer` - auch die lokal gefilterten
Treffer aus Feeds, damit die Suchbegriff-Statistik fuer alle Quellen stimmt.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .db import Speicher
from .models import Job
from .sources import QUELLEN, client_fuer
from .sources.basis import begriff_passt, normalisiert, ort_passt
from .zeitplan import als_datetime

log = logging.getLogger(__name__)

FEED_BEGRIFF = "(Feed)"
WUNSCH_BEGRIFF = "Wunsch-Arbeitgeber"


def _protokolliert(db: Speicher, quelle: str, suchbegriff: str, parameter: dict[str, Any], holen) -> tuple[list[Job], int, int]:
    abruf_id = db.starte_abruf(quelle, suchbegriff, parameter)
    try:
        jobs = holen()
        # Die Abfrage ist die verlaessliche Herkunft, falls ein Adapter quelle vergisst
        for job in jobs:
            job.quelle = job.quelle or quelle
        neu, aktualisiert = db.speichere_jobs(jobs)
        db.merke_abruf_treffer(abruf_id, [j.ref for j in jobs])
    except Exception as exc:
        db.beende_abruf(abruf_id, fehler=str(exc)[:1000])
        raise
    db.beende_abruf(abruf_id, treffer=len(jobs), neu=neu, aktualisiert=aktualisiert)
    return jobs, neu, aktualisiert


def hole_jobs(
    db: Speicher,
    *,
    quelle: str,
    suchbegriff: str,
    wo: str,
    umkreis: int,
    tage: int,
    nur_vollzeit: bool,
    max_treffer: int,
    mit_details: bool,
    profil: Any = None,
    **extra: Any,
) -> tuple[list[Job], int, int]:
    """Eine Suchabfrage bei einer Quelle mit Stichwortsuche."""
    parameter: dict[str, Any] = {
        "wo": wo, "umkreis_km": umkreis, "veroeffentlicht_seit_tagen": tage,
        "nur_vollzeit": nur_vollzeit, "max_treffer": max_treffer,
        "mit_details": mit_details,
    }

    def holen() -> list[Job]:
        with client_fuer(quelle, profil) as client:
            return client.hole_jobs(
                was=suchbegriff, wo=wo, umkreis=umkreis,
                veroeffentlicht_seit_tagen=tage, nur_vollzeit=nur_vollzeit,
                max_treffer=max_treffer, mit_details=mit_details, **extra,
            )

    return _protokolliert(db, quelle, suchbegriff, parameter, holen)


def _faellig(db: Speicher, quelle: str, suchbegriff: str, minuten: int) -> bool:
    letzter = db.letzter_abruf(quelle, suchbegriff)
    if not letzter or minuten <= 0:
        return True
    zeitpunkt = als_datetime(letzter["gestartet_am"])
    return zeitpunkt is None or datetime.now(timezone.utc) - zeitpunkt >= timedelta(minutes=minuten)


def aktualisiere_feed(db: Speicher, quelle: str, profil: Any = None, erzwingen: bool = False) -> tuple[int, int, bool]:
    """Laedt den Gesamtbestand einer Feed-Quelle, wenn der Mindestabstand vorbei ist.

    Liefert (neu, aktualisiert, abgerufen).
    """
    info = QUELLEN[quelle]
    if not erzwingen and not _faellig(db, quelle, FEED_BEGRIFF, info.min_abstand_min):
        return 0, 0, False

    def holen() -> list[Job]:
        with client_fuer(quelle, profil) as client:
            return client.hole_feed()

    _, neu, aktualisiert = _protokolliert(db, quelle, FEED_BEGRIFF, {"art": "feed"}, holen)
    return neu, aktualisiert, True


def orte_fuer(db: Speicher, wo: str, umkreis: int) -> list[str]:
    """Normalisierte Ortsnamen im Umkreis (aus den Treffern der Arbeitsagentur gelernt)."""
    if not (wo or "").strip():
        return []
    orte = {normalisiert(wo)}
    orte |= {normalisiert(o) for o in db.orte_im_umkreis(umkreis)}
    return sorted(o for o in orte if o)


def _waehle_lokal(
    jobs: Iterable[Job], *, begriffe: list[str] | None, orte: list[str], remote_ok: bool,
    nur_vollzeit: bool, max_treffer: int,
) -> list[Job]:
    ergebnis = []
    for job in jobs:
        if begriffe is not None and not any(begriff_passt(job, b) for b in begriffe):
            continue
        if not ort_passt(job, orte, remote_ok):
            continue
        if nur_vollzeit and job.vollzeit is False:
            continue
        ergebnis.append(job)
        if len(ergebnis) >= max_treffer:
            break
    return ergebnis


def _anreichern(db: Speicher, quelle: str, jobs: list[Job], profil: Any) -> None:
    """Volltext fuer ausgewaehlte Feed-Stellen nachladen (einzeln, Fehler sind egal)."""
    fehlend = [j for j in jobs if not j.beschreibung]
    if not fehlend:
        return
    with client_fuer(quelle, profil) as client:
        for job in fehlend:
            try:
                client.anreichern(job)
            except Exception as e:
                log.info("Anreichern von %s fehlgeschlagen: %s", job.ref, e)
    db.speichere_jobs(fehlend)


def lokale_treffer(
    db: Speicher,
    quelle: str,
    *,
    suchbegriff: str,
    orte: list[str],
    remote_ok: bool,
    tage: int,
    nur_vollzeit: bool,
    max_treffer: int,
    mit_details: bool = True,
    profil: Any = None,
) -> list[Job]:
    """Stellen einer Feed-Quelle aus der Datenbank, passend zu Suchbegriff und Ort."""
    seit = (date.today() - timedelta(days=max(1, int(tage)))).isoformat()
    parameter = {"lokal": True, "orte": len(orte), "remote_ok": remote_ok, "veroeffentlicht_seit_tagen": tage}

    def holen() -> list[Job]:
        jobs = _waehle_lokal(db.jobs_der_quelle(quelle, seit), begriffe=[suchbegriff], orte=orte,
                             remote_ok=remote_ok, nur_vollzeit=nur_vollzeit, max_treffer=max_treffer)
        if mit_details:
            _anreichern(db, quelle, jobs, profil)
        return jobs

    jobs, _, _ = _protokolliert(db, quelle, suchbegriff, parameter, holen)
    return jobs


def hole_wunscharbeitgeber(
    db: Speicher,
    profil: Any,
    *,
    orte: list[str],
    remote_ok: bool,
    begriffe: list[str],
    max_treffer: int = 50,
    erzwingen: bool = False,
) -> tuple[list[Job], list[str]]:
    """Stellen der Wunsch-Arbeitgeber: Feeds je Firma auffrischen, dann passende waehlen.

    Relevanz: Titel passt zu einem Suchbegriff ODER enthaelt einen Pflicht-/Wunsch-Skill.
    Liefert (Stellen, Fehlermeldungen).
    """
    info = QUELLEN["wunscharbeitgeber"]
    fehler: list[str] = []
    praefixe: list[str] = []
    for firma in getattr(profil, "zielfirmen", None) or []:
        name = firma.get("name") or firma.get("kennung") or firma.get("url") or "?"
        if not (firma.get("ats") and firma.get("kennung")) and firma.get("url"):
            from .sources.ats import erkenne_ats

            erkannt = erkenne_ats(firma["url"])
            if erkannt:
                firma.update(erkannt)  # wird beim naechsten Speichern des Profils uebernommen
        if not (firma.get("ats") and firma.get("kennung")):
            fehler.append(f"{name}: Bewerbermanagement nicht erkannt")
            continue
        praefixe.append(f"{firma['ats'].lower()}:{firma['kennung']}:")
        schluessel = f"(Firma) {name}"
        if not erzwingen and not _faellig(db, "wunscharbeitgeber", schluessel, info.min_abstand_min):
            continue

        def holen(firma=firma) -> list[Job]:
            with client_fuer("wunscharbeitgeber", profil) as client:
                return client.hole_arbeitgeber(firma)

        try:
            _protokolliert(db, "wunscharbeitgeber", schluessel, {"firma": firma}, holen)
        except Exception as e:
            fehler.append(f"{name}: {e}")
    if not praefixe:
        return [], fehler

    skills = [*getattr(profil.bewertung, "skills", []), *getattr(profil.bewertung, "wunsch", [])]

    def relevant(job: Job) -> bool:
        if any(begriff_passt(job, b) for b in begriffe):
            return True
        titel = normalisiert(job.titel)
        return any(normalisiert(s) and normalisiert(s) in titel for s in skills)

    def holen_lokal() -> list[Job]:
        kandidaten = [j for p in praefixe for j in db.jobs_der_quelle("wunscharbeitgeber", praefix=p)]
        kandidaten = [j for j in kandidaten if relevant(j)]
        jobs = _waehle_lokal(kandidaten, begriffe=None, orte=orte, remote_ok=remote_ok,
                             nur_vollzeit=False, max_treffer=max_treffer)
        _anreichern(db, "wunscharbeitgeber", jobs, profil)
        return jobs

    jobs, _, _ = _protokolliert(db, "wunscharbeitgeber", WUNSCH_BEGRIFF, {"lokal": True}, holen_lokal)
    return jobs, fehler
