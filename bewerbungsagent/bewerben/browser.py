"""Bewerbung tatsaechlich abschicken - gesteuert durch browser-use.

Der Agent oeffnet die Bewerbungsseite, fuellt das Formular aus den Profildaten,
haengt die Unterlagen an und schickt ab. Standard ist der Probelauf
(`dry_run=True`): alles wird ausgefuellt, aber der letzte Absende-Klick
unterbleibt.

browser-use ist ein optionales Extra:  pip install -e ".[browser]"
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import Profil
from ..models import Application, Job

log = logging.getLogger(__name__)

# Portale leiten haeufig auf externe Bewerbermanagementsysteme weiter.
STANDARD_DOMAINS = [
    "*.arbeitsagentur.de",
    "arbeitsagentur.de",
]

# Verbreitete Bewerbermanagementsysteme (ATS), auf die Karriereseiten weiterleiten.
# Ohne sie bricht der Agent an der Domain-Schranke ab, sobald "Jetzt bewerben"
# auf das System des Arbeitgebers springt.
ATS_DOMAINS = [
    "softgarden.io", "softgarden.de", "personio.de", "personio.com", "jobs.personio.de",
    "myworkdayjobs.com", "myworkdaysite.com", "successfactors.com", "successfactors.eu",
    "sapsf.com", "sapsf.eu", "smartrecruiters.com", "greenhouse.io", "lever.co", "join.com",
    "recruitee.com", "dvinci-hr.com", "dvinci.de", "rexx-systems.com", "onlyfy.jobs",
    "prescreen.io", "concludis.de", "umantis.com", "b-ite.com", "b-ite.de", "workable.com",
    "teamtailor.com", "bewerbermanagement.net", "jobbase.io", "taleo.net", "oraclecloud.com",
    "icims.com", "jobvite.com", "ashbyhq.com", "kenjo.io", "factorial.de",
    "csod.com", "avature.net", "phenompeople.com", "eightfold.ai", "hibob.com", "zohorecruit.eu",
    "zohorecruit.com", "breezy.hr", "pinpointhq.com", "jobs.ashbyhq.com", "jobylon.com", "haufe-umantis.com",
    "refline.ch", "milchundzucker.de", "talention.de", "coveto.de", "perbit.com", "interamt.de",
]


class BrowserUseFehlt(RuntimeError):
    pass


def _pruefe_browser_use() -> None:
    _browser_umgebung()
    try:
        import browser_use  # noqa: F401
    except ImportError as e:
        raise BrowserUseFehlt(
            'browser-use ist nicht installiert. Installiere es mit:  pip install -e ".[browser]"'
        ) from e
    _cdp_wartezeit_verlaengern()


# --------------------------------------------------------------------------- #
# Browser-Start robust machen (Raspberry Pi, Docker)
# --------------------------------------------------------------------------- #

# browser-use gibt Chromium standardmaessig nur 30 s zum Starten - auf einem
# Raspberry Pi (SD-Karte, wenig RAM) reicht das oft nicht.
START_TIMEOUT_S = 150
_CDP_GEPATCHT = False


def _browser_umgebung() -> None:
    """Grosszuegige Startzeiten setzen - muss vor dem Import von browser_use passieren."""
    for name in ("TIMEOUT_BrowserStartEvent", "TIMEOUT_BrowserLaunchEvent", "TIMEOUT_BrowserConnectedEvent"):
        os.environ.setdefault(name, str(START_TIMEOUT_S))
    os.environ.setdefault("BROWSER_USE_CDP_TIMEOUT_S", "90")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    if Path("/.dockerenv").exists():
        os.environ.setdefault("IN_DOCKER", "true")


def _cdp_wartezeit_verlaengern() -> None:
    """browser-use wartet hart kodiert 30 s auf Chromium; auf langsamen Geraeten verlaengern."""
    global _CDP_GEPATCHT
    if _CDP_GEPATCHT:
        return
    try:
        from browser_use.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
    except Exception:  # andere browser-use-Version: nichts anfassen
        return
    original = getattr(LocalBrowserWatchdog, "_wait_for_cdp_url", None)
    if original is None or "timeout" not in inspect.signature(original).parameters:
        return

    async def warte(port: int, timeout: float = 30, process: Any = None) -> str:
        return await original(port, timeout=max(float(timeout), START_TIMEOUT_S - 30), process=process)

    LocalBrowserWatchdog._wait_for_cdp_url = staticmethod(warte)
    _CDP_GEPATCHT = True


def browser_pfad() -> str | None:
    """Chromium/Chrome finden: Umgebung, Systempaket, dann Playwright-Downloads."""
    kandidaten = [os.environ.get("BROWSER_EXECUTABLE_PATH"), "/usr/bin/chromium", "/usr/bin/chromium-browser",
                  "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome"]
    for basis in {os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", str(Path.home() / ".cache" / "ms-playwright")}:
        if basis:
            kandidaten += sorted(glob.glob(f"{basis}/chromium-*/chrome-linux*/chrome"), reverse=True)
    for kandidat in kandidaten:
        if kandidat and Path(kandidat).is_file() and os.access(kandidat, os.X_OK):
            return kandidat
    return None


# Nur Meldungen, die browser-use beim Starten von Chromium erzeugt. Ein Timeout mitten im Lauf
# (Navigation, Klick, Download, KI-Aufruf) ist KEIN Startfehler: danach kann das Formular schon
# abgeschickt sein, die Bewerbung darf dann nicht automatisch erneut rausgehen.
_STARTFEHLER = re.compile(
    r"BrowserStartEvent|BrowserLaunchEvent|BrowserConnectedEvent|exited before CDP|No local Chrome|"
    r"failed to install with playwright|No local browser path|Failed to launch browser|"
    r"Browser did not start within|Failed to establish CDP connection|Chrome not found|"
    r"connect\(\) timed out after \d+s|CDP connection to \S+ is too slow or unresponsive",
    re.IGNORECASE,
)


def ist_startfehler(text: str | None) -> bool:
    return bool(text and _STARTFEHLER.search(text))


def startfehler_text(fehler: str) -> str:
    """Technische Startfehler in eine verstaendliche Ursache uebersetzen."""
    f = fehler.lower()
    if "no local chrome" in f or "failed to install" in f:
        return ("Auf dem Server ist kein Chromium-Browser installiert. Im Docker-Image wird er ab diesem Update "
                "mitgeliefert – nach dem nächsten Deploy erneut testen.")
    if "exited before cdp" in f:
        return ("Chromium stürzt beim Start ab (oft zu wenig Arbeitsspeicher oder fehlende Systembibliotheken). "
                f"Technisch: {fehler[:200]}")
    if "timed out" in f or "timeout" in f or "did not start within" in f:
        return (f"Der Browser startet zu langsam (über {START_TIMEOUT_S} s). Der Server ist ausgelastet oder "
                "Chromium hängt beim Start.")
    return f"Der Browser lässt sich nicht starten: {fehler[:240]}"


@dataclass
class Selbsttest:
    ok: bool
    sekunden: float
    meldung: str
    pfad: str | None = None


async def _selbsttest_async(timeout: float) -> Selbsttest:
    from browser_use import BrowserProfile, BrowserSession

    pfad = browser_pfad()
    profil = BrowserProfile(headless=True, chromium_sandbox=_sandbox_moeglich(), executable_path=pfad,
                            enable_default_extensions=False)
    sitzung = BrowserSession(browser_profile=profil)
    beginn = time.monotonic()
    try:
        await asyncio.wait_for(sitzung.start(), timeout)
        dauer = time.monotonic() - beginn
        return Selbsttest(True, dauer, f"Browser startet in {dauer:.0f} s ({pfad or 'browser-use-Standard'}).", pfad)
    finally:
        try:
            await asyncio.wait_for(sitzung.kill(), 30)
        except Exception:
            pass


def _testdatei() -> Path:
    from ..db import STANDARD_DB

    return STANDARD_DB.parent / "browser_test.json"


def letzter_selbsttest() -> dict[str, Any] | None:
    """Ergebnis des letzten Selbsttests (auch aus anderen Prozessen, z. B. dem Cron-Tick)."""
    import json

    try:
        return json.loads(_testdatei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def selbsttest(timeout: float = START_TIMEOUT_S) -> Selbsttest:
    """Startet den Browser einmal headless und schliesst ihn wieder - ohne KI, ohne Webseite."""
    import json
    from datetime import datetime, timezone

    beginn = time.monotonic()
    try:
        _pruefe_browser_use()
        ergebnis = asyncio.run(_selbsttest_async(timeout))
    except BrowserUseFehlt as e:
        ergebnis = Selbsttest(False, 0.0, str(e), browser_pfad())
    except (TimeoutError, asyncio.TimeoutError):
        ergebnis = Selbsttest(False, time.monotonic() - beginn, startfehler_text("timed out"), browser_pfad())
    except Exception as e:
        ergebnis = Selbsttest(False, time.monotonic() - beginn, startfehler_text(f"{type(e).__name__}: {e}"),
                              browser_pfad())
    try:
        _testdatei().parent.mkdir(parents=True, exist_ok=True)
        _testdatei().write_text(json.dumps({
            "ok": ergebnis.ok, "sekunden": round(ergebnis.sekunden, 1), "meldung": ergebnis.meldung,
            "pfad": ergebnis.pfad, "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return ergebnis


def _browser_llm(modell: str, ausser: tuple[str, ...] = ()):
    """LLM fuer browser-use (Anthropic, OpenAI oder OpenAI-kompatibles Ollama).

    Das Modell wird vorher beim Anbieter geprueft: gibt es das gewuenschte
    nicht, nimmt der Agent ein verfuegbares, statt mit "model not found" zu
    scheitern.
    """
    from ..scoring import llm as llm_modul

    anbieter = llm_modul.provider(modell)
    if anbieter is None:
        raise llm_modul.LLMNichtVerfuegbar(
            "Kein KI-Zugang eingerichtet – der Browser-Agent braucht ein Modell (Einstellungen → Konten)."
        )
    ziel, _ = llm_modul.pruefe_modell(anbieter, modell, ausser=ausser)
    if anbieter == "ollama":
        from browser_use import ChatOpenAI

        base = (llm_modul._env("OLLAMA_BASE_URL") or llm_modul.OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        key = llm_modul._env("OLLAMA_API_KEY") or "ollama"
        return ChatOpenAI(model=ziel, base_url=f"{base}/v1", api_key=key, temperature=0.2)
    if anbieter == "openai":
        from browser_use import ChatOpenAI

        return ChatOpenAI(model=ziel, api_key=llm_modul._env("OPENAI_API_KEY"), temperature=0.2)
    from browser_use import ChatAnthropic

    return ChatAnthropic(model=ziel)


_MODELL_FEHLT = re.compile(r"model_not_found|model .{0,80}(does not exist|not found)", re.I)
# Die KI selbst ist nicht nutzbar (Konto ohne Guthaben, Schluessel abgelehnt, ueberlastet)
_KI_FEHLER = re.compile(
    r"insufficient_quota|credit_balance_exhausted|no credits|exceeded your current quota|quota|"
    r"rate.?limit|too many requests|error code: 429|\b429\b|invalid_api_key|incorrect api key|"
    r"authentication|unauthorized|error code: 40[13]\b",
    re.I,
)


def ki_fehler_text(fehler: str) -> str:
    """Verstaendliche Ursache, wenn die KI nicht geantwortet hat."""
    from ..scoring.llm import ki_grund

    return f"Nichts ausgefüllt oder abgeschickt – {ki_grund(fehler)}. Ich versuche es automatisch wieder."


def _ergebnis_modell():
    from pydantic import BaseModel, Field

    class BewerbungsErgebnis(BaseModel):
        abgeschickt: bool = Field(
            description="true nur, wenn die Bewerbung nachweislich final abgesendet wurde"
        )
        bestaetigung: str = Field(
            default="", description="Wortlaut der Bestaetigungsmeldung, falls vorhanden"
        )
        ausgefuellte_felder: list[str] = Field(
            default_factory=list, description="Welche Formularfelder ausgefuellt wurden"
        )
        fehlende_angaben: list[str] = Field(
            default_factory=list,
            description=(
                "Pflichtfelder, die nicht aus dem Profil befuellt werden konnten, je Eintrag "
                "'Feldname: Frage wie im Formular' (z. B. 'Gehalt: Wie hoch ist Ihre Gehaltsvorstellung?')"
            ),
        )
        hindernis: str = Field(
            default="",
            description="Login-Pflicht, Captcha, kein Formular, Bewerbung nur per E-Mail o. Ae.",
        )
        bewerbungs_email: str = Field(
            default="",
            description="E-Mail-Adresse fuer Bewerbungen, falls die Seite nur diesen Weg anbietet",
        )
        letzte_url: str = Field(default="")

    return BewerbungsErgebnis


def _sandbox_moeglich() -> bool:
    """Chromium verweigert den Start als root mit aktivierter Sandbox."""
    return getattr(os, "geteuid", lambda: 1000)() != 0


def _host_domains(url: str) -> list[str]:
    host = urlparse(url).hostname or ""
    if not host:
        return []
    teile = host.split(".")
    basis = ".".join(teile[-2:]) if len(teile) >= 2 else host
    return [host, basis, f"*.{basis}"]


BROWSER_START_RETRIES = 2          # zusaetzlich zum ersten Versuch
BROWSER_START_BACKOFF_S = 10.0     # Wartezeit zwischen den Versuchen


def _hat_gearbeitet(history: Any, spur: list | None = None) -> bool:
    """True, wenn das KI-Modell in mindestens einem Schritt geantwortet hat – dann kann auf der Seite etwas passiert sein.

    Schritt 0 zaehlt nicht: browser-use legt fuer die automatische Startnavigation (`initial_actions`)
    eine kuenstliche Modell-Antwort an ("Initial navigation"), auch wenn die KI nie geantwortet hat.
    Bricht ein Schritt wegen Zeitueberschreitung ab, fehlt er oft im Verlauf, obwohl die KI schon geantwortet
    und geklickt hat. Deshalb zaehlen auch die Beobachtung nach jedem Schritt (`spur`) und die Token-Abrechnung.
    """
    if spur:
        return True
    antworten = getattr(getattr(history, "usage", None), "total_completion_tokens", None)
    if isinstance(antworten, int) and antworten > 0:
        return True
    eintraege = getattr(history, "history", None)
    if isinstance(eintraege, list):
        return any(
            getattr(h, "model_output", None) is not None
            and getattr(getattr(h, "metadata", None), "step_number", None) != 0
            for h in eintraege
        )
    try:
        return bool(history.model_outputs())
    except Exception:
        return history.number_of_steps() > 1


def _lebenslauf_vorhanden(profil: Profil) -> bool:
    # findet alte Pfade auch im Datenordner wieder (nach Container-Updates)
    return profil.lebenslauf_pfad() is not None


async def _run_mit_retry(run_factory):
    """Fuehrt `run_factory` aus, bei transientem Browser-Start-Fehler erneut.

    Kam Chromium nicht hoch (z. B. Timeout des `BrowserStartEvent`-Watchdogs),
    wird mit kurzem Backoff erneut versucht. Andere Fehler – auch Timeouts
    mitten im Lauf – werden nie wiederholt: das Formular koennte schon
    abgeschickt sein. `run_factory` ist async, baut pro Versuch einen neuen
    Agenten und liefert das history-Objekt.
    """
    letzter_fehler: BaseException | None = None
    for versuch in range(1 + BROWSER_START_RETRIES):
        try:
            return await run_factory()
        except Exception as e:
            letzter_fehler = e
            if not ist_startfehler(str(e)):
                raise
            log.warning(
                "Browser-Start-Timeout (Versuch %d/%d): %s",
                versuch + 1, 1 + BROWSER_START_RETRIES, e,
            )
            await asyncio.sleep(BROWSER_START_BACKOFF_S)
    assert letzter_fehler is not None
    raise letzter_fehler


def _erlaubte_domains(url: str, extra: list[str] | None = None) -> list[str]:
    domains = list(STANDARD_DOMAINS)
    domains += _host_domains(url)
    for ats in ATS_DOMAINS:
        domains += [ats, f"*.{ats}"]
    for eintrag in extra or []:
        domains += _host_domains(eintrag) if "/" in eintrag else [eintrag]
    return sorted(set(domains))


def _profilblock(profil: Profil) -> str:
    p = profil.person
    zeilen = [
        f"Vorname: {p.vorname}",
        f"Nachname: {p.nachname}",
        f"E-Mail: {p.email}",
        f"Telefon: {p.telefon}",
        f"Strasse und Hausnummer: {p.strasse}",
        f"PLZ: {p.plz}",
        f"Ort: {p.ort}",
        f"Land: {p.land}",
    ]
    if p.geburtsdatum:
        zeilen.append(f"Geburtsdatum: {p.geburtsdatum}")
    for label, wert in (("LinkedIn", p.linkedin), ("GitHub", p.github), ("Website", p.website)):
        if wert:
            zeilen.append(f"{label}: {wert}")
    a = profil.angaben
    for label, wert in (
        ("Anrede", a.anrede),
        ("Akademischer Titel", a.titel),
        ("Verfuegbar ab / fruehester Eintrittstermin", a.verfuegbar_ab),
        ("Kuendigungsfrist", a.kuendigungsfrist),
        ("Gehaltsvorstellung", a.gehaltsvorstellung),
        ("Arbeitserlaubnis", a.arbeitserlaubnis),
        ("Staatsangehoerigkeit", a.nationalitaet),
        ("Hoechster Abschluss", a.abschluss),
        ("Berufserfahrung (Jahre)", a.berufserfahrung_jahre),
        ("Umzugsbereitschaft", a.umzugsbereitschaft),
        ("Fuehrerschein", a.fuehrerschein),
        ("Sprachkenntnisse", a.sprachen),
    ):
        if wert:
            zeilen.append(f"{label}: {wert}")
    for frage, antwort in (a.weitere or {}).items():
        if frage and antwort:
            zeilen.append(f"{frage}: {antwort}")
    return "\n".join(zeilen)


def _anhang_zeilen(anschreiben_pdf: str | None, profil: Profil) -> tuple[list[str], str]:
    """(Pfade fuer browser-use, beschriftete Liste fuer die Aufgabe)."""
    pfade: list[str] = []
    zeilen: list[str] = []
    if anschreiben_pdf and Path(anschreiben_pdf).exists():
        pfad = str(Path(anschreiben_pdf).resolve())
        pfade.append(pfad)
        zeilen.append(f"- Anschreiben: {pfad}")
    lebenslauf = profil.lebenslauf_pfad()
    for pfad in profil.anhaenge():
        pfade.append(pfad)
        art = "Lebenslauf" if lebenslauf and Path(pfad) == lebenslauf.resolve() else "Zeugnis/Anlage"
        zeilen.append(f"- {art}: {pfad}")
    return pfade, "\n".join(zeilen) or "- (keine Dateien hinterlegt)"


AUFGABE = """Du bewirbst dich im Auftrag von {name} auf eine konkrete Stelle.

STELLE
Titel: {titel}
Arbeitgeber: {arbeitgeber}
Ort: {ort}
Referenznummer: {ref}
Bewerbungsseite: {url}

BEWERBERDATEN (nur diese verwenden, nichts erfinden)
{profilblock}

ANSCHREIBEN (woertlich in ein Freitext-/Motivationsfeld einfuegen, falls vorhanden)
---
{anschreiben}
---

ANHAENGE
Diese Dateien stehen zum Hochladen bereit (Pfade exakt so verwenden):
{anhaenge}
Gibt es ein eigenes Upload-Feld fuer das Anschreiben, lade dort die
Anschreiben-PDF hoch; Lebenslauf und Zeugnisse in die passenden Felder.
Gibt es nur EIN Upload-Feld, lade den Lebenslauf hoch.

ABLAUF
1. Oeffne die Bewerbungsseite.
2. Suche den Bewerbungsweg: Button "Jetzt bewerben", "Online bewerben",
   "Bewerben", "Apply now" oder ein direkt eingebettetes Formular. Folge
   Weiterleitungen auf das Bewerbermanagementsystem des Arbeitgebers.
3. Cookie-Banner: klicke "Nur notwendige" / "Ablehnen", falls vorhanden,
   sonst akzeptiere, damit die Seite nutzbar wird.
4. Bietet die Seite "Als Gast bewerben", "Ohne Konto bewerben" oder "Schnell-
   bewerbung" an, nimm diesen Weg statt einer Registrierung. Eine Bewerbung
   per LinkedIn/XING/Indeed-Login nutzt du NICHT.
5. Fuelle alle Pflichtfelder ausschliesslich aus den Bewerberdaten. Feldnamen
   variieren (Vorname/First name, PLZ/Zip, Anrede/Salutation). Bei Auswahl-
   feldern waehle die Option, die den Bewerberdaten am naechsten kommt.
   Formate: Datum als TT.MM.JJJJ (bzw. wie das Feld es vorgibt), Gehalt als
   Zahl in EUR brutto pro Jahr ohne Tausenderpunkte, wenn das Feld nur Zahlen
   annimmt. Englische Formulare fuellst du sinngemaess auf Englisch aus.
6. "Wie sind Sie auf uns aufmerksam geworden?": waehle {quelle}, sonst
   "Jobboerse"/"Internet"/"Sonstiges". "Haben Sie sich schon einmal bei uns
   beworben?": nein. Datenschutzerklaerung/Einwilligung zur Verarbeitung der
   Bewerbungsdaten: zustimmen (Pflicht fuer die Bewerbung). Newsletter,
   Talentpool-Werbung und Marketing-Einwilligungen: NICHT ankreuzen.
7. Lade die Unterlagen hoch, wenn ein Datei-Upload existiert.
8. Erfinde niemals Angaben. Wenn ein Pflichtfeld nicht aus den Bewerberdaten
   befuellbar ist (Gehaltsvorstellung, Verfuegbarkeit, Sicherheitsfragen),
   fuelle es NICHT, sondern vermerke es in "fehlende_angaben" mit dem
   Feldnamen und der Frage, wie sie im Formular steht.
9. {absende_regel}

ABBRUCHREGELN - brich ab und berichte in "hindernis":
- Login oder Registrierung noetig und keine Zugangsdaten vorhanden.
- Captcha, das du nicht loesen kannst.
- Bewerbung ist nur per E-Mail oder Post moeglich (kein Formular). Steht dort
  eine Bewerbungs-E-Mail-Adresse, trage sie in "bewerbungs_email" ein.
- Die Seite verlangt eine Zahlung oder wirkt unserioes.
- Nach {max_schritte} Schritten kein Fortschritt.

Berichte am Ende strukturiert, was du ausgefuellt hast und ob abgeschickt wurde.
Setze "abgeschickt" nur auf true, wenn du eine Bestaetigung gesehen hast."""

REGEL_PROBELAUF = """PROBELAUF - WICHTIG: Klicke NICHT auf den finalen
   Absende-Button ("Bewerbung absenden", "Submit application", "Jetzt
   bewerben" im letzten Schritt). Fuelle das Formular vollstaendig aus, halte
   unmittelbar davor an und berichte, was abgeschickt WUERDE. Zwischenschritte
   ("Weiter", "Next") in mehrseitigen Formularen darfst du klicken, solange
   sie die Bewerbung nicht final einreichen. Setze "abgeschickt" auf false."""

REGEL_ABSENDEN = """ABSENDEN: Pruefe alle Eingaben, klicke dann den finalen
   Absende-Button und warte auf die Bestaetigungsseite. Uebernimm den Wortlaut
   der Bestaetigung in "bestaetigung"."""


@dataclass
class BewerbungsLauf:
    ref: str
    dry_run: bool
    abgeschickt: bool
    zusammenfassung: str
    schritte: int
    url: str | None
    rohdaten: dict[str, Any] | None = None
    gearbeitet: bool | None = None  # hat die KI auf der Seite etwas getan? (None = unbekannt)

    def als_application(self, anschreiben: str | None) -> Application:
        if self.abgeschickt:
            status = "abgeschickt"
        elif self.dry_run:
            status = "probelauf"
        else:
            status = "fehlgeschlagen"
        return Application(
            ref=self.ref,
            status=status,
            url=self.url,
            anschreiben=anschreiben,
            ergebnis=self.zusammenfassung,
            schritte=self.schritte,
            dry_run=self.dry_run,
        )


class BewerbungsBrowser:
    def __init__(
        self,
        profil: Profil,
        headless: bool = False,
        modell: str | None = None,
        max_schritte: int = 40,
        extra_domains: list[str] | None = None,
        domains_offen: bool = False,
        aufzeichnung: str | None = None,
    ):
        _pruefe_browser_use()
        self.profil = profil
        self.headless = headless
        self.modell = modell or profil.bewertung.modell
        self.max_schritte = max_schritte
        self.extra_domains = extra_domains or []
        self.domains_offen = domains_offen
        self.aufzeichnung = aufzeichnung
        # Modelle, die der Anbieter in diesem Lauf als unbekannt gemeldet hat
        self._modell_ausser: tuple[str, ...] = ()

    def baue_agent(
        self,
        job: Job,
        anschreiben: str,
        dry_run: bool = True,
        start_url: str | None = None,
        anschreiben_pdf: str | None = None,
    ):
        """Konfiguriert den browser-use-Agenten, ohne ihn zu starten.

        `start_url` ist der bereits aufgeloeste Bewerbungslink (z. B. das
        Bewerbermanagementsystem hinter GET IN IT), `anschreiben_pdf` wird
        zusaetzlich zum Hochladen angeboten.
        """
        from browser_use import Agent, BrowserProfile

        url = start_url or job.bewerbungs_url
        if not url:
            raise ValueError(f"{job.ref}: keine Bewerbungs-URL")

        anhaenge, anhang_text = _anhang_zeilen(anschreiben_pdf, self.profil)
        ergebnis_modell = _ergebnis_modell()
        llm = _browser_llm(self.modell, self._modell_ausser)
        # Die meisten Ollama-Cloud-Modelle sind reine Textmodelle und antworten
        # auf Screenshots mit HTTP 500. Vision dort nur per BROWSER_VISION=1.
        from ..scoring import llm as llm_modul

        if llm_modul.provider(self.modell) == "ollama":
            vision = os.environ.get("BROWSER_VISION", "").lower() in ("1", "true", "ja")
        else:
            vision = True

        aufgabe = AUFGABE.format(
            name=self.profil.person.name,
            titel=job.titel,
            arbeitgeber=job.arbeitgeber or "-",
            ort=job.ort or "-",
            ref=job.ref,
            url=url,
            profilblock=_profilblock(self.profil),
            anschreiben=anschreiben.strip(),
            anhaenge=anhang_text,
            quelle=_quelle_text(job),
            absende_regel=REGEL_PROBELAUF if dry_run else REGEL_ABSENDEN,
            max_schritte=self.max_schritte,
        )

        browser_profil = BrowserProfile(
            headless=self.headless,
            allowed_domains=None if self.domains_offen else _erlaubte_domains(
                url, [*self.extra_domains, *([job.bewerbungs_url] if job.bewerbungs_url else [])]
            ),
            downloads_path="daten/downloads",
            # Chromium startet als root nicht mit aktivierter Sandbox (Container,
            # CI). Lokal als normaler Nutzer bleibt die Sandbox an.
            chromium_sandbox=_sandbox_moeglich(),
            executable_path=browser_pfad(),
            # Keine Erweiterungen bei jedem Start aus dem Netz laden (kostet auf dem Pi die Startzeit)
            enable_default_extensions=False,
        )

        # Zugangsdaten erreichen das Modell nie im Klartext: browser-use setzt
        # die Platzhalter erst im Browser ein.
        sensitive: dict[str, Any] = {}
        if self.profil.ba_benutzer and self.profil.ba_passwort:
            sensitive["https://*.arbeitsagentur.de"] = {
                "ba_benutzer": self.profil.ba_benutzer,
                "ba_passwort": self.profil.ba_passwort,
            }
            aufgabe += (
                "\n\nZUGANGSDATEN: Falls arbeitsagentur.de einen Login verlangt, melde dich mit "
                "den Platzhaltern ba_benutzer und ba_passwort an."
            )

        return Agent(
            task=aufgabe,
            llm=llm,
            browser_profile=browser_profil,
            available_file_paths=anhaenge or None,
            sensitive_data=sensitive or None,
            output_model_schema=ergebnis_modell,
            # Start-URL fest vorgeben: automatisch aus dem Aufgabentext geraten
            # wuerde sonst evtl. ein LinkedIn-/GitHub-Link aus dem Profil.
            directly_open_url=False,
            initial_actions=[{"navigate": {"url": url, "new_tab": False}}],
            save_conversation_path=self.aufzeichnung,
            use_vision=vision,
        )

    @staticmethod
    def _startfehler(history: Any) -> bool:
        if not history.has_errors() or history.number_of_steps() > 1:
            return False
        fehler = [f for f in history.errors() if f]
        return bool(fehler) and ist_startfehler(fehler[-1])

    def _modell_fehlte(self, history: Any, agent: Any, spur: list | None = None) -> bool:
        """True, wenn der Lauf am unbekannten Modell scheiterte und ein Ersatz bereitsteht."""
        from ..scoring import llm as llm_modul

        if self._modell_ausser or not history.has_errors():
            return False
        # Hat das Modell schon geantwortet (oder gar ein Ergebnis geliefert), wurde auf der Seite gearbeitet:
        # dann nie den ganzen Agenten neu starten – das Formular koennte schon abgeschickt sein.
        if getattr(history, "structured_output", None) is not None or _hat_gearbeitet(history, spur):
            return False
        fehler = [f for f in history.errors() if f]
        if not fehler or not (_MODELL_FEHLT.search(fehler[-1]) or _KI_FEHLER.search(fehler[-1])):
            return False
        genutzt = getattr(getattr(agent, "llm", None), "model", None)
        anbieter = llm_modul.provider(self.modell)
        if not genutzt or anbieter is None:
            return False
        llm_modul.vergiss_modelle()
        ersatz, _ = llm_modul.pruefe_modell(anbieter, self.modell, ausser=(genutzt,))
        if ersatz == genutzt:
            return False
        log.warning("Browser-Agent: Modell %s nicht nutzbar (%s) - neuer Versuch mit %s.", genutzt,
                    fehler[-1][:120], ersatz)
        self._modell_ausser = (genutzt,)
        return True

    async def bewerbe(
        self,
        job: Job,
        anschreiben: str,
        dry_run: bool = True,
        start_url: str | None = None,
        anschreiben_pdf: str | None = None,
    ) -> BewerbungsLauf:
        url = start_url or job.bewerbungs_url
        if not url:
            return BewerbungsLauf(
                ref=job.ref,
                dry_run=dry_run,
                abgeschickt=False,
                zusammenfassung="Keine Bewerbungs-URL hinterlegt.",
                schritte=0,
                url=None,
            )

        # Vorab-Pruefung: Ohne Lebenslauf kann ein Online-Formular kaum
        # vollstaendig abgeschickt werden. Frueh abbrechen statt 40
        # Browser-Schritte lang zu scheitern.
        if not dry_run and not _lebenslauf_vorhanden(self.profil):
            return BewerbungsLauf(
                ref=job.ref,
                dry_run=dry_run,
                abgeschickt=False,
                zusammenfassung=(
                    "Kein Lebenslauf hinterlegt – bitte unter „Lebenslauf“ hochladen. "
                    "Bewerbung nicht gestartet."
                ),
                schritte=0,
                url=url,
            )

        # Schritte, in denen die KI geantwortet hat – auch solche, die danach wegen Zeitueberschreitung
        # abbrachen und deshalb im Verlauf fehlen. Gilt fuer alle Versuche dieser Bewerbung.
        spur: list[int] = []

        async def _nach_schritt(agent: Any) -> None:
            zustand = getattr(agent, "state", None)
            if getattr(zustand, "last_model_output", None) is not None:
                spur.append(getattr(zustand, "n_steps", 0))

        async def _starte_einmal():
            agent = self.baue_agent(
                job, anschreiben, dry_run=dry_run, start_url=url, anschreiben_pdf=anschreiben_pdf
            )
            history = await agent.run(max_steps=self.max_schritte, on_step_end=_nach_schritt)
            if self._startfehler(history) and not spur:
                # Chromium kam nicht hoch (langsamer Pi, Kaltstart) und browser-use hat den
                # Fehler nur protokolliert statt ihn zu werfen: genau ein zweiter Versuch
                log.warning("Browser-Start fehlgeschlagen - zweiter Versuch.")
                agent = self.baue_agent(
                    job, anschreiben, dry_run=dry_run, start_url=url, anschreiben_pdf=anschreiben_pdf
                )
                history = await agent.run(max_steps=self.max_schritte, on_step_end=_nach_schritt)
            if self._modell_fehlte(history, agent, spur):
                # Das Modell gibt es beim Anbieter nicht: einmal mit einem verfuegbaren neu starten
                agent = self.baue_agent(
                    job, anschreiben, dry_run=dry_run, start_url=url, anschreiben_pdf=anschreiben_pdf
                )
                history = await agent.run(max_steps=self.max_schritte, on_step_end=_nach_schritt)
            return history

        history = await _run_mit_retry(_starte_einmal)

        strukturiert = None
        try:
            strukturiert = history.structured_output
        except Exception as e:
            log.warning("Strukturierte Ausgabe nicht lesbar: %s", e)

        urls = [u for u in (history.urls() or []) if u]
        letzte_url = urls[-1] if urls else url

        if strukturiert is not None:
            abgeschickt = bool(strukturiert.abgeschickt) and not dry_run
            zusammenfassung = _zusammenfassen(strukturiert, dry_run)
            rohdaten = strukturiert.model_dump()
            letzte_url = strukturiert.letzte_url or letzte_url
            if dry_run and (strukturiert.abgeschickt or strukturiert.bestaetigung):
                # Der Probelauf hat trotz Verbot abgeschickt: nicht noch einmal senden
                rohdaten["versehentlich_abgeschickt"] = True
                zusammenfassung = ("ACHTUNG: Der Probelauf hat die Bewerbung offenbar abgeschickt. "
                                   + zusammenfassung)
        else:
            abgeschickt = False
            zusammenfassung = (history.final_result() or "Kein Ergebnis vom Agenten.")[:2000]
            rohdaten = None

        if history.has_errors():
            fehler = [f for f in history.errors() if f]
            if fehler:
                if _MODELL_FEHLT.search(fehler[-1]):
                    from ..scoring import llm as llm_modul

                    llm_modul.vergiss_modelle()
                    zusammenfassung += (
                        "\nDas KI-Modell ist bei deinem Zugang nicht verfügbar. Unter Einstellungen → Konten "
                        "ein verfügbares Modell wählen und erneut versuchen."
                    )
                if strukturiert is None and self._startfehler(history):
                    # Der Browser kam nie hoch - nichts wurde ausgefuellt oder abgeschickt
                    zusammenfassung = startfehler_text(fehler[-1])
                    rohdaten = {**(rohdaten or {}), "browser_startfehler": True}
                elif strukturiert is None and not _hat_gearbeitet(history, spur):
                    # Die KI hat nie geantwortet (z. B. Konto ohne Guthaben): auf der Seite ist nichts passiert
                    zusammenfassung = ki_fehler_text(fehler[-1])
                    rohdaten = {**(rohdaten or {}), "ki_fehler": True, "fehler": fehler[-1][:500]}
                else:
                    zusammenfassung += f"\nFehler: {fehler[-1]}"

        return BewerbungsLauf(
            ref=job.ref,
            dry_run=dry_run,
            abgeschickt=abgeschickt,
            zusammenfassung=zusammenfassung,
            schritte=history.number_of_steps(),
            url=letzte_url,
            rohdaten=rohdaten,
            gearbeitet=strukturiert is not None or _hat_gearbeitet(history, spur),
        )


def _quelle_text(job: Job) -> str:
    from ..sources import QUELLEN

    if job.quelle == "arbeitsagentur":
        return '"Bundesagentur fuer Arbeit" bzw. "Jobboerse"'
    if job.quelle == "wunscharbeitgeber":
        return '"Karriereseite/Unternehmenswebsite"'
    info = QUELLEN.get(job.quelle or "")
    return f'"{info.label}" bzw. "Jobboerse/Internet"' if info else '"Jobboerse/Internet"'


def _zusammenfassen(e: Any, dry_run: bool) -> str:
    teile = []
    if dry_run:
        teile.append("Probelauf: Formular ausgefuellt, nicht abgeschickt.")
    else:
        teile.append("Abgeschickt." if e.abgeschickt else "NICHT abgeschickt.")
    if e.bestaetigung:
        teile.append(f"Bestaetigung: {e.bestaetigung}")
    if e.ausgefuellte_felder:
        teile.append("Ausgefuellt: " + ", ".join(e.ausgefuellte_felder[:15]))
    if e.fehlende_angaben:
        teile.append("Offen: " + ", ".join(e.fehlende_angaben[:10]))
    if e.hindernis:
        teile.append(f"Hindernis: {e.hindernis}")
    if getattr(e, "bewerbungs_email", ""):
        teile.append(f"Bewerbungs-E-Mail: {e.bewerbungs_email}")
    return " | ".join(teile)
