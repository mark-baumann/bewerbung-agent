"""Gemeinsamer, nachvollziehbar protokollierter Abruf von Jobboersen."""

from __future__ import annotations

from typing import Any

from .db import Speicher
from .models import Job
from .sources import client_fuer


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
) -> tuple[list[Job], int, int]:
    """Holt eine Abfrage und speichert sowohl Ergebnis als auch Herkunft."""
    parameter: dict[str, Any] = {
        "wo": wo, "umkreis_km": umkreis, "veroeffentlicht_seit_tagen": tage,
        "nur_vollzeit": nur_vollzeit, "max_treffer": max_treffer,
        "mit_details": mit_details,
    }
    abruf_id = db.starte_abruf(quelle, suchbegriff, parameter)
    try:
        with client_fuer(quelle) as client:
            jobs = client.hole_jobs(
                was=suchbegriff, wo=wo, umkreis=umkreis,
                veroeffentlicht_seit_tagen=tage, nur_vollzeit=nur_vollzeit,
                max_treffer=max_treffer, mit_details=mit_details,
            )
        # Quellenadapter duerfen den Wert nicht vergessen; die Abfrage ist die
        # verlässliche Herkunft fuer jeden hier erhaltenen Datensatz.
        for job in jobs:
            job.quelle = job.quelle or quelle
        neu, aktualisiert = db.speichere_jobs(jobs)
    except Exception as exc:
        db.beende_abruf(abruf_id, fehler=str(exc)[:1000])
        raise
    db.beende_abruf(abruf_id, treffer=len(jobs), neu=neu, aktualisiert=aktualisiert)
    return jobs, neu, aktualisiert
