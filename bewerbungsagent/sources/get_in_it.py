"""Lesender Adapter fuer die oeffentlichen Stellenlisten von GET IN IT.

GET IN IT stellt keine dokumentierte Partner-API bereit. Die Jobsuche der
Website (``/jobsuche``) laedt ihre Treffer aber ueber eine offene JSON-API
(``/api/v2/open/job/search``); dieselbe wird hier lesend verwendet. Die API
kennt keine Freitextsuche, nur Filter (Ort per Koordinaten + Radius, Fachgebiet
usw.). Der Suchbegriff wird deshalb clientseitig gegen Titel und Fachgebiet
abgeglichen. Details (Beschreibung, Datum, Anstellungsart) stehen als JSON-LD
auf der jeweiligen Stellenseite.

Das ist bewusst nur eine Suche: Bewerbungen bleiben auf der jeweiligen
Arbeitgeberseite und werden niemals automatisiert gestartet.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..models import Job

log = logging.getLogger(__name__)

BASIS_URL = "https://www.get-in-it.de"
API_URL = f"{BASIS_URL}/api/v2/open"
SEITEN_GROESSE = 1000
MAX_SEITEN = 10


class GetInItClient:
    def __init__(self, timeout: float = 30.0, transport=None):
        self.http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": "bewerbungsagent/0.1"},
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "GetInItClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def hole_jobs(
        self,
        *,
        was: str,
        wo: str = "",
        umkreis: int = 25,
        veroeffentlicht_seit_tagen: Any = None,
        nur_vollzeit: bool = False,
        max_treffer: int = 50,
        mit_details: bool = True,
        **_ignored: Any,
    ) -> list[Job]:
        filter_params: dict[str, Any] = {}
        if wo.strip():
            koordinaten = self._koordinaten(wo)
            if koordinaten:
                lat, lon = koordinaten
                filter_params = {
                    "filter[city][city][0][lat]": lat,
                    "filter[city][city][0][lon]": lon,
                    "filter[city][radius]": int(umkreis),
                }
            else:
                log.warning("GET IN IT kennt den Ort %r nicht; suche ohne Ortsfilter.", wo)

        treffer = _passende_treffer(self._alle_treffer(filter_params), was)
        jobs = [_job_aus_treffer(t) for t in treffer]
        if not mit_details:
            return jobs[: max(1, max_treffer)]

        ergebnis: list[Job] = []
        grenze = _grenzdatum(veroeffentlicht_seit_tagen)
        for job in jobs:
            try:
                self._details_anreichern(job)
            except httpx.HTTPError as exc:
                log.warning("GET IN IT Detailseite %s nicht abrufbar: %s", job.detail_url, exc)
            if nur_vollzeit and job.vollzeit is False:
                continue
            if grenze and job.veroeffentlicht and job.veroeffentlicht[:10] < grenze:
                continue
            ergebnis.append(job)
            if len(ergebnis) >= max(1, max_treffer):
                break
        return ergebnis

    def _api(self, pfad: str, params: dict[str, Any]) -> Any:
        response = self.http.get(
            f"{API_URL}{pfad}", params=params,
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return response.json()

    def _alle_treffer(self, filter_params: dict[str, Any]) -> list[dict[str, Any]]:
        treffer: list[dict[str, Any]] = []
        for seite in range(MAX_SEITEN):
            daten = self._api("/job/search", {**filter_params, "start": seite * SEITEN_GROESSE, "limit": SEITEN_GROESSE})
            ergebnisse = (daten.get("items") or {}).get("results") or []
            treffer.extend(ergebnisse)
            if not ergebnisse or len(treffer) >= int(daten.get("total") or 0):
                break
        return treffer

    def _koordinaten(self, wo: str) -> tuple[float, float] | None:
        """Loest einen Ortsnamen ueber die Orte der vorhandenen Stellen auf.

        Die Orts-API nimmt nur IDs entgegen; die ID steht an jedem Treffer.
        """
        name = _ortsname(wo)
        if not name:
            return None
        for t in self._alle_treffer({}):
            for ort in t.get("locations") or []:
                if _ortsname(str(ort.get("name") or "")) == name:
                    staedte = self._api("/city", {"filter[id]": ort["id"]})
                    if staedte:
                        return float(staedte[0]["latitude"]), float(staedte[0]["longitude"])
        return None

    def _details_anreichern(self, job: Job) -> None:
        response = self.http.get(job.detail_url, headers={"Accept": "text/html,application/xhtml+xml"})
        response.raise_for_status()
        details = _jobs_aus_html(response.text, max_treffer=1)
        if not details:
            return
        d = details[0]
        job.beschreibung = d.beschreibung or job.beschreibung
        job.veroeffentlicht = d.veroeffentlicht or job.veroeffentlicht
        job.vollzeit = d.vollzeit
        job.plz = d.plz or job.plz
        job.region = d.region or job.region


def _ortsname(wert: str) -> str:
    """'80331 München, Bayern' -> 'münchen'."""
    wert = wert.split(",")[0]
    wert = re.sub(r"\d+", " ", wert)
    return re.sub(r"\s+", " ", wert).strip().casefold()


def _suchworte(was: str) -> list[str]:
    return [w.casefold() for w in re.findall(r"[\w#+.-]+", was) if len(w) >= 2]


def _wort_passt(wort: str, text: str) -> bool:
    if wort in text:
        return True
    # Komposita: "Softwareentwickler" soll auch "Software Engineer" finden.
    return len(wort) >= 10 and wort[:6] in text


def _passende_treffer(treffer: list[dict[str, Any]], was: str) -> list[dict[str, Any]]:
    """Sortiert nach Anzahl passender Suchworte; ohne Treffer fliegt raus."""
    worte = _suchworte(was)
    if not worte:
        return treffer
    bewertet = []
    for t in treffer:
        text = " ".join([str(t.get("title") or "")] + [str(c.get("name") or "") for c in t.get("careers") or []]).casefold()
        punkte = sum(_wort_passt(w, text) for w in worte)
        if punkte:
            bewertet.append((punkte, t))
    bewertet.sort(key=lambda x: -x[0])
    return [t for _, t in bewertet]


def _job_aus_treffer(t: dict[str, Any]) -> Job:
    url = BASIS_URL + str(t.get("url") or f"/jobsuche/p{t.get('id')}")
    orte = [str(o.get("name")) for o in t.get("locations") or [] if o.get("name")]
    return Job(
        ref=_ref(url),
        titel=str(t.get("title") or "").strip(),
        arbeitgeber=str((t.get("company") or {}).get("title") or "GET IN IT").strip(),
        quelle="get-in-it",
        beruf=", ".join(str(c.get("name")) for c in t.get("careers") or [] if c.get("name")) or None,
        ort=", ".join(orte) or None,
        externe_url=url,
        detail_url=url,
        homeoffice=t.get("homeOffice") if isinstance(t.get("homeOffice"), bool) else None,
    )


def _ref(url: str) -> str:
    return "get-in-it:" + hashlib.sha256(url.encode()).hexdigest()[:20]


def _grenzdatum(tage: Any) -> str | None:
    try:
        tage = int(tage)
    except (TypeError, ValueError):
        return None
    if tage <= 0:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=tage)).date().isoformat()


def _jobs_aus_html(seite: str, max_treffer: int = 50) -> list[Job]:
    jobs: list[Job] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', seite, re.I | re.S):
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
    ort = daten.get("jobLocation")
    if isinstance(ort, list):
        ort = ort[0] if ort else {}
    adressen = ort.get("address", {}) if isinstance(ort, dict) else {}
    if isinstance(adressen, dict):
        adressen = [adressen]
    adressen = [a for a in adressen if isinstance(a, dict)]
    adresse = adressen[0] if adressen else {}
    orte = [_text(a.get("addressLocality")) for a in adressen]
    firma = daten.get("hiringOrganization", {}) if isinstance(daten.get("hiringOrganization"), dict) else {}
    return Job(
        ref=_ref(url),
        titel=titel,
        arbeitgeber=str(firma.get("name") or "GET IN IT").strip(),
        quelle="get-in-it",
        ort=", ".join(o for o in orte if o) or None,
        plz=_text(adresse.get("postalCode")),
        region=_text(adresse.get("addressRegion")),
        veroeffentlicht=_text(daten.get("datePosted")),
        externe_url=url,
        detail_url=url,
        beschreibung=_text(_ohne_html(daten.get("description"))),
        vollzeit=_vollzeit(daten.get("employmentType")),
    )


def _vollzeit(value: Any) -> bool | None:
    arten = value if isinstance(value, list) else [value]
    arten = [a for a in arten if a]
    return "FULL_TIME" in arten if arten else None


def _ohne_html(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()
