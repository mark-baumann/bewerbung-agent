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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import dokumente, dossier as dossier_modul, zeitplan
from .anschreiben import erzeuge_geprueft
from .bewerben import kanal as kanal_modul
from .bewerben import mail
from .config import Profil, daten_dir, ermittle_profil_pfad, speichere_profil
from .db import Speicher
from .hintergrund import sperre
from .jobsuche import WUNSCH_BEGRIFF, aktualisiere_feed, hole_jobs, hole_wunscharbeitgeber, lokale_treffer, orte_fuer
from .models import Application, Job, Score, Vorgang
from .scoring import heuristik
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


def beworben_heute(db: Speicher) -> int:
    """Heute verschickte plus bereits freigegebene Bewerbungen (fuer Tageslimits)."""
    return db.beworben_seit(tagesbeginn_utc(), status=("abgeschickt",)) + len(
        db.vorgaenge(("freigegeben", "wird_gesendet")))


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
    je_quelle: dict[str, int] = field(default_factory=dict)
    fehler: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


_ZEITARBEIT = re.compile(r"zeitarbeit|arbeitnehmer.?ueberlassung|arbeitnehmerüberlassung|personaldienstleist", re.I)


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
    """Fragt alle Quellen ab; ein Fehler bricht nur die eine Abfrage ab.

    Such-Quellen: eine Abfrage je Suchbegriff. Feed-Quellen: Bestand auffrischen
    (Mindestabstand beachten) und lokal je Suchbegriff/Ort filtern.
    Wunsch-Arbeitgeber: Stellen direkt aus deren Bewerbermanagement.
    """
    from .sources import QUELLEN, bekannte

    s = profil.suche
    begriffe = [b.strip() for b in (begriffe or s.was) if b and b.strip()]
    ort = ((s.wo if wo is None else wo) or "").strip()
    umkreis = int(s.umkreis if umkreis is None else umkreis)
    tage = int(s.veroeffentlicht_seit_tagen if tage is None else tage)
    max_treffer = int(s.max_pro_query if max_treffer is None else max_treffer)
    nur_vollzeit = s.nur_vollzeit if nur_vollzeit is None else nur_vollzeit
    quellen = bekannte(list(quellen or s.quellen or ["arbeitsagentur"])) or ["arbeitsagentur"]
    # Suchende Quellen zuerst - aus den Treffern der Arbeitsagentur lernen wir die Orte im Umkreis
    quellen.sort(key=lambda q: 0 if QUELLEN[q].art == "suche" else 1)
    extra = {
        "remote_ok": s.remote,
        "ohne_zeitarbeit": any(_ZEITARBEIT.search(a) for a in profil.bewertung.ausschluss),
        "ausbildung": s.ausbildung,
    }

    ergebnis = SuchErgebnis()
    gesehen: dict[str, None] = {}
    schritte = max(1, len(quellen) * len(begriffe))
    nummer = 0

    def uebernehmen(quelle: str, begriff: str, jobs: list[Job], neu: int = 0, aktualisiert: int = 0) -> None:
        ergebnis.neu += neu
        ergebnis.aktualisiert += aktualisiert
        ergebnis.je_begriff[begriff] = ergebnis.je_begriff.get(begriff, 0) + len(jobs)
        ergebnis.je_quelle[quelle] = ergebnis.je_quelle.get(quelle, 0) + len(jobs)
        for job in jobs:
            gesehen.setdefault(job.ref, None)

    for quelle in quellen:
        info = QUELLEN[quelle]
        if not info.verfuegbar():
            ergebnis.hinweise.append(f"{info.label}: API-Schlüssel fehlt (Einstellungen → Konten)")
            nummer += len(begriffe)
            continue

        if info.art == "suche":
            for begriff in begriffe:
                if fortschritt:
                    fortschritt(f"{info.label}: „{begriff}“ in {ort or 'ganz Deutschland'} …", nummer / schritte)
                try:
                    jobs, neu, aktualisiert = hole_jobs(
                        db, quelle=quelle, suchbegriff=begriff, wo=ort, umkreis=umkreis,
                        tage=tage, nur_vollzeit=nur_vollzeit, max_treffer=max_treffer,
                        mit_details=mit_details, profil=profil,
                        orte=orte_fuer(db, ort, umkreis), **extra,
                    )
                except Exception as e:
                    ergebnis.fehler.append(f"{info.label} / {begriff}: {e}")
                    log.warning("Suche %s/%s fehlgeschlagen: %s", quelle, begriff, e)
                else:
                    uebernehmen(quelle, begriff, jobs, neu, aktualisiert)
                nummer += 1
            continue

        orte = orte_fuer(db, ort, umkreis)
        if info.art == "feed":
            if fortschritt:
                fortschritt(f"{info.label}: Bestand abrufen …", nummer / schritte)
            try:
                neu, aktualisiert, _ = aktualisiere_feed(db, quelle, profil)
                ergebnis.neu += neu
                ergebnis.aktualisiert += aktualisiert
            except Exception as e:  # mit dem bisherigen Bestand weiterarbeiten
                ergebnis.fehler.append(f"{info.label}: {e}")
                log.warning("Feed %s fehlgeschlagen: %s", quelle, e)
            for begriff in begriffe:
                try:
                    jobs = lokale_treffer(
                        db, quelle, suchbegriff=begriff, orte=orte, remote_ok=s.remote, tage=tage,
                        nur_vollzeit=nur_vollzeit, max_treffer=max_treffer, mit_details=mit_details, profil=profil,
                    )
                except Exception as e:
                    ergebnis.fehler.append(f"{info.label} / {begriff}: {e}")
                else:
                    uebernehmen(quelle, begriff, jobs)
                nummer += 1
        elif info.art == "arbeitgeber":
            nummer += len(begriffe)
            if not profil.zielfirmen:
                continue
            if fortschritt:
                fortschritt(f"{info.label}: Karriereseiten abrufen …", nummer / schritte)
            try:
                jobs, fehler = hole_wunscharbeitgeber(
                    db, profil, orte=orte, remote_ok=s.remote, begriffe=begriffe, max_treffer=max_treffer * 2,
                )
                ergebnis.fehler += [f"{info.label} / {f}" for f in fehler]
                uebernehmen(quelle, WUNSCH_BEGRIFF, jobs)
            except Exception as e:
                ergebnis.fehler.append(f"{info.label}: {e}")

    ergebnis.refs = list(gesehen)
    if merken:
        db.speichere_suche(
            {
                "begriffe": begriffe, "wo": ort, "umkreis": umkreis, "tage": tage,
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
    fehler: list[str] | None = None,
    llm_budget: int | None = None,
) -> list[Score]:
    """Bewertet unbewertete Stellen (oder mit `alle=True` auch bewertete neu).

    Spart KI-Aufrufe: dieselbe Stelle aus einer anderen Quelle uebernimmt deren
    Bewertung, Stellen, die schon nach Stichworten klar nicht passen, werden nicht
    per KI bewertet, und je Lauf gibt es hoechstens `max_llm_pro_lauf` KI-Bewertungen
    (der Rest wird spaeter nachbewertet).
    """
    fehler = fehler if fehler is not None else []
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
    rest: list[Job] = []
    for job in jobs:
        dublette = None if alle else db.score_einer_dublette(job)
        if dublette is not None:
            uebernommen = replace(dublette, ref=job.ref)
            db.speichere_score(uebernommen)
            scores.append(uebernommen)
        else:
            rest.append(job)

    mit_ki: set[str] = set()
    if mit_llm and llm_modul.client_verfuegbar() and rest:
        b = profil.bewertung
        vorab = [(heuristik.bewerte(j, profil), j) for j in rest]
        lohnend = sorted(
            ((h, j) for h, j in vorab if not h.ausgeschlossen and h.gesamt >= b.vorfilter_min),
            key=lambda paar: -paar[0].gesamt,
        )
        budget = max(0, int(b.max_llm_pro_lauf if llm_budget is None else llm_budget))
        mit_ki = {j.ref for _, j in lohnend[:budget]}
        if len(lohnend) > budget:
            fehler.append(
                f"Kostenbremse: {len(lohnend) - budget} Stelle(n) vorerst nur per Stichworten bewertet "
                f"(höchstens {budget} KI-Bewertungen je Lauf) – sie werden in späteren Läufen nachbewertet."
            )

    # In kleinen Paketen bewerten und sofort speichern: bricht die Seite oder
    # der Prozess ab, bleibt der bisherige Fortschritt erhalten.
    paket = 5
    gesamt = len(jobs)
    for start in range(0, len(rest), paket):
        teil_jobs = rest[start:start + paket]
        ki = [j for j in teil_jobs if j.ref in mit_ki]
        ohne = [j for j in teil_jobs if j.ref not in mit_ki]
        teil = llm_modul.bewerte_jobs(ki, profil, mit_llm=True, fehler=fehler) if ki else []
        teil += llm_modul.bewerte_jobs(ohne, profil, mit_llm=False) if ohne else []
        for score in teil:
            db.speichere_score(score)
        scores += teil
        if fortschritt:
            fortschritt(f"Bewerte Stellen … {len(scores)}/{gesamt}", len(scores) / gesamt)
    return scores


def stichwort_bewertungen_auffrischen(db: Speicher, profil: Profil) -> int:
    """Nach neuem Lebenslauf (andere Faehigkeiten/Jobtitel): offene Stellen, die nur per Stichwort bewertet
    sind, neu bewerten – KI-Bewertungen bleiben, sie kennen den Lebenslauf schon."""
    refs = [job.ref for job, score in db.kandidaten(min_score=0, limit=1000) if score.bewerter == "heuristik"]
    if refs:
        bewerte_neue(db, profil, refs=refs, alle=True, mit_llm=False)
    return len(refs)


def nachbewerten(db: Speicher, profil: Profil, *, budget: int, fehler: list[str] | None = None) -> list[Score]:
    """Stellen, die bisher nur per Stichworten bewertet wurden, mit KI neu bewerten."""
    if budget <= 0 or not llm_modul.client_verfuegbar():
        return []
    refs = db.heuristisch_bewertet(min_gesamt=profil.bewertung.vorfilter_min, limit=budget)
    if not refs:
        return []
    return bewerte_neue(db, profil, refs=refs, alle=True, mit_llm=True, fehler=fehler, llm_budget=budget)


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
        from .bewerben.browser import ist_startfehler, startfehler_text

        if ist_startfehler(str(e)):
            # Der Browser kam nicht hoch - das sagt nichts ueber das Formular
            v.hinweis = f"Formular-Test war nicht möglich: {startfehler_text(str(e))}"
            return
        if isinstance(e, llm_modul.LLMNichtVerfuegbar):
            v.hinweis = f"Formular-Test war nicht möglich: {e}"
            return
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
    if (roh.get("browser_startfehler") or roh.get("ki_fehler")) and not roh.get("versehentlich_abgeschickt"):
        # Der Browser kam nicht hoch - das sagt nichts ueber das Formular.
        # Ein versehentliches Absenden geht immer vor (siehe unten), sonst droht ein zweites Senden.
        v.hinweis = f"Formular-Test war nicht möglich: {lauf.zusammenfassung}"
        return
    db.speichere_bewerbung(lauf.als_application(v.anschreiben))

    email = (roh.get("bewerbungs_email") or "").strip()
    hindernis = (roh.get("hindernis") or "").strip()
    if roh.get("versehentlich_abgeschickt"):
        # Nie ein zweites Mal senden - als abgeschickt fuehren und pruefen lassen
        v.status = "abgeschickt"
        v.gesendet_am = _jetzt()
        v.hinweis = ("Der Probelauf hat die Bewerbung offenbar schon abgeschickt – bitte im Postfach prüfen, "
                     "ob eine Bestätigung kam.")
        return
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
        wahl = kanal_modul.bestimme_kanal(job, ba_login=bool(profil.ba_benutzer and profil.ba_passwort))
        v.kanal, v.empfaenger = wahl.kanal, wahl.empfaenger
        v.bewerbungs_url = job.bewerbungs_url
        if wahl.kanal == "formular" and link_aufloesen:
            v.bewerbungs_url = kanal_modul.loese_bewerbungslink(job) or job.bewerbungs_url
        d = dossier_modul.fuer_job(db, job, profil, mit_llm=mit_llm)
        ergebnis = erzeuge_geprueft(job, profil, score, mit_llm=mit_llm, dossier=d)
        v.anschreiben = v.anschreiben_original = ergebnis.text
        v.anschreiben_quelle, v.qualitaet = ergebnis.quelle, ergebnis.qualitaet
        bericht = dict(ergebnis.bericht)
        bericht["empfaenger_sicher"] = wahl.kanal == "email" and "einzige" not in wahl.grund
        v.pruefung = json.dumps(bericht, ensure_ascii=False)
        v.anschreiben_pdf = str(dokumente.anschreiben_pdf(job, profil, v.anschreiben))
        v.betreff = dokumente.betreff(job, referenz=d.referenz)
        v.email_text = mail.begleittext(job, profil, [v.anschreiben_pdf, *profil.anhaenge()],
                                        anrede=d.anrede if d.sprache == "de" else None, referenz=d.referenz)
        # Der Versandweg steht schon auf der Karte; Hinweise nur fuer Auffaelliges
        v.hinweis = wahl.grund if wahl.kanal == "manuell" or (wahl.kanal == "email" and "einzige" in wahl.grund) else None
        if job.bewerbung_hinweis and wahl.kanal != "manuell" and "Unterlagen" in job.bewerbung_hinweis:
            v.hinweis = " · ".join(x for x in (v.hinweis, job.bewerbung_hinweis) if x)

        if probelauf is None:
            probelauf = profil.automatisierung.probelauf_vorab
        if v.kanal == "formular" and probelauf:
            _probelauf(db, profil, job, v, browser_fabrik)
    except Exception as e:
        log.exception("Vorbereitung von %s fehlgeschlagen", ref)
        v.hinweis = f"Vorbereitung unvollständig: {str(e)[:300]}"
    if v.status == "vorbereitung":
        v.status = "wartet"
    db.setze_vorgang(v)
    if herkunft == "automatik" and v.status == "wartet":
        from . import autopilot

        try:
            autopilot.nach_vorbereitung(db, profil, v)
        except Exception:
            log.exception("Autopilot-Prüfung für %s fehlgeschlagen", ref)
        v = db.vorgang(ref) or v
    return v


# --------------------------------------------------------------------------- #
# Entscheidungen des Nutzers
# --------------------------------------------------------------------------- #

def versandweg_fehlt(kanal: str, profil: Profil | None = None, *, browser_fabrik: Any = None) -> str | None:
    """Warum der gewaehlte Weg gerade nicht nutzbar ist (None = alles bereit)."""
    if kanal == "email" and mail.smtp_konfig(profil) is None:
        return "Dein E-Mail-Konto ist noch nicht verbunden (Einstellungen → Konten)."
    if kanal == "formular" and browser_fabrik is None:
        if not browser_verfuegbar():
            return "Der Browser-Agent (browser-use) ist nicht installiert."
        if not llm_modul.client_verfuegbar():
            return "Der Browser-Agent braucht einen KI-Zugang (Einstellungen → Konten)."
    return None


def freigeben(
    db: Speicher,
    profil: Profil,
    ref: str,
    *,
    anschreiben: str | None = None,
    kanal: str | None = None,
    empfaenger: str | None = None,
    email_text: str | None = None,
    durch: str = "nutzer",
) -> Vorgang:
    """Ja: die Bewerbung darf raus. Prueft, ob der gewaehlte Weg vollstaendig ist.

    `durch="autopilot"`, wenn der (vom Nutzer eingeschaltete) Autopilot freigibt.
    """
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
    v.zurueckgestellt_seit = None
    fehlt = versandweg_fehlt(v.kanal, profil)
    v.hinweis = f"Geht raus, sobald das erledigt ist: {fehlt}" if fehlt else None
    v.auto_senden_ab = None
    db.setze_vorgang(v)
    db.protokolliere_entscheidung(ref, "autopilot" if durch == "autopilot" else "ja",
                                  details={"kanal": v.kanal, "bearbeitet": _wurde_bearbeitet(v),
                                           "qualitaet": v.qualitaet})
    return v


def _wurde_bearbeitet(v: Vorgang) -> bool:
    return bool(v.anschreiben_original) and (v.anschreiben or "").strip() != v.anschreiben_original.strip()


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
    v.auto_senden_ab = None
    db.setze_vorgang(v)
    gruende = [g.strip() for g in re.split(r"[;,]\s*", grund or "") if g.strip()]
    db.protokolliere_entscheidung(ref, "nein", gruende=gruende,
                                  details={"arbeitgeber_gesperrt": arbeitgeber_sperren})
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
    v.auto_senden_ab = None
    db.setze_vorgang(v)
    db.protokolliere_entscheidung(ref, "selbst")
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
        # Unklar, ob etwas rausging: nie automatisch erneut senden, sondern nachfragen
        _selbst_bewerben_lassen(db, v, vielleicht_raus=True)
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
        return False, "Dein E-Mail-Konto ist noch nicht verbunden (Einstellungen → Konten)."
    anhaenge = [p for p in [v.anschreiben_pdf, *profil.anhaenge()] if p and Path(p).exists()]
    nachricht = mail.baue_nachricht(
        absender=konfig.absender, absender_name=profil.person.name,
        empfaenger=v.empfaenger or "", betreff=v.betreff or dokumente.betreff(job),
        text=v.email_text or mail.begleittext(job, profil, anhaenge), anhaenge=anhaenge,
    )
    message_id = (mail_sender or mail.sende)(nachricht, konfig)
    db.aktualisiere_vorgang(v.ref, message_id=message_id or nachricht["Message-ID"])
    return True, f"E-Mail an {v.empfaenger} gesendet."


@dataclass
class FormularErgebnis:
    ok: bool
    text: str
    gearbeitet: bool = False   # die KI hat auf der Bewerbungsseite wirklich etwas getan
    startfehler: bool = False  # der Browser kam gar nicht erst hoch
    ki_fehler: bool = False    # die KI hat nicht geantwortet (z. B. Konto ohne Guthaben)


def _sende_formular(
    db: Speicher, profil: Profil, job: Job, v: Vorgang, browser_fabrik: Callable[[Profil], Any] | None,
) -> FormularErgebnis:
    if browser_fabrik is None and not browser_verfuegbar():
        return FormularErgebnis(False, "Browser-Agent (browser-use) ist nicht installiert.", startfehler=True)
    try:
        browser = (browser_fabrik or _standard_browser)(profil)
        lauf = asyncio.run(browser.bewerbe(
            job, v.anschreiben or "", dry_run=False,
            start_url=v.bewerbungs_url, anschreiben_pdf=v.anschreiben_pdf,
        ))
    except llm_modul.LLMNichtVerfuegbar as e:
        # Ohne KI startet der Agent gar nicht erst - nichts wurde ausgefuellt
        return FormularErgebnis(False, str(e), ki_fehler=True)
    except Exception as e:
        from .bewerben.browser import ist_startfehler, startfehler_text

        if ist_startfehler(str(e)):
            # Auch nach den Wiederholungen kein Browser - nichts wurde abgeschickt
            return FormularErgebnis(False, startfehler_text(str(e)), startfehler=True)
        raise
    roh = lauf.rohdaten or {}
    gearbeitet = lauf.gearbeitet if lauf.gearbeitet is not None else lauf.schritte > 0
    # Nur wenn nie etwas auf der Seite passiert ist, darf die Freigabe stehen bleiben (sonst Doppelversand)
    startfehler = bool(roh.get("browser_startfehler")) and lauf.schritte <= 1
    ki_fehler = bool(roh.get("ki_fehler")) and not gearbeitet and not lauf.abgeschickt
    if not (startfehler or ki_fehler):
        db.speichere_bewerbung(lauf.als_application(v.anschreiben))
    if gearbeitet:
        _ki_pause_merken(None)  # die KI antwortet wieder
    return FormularErgebnis(lauf.abgeschickt, lauf.zusammenfassung,
                            gearbeitet=gearbeitet and not (startfehler or ki_fehler),
                            startfehler=startfehler, ki_fehler=ki_fehler)


# Wie lange eine Formular-Bewerbung auf Browser/KI warten darf, bevor sie als Karte zum Selbstbewerben kommt
WARTEN_STUNDEN = 48

HINWEIS_SELBST = ("Das Formular ließ sich nicht automatisch ausfüllen – bitte selbst bewerben, das Anschreiben "
                  "ist fertig.")
LEBENSLAUF_FEHLT = "Die Lebenslauf-Datei fehlt – bitte unter „Lebenslauf“ erneut hochladen."
HINWEIS_VIELLEICHT = ("Ich war schon im Formular, bin mir aber nicht sicher, ob es abgeschickt wurde. Kam eine "
                      "Bestätigung? Dann einfach „Ja, erledigt“ – sonst bitte selbst bewerben.")
HINWEIS_VIELLEICHT_MAIL = ("Der E-Mail-Versand wurde unterbrochen – vielleicht ist sie schon raus. Liegt bei dir eine "
                           "Kopie im Postfach? Dann einfach „Ja, erledigt“ – sonst bitte selbst bewerben.")
HINWEISE_VIELLEICHT = (HINWEIS_VIELLEICHT, HINWEIS_VIELLEICHT_MAIL)
# So begannen fruehere Hinweise, wenn die Bewerbung vielleicht schon raus war
_VIELLEICHT_FRUEHER = ("Nicht sicher abgeschickt", "Versand abgebrochen")
# Fruehere Hinweise, bei denen sicher nichts auf der Bewerbungsseite passiert ist
_NICHTS_PASSIERT = re.compile(
    r"^(Keine Bewerbungs-URL hinterlegt|Kein Lebenslauf hinterlegt|(Der )?Browser-Agent \(browser-use\) ist nicht "
    r"installiert|Nichts ausgefüllt oder abgeschickt|Stelle nicht mehr in der Datenbank)"
)


def _auf_email_umstellen(db: Speicher, profil: Profil, job: Job | None, v: Vorgang) -> bool:
    """Formular geht nicht: mit sicherer Bewerbungsadresse und verbundenem Konto per E-Mail bewerben.

    Die Bewerbung hatte schon das Ja des Nutzers; nur der Weg aendert sich. Nur aufrufen, wenn auf der
    Bewerbungsseite nichts passiert ist (sonst koennte sie doppelt ankommen).
    """
    if job is None or v.kanal != "formular" or mail.smtp_konfig(profil) is None:
        return False
    adresse, sicher = kanal_modul.sichere_bewerbungs_email(job, v.probelauf_daten)
    if not (adresse and sicher):
        return False
    db.aktualisiere_vorgang(v.ref, status="freigegeben", kanal="email", empfaenger=adresse,
                            hinweis=f"Das Formular ging nicht – ich bewerbe mich per E-Mail an {adresse}.")
    return True


def _selbst_bewerben_lassen(db: Speicher, v: Vorgang, *, vielleicht_raus: bool = False, text: str = "") -> None:
    """Statt einer Fehlermeldung: die Stelle kommt als normale Ja/Nein-Karte „Selbst bewerben“ zurueck."""
    if text:
        log.info("Bewerbung %s geht an den Nutzer zurueck: %s", v.ref, text[:300])
    hinweis = HINWEIS_SELBST
    if vielleicht_raus:
        hinweis = HINWEIS_VIELLEICHT_MAIL if v.kanal == "email" else HINWEIS_VIELLEICHT
    db.aktualisiere_vorgang(v.ref, status="wartet", kanal="manuell", auto_senden_ab=None,
                            zurueckgestellt_seit=None, hinweis=hinweis)


def _email_zurueck(db: Speicher, v: Vorgang, text: str) -> None:
    """E-Mail ging nicht raus (z. B. Server weg, Passwort falsch): als Karte zurueck, „Ja“ versucht es erneut."""
    log.info("E-Mail-Bewerbung %s nicht gesendet: %s", v.ref, text[:300])
    grund = re.sub(r"\s+", " ", text).strip()[:200] or "unbekannter Fehler"
    db.aktualisiere_vorgang(
        v.ref, status="wartet", auto_senden_ab=None, zurueckgestellt_seit=None,
        hinweis=(f"Die E-Mail an {v.empfaenger or '?'} ging nicht raus ({grund}). Bitte unter Einstellungen → Konten "
                 "dein E-Mail-Konto prüfen – mit „Ja“ versuche ich es noch einmal."),
    )


def _zurueckstellen_seit(v: Vorgang) -> dict[str, str]:
    """Felder fuer das erste Warten auf Browser/KI (die 48 Stunden zaehlen ab dann, nicht ab dem Ja)."""
    return {} if v.zurueckgestellt_seit else {"zurueckgestellt_seit": _jetzt()}


def _wartet_zu_lange(v: Vorgang) -> bool:
    seit = zeitplan.als_datetime(v.zurueckgestellt_seit)
    return bool(seit) and datetime.now(timezone.utc) - seit > timedelta(hours=WARTEN_STUNDEN)


def fehlgeschlagene_umleiten(db: Speicher, profil: Profil) -> int:
    """Alte Vorgaenge im Status „fehlgeschlagen“ (fruehere Versionen) in den neuen Ablauf bringen.

    Nie doppelt senden: Wer weiss, ob die Bewerbung schon raus ist, fragt nach („Ja, erledigt“?). Per
    E-Mail weiter geht es nur, wenn der fruehere Hinweis zeigt, dass auf der Bewerbungsseite nichts
    passiert ist. Fehler beim E-Mail-Versand kommen als Karte zurueck, deren „Ja“ es erneut versucht.
    """
    from .bewerben.browser import ist_startfehler

    anzahl = 0
    for v in db.vorgaenge("fehlgeschlagen"):
        hinweis = (v.hinweis or "").strip()
        if hinweis.startswith(_VIELLEICHT_FRUEHER):
            _selbst_bewerben_lassen(db, v, vielleicht_raus=True)
        elif v.kanal == "email":
            _email_zurueck(db, v, hinweis)
        elif v.kanal == "formular" and (_NICHTS_PASSIERT.match(hinweis) or ist_startfehler(hinweis)):
            if not _auf_email_umstellen(db, profil, db.job(v.ref), v):
                _selbst_bewerben_lassen(db, v)
        else:
            # Unbekannt, wie weit das Formular kam (z. B. Absturz mitten im Lauf): lieber nachfragen
            _selbst_bewerben_lassen(db, v, vielleicht_raus=v.kanal == "formular")
        anzahl += 1
    return anzahl


# Nach einem gescheiterten Browser-Selbsttest so lange nicht erneut testen (der Tick laeuft alle 5 Minuten)
BROWSER_PAUSE_MIN = 30


def _browser_bereit(browser_fabrik: Callable[[Profil], Any] | None) -> tuple[bool, str]:
    """Kann der Server gerade einen Browser starten? Testet hoechstens alle 30 Minuten neu."""
    if browser_fabrik is not None:
        return True, ""
    from .bewerben import browser as browser_modul

    letzter = browser_modul.letzter_selbsttest()
    if letzter and not letzter.get("ok"):
        zeit = zeitplan.als_datetime(letzter.get("zeit"))
        if zeit and datetime.now(timezone.utc) - zeit < timedelta(minutes=BROWSER_PAUSE_MIN):
            return False, letzter.get("meldung") or "Browser startet nicht."
    test = browser_modul.selbsttest()
    return test.ok, test.meldung


# Nach einem KI-Ausfall (z. B. Konto ohne Guthaben) so lange keinen neuen Formular-Versuch starten
KI_PAUSE_MIN = 30


def _ki_pause_datei() -> Path:
    return daten_dir() / "ki_pause.json"


def ki_pausiert() -> str | None:
    """Meldung des letzten KI-Ausfalls, solange er weniger als 30 Minuten her ist (auch aus anderen Prozessen)."""
    try:
        daten = json.loads(_ki_pause_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    zeit = zeitplan.als_datetime(daten.get("zeit")) if isinstance(daten, dict) else None
    if zeit and datetime.now(timezone.utc) - zeit < timedelta(minutes=KI_PAUSE_MIN):
        return str(daten.get("meldung") or "Die KI antwortet gerade nicht.")
    return None


def _ki_pause_merken(meldung: str | None) -> None:
    try:
        if meldung is None:
            _ki_pause_datei().unlink(missing_ok=True)
        else:
            _ki_pause_datei().parent.mkdir(parents=True, exist_ok=True)
            _ki_pause_datei().write_text(json.dumps({"zeit": _jetzt(), "meldung": meldung[:300]}, ensure_ascii=False),
                                         encoding="utf-8")
    except OSError:
        pass


def sende_freigegebene(
    db: Speicher,
    profil: Profil,
    *,
    mail_sender: Callable | None = None,
    browser_fabrik: Callable[[Profil], Any] | None = None,
) -> dict[str, int]:
    """Verschickt alle freigegebenen Bewerbungen (auch solche, die waehrenddessen dazukommen).

    Klappt ein Formular nicht, wird ausgewichen statt ein Fehler gezeigt: per E-Mail, wenn es eine
    sichere Bewerbungsadresse gibt, sonst als Karte „Selbst bewerben“. Kam der Browser oder die KI gar
    nicht erst hoch, bleibt die Freigabe stehen und der naechste Lauf versucht es erneut.
    """
    zaehler = {"abgeschickt": 0, "fehlgeschlagen": 0, "zurueckgestellt": 0}
    aufraeumen(db)
    fehlgeschlagene_umleiten(db, profil)
    bearbeitet: set[str] = set()
    browser_status: tuple[bool, str] | None = None  # einmal je Lauf pruefen
    pause = ki_pausiert()
    ki_status: tuple[bool, str] = (False, pause) if pause else (True, "")
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
        fehlt = None if profil.lebenslauf_pfad() else LEBENSLAUF_FEHLT
        if not fehlt:
            fehlt = versandweg_fehlt(v.kanal, profil, browser_fabrik=browser_fabrik)
            if not fehlt and v.kanal == "formular":
                if browser_status is None:
                    browser_status = _browser_bereit(browser_fabrik)
                if not browser_status[0]:
                    fehlt = f"Der Browser startet auf dem Server gerade nicht ({browser_status[1]})."
                elif not ki_status[0]:
                    fehlt = ki_status[1]
            if fehlt and v.kanal == "formular" and _auf_email_umstellen(db, profil, db.job(v.ref), v):
                bearbeitet.discard(v.ref)  # gleich per E-Mail
                continue
            if fehlt and v.kanal == "formular":
                if _wartet_zu_lange(v):
                    _selbst_bewerben_lassen(db, v, text=fehlt)
                    zaehler["fehlgeschlagen"] += 1
                    continue
                db.aktualisiere_vorgang(v.ref, **_zurueckstellen_seit(v))
        if fehlt:
            db.aktualisiere_vorgang(v.ref, hinweis=f"Geht raus, sobald das erledigt ist: {fehlt}")
            zaehler["zurueckgestellt"] += 1
            continue
        if not db.beanspruche_zum_senden(v.ref):
            continue
        v = db.vorgang(v.ref) or v  # frischer Stand nach dem Beanspruchen
        job = db.job(v.ref)
        f = FormularErgebnis(False, "")
        try:
            if job is None:
                raise ValueError("Stelle nicht mehr in der Datenbank.")
            if v.kanal == "email":
                ok, text = _sende_email(db, profil, job, v, mail_sender)
                if ok:
                    db.speichere_bewerbung(Application(
                        ref=v.ref, status="abgeschickt", url=f"mailto:{v.empfaenger}",
                        anschreiben=v.anschreiben, ergebnis=text, dry_run=False,
                    ))
            elif v.kanal == "formular":
                f = _sende_formular(db, profil, job, v, browser_fabrik)
                ok, text = f.ok, f.text
            else:
                ok, text = False, "Kein automatischer Bewerbungsweg."
        except Exception as e:
            log.exception("Versand %s fehlgeschlagen", v.ref)
            # Unbekannt, wie weit ein Formular kam: vorsichtshalber wie „gearbeitet“ behandeln
            ok, text = False, str(e)[:500]
            f = FormularErgebnis(False, text, gearbeitet=v.kanal == "formular" and job is not None)
        if ok:
            db.aktualisiere_vorgang(v.ref, status="abgeschickt", gesendet_am=_jetzt(), hinweis=text[:1000])
            zaehler["abgeschickt"] += 1
        elif f.startfehler or f.ki_fehler:
            # Nichts ist passiert - die Freigabe bleibt bestehen; mit sicherer Adresse sofort per E-Mail
            if f.startfehler:
                browser_status = (False, text)
            else:
                ki_status = (False, text)
                _ki_pause_merken(text)
            if _auf_email_umstellen(db, profil, job, v):
                bearbeitet.discard(v.ref)
                continue
            db.aktualisiere_vorgang(v.ref, status="freigegeben", **_zurueckstellen_seit(v),
                                    hinweis=f"Geht raus, sobald es wieder geht: {text}"[:1000])
            zaehler["zurueckgestellt"] += 1
        elif v.kanal == "formular" and not f.gearbeitet and _auf_email_umstellen(db, profil, job, v):
            bearbeitet.discard(v.ref)  # Formular ging nicht, nichts abgeschickt: per E-Mail
        elif v.kanal == "email":
            _email_zurueck(db, v, text)
            zaehler["fehlgeschlagen"] += 1
        else:
            # Kein Fehlerbildschirm: die Stelle kommt als Ja/Nein-Karte „Selbst bewerben“ zurueck
            _selbst_bewerben_lassen(db, v, vielleicht_raus=v.kanal == "formular" and f.gearbeitet, text=text)
            zaehler["fehlgeschlagen"] += 1
    return zaehler


# --------------------------------------------------------------------------- #
# Laeufe (Automatik, Vorbereiten, Senden) und Cron-Tick
# --------------------------------------------------------------------------- #

def _zu_allgemein(begriff: str) -> bool:
    from .vorschlaege import zu_allgemein

    return zu_allgemein(begriff)


def erkunde_suchbegriffe(
    db: Speicher, profil: Profil, *, mit_llm: bool = True, fehler: list[str] | None = None,
) -> dict[str, list[str]]:
    """Probiert selbststaendig neue Suchbegriffe aus und behaelt nur, was passende Stellen bringt.

    Kandidaten kommen aus den Titeln gut passender Stellen, bei Bedarf von der KI.
    Bewaehrte Begriffe nutzt die Automatik weiter und schlaegt sie zur Uebernahme
    ins Profil vor; tote Begriffe werden verworfen.
    """
    from . import lernen, vorschlaege
    from .sources import QUELLEN

    a = profil.automatisierung
    ergebnis: dict[str, list[str]] = {"aktiv": [], "verworfen": []}
    if not a.erkunden or a.erkundung_max_begriffe <= 0:
        return ergebnis
    bekannt = {b["begriff"].casefold() for b in db.suchbegriffe()} | {w.casefold() for w in profil.suche.was}
    kandidaten = [v.begriff for v in vorschlaege.aus_treffern(db, profil, limit=10)
                  if v.begriff.casefold() not in bekannt and not _zu_allgemein(v.begriff)]
    if len(kandidaten) < a.erkundung_max_begriffe and mit_llm and llm_modul.client_verfuegbar():
        gute = [j.titel for j, _ in db.bestenliste(min_score=profil.bewertung.min_score, limit=20)]
        try:
            kandidaten += [v.begriff for v in vorschlaege.per_llm(profil, gute, limit=6)
                           if v.begriff.casefold() not in bekannt and v.begriff not in kandidaten
                           and not _zu_allgemein(v.begriff)]
        except Exception as e:
            log.info("KI-Suchvorschläge nicht verfügbar: %s", e)
    quellen = [q for q in profil.suche.quellen if q in QUELLEN and QUELLEN[q].art != "arbeitgeber"]
    for begriff in kandidaten[: a.erkundung_max_begriffe]:
        db.merke_suchbegriff(begriff, "erkundung", "test")
        such = suche_ausfuehren(db, profil, begriffe=[begriff], quellen=quellen,
                                max_treffer=min(20, profil.suche.max_pro_query), merken=False)
        bewerte_neue(db, profil, refs=such.refs, mit_llm=mit_llm, fehler=fehler, llm_budget=10)
        passend = 0
        for ref in such.refs:
            sc = db.score(ref)
            if sc and not sc.ausgeschlossen and sc.gesamt >= profil.bewertung.min_score:
                passend += 1
        db.suchbegriff_ertrag(begriff, len(such.refs), passend)
        if passend:
            db.merke_suchbegriff(begriff, "erkundung", "aktiv", f"{passend} passende Stelle(n)")
            lernen.schlage_suchbegriff_vor(db, begriff, passend)
            ergebnis["aktiv"].append(begriff)
        else:
            db.merke_suchbegriff(begriff, "erkundung", "verworfen", "keine passenden Stellen")
            ergebnis["verworfen"].append(begriff)
    return ergebnis


def automatik_lauf(
    db: Speicher,
    profil: Profil,
    *,
    ausloeser: str = "zeitplan",
    nur_suchen: bool = False,
    mit_llm: bool = True,
    browser_fabrik: Callable[[Profil], Any] | None = None,
) -> dict[str, Any]:
    """Suchen, bewerten, dazulernen und die besten offenen Stellen fuer das Ja/Nein vorbereiten."""
    from . import lernen

    lauf_id = db.starte_lauf("automatik", ausloeser)
    zusammenfassung: dict[str, Any] = {"nur_suchen": nur_suchen}
    ki_hinweise: list[str] = []
    try:
        erkundet = [b["begriff"] for b in db.suchbegriffe("aktiv")
                    if b["herkunft"] == "erkundung" and b["begriff"] not in profil.suche.was]
        suche = suche_ausfuehren(db, profil, begriffe=[*profil.suche.was, *erkundet], merken=False)
        zusammenfassung.update(gefunden=len(suche.refs), neu=suche.neu, suchfehler=suche.fehler,
                               je_quelle=suche.je_quelle, hinweise=suche.hinweise)
        scores = bewerte_neue(db, profil, mit_llm=mit_llm, fehler=ki_hinweise)
        if mit_llm:
            genutzt = sum(1 for sc in scores if sc.bewerter.startswith("llm"))
            nachbewertet = nachbewerten(db, profil, budget=profil.bewertung.max_llm_pro_lauf - genutzt,
                                        fehler=ki_hinweise)
            if nachbewertet:
                zusammenfassung["nachbewertet"] = len(nachbewertet)
        try:
            erkundung = erkunde_suchbegriffe(db, profil, mit_llm=mit_llm, fehler=ki_hinweise)
            if any(erkundung.values()):
                zusammenfassung["erkundung"] = erkundung
        except Exception as e:
            log.exception("Erkundung fehlgeschlagen")
            ki_hinweise.append(f"Erkundung neuer Suchbegriffe fehlgeschlagen: {e}")
        schwelle = profil.bewertung.min_score
        zusammenfassung["bewertet"] = len(scores)
        zusammenfassung["passend"] = sum(1 for sc in scores if not sc.ausgeschlossen and sc.gesamt >= schwelle)

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
                min_score=schwelle, limit=platz * 3,
                ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber,
            ) if platz else []
            # Gelernte Vorlieben beeinflussen die Reihenfolge (nicht die Schwelle)
            vorlieben = lernen.vorlieben(db)
            kandidaten.sort(key=lambda paar: -(paar[1].gesamt + vorlieben.anpassung(paar[0])))
            for job, _score in kandidaten[:platz]:
                try:
                    vorbereiten(db, profil, job.ref, herkunft="automatik", mit_llm=mit_llm,
                                browser_fabrik=browser_fabrik)
                    vorbereitet.append(job.ref)
                except Exception as e:
                    log.exception("Vorbereitung %s fehlgeschlagen", job.ref)
                    zusammenfassung.setdefault("fehler", []).append(f"{job.ref}: {e}")
        zusammenfassung["vorbereitet"] = len(vorbereitet)
        autopilot_status = [db.vorgang(r) for r in vorbereitet]
        sofort = sum(1 for v in autopilot_status if v and v.status == "freigegeben")
        mit_frist = sum(1 for v in autopilot_status if v and v.status == "wartet" and v.auto_senden_ab)
        if sofort or mit_frist:
            zusammenfassung["autopilot"] = {"freigegeben": sofort, "mit_frist": mit_frist}
        try:
            neue_vorschlaege = lernen.leite_vorschlaege_ab(db, profil, mit_llm=mit_llm)
            if neue_vorschlaege:
                zusammenfassung["vorschlaege"] = neue_vorschlaege
        except Exception as e:
            log.exception("Vorschläge ableiten fehlgeschlagen")
            ki_hinweise.append(f"Lernen aus Entscheidungen fehlgeschlagen: {e}")
        if ki_hinweise:
            zusammenfassung["ki_hinweise"] = ki_hinweise
    except Exception as e:
        db.beende_lauf(lauf_id, zusammenfassung, fehler=str(e)[:1000])
        raise
    fehler = None
    if suche.fehler and not suche.refs:
        fehler = "Alle Abfragen sind fehlgeschlagen: " + "; ".join(suche.fehler[:3])
    db.beende_lauf(lauf_id, zusammenfassung, fehler=fehler)
    return zusammenfassung


def postfach_lauf(db: Speicher, profil: Profil, *, ausloeser: str = "zeitplan",
                  mindestabstand_min: int = 30, verbindung: Any = None) -> dict[str, Any] | None:
    """Antworten im Postfach auswerten (hoechstens alle 30 Minuten)."""
    from .bewerben import postfach

    if not profil.automatisierung.postfach or (verbindung is None and postfach.imap_konfig() is None):
        return None
    letzte = db.letzte_laeufe("postfach", limit=1)
    if letzte and mindestabstand_min > 0:
        zeit = zeitplan.als_datetime(letzte[0]["gestartet_am"])
        if zeit and datetime.now(timezone.utc) - zeit < timedelta(minutes=mindestabstand_min):
            return None
    lauf_id = db.starte_lauf("postfach", ausloeser)
    try:
        ergebnis = postfach.pruefe_postfach(db, verbindung=verbindung)
    except Exception as e:
        db.beende_lauf(lauf_id, {}, fehler=str(e)[:500])
        raise
    db.beende_lauf(lauf_id, ergebnis)
    return ergebnis


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
    """Cron-Einstieg (alle paar Minuten).

    Startet den faelligen Automatiklauf, gibt Autopilot-Bewerbungen nach Ablauf
    der Einspruchsfrist frei, verschickt Freigaben, wertet das Postfach aus und
    schlaegt Nachfass-Mails vor. Ein Fehler in einem Schritt haelt die anderen
    nicht auf.
    """
    from . import autopilot, nachfassen

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
                    try:
                        ergebnis["automatik"] = automatik_lauf(
                            db, profil, ausloeser="zeitplan", nur_suchen=modus == "nur_suchen",
                            mit_llm=mit_llm, browser_fabrik=kwargs.get("browser_fabrik"),
                        )
                    except Exception as e:
                        log.exception("Automatiklauf fehlgeschlagen")
                        ergebnis["automatik_fehler"] = str(e)[:500]
                else:
                    ergebnis["automatik"] = "läuft bereits"
    try:
        freigegeben = autopilot.faellige_freigeben(db, profil, jetzt=jetzt)
        if freigegeben:
            ergebnis["autopilot"] = freigegeben
    except Exception as e:
        log.exception("Autopilot fehlgeschlagen")
        ergebnis["autopilot_fehler"] = str(e)[:500]
    try:
        ergebnis["senden"] = senden_lauf(db, profil, ausloeser="zeitplan", **kwargs)
    except Exception as e:
        log.exception("Versand fehlgeschlagen")
        ergebnis["senden_fehler"] = str(e)[:500]
    try:
        post = postfach_lauf(db, profil)
        if post:
            ergebnis["postfach"] = post
    except Exception as e:
        log.warning("Postfach nicht auswertbar: %s", e)
        ergebnis["postfach_fehler"] = str(e)[:500]
    try:
        neu = nachfassen.pruefe(db, profil, mit_llm=mit_llm)
        if neu:
            ergebnis["nachfassen"] = neu
    except Exception as e:
        log.exception("Nachfassen fehlgeschlagen")
        ergebnis["nachfassen_fehler"] = str(e)[:500]
    return ergebnis
