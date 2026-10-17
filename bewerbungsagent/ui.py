"""Gemeinsame Streamlit-Bausteine fuer Heute, Suchen, Lebenslauf und Einstellungen.

Aktionen (Ja, Nein, selbst beworben ...) laufen als `on_click`-Callbacks mit
eigener Datenbankverbindung, damit sie unabhaengig vom Seitenaufbau sind.
Lange Arbeiten (Vorbereiten mit Probelauf, Versand) starten als
Hintergrundprozess, die Seite fragt den Stand per Fragment-Refresh ab.

Gestaltung: Material-Icons statt Emoji, hoechstens ein Hauptknopf je Bereich,
keine Fachbegriffe (SMTP, Cron, LLM, Score) in der Oberflaeche.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from . import ablauf, hintergrund, zeitplan
from .bewerben import mail
from .config import Profil, lade_profil
from .db import STANDARD_DB, Speicher
from .models import Job, Score, Vorgang
from .sources import QUELLEN, bekannte

SEITE_HEUTE = "seiten/cockpit.py"
SEITE_SUCHEN = "seiten/schritt_fuer_schritt.py"
SEITE_JOBS = "seiten/jobs.py"
SEITE_LEBENSLAUF = "seiten/lebenslauf.py"
SEITE_EINSTELLUNGEN = "seiten/profil.py"

STATUS_TEXT = {
    "vorbereitung": "wird vorbereitet",
    "wartet": "wartet auf dein Ja",
    "freigegeben": "geht gleich raus",
    "wird_gesendet": "wird gesendet",
    "abgeschickt": "abgeschickt",
    "fehlgeschlagen": "braucht deine Hilfe",
    "abgelehnt": "abgelehnt",
    "selbst_beworben": "selbst beworben",
}
KANAL_TEXT = {"email": "E-Mail", "formular": "Online-Formular", "manuell": "Selbst bewerben"}
KANAL_ICON = {"email": ":material/mail:", "formular": ":material/language:", "manuell": ":material/front_hand:"}
QUELLE_TEXT = {name: info.label for name, info in QUELLEN.items()}
RUECKMELDUNG_TEXT = {
    "": "noch keine",
    "eingangsbestaetigung": "Eingang bestätigt",
    "einladung": "Einladung zum Gespräch",
    "absage": "Absage",
    "zusage": "Zusage",
}
RUECKMELDUNG_KURZ = {"eingangsbestaetigung": "Eingang", "einladung": "Einladung", "absage": "Absage",
                     "zusage": "Zusage"}
RUECKMELDUNG_ICON = {"eingangsbestaetigung": ":material/mark_email_read:", "einladung": ":material/celebration:",
                     "absage": ":material/thumb_down:", "zusage": ":material/emoji_events:"}
NEIN_GRUENDE = ["passt fachlich nicht", "zu weit weg", "Gehalt/Konditionen", "Arbeitgeber uninteressant", "Zeitarbeit"]
LAUF_TEXT = {
    "automatik": "Ich suche, bewerte und bereite vor …",
    "vorbereiten": "Ich schreibe Anschreiben und teste die Formulare …",
    "senden": "Ich verschicke Bewerbungen …",
}


def kanal_label(kanal: str) -> str:
    return f"{KANAL_ICON.get(kanal, '')} {KANAL_TEXT.get(kanal, kanal)}".strip()


def quellen_label(quelle: str) -> str:
    info = QUELLEN.get(quelle)
    if info is None:
        return quelle
    return info.label + ("" if info.verfuegbar() else " (Schlüssel fehlt)")


def quellen_optionen() -> list[str]:
    return list(QUELLEN)


def quellen_auswahl(quellen: list[str] | None) -> list[str]:
    """Gespeicherte Quellen ohne unbekannte Namen (vermeidet Streamlit-Fehler)."""
    return bekannte(list(quellen or [])) or ["arbeitsagentur"]


def oeffne_db() -> Speicher:
    return Speicher(STANDARD_DB)


def profil_laden() -> Profil | None:
    try:
        return lade_profil()
    except Exception:
        return None


def profil_status() -> tuple[Profil | None, str | None]:
    """(Profil, Fehler): (None, None) = noch nicht (fertig) eingerichtet -> Assistent;
    (None, Text) = Profil vorhanden, aber kaputt -> Fehler zeigen statt neu einrichten."""
    try:
        return lade_profil(), None
    except FileNotFoundError:
        return None, None
    except Exception as e:
        try:
            halb = lade_profil(pruefen=False)
        except Exception as e2:
            return None, str(e2)
        return (None, None) if not halb.suche.was else (None, str(e))


def profil_fehler(text: str) -> None:
    st.error(f"Dein Profil konnte nicht geladen werden: {text}", icon=":material/error:")
    st.page_link(SEITE_EINSTELLUNGEN, label="Zu den Einstellungen", icon=":material/settings:")


def zeit(iso: str | None) -> str:
    return zeitplan.beschreibe(zeitplan.als_datetime(iso)) if iso else "–"


def score_farbe(wert: float) -> str:
    return "green" if wert >= 75 else ("orange" if wert >= 55 else "red")


def hostname(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").removeprefix("www.")


def zeitplan_text(cron: str) -> str:
    """„täglich 6:00“ statt „0 6 * * *“."""
    for name, ausdruck in zeitplan.VOREINSTELLUNGEN.items():
        if ausdruck == cron:
            return name[0].lower() + name[1:]
    return "nach eigenem Zeitplan"


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #

def navleiste() -> None:
    """Immer sichtbare Leiste oben – auch am Handy, wo die Seitenleiste versteckt waere."""
    # Am Handy passen alle fuenf Ziele nur ohne Symbole und mit wenig Rand in eine Zeile
    st.html("<style>@media (max-width: 480px) {.st-key-navleiste {gap: 0.15rem !important;}"
            ".st-key-navleiste a {padding-left: 0.3rem !important; padding-right: 0.3rem !important;}"
            ".st-key-navleiste a > span:has([data-testid='stIconMaterial']) {display: none;}}</style>")
    with st.container(horizontal=True, vertical_alignment="center", gap="small", key="navleiste"):
        st.page_link(SEITE_HEUTE, label="Heute", icon=":material/today:")
        st.page_link(SEITE_SUCHEN, label="Suchen", icon=":material/search:")
        st.page_link(SEITE_JOBS, label="Jobs", icon=":material/work:")
        st.page_link(SEITE_LEBENSLAUF, label="Lebenslauf", icon=":material/description:")
        st.page_link(SEITE_EINSTELLUNGEN, label="Einstellungen", icon=":material/settings:")


# --------------------------------------------------------------------------- #
# Hintergrund
# --------------------------------------------------------------------------- #

def starte_hintergrund(*argumente: str, meldung: str) -> None:
    hintergrund.starte(*argumente)
    st.toast(meldung, icon=":material/hourglass_top:")


def vorbereitung_starten(profil: Profil, refs: list[str]) -> list[str]:
    """Fuer ausgewaehlte Stellen Anschreiben schreiben lassen (im Hintergrund); entschieden wird auf „Heute“.

    Nur Stellen ohne Vorgang und ohne Bewerbung: eine laufende, wartende oder abgeschickte wird nie ueberschrieben
    (die Liste auf der Seite kann aelter sein als der Stand). Rueckgabe: die Stellen, die wirklich starten.
    """
    with oeffne_db() as db:
        frei = [ref for ref in refs if db.vorgang(ref) is None and not db.schon_beworben(ref)]
        for ref in frei:
            db.setze_vorgang(Vorgang(ref=ref, status="vorbereitung", herkunft="manuell"))
    if not frei:
        return []
    argumente = ["vorbereiten", *frei, "--ausloeser", "knopf"]
    if not (profil.automatisierung.probelauf_vorab and ablauf.browser_verfuegbar()):
        argumente.append("--ohne-probelauf")
    starte_hintergrund(*argumente, meldung=f"{len(frei)} Bewerbung(en) werden vorbereitet …")
    return frei


def auffrischen_alle(db: Speicher) -> int | None:
    """Sekunden bis zum naechsten Blick auf den Stand: oft bei echter Arbeit, selten bei wartenden Freigaben."""
    if db.laufende_laeufe() or db.vorgaenge(("vorbereitung", "wird_gesendet")):
        return 5
    return 30 if db.vorgaenge("freigegeben") else None


STAND_STATUS = ("vorbereitung", "wartet", "freigegeben", "wird_gesendet", "abgeschickt", "selbst_beworben")


def stand(db: Speicher) -> tuple[int, ...]:
    """Vorgaenge je Status, laufende Laeufe und offene Ideen – aendert sich etwas, baut „Heute“ neu auf.

    Auch das Ende eines Laufs zaehlt: dann stimmt der Takt des Nachsehens (`auffrischen_alle`) wieder, und
    Ideen aus einer Suche ohne neue Karten erscheinen sofort.
    """
    return (*(len(db.vorgaenge(status)) for status in STAND_STATUS), len(db.laufende_laeufe()),
            len(db.vorschlaege("offen")))


def lauf_zusammenfassung(lauf: dict) -> str:
    z = lauf.get("zusammenfassung") or {}
    teile = [zeit(lauf["gestartet_am"])]
    for schluessel, text in (("gefunden", "gefunden"), ("neu", "neu"), ("passend", "passend"),
                             ("vorbereitet", "vorbereitet"), ("abgeschickt", "abgeschickt"),
                             ("fehlgeschlagen", "fehlgeschlagen"), ("zugeordnet", "Antworten erkannt")):
        if z.get(schluessel):
            teile.append(f"{z[schluessel]} {text}")
    autopilot = z.get("autopilot") or {}
    if autopilot.get("freigegeben") or autopilot.get("mit_frist"):
        teile.append(f"{autopilot.get('freigegeben', 0) + autopilot.get('mit_frist', 0)} vom Autopilot")
    erkundung = z.get("erkundung") or {}
    if erkundung.get("aktiv"):
        teile.append("neue Suchbegriffe: " + ", ".join(erkundung["aktiv"]))
    if z.get("suchfehler"):
        teile.append(f"{len(z['suchfehler'])} Jobbörsen-Abfrage(n) fehlgeschlagen")
    if lauf.get("fehler"):
        teile.append(f"Fehler: {lauf['fehler'][:120]}")
    return " · ".join(teile)


def zeige_laufstatus(db: Speicher) -> bool:
    """Eine dezente Zeile, solange im Hintergrund etwas passiert; True, wenn etwas laeuft."""
    teile = [LAUF_TEXT.get(lauf["art"], lauf["art"]) for lauf in db.laufende_laeufe()]
    if n := len(db.vorgaenge("vorbereitung")):
        teile.append(f"{n} Bewerbung{'en' if n > 1 else ''} in Vorbereitung")
    if teile:
        st.caption(":material/autorenew: " + " · ".join(dict.fromkeys(teile)))
    return bool(teile)


# --------------------------------------------------------------------------- #
# Dialoge: Merker, damit ein Hintergrund-Refresh offene Dialoge nicht schliesst
# --------------------------------------------------------------------------- #

DIALOG_OFFEN = "dialog_offen"
DIALOG_SCHLUESSEL = "dialog_schluessel"


def dialog_merken(*schluessel: str) -> None:
    """Im Dialog-Koerper aufrufen: Dialog ist offen; `schluessel` = Eingaben, die beim Schliessen verfallen."""
    st.session_state[DIALOG_OFFEN] = True
    if schluessel:
        st.session_state[DIALOG_SCHLUESSEL] = list(schluessel)


def dialog_offen() -> bool:
    return bool(st.session_state.get(DIALOG_OFFEN))


def _dialog_zu() -> None:
    """on_dismiss: Merker loeschen und nicht gespeicherte Eingaben verwerfen."""
    st.session_state.pop(DIALOG_OFFEN, None)
    for schluessel in st.session_state.pop(DIALOG_SCHLUESSEL, None) or []:
        st.session_state.pop(schluessel, None)


def neuer_seitenlauf() -> None:
    """Zu Beginn jedes vollen Laufs (app.py): ein voller Lauf schliesst alle Dialoge."""
    st.session_state.pop(DIALOG_OFFEN, None)


# --------------------------------------------------------------------------- #
# Stellen
# --------------------------------------------------------------------------- #

def job_zeile(job: Job) -> str:
    """Arbeitgeber · Ort · Quelle · Datum in einer Zeile."""
    ort = job.ort or "Ort unbekannt"
    if job.entfernung_km is not None:
        ort += f" ({job.entfernung_km:.0f} km)"
    teile = [job.arbeitgeber or "Arbeitgeber unbekannt", ort, QUELLE_TEXT.get(job.quelle, job.quelle)]
    if job.veroeffentlicht:
        teile.append(f"seit {job.veroeffentlicht[:10]}")
    return " · ".join(t for t in teile if t)


def job_merkmale(job: Job) -> list[str]:
    merkmale = []
    if job.homeoffice:
        merkmale.append("Homeoffice")
    if job.befristung:
        merkmale.append(job.befristung)
    if job.verguetung:
        merkmale.append(job.verguetung)
    return merkmale


def passung_text(score: Score | None, max_treffer: int = 6) -> str:
    if not score:
        return ""
    teile = []
    if score.begruendung and score.bewerter != "heuristik":
        teile.append(score.begruendung)
    if score.treffer:
        teile.append("Passt bei: " + ", ".join(score.treffer[:max_treffer]))
    if score.rot:
        teile.append("Achtung: " + ", ".join(score.rot[:4]))
    return "  \n".join(teile)


def passung_badge(score: Score) -> None:
    st.badge(f"Passung {score.gesamt:.0f}", icon=":material/target:", color=score_farbe(score.gesamt),
             help=passung_text(score) or None)


def job_kopf(job: Job, score: Score | None, *, ueberschrift: str = "####") -> None:
    st.markdown(f"{ueberschrift} {job.titel}")
    st.caption(job_zeile(job))
    merkmale = job_merkmale(job)
    if score or merkmale:
        with st.container(horizontal=True, gap="xsmall"):
            if score:
                passung_badge(score)
            for merkmal in merkmale:
                st.badge(merkmal, color="gray")


def score_gruende(score: Score | None, max_treffer: int = 6) -> None:
    if not score:
        return
    if score.begruendung and score.bewerter != "heuristik":
        st.caption(score.begruendung)
    zeilen = []
    if score.treffer:
        zeilen.append(":green[:material/check:] " + ", ".join(score.treffer[:max_treffer]))
    if score.rot:
        zeilen.append(":orange[:material/warning:] " + ", ".join(score.rot[:4]))
    if zeilen:
        st.markdown("  \n".join(zeilen))


# --------------------------------------------------------------------------- #
# Aktionen (Callbacks)
# --------------------------------------------------------------------------- #

def _schluessel(praefix: str, ref: str, feld: str) -> str:
    return f"{praefix}_{feld}_{ref}"


def _cb_ja(ref: str, praefix: str) -> None:
    profil = lade_profil()
    s = st.session_state
    kanal = s.get(_schluessel(praefix, ref, "kanal"))
    with oeffne_db() as db:
        try:
            ablauf.freigeben(
                db, profil, ref,
                anschreiben=s.get(_schluessel(praefix, ref, "text")),
                kanal=kanal,
                empfaenger=s.get(_schluessel(praefix, ref, "empf")) if kanal == "email" else None,
                email_text=s.get(_schluessel(praefix, ref, "mail")) if kanal == "email" else None,
            )
        except ValueError as e:
            s[_schluessel(praefix, ref, "fehler")] = str(e)
            return
    s.pop(_schluessel(praefix, ref, "fehler"), None)
    starte_hintergrund("senden", "--ausloeser", "ja", meldung="Bewerbung wird verschickt …")


def _cb_freigeben(ref: str, praefix: str) -> None:
    """Ja auf der Karte: genau das Gespeicherte absenden (nie Eingaben aus einem geschlossenen Dialog)."""
    profil = lade_profil()
    s = st.session_state
    for feld in ("kanal", "text", "empf", "mail"):
        s.pop(_schluessel(praefix, ref, feld), None)
    with oeffne_db() as db:
        try:
            ablauf.freigeben(db, profil, ref)
        except ValueError as e:
            s[_schluessel(praefix, ref, "fehler")] = str(e)
            return
    s.pop(_schluessel(praefix, ref, "fehler"), None)
    starte_hintergrund("senden", "--ausloeser", "ja", meldung="Bewerbung wird verschickt …")


def _cb_speichern(ref: str, praefix: str) -> None:
    """Bearbeitetes Anschreiben/Empfaenger speichern, ohne freizugeben."""
    from . import dokumente

    profil = lade_profil()
    s = st.session_state
    with oeffne_db() as db:
        v, job = db.vorgang(ref), db.job(ref)
        if not v or not job:
            return
        felder = {}
        text = s.get(_schluessel(praefix, ref, "text"))
        if text and text.strip() != (v.anschreiben or "").strip():
            felder["anschreiben"] = text.strip()
            felder["anschreiben_pdf"] = str(dokumente.anschreiben_pdf(job, profil, text.strip()))
        for feld, name in (("empf", "empfaenger"), ("mail", "email_text"), ("kanal", "kanal")):
            wert = s.get(_schluessel(praefix, ref, feld))
            if wert is not None:
                felder[name] = wert.strip().lower() if name == "empfaenger" else wert
        if felder:
            db.aktualisiere_vorgang(ref, **felder)
    st.toast("Gespeichert.", icon=":material/save:")


def _cb_nein(ref: str, praefix: str) -> None:
    profil = lade_profil()
    s = st.session_state
    gruende = s.get(_schluessel(praefix, ref, "grund")) or []
    with oeffne_db() as db:
        ablauf.ablehnen(
            db, profil, ref, grund=", ".join(gruende),
            arbeitgeber_sperren=bool(s.get(_schluessel(praefix, ref, "sperren"))),
        )
    st.toast("Abgelehnt – die Stelle taucht nicht wieder auf.", icon=":material/block:")


def _cb_selbst(ref: str) -> None:
    with oeffne_db() as db:
        ablauf.selbst_beworben(db, ref)
    st.toast("Als „selbst beworben“ eingetragen.", icon=":material/task_alt:")


def _cb_neu_bewerben(ref: str, praefix: str) -> None:
    """Karte „vielleicht schon raus“: Du hast geprüft, dass nichts ankam – mit deinem Ja automatisch neu."""
    profil = lade_profil()
    with oeffne_db() as db:
        v = db.vorgang(ref)
        # Derselbe Weg wie beim abgebrochenen Versuch: nur eine unterbrochene E-Mail geht wieder per E-Mail
        # (an dieselbe Adresse), sonst das Formular – eine nebenbei gespeicherte Adresse wird nie gewählt
        kanal = "email" if v and v.empfaenger and v.hinweis == ablauf.HINWEIS_VIELLEICHT_MAIL else "formular"
        try:
            ablauf.freigeben(db, profil, ref, kanal=kanal)
        except ValueError as e:
            st.session_state[_schluessel(praefix, ref, "fehler")] = str(e)
            return
    st.session_state.pop(_schluessel(praefix, ref, "fehler"), None)
    starte_hintergrund("senden", "--ausloeser", "ja", meldung="Bewerbung wird neu verschickt …")


def _cb_neu_vorbereiten(ref: str) -> None:
    with oeffne_db() as db:
        ablauf.zuruecksetzen(db, ref)
        db.setze_vorgang(Vorgang(ref=ref, status="vorbereitung"))
    starte_hintergrund("vorbereiten", ref, "--ausloeser", "knopf", meldung="Wird neu vorbereitet …")


def _cb_rueckmeldung(ref: str, schluessel: str) -> None:
    wert = st.session_state.get(schluessel) or ""
    with oeffne_db() as db:
        ablauf.setze_rueckmeldung(db, ref, wert)
    st.toast("Rückmeldung gespeichert.", icon=":material/save:")


# --------------------------------------------------------------------------- #
# Bausteine der Freigabe-Karte
# --------------------------------------------------------------------------- #

QUALITAET_HILFE = ("Selbstprüfung des Anschreibens: Sind alle Aussagen durch Lebenslauf/Profil belegt, "
                   "werden die Muss-Anforderungen angesprochen, passen Anrede und Länge?")


def _weg_badge(v: Vorgang) -> None:
    if v.kanal == "email":
        st.badge(f"E-Mail an {v.empfaenger or '?'}", icon=":material/mail:", color="blue",
                 help="Anschreiben als PDF und deine Unterlagen gehen im Anhang mit.")
    elif v.kanal == "formular":
        st.badge(f"Formular auf {hostname(v.bewerbungs_url) or '?'}", icon=":material/language:", color="blue",
                 help="Der Browser-Agent füllt das Online-Formular für dich aus.")
    else:
        st.badge("Selbst bewerben", icon=":material/front_hand:", color="orange",
                 help="Ich habe keinen automatischen Weg gefunden.")


def _qualitaet_badge(v: Vorgang) -> None:
    bericht = v.pruefung_daten
    if v.anschreiben_quelle == "vorlage":
        st.badge("Anschreiben aus Vorlage", icon=":material/edit_note:", color="orange",
                 help=(f"Ohne KI erstellt, weil {bericht['ki_fehler']}" if bericht.get("ki_fehler")
                       else "Ohne KI erstellt") + " – bitte lesen und anpassen.")
    elif v.qualitaet is not None:
        st.badge(f"Anschreiben {v.qualitaet:.0f}/100", icon=":material/verified:", color=score_farbe(v.qualitaet),
                 help=QUALITAET_HILFE + (" Bereits einmal überarbeitet." if bericht.get("ueberarbeitet") else ""))


def _probelauf_badge(v: Vorgang) -> None:
    daten = v.probelauf_daten
    if not daten or v.kanal != "formular":
        return
    ausgefuellt = daten.get("ausgefuellte_felder") or []
    fehlend = daten.get("fehlende_angaben") or []
    if fehlend:
        st.badge(f"Formular-Test: {len(fehlend)} offen", icon=":material/science:", color="orange",
                 help="Ausgefüllt: " + (", ".join(ausgefuellt) or "–"))
    elif ausgefuellt:
        st.badge(f"Formular getestet ({len(ausgefuellt)} Felder)", icon=":material/science:", color="green",
                 help="Ausgefüllt: " + ", ".join(ausgefuellt))


def _pruefung_anzeige(v: Vorgang) -> None:
    bericht = v.pruefung_daten
    if bericht.get("unbelegt"):
        st.warning("**Nicht durch deinen Lebenslauf belegt:** " + "; ".join(bericht["unbelegt"][:4])
                   + " – bitte im Text korrigieren oder streichen.", icon=":material/fact_check:")


def _cb_autopilot_stopp(ref: str) -> None:
    from . import autopilot

    with oeffne_db() as db:
        autopilot.stoppen(db, ref)
    st.toast("Angehalten – diese Bewerbung geht nur mit deinem Ja raus.", icon=":material/pan_tool:")


def _autopilot_anzeige(v: Vorgang, praefix: str, autopilot_an: bool) -> None:
    if not autopilot_an:
        return  # ausgeschaltet sendet der Autopilot nichts (autopilot.faellige_freigeben)
    if v.auto_senden_ab:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.info(f"Der Autopilot schickt sie **{zeit(v.auto_senden_ab)}** ab, wenn du nichts tust.",
                    icon=":material/smart_toy:")
            st.button("Stopp", key=f"{praefix}_stopp_{v.ref}", on_click=_cb_autopilot_stopp, args=(v.ref,),
                      icon=":material/pan_tool:", help="Nicht automatisch senden – wartet dann auf dein Ja.")
        return
    info = v.pruefung_daten.get("autopilot") or {}
    if info.get("gruende") and info.get("freigabe") is False:
        st.caption(":material/smart_toy: Der Autopilot wartet hier auf dich: " + "; ".join(info["gruende"][:3]))


_ANGABE_FELDER = [
    (r"gehalt|salary", "gehaltsvorstellung"),
    (r"eintritt|verfügbar|verfuegbar|start|beginn", "verfuegbar_ab"),
    (r"kündigung|kuendigung|notice", "kuendigungsfrist"),
    (r"staatsangeh|nationalit|citizenship", "nationalitaet"),
    (r"arbeitserlaubnis|work permit|visa", "arbeitserlaubnis"),
    (r"führerschein|fuehrerschein|driving", "fuehrerschein"),
    (r"anrede|salutation|geschlecht", "anrede"),
    (r"abschluss|degree|studium", "abschluss"),
    (r"berufserfahrung|experience", "berufserfahrung_jahre"),
    (r"umzug|relocat", "umzugsbereitschaft"),
    (r"sprach|language", "sprachen"),
    (r"titel|title", "titel"),
]


def _angabe_feld(frage: str) -> str | None:
    return next((feld for muster, feld in _ANGABE_FELDER if re.search(muster, frage, re.IGNORECASE)), None)


def angabe_merken(profil: Profil, frage: str, antwort: str) -> str:
    """Speichert eine Formular-Antwort im passenden Profilfeld oder unter `weitere`."""
    antwort = antwort.strip()
    feld = _angabe_feld(frage)
    if feld:
        setattr(profil.angaben, feld, antwort)
        return feld
    profil.angaben.weitere[frage.strip()] = antwort
    return "weitere"


def bekannte_angabe(profil: Profil, frage: str) -> str:
    """Was im Profil zu einer Formularfrage schon steht (zum Vorbelegen)."""
    feld = _angabe_feld(frage)
    if feld:
        return str(getattr(profil.angaben, feld, "") or "")
    return profil.angaben.weitere.get(frage.strip(), "")


def _cb_angaben(ref: str, praefix: str, fragen: list[str]) -> None:
    from .config import ermittle_profil_pfad, speichere_profil

    profil = lade_profil()
    gemerkt = 0
    for i, frage in enumerate(fragen):
        antwort = (st.session_state.get(f"{praefix}_angabe_{ref}_{i}") or "").strip()
        if antwort:
            angabe_merken(profil, frage, antwort)
            gemerkt += 1
    if not gemerkt:
        st.session_state[_schluessel(praefix, ref, "fehler")] = "Bitte mindestens eine Angabe ausfüllen."
        return
    speichere_profil(profil, ermittle_profil_pfad())
    _cb_neu_vorbereiten(ref)
    st.toast(f"{gemerkt} Angabe(n) gemerkt – ich teste das Formular neu.", icon=":material/save:")


def _fehlende_angaben(profil: Profil, v: Vorgang, praefix: str) -> None:
    fehlend = [str(f) for f in (v.probelauf_daten.get("fehlende_angaben") or []) if str(f).strip()]
    if not fehlend:
        return
    with st.container(border=True):
        st.markdown("**:material/edit_note: Das Formular fragt noch nach** – einmal beantworten, ich merke es mir.")
        fragen = []
        for i, eintrag in enumerate(fehlend[:6]):
            feld, _, frage = eintrag.partition(":")
            frage = frage.strip() or feld.strip()
            fragen.append(frage)
            st.text_input(frage, value=bekannte_angabe(profil, frage), key=f"{praefix}_angabe_{v.ref}_{i}")
        st.button("Merken & Formular neu testen", key=f"{praefix}_angaben_{v.ref}", on_click=_cb_angaben,
                  args=(v.ref, praefix, fragen), icon=":material/save:")


def anschreiben_vorschau(text: str, laenge: int = 260) -> str:
    """Erster inhaltlicher Absatz (ohne Anrede), gekuerzt."""
    absaetze = [a.strip() for a in (text or "").split("\n\n") if a.strip()]
    rumpf = next((a for a in absaetze if not re.match(r"^(sehr geehrte|liebe|hallo|guten tag)", a, re.I)
                  and len(a) > 60), absaetze[0] if absaetze else "")
    rumpf = " ".join(rumpf.split())
    return rumpf if len(rumpf) <= laenge else rumpf[:laenge].rsplit(" ", 1)[0] + " …"


@st.dialog("Anschreiben & Versand", width="large", icon=":material/edit:", on_dismiss=_dialog_zu)
def _dialog_bearbeiten(ref: str, praefix: str) -> None:
    k = lambda feld: _schluessel(praefix, ref, feld)  # noqa: E731
    # Schliessen ohne Speichern verwirft die Eingaben (sie duerfen nie unbemerkt mitgesendet werden)
    dialog_merken(k("kanal"), k("text"), k("empf"), k("mail"))
    with oeffne_db() as db:
        v, job = db.vorgang(ref), db.job(ref)
    if not v or not job:
        st.warning("Diese Bewerbung gibt es nicht mehr.")
        return
    st.caption(f"{job.titel} – {job.arbeitgeber}")
    kanaele = ["email", "formular"] if v.kanal != "manuell" else ["manuell", "email", "formular"]
    if v.kanal not in kanaele:
        kanaele.insert(0, v.kanal)
    st.segmented_control("Bewerbungsweg", kanaele, default=v.kanal, key=k("kanal"), format_func=kanal_label)
    kanal = st.session_state.get(k("kanal")) or v.kanal
    if kanal == "email":
        st.text_input("An", value=v.empfaenger or "", key=k("empf"), placeholder="bewerbung@firma.de")
        st.text_area("Kurze E-Mail dazu", value=v.email_text or "", key=k("mail"), height=150)
        if not mail.smtp_eingerichtet():
            st.warning("Dein E-Mail-Konto ist noch nicht verbunden (Einstellungen → Konten). Ein Ja gilt "
                       "trotzdem – die Bewerbung geht raus, sobald es verbunden ist.", icon=":material/mail:")
    elif kanal == "formular":
        st.caption(f"Bewerbungsseite: {v.bewerbungs_url or job.bewerbungs_url or '–'}")
    st.text_area("Anschreiben", value=v.anschreiben or "", key=k("text"), height=380)
    if kanal == "manuell" and v.anschreiben:
        with st.expander("Zum Kopieren", icon=":material/content_copy:"):
            st.code(v.anschreiben, language=None, wrap_lines=True)
    bericht = v.pruefung_daten
    rest = [*bericht.get("maengel", []), *(f"Nicht angesprochen: {m}" for m in bericht.get("fehlende_muss", [])[:3]),
            *bericht.get("hinweise", [])]
    if rest:
        with st.expander(f"Hinweise der Selbstprüfung ({len(rest)})", icon=":material/fact_check:"):
            st.markdown("\n".join(f"- {r}" for r in rest))
    with st.container(horizontal=True, horizontal_alignment="right"):
        if v.anschreiben_pdf and Path(v.anschreiben_pdf).exists():
            st.download_button("PDF", Path(v.anschreiben_pdf).read_bytes(), file_name=Path(v.anschreiben_pdf).name,
                               mime="application/pdf", key=k("pdf"), icon=":material/download:", on_click="ignore")
        if st.button("Speichern", key=k("speichern"), icon=":material/save:"):
            _cb_speichern(ref, praefix)
            _dialog_zu()
            st.rerun()
        if kanal != "manuell" and st.button("Speichern & absenden", key=k("dialog_ja"), type="primary",
                                            icon=":material/send:"):
            _cb_speichern(ref, praefix)
            _cb_ja(ref, praefix)
            if not st.session_state.get(k("fehler")):
                _dialog_zu()
                st.rerun()
    if st.session_state.get(k("fehler")):
        st.error(st.session_state[k("fehler")])


def freigabe_karte(db: Speicher, profil: Profil, v: Vorgang, praefix: str = "fk") -> None:
    """Vorbereitete Bewerbung: Kopf, eine Badge-Zeile, Vorschau, dann [Mehr] [Nein] [Ja, absenden]."""
    job, score = db.job(v.ref), db.score(v.ref)
    if job is None:
        return
    k = lambda feld: _schluessel(praefix, v.ref, feld)  # noqa: E731
    autopilot_an = profil.automatisierung.effektiver_autopilot != "aus"
    with st.container(border=True):
        st.markdown(f"#### {job.titel}")
        st.caption(job_zeile(job))
        with st.container(horizontal=True, gap="xsmall"):
            if score:
                passung_badge(score)
            _weg_badge(v)
            _qualitaet_badge(v)
            _probelauf_badge(v)
            if v.herkunft == "automatik" and autopilot_an:
                st.badge("vom Agenten gefunden", icon=":material/smart_toy:", color="violet")
        if score and score.treffer:
            st.caption(":material/check: Passt bei " + ", ".join(score.treffer[:5])
                       + (f" · :material/warning: {', '.join(score.rot[:2])}" if score.rot else ""))

        if v.hinweis in ablauf.HINWEISE_VIELLEICHT:
            st.info(v.hinweis, icon=":material/help:")
        elif v.hinweis and "ging nicht raus" in v.hinweis:
            st.info(v.hinweis, icon=":material/mail:")
        elif v.hinweis:
            warnung = v.kanal != "manuell" and any(w in v.hinweis.lower() for w in ("fehlgeschlagen", "unvollständig"))
            (st.warning if warnung else st.caption)(v.hinweis)
        _autopilot_anzeige(v, praefix, autopilot_an)
        _pruefung_anzeige(v)
        if v.kanal != "manuell":  # „Formular neu testen“ nie auf Karten zum Selbstbewerben (vielleicht schon raus)
            _fehlende_angaben(profil, v, praefix)

        if v.anschreiben:
            st.markdown(f":gray[„{anschreiben_vorschau(v.anschreiben)}“]")

        fehler = st.session_state.get(k("fehler"))
        if fehler:
            st.error(fehler)

        manuell = v.kanal == "manuell"
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if st.button("Lesen & bearbeiten", key=k("lesen"), icon=":material/edit:", type="tertiary"):
                _dialog_bearbeiten(v.ref, praefix)
            with st.popover("Mehr", icon=":material/more_horiz:"):
                if job.anzeige_url:
                    st.link_button("Anzeige öffnen", job.anzeige_url, icon=":material/open_in_new:", width="stretch")
                if not manuell:
                    st.caption("Schreibt das Anschreiben neu und testet das Formular erneut – "
                               "deine Änderungen am Text gehen dabei verloren.")
                    st.button("Neu schreiben lassen", key=k("neu"), on_click=_cb_neu_vorbereiten, args=(v.ref,),
                              icon=":material/refresh:", width="stretch")
                    st.button("Habe mich selbst beworben", key=k("selbst"), on_click=_cb_selbst, args=(v.ref,),
                              icon=":material/task_alt:", width="stretch")
                if manuell and v.hinweis in ablauf.HINWEISE_VIELLEICHT:
                    st.caption("Du hast geprüft, dass nichts angekommen ist?")
                    st.button("Nicht angekommen – automatisch neu bewerben", key=k("neu_bewerben"),
                              on_click=_cb_neu_bewerben, args=(v.ref, praefix), icon=":material/send:",
                              width="stretch")
                if manuell and v.anschreiben_pdf and Path(v.anschreiben_pdf).exists():
                    st.download_button("Anschreiben als PDF", Path(v.anschreiben_pdf).read_bytes(),
                                       file_name=Path(v.anschreiben_pdf).name, mime="application/pdf",
                                       key=k("karte_pdf"), icon=":material/download:", width="stretch",
                                       on_click="ignore")
            with st.popover("Nein", icon=":material/close:"):
                st.pills("Warum nicht? (hilft mir beim Lernen)", NEIN_GRUENDE, selection_mode="multi", key=k("grund"))
                st.checkbox(f"„{job.arbeitgeber}“ nie wieder vorschlagen", key=k("sperren"))
                st.button("Ablehnen", key=k("nein"), on_click=_cb_nein, args=(v.ref, praefix), type="primary",
                          width="stretch")
            if manuell:
                # Selbst bewerben: hingehen, bewerben, dann „Ja, erledigt“ (zaehlt fuer Wochenziel und Nachweis)
                if v.bewerbungs_url or job.bewerbungs_url or job.anzeige_url:
                    st.link_button("Zur Bewerbung", v.bewerbungs_url or job.bewerbungs_url or job.anzeige_url,
                                   icon=":material/open_in_new:")
                st.button("Ja, erledigt", key=k("selbst"), type="primary", on_click=_cb_selbst, args=(v.ref,),
                          icon=":material/task_alt:", help="Du hast dich selbst beworben.")
            else:
                st.button("Ja, absenden", key=k("ja"), type="primary", on_click=_cb_freigeben, args=(v.ref, praefix),
                          icon=":material/send:")


# --------------------------------------------------------------------------- #
# Vorschlaege des Agenten
# --------------------------------------------------------------------------- #

VORSCHLAG_ICON = {"nachfassen": ":material/forward_to_inbox:", "ausschluss": ":material/block:",
                  "umkreis": ":material/location_on:", "min_score": ":material/target:",
                  "arbeitgeber": ":material/apartment:", "stil": ":material/edit_note:",
                  "suchbegriff": ":material/search:"}


def _cb_vorschlag(vorschlag_id: int, ja: bool, praefix: str) -> None:
    from . import lernen

    profil = lade_profil()
    text = st.session_state.get(f"{praefix}_vtext_{vorschlag_id}")
    with oeffne_db() as db:
        meldung = lernen.wende_an(db, profil, vorschlag_id, ja, text=text)
    st.toast(meldung, icon=":material/check:" if ja else ":material/thumb_up:")


def _cb_suchbegriffe(ids: list[int], praefix: str) -> None:
    """Gebuendelte Suchbegriff-Vorschlaege: gewaehlte uebernehmen, den Rest verwerfen."""
    from . import lernen

    gewaehlt = set(st.session_state.get(f"{praefix}_begriffe") or [])
    uebernommen = 0
    with oeffne_db() as db:
        offen = {x["id"]: x for x in db.vorschlaege("offen")}
        for vid in ids:
            if vid not in offen:
                continue
            ja = offen[vid]["daten"].get("suchbegriff_hinzu") in gewaehlt
            lernen.wende_an(db, lade_profil(), vid, ja)
            uebernommen += int(ja)
    st.toast(f"{uebernommen} Suchbegriff(e) übernommen." if uebernommen else "Keine Suchbegriffe übernommen.",
             icon=":material/search:")


def offene_vorschlaege(db: Speicher) -> list[dict]:
    """Offene Vorschlaege des Agenten - unsinnige (z. B. Plattformnamen als Suchbegriff) vorher verwerfen."""
    from . import lernen

    lernen.verwerfe_unsinnige_vorschlaege(db)
    return db.vorschlaege("offen")


def vorschlag_karte(vorschlag: dict, praefix: str = "vk") -> None:
    """Ja/Nein-Karte fuer einen Vorschlag des Agenten (Nachfassen, Profil-Aenderung ...)."""
    with st.container(border=True):
        st.markdown(f"{VORSCHLAG_ICON.get(vorschlag['art'], ':material/lightbulb:')} **{vorschlag['titel']}**")
        if vorschlag["art"] == "nachfassen":
            st.caption(f"An {vorschlag['daten'].get('empfaenger', '?')} · Betreff: {vorschlag['daten'].get('betreff', '')}")
            st.text_area("Text", value=vorschlag["text"], key=f"{praefix}_vtext_{vorschlag['id']}", height=200,
                         label_visibility="collapsed")
        elif vorschlag.get("text"):
            st.caption(vorschlag["text"])
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("Nein", key=f"{praefix}_vnein_{vorschlag['id']}", on_click=_cb_vorschlag,
                      args=(vorschlag["id"], False, praefix), icon=":material/close:")
            st.button("Ja" + (", senden" if vorschlag["art"] == "nachfassen" else ""), type="primary",
                      key=f"{praefix}_vja_{vorschlag['id']}", on_click=_cb_vorschlag,
                      args=(vorschlag["id"], True, praefix), icon=":material/check:")


def suchbegriffe_karte(vorschlaege: list[dict], praefix: str = "vk") -> None:
    """Alle Suchbegriff-Vorschlaege in einer Karte statt einer Karte pro Begriff."""
    begriffe = {x["daten"].get("suchbegriff_hinzu"): x for x in vorschlaege if x["daten"].get("suchbegriff_hinzu")}
    if not begriffe:
        return
    with st.container(border=True):
        st.markdown(f"{VORSCHLAG_ICON['suchbegriff']} **Diese Suchbegriffe habe ich mit Erfolg ausprobiert**")
        st.caption("Gewählte suche ich ab jetzt immer mit, den Rest lasse ich weg.")
        st.pills("Suchbegriffe", list(begriffe), selection_mode="multi", default=list(begriffe),
                 key=f"{praefix}_begriffe", label_visibility="collapsed")
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("Übernehmen", type="primary", key=f"{praefix}_begriffe_ok", icon=":material/check:",
                      on_click=_cb_suchbegriffe, args=([x["id"] for x in begriffe.values()], praefix))


# --------------------------------------------------------------------------- #
# Bewerbungsadresse und Browser-Test
# --------------------------------------------------------------------------- #

def bewerbungs_email(job: Job, v: Vorgang) -> tuple[str | None, bool]:
    """(Adresse, sicher) – siehe `kanal.sichere_bewerbungs_email`."""
    from .bewerben.kanal import sichere_bewerbungs_email

    return sichere_bewerbungs_email(job, v.probelauf_daten)


def _cb_browser_test() -> None:
    from .bewerben import browser

    with st.spinner("Starte den Browser testweise …"):
        browser.selbsttest()


def browser_test_anzeige(schluessel: str = "browser_test") -> None:
    """Letztes Ergebnis des Browser-Selbsttests + Knopf zum erneuten Testen."""
    from .bewerben import browser

    letzter = browser.letzter_selbsttest()
    if letzter:
        text = f"{letzter.get('meldung')} (getestet {zeit(letzter.get('zeit'))})"
        (st.success if letzter.get("ok") else st.error)(text)
    else:
        st.caption("Noch nicht getestet, ob der Browser für Online-Formulare auf dem Server startet.")
    st.button("Browser testen", key=schluessel, on_click=_cb_browser_test, icon=":material/play_circle:")


# --------------------------------------------------------------------------- #
# Bewerbungen: Rueckmeldung und Nachweis
# --------------------------------------------------------------------------- #

def beworben_zeile(db: Speicher, v: Vorgang, praefix: str = "bw") -> None:
    """Eine verschickte Bewerbung mit Rueckmeldung zum Antippen; Einladungen hervorgehoben."""
    job = db.job(v.ref)
    if job is None:
        return
    schluessel, zuletzt = f"{praefix}_rm_{v.ref}", f"{praefix}_rm_db_{v.ref}"
    db_wert = v.rueckmeldung or None
    if schluessel not in st.session_state or st.session_state.get(zuletzt, "–") != db_wert:
        # Neu, nach einem Seitenwechsel verworfen oder vom Postfach geaendert: Anzeige auf den
        # gespeicherten Stand bringen (vor dem Widget erlaubt)
        st.session_state[schluessel] = st.session_state[zuletzt] = db_wert
    with st.container(border=True):
        icon = RUECKMELDUNG_ICON.get(v.rueckmeldung or "", ":material/schedule_send:")
        if v.rueckmeldung in ("einladung", "zusage"):
            st.markdown(f":green-badge[{icon} {RUECKMELDUNG_TEXT[v.rueckmeldung]}]")
        weg = "selbst beworben" if v.status == "selbst_beworben" else kanal_label(v.kanal)
        st.markdown(f"{icon} **{job.titel}**  \n:gray[{job.arbeitgeber} · {weg} · {zeit(v.gesendet_am)}]")
        st.pills("Rückmeldung", list(RUECKMELDUNG_KURZ), key=schluessel, format_func=RUECKMELDUNG_KURZ.get,
                 on_change=_cb_rueckmeldung, args=(v.ref, schluessel), label_visibility="collapsed")


def unterwegs_zeile(db: Speicher, v: Vorgang) -> None:
    """Bewerbung mit Ja, die gerade (oder sobald es geht) verschickt wird."""
    job = db.job(v.ref)
    if job is None:
        return
    with st.container(border=True):
        st.markdown(f":material/schedule_send: **{job.titel}**  \n:gray[{job.arbeitgeber} · {kanal_label(v.kanal)} · "
                    f"{STATUS_TEXT.get(v.status, v.status)}]")
        if v.hinweis and v.status == "freigegeben":
            st.caption(v.hinweis)


@st.dialog("Nachweis fürs Amt", icon=":material/description:", width="large", on_dismiss=_dialog_zu)
def nachweis_dialog() -> None:
    from datetime import date

    from . import dokumente

    dialog_merken()
    st.caption("Liste deiner Bewerbungen als Nachweis der Eigenbemühungen für Agentur für Arbeit oder Jobcenter.")
    heute = date.today()
    zeitraum = st.date_input("Zeitraum", value=(heute.replace(day=1), heute), format="DD.MM.YYYY",
                             key="nachweis_zeitraum")
    if not (isinstance(zeitraum, (tuple, list)) and len(zeitraum) == 2):
        st.caption("Bitte Anfang und Ende wählen.")
        return
    von, bis = zeitraum
    profil = lade_profil(pruefen=False)
    with oeffne_db() as db:
        zeilen = dokumente.nachweis_zeilen(db, von, bis)
    st.markdown(f"**{len(zeilen)} Bewerbung(en)** vom {von:%d.%m.%Y} bis {bis:%d.%m.%Y}")
    if not zeilen:
        return
    st.dataframe(
        [{"Datum": z["datum"].strftime("%d.%m.%Y"), "Arbeitgeber": z["arbeitgeber"], "Stelle": z["stelle"],
          "Weg": z["weg"], "Stand": z["stand"]} for z in zeilen],
        hide_index=True, width="stretch",
    )
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.download_button("Als Tabelle (Excel)", dokumente.nachweis_csv(zeilen).encode("utf-8-sig"),
                           file_name=f"Eigenbemuehungen_{von}_{bis}.csv", mime="text/csv",
                           icon=":material/table_view:", on_click="ignore")
        st.download_button("Als PDF", dokumente.nachweis_pdf(zeilen, profil, von, bis),
                           file_name=f"Eigenbemuehungen_{von}_{bis}.pdf", mime="application/pdf",
                           type="primary", icon=":material/download:", on_click="ignore")
