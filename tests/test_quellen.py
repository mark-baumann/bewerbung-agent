"""Neue Jobquellen: Parser mit aufgezeichneten Live-Antworten (kein Netz) + Such-Orchestrierung."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bewerbungsagent import jobsuche
from bewerbungsagent.db import Speicher
from bewerbungsagent.models import Job
from bewerbungsagent.sources import QUELLEN, bekannte, client_fuer
from bewerbungsagent.sources import aggregatoren, arbeitnow, ats, oeffentlicher_dienst, remote
from bewerbungsagent.sources.basis import begriff_passt, normalisiert, ohne_html, ort_passt

DATEN = Path(__file__).parent / "daten" / "quellen"


def roh(name: str) -> str:
    return (DATEN / name).read_text(encoding="utf-8")


def js(name: str):
    return json.loads(roh(name))


# --------------------------------------------------------------------------- #
# Basis
# --------------------------------------------------------------------------- #

def test_normalisierung_und_staedte():
    assert normalisiert("München, Bayern") == "muenchen bayern"
    assert normalisiert("Munich, Bavaria, Germany") == "muenchen bavaria germany"
    assert normalisiert("Köln") == normalisiert("Cologne")


def test_begriff_und_ort():
    job = Job(ref="x", titel="Senior Software-Entwickler Python (m/w/d)", arbeitgeber="A", ort="Munich")
    assert begriff_passt(job, "Python Entwickler")
    assert begriff_passt(job, "Softwareentwickler")
    assert not begriff_passt(job, "Java")
    assert ort_passt(job, ["muenchen"], remote_ok=False)
    assert not ort_passt(job, ["berlin"], remote_ok=False)
    remote_job = Job(ref="y", titel="T", arbeitgeber="A", ort="Remote in Deutschland")
    assert ort_passt(remote_job, ["berlin"], remote_ok=True)
    assert not ort_passt(remote_job, ["berlin"], remote_ok=False)


def test_ohne_html_doppelt_escaped():
    assert ohne_html("&lt;p&gt;Hallo &amp;amp; Tsch&uuml;ss&lt;/p&gt;") == "Hallo & Tschüss"


def test_registry_und_unbekannte_quellen():
    assert {"arbeitsagentur", "arbeitnow", "service-bund", "interamt", "remote", "wunscharbeitgeber"} <= set(QUELLEN)
    assert bekannte(["arbeitsagentur", "gibtsnicht"]) == ["arbeitsagentur"]
    with client_fuer("arbeitnow") as client:
        assert client.name == "arbeitnow"
    assert not QUELLEN["adzuna"].verfuegbar() or QUELLEN["adzuna"].schluessel


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def test_arbeitnow():
    jobs = [arbeitnow.job_aus_eintrag(e) for e in js("arbeitnow.json")["data"]]
    assert jobs and all(j.ref.startswith("arbeitnow:") and j.quelle == "arbeitnow" for j in jobs)
    assert jobs[0].beschreibung and "<" not in jobs[0].beschreibung
    assert any("muenchen" in normalisiert(j.ort) for j in jobs)


def test_service_bund_rss_und_detail():
    jobs = oeffentlicher_dienst.jobs_aus_rss(roh("servicebund.xml"))
    assert jobs[0].ref.startswith("interamt:")  # Interamt-Stellen teilen die Referenz
    assert jobs[0].plz and jobs[0].ort and jobs[0].bewerbungsfrist and jobs[0].arbeitgeber.startswith("Landesamt")
    job = Job(ref="service-bund:editor-1", titel="IT", arbeitgeber="BLKA", quelle="service-bund")
    oeffentlicher_dienst.details_aus_html(job, roh("servicebund_detail.html"))
    assert job.beschreibung.startswith("Tätigkeitsprofil") and job.bewerbungsfrist == "2026-11-06"
    assert "bevi.polizei.bayern.de" in job.externe_url


def test_interamt_liste_und_detail():
    daten = js("interamt_liste.json")
    jobs = [oeffentlicher_dienst.job_aus_interamt(s) for s in daten["Stellenangebote"]]
    muc = [j for j in jobs if j.ort == "München"]
    assert muc and muc[0].ref.startswith("interamt:") and muc[0].strasse
    job = Job(ref="interamt:1492085", titel="X", arbeitgeber="Y", quelle="interamt")
    oeffentlicher_dienst.details_aus_interamt(job, js("interamt_detail.json"))
    assert job.kontakt_email == "alexander.batschkus@lotto-bayern.de"
    assert job.ansprechpartner == "Alexander Batschkus"
    assert job.bewerbungsfrist == "2026-10-30"
    assert "interamt.de" in job.bewerbung_hinweis and "Lebenslauf" in job.bewerbung_hinweis


def test_interamt_client_filtert_ort_und_reichert_an():
    def antwort(request: httpx.Request) -> httpx.Response:
        if "id" in request.url.params:
            return httpx.Response(200, json=js("interamt_detail.json"))
        return httpx.Response(200, json=js("interamt_liste.json"))

    with oeffentlicher_dienst.InteramtClient(transport=httpx.MockTransport(antwort)) as client:
        jobs = client.hole_jobs(was="Informatik", wo="München", orte=["muenchen"], max_treffer=5)
    assert jobs and all(j.ort == "München" for j in jobs)
    assert jobs[0].kontakt_email


def test_eures_ueberspringt_stellen_der_arbeitsagentur():
    jobs = aggregatoren.eures(js("eures.json"))
    ids = [j["id"] for j in js("eures.json")["jvs"]]
    assert all(j.quelle == "eures" for j in jobs)
    assert len(jobs) < len(ids)  # das erste Beispiel stammt von der BA (10001-...)


def test_remote_boards_nur_fuer_deutschland_offen():
    alle = (remote.himalayas(js("himalayas.json")) + remote.remotive(js("remotive.json"))
            + remote.remoteok(js("remoteok.json")) + remote.jobicy(js("jobicy.json")) + remote.wwr(roh("wwr.xml")))
    assert alle and all(j.homeoffice and j.quelle == "remote" for j in alle)
    regionen = [r["candidate_required_location"] for r in js("remotive.json")["jobs"]]
    assert "USA" in regionen and len(remote.remotive(js("remotive.json"))) == len(regionen) - 1


def test_adzuna_und_jooble():
    jobs = aggregatoren.adzuna({"results": [{
        "id": "1", "title": "<strong>Python</strong> Entwickler", "company": {"display_name": "Firma"},
        "location": {"display_name": "München"}, "created": "2026-10-01T10:00:00Z",
        "redirect_url": "https://adzuna.de/x", "description": "Kurz", "contract_time": "full_time",
    }]})
    assert jobs[0].titel == "Python Entwickler" and jobs[0].vollzeit
    jobs = aggregatoren.jooble({"jobs": [{"id": 5, "title": "Dev", "company": "F", "location": "Berlin",
                                          "snippet": "x", "link": "https://jooble.org/x", "updated": "2026-10-02"}]})
    assert jobs[0].ref == "jooble:5"


def test_adzuna_ohne_schluessel_meldet_klaren_fehler(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    with aggregatoren.AdzunaClient() as client, pytest.raises(Exception, match="Konten"):
        client.hole_jobs(was="Python")


@pytest.mark.parametrize("parser,datei,kennung", [
    (ats.greenhouse, "greenhouse.json", "celonis"),
    (ats.lever, "lever.json", "qonto"),
    (ats.smartrecruiters, "smartrecruiters.json", "BoschGroup"),
    (ats.recruitee, "recruitee.json", "channable"),
    (ats.ashby, "ashby.json", "deepl"),
])
def test_ats_parser(parser, datei, kennung):
    jobs = parser(js(datei), kennung)
    assert jobs and all(j.quelle == "wunscharbeitgeber" and j.externe_url and j.titel for j in jobs)
    assert all(j.ref.split(":")[1] == kennung for j in jobs)


def test_personio_xml():
    jobs = ats.personio(roh("personio.xml"), "tado")
    assert jobs[0].ref == "personio:tado:2502124" and jobs[0].arbeitgeber == "tado GmbH"
    assert jobs[0].externe_url.endswith("/job/2502124") and "EUR" in jobs[0].verguetung


@pytest.mark.parametrize("url,erwartet", [
    ("https://job-boards.greenhouse.io/celonis/jobs/123", ("greenhouse", "celonis")),
    ("https://jobs.lever.co/qonto/abc", ("lever", "qonto")),
    ("https://tado.jobs.personio.de/", ("personio", "tado")),
    ("https://jobs.ashbyhq.com/deepl", ("ashby", "deepl")),
    ("https://channable.recruitee.com/o/x", ("recruitee", "channable")),
    ("https://jobs.smartrecruiters.com/BoschGroup/1", ("smartrecruiters", "BoschGroup")),
])
def test_ats_erkennung_aus_url(url, erwartet):
    assert tuple(ats.erkenne_ats(url).values()) == erwartet


def test_ats_erkennung_aus_html():
    seite = '<html><iframe src="https://boards.greenhouse.io/embed/job_board?for=n26"></iframe></html>'
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=seite)))
    assert ats.erkenne_ats("https://n26.com/karriere", client) == {"ats": "greenhouse", "kennung": "n26"}


# --------------------------------------------------------------------------- #
# Orchestrierung
# --------------------------------------------------------------------------- #

class FeedFake:
    aufrufe = 0

    def __init__(self, jobs):
        self.jobs = jobs

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def hole_feed(self):
        FeedFake.aufrufe += 1
        return [Job(**j.as_dict()) for j in self.jobs]

    def anreichern(self, job):
        job.beschreibung = "Volltext nachgeladen"


def test_feed_mindestabstand_und_lokale_auswahl(tmp_path, monkeypatch):
    jobs = [
        Job(ref="arbeitnow:a", titel="Python Entwickler", arbeitgeber="A", quelle="arbeitnow", ort="Munich",
            veroeffentlicht="2099-01-01"),
        Job(ref="arbeitnow:b", titel="Python Entwickler", arbeitgeber="B", quelle="arbeitnow", ort="Berlin",
            veroeffentlicht="2099-01-01"),
        Job(ref="arbeitnow:c", titel="Koch", arbeitgeber="C", quelle="arbeitnow", ort="München",
            veroeffentlicht="2099-01-01"),
    ]
    FeedFake.aufrufe = 0
    monkeypatch.setattr(jobsuche, "client_fuer", lambda quelle, profil=None: FeedFake(jobs))
    with Speicher(tmp_path / "db.sqlite3") as db:
        assert jobsuche.aktualisiere_feed(db, "arbeitnow")[2] is True
        assert jobsuche.aktualisiere_feed(db, "arbeitnow")[2] is False  # Mindestabstand
        assert FeedFake.aufrufe == 1
        treffer = jobsuche.lokale_treffer(db, "arbeitnow", suchbegriff="Python", orte=["muenchen"],
                                          remote_ok=False, tage=30, nur_vollzeit=False, max_treffer=10)
        assert [j.ref for j in treffer] == ["arbeitnow:a"]
        assert db.job("arbeitnow:a").beschreibung == "Volltext nachgeladen"
        statistik = db.suchbegriff_statistik()
        assert statistik[0]["begriff"] == "Python" and statistik[0]["treffer"] == 1


def test_orte_im_umkreis_aus_arbeitsagentur(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as db:
        db.speichere_jobs([
            Job(ref="1", titel="T", arbeitgeber="A", quelle="arbeitsagentur", ort="Garching", entfernung_km=15),
            Job(ref="2", titel="T", arbeitgeber="A", quelle="arbeitsagentur", ort="Augsburg", entfernung_km=60),
        ])
        assert jobsuche.orte_fuer(db, "München", 30) == ["garching", "muenchen"]


def test_speichern_ueberschreibt_volltext_nicht(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as db:
        db.speichere_jobs([Job(ref="1", titel="T", arbeitgeber="A", quelle="arbeitnow", beschreibung="Volltext")])
        db.speichere_jobs([Job(ref="1", titel="T", arbeitgeber="A", quelle="service-bund", beschreibung=None)])
        job = db.job("1")
        assert job.beschreibung == "Volltext" and job.quelle == "arbeitnow"
