"""Suchende Aggregatoren: EURES (EU-Portal), Adzuna und Jooble (beide mit kostenlosem Key).

- EURES: Stellen im europaeischen Ausland (z. B. Oesterreich, Schweiz). Deutsche
  EURES-Stellen stammen fast alle von der Arbeitsagentur und werden uebersprungen.
- Adzuna/Jooble: nur aktiv, wenn der Schluessel in der UI (oder .env) hinterlegt ist.
  Beide liefern nur Ausschnitte der Anzeige; den Volltext erhaelt die Bewertung
  erst ueber die verlinkte Originalseite.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from ..models import Job
from .basis import QuellenClient, QuelleFehler, iso_datum, ohne_html

EURES_SUCHE = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
EURES_WEB = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=de"
ADZUNA = "https://api.adzuna.com/v1/api/jobs/de/search/{seite}"
JOOBLE = "https://jooble.org/api/{key}"


class EuresClient(QuellenClient):
    name = "eures"

    def hole_jobs(self, *, was: str, max_treffer: int = 50, tage: int | None = None, **_ignoriert: Any) -> list[Job]:
        laender = [l.lower() for l in (getattr(getattr(self.profil, "suche", None), "ausland", None) or [])]
        if not laender:
            return []
        anfrage = {
            "resultsPerPage": min(50, max(1, int(max_treffer))), "page": 1, "sortSearch": "MOST_RECENT",
            "keywords": [{"keyword": was, "specificSearchCode": "EVERYWHERE"}],
            "publicationPeriod": None, "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [],
            "positionScheduleCodes": [], "sectorCodes": [], "educationAndQualificationLevelCodes": [],
            "positionOfferingCodes": [], "locationCodes": laender, "euresFlagCodes": [], "otherBenefitsCodes": [],
            "requiredLanguages": [], "minNumberPost": None, "sessionId": "bewerbungsagent",
        }
        antwort = self.http.post(EURES_SUCHE, json=anfrage)
        antwort.raise_for_status()
        return eures(antwort.json())


def _eures_herkunft(kennung: str) -> str:
    try:
        return base64.b64decode(kennung + "=" * (-len(kennung) % 4)).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def eures(daten: dict) -> list[Job]:
    jobs = []
    for j in daten.get("jvs") or []:
        kennung = j.get("id") or ""
        herkunft = _eures_herkunft(kennung)
        if herkunft.startswith("10001-") or herkunft.startswith("10000-"):
            continue  # Stelle der Arbeitsagentur - kommt schon ueber deren Suche
        orte = j.get("locationMap") or {}
        laender = ", ".join(sorted(orte)) or None
        jobs.append(Job(
            ref=f"eures:{kennung}",
            titel=(j.get("title") or "").strip(),
            arbeitgeber=((j.get("employer") or {}).get("name") or "").strip(),
            quelle="eures",
            ort=laender,
            veroeffentlicht=iso_datum(j.get("creationDate")),
            externe_url=EURES_WEB.format(id=kennung),
            detail_url=EURES_WEB.format(id=kennung),
            beschreibung=ohne_html(j.get("description")),
            vollzeit=True if "fulltime" in (j.get("positionScheduleCodes") or []) else None,
        ))
    return jobs


class AdzunaClient(QuellenClient):
    name = "adzuna"

    def hole_jobs(self, *, was: str, wo: str = "", umkreis: int = 25, veroeffentlicht_seit_tagen: Any = 30,
                  nur_vollzeit: bool = False, max_treffer: int = 50, **_ignoriert: Any) -> list[Job]:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if not (app_id and app_key):
            raise QuelleFehler("Adzuna: app_id/app_key fehlen (Profil → Zugänge)")
        params: dict[str, Any] = {
            "app_id": app_id, "app_key": app_key, "what": was, "results_per_page": min(50, int(max_treffer)),
            "max_days_old": int(veroeffentlicht_seit_tagen or 30), "content-type": "application/json",
        }
        if wo:
            params.update(where=wo, distance=int(umkreis or 25))
        if nur_vollzeit:
            params["full_time"] = 1
        return adzuna(self._json(ADZUNA.format(seite=1), params=params))


def adzuna(daten: dict) -> list[Job]:
    jobs = []
    for j in daten.get("results") or []:
        if not j.get("id"):
            continue
        ort = (j.get("location") or {}).get("display_name")
        zeit = j.get("contract_time") or ""
        von, bis = j.get("salary_min"), j.get("salary_max")
        jobs.append(Job(
            ref=f"adzuna:{j['id']}",
            titel=ohne_html(j.get("title")) or "",
            arbeitgeber=((j.get("company") or {}).get("display_name") or "").strip(),
            quelle="adzuna",
            beruf=(j.get("category") or {}).get("label"),
            ort=ort,
            veroeffentlicht=iso_datum(j.get("created")),
            externe_url=j.get("redirect_url"),
            detail_url=j.get("redirect_url"),
            beschreibung=ohne_html(j.get("description")),
            vollzeit=True if zeit == "full_time" else (False if zeit == "part_time" else None),
            befristung="unbefristet" if j.get("contract_type") == "permanent" else None,
            verguetung=(f"{von:,.0f} – {bis:,.0f} EUR".replace(",", ".")
                        if von and bis and not j.get("salary_is_predicted") else None),
        ))
    return jobs


class JoobleClient(QuellenClient):
    name = "jooble"

    def hole_jobs(self, *, was: str, wo: str = "", umkreis: int = 25, max_treffer: int = 50,
                  **_ignoriert: Any) -> list[Job]:
        key = os.environ.get("JOOBLE_API_KEY")
        if not key:
            raise QuelleFehler("Jooble: API-Key fehlt (Profil → Zugänge)")
        anfrage = {"keywords": was, "location": wo, "radius": str(int(umkreis or 25)), "page": "1",
                   "ResultOnPage": str(min(50, int(max_treffer)))}
        antwort = self.http.post(JOOBLE.format(key=key), json=anfrage)
        antwort.raise_for_status()
        return jooble(antwort.json())


def jooble(daten: dict) -> list[Job]:
    jobs = []
    for j in daten.get("jobs") or []:
        if not j.get("id"):
            continue
        typ = (j.get("type") or "").lower()
        jobs.append(Job(
            ref=f"jooble:{j['id']}",
            titel=ohne_html(j.get("title")) or "",
            arbeitgeber=(j.get("company") or "").strip(),
            quelle="jooble",
            ort=j.get("location"),
            veroeffentlicht=iso_datum(j.get("updated")),
            externe_url=j.get("link"),
            detail_url=j.get("link"),
            beschreibung=ohne_html(j.get("snippet")),
            vollzeit=True if "vollzeit" in typ or "full" in typ else None,
            verguetung=j.get("salary") or None,
        ))
    return jobs
