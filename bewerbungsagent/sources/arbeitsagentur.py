"""Client fuer die oeffentliche Jobsuche-API der Bundesagentur fuer Arbeit.

Endpunkte (oeffentlich, fester API-Key der Jobboerse-Webanwendung):
  GET /pc/v4/jobs                  Suche
  GET /pc/v3/jobdetails/{base64}   Detail inkl. Volltext-Beschreibung
"""

from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any, Iterator

import httpx

from ..models import Job

BASIS = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
API_KEY = "jobboerse-jobsuche"  # oeffentlicher Key der Jobboerse-Webanwendung
JOBDETAIL_WEB = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"

log = logging.getLogger(__name__)


class ArbeitsagenturClient:
    def __init__(self, timeout: float = 30.0, pause: float = 0.3):
        self.pause = pause
        self.http = httpx.Client(
            timeout=timeout,
            headers={
                "X-API-Key": API_KEY,
                "Accept": "application/json",
                "User-Agent": "bewerbungsagent/0.1 (+persoenliche Jobsuche)",
            },
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "ArbeitsagenturClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------- Suche ----------------

    def suche(
        self,
        was: str,
        wo: str = "",
        umkreis: int = 25,
        veroeffentlicht_seit_tagen: int = 30,
        nur_vollzeit: bool = False,
        max_treffer: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Blaettert durch die Trefferliste und liefert rohe Angebots-Dicts."""
        seite, geliefert = 1, 0
        # API akzeptiert 0/7/14/30/100 Tage
        seit = min([0, 7, 14, 30, 100], key=lambda t: abs(t - veroeffentlicht_seit_tagen))
        while geliefert < max_treffer:
            params: dict[str, Any] = {
                "was": was,
                "page": seite,
                "size": min(100, max_treffer - geliefert),
                "veroeffentlichtseit": seit,
                "angebotsart": 1,  # 1 = Arbeit (keine Ausbildung/Praktikum)
            }
            if wo:
                params["wo"] = wo
                params["umkreis"] = umkreis
            if nur_vollzeit:
                params["arbeitszeit"] = "vz"

            r = self.http.get(f"{BASIS}/v4/jobs", params=params)
            if r.status_code == 404:  # keine Treffer
                return
            r.raise_for_status()
            angebote = r.json().get("stellenangebote") or []
            if not angebote:
                return
            for a in angebote:
                yield a
                geliefert += 1
                if geliefert >= max_treffer:
                    return
            seite += 1
            time.sleep(self.pause)

    # ---------------- Detail ----------------

    def detail(self, refnr: str) -> dict[str, Any] | None:
        kodiert = base64.b64encode(refnr.encode()).decode()
        r = self.http.get(f"{BASIS}/v3/jobdetails/{kodiert}")
        if r.status_code in (404, 410):
            return None
        r.raise_for_status()
        return r.json()

    # ---------------- Normalisierung ----------------

    def hole_jobs(
        self,
        was: str,
        wo: str = "",
        umkreis: int = 25,
        veroeffentlicht_seit_tagen: int = 30,
        nur_vollzeit: bool = False,
        max_treffer: int = 50,
        mit_details: bool = True,
    ) -> list[Job]:
        jobs: list[Job] = []
        for roh in self.suche(
            was=was,
            wo=wo,
            umkreis=umkreis,
            veroeffentlicht_seit_tagen=veroeffentlicht_seit_tagen,
            nur_vollzeit=nur_vollzeit,
            max_treffer=max_treffer,
        ):
            job = _job_aus_treffer(roh)
            if mit_details:
                try:
                    d = self.detail(job.ref)
                except httpx.HTTPError as e:  # Detail ist nice-to-have
                    log.warning("Detail fuer %s nicht abrufbar: %s", job.ref, e)
                    d = None
                if d:
                    _detail_anreichern(job, d)
                time.sleep(self.pause)
            jobs.append(job)
        return jobs


def _job_aus_treffer(roh: dict[str, Any]) -> Job:
    ort = roh.get("arbeitsort") or {}
    ref = roh.get("refnr", "")
    entfernung = ort.get("entfernung")
    return Job(
        ref=ref,
        titel=(roh.get("titel") or roh.get("beruf") or "").strip(),
        arbeitgeber=(roh.get("arbeitgeber") or "").strip(),
        beruf=roh.get("beruf"),
        ort=_saeubern(ort.get("ort")),
        plz=_saeubern(ort.get("plz")),
        region=_saeubern(ort.get("region")),
        entfernung_km=float(entfernung) if entfernung not in (None, "") else None,
        veroeffentlicht=roh.get("aktuelleVeroeffentlichungsdatum"),
        eintrittsdatum=roh.get("eintrittsdatum"),
        externe_url=_saeubern(roh.get("externeUrl")),
        detail_url=f"{JOBDETAIL_WEB}{ref}" if ref else None,
    )


def _detail_anreichern(job: Job, d: dict[str, Any]) -> None:
    job.beschreibung = _text_normalisieren(d.get("stellenangebotsBeschreibung"))
    job.titel = job.titel or (d.get("stellenangebotsTitel") or "")
    job.arbeitgeber = job.arbeitgeber or (d.get("firma") or "")
    job.homeoffice = d.get("homeofficemoeglich")
    job.vollzeit = d.get("arbeitszeitVollzeit")
    job.teilzeit = any(
        d.get(k)
        for k in (
            "arbeitszeitTeilzeitFlexibel",
            "arbeitszeitTeilzeitVormittag",
            "arbeitszeitTeilzeitNachmittag",
            "arbeitszeitTeilzeitAbend",
        )
    )
    dauer = d.get("vertragsdauer")
    if dauer == "BEFRISTET":
        monate = d.get("befristungInMonaten")
        job.befristung = f"befristet ({monate} Monate)" if monate else "befristet"
    elif dauer:
        job.befristung = "unbefristet"
    verg = d.get("verguetungsangabe")
    job.verguetung = None if verg in (None, "KEINE_ANGABEN") else str(verg)

    lokationen = d.get("stellenlokationen") or []
    if lokationen and not job.ort:
        adr = (lokationen[0] or {}).get("adresse") or {}
        job.ort = _saeubern(adr.get("ort"))
        job.plz = _saeubern(adr.get("plz"))


def _saeubern(wert: Any) -> str | None:
    """Die API liefert an einigen Stellen den String 'null'."""
    if wert in (None, "", "null"):
        return None
    return str(wert).strip()


def _text_normalisieren(text: str | None) -> str | None:
    if not text:
        return None
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
