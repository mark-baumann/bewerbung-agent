"""Ablauf von Suche bis Versand – ohne Netz, LLM, Browser oder echten Mailserver."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bewerbungsagent import ablauf, dokumente
from bewerbungsagent.bewerben.browser import BewerbungsLauf
from bewerbungsagent.bewerben.kanal import bestimme_kanal, finde_bewerbungs_email
from bewerbungsagent.config import lade_profil, speichere_profil
from bewerbungsagent.db import Speicher, dubletten_schluessel
from bewerbungsagent.models import Job, Score, Vorgang

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #

@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    """Arbeitsverzeichnis, Profil mit Lebenslauf, keine LLM-/SMTP-Keys."""
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OLLAMA_BASE_URL",
                 "OLLAMA_API_KEY", "OLLAMA_MODEL", "SMTP_HOST", "BA_BENUTZER", "BA_PASSWORT"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    profil_pfad = tmp_path / "config" / "profil.yaml"
    profil_pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(profil_pfad)
    lebenslauf = tmp_path / "unterlagen" / "lebenslauf.txt"
    lebenslauf.parent.mkdir()
    lebenslauf.write_text("Max Mustermann\nPython-Entwickler seit 2019\nPostgreSQL, Docker", encoding="utf-8")
    profil.unterlagen.lebenslauf = str(lebenslauf)
    profil.unterlagen.zeugnisse = []
    profil.automatisierung.probelauf_vorab = False
    speichere_profil(profil, profil_pfad)
    db = Speicher(tmp_path / "daten" / "test.sqlite3")
    yield profil, db, profil_pfad
    db.close()


def job(ref: str, **kw) -> Job:
    daten = dict(
        titel="Python Entwickler (m/w/d)", arbeitgeber=f"Firma {ref} GmbH", ort="München",
        beschreibung="Wir suchen Python, PostgreSQL, Docker, Kubernetes, REST, CI/CD und Git. "
                     "Unbefristet, Gleitzeit, Homeoffice.",
        externe_url=f"https://karriere.firma-{ref}.de/job",
    )
    daten.update(kw)
    return Job(ref=ref, **daten)


def score(ref: str, gesamt: float, ausgeschlossen: bool = False) -> Score:
    return Score(ref=ref, gesamt=gesamt, passung=gesamt, sentiment=50, ausgeschlossen=ausgeschlossen)


class FakeBrowser:
    def __init__(self, abgeschickt=True, rohdaten=None):
        self.aufrufe = []
        self.abgeschickt = abgeschickt
        self.rohdaten = rohdaten or {}

    async def bewerbe(self, job, anschreiben, dry_run=True, start_url=None, anschreiben_pdf=None):
        self.aufrufe.append({"ref": job.ref, "dry_run": dry_run, "start_url": start_url, "pdf": anschreiben_pdf})
        return BewerbungsLauf(
            ref=job.ref, dry_run=dry_run, abgeschickt=self.abgeschickt and not dry_run,
            zusammenfassung="Probelauf ok" if dry_run else "Abgeschickt.", schritte=7,
            url=start_url, rohdaten=dict(self.rohdaten),
        )


class FakeSmtp:
    def __init__(self):
        self.gesendet = []

    def __call__(self, nachricht, konfig):
        self.gesendet.append((nachricht, konfig))
        return "<id@test>"


def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.de")
    monkeypatch.setenv("SMTP_BENUTZER", "ich@example.de")
    monkeypatch.setenv("SMTP_PASSWORT", "geheim")


# --------------------------------------------------------------------------- #
# Kanal
# --------------------------------------------------------------------------- #

def test_bewerbungs_email_wird_erkannt_auch_verschleiert():
    text = "Fragen an datenschutz@firma.de. Bitte senden Sie Ihre Bewerbung an jobs (at) firma (dot) de."
    assert finde_bewerbungs_email(text) == ("jobs@firma.de", True)
    assert finde_bewerbungs_email("Kontakt: info@firma.de") == ("info@firma.de", False)
    assert finde_bewerbungs_email(None) == (None, False)


def test_kanal_rangfolge():
    mit_mail = job("a", beschreibung="Ihre Bewerbungsunterlagen an karriere@firma.de")
    assert bestimme_kanal(mit_mail).kanal == "email"
    nur_url = job("b", beschreibung="Kontakt: info@firma.de")
    assert bestimme_kanal(nur_url).kanal == "formular"
    nur_kontakt = job("c", beschreibung="Kontakt: info@firma.de", externe_url=None)
    wahl = bestimme_kanal(nur_kontakt)
    assert (wahl.kanal, wahl.empfaenger) == ("email", "info@firma.de")
    nichts = Job(ref="d", titel="X", arbeitgeber="Y", quelle="get-in-it")
    assert bestimme_kanal(nichts).kanal == "manuell"


def test_get_in_it_bewerbungslink_wird_aufgeloest():
    import httpx

    from bewerbungsagent.bewerben.kanal import loese_bewerbungslink

    def antwort(request):
        if "get-in-it" in request.url.host:
            return httpx.Response(200, text='<script>{"applyUrl":"https:\\/\\/jobs.firma.de\\/42"}</script>')
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(antwort), follow_redirects=True)
    j = Job(ref="g", titel="X", arbeitgeber="Y", quelle="get-in-it",
            externe_url="https://www.get-in-it.de/jobsuche/p1")
    assert loese_bewerbungslink(j, client) == "https://jobs.firma.de/42"


# --------------------------------------------------------------------------- #
# Datenbank
# --------------------------------------------------------------------------- #

def test_kandidaten_ohne_vorgang_dubletten_und_gesperrte(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([
        job("1"), job("2", arbeitgeber="Gesperrt AG"),
        job("3", arbeitgeber="Doppelt GmbH"), job("4", arbeitgeber="Doppelt GmbH", titel="Python Entwickler (w/m/d)"),
        job("5"),
    ])
    for ref, wert in (("1", 90), ("2", 85), ("3", 80), ("4", 79), ("5", 70)):
        db.speichere_score(score(ref, wert))
    db.setze_vorgang(Vorgang(ref="5", status="abgelehnt"))
    refs = [j.ref for j, _ in db.kandidaten(min_score=60, limit=10, ausschluss_arbeitgeber=["gesperrt ag"])]
    assert refs == ["1", "3"]


def test_dubletten_schluessel_ignoriert_gender_und_rechtsform():
    assert dubletten_schluessel("Beispiel GmbH", "Entwickler (m/w/d)") == dubletten_schluessel(
        "Beispiel", "Entwickler (w/m/d)"
    )


def test_nur_ein_worker_darf_senden(umgebung):
    _, db, _ = umgebung
    db.speichere_jobs([job("1")])
    db.setze_vorgang(Vorgang(ref="1", status="freigegeben"))
    assert db.beanspruche_zum_senden("1") is True
    assert db.beanspruche_zum_senden("1") is False
    assert db.vorgang("1").status == "wird_gesendet"


# --------------------------------------------------------------------------- #
# Vorbereiten, Freigeben, Senden
# --------------------------------------------------------------------------- #

def test_vorbereiten_erzeugt_anschreiben_pdf_und_wartet(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1", beschreibung="Bewerbung bitte an jobs@firma.de. Python, Docker.")])
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    assert v.status == "wartet"
    assert (v.kanal, v.empfaenger) == ("email", "jobs@firma.de")
    assert "Sehr geehrte" in v.anschreiben
    assert Path(v.anschreiben_pdf).exists()
    assert v.betreff.startswith("Bewerbung als Python Entwickler")
    assert db.vorgang("1").status == "wartet"


def test_probelauf_findet_email_und_wechselt_kanal(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1")])
    browser = FakeBrowser(rohdaten={"bewerbungs_email": "HR@Firma.de", "hindernis": "nur per E-Mail"})
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, probelauf=True, link_aufloesen=False,
                           browser_fabrik=lambda p: browser)
    assert browser.aufrufe[0]["dry_run"] is True
    assert browser.aufrufe[0]["pdf"] == v.anschreiben_pdf
    assert (v.kanal, v.empfaenger) == ("email", "hr@firma.de")
    assert v.probelauf_daten["schritte"] == 7
    assert db.bewerbungen("1")[0]["status"] == "probelauf"


def test_probelauf_mit_login_pflicht_wird_selbst_bewerben(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1")])
    browser = FakeBrowser(rohdaten={"hindernis": "Registrierung im Karriereportal nötig"})
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, probelauf=True, link_aufloesen=False,
                           browser_fabrik=lambda p: browser)
    assert v.kanal == "manuell"
    with pytest.raises(ValueError, match="selbst bewerben"):
        ablauf.freigeben(db, profil, "1")


def test_freigeben_prueft_und_erzeugt_pdf_bei_geaendertem_text(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1", externe_url=None, beschreibung="Kontakt: info@firma.de")])
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    pdf_vorher = Path(v.anschreiben_pdf).read_bytes()
    with pytest.raises(ValueError, match="Empfängeradresse"):
        ablauf.freigeben(db, profil, "1", empfaenger="")
    v = ablauf.freigeben(db, profil, "1", anschreiben="Sehr geehrte Damen und Herren,\n\nNeu.\n\nGruß",
                         empfaenger="info@firma.de")
    assert v.status == "freigegeben"
    assert Path(v.anschreiben_pdf).read_bytes() != pdf_vorher


def test_senden_per_email_mit_anhaengen_und_bcc(umgebung, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    db.speichere_jobs([job("1", beschreibung="Bewerbung an jobs@firma.de")])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    ablauf.freigeben(db, profil, "1")
    smtp = FakeSmtp()
    ergebnis = ablauf.sende_freigegebene(db, profil, mail_sender=smtp)
    assert ergebnis["abgeschickt"] == 1
    nachricht, konfig = smtp.gesendet[0]
    assert nachricht["To"] == "jobs@firma.de"
    assert nachricht["Bcc"] == "ich@example.de"
    namen = [a.get_filename() for a in nachricht.iter_attachments()]
    assert namen[0].endswith(".pdf") and "lebenslauf.txt" in namen
    v = db.vorgang("1")
    assert v.status == "abgeschickt" and v.gesendet_am
    assert db.bewerbungen("1")[0]["status"] == "abgeschickt"


def test_senden_per_formular_nutzt_aufgeloeste_url(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1")])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    db.aktualisiere_vorgang("1", bewerbungs_url="https://firma.softgarden.io/apply/1")
    ablauf.freigeben(db, profil, "1")
    browser = FakeBrowser()
    ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: browser)
    assert browser.aufrufe == [{
        "ref": "1", "dry_run": False, "start_url": "https://firma.softgarden.io/apply/1",
        "pdf": db.vorgang("1").anschreiben_pdf,
    }]
    assert db.vorgang("1").status == "abgeschickt"


def test_fehlgeschlagener_versand_wird_nicht_wiederholt(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1")])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    ablauf.freigeben(db, profil, "1")
    browser = FakeBrowser(abgeschickt=False)
    ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: browser)
    ablauf.sende_freigegebene(db, profil, browser_fabrik=lambda p: browser)
    assert len(browser.aufrufe) == 1
    assert db.vorgang("1").status == "fehlgeschlagen"


def test_ohne_smtp_wartet_email_auf_einrichtung(umgebung, monkeypatch):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1", beschreibung="Bewerbung an jobs@firma.de")])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    v = ablauf.freigeben(db, profil, "1")
    # Das Ja gilt, der Hinweis sagt, was noch fehlt
    assert v.status == "freigegeben" and "SMTP" in v.hinweis
    assert ablauf.sende_freigegebene(db, profil)["zurueckgestellt"] == 1
    v = db.vorgang("1")
    assert v.status == "freigegeben" and "SMTP" in v.hinweis
    # Sobald SMTP eingerichtet ist, geht sie raus
    smtp_env(monkeypatch)
    assert ablauf.sende_freigegebene(db, profil, mail_sender=FakeSmtp())["abgeschickt"] == 1
    assert db.vorgang("1").message_id == "<id@test>"
    assert [e["art"] for e in db.entscheidungen()] == ["ja"]


def test_tageslimit_stellt_zurueck(umgebung, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.max_pro_tag = 1
    db.speichere_jobs([job(r, beschreibung="Bewerbung an jobs@firma.de") for r in ("1", "2")])
    for ref in ("1", "2"):
        ablauf.vorbereiten(db, profil, ref, mit_llm=False, link_aufloesen=False)
        ablauf.freigeben(db, profil, ref)
    ergebnis = ablauf.sende_freigegebene(db, profil, mail_sender=FakeSmtp())
    assert ergebnis == {"abgeschickt": 1, "fehlgeschlagen": 0, "zurueckgestellt": 1}
    zurueck = [v for v in db.vorgaenge() if v.status == "freigegeben"]
    assert len(zurueck) == 1 and "Tageslimit" in zurueck[0].hinweis


def test_haengender_versand_wird_fehlgeschlagen(umgebung):
    _, db, _ = umgebung
    db.speichere_jobs([job("1")])
    db.setze_vorgang(Vorgang(ref="1", status="wird_gesendet"))
    alt = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    db.con.execute("UPDATE vorgaenge SET aktualisiert_am = ? WHERE ref = '1'", (alt,))
    db.con.commit()
    assert ablauf.aufraeumen(db) == 1
    assert db.vorgang("1").status == "fehlgeschlagen"


def test_ablehnen_sperrt_arbeitgeber_im_profil(umgebung):
    profil, db, profil_pfad = umgebung
    db.speichere_jobs([job("1", arbeitgeber="Nervig AG")])
    ablauf.ablehnen(db, profil, "1", grund="passt nicht", arbeitgeber_sperren=True, profil_pfad=str(profil_pfad))
    assert db.vorgang("1").status == "abgelehnt"
    assert lade_profil(profil_pfad).bewertung.ausschluss_arbeitgeber == ["Nervig AG"]


def test_selbst_beworben_zaehlt_im_nachweis(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([job("1")])
    ablauf.selbst_beworben(db, "1")
    zeilen = dokumente.nachweis_zeilen(db, date.today() - timedelta(days=1), date.today() + timedelta(days=1))
    assert [z["weg"] for z in zeilen] == ["selbst"]
    assert "Firma 1" in dokumente.nachweis_csv(zeilen)
    assert dokumente.nachweis_pdf(zeilen, profil, date.today(), date.today()).startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# Automatiklauf und Cron-Tick
# --------------------------------------------------------------------------- #

class FakeQuelle:
    def __init__(self, jobs):
        self.jobs = jobs

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def hole_jobs(self, **_kwargs):
        return [Job(**j.as_dict()) for j in self.jobs]


def test_automatiklauf_sucht_bewertet_und_bereitet_vor(umgebung, monkeypatch):
    profil, db, _ = umgebung
    profil.suche.was = ["Python"]
    profil.suche.quellen = ["arbeitsagentur"]
    profil.automatisierung.max_pro_lauf = 2
    profil.bewertung.min_score = 30
    stellen = [job(str(i)) for i in range(4)] + [job("zeit", beschreibung="Zeitarbeit für Python-Entwickler")]
    monkeypatch.setattr("bewerbungsagent.jobsuche.client_fuer", lambda quelle, profil=None: FakeQuelle(stellen))
    monkeypatch.setattr("bewerbungsagent.bewerben.kanal.loese_bewerbungslink", lambda j, client=None: j.bewerbungs_url)

    ergebnis = ablauf.automatik_lauf(db, profil, mit_llm=False)
    assert ergebnis["gefunden"] == 5 and ergebnis["bewertet"] == 5
    assert ergebnis["vorbereitet"] == 2
    wartend = db.vorgaenge("wartet")
    assert len(wartend) == 2 and all(v.herkunft == "automatik" for v in wartend)
    assert db.score("zeit").ausgeschlossen
    lauf = db.letzte_laeufe("automatik")[0]
    assert lauf["status"] == "erfolgreich" and lauf["zusammenfassung"]["vorbereitet"] == 2
    # Suchbegriff-Statistik kennt die Treffer
    statistik = db.suchbegriff_statistik(30)
    assert statistik[0]["begriff"] == "Python" and statistik[0]["treffer"] == 5


def test_automatik_stapelt_nicht_endlos(umgebung, monkeypatch):
    profil, db, _ = umgebung
    profil.suche.was = ["Python"]
    profil.suche.quellen = ["arbeitsagentur"]
    profil.automatisierung.max_pro_lauf = 1
    profil.bewertung.min_score = 30
    stellen = [job(str(i)) for i in range(6)]
    monkeypatch.setattr("bewerbungsagent.jobsuche.client_fuer", lambda quelle, profil=None: FakeQuelle(stellen))
    monkeypatch.setattr("bewerbungsagent.bewerben.kanal.loese_bewerbungslink", lambda j, client=None: j.bewerbungs_url)
    for _ in range(5):
        ablauf.automatik_lauf(db, profil, mit_llm=False)
    assert len(db.vorgaenge("wartet")) == 3  # max_pro_lauf * MAX_WARTEND_FAKTOR


def test_tick_startet_nur_wenn_faellig_und_sendet_freigaben(umgebung, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.suche.was = ["Python"]
    profil.suche.quellen = ["arbeitsagentur"]
    profil.automatisierung.cron = "0 6 * * *"
    monkeypatch.setattr("bewerbungsagent.jobsuche.client_fuer", lambda quelle, profil=None: FakeQuelle([]))

    nicht_faellig = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    assert "automatik" not in ablauf.tick(db, profil, jetzt=nicht_faellig, mit_llm=False)

    faellig = datetime(2026, 10, 11, 4, 2, tzinfo=timezone.utc)
    ergebnis = ablauf.tick(db, profil, jetzt=faellig, mit_llm=False)
    assert ergebnis["automatik"]["gefunden"] == 0

    db.speichere_jobs([job("1", beschreibung="Bewerbung an jobs@firma.de")])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    ablauf.freigeben(db, profil, "1")
    smtp = FakeSmtp()
    ergebnis = ablauf.tick(db, profil, jetzt=nicht_faellig, mit_llm=False, mail_sender=smtp)
    assert ergebnis["senden"]["abgeschickt"] == 1 and len(smtp.gesendet) == 1


def test_tick_im_modus_aus_sucht_nicht(umgebung, monkeypatch):
    profil, db, _ = umgebung
    profil.automatisierung.modus = "aus"
    monkeypatch.setattr("bewerbungsagent.jobsuche.client_fuer", lambda quelle, profil=None: pytest.fail("darf nicht suchen"))
    ergebnis = ablauf.tick(db, profil, jetzt=datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc))
    assert "automatik" not in ergebnis


def test_lebenslauf_text_wird_gelesen_und_gecacht(umgebung):
    profil, _, _ = umgebung
    assert "Python-Entwickler seit 2019" in dokumente.lebenslauf_text(profil)
    assert Path("daten/lebenslauf_cache.json").exists()


def test_env_datei_ueberschreibt_nichts(tmp_path, monkeypatch):
    import os

    from bewerbungsagent.config import lade_env

    (tmp_path / ".env").write_text(
        "# Kommentar\nSMTP_HOST=smtp.test\nexport SMTP_PORT='587'\nANTHROPIC_API_KEY=sk-ant-...\n"
        "OPENAI_API_KEY=schon\n", encoding="utf-8",
    )
    # Kopie der Umgebung, damit nichts in andere Tests durchsickert
    monkeypatch.setattr(os, "environ", {
        k: v for k, v in os.environ.items()
        if k not in ("SMTP_HOST", "SMTP_PORT", "ANTHROPIC_API_KEY")
    })
    os.environ["OPENAI_API_KEY"] = "gesetzt"
    assert lade_env(tmp_path / ".env") == 2

    assert os.environ["SMTP_HOST"] == "smtp.test" and os.environ["SMTP_PORT"] == "587"
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["OPENAI_API_KEY"] == "gesetzt"
