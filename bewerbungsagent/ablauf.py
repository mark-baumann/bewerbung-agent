"""Der Bewerbungsablauf als gemeinsame Geschaeftslogik fuer CLI, Cron und UI.

    suchen -> bewerten -> vorbereiten (Anschreiben, PDF, Kanal, Probelauf)
           -> wartet auf Ja/Nein -> freigegeben -> senden -> abgeschickt

Echte Bewerbungen gehen nur fuer Vorgaenge raus, die der Nutzer ausdruecklich
freigegeben hat. Fehlgeschlagene Sendungen werden nie automatisch wiederholt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import dokumente, zeitplan
from .anschreiben import erzeuge as erzeuge_anschreiben
from .bewerben import kanal as kanal_modul
from .bewerben import mail
from .config import Profil, ermittle_profil_pfad, speichere_profil
from .db import Speicher
from .hintergrund import sperre
from .jobsuche import hole_jobs
from .models import Application, Job, Score, Vorgang
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

Fortschritt = Callable[[str, float], None]

# Ab so vielen unbeantworteten Vorschlaegen bereitet die Automatik nichts Neues vor.
MAX_WARTEND_FAKTOR = 3
_HINDERNIS_SELBST = re.compile(
    r"login|anmeld|registr|konto|account|sign.?in|captcha|passwort", re.IGNORECASE
)


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tagesbeginn_utc(jetzt: datetime | None = None) -> str:
    lokal = (jetzt or datetime.now(timezone.utc)).astimezone(zeitplan.ZEITZONE)
    beginn = lokal.replace(hour=0, minute=0, second=0, microsecond=0)
    return beginn.astimezone(timezone.utc).isoformat(timespec="seconds")


def wochenbeginn_utc(jetzt: datetime | None = None) -> str:
    lokal = (jetzt or datetime.now(timezone.utc)).astimezone(zeitplan.ZEITZONE)
    beginn = (lokal - timedelta(days=lokal.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return beginn.astimezone(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Suchen & Bewerten
# --------------------------------------------------------------------------- #

@dataclass
class SuchErgebnis:
    refs: list[str] = field(default_factory=list)
    neu: int = 0
    aktualisiert: int = 0
    je_begriff: dict[str, int] = field(default_factory=dict)
    fehler: list[str] = field(default_factory=list)


def suche_ausfuehren(
    db: Speicher,
    profil: Profil,
    *,
    begriffe: list[str] | None = None,
    wo: str | None = None,
    umkreis: int | None = None,
    tage: int | None = None,
    max_treffer: int | None = None,
    nur_vollzeit: bool | None = None,
    quellen: list[str] | None = None,
    mit_details: bool = True,
    merken: bool = True,
    fortschritt: Fortschritt | None = None,
) -> SuchErgebnis:
    """Fragt alle Quellen x Suchbegriffe ab; ein Fehler bricht nur die eine Abfrage ab."""
    s = profil.suche
    begriffe = [b.strip() for b in (begriffe or s.was) if b and b.strip()]
    ort = (s.wo if wo is None else wo) or ""
    umkreis = int(s.umkreis if umkreis is None else umkreis)
    tage = int(s.veroeffentlicht_seit_tagen if tage is None else tage)
    max_treffer = int(s.max_pro_query if max_treffer is None else max_treffer)
    nur_vollzeit = s.nur_vollzeit if nur_vollzeit is None else nur_vollzeit
    quellen = list(quellen or s.quellen or ["arbeitsagentur"])

    ergebnis = SuchErgebnis()
    gesehen: dict[str, None] = {}
    schritte = max(1, len(quellen) * len(begriffe))
    nummer = 0
    for quelle in quellen:
        for begriff in begriffe:
            if fortschritt:
                fortschritt(f"Suche bei {quelle}: „{begriff}“ in {ort or 'ganz Deutschland'} …", nummer / schritte)
            try:
                jobs, neu, aktualisiert = hole_jobs(
                    db, quelle=quelle, suchbegriff=begriff, wo=ort.strip(), umkreis=umkreis,
                    tage=tage, nur_vollzeit=nur_vollzeit, max_treffer=max_treffer,
                    mit_details=mit_details,
                )
            except Exception as e:
                ergebnis.fehler.append(f"{quelle} / {begriff}: {e}")
                log.warning("Suche %s/%s fehlgeschlagen: %s", quelle, begriff, e)
            else:
                ergebnis.neu += neu
                ergebnis.aktualisiert += aktualisiert
                ergebnis.je_begriff[begriff] = ergebnis.je_begriff.get(begriff, 0) + len(jobs)
                for job in jobs:
                    gesehen.setdefault(job.ref, None)
            nummer += 1
    ergebnis.refs = list(gesehen)
    if merken:
        db.speichere_suche(
            {
                "begriffe": begriffe, "wo": ort.strip(), "umkreis": umkreis, "tage": tage,
                "max_treffer": max_treffer, "nur_vollzeit": bool(nur_vollzeit),
                "mit_details": bool(mit_details), "quellen": quellen,
            },
            ergebnis.refs,
        )
    if fortschritt:
        fortschritt(f"{len(ergebnis.refs)} Stellen gefunden.", 1.0)
    return ergebnis


def bewerte_neue(
    db: Speicher,
    profil: Profil,
    *,
    refs: list[str] | None = None,
    alle: bool = False,
    mit_llm: bool = True,
    limit: int | None = None,
    fortschritt: Fortschritt | None = None,
) -> list[Score]:
    """Bewertet unbewertete Stellen (oder mit `alle=True` auch bewertete neu)."""
    if refs is not None:
        jobs = db.jobs_nach_refs(refs)
        if not alle:
            jobs = [j for j in jobs if db.score(j.ref) is None]
    else:
        jobs = db.jobs(nur_ohne_score=not alle)
    if limit:
        jobs = jobs[:limit]
    if not jobs:
        return []

    scores: list[Score] = []
    # In kleinen Paketen bewerten und sofort speichern: bricht die Seite oder
    # der Prozess ab, bleibt der bisherige Fortschritt erhalten.
    paket = 5
    for start in range(0, len(jobs), paket):
        teil = llm_modul.bewerte_jobs(jobs[start:start + paket], profil, mit_llm=mit_llm)
        for score in teil:
            db.speichere_score(score)
        scores += teil
        if fortschritt:
            fortschritt(f"Bewerte Stellen … {len(scores)}/{len(jobs)}", len(scores) / len(jobs))
    return scores


# --------------------------------------------------------------------------- #
# Vorbereiten
# --------------------------------------------------------------------------- #

def _standard_browser(profil: Profil):
    from .bewerben.browser import BewerbungsBrowser

    return BewerbungsBrowser(profil=profil, headless=True, max_schritte=40)


def browser_verfuegbar() -> bool:
    try:
        import browser_use  # noqa: F401
    except ImportError:
        return False
    return True


def _probelauf(
    db: Speicher, profil: Profil, job: Job, v: Vorgang, browser_fabrik: Callable[[Profil], Any] | None,
) -> None:
    """Fuellt das Formular testweise aus und passt Kanal/Hinweis an das Ergebnis an."""
    if browser_fabrik is None and not browser_verfuegbar():
        v.hinweis = "Probelauf übersprungen: Browser-Agent (browser-use) ist nicht installiert."
        return
    try:
        browser = (browser_fabrik or _standard_browser)(profil)
        lauf = asyncio.run(browser.bewerbe(
            job, v.anschreiben or "", dry_run=True,
            start_url=v.bewerbungs_url, anschreiben_pdf=v.anschreiben_pdf,
        ))
    except Exception as e:
        v.hinweis = f"Probelauf fehlgeschlagen: {str(e)[:300]}"
        db.speichere_bewerbung(Application(
            ref=job.ref, status="fehlgeschlagen", url=v.bewerbungs_url,
            anschreiben=v.anschreiben, ergebnis=f"Probelauf: {e}"[:1000], dry_run=True,
        ))
        return

    roh = dict(lauf.rohdaten or {})
    roh.update({"zusammenfassung": lauf.zusammenfassung, "schritte": lauf.schritte,
                "zeitpunkt": _jetzt(), "letzte_url": roh.get("letzte_url") or lauf.url})
    v.probelauf = json.dumps(roh, ensure_ascii=False)
    db.speichere_bewerbung(lauf.als_application(v.anschreiben))

    email = (roh.get("bewerbungs_email") or "").strip()
    hindernis = (roh.get("hindernis") or "").strip()
    if email and "@" in email:
        v.kanal, v.empfaenger = "email", email.lower()
        v.hinweis = "Die Bewerbungsseite nennt eine E-Mail-Adresse – Versand per E-Mail."
    elif hindernis and _HINDERNIS_SELBST.search(hindernis) and not (
        "arbeitsagentur.de" in (v.bewerbungs_url or "") and profil.ba_benutzer and profil.ba_passwort
    ):
        v.kanal = "manuell"
        v.hinweis = f"Formular braucht Login/Captcha – bitte selbst bewerben. ({hindernis[:200]})"
    elif hindernis:
        v.hinweis = f"Probelauf-Hinweis: {hindernis[:300]}"


def vorbereiten(
    db: Speicher,
    profil: Profil,
    ref: str,
    *,
    probelauf: bool | None = None,
    herkunft: str = "manuell",
    mit_llm: bool = True,
    browser_fabrik: Callable[[Profil], Any] | None = None,
    link_aufloesen: bool = True,
) -> Vorgang:
    """Erzeugt Anschreiben, PDF und Versandweg; Ergebnis wartet auf Ja/Nein."""
    job = db.job(ref)
    if job is None:
        raise ValueError(f"Unbekannte Referenz: {ref}")
    vorhanden = db.vorgang(ref)
    if vorhanden and vorhanden.status in ("freigegeben", "wird_gesendet", "abgeschickt", "selbst_beworben"):
        return vorhanden

    v = vorhanden or Vorgang(ref=ref, herkunft=herkunft)
    v.status, v.hinweis, v.probelauf = "vorbereitung", None, None
    db.setze_vorgang(v)

    try:
        score = db.score(ref)
        wahl = kanal_modul.bestimme_kanal(job)
        v.kanal, v.empfaenger = wahl.kanal, wahl.empfaenger
        v.bewerbungs_url = job.bewerbungs_url
        if wahl.kanal == "formular" and link_aufloesen:
            v.bewerbungs_url = kanal_modul.loese_bewerbungslink(job) or job.bewerbungs_url
        v.anschreiben = erzeuge_anschreiben(job, profil, score, mit_llm=mit_llm)
        v.anschreiben_pdf = str(dokumente.anschreiben_pdf(job, profil, v.anschreiben))
        v.betreff = dokumente.betreff(job)
        v.email_text = mail.begleittext(job, profil, [v.anschreiben_pdf, *profil.anhaenge()])
        # Der Versandweg steht schon auf der Karte; Hinweise nur fuer Auffaelliges
        v.hinweis = wahl.grund if wahl.kanal == "manuell" or (wahl.kanal == "email" and "einzige" in wahl.grund) else None

        if probelauf is None:
            probelauf = profil.automatisierung.probelauf_vorab
        if v.kanal == "formular" and probelauf:
            _probelauf(db, profil, job, v, browser_fabrik)
    except Exception as e:
        log.exception("Vorbereitung von %s fehlgeschlagen", ref)
        v.hinweis = f"Vorbereitung unvollständig: {str(e)[:300]}"
    v.status = "wartet"
    db.setze_vorgang(v)
    return v


# --------------------------------------------------------------------------- #
# Entscheidungen des Nutzers
# --------------------------------------------------------------------------- #

def freigeben(
    db: Speicher,
    profil: Profil,
    ref: str,
    *,
    anschreiben: str | None = None,
    kanal: str | None = None,
    empfaenger: str | None = None,
    email_text: str | None = None,
) -> Vorgang:
    """Ja: die Bewerbung darf raus. Prueft, ob der gewaehlte Weg vollstaendig ist."""
    v = db.vorgang(ref)
    job = db.job(ref)
    if v is None or job is None:
        raise ValueError(f"Keine vorbereitete Bewerbung für {ref}.")
    if v.status not in ("wartet", "fehlgeschlagen"):
        raise ValueError(f"Bewerbung ist im Status „{v.status}“ und kann nicht freigegeben werden.")

    if kanal:
        v.kanal = kanal
    if empfaenger is not None:
        v.empfaenger = empfaenger.strip().lower() or None
    if email_text is not None:
        v.email_text = email_text
    if anschreiben is not None and anschreiben.strip() != (v.anschreiben or "").strip():
        v.anschreiben = anschreiben.strip()
        v.anschreiben_pdf = str(dokumente.anschreiben_pdf(job, profil, v.anschreiben))

    if not v.anschreiben:
        raise ValueError("Es gibt noch kein Anschreiben – bitte zuerst vorbereiten.")
    if v.kanal == "email" and not (v.empfaenger and "@" in v.empfaenger):
        raise ValueError("Für den E-Mail-Versand fehlt die Empfängeradresse.")
    if v.kanal == "formular" and not (v.bewerbungs_url or job.bewerbungs_url):
        raise ValueError("Für das Online-Formular fehlt die Bewerbungs-URL.")
    if v.kanal == "manuell":
        raise ValueError("Für diese Stelle gibt es keinen automatischen Weg – bitte selbst bewerben.")

    v.status = "freigegeben"
    v.freigegeben_am = _jetzt()
    v.hinweis = None
    db.setze_vorgang(v)
    return v


def ablehnen(
    db: Speicher,
    profil: Profil,
    ref: str,
    *,
    grund: str = "",
    arbeitgeber_sperren: bool = False,
    profil_pfad: str | None = None,
) -> Vorgang:
    """Nein: Stelle taucht nicht wieder auf (optional nie wieder dieser Arbeitgeber)."""
    v = db.vorgang(ref) or Vorgang(ref=ref)
    if v.status in ("wird_gesendet", "abgeschickt"):
        raise ValueError("Diese Bewerbung ist bereits unterwegs bzw. abgeschickt.")
    v.status = "abgelehnt"
    v.notiz = grund or v.notiz
    db.setze_vorgang(v)
    job = db.job(ref)
    if arbeitgeber_sperren and job and job.arbeitgeber:
        liste = profil.bewertung.ausschluss_arbeitgeber
        if job.arbeitgeber not in liste:
            liste.append(job.arbeitgeber)
            speichere_profil(profil, ermittle_profil_pfad(profil_pfad))
    return v


def selbst_beworben(db: Speicher, ref: str, notiz: str | None = None) -> Vorgang:
    """Der Nutzer hat sich selbst beworben (zaehlt fuer Wochenziel und Nachweis)."""
    v = db.vorgang(ref) or Vorgang(ref=ref, kanal="manuell")
    v.status = "selbst_beworben"
    v.gesendet_am = _jetzt()
    v.notiz = notiz or v.notiz
    db.setze_vorgang(v)
    job = db.job(ref)
    db.speichere_bewerbung(Application(
        ref=ref, status="manuell_beworben", url=job.bewerbungs_url if job else None,
        anschreiben=v.anschreiben, ergebnis="Selbst beworben", dry_run=False,
    ))
    return v


def zuruecksetzen(db: Speicher, ref: str) -> None:
    """Vorgang verwerfen, damit die Stelle wieder vorgeschlagen bzw. neu vorbereitet wird."""
    v = db.vorgang(ref)
    if v and v.status in ("wird_gesendet", "abgeschickt"):
        raise ValueError("Bereits gesendete Bewerbungen können nicht zurückgesetzt werden.")
    db.loesche_vorgang(ref)


def setze_rueckmeldung(db: Speicher, ref: str, rueckmeldung: str, notiz: str | None = None) -> None:
    felder: dict[str, Any] = {"rueckmeldung": rueckmeldung, "rueckmeldung_am": _jetzt() if rueckmeldung else None}
    if notiz is not None:
        felder["notiz"] = notiz
    db.aktualisiere_vorgang(ref, **felder)


# --------------------------------------------------------------------------- #
# Senden
# --------------------------------------------------------------------------- #

def aufraeumen(db: Speicher) -> int:
    """Haengengebliebene Vorgaenge (abgestuerzter Worker) in einen klaren Zustand bringen."""
    anzahl = 0
    for v in db.haengende_vorgaenge("wird_gesendet", minuten=30):
        db.aktualisiere_vorgang(
            v.ref, status="fehlgeschlagen",
            hinweis="Versand abgebrochen – bitte prüfen, ob die Bewerbung angekommen ist, "
                    "bevor du erneut freigibst.",
        )
        anzahl += 1
    for v in db.haengende_vorgaenge("vorbereitung", minuten=60):
        if v.anschreiben:
            db.aktualisiere_vorgang(v.ref, status="wartet", hinweis="Vorbereitung wurde unterbrochen.")
        else:
            db.loesche_vorgang(v.ref)
        anzahl += 1
    return anzahl


def _sende_email(db: Speicher, profil: Profil, job: Job, v: Vorgang, mail_sender: Callable | None) -> tuple[bool, str]:
    konfig = mail.smtp_konfig(profil)
    if konfig is None:
        return False, "E-Mail-Versand ist nicht eingerichtet (SMTP_HOST etc. in der .env fehlen)."
    anhaenge = [p for p in [v.anschreiben_pdf, *profil.anhaenge()] if p and Path(p).exists()]
    nachricht = mail.baue_nachricht(
        absender=konfig.absender, absender_name=profil.person.name,
        empfaenger=v.empfaenger or "", betreff=v.betreff or dokumente.betreff(job),
        text=v.email_text or mail.begleittext(job, profil, anhaenge), anhaenge=anhaenge,
    )
    message_id = (mail_sender or mail.sende)(nachricht, konfig)
    return True, f"E-Mail an {v.empfaenger} gesendet (Message-ID {message_id})."


def _sende_formular(
    db: Speicher, profil: Profil, job: Job, v: Vorgang, browser_fabrik: Callable[[Profil], Any] | None,
) -> tuple[bool, str]:
    if browser_fabrik is None and not browser_verfuegbar():
        return False, "Browser-Agent (browser-use) ist nicht installiert."
    browser = (browser_fabrik or _standard_browser)(profil)
    lauf = asyncio.run(browser.bewerbe(
        job, v.anschreiben or "", dry_run=False,
        start_url=v.bewerbungs_url, anschreiben_pdf=v.anschreiben_pdf,
    ))
    db.speichere_bewerbung(lauf.als_application(v.anschreiben))
    return lauf.abgeschickt, lauf.zusammenfassung


def sende_freigegebene(
    db: Speicher,
    profil: Profil,
    *,
    mail_sender: Callable | None = None,
    browser_fabrik: Callable[[Profil], Any] | None = None,
) -> dict[str, int]:
    """Verschickt alle freigegebenen Bewerbungen (auch solche, die waehrenddessen dazukommen)."""
    zaehler = {"abgeschickt": 0, "fehlgeschlagen": 0, "zurueckgestellt": 0}
    aufraeumen(db)
    bearbeitet: set[str] = set()
    while True:
        offen = [v for v in reversed(db.vorgaenge("freigegeben")) if v.ref not in bearbeitet]
        if not offen:
            break
        v = offen[0]
        bearbeitet.add(v.ref)

        heute = db.beworben_seit(tagesbeginn_utc(), status=("abgeschickt",))
        if heute >= profil.automatisierung.max_pro_tag:
            for rest in offen:
                db.aktualisiere_vorgang(
                    rest.ref,
                    hinweis=f"Tageslimit von {profil.automatisierung.max_pro_tag} Bewerbungen erreicht – "
                            "geht beim nächsten Lauf morgen raus.",
                )
            zaehler["zurueckgestellt"] += len(offen)
            break
        if not db.beanspruche_zum_senden(v.ref):
            continue
        v = db.vorgang(v.ref) or v  # frischer Stand nach dem Beanspruchen
        job = db.job(v.ref)
        try:
            if job is None:
                raise ValueError("Stelle nicht mehr in der Datenbank.")
            if not profil.unterlagen.lebenslauf or not profil.anhaenge():
                raise ValueError("Kein Lebenslauf hinterlegt – bitte im Profil hochladen.")
            if v.kanal == "email":
                ok, text = _sende_email(db, profil, job, v, mail_sender)
                if ok:
                    db.speichere_bewerbung(Application(
                        ref=v.ref, status="abgeschickt", url=f"mailto:{v.empfaenger}",
                        anschreiben=v.anschreiben, ergebnis=text, dry_run=False,
                    ))
            elif v.kanal == "formular":
                ok, text = _sende_formular(db, profil, job, v, browser_fabrik)
            else:
                ok, text = False, "Kein automatischer Bewerbungsweg."
        except Exception as e:
            log.exception("Versand %s fehlgeschlagen", v.ref)
            ok, text = False, str(e)[:500]
        if ok:
            db.aktualisiere_vorgang(v.ref, status="abgeschickt", gesendet_am=_jetzt(), hinweis=text[:1000])
            zaehler["abgeschickt"] += 1
        else:
            db.aktualisiere_vorgang(v.ref, status="fehlgeschlagen", hinweis=text[:1000])
            zaehler["fehlgeschlagen"] += 1
    return zaehler


# --------------------------------------------------------------------------- #
# Laeufe (Automatik, Vorbereiten, Senden) und Cron-Tick
# --------------------------------------------------------------------------- #

def automatik_lauf(
    db: Speicher,
    profil: Profil,
    *,
    ausloeser: str = "zeitplan",
    nur_suchen: bool = False,
    mit_llm: bool = True,
    browser_fabrik: Callable[[Profil], Any] | None = None,
) -> dict[str, Any]:
    """Suchen, bewerten und die besten offenen Stellen fuer das Ja/Nein vorbereiten."""
    lauf_id = db.starte_lauf("automatik", ausloeser)
    zusammenfassung: dict[str, Any] = {"nur_suchen": nur_suchen}
    try:
        suche = suche_ausfuehren(db, profil, merken=False)
        zusammenfassung.update(gefunden=len(suche.refs), neu=suche.neu, suchfehler=suche.fehler)
        scores = bewerte_neue(db, profil, mit_llm=mit_llm)
        schwelle = profil.bewertung.min_score
        zusammenfassung["bewertet"] = len(scores)
        zusammenfassung["passend"] = sum(1 for s in scores if not s.ausgeschlossen and s.gesamt >= schwelle)

        vorbereitet: list[str] = []
        if not nur_suchen:
            a = profil.automatisierung
            wartend = len(db.vorgaenge("wartet"))
            platz = max(0, min(a.max_pro_lauf, a.max_pro_lauf * MAX_WARTEND_FAKTOR - wartend))
            if platz == 0:
                zusammenfassung["hinweis"] = (
                    f"{wartend} Bewerbungen warten noch auf dein Ja/Nein – keine neuen vorbereitet."
                )
            kandidaten = db.kandidaten(
                min_score=schwelle, limit=platz,
                ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber,
            ) if platz else []
            for job, _score in kandidaten:
                try:
                    vorbereiten(db, profil, job.ref, herkunft="automatik", mit_llm=mit_llm,
                                browser_fabrik=browser_fabrik)
                    vorbereitet.append(job.ref)
                except Exception as e:
                    log.exception("Vorbereitung %s fehlgeschlagen", job.ref)
                    zusammenfassung.setdefault("fehler", []).append(f"{job.ref}: {e}")
        zusammenfassung["vorbereitet"] = len(vorbereitet)
    except Exception as e:
        db.beende_lauf(lauf_id, zusammenfassung, fehler=str(e)[:1000])
        raise
    db.beende_lauf(lauf_id, zusammenfassung)
    return zusammenfassung


def vorbereiten_lauf(
    db: Speicher,
    profil: Profil,
    refs: list[str],
    *,
    ausloeser: str = "knopf",
    mit_llm: bool = True,
    probelauf: bool | None = None,
    browser_fabrik: Callable[[Profil], Any] | None = None,
) -> dict[str, Any]:
    lauf_id = db.starte_lauf("vorbereiten", ausloeser)
    zusammenfassung: dict[str, Any] = {"angefragt": len(refs), "vorbereitet": 0}
    for ref in refs:
        try:
            vorbereiten(db, profil, ref, herkunft="manuell", mit_llm=mit_llm,
                        probelauf=probelauf, browser_fabrik=browser_fabrik)
            zusammenfassung["vorbereitet"] += 1
        except Exception as e:
            zusammenfassung.setdefault("fehler", []).append(f"{ref}: {e}")
    db.beende_lauf(lauf_id, zusammenfassung)
    return zusammenfassung


def senden_lauf(db: Speicher, profil: Profil, *, ausloeser: str = "knopf", **kwargs: Any) -> dict[str, Any]:
    """Versand mit Sperre, damit UI und Cron nicht gleichzeitig senden."""
    with sperre("senden") as frei:
        if not frei:
            return {"uebersprungen": "Versand läuft bereits"}
        if not db.vorgaenge("freigegeben"):
            aufraeumen(db)
            return {"abgeschickt": 0}
        lauf_id = db.starte_lauf("senden", ausloeser)
        try:
            ergebnis = sende_freigegebene(db, profil, **kwargs)
        except Exception as e:
            db.beende_lauf(lauf_id, {}, fehler=str(e)[:1000])
            raise
        db.beende_lauf(lauf_id, ergebnis)
        return ergebnis


def letzter_automatik_start(db: Speicher) -> datetime | None:
    laeufe = db.letzte_laeufe("automatik", limit=1)
    return zeitplan.als_datetime(laeufe[0]["gestartet_am"]) if laeufe else None


def naechster_automatik_termin(profil: Profil) -> datetime | None:
    if profil.automatisierung.effektiver_modus == "aus":
        return None
    try:
        return zeitplan.naechster_termin(profil.automatisierung.cron)
    except zeitplan.ZeitplanFehler:
        return None


def tick(
    db: Speicher,
    profil: Profil,
    *,
    jetzt: datetime | None = None,
    mit_llm: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Cron-Einstieg (alle paar Minuten): faelligen Automatiklauf starten, Freigaben senden."""
    ergebnis: dict[str, Any] = {}
    modus = profil.automatisierung.effektiver_modus
    if modus != "aus":
        try:
            faellig = zeitplan.faellig(profil.automatisierung.cron, letzter_automatik_start(db), jetzt)
        except zeitplan.ZeitplanFehler as e:
            ergebnis["zeitplan_fehler"] = str(e)
            faellig = False
        if faellig:
            with sperre("automatik") as frei:
                if frei:
                    ergebnis["automatik"] = automatik_lauf(
                        db, profil, ausloeser="zeitplan", nur_suchen=modus == "nur_suchen",
                        mit_llm=mit_llm, browser_fabrik=kwargs.get("browser_fabrik"),
                    )
                else:
                    ergebnis["automatik"] = "läuft bereits"
    ergebnis["senden"] = senden_lauf(db, profil, ausloeser="zeitplan", **kwargs)
    return ergebnis
