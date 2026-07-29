"""Bewerbung tatsaechlich abschicken - gesteuert durch browser-use.

Der Agent oeffnet die Bewerbungsseite, fuellt das Formular aus den Profildaten,
haengt die Unterlagen an und schickt ab. Standard ist der Probelauf
(`dry_run=True`): alles wird ausgefuellt, aber der letzte Absende-Klick
unterbleibt.

browser-use ist ein optionales Extra:  pip install -e ".[browser]"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
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


class BrowserUseFehlt(RuntimeError):
    pass


def _pruefe_browser_use() -> None:
    try:
        import browser_use  # noqa: F401
    except ImportError as e:
        raise BrowserUseFehlt(
            'browser-use ist nicht installiert. Installiere es mit:  pip install -e ".[browser]"'
        ) from e


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
            description="Pflichtfelder, die nicht aus dem Profil befuellt werden konnten",
        )
        hindernis: str = Field(
            default="",
            description="Login-Pflicht, Captcha, kein Formular, Bewerbung nur per E-Mail o. Ae.",
        )
        letzte_url: str = Field(default="")

    return BewerbungsErgebnis


def _sandbox_moeglich() -> bool:
    """Chromium verweigert den Start als root mit aktivierter Sandbox."""
    return getattr(os, "geteuid", lambda: 1000)() != 0


def _erlaubte_domains(url: str, extra: list[str] | None = None) -> list[str]:
    domains = list(STANDARD_DOMAINS)
    host = urlparse(url).hostname or ""
    if host:
        teile = host.split(".")
        basis = ".".join(teile[-2:]) if len(teile) >= 2 else host
        domains += [host, basis, f"*.{basis}"]
    domains += extra or []
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
    return "\n".join(zeilen)


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

ABLAUF
1. Oeffne die Bewerbungsseite.
2. Suche den Bewerbungsweg: Button "Jetzt bewerben", "Online bewerben",
   "Bewerben", "Apply now" oder ein direkt eingebettetes Formular. Folge
   Weiterleitungen auf das Bewerbermanagementsystem des Arbeitgebers.
3. Cookie-Banner: klicke "Nur notwendige" / "Ablehnen", falls vorhanden,
   sonst akzeptiere, damit die Seite nutzbar wird.
4. Fuelle alle Pflichtfelder ausschliesslich aus den Bewerberdaten. Feldnamen
   variieren (Vorname/First name, PLZ/Zip). Bei Auswahlfeldern waehle die
   Option, die den Bewerberdaten am naechsten kommt.
5. Lade den Lebenslauf hoch, wenn ein Datei-Upload existiert.
6. Erfinde niemals Angaben. Wenn ein Pflichtfeld nicht aus den Bewerberdaten
   befuellbar ist (Gehaltsvorstellung, Verfuegbarkeit, Sicherheitsfragen),
   fuelle es NICHT, sondern vermerke es in "fehlende_angaben".
7. {absende_regel}

ABBRUCHREGELN - brich ab und berichte in "hindernis":
- Login oder Registrierung noetig und keine Zugangsdaten vorhanden.
- Captcha, das du nicht loesen kannst.
- Bewerbung ist nur per E-Mail oder Post moeglich (kein Formular).
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

    def baue_agent(self, job: Job, anschreiben: str, dry_run: bool = True):
        """Konfiguriert den browser-use-Agenten, ohne ihn zu starten."""
        from browser_use import Agent, BrowserProfile, ChatAnthropic

        url = job.bewerbungs_url
        if not url:
            raise ValueError(f"{job.ref}: keine Bewerbungs-URL")

        anhaenge = self.profil.anhaenge()
        ergebnis_modell = _ergebnis_modell()

        aufgabe = AUFGABE.format(
            name=self.profil.person.name,
            titel=job.titel,
            arbeitgeber=job.arbeitgeber or "-",
            ort=job.ort or "-",
            ref=job.ref,
            url=url,
            profilblock=_profilblock(self.profil),
            anschreiben=anschreiben.strip(),
            anhaenge="\n".join(f"- {p}" for p in anhaenge) or "- (keine Dateien hinterlegt)",
            absende_regel=REGEL_PROBELAUF if dry_run else REGEL_ABSENDEN,
            max_schritte=self.max_schritte,
        )

        browser_profil = BrowserProfile(
            headless=self.headless,
            allowed_domains=None if self.domains_offen else _erlaubte_domains(url, self.extra_domains),
            downloads_path="daten/downloads",
            # Chromium startet als root nicht mit aktivierter Sandbox (Container,
            # CI). Lokal als normaler Nutzer bleibt die Sandbox an.
            chromium_sandbox=_sandbox_moeglich(),
            executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH") or None,
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
            llm=ChatAnthropic(model=self.modell),
            browser_profile=browser_profil,
            available_file_paths=anhaenge or None,
            sensitive_data=sensitive or None,
            output_model_schema=ergebnis_modell,
            directly_open_url=url,
            save_conversation_path=self.aufzeichnung,
            use_vision=True,
        )

    async def bewerbe(
        self,
        job: Job,
        anschreiben: str,
        dry_run: bool = True,
    ) -> BewerbungsLauf:
        url = job.bewerbungs_url
        if not url:
            return BewerbungsLauf(
                ref=job.ref,
                dry_run=dry_run,
                abgeschickt=False,
                zusammenfassung="Keine Bewerbungs-URL hinterlegt.",
                schritte=0,
                url=None,
            )

        agent = self.baue_agent(job, anschreiben, dry_run=dry_run)
        history = await agent.run(max_steps=self.max_schritte)

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
        else:
            abgeschickt = False
            zusammenfassung = (history.final_result() or "Kein Ergebnis vom Agenten.")[:2000]
            rohdaten = None

        if history.has_errors():
            fehler = [f for f in history.errors() if f]
            if fehler:
                zusammenfassung += f"\nFehler: {fehler[-1]}"

        return BewerbungsLauf(
            ref=job.ref,
            dry_run=dry_run,
            abgeschickt=abgeschickt,
            zusammenfassung=zusammenfassung,
            schritte=history.number_of_steps(),
            url=letzte_url,
            rohdaten=rohdaten,
        )


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
    return " | ".join(teile)
