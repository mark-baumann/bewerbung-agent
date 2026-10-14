"""Remote-Jobboersen: Himalayas, Remotive, Remote OK, Jobicy, We Work Remotely.

Alle liefern frei abrufbare Feeds mit Volltext. Uebernommen werden nur Stellen,
die Bewerber aus Deutschland ansprechen (Deutschland, Europa, EMEA, weltweit).
Die Anbieter verlangen Quellenangabe und Rueckverlinkung - jede Stelle verlinkt
auf ihre Originalseite. Abruf hoechstens alle paar Stunden (Remotive: max. 4x/Tag).
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable

from ..models import Job
from .basis import REMOTE_FUER_DE, QuellenClient, iso_datum, ohne_html

log = logging.getLogger(__name__)

HIMALAYAS = "https://himalayas.app/jobs/api/search"
REMOTIVE = "https://remotive.com/api/remote-jobs"
REMOTEOK = "https://remoteok.com/api"
JOBICY = "https://jobicy.com/api/v2/remote-jobs"
WWR = "https://weworkremotely.com/remote-jobs.rss"


def _remote_job(ref: str, titel: str, firma: str, region: str, beschreibung: Any, url: str | None,
                datum: Any, plattform: str, typ: str = "") -> Job:
    typ = (typ or "").lower()
    return Job(
        ref=ref,
        titel=(titel or "").strip(),
        arbeitgeber=(firma or "").strip() or plattform,
        quelle="remote",
        ort=f"Remote ({region})" if region else "Remote",
        veroeffentlicht=iso_datum(datum),
        externe_url=url,
        detail_url=url,
        beschreibung=ohne_html(beschreibung),
        homeoffice=True,
        vollzeit=True if "full" in typ else (False if ("part" in typ or "contract" in typ) else None),
    )


def _fuer_de(region: str | None) -> bool:
    return bool(region and REMOTE_FUER_DE.search(region))


class RemoteClient(QuellenClient):
    name = "remote"

    def hole_feed(self) -> list[Job]:
        jobs: list[Job] = []
        anbieter: list[tuple[str, Callable[[], list[Job]]]] = [
            ("Himalayas", self._himalayas), ("Remotive", self._remotive), ("Remote OK", self._remoteok),
            ("Jobicy", self._jobicy), ("We Work Remotely", self._wwr),
        ]
        fehler = []
        for name, holen in anbieter:
            try:
                jobs += holen()
            except Exception as e:  # ein Anbieter darf die anderen nicht blockieren
                log.warning("Remote-Quelle %s fehlgeschlagen: %s", name, e)
                fehler.append(f"{name}: {e}")
            time.sleep(0.3)
        if not jobs and fehler:
            raise RuntimeError("; ".join(fehler))
        return jobs

    def _himalayas(self) -> list[Job]:
        jobs = []
        for seite in (1, 2, 3):
            daten = self._json(HIMALAYAS, params={"country": "DE", "page": seite})
            jobs += himalayas(daten)
            if len(daten.get("jobs") or []) < 20:
                break
        return jobs

    def _remotive(self) -> list[Job]:
        return remotive(self._json(REMOTIVE))

    def _remoteok(self) -> list[Job]:
        return remoteok(self._json(REMOTEOK))

    def _jobicy(self) -> list[Job]:
        jobs = jobicy(self._json(JOBICY, params={"count": 50, "geo": "germany"}))
        jobs += jobicy(self._json(JOBICY, params={"count": 50, "geo": "europe"}))
        return jobs

    def _wwr(self) -> list[Job]:
        return wwr(self._text(WWR))


def himalayas(daten: dict) -> list[Job]:
    jobs = []
    for j in daten.get("jobs") or []:
        laender = j.get("locationRestrictions") or []
        if laender and "Germany" not in laender:
            continue
        region = "Deutschland" if laender else "weltweit"
        guid = j.get("guid") or j.get("applicationLink") or ""
        jobs.append(_remote_job(
            f"himalayas:{guid.rstrip('/').rsplit('/', 1)[-1]}", j.get("title"), j.get("companyName"), region,
            j.get("description"), j.get("applicationLink"), j.get("pubDate"), "Himalayas", j.get("employmentType") or "",
        ))
    return jobs


def remotive(daten: dict) -> list[Job]:
    return [
        _remote_job(f"remotive:{j['id']}", j.get("title"), j.get("company_name"), j.get("candidate_required_location"),
                    j.get("description"), j.get("url"), j.get("publication_date"), "Remotive", j.get("job_type") or "")
        for j in daten.get("jobs") or []
        if j.get("id") and _fuer_de(j.get("candidate_required_location"))
    ]


def remoteok(daten: list) -> list[Job]:
    jobs = []
    for j in daten or []:
        if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
            continue  # erstes Element ist der Rechtshinweis
        region = (j.get("location") or "").strip()
        if region and not _fuer_de(region):
            continue
        jobs.append(_remote_job(f"remoteok:{j['id']}", j.get("position"), j.get("company"), region or "weltweit",
                                j.get("description"), j.get("url"), j.get("date"), "Remote OK"))
    return jobs


def jobicy(daten: dict) -> list[Job]:
    return [
        _remote_job(f"jobicy:{j['id']}", j.get("jobTitle"), j.get("companyName"), j.get("jobGeo"),
                    j.get("jobDescription"), j.get("url"), j.get("pubDate"), "Jobicy", " ".join(j.get("jobType") or []))
        for j in daten.get("jobs") or []
        if j.get("id") and _fuer_de(j.get("jobGeo"))
    ]


def wwr(xml_text: str) -> list[Job]:
    jobs = []
    for item in ET.fromstring(xml_text).iter("item"):
        titel = item.findtext("title") or ""
        firma, _, rolle = titel.partition(": ")
        region = item.findtext("region") or ""
        if not _fuer_de(region):
            continue
        link = item.findtext("link") or item.findtext("guid") or ""
        jobs.append(_remote_job(f"wwr:{link.rstrip('/').rsplit('/', 1)[-1]}", rolle or titel, firma if rolle else "",
                                region, item.findtext("description"), link, item.findtext("pubDate"),
                                "We Work Remotely", item.findtext("type") or ""))
    return jobs
