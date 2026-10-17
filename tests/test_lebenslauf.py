"""Lebenslauf als Quelle: Auslesen (KI/Heuristik), einmal je Inhalt, Uebernahme ins Profil, Anschreiben."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bewerbungsagent import anschreiben, dokumente, lebenslauf
from bewerbungsagent.config import lade_profil, speichere_profil
from bewerbungsagent.models import Job, Score
from bewerbungsagent.scoring import llm

REPO = Path(__file__).resolve().parent.parent

# Aufbau wie ein zweispaltiger PDF-Lebenslauf (Abschnitte, Name am Ende) – erfundene Daten
TEXT = """Über mich
Mit umfassender Erfahrung in der
Digitalisierung von Geschäftsprozessen
sowie in der Softwareentwicklung bringe ich
fundierte Praxiskenntnisse mit.
Fähigkeiten
SQL
Python
Projektmanagement
IT-Projektmanagement
Produktmanagement
Ausbildung
Fachschule Musterstadt
2020 - 2023
Fachabitur IT
Persönliche Infos
Musterweg 13
85256 Giebing
Deutschland
max.beispiel@example.org
05/08/2000
Erfahrung
Augustinum 04/2026 - 09/2026
Digitalisierung von Geschäftsprozessen
Muster Software GmbH 03/2023 - 03/2026
Produktverantwortlicher
Micro Demo 08/2022 - 03/2023
Programmierer
Max
Beispiel
Lebenslauf"""

KI_ANTWORT = {
    "kurzprofil": "Produktverantwortlicher mit Erfahrung in Digitalisierung, Python und SQL.",
    "jobtitel": ["Produktmanager (m/w/d)", "IT-Projektmanager", "Prozessmanager Digitalisierung",
                 "Softwareentwickler Python", "it-projektmanager"],
    "ort": "85256 Giebing",
    "suchort": "München",
    "faehigkeiten": ["Python", "SQL", "Projektmanagement", "Produktmanagement"],
    "stationen": [{"rolle": "Digitalisierung von Geschäftsprozessen", "arbeitgeber": "Augustinum",
                   "zeitraum": "04/2026 - 09/2026"},
                  {"rolle": "Produktverantwortlicher", "arbeitgeber": "Muster Software GmbH",
                   "zeitraum": "03/2023 - 03/2026"}],
    "kontakt": {"vorname": "Max", "nachname": "Beispiel", "email": "max.beispiel@example.org",
                "telefon": "05/08/2000", "strasse": "Musterweg 13", "plz": "85256", "ort": "Giebing"},
}


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_API_KEY", "BEWERBUNGSAGENT_DB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    pfad = tmp_path / "config" / "profil.yaml"
    pfad.write_text((REPO / "config" / "profil.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    profil = lade_profil(pfad)
    datei = tmp_path / "unterlagen" / "lebenslauf.txt"
    datei.parent.mkdir()
    datei.write_text(TEXT, encoding="utf-8")
    profil.unterlagen.lebenslauf = str(datei)
    profil.person.telefon, profil.person.strasse = "", ""  # sollen aus dem Lebenslauf kommen
    profil.suche.was, profil.suche.wo, profil.suche.umkreis = ["Softwareentwickler Python"], "Köln", 25
    profil.bewertung.skills = []
    speichere_profil(profil, pfad)
    return profil, pfad, datei


def _ki(monkeypatch, antwort=KI_ANTWORT):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    aufrufe = []

    def chat(modell, **kw):
        aufrufe.append(kw)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort if isinstance(antwort, str) else json.dumps(antwort)

    monkeypatch.setattr(llm, "chat", chat)
    return aufrufe


# --------------------------------------------------------------------------- #
# Heuristik
# --------------------------------------------------------------------------- #

def test_heuristik_liest_abschnitte_statt_nur_kontaktdaten():
    a = lebenslauf.heuristik(TEXT)
    assert a.kurzprofil.startswith("Mit umfassender Erfahrung in der Digitalisierung")
    assert a.faehigkeiten == ["SQL", "Python", "Projektmanagement", "IT-Projektmanagement", "Produktmanagement"]
    # „Programmierer“ bei „Micro Demo“: ohne Rechtsform ist die Zuordnung nicht sicher – kein Jobtitel daraus
    assert a.jobtitel == ["Produktverantwortlicher", "Projektmanager", "IT-Projektmanager", "Produktmanager"]
    assert [(s["arbeitgeber"], s["sicher"]) for s in a.stationen] == [
        ("Augustinum", False), ("Muster Software GmbH", True), ("Micro Demo", False)]
    assert a.kontakt == {"email": "max.beispiel@example.org", "plz": "85256", "ort": "Giebing",
                         "strasse": "Musterweg 13", "vorname": "Max", "nachname": "Beispiel"}
    assert "telefon" not in a.kontakt  # das Geburtsdatum ist keine Telefonnummer


@pytest.mark.parametrize("zeile, erwartet", [
    ("Tel.: 0151 2345 6789", {"telefon": "0151 2345 6789"}),
    ("Mobil +49 171 1234567", {"telefon": "+49 171 1234567"}),
    ("01/2015 - 03/2020", {}),
    ("Umsatzverantwortung 50000 Euro", {}),
    ("Mechanical Engineering 2010", {}),
    ("Leopoldstraße 12a, 80802 München", {"plz": "80802", "ort": "München", "strasse": "Leopoldstraße 12a"}),
])
def test_kontaktmuster_ohne_fehlalarme(zeile, erwartet):
    assert lebenslauf.heuristik(zeile).kontakt == erwartet


def test_rolle_und_arbeitgeber_werden_nicht_vertauscht():
    text = ("Berufserfahrung\n03/2019 - heute Verkäuferin\nRossmann, Hannover\n"
            "2015 - 2019 Deutsche Bahn\nZugbegleiter\n")
    a = lebenslauf.heuristik(text)
    assert [(s["rolle"], s["arbeitgeber"], s["sicher"]) for s in a.stationen] == [
        ("Verkäuferin", "Rossmann, Hannover", True), ("Zugbegleiter", "Deutsche Bahn", False)]
    assert a.jobtitel == ["Verkäuferin"]  # nie „Rossmann“ oder „Deutsche Bahn“ als Jobtitel
    assert lebenslauf.stationssatz(a) == "Derzeit bin ich als Verkäuferin bei Rossmann in Hannover tätig."


def test_aufsteigender_lebenslauf_und_offene_zeitraeume():
    text = ("Berufserfahrung\nAlpha GmbH 2010 - 2014\nSachbearbeiter\nBeta AG seit 03/2019\nTeamleiter\n"
            "Gamma GmbH 2014 - 2019\nReferent\n")
    a = lebenslauf.heuristik(text)
    assert [s["arbeitgeber"] for s in a.stationen] == ["Beta AG", "Gamma GmbH", "Alpha GmbH"]
    assert a.jobtitel == ["Teamleiter", "Referent", "Sachbearbeiter"]
    assert lebenslauf.stationssatz(a) == ("Derzeit bin ich als Teamleiter bei Beta AG tätig, davor war ich als "
                                          "Referent bei Gamma GmbH.")
    for offen in ("seit 2019", "since 2019", "03/2019 bis dato", "03/2019 – ", "ab 01/2024", "2019 - heute"):
        assert lebenslauf._laeuft_noch(offen), offen
    assert not lebenslauf._laeuft_noch("2019 - 2021")


def test_soft_skills_werden_keine_jobtitel():
    faehigkeiten = ["Zeitmanagement", "Selbstmanagement", "Konfliktmanagement", "Management", "Change Management",
                    "Time Management", "Persönlichkeitsentwicklung", "Qualitätsmanagement", "Softwareentwicklung"]
    assert lebenslauf._titel_aus([], faehigkeiten) == ["Qualitätsmanager", "Softwareentwickler"]


def test_name_und_kontaktzeilen_sind_keine_faehigkeiten(umgebung):
    profil, _, _ = umgebung
    profil.person.vorname, profil.person.nachname = "Erika", "Probe"
    text = "Fähigkeiten\nPython\nSoftware:\nExcel\nTelefon:\n0151 2345 6789\nErika\nProbe"
    a = lebenslauf.ableiten(profil, text, mit_ki=False)
    assert a.faehigkeiten == ["Python", "Excel"] and a.kontakt["telefon"] == "0151 2345 6789"


@pytest.mark.parametrize("zeile", ["-" * 60, "A" + "-a" * 40, "Muster-" * 11 + "weg", "Ab " * 26 + "Ring",
                                   "x" * 3000, "Mustermann-Schmidt-Müller-Lüdenscheid-" * 2 + "-" * 34 + " 1"])
def test_strassenmuster_haengt_nicht(zeile):
    import time

    start = time.perf_counter()
    lebenslauf.heuristik(f"Persönliche Infos\n{zeile}\nMusterweg 13\n85256 Giebing")
    assert time.perf_counter() - start < 1


def test_ueberschriften_sind_kein_name():
    a = lebenslauf.heuristik("ANNA SCHMIDT\nBeruflicher Werdegang\nFähigkeiten\nPython\nExcel")
    assert "vorname" not in a.kontakt


# --------------------------------------------------------------------------- #
# Uebernehmen
# --------------------------------------------------------------------------- #

def test_ki_auswertung_wird_uebernommen_mit_suchort_und_nur_einmal_ausgelesen(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    aufrufe = _ki(monkeypatch)
    vorher = (profil.person.vorname, profil.person.email)
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "ki" and len(aufrufe) == 1 and aufrufe[0]["max_tokens"] >= 8000
    assert {"Kurzprofil", "Jobtitel", "Fähigkeiten", "Ort", "Kontaktdaten"} <= set(uebernommen)
    gespeichert = lade_profil(pfad)
    # Titel aus dem Lebenslauf zuerst, der eigene Titel bleibt dahinter
    assert gespeichert.suche.was == ["Produktmanager", "IT-Projektmanager", "Prozessmanager Digitalisierung",
                                     "Softwareentwickler Python"]
    assert (gespeichert.suche.wo, gespeichert.suche.umkreis) == ("München", lebenslauf.UMKREIS_SUCHORT)
    assert gespeichert.bewertung.skills == ["Python", "SQL", "Projektmanagement", "Produktmanagement"]
    assert gespeichert.kurzprofil.startswith("Produktverantwortlicher")
    assert (gespeichert.person.vorname, gespeichert.person.email) == vorher  # vorhandene bleiben
    assert gespeichert.person.strasse == "Musterweg 13" and gespeichert.person.telefon == ""  # kein Datum
    assert a.ort == "Giebing" and a.stationen[1]["rolle"] == "Produktverantwortlicher"

    # Gleicher Inhalt: keine neue Anfrage
    assert lebenslauf.aktualisieren(gespeichert, pfad)[1] == [] and len(aufrufe) == 1


def test_erneutes_auslesen_laesst_aenderungen_des_nutzers_stehen(umgebung, monkeypatch):
    profil, pfad, datei = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    # Der Nutzer aendert Ort und Jobtitel unter „Suchen“, dann kommt ein neuer Lebenslauf
    profil.suche.wo = "Augsburg"
    profil.suche.was = [*profil.suche.was, "Scrum Master"]
    speichere_profil(profil, pfad)
    datei.write_text(TEXT + "\nZertifikate\nScrum", encoding="utf-8")
    _, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert profil.suche.wo == "Augsburg" and "Ort" not in uebernommen
    assert profil.suche.was[-1] == "Scrum Master" and profil.suche.was[0] == "Produktmanager"
    # „Neu auslesen“ darf ueberschreiben
    lebenslauf.aktualisieren(profil, pfad, erzwingen=True)
    assert profil.suche.wo == "München"


def test_umkreis_und_leerer_ort_des_nutzers_bleiben(umgebung, monkeypatch):
    profil, pfad, datei = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    assert (profil.suche.wo, profil.suche.umkreis) == ("München", lebenslauf.UMKREIS_SUCHORT)
    profil.suche.umkreis = 80  # nur den Umkreis geaendert
    datei.write_text(TEXT + "\nZertifikate\nScrum", encoding="utf-8")
    lebenslauf.aktualisieren(profil, pfad)
    assert (profil.suche.wo, profil.suche.umkreis) == ("München", 80)
    profil.suche.wo = ""  # bewusst ganz Deutschland
    datei.write_text(TEXT + "\nZertifikate\nScrum, Kanban", encoding="utf-8")
    _, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert profil.suche.wo == "" and "Ort" not in uebernommen


def test_eigene_jobtitel_werden_nie_abgeschnitten(umgebung, monkeypatch):
    profil, pfad, datei = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    eigene = [f"Eigener Titel {i}" for i in range(lebenslauf.MAX_JOBTITEL)]
    profil.suche.was = [*profil.suche.was, *eigene]
    datei.write_text(TEXT + "\nZertifikate\nScrum", encoding="utf-8")
    lebenslauf.aktualisieren(profil, pfad)
    assert profil.suche.was[:2] == ["Produktmanager", "IT-Projektmanager"] and profil.suche.was[-10:] == eigene


def test_ohne_ki_wohnort_mit_grossem_umkreis_und_titel_aus_den_abschnitten(umgebung):
    profil, pfad, _ = umgebung
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "heuristik" and a.fehler == ""  # kein KI-Zugang eingerichtet
    assert profil.suche.was[:2] == ["Produktverantwortlicher", "Projektmanager"]
    assert (profil.suche.wo, profil.suche.umkreis) == ("Giebing", lebenslauf.UMKREIS_WOHNORT)
    assert "Python" in profil.bewertung.skills and profil.kurzprofil.startswith("Mit umfassender")


def test_ki_fehler_steht_mit_grund_da(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    _ki(monkeypatch, llm.LLMNichtVerfuegbar("Error code: 429 - insufficient_quota credit_balance_exhausted"))
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "heuristik" and a.fehler == "das KI-Konto hat kein Guthaben mehr"
    assert profil.suche.was[0] == "Produktverantwortlicher"  # trotzdem nicht „schwach“


@pytest.mark.parametrize("antwort", ["", "Gerne! Leider kann ich das nicht.", {"kurzprofil": "", "jobtitel": []}])
def test_leere_ki_antwort_ist_kein_ki_ergebnis(umgebung, monkeypatch, antwort):
    profil, pfad, _ = umgebung
    _ki(monkeypatch, antwort)
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert a.quelle == "heuristik" and a.fehler


def test_ki_fehler_behaelt_fruehere_ki_auswertung(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    _ki(monkeypatch, llm.LLMNichtVerfuegbar("Error code: 429 - insufficient_quota"))
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad, erzwingen=True)
    assert a.quelle == "ki" and uebernommen == [] and profil.suche.wo == "München"


def test_ohne_ki_gelesen_wird_spaeter_einmal_mit_ki_wiederholt(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    lebenslauf.aktualisieren(profil, pfad)  # ohne KI-Zugang
    aufrufe = _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    assert aufrufe == []  # nicht bei jedem Seitenaufruf
    alt = lebenslauf.gespeichert()
    alt.zeit = (datetime.now(timezone.utc) - timedelta(hours=lebenslauf.NEU_VERSUCHEN_STUNDEN + 1)).isoformat()
    lebenslauf._speichern(alt)
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert len(aufrufe) == 1 and a.quelle == "ki" and profil.suche.wo == "München"


def test_alte_auswertung_wird_nach_update_neu_gelesen_und_falsches_telefon_repariert(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    # So sah es auf dem Pi aus: nur Kontaktdaten, Geburtsdatum als Telefon, Ort Giebing (Version 1)
    profil.person.telefon, profil.suche.wo = "05/08/2000", "Giebing"
    speichere_profil(profil, pfad)
    alt = lebenslauf.Ableitung(ort="Giebing", kontakt={"telefon": "05/08/2000"}, quelle="heuristik",
                               pruefsumme=lebenslauf.pruefsumme(dokumente.lebenslauf_text(profil)),
                               zeit=datetime.now(timezone.utc).isoformat())
    lebenslauf._speichern(alt)
    aufrufe = _ki(monkeypatch)
    a, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert len(aufrufe) == 1 and a.version == lebenslauf.VERSION
    assert profil.person.telefon == "" and "Telefon korrigiert" in uebernommen
    assert profil.suche.wo == "München" and profil.suche.was[0] == "Produktmanager"


def test_ohne_lesbaren_text_keine_auswertung(umgebung):
    profil, pfad, datei = umgebung
    datei.write_text("   ", encoding="utf-8")
    assert lebenslauf.aktualisieren(profil, pfad) == (None, [])


def test_word_lebenslauf_ist_lesbar(umgebung):
    profil, _, datei = umgebung
    docx = datei.with_suffix(".docx")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Mark Beispiel</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>80802 M&#252;nchen</w:t></w:r></w:p></w:body></w:document>")
    profil.unterlagen.lebenslauf = str(docx)
    assert dokumente.lebenslauf_text(profil) == "Mark Beispiel\n80802 München"


# --------------------------------------------------------------------------- #
# Fuer das Anschreiben
# --------------------------------------------------------------------------- #

def test_vorlage_nutzt_werdegang_und_passende_faehigkeiten(umgebung):
    profil, pfad, _ = umgebung
    lebenslauf.aktualisieren(profil, pfad)  # ohne KI
    job = Job(ref="1", titel="Produktmanager", arbeitgeber="Demo AG", beschreibung="Python, SQL, Jira")
    score = Score(ref="1", gesamt=80, passung=80, sentiment=50, treffer=["Python", "SQL", "Jira"])
    ergebnis = anschreiben.erzeuge_geprueft(job, profil, score, mit_llm=False)
    assert ergebnis.quelle == "vorlage"
    # Die neueste Station (Augustinum) ist ohne KI nicht sicher zugeordnet: nur die sichere, ohne „Zuletzt“
    assert "Unter anderem war ich als Produktverantwortlicher bei Muster Software GmbH tätig." in ergebnis.text
    assert "Augustinum" not in ergebnis.text
    assert "Python, SQL." in ergebnis.text and "Jira" not in ergebnis.text  # nur, was im Lebenslauf steht


def test_vorlage_nennt_den_ki_grund(umgebung, monkeypatch):
    profil, _, _ = umgebung
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")

    def kaputt(*a, **k):
        raise llm.LLMNichtVerfuegbar("Error code: 429 - insufficient_quota")

    monkeypatch.setattr(anschreiben, "chat", kaputt)
    ergebnis = anschreiben.erzeuge_geprueft(Job(ref="1", titel="T", arbeitgeber="A"), profil, None)
    assert ergebnis.quelle == "vorlage" and ergebnis.bericht["ki_fehler"] == "das KI-Konto hat kein Guthaben mehr"


def test_laufende_station_heisst_derzeit():
    a = lebenslauf.Ableitung(stationen=[
        {"rolle": "Werkstudent", "arbeitgeber": "Uni X", "zeitraum": "2020 - 2023", "sicher": True},
        {"rolle": "Entwickler", "arbeitgeber": "Demo AG", "zeitraum": "seit 03/2024", "sicher": True}])
    assert lebenslauf.stationssatz(a) == ("Derzeit bin ich als Entwickler bei Demo AG tätig, davor war ich als "
                                          "Werkstudent bei Uni X.")


def test_ki_vorlage_mit_ki_stationen(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    _ki(monkeypatch)
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert all(s["sicher"] for s in a.stationen)  # steht so im Lebenslauf
    assert lebenslauf.stationssatz(profil=profil) == (
        "Zuletzt war ich bei Augustinum im Bereich Digitalisierung von Geschäftsprozessen tätig, davor als "
        "Produktverantwortlicher bei Muster Software GmbH.")


def test_erfundene_station_der_ki_ist_nicht_sicher(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    erfunden = {"rolle": "Head of Product", "arbeitgeber": "Weltkonzern AG", "zeitraum": "2026 - heute"}
    _ki(monkeypatch, {**KI_ANTWORT, "stationen": [erfunden, *KI_ANTWORT["stationen"]]})
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert [s["sicher"] for s in a.stationen] == [False, True, True]
    satz = lebenslauf.stationssatz(profil=profil)
    assert "Weltkonzern" not in satz and satz.startswith("Unter anderem war ich bei Augustinum")


def test_ohne_ki_stationen_keine_geratenen_aus_der_heuristik(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    _ki(monkeypatch, {**KI_ANTWORT, "stationen": []})
    a, _ = lebenslauf.aktualisieren(profil, pfad)
    assert a.stationen == [] and lebenslauf.stationssatz(profil=profil) == ""


def test_neuer_unlesbarer_lebenslauf_liefert_keinen_alten_werdegang(umgebung, monkeypatch):
    profil, pfad, datei = umgebung
    _ki(monkeypatch)
    lebenslauf.aktualisieren(profil, pfad)
    assert lebenslauf.stationssatz(profil=profil)
    datei.write_text("", encoding="utf-8")  # z. B. ein eingescanntes PDF ohne Text
    assert lebenslauf.aktualisieren(profil, pfad) == (None, [])
    assert lebenslauf.stationssatz(profil=profil) == "" and anschreiben._werdegang(profil) == ""


def test_themen_werden_wunschthemen_der_bewertung(umgebung, monkeypatch):
    profil, pfad, _ = umgebung
    profil.bewertung.wunsch = ["Open Source"]
    speichere_profil(profil, pfad)
    _ki(monkeypatch, {**KI_ANTWORT, "themen": ["Digitalisierung", "Prozessautomatisierung"]})
    _, uebernommen = lebenslauf.aktualisieren(profil, pfad)
    assert "Themen" in uebernommen
    assert lade_profil(pfad).bewertung.wunsch == ["Digitalisierung", "Prozessautomatisierung", "Open Source"]


def test_ki_fehlertext_vom_server_bleibt_erhalten(monkeypatch):
    import httpx

    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    antwort = {"error": {"message": "You have no credits remaining.", "type": "insufficient_quota"}}
    client = llm.OllamaClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, json=antwort)))
    with pytest.raises(llm.LLMNichtVerfuegbar) as fehler:
        client.chat(modell="gpt-oss:120b", system="S", messages=[], max_tokens=50)
    assert llm.ki_grund(fehler.value) == "das KI-Konto hat kein Guthaben mehr"
