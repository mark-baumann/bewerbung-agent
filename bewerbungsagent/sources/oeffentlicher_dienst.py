"""Oeffentlicher Dienst: service.bund.de (Bund, Laender, Kommunen) und Interamt.

- service.bund.de: offizieller RSS-Feed der neuesten Stellen (ohne Filter, robots-konform),
  hoechstens stuendlich abrufen; Volltext nur fuer lokal ausgewaehlte Stellen nachladen.
- Interamt: JSON-Webservice (`webservice_v2`) - Stichwortsuche und Details mit
  Ansprechpartner, E-Mail, Bewerbungsfrist und Pflichtanlagen.

Stellen, die service.bund.de von Interamt uebernimmt, bekommen dieselbe Referenz
`interamt:<id>` - so entstehen keine Dubletten.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any

from ..models import Job
from .basis import QuellenClient, iso_datum, normalisiert, ohne_html, ort_passt

SERVICE_BUND_RSS = "https://www.service.bund.de/Content/Globals/Functions/RSSFeed/RSSGenerator_Stellen.xml"
INTERAMT_API = "https://interamt.de/koop/app/webservice_v2"
INTERAMT_STELLE = "https://interamt.de/koop/app/stelle?id={id}"
_TEILEN = re.compile(r"facebook|twitter|xing\.com|linkedin|whatsapp|service\.bund\.de|bund\.de/SiteGlobals", re.I)


# --------------------------------------------------------------------------- #
# service.bund.de
# --------------------------------------------------------------------------- #

class ServiceBundClient(QuellenClient):
    name = "service-bund"

    def hole_feed(self) -> list[Job]:
        return jobs_aus_rss(self._text(SERVICE_BUND_RSS))

    def anreichern(self, job: Job) -> None:
        if job.ref.startswith("interamt:"):
            with InteramtClient(transport=getattr(self.http, "_transport", None)) as interamt:
                interamt.anreichern(job)
            if job.beschreibung:
                return
        if job.detail_url:
            details_aus_html(job, self._text(job.detail_url))


def jobs_aus_rss(xml_text: str) -> list[Job]:
    jobs: list[Job] = []
    wurzel = ET.fromstring(xml_text)
    for item in wurzel.iter("item"):
        titel = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").split("#")[0].strip()
        if not titel or not link:
            continue
        beschreibung = item.findtext("description") or ""
        felder = _rss_felder(beschreibung)
        ort = felder.get("ort") or ""
        plz_m = re.match(r"(\d{5})\s+(.*)", ort)
        m = re.search(r"/IMPORTE/Stellenangebote/([^/]+)/.*?/(\w+)\.html", link)
        herkunft, kennung = (m.group(1), m.group(2)) if m else ("", link.rsplit("/", 1)[-1])
        ref = f"interamt:{kennung}" if herkunft == "interamt" else f"service-bund:{herkunft}-{kennung}"
        jobs.append(Job(
            ref=ref,
            titel=titel,
            arbeitgeber=felder.get("arbeitgeber") or "Öffentlicher Dienst",
            quelle="service-bund",
            ort=plz_m.group(2) if plz_m else (ort or None),
            plz=plz_m.group(1) if plz_m else None,
            veroeffentlicht=iso_datum(item.findtext("pubDate")),
            bewerbungsfrist=iso_datum(felder.get("frist")),
            externe_url=link,
            detail_url=link,
        ))
    return jobs


def _rss_felder(beschreibung: str) -> dict[str, str]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", beschreibung))
    text = re.sub(r"\s+", " ", text)
    felder = {}
    for name, muster in (
        ("arbeitgeber", r"Arbeitgeber:\s*(.+?)\s+Ort:"),
        ("ort", r"Ort:\s*(.+?)\s+(?:Bewerbungsfrist|Veröffentlichungsende|$)"),
        ("frist", r"Bewerbungsfrist:\s*([\d.]+)"),
    ):
        m = re.search(muster, text)
        if m:
            felder[name] = m.group(1).strip()
    return felder


def details_aus_html(job: Job, seite: str) -> None:
    """Volltext und Originallink aus der Detailseite von service.bund.de."""
    i, j = seite.find("<article"), seite.find("</article>")
    artikel = seite[i:j] if i >= 0 and j > i else seite
    text = ohne_html(artikel) or ""
    # Plattform-Hinweis abschneiden, eigentliche Ausschreibung behalten
    for marke in ("Tätigkeitsprofil:", "Ihre Aufgaben", "Aufgaben"):
        k = text.find(marke)
        if k > 0:
            kopf = text[:k]
            text = text[k:]
            frist = re.search(r"Bewerbungsfrist\s+([\d.]+)", kopf)
            if frist and not job.bewerbungsfrist:
                job.bewerbungsfrist = iso_datum(frist.group(1))
            break
    job.beschreibung = text[:20000] or job.beschreibung
    for link in re.findall(r'href="(https?://[^"]+)"', artikel):
        link = html.unescape(link)
        if not _TEILEN.search(link):
            job.externe_url = link
            break


# --------------------------------------------------------------------------- #
# Interamt
# --------------------------------------------------------------------------- #

class InteramtClient(QuellenClient):
    name = "interamt"

    def hole_jobs(
        self, *, was: str, wo: str = "", orte: list[str] | None = None, remote_ok: bool = False,
        max_treffer: int = 50, mit_details: bool = True, **_ignoriert: Any,
    ) -> list[Job]:
        daten = self._json(INTERAMT_API, params={"suchtext": was})
        jobs = [job_aus_interamt(s) for s in daten.get("Stellenangebote") or []]
        jobs = [j for j in jobs if j and _frist_offen(j)]
        if wo:
            ortsnamen = [normalisiert(o) for o in (orte or [wo])]
            jobs = [j for j in jobs if ort_passt(j, ortsnamen, remote_ok)]
        jobs = jobs[: max(1, int(max_treffer))]
        if mit_details:
            for job in jobs:
                try:
                    self.anreichern(job)
                except Exception:  # Details sind optional
                    pass
        return jobs

    def anreichern(self, job: Job) -> None:
        kennung = job.ref.split(":", 1)[-1]
        d = self._json(INTERAMT_API, params={"id": kennung})
        if not isinstance(d, dict) or not d.get("Id"):
            return
        details_aus_interamt(job, d)


def _frist_offen(job: Job) -> bool:
    from datetime import date

    frist = job.bewerbungsfrist or ""
    return not re.match(r"\d{4}-\d{2}-\d{2}$", frist) or frist >= date.today().isoformat()


def job_aus_interamt(s: dict[str, Any]) -> Job | None:
    if not s.get("Id"):
        return None
    orte = s.get("StellenangebotOrt") or []
    ort = orte[0] if orte else {}
    bezahlung = s.get("Bezahlung") or {}
    verguetung = " / ".join(x for x in (bezahlung.get("Entgelt"), bezahlung.get("Besoldung")) if x) or None
    daten = s.get("Daten") or {}
    return Job(
        ref=f"interamt:{s['Id']}",
        titel=(s.get("StellenBezeichnung") or "").strip(),
        arbeitgeber=(s.get("Behoerde") or "Öffentlicher Dienst").strip(),
        quelle="interamt",
        ort=ort.get("Ort"),
        plz=ort.get("PLZ"),
        strasse=" ".join(x for x in (ort.get("Strasse"), ort.get("Hausnummer")) if x) or None,
        veroeffentlicht=iso_datum(daten.get("Eingestellt")),
        bewerbungsfrist=iso_datum(daten.get("Bewerbungsfrist")) or daten.get("Bewerbungsfrist"),
        befristung=s.get("BeschaeftigungDauer") or None,
        verguetung=verguetung,
        teilzeit=True if "teilzeit" in (s.get("Teilzeit") or "").lower() or "beides" in (s.get("Teilzeit") or "") else None,
        externe_url=INTERAMT_STELLE.format(id=s["Id"]),
        detail_url=INTERAMT_STELLE.format(id=s["Id"]),
    )


def details_aus_interamt(job: Job, d: dict[str, Any]) -> None:
    job.beschreibung = ohne_html(d.get("Beschreibung")) or job.beschreibung
    job.arbeitgeber = d.get("StellenangebotBehoerde") or job.arbeitgeber
    job.bewerbungsfrist = iso_datum(d.get("DatumBewerbungsfrist")) or job.bewerbungsfrist
    orte = d.get("StellenangebotOrt") or []
    if orte:
        o = orte[0]
        job.ort, job.plz = o.get("Ort") or job.ort, o.get("PLZ") or job.plz
        job.strasse = " ".join(x for x in (o.get("Strasse"), o.get("Hausnummer")) if x) or job.strasse
    kontakt = d.get("ExtAnsprechpartner") or {}
    name = " ".join(x for x in (kontakt.get("ExtAnsprechpartnerAnrede"), kontakt.get("ExtAnsprechpartnerVorname"),
                                kontakt.get("ExtAnsprechpartnerNachname")) if x)
    job.ansprechpartner = name or job.ansprechpartner
    job.kontakt_email = (kontakt.get("ExtAnsprechpartnerEMail") or "").strip().lower() or job.kontakt_email
    prozess = (d.get("ProzessBewerbung") or "").strip()
    url = (d.get("BewerbungUrl") or "").strip()
    anlagen = [a.get("Anlagetyp") for a in d.get("Pflichtanlagen") or [] if a.get("Anlagetyp")]
    if url:
        job.externe_url = url
        hinweis = "Bewerbung über das Online-Formular der Behörde"
    elif prozess.lower() == "online":
        hinweis = "Bewerbung online über interamt.de (Konto bei Interamt nötig)"
    elif prozess:
        hinweis = f"Bewerbungsweg laut Anzeige: {prozess}"
    else:
        hinweis = None
    if anlagen:
        hinweis = f"{hinweis + '. ' if hinweis else ''}Geforderte Unterlagen: {', '.join(anlagen)}"
    job.bewerbung_hinweis = hinweis or job.bewerbung_hinweis
