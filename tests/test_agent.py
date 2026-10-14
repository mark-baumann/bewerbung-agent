"""Der selbststaendige Agent: Dossier, Selbstpruefung, Autopilot, Lernen, Nachfassen, Tick."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bewerbungsagent import ablauf, anschreiben, autopilot, dossier, lernen, nachfassen, pruefung
from bewerbungsagent.bewerben.kanal import bestimme_kanal
from bewerbungsagent.config import lade_profil, speichere_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.models import Job, Score, Vorgang
from bewerbungsagent.scoring import llm

REPO = Path(__file__).resolve().parent.parent

ANZEIGE = """Ihre Aufgaben
- Entwicklung von Backend-Services in Python
- Betrieb der Plattform auf Kubernetes
Ihr Profil
- Mehrjährige Erfahrung mit Python und PostgreSQL
- Erfahrung mit Terraform von Vorteil
Wir bieten
- Gleitzeit, Homeoffice
Bitte senden Sie Ihre Unterlagen unter Angabe der Kennziffer 2026-IT-17 bis zum 30.11.2099
an bewerbung@musterfirma.de. Für Rückfragen steht Ihnen Frau Dr. Erika Muster zur Verfügung."""

GUT = """Sehr geehrte Frau Dr. Muster,

in Ihrer Ausschreibung suchen Sie jemanden, der Backend-Services in Python entwickelt und die Plattform
zuverlässig betreibt. Genau das mache ich seit 2019: Ich entwickle Python-Services mit PostgreSQL und Docker
und habe zuletzt als Teamlead eine Zahlungsplattform verantwortet. Den Betrieb auf Kubernetes kenne ich aus
dem Alltag, Deployments und Monitoring gehören für mich dazu. Besonders reizt mich an Ihrer Stelle, dass
Entwicklung und Betrieb eng zusammenarbeiten und ich meine Erfahrung mit verteilten Systemen einbringen kann.
Gleichzeitig freue ich mich darauf, in einem neuen Umfeld dazuzulernen und Verantwortung für stabile Dienste
zu übernehmen. Gern erläutere ich Ihnen in einem persönlichen Gespräch, wie ich Ihr Team unterstützen kann.
Ich bin ab sofort verfügbar und freue mich auf Ihre Rückmeldung zu meiner Bewerbung und ein erstes Kennenlernen.

Mit freundlichen Grüßen
Max Mustermann"""

SCHLECHT = GUT.replace("Den Betrieb auf Kubernetes", "Mit Terraform automatisiere ich seit 2012 Infrastruktur. Den Betrieb auf Kubernetes")


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OLLAMA_BASE_URL",
                 "OLLAMA_API_KEY", "OLLAMA_MODEL", "SMTP_HOST", "BA_BENUTZER", "BA_PASSWORT", "IMAP_HOST"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    pfad = tmp_path / "config" / "profil.yaml"
    pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(pfad)
    lebenslauf = tmp_path / "unterlagen" / "lebenslauf.txt"
    lebenslauf.parent.mkdir()
    lebenslauf.write_text("Max Mustermann\nPython-Entwickler seit 2019\nPostgreSQL, Docker, Kubernetes\n"
                          "Teamlead Zahlungsplattform", encoding="utf-8")
    profil.unterlagen.lebenslauf = str(lebenslauf)
    profil.unterlagen.zeugnisse = []
    profil.automatisierung.probelauf_vorab = False
    speichere_profil(profil, pfad)
    db = Speicher(tmp_path / "daten" / "test.sqlite3")
    yield profil, db, pfad
    db.close()


def stelle(ref: str = "1", **kw) -> Job:
    daten = dict(titel="Backend Entwickler (m/w/d)", arbeitgeber="Musterfirma GmbH", ort="München",
                 beschreibung=ANZEIGE, externe_url=f"https://karriere.musterfirma.de/{ref}")
    daten.update(kw)
    return Job(ref=ref, **daten)


def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.de")
    monkeypatch.setenv("SMTP_BENUTZER", "max@example.de")
    monkeypatch.setenv("SMTP_PASSWORT", "geheim")


class FakeSmtp:
    def __init__(self):
        self.gesendet = []

    def __call__(self, nachricht, konfig):
        self.gesendet.append(nachricht)
        return nachricht["Message-ID"]


@pytest.fixture
def ki(monkeypatch):
    """Ein Fake-LLM: schreibt zuerst ein Anschreiben mit unbelegter Aussage, korrigiert es auf Nachfrage."""
    monkeypatch.setenv("OLLAMA_API_KEY", "test")
    aufrufe: list[str] = []

    def chat(modell, *, system, nutzer, max_tokens=4000, json_schema=None, effort="medium"):
        if json_schema is dossier.SCHEMA:
            aufrufe.append("dossier")
            return json.dumps({"muss": ["Erfahrung mit Python und PostgreSQL"], "kann": ["Terraform"],
                               "aufgaben": ["Backend-Services in Python"], "ansprechpartner": None,
                               "referenz": None, "frist": None, "unterlagen": [], "gehalt_gefragt": False,
                               "eintritt_gefragt": False, "sprache": "de"})
        if json_schema is pruefung.SCHEMA:
            aufrufe.append("pruefung")
            unbelegt = ["Mit Terraform automatisiere ich seit 2012"] if "Terraform" in nutzer else []
            return json.dumps({"qualitaet": 60 if unbelegt else 90, "unbelegt": unbelegt,
                               "fehlende_muss": [], "maengel": []})
        if json_schema is llm.SCHEMA:
            aufrufe.append("bewertung")
            return json.dumps({"passung": 90, "sentiment": 70, "treffer": ["Python"], "gruen": [], "rot": [],
                               "ausgeschlossen": False, "ausschlussgrund": None, "begruendung": "passt"})
        if "Ueberarbeite" in system:
            aufrufe.append("ueberarbeitung")
            return GUT
        aufrufe.append("anschreiben")
        return SCHLECHT

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(anschreiben, "chat", chat)

    class Bewerter:
        def __init__(self, profil):
            self.hinweis = None

        def bewerte(self, job):
            aufrufe.append("bewertung")
            return Score(ref=job.ref, gesamt=88, passung=90, sentiment=70, treffer=["Python"], bewerter="llm:test")

    monkeypatch.setattr(llm, "LLMBewerter", Bewerter)
    return aufrufe


# --------------------------------------------------------------------------- #
# Dossier & Pruefung
# --------------------------------------------------------------------------- #

def test_dossier_aus_regeln_mit_anrede_kennziffer_und_frist():
    d = dossier.aus_regeln(stelle())
    assert d.ansprechpartner == "Frau Dr. Erika Muster"
    assert d.anrede == "Sehr geehrte Frau Dr. Muster,"
    assert d.referenz == "2026-IT-17" and d.frist == "2099-11-30"
    assert "Erfahrung mit Terraform von Vorteil" in d.kann
    assert any("PostgreSQL" in m for m in d.muss)
    # Strukturierte Angaben der Quelle gewinnen
    d = dossier.erstelle(stelle(ansprechpartner="Herr Hans Huber"), None, mit_llm=False)
    assert d.anrede == "Sehr geehrter Herr Huber,"


def test_regelpruefung_findet_unbelegte_aussagen(umgebung):
    profil, _, _ = umgebung
    d = dossier.aus_regeln(stelle())
    gut = pruefung.regelpruefung(GUT, stelle(), profil, d)
    assert gut.unbelegt == [] and gut.maengel == [] and gut.qualitaet >= 80
    schlecht = pruefung.regelpruefung(SCHLECHT, stelle(), profil, d)
    assert any("Terraform" in u for u in schlecht.unbelegt)
    assert any("2012" in u for u in schlecht.unbelegt)
    assert schlecht.qualitaet < gut.qualitaet
    falsche_anrede = pruefung.regelpruefung(GUT.replace("Sehr geehrte Frau Dr. Muster,", "Hallo zusammen,"),
                                            stelle(), profil, d)
    assert any("Anrede" in m for m in falsche_anrede.maengel)


def test_anschreiben_wird_geprueft_und_ueberarbeitet(umgebung, ki):
    profil, _, _ = umgebung
    d = dossier.aus_regeln(stelle())
    ergebnis = anschreiben.erzeuge_geprueft(stelle(), profil, None, dossier=d)
    assert ergebnis.text == GUT and ergebnis.quelle == "llm"
    assert ergebnis.bericht["ueberarbeitet"] and not ergebnis.bericht["unbelegt"]
    assert ergebnis.qualitaet >= 80
    assert ki == ["anschreiben", "pruefung", "ueberarbeitung", "pruefung"]


def test_ohne_ki_vorlage_mit_persoenlicher_anrede(umgebung):
    profil, _, _ = umgebung
    ergebnis = anschreiben.erzeuge_geprueft(stelle(), profil, None, mit_llm=False, dossier=dossier.aus_regeln(stelle()))
    assert ergebnis.quelle == "vorlage" and ergebnis.text.startswith("Sehr geehrte Frau Dr. Muster,")
    assert ergebnis.qualitaet <= 50


# --------------------------------------------------------------------------- #
# Autopilot
# --------------------------------------------------------------------------- #

def _vorbereitet(db, profil, ki_score=True):
    db.speichere_jobs([stelle()])
    db.speichere_score(Score(ref="1", gesamt=88, passung=90, sentiment=70, bewerter="llm:test" if ki_score else "heuristik"))
    return ablauf.vorbereiten(db, profil, "1", herkunft="automatik", link_aufloesen=False)


def test_autopilot_aus_braucht_ja(umgebung, ki, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    v = _vorbereitet(db, profil)
    assert v.status == "wartet" and not v.auto_senden_ab
    assert v.kanal == "email" and v.empfaenger == "bewerbung@musterfirma.de"
    assert v.betreff.endswith("Kennziffer 2026-IT-17") and v.anschreiben.startswith("Sehr geehrte Frau Dr. Muster")


def test_autopilot_sofort_gibt_frei_wenn_alles_passt(umgebung, ki, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.autopilot = "sofort"
    v = _vorbereitet(db, profil)
    assert v.status == "freigegeben"
    assert db.entscheidungen()[0]["art"] == "autopilot"
    assert ablauf.sende_freigegebene(db, profil, mail_sender=FakeSmtp())["abgeschickt"] == 1


def test_autopilot_haelt_bei_schwacher_passung_zurueck(umgebung, ki, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.autopilot = "sofort"
    db.speichere_jobs([stelle()])
    db.speichere_score(Score(ref="1", gesamt=62, passung=60, sentiment=70, bewerter="llm:test"))
    v = ablauf.vorbereiten(db, profil, "1", herkunft="automatik", link_aufloesen=False)
    assert v.status == "wartet"
    assert any("Passung" in g for g in v.pruefung_daten["autopilot"]["gruende"])


def test_autopilot_ohne_smtp_und_ohne_ki_sendet_nie(umgebung):
    profil, db, _ = umgebung
    profil.automatisierung.autopilot = "sofort"
    v = _vorbereitet(db, profil)
    assert v.status == "wartet"
    gruende = v.pruefung_daten["autopilot"]["gruende"]
    assert any("Vorlage" in g for g in gruende) and any("E-Mail-Konto" in g for g in gruende)


def test_autopilot_mit_frist_und_stopp(umgebung, ki, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.autopilot = "mit_frist"
    profil.automatisierung.autopilot_frist_stunden = 2
    v = _vorbereitet(db, profil)
    assert v.status == "wartet" and v.auto_senden_ab
    jetzt = datetime.now(timezone.utc)
    assert autopilot.faellige_freigeben(db, profil, jetzt=jetzt) == 0
    assert autopilot.faellige_freigeben(db, profil, jetzt=jetzt + timedelta(hours=3)) == 1
    assert db.vorgang("1").status == "freigegeben"

    # Zweite Stelle desselben Arbeitgebers: Autopilot haelt zurueck (1 Bewerbung je Arbeitgeber in 30 Tagen)
    db.speichere_jobs([stelle("2", titel="Data Engineer")])
    db.speichere_score(Score(ref="2", gesamt=88, passung=90, sentiment=70, bewerter="llm:test"))
    v2 = ablauf.vorbereiten(db, profil, "2", herkunft="automatik", link_aufloesen=False)
    assert not v2.auto_senden_ab and any("Musterfirma" in g for g in v2.pruefung_daten["autopilot"]["gruende"])


def test_autopilot_stopp_verhindert_versand(umgebung, ki, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.autopilot = "mit_frist"
    _vorbereitet(db, profil)
    autopilot.stoppen(db, "1")
    spaeter = datetime.now(timezone.utc) + timedelta(days=1)
    assert autopilot.faellige_freigeben(db, profil, jetzt=spaeter) == 0
    assert db.vorgang("1").status == "wartet"


# --------------------------------------------------------------------------- #
# Lernen & Nachfassen
# --------------------------------------------------------------------------- #

def test_lernen_schlaegt_zeitarbeit_ausschluss_vor(umgebung):
    profil, db, pfad = umgebung
    profil.bewertung.ausschluss = []
    for i in range(3):
        db.speichere_jobs([stelle(f"z{i}", arbeitgeber=f"Personal {i} GmbH", titel="Lagerhelfer")])
        ablauf.ablehnen(db, profil, f"z{i}", grund="Zeitarbeit")
    assert lernen.leite_vorschlaege_ab(db, profil, mit_llm=False) == 1
    vorschlag = db.vorschlaege()[0]
    assert vorschlag["art"] == "ausschluss"
    assert lernen.wende_an(db, profil, vorschlag["id"], True, profil_pfad=str(pfad)).startswith("Übernommen")
    assert "Zeitarbeit" in lade_profil(pfad).bewertung.ausschluss
    # kein zweites Mal
    assert lernen.leite_vorschlaege_ab(db, lade_profil(pfad), mit_llm=False) == 0
    v = lernen.vorlieben(db)
    assert v.anpassung(Job(ref="x", titel="Lagerhelfer", arbeitgeber="Neu")) < 0


def test_nachfassen_im_selben_thread(umgebung, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    db.speichere_jobs([stelle()])
    vor_15_tagen = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(timespec="seconds")
    db.setze_vorgang(Vorgang(ref="1", status="abgeschickt", kanal="email", empfaenger="bewerbung@musterfirma.de",
                             betreff="Bewerbung als Backend Entwickler", gesendet_am=vor_15_tagen,
                             message_id="<bewerbung-1@example.de>"))
    assert nachfassen.pruefe(db, profil, mit_llm=False) == 1
    assert nachfassen.pruefe(db, profil, mit_llm=False) == 0  # keine Dublette
    vorschlag = db.vorschlaege()[0]
    assert vorschlag["art"] == "nachfassen" and "vor 15 Tagen" in vorschlag["text"]
    smtp = FakeSmtp()
    meldung = lernen.wende_an(db, profil, vorschlag["id"], True, mail_sender=smtp, text="Kurze Nachfrage.")
    assert "gesendet" in meldung
    mail = smtp.gesendet[0]
    assert mail["In-Reply-To"] == "<bewerbung-1@example.de>" and mail["Subject"].startswith("Re: ")
    assert mail.get_content().strip() == "Kurze Nachfrage."
    assert db.vorschlag(vorschlag["id"])["status"] == "erledigt"


# --------------------------------------------------------------------------- #
# Kanal, Kostenbremse, Tick
# --------------------------------------------------------------------------- #

def test_kanal_regeln():
    assert bestimme_kanal(Job(ref="a", titel="X", arbeitgeber="Y",
                              externe_url="https://www.stepstone.de/stellenangebote--x")).kanal == "manuell"
    ba = Job(ref="10001-1", titel="X", arbeitgeber="Y", quelle="arbeitsagentur",
             detail_url="https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1")
    assert bestimme_kanal(ba).kanal == "manuell"
    assert bestimme_kanal(ba, ba_login=True).kanal == "formular"
    partner = Job(ref="10001-2", titel="X", arbeitgeber="Y", quelle="arbeitsagentur",
                  detail_url="https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-2",
                  bewerbung_hinweis="Stelle über Partnerbörse (https://partner.example)")
    assert bestimme_kanal(partner, ba_login=True).kanal == "manuell"
    interamt = Job(ref="interamt:1", titel="X", arbeitgeber="Y", quelle="interamt",
                   externe_url="https://interamt.de/koop/app/stelle?id=1", kontakt_email="frage@amt.de",
                   bewerbung_hinweis="Bewerbung online über interamt.de (Konto bei Interamt nötig)")
    assert bestimme_kanal(interamt).kanal == "manuell"


def test_kostenbremse_und_dubletten(umgebung, ki):
    profil, db, _ = umgebung
    profil.bewertung.max_llm_pro_lauf = 2
    db.speichere_jobs([stelle(str(i), arbeitgeber=f"Firma {i}") for i in range(4)])
    fehler: list[str] = []
    scores = ablauf.bewerte_neue(db, profil, fehler=fehler)
    assert sum(1 for s in scores if s.bewerter.startswith("llm")) == 2
    assert any("Kostenbremse" in f for f in fehler)
    # Dieselbe Stelle aus einer anderen Quelle uebernimmt die Bewertung ohne neuen KI-Aufruf
    vorher = ki.count("bewertung")
    db.speichere_jobs([stelle("eures:1", arbeitgeber="Firma 0", quelle="eures")])
    ablauf.bewerte_neue(db, profil)
    assert ki.count("bewertung") == vorher and db.score("eures:1").gesamt == db.score("0").gesamt
    # Spaeter werden die nur per Stichworten bewerteten nachbewertet
    nach = ablauf.nachbewerten(db, profil, budget=5)
    assert nach and all(s.bewerter.startswith("llm") for s in nach)


def test_tick_sendet_auch_wenn_automatik_scheitert(umgebung, monkeypatch):
    profil, db, _ = umgebung
    smtp_env(monkeypatch)
    profil.automatisierung.cron = "0 6 * * *"

    def kaputt(*a, **k):
        raise RuntimeError("Jobbörse kaputt")

    monkeypatch.setattr(ablauf, "automatik_lauf", kaputt)
    db.speichere_jobs([stelle()])
    ablauf.vorbereiten(db, profil, "1", mit_llm=False, link_aufloesen=False)
    ablauf.freigeben(db, profil, "1")
    ergebnis = ablauf.tick(db, profil, jetzt=datetime(2026, 10, 11, 4, 2, tzinfo=timezone.utc), mit_llm=False,
                           mail_sender=FakeSmtp())
    assert "Jobbörse kaputt" in ergebnis["automatik_fehler"]
    assert ergebnis["senden"]["abgeschickt"] == 1


def test_probelauf_der_absendet_wird_nicht_wiederholt(umgebung):
    from bewerbungsagent.bewerben.browser import BewerbungsLauf

    profil, db, _ = umgebung

    class Browser:
        async def bewerbe(self, job, anschreiben, dry_run=True, start_url=None, anschreiben_pdf=None):
            return BewerbungsLauf(ref=job.ref, dry_run=True, abgeschickt=False, zusammenfassung="ACHTUNG",
                                  schritte=3, url=start_url, rohdaten={"versehentlich_abgeschickt": True})

    db.speichere_jobs([stelle(beschreibung="Bewerben Sie sich online.")])
    v = ablauf.vorbereiten(db, profil, "1", mit_llm=False, probelauf=True, link_aufloesen=False,
                           browser_fabrik=lambda p: Browser())
    assert v.status == "abgeschickt" and "Probelauf" in v.hinweis


def test_rohbestand_aus_feeds_ist_kein_kandidat(umgebung):
    profil, db, _ = umgebung
    db.speichere_jobs([stelle("feed-1", arbeitgeber="Fern GmbH", ort="Hamburg"),
                       stelle("feed-2", arbeitgeber="Nah GmbH")])
    roh = db.starte_abruf("arbeitnow", "(Feed)", {})
    db.merke_abruf_treffer(roh, ["feed-1", "feed-2"])
    db.beende_abruf(roh, treffer=2)
    treffer = db.starte_abruf("arbeitnow", "Backend Entwickler", {"lokal": True})
    db.merke_abruf_treffer(treffer, ["feed-2"])
    db.beende_abruf(treffer, treffer=1)
    # Nur der Suchtreffer wird bewertet und vorgeschlagen
    assert [j.ref for j in db.jobs(nur_ohne_score=True)] == ["feed-2"]
    for ref in ("feed-1", "feed-2"):
        db.speichere_score(Score(ref=ref, gesamt=90, passung=90, sentiment=60))
    assert [j.ref for j, _ in db.kandidaten(min_score=50)] == ["feed-2"]
    assert [j.ref for j, _ in db.bestenliste(min_score=50)] == ["feed-2"]


def test_erkundung_meidet_allgemeine_begriffe():
    assert ablauf._zu_allgemein("Engineering") and ablauf._zu_allgemein("Senior Manager")
    assert not ablauf._zu_allgemein("Data Engineer Python") and not ablauf._zu_allgemein("Softwareentwickler")


def test_unsinnige_suchbegriff_vorschlaege_werden_verworfen(umgebung):
    profil, db, _ = umgebung
    for begriff in ("Jobicy", "Engineering", "Fullstack-Entwickler"):
        lernen.schlage_suchbegriff_vor(db, begriff, 3)
    assert lernen.verwerfe_unsinnige_vorschlaege(db) == 2
    assert [v["daten"]["suchbegriff_hinzu"] for v in db.vorschlaege()] == ["Fullstack-Entwickler"]
