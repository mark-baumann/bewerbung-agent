from pathlib import Path

import pytest
import socket

from bewerbungsagent import mailer
from bewerbungsagent.config import (
    STANDARD_SUCHORT,
    SMTPSettings,
    Suche,
    ensure_seeded_profil,
    lade_profil,
    speichere_profil,
)
from bewerbungsagent.db import Speicher
from bewerbungsagent.models import Application, Job
from bewerbungsagent.scoring import heuristik, sentiment
from bewerbungsagent.sources.arbeitsagentur import _detail_anreichern, _job_aus_treffer

BEISPIEL_TREFFER = {
    "beruf": "Softwareentwickler/in",
    "titel": "Softwareentwickler Python (m/w/d)",
    "refnr": "10001-1003353506-S",
    "arbeitsort": {
        "plz": "10785",
        "ort": "Berlin",
        "strasse": "null",
        "region": "Berlin",
        "entfernung": "5",
    },
    "arbeitgeber": "Beispiel GmbH",
    "aktuelleVeroeffentlichungsdatum": "2026-07-08",
    "externeUrl": "https://karriere.beispiel.de/job/42",
}

BEISPIEL_DETAIL = {
    "stellenangebotsBeschreibung": (
        "Wir bieten einen unbefristeten Vertrag, Gleitzeit und 30 Tage Urlaub. "
        "Sie arbeiten mit Python, PostgreSQL und Kubernetes. Homeoffice moeglich."
    ),
    "homeofficemoeglich": True,
    "arbeitszeitVollzeit": True,
    "vertragsdauer": "UNBEFRISTET",
    "verguetungsangabe": "KEINE_ANGABEN",
}


def profil(tmp_path: Path):
    quelle = Path("config/profil.example.yaml").read_text(encoding="utf-8")
    ziel = tmp_path / "profil.yaml"
    ziel.write_text(quelle, encoding="utf-8")
    return lade_profil(ziel)


def test_treffer_normalisierung():
    job = _job_aus_treffer(BEISPIEL_TREFFER)
    assert job.ref == "10001-1003353506-S"
    assert job.ort == "Berlin"
    assert job.entfernung_km == 5.0
    # Die API liefert den String "null" statt eines fehlenden Werts.
    assert job.plz == "10785"
    assert job.bewerbungs_url == "https://karriere.beispiel.de/job/42"


def test_detail_anreicherung():
    job = _job_aus_treffer(BEISPIEL_TREFFER)
    _detail_anreichern(job, BEISPIEL_DETAIL)
    assert job.homeoffice is True
    assert job.befristung == "unbefristet"
    assert job.verguetung is None
    assert "Kubernetes" in (job.beschreibung or "")


def test_sentiment_erkennt_gute_bedingungen():
    gut = sentiment.analysiere(
        "Unbefristeter Vertrag nach Tarifvertrag, Gleitzeit, 30 Tage Urlaub, "
        "Weiterbildung und Homeoffice."
    )
    assert gut.wert > 70
    assert gut.label in ("positiv", "eher positiv")


def test_sentiment_erkennt_warnsignale():
    schlecht = sentiment.analysiere(
        "Du bist belastbar und bringst hohe Einsatzbereitschaft mit? In unserem "
        "jungen dynamischen Team mit Hands-on-Mentalitaet suchen wir Macher. "
        "Ueberstunden gehoeren dazu. Zunaechst befristet."
    )
    assert schlecht.wert < 30
    assert schlecht.label == "kritisch"
    assert any("belastbar" in r for r in schlecht.rot)


def test_sentiment_ohne_text_ist_neutral():
    assert sentiment.analysiere(None).wert == 50.0
    assert sentiment.analysiere("zu kurz").wert == 50.0


def test_wortgrenzen_verhindern_teiltreffer():
    assert heuristik.finde_begriffe("Erfahrung mit Java", ["Java"]) == ["Java"]
    assert heuristik.finde_begriffe("Erfahrung mit JavaScript", ["Java"]) == []
    assert heuristik.finde_begriffe("Wir nutzen C++ und C#", ["C++", "C#"]) == ["C++", "C#"]


def test_heuristik_bewertet_passende_stelle_hoch(tmp_path):
    p = profil(tmp_path)
    job = _job_aus_treffer(BEISPIEL_TREFFER)
    _detail_anreichern(job, BEISPIEL_DETAIL)
    score = heuristik.bewerte(job, p)
    assert not score.ausgeschlossen
    assert score.gesamt > 55
    assert "Python" in score.treffer


def test_heuristik_schliesst_ausschlussbegriffe_aus(tmp_path):
    p = profil(tmp_path)
    job = Job(
        ref="X-1",
        titel="Python Entwickler",
        arbeitgeber="Verleiher AG",
        beschreibung="Einsatz im Rahmen der Arbeitnehmerueberlassung bei unserem Kunden.",
    )
    score = heuristik.bewerte(job, p)
    assert score.ausgeschlossen
    assert "Arbeitnehmerueberlassung" in (score.ausschlussgrund or "")


def test_speicher_roundtrip(tmp_path):
    p = profil(tmp_path)
    job = _job_aus_treffer(BEISPIEL_TREFFER)
    _detail_anreichern(job, BEISPIEL_DETAIL)
    with Speicher(tmp_path / "db.sqlite3") as db:
        neu, akt = db.speichere_jobs([job])
        assert (neu, akt) == (1, 0)
        neu, akt = db.speichere_jobs([job])
        assert (neu, akt) == (0, 1)

        assert db.jobs(nur_ohne_score=True)
        db.speichere_score(heuristik.bewerte(job, p))
        assert not db.jobs(nur_ohne_score=True)

        gespeichert = db.job(job.ref)
        assert gespeichert is not None
        assert gespeichert.homeoffice is True
        assert db.scores_by_ref()[job.ref].gesamt == pytest.approx(db.score(job.ref).gesamt)

        beste = db.bestenliste(min_score=0, limit=5)
        assert beste and beste[0][0].ref == job.ref
        assert beste[0][1].treffer  # JSON-Spalten korrekt zurueckgelesen

        db.speichere_bewerbung(
            Application(ref=job.ref, status="abgeschickt", url=job.bewerbungs_url, dry_run=False)
        )
        assert db.schon_beworben(job.ref)
        # 'offen' blendet bereits beworbene Stellen aus
        assert db.bestenliste(min_score=0, limit=5, offen=True) == []
        assert db.statistik()["abgeschickt"] == 1


def test_profil_meldet_unbekannte_felder(tmp_path):
    (tmp_path / "profil.yaml").write_text(
        "person:\n  vorname: A\n  quatsch: 1\nsuche:\n  was: [x]\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="quatsch"):
        lade_profil(tmp_path / "profil.yaml")


def test_erlaubte_domains_bleiben_eng():
    pytest.importorskip("browser_use")
    from bewerbungsagent.bewerben.browser import _erlaubte_domains

    domains = _erlaubte_domains("https://karriere.beispiel.de/job/42")
    assert "karriere.beispiel.de" in domains
    assert "*.beispiel.de" in domains
    assert "arbeitsagentur.de" in domains
    assert not any("boese" in d for d in domains)


def test_seeded_profile_erfindet_keine_personendaten(tmp_path):
    profile = ensure_seeded_profil(tmp_path / "profil.yaml")
    assert profile.person.name == ""
    assert profile.person.ort == ""
    assert profile.suche.was == []
    assert profile.unterlagen.lebenslauf == ""


def test_standard_suchort_ist_muenchen():
    assert STANDARD_SUCHORT == "München"
    assert Suche().wo == "München"


def test_job_zeigt_quellenname_und_icon():
    job = Job(ref="source", titel="Engineer", arbeitgeber="Beispiel")
    assert job.quelle_label == "Bundesagentur für Arbeit"
    assert job.quelle_icon == "🏛️"


def test_smtp_versand_enthaelt_anschreiben_und_profilanhaenge(tmp_path, monkeypatch):
    profile = profil(tmp_path)
    profile.smtp = SMTPSettings(
        host="smtp.example.test",
        sender_email="bewerber@example.test",
    )
    resume = tmp_path / "lebenslauf.pdf"
    resume.write_bytes(b"resume")
    reference = tmp_path / "zeugnis.pdf"
    reference.write_bytes(b"reference")
    profile.unterlagen.lebenslauf = str(resume)
    profile.unterlagen.zeugnisse = [str(reference)]

    class FakeSMTP:
        instance = None

        def __init__(self, *args, **kwargs):
            self.__class__.instance = self

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def send_message(self, message, **kwargs):
            self.message = message
            self.recipients = kwargs.get("to_addrs")
            return getattr(self, "refused", {})

        def close(self):
            self.closed = True

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer, "_smtp_passwort", lambda _profile: None)

    mailer.sende_bewerbung(
        profile,
        Job(ref="job-1", titel="Python Entwickler", arbeitgeber="Beispiel GmbH"),
        "jobs@example.test",
        "Guten Tag,\n\nhiermit bewerbe ich mich.",
    )

    message = FakeSMTP.instance.message
    assert message["To"] == "jobs@example.test"
    assert message["Bcc"] == "kontakt@markb.de"
    assert FakeSMTP.instance.recipients == ["jobs@example.test", "kontakt@markb.de"]
    assert "hiermit bewerbe ich mich" in message.get_body().get_content()
    assert {part.get_filename() for part in message.iter_attachments()} == {
        "lebenslauf.pdf",
        "zeugnis.pdf",
    }
    assert FakeSMTP.instance.closed


def test_smtp_versand_meldet_abgelehnten_bcc_empfaenger(tmp_path, monkeypatch):
    profile = profil(tmp_path)
    profile.smtp = SMTPSettings(
        host="smtp.example.test",
        sender_email="bewerber@example.test",
    )

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def send_message(self, message, **kwargs):
            return {"kontakt@markb.de": (550, b"Mailbox rejected")}

        def close(self):
            self.closed = True

    fake_server = FakeSMTP()
    monkeypatch.setattr(mailer.smtplib, "SMTP", lambda *args, **kwargs: fake_server)
    monkeypatch.setattr(mailer, "_smtp_passwort", lambda _profile: None)

    with pytest.raises(mailer.smtplib.SMTPRecipientsRefused):
        mailer.sende_bewerbung(
            profile,
            Job(ref="job-1", titel="Python Entwickler", arbeitgeber="Beispiel GmbH"),
            "jobs@example.test",
            "Anschreiben",
        )

    assert fake_server.closed


def test_smtp_rejects_invalid_recipient_before_connecting(tmp_path, monkeypatch):
    profile = profil(tmp_path)
    profile.smtp = SMTPSettings(
        host="smtp.example.test",
        sender_email="bewerber@example.test",
    )
    monkeypatch.setattr(
        mailer.smtplib,
        "SMTP",
        lambda *args, **kwargs: pytest.fail("SMTP must not be opened for an invalid recipient"),
    )

    with pytest.raises(ValueError, match="Empfänger"):
        mailer.sende_bewerbung(
            profile,
            Job(ref="job-1", titel="Python Entwickler", arbeitgeber="Beispiel GmbH"),
            "not-an-email",
            "Anschreiben",
        )


def test_smtp_dns_error_explains_invalid_server_name(tmp_path, monkeypatch):
    profile = profil(tmp_path)
    profile.smtp = SMTPSettings(
        host="typo.example.test",
        sender_email="bewerber@example.test",
    )

    class UnresolvableSMTP:
        def __init__(self, *args, **kwargs):
            raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(mailer.smtplib, "SMTP", UnresolvableSMTP)
    with pytest.raises(RuntimeError, match="SMTP-Server.*DNS nicht auffindbar"):
        mailer.teste_verbindung(profile)


def test_smtp_settings_are_persisted_without_password(tmp_path):
    profile_path = tmp_path / "profil.yaml"
    profile = ensure_seeded_profil(profile_path)
    profile.smtp = SMTPSettings(
        host="smtp.example.test",
        port=465,
        username="bewerber@example.test",
        sender_email="bewerber@example.test",
        starttls=False,
        use_ssl=True,
    )

    speichere_profil(profile, profile_path)
    loaded = lade_profil(profile_path)
    contents = profile_path.read_text(encoding="utf-8")

    assert loaded.smtp == profile.smtp
    assert "password" not in contents.lower()


def test_automated_searches_and_inbox_roundtrip(tmp_path):
    with Speicher(tmp_path / "db.sqlite3") as db:
        db.ensure_automated_searches([
            {"name": "Python @ Berlin", "query": "Python Developer", "location": "Berlin", "radius_km": 25, "interval_minutes": 180}
        ])
        rows = db.automated_searches()
        assert rows[0]["query"] == "Python Developer"

        db.ensure_automated_searches([
            {"name": "Python @ Hamburg", "query": "Python Developer", "location": "Hamburg", "radius_km": 40, "interval_minutes": 360}
        ])
        rows = db.automated_searches()
        active_rows = [row for row in rows if row["enabled"]]
        assert len(active_rows) == 1
        assert active_rows[0]["location"] == "Hamburg"

        job = Job(
            ref="A-42",
            titel="Python Developer",
            arbeitgeber="ACME GmbH",
            ort="Berlin",
            detail_url="https://example.de/jobs/A-42",
        )
        item = db.add_inbox_item(job, "Python Developer", score=88.0)
        assert item["ref"] == "A-42"
        assert db.inbox()[0]["title"] == "Python Developer"

        db.record_search_run(rows[0]["id"])
        updated = db.automated_searches()[0]
        assert updated["last_run_at"]
        assert updated["next_run_at"]


# --------------------------------------------------------------------------- #
# Browser-Agent: Konfiguration pruefen, ohne einen Browser zu starten
# --------------------------------------------------------------------------- #

def _browser_job() -> Job:
    job = _job_aus_treffer(BEISPIEL_TREFFER)
    _detail_anreichern(job, BEISPIEL_DETAIL)
    return job


def _agent(tmp_path, monkeypatch, dry_run: bool, **kwargs):
    pytest.importorskip("browser_use")
    from bewerbungsagent.bewerben.browser import BewerbungsBrowser

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    browser = BewerbungsBrowser(profil=profil(tmp_path), headless=True, **kwargs)
    return browser.baue_agent(_browser_job(), "Sehr geehrte Damen und Herren, ...", dry_run=dry_run)


def test_agent_probelauf_verbietet_absenden(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, dry_run=True)
    aufgabe = agent.task
    assert "PROBELAUF" in aufgabe
    assert "Klicke NICHT auf den finalen" in aufgabe
    assert "ABSENDEN: Pruefe alle Eingaben" not in aufgabe


def test_agent_absendemodus_erlaubt_absenden(tmp_path, monkeypatch):
    aufgabe = _agent(tmp_path, monkeypatch, dry_run=False).task
    assert "ABSENDEN: Pruefe alle Eingaben" in aufgabe
    assert "PROBELAUF" not in aufgabe


def test_agent_bekommt_profildaten_und_zieldomain(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, dry_run=True)
    assert "max.mustermann@example.de" in agent.task
    assert "Erfinde niemals Angaben" in agent.task
    erlaubt = agent.browser_profile.allowed_domains
    assert "karriere.beispiel.de" in erlaubt
    assert "*.arbeitsagentur.de" in erlaubt


def test_agent_ohne_zugangsdaten_ohne_sensitive_data(tmp_path, monkeypatch):
    monkeypatch.delenv("BA_BENUTZER", raising=False)
    monkeypatch.delenv("BA_PASSWORT", raising=False)
    agent = _agent(tmp_path, monkeypatch, dry_run=True)
    assert not agent.sensitive_data


def test_agent_haelt_zugangsdaten_aus_dem_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("BA_BENUTZER", "nutzer@example.de")
    monkeypatch.setenv("BA_PASSWORT", "geheim123")
    agent = _agent(tmp_path, monkeypatch, dry_run=True)
    # Platzhalter im Prompt, echte Werte nur in sensitive_data
    assert "geheim123" not in agent.task
    assert "ba_passwort" in agent.task
    assert agent.sensitive_data["https://*.arbeitsagentur.de"]["ba_passwort"] == "geheim123"


def test_domains_offen_hebt_beschraenkung_auf(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, dry_run=True, domains_offen=True)
    assert agent.browser_profile.allowed_domains is None


def test_agent_ohne_bewerbungs_url_meldet_fehler(tmp_path, monkeypatch):
    pytest.importorskip("browser_use")
    from bewerbungsagent.bewerben.browser import BewerbungsBrowser

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    browser = BewerbungsBrowser(profil=profil(tmp_path), headless=True)
    ohne_url = Job(ref="X-2", titel="Test", arbeitgeber="Test AG")
    with pytest.raises(ValueError, match="keine Bewerbungs-URL"):
        browser.baue_agent(ohne_url, "Text", dry_run=True)
