"""Wunsch-Arbeitgeber: Stellen direkt aus deren Bewerbermanagementsystem (ATS).

Unterstuetzt die oeffentlichen, fuer Karriereseiten gedachten Stellen-Schnittstellen
von Greenhouse, Lever, SmartRecruiters, Personio, Recruitee und Ashby. Sie liefern
Volltext und den direkten Link zum Bewerbungsformular des Arbeitgebers.
Bewerbungen laufen NICHT ueber deren Apply-APIs (die brauchen Schluessel des
Arbeitgebers), sondern ueber das gehostete Formular per Browser-Agent.

`erkenne_ats(url)` leitet aus einer Karriereseiten-URL (oder deren HTML) ab,
welches System ein Arbeitgeber nutzt.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..models import Job
from .basis import QuellenClient, http_client, iso_datum, ohne_html

ATS_NAMEN = {
    "greenhouse": "Greenhouse", "lever": "Lever", "smartrecruiters": "SmartRecruiters",
    "personio": "Personio", "recruitee": "Recruitee", "ashby": "Ashby",
}

_MUSTER = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ("greenhouse", r"greenhouse\.io/embed/job_(?:board|app)\?for=([A-Za-z0-9_-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),
]
_IGNORIEREN = {"embed", "api", "jobs", "careers", "www", "static", "assets", "app"}


def ats_aus_text(text: str) -> dict[str, str] | None:
    for ats, muster in _MUSTER:
        for m in re.finditer(muster, text, re.IGNORECASE):
            kennung = m.group(1)
            if kennung.lower() not in _IGNORIEREN:
                return {"ats": ats, "kennung": kennung}
    return None


def erkenne_ats(url: str, http: httpx.Client | None = None) -> dict[str, str] | None:
    """ATS + Kennung aus URL oder - falls noetig - aus dem HTML der Karriereseite."""
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    treffer = ats_aus_text(url)
    if treffer:
        return treffer
    eigener = http is None
    http = http or http_client(timeout=20.0)
    try:
        antwort = http.get(url)
        treffer = ats_aus_text(str(antwort.url)) or ats_aus_text(antwort.text)
    except httpx.HTTPError:
        treffer = None
    finally:
        if eigener:
            http.close()
    return treffer


class WunschArbeitgeberClient(QuellenClient):
    name = "wunscharbeitgeber"

    def hole_arbeitgeber(self, firma: dict[str, str]) -> list[Job]:
        ats, kennung = (firma.get("ats") or "").lower(), (firma.get("kennung") or "").strip()
        if not ats or not kennung:
            erkannt = erkenne_ats(firma.get("url") or "", self.http)
            if not erkannt:
                raise ValueError(f"Bewerbermanagement von {firma.get('name') or firma.get('url')} nicht erkannt")
            ats, kennung = erkannt["ats"], erkannt["kennung"]
        holer = {
            "greenhouse": self._greenhouse, "lever": self._lever, "smartrecruiters": self._smartrecruiters,
            "personio": self._personio, "recruitee": self._recruitee, "ashby": self._ashby,
        }.get(ats)
        if holer is None:
            raise ValueError(f"Unbekanntes Bewerbermanagement: {ats}")
        jobs = holer(kennung)
        for job in jobs:
            if firma.get("name") and job.arbeitgeber in ("", kennung):
                job.arbeitgeber = firma["name"]
        return jobs

    def _greenhouse(self, token: str) -> list[Job]:
        return greenhouse(self._json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                                     params={"content": "true"}), token)

    def _lever(self, site: str) -> list[Job]:
        try:
            daten = self._json(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"})
        except httpx.HTTPStatusError:
            daten = self._json(f"https://api.eu.lever.co/v0/postings/{site}", params={"mode": "json"})
        return lever(daten, site)

    def _smartrecruiters(self, firma: str) -> list[Job]:
        jobs, offset = [], 0
        while offset < 300:
            daten = self._json(f"https://api.smartrecruiters.com/v1/companies/{firma}/postings",
                               params={"limit": 100, "offset": offset, "country": "de"})
            jobs += smartrecruiters(daten, firma)
            offset += 100
            if offset >= int(daten.get("totalFound") or 0):
                break
        return jobs

    def _personio(self, slug: str) -> list[Job]:
        return personio(self._text(f"https://{slug}.jobs.personio.de/xml", params={"language": "de"}), slug)

    def _recruitee(self, sub: str) -> list[Job]:
        return recruitee(self._json(f"https://{sub}.recruitee.com/api/offers/"), sub)

    def _ashby(self, name: str) -> list[Job]:
        return ashby(self._json(f"https://api.ashbyhq.com/posting-api/job-board/{name}"), name)

    def anreichern(self, job: Job) -> None:
        """Volltext nachladen, wo die Liste keinen liefert (SmartRecruiters, leere Personio-Texte)."""
        if job.ref.startswith("smartrecruiters:") and not job.beschreibung:
            _, firma, kennung = job.ref.split(":", 2)
            d = self._json(f"https://api.smartrecruiters.com/v1/companies/{firma}/postings/{kennung}")
            abschnitte = ((d.get("jobAd") or {}).get("sections") or {})
            texte = [ohne_html((abschnitte.get(k) or {}).get("text")) for k in
                     ("companyDescription", "jobDescription", "qualifications", "additionalInformation")]
            job.beschreibung = "\n\n".join(t for t in texte if t) or job.beschreibung
            job.externe_url = d.get("applyUrl") or d.get("postingUrl") or job.externe_url
        elif job.ref.startswith("personio:") and not job.beschreibung and job.detail_url:
            seite = self._text(job.detail_url)
            for roh in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', seite, re.S | re.I):
                try:
                    daten = json.loads(roh.strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(daten, dict) and daten.get("description"):
                    job.beschreibung = ohne_html(daten["description"])
                    break


def greenhouse(daten: dict, token: str) -> list[Job]:
    jobs = []
    for j in daten.get("jobs") or []:
        ort = (j.get("location") or {}).get("name") or ""
        email = None
        for m in j.get("metadata") or []:
            wert = m.get("value")
            if isinstance(wert, dict) and wert.get("email") and "recruit" in (m.get("name") or "").lower():
                email = wert["email"].lower()
        jobs.append(Job(
            ref=f"greenhouse:{token}:{j['id']}",
            titel=(j.get("title") or "").strip(),
            arbeitgeber=(j.get("company_name") or "").strip(),
            quelle="wunscharbeitgeber",
            beruf=", ".join(d.get("name") for d in j.get("departments") or [] if d.get("name")) or None,
            ort=ort or None,
            veroeffentlicht=iso_datum(j.get("first_published") or j.get("updated_at")),
            externe_url=j.get("absolute_url"),
            detail_url=j.get("absolute_url"),
            beschreibung=ohne_html(j.get("content")),
            homeoffice=True if "remote" in ort.lower() else None,
            kontakt_email=email,
        ))
    return jobs


def lever(daten: list, site: str) -> list[Job]:
    jobs = []
    for j in daten or []:
        kat = j.get("categories") or {}
        listen = "\n\n".join(
            f"{l.get('text', '')}\n{ohne_html(l.get('content')) or ''}" for l in j.get("lists") or []
        )
        typ = (kat.get("commitment") or "").lower()
        jobs.append(Job(
            ref=f"lever:{site}:{j['id']}",
            titel=(j.get("text") or "").strip(),
            arbeitgeber=site,
            quelle="wunscharbeitgeber",
            beruf=kat.get("team") or kat.get("department"),
            ort=", ".join(kat.get("allLocations") or [kat.get("location") or ""]) or None,
            region=j.get("country"),
            veroeffentlicht=iso_datum(j.get("createdAt")),
            externe_url=j.get("applyUrl") or j.get("hostedUrl"),
            detail_url=j.get("hostedUrl"),
            beschreibung="\n\n".join(x for x in (j.get("descriptionPlain"), listen, j.get("additionalPlain")) if x) or None,
            homeoffice=True if j.get("workplaceType") == "remote" else None,
            vollzeit=True if "full" in typ else (False if "part" in typ else None),
        ))
    return jobs


def smartrecruiters(daten: dict, firma: str) -> list[Job]:
    jobs = []
    for j in daten.get("content") or []:
        ort = j.get("location") or {}
        typ = ((j.get("typeOfEmployment") or {}).get("id") or "").lower()
        jobs.append(Job(
            ref=f"smartrecruiters:{firma}:{j['id']}",
            titel=(j.get("name") or "").strip(),
            arbeitgeber=((j.get("company") or {}).get("name") or firma).strip(),
            quelle="wunscharbeitgeber",
            beruf=(j.get("function") or {}).get("label"),
            ort=ort.get("city"),
            plz=ort.get("postalCode"),
            region=ort.get("region"),
            strasse=ort.get("address"),
            veroeffentlicht=iso_datum(j.get("releasedDate")),
            externe_url=f"https://jobs.smartrecruiters.com/{firma}/{j['id']}",
            detail_url=f"https://jobs.smartrecruiters.com/{firma}/{j['id']}",
            homeoffice=True if ort.get("remote") else None,
            vollzeit=True if "full" in typ else (False if "part" in typ else None),
        ))
    return jobs


def personio(xml_text: str, slug: str) -> list[Job]:
    jobs = []
    for p in ET.fromstring(xml_text).iter("position"):
        kennung = (p.findtext("id") or "").strip()
        if not kennung:
            continue
        texte = [f"{(d.findtext('name') or '').strip()}\n{ohne_html(d.findtext('value')) or ''}".strip()
                 for d in p.iter("jobDescription")]
        orte = [p.findtext("office") or ""] + [o.text or "" for o in p.iter("office")][1:]
        schedule = (p.findtext("schedule") or "").lower()
        gehalt = p.find("salaryInformation")
        verguetung = None
        if gehalt is not None and gehalt.findtext("min"):
            verguetung = f"{float(gehalt.findtext('min')):,.0f} – {float(gehalt.findtext('max') or 0):,.0f} " \
                         f"{gehalt.findtext('currencyCode') or 'EUR'}".replace(",", ".")
        url = f"https://{slug}.jobs.personio.de/job/{kennung}"
        jobs.append(Job(
            ref=f"personio:{slug}:{kennung}",
            titel=(p.findtext("name") or "").strip(),
            arbeitgeber=(p.findtext("subcompany") or slug).strip(),
            quelle="wunscharbeitgeber",
            beruf=p.findtext("department"),
            ort=", ".join(dict.fromkeys(o.strip() for o in orte if o.strip())) or None,
            veroeffentlicht=iso_datum(p.findtext("createdAt")),
            externe_url=url,
            detail_url=url,
            beschreibung="\n\n".join(t for t in texte if t) or None,
            vollzeit=True if "full" in schedule else (False if "part" in schedule else None),
            befristung="unbefristet" if (p.findtext("employmentType") or "") == "permanent" else None,
            verguetung=verguetung,
        ))
    return jobs


def recruitee(daten: dict, sub: str) -> list[Job]:
    jobs = []
    for o in daten.get("offers") or []:
        typ = (o.get("employment_type_code") or "").lower()
        jobs.append(Job(
            ref=f"recruitee:{sub}:{o['id']}",
            titel=(o.get("title") or "").strip(),
            arbeitgeber=(o.get("company_name") or sub).strip(),
            quelle="wunscharbeitgeber",
            beruf=o.get("department"),
            ort=", ".join(x for x in (o.get("city"), o.get("country_code")) if x) or None,
            plz=o.get("postal_code"),
            veroeffentlicht=iso_datum(o.get("published_at")),
            externe_url=o.get("careers_apply_url") or o.get("careers_url"),
            detail_url=o.get("careers_url"),
            beschreibung="\n\n".join(x for x in (ohne_html(o.get("description")), ohne_html(o.get("requirements"))) if x)
            or None,
            homeoffice=True if o.get("remote") else None,
            vollzeit=True if "fulltime" in typ else (False if "parttime" in typ else None),
        ))
    return jobs


def ashby(daten: dict, name: str) -> list[Job]:
    jobs = []
    for j in daten.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        adresse = ((j.get("address") or {}).get("postalAddress") or {})
        orte = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        typ = (j.get("employmentType") or "").lower()
        jobs.append(Job(
            ref=f"ashby:{name}:{j['id']}",
            titel=(j.get("title") or "").strip(),
            arbeitgeber=name,
            quelle="wunscharbeitgeber",
            beruf=j.get("department") or j.get("team"),
            ort=", ".join(dict.fromkeys(o for o in orte if o)) or None,
            region=adresse.get("addressCountry"),
            veroeffentlicht=iso_datum(j.get("publishedAt")),
            externe_url=j.get("applyUrl") or j.get("jobUrl"),
            detail_url=j.get("jobUrl"),
            beschreibung=j.get("descriptionPlain") or ohne_html(j.get("descriptionHtml")),
            homeoffice=True if j.get("isRemote") or j.get("workplaceType") == "Remote" else None,
            vollzeit=True if "fulltime" in typ else (False if "parttime" in typ else None),
        ))
    return jobs


def kurzbeschreibung(firma: dict[str, Any]) -> str:
    ats = ATS_NAMEN.get((firma.get("ats") or "").lower(), firma.get("ats") or "?")
    return f"{firma.get('name') or firma.get('kennung')} ({ats}: {firma.get('kennung')})"
