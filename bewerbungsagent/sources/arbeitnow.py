"""Arbeitnow (arbeitnow.com) - freie Job-Board-API, Stellen in Deutschland mit Volltext.

Die API kennt keine Suche: sie liefert den aktuellen Bestand seitenweise
(stuendlich aktualisiert). Wir holen wenige Seiten als Feed und filtern lokal.
Nutzungsbedingungen: kostenlos, nicht missbrauchen, Rueckverlinkung erwuenscht -
die Anzeige verlinkt immer auf die Arbeitnow-Seite der Stelle.
"""

from __future__ import annotations

import time

from ..models import Job
from .basis import QuellenClient, iso_datum, ohne_html

API = "https://www.arbeitnow.com/api/job-board-api"
SEITEN = 3


class ArbeitnowClient(QuellenClient):
    name = "arbeitnow"

    def hole_feed(self) -> list[Job]:
        jobs: list[Job] = []
        for seite in range(1, SEITEN + 1):
            daten = self._json(API, params={"page": seite})
            eintraege = daten.get("data") or []
            jobs += [job_aus_eintrag(e) for e in eintraege if e.get("slug")]
            if not eintraege or not (daten.get("links") or {}).get("next"):
                break
            time.sleep(0.5)
        return jobs


def job_aus_eintrag(e: dict) -> Job:
    typen = " ".join(e.get("job_types") or []).lower()
    ort = (e.get("location") or "").strip()
    remote = bool(e.get("remote")) or "remote" in ort.lower() or "homeoffice" in ort.lower()
    return Job(
        ref=f"arbeitnow:{e['slug']}",
        titel=(e.get("title") or "").strip(),
        arbeitgeber=(e.get("company_name") or "").strip(),
        quelle="arbeitnow",
        beruf=", ".join((e.get("tags") or [])[:5]) or None,
        ort=ort or None,
        veroeffentlicht=iso_datum(e.get("created_at")),
        externe_url=e.get("url"),
        detail_url=e.get("url"),
        beschreibung=ohne_html(e.get("description")),
        homeoffice=True if remote else None,
        vollzeit=True if "full" in typen else (False if "part" in typen else None),
        teilzeit=True if "part" in typen else None,
        befristung="unbefristet" if "permanent" in typen else None,
    )
