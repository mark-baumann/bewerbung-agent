"""Lesender Adapter fuer die oeffentlichen Stellenlisten von GET IN IT.

GET IN IT stellt keine stabile, oeffentliche Partner-API bereit. Deshalb wird
die oeffentlich auslieferte JSON-LD-Auszeichnung der Suchseite verwendet statt
HTML-Klassen zu scrapen. Das ist bewusst nur eine Suche: Bewerbungen bleiben
auf der jeweiligen Arbeitgeberseite und werden niemals automatisiert gestartet.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlencode

import httpx

from ..models import Job

SUCH_URL = "https://www.get-in-it.de/jobs"


class GetInItClient:
    def __init__(self, timeout: float = 30.0, transport=None):
        self.http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "bewerbungsagent/0.1"},
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "GetInItClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def hole_jobs(self, *, was: str, wo: str = "", umkreis: int = 25, max_treffer: int = 50, **_ignored: Any) -> list[Job]:
        """Sucht mit Profilort; der Radius wird vom Anbieter selbst bestimmt."""
        params = {"search": was}
        if wo.strip():
            params["location"] = wo.strip()
        response = self.http.get(f"{SUCH_URL}?{urlencode(params)}")
        response.raise_for_status()
        return _jobs_aus_html(response.text, max_treffer=max_treffer)


def _jobs_aus_html(html: str, max_treffer: int = 50) -> list[Job]:
    jobs: list[Job] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            daten = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        for posting in _postings(daten):
            job = _job_aus_posting(posting)
            if job:
                jobs.append(job)
                if len(jobs) >= max(1, max_treffer):
                    return jobs
    return jobs


def _postings(value: Any):
    if isinstance(value, dict):
        typ = value.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            yield value
        for child in value.values():
            yield from _postings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _postings(child)


def _job_aus_posting(daten: dict[str, Any]) -> Job | None:
    titel = str(daten.get("title") or "").strip()
    url = str(daten.get("url") or "").strip()
    if not titel or not url:
        return None
    adresse = daten.get("jobLocation", {}).get("address", {}) if isinstance(daten.get("jobLocation"), dict) else {}
    firma = daten.get("hiringOrganization", {}) if isinstance(daten.get("hiringOrganization"), dict) else {}
    ref = "get-in-it:" + hashlib.sha256(url.encode()).hexdigest()[:20]
    return Job(
        ref=ref,
        titel=titel,
        arbeitgeber=str(firma.get("name") or "GET IN IT").strip(),
        quelle="get-in-it",
        ort=_text(adresse.get("addressLocality")),
        plz=_text(adresse.get("postalCode")),
        region=_text(adresse.get("addressRegion")),
        veroeffentlicht=_text(daten.get("datePosted")),
        externe_url=url,
        detail_url=url,
        beschreibung=_text(daten.get("description")),
        vollzeit=daten.get("employmentType") == "FULL_TIME",
    )


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()
