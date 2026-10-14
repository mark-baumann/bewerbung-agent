"""Gemeinsame Streamlit-Bausteine fuer Cockpit, Schritt-fuer-Schritt und Tracker.

Aktionen (Ja, Nein, selbst beworben ...) laufen als `on_click`-Callbacks mit
eigener Datenbankverbindung, damit sie unabhaengig vom Seitenaufbau sind.
Lange Arbeiten (Vorbereiten mit Probelauf, Versand) starten als
Hintergrundprozess, die Seite fragt den Stand per Fragment-Refresh ab.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

from . import ablauf, hintergrund, zeitplan
from .bewerben import mail
from .config import Profil, lade_profil
from .db import STANDARD_DB, Speicher
from .models import Job, Score, Vorgang
from .scoring import llm as llm_modul
from .sources import QUELLEN, bekannte

STATUS_TEXT = {
    "vorbereitung": "⏳ wird vorbereitet",
    "wartet": "✋ wartet auf dich",
    "freigegeben": "📤 freigegeben",
    "wird_gesendet": "📤 wird gesendet",
    "abgeschickt": "✅ abgeschickt",
    "fehlgeschlagen": "⚠️ fehlgeschlagen",
    "abgelehnt": "❌ abgelehnt",
    "selbst_beworben": "✅ selbst beworben",
}
KANAL_TEXT = {"email": "📧 E-Mail", "formular": "🌐 Online-Formular", "manuell": "✋ Selbst bewerben"}
QUELLE_TEXT = {name: info.label for name, info in QUELLEN.items()}
RUECKMELDUNG_TEXT = {
    "": "– noch keine –",
    "eingangsbestaetigung": "📨 Eingang bestätigt",
    "einladung": "🎉 Einladung zum Gespräch",
    "absage": "👎 Absage",
    "zusage": "🏆 Zusage",
}
NEIN_GRUENDE = ["passt fachlich nicht", "zu weit weg", "Gehalt/Konditionen", "Arbeitgeber uninteressant", "Zeitarbeit"]
LAUF_TEXT = {
    "automatik": "Suche, Bewertung und Vorbereitung laufen …",
    "vorbereiten": "Bewerbungen werden vorbereitet (Anschreiben, Probelauf) …",
    "senden": "Bewerbungen werden verschickt …",
}


def quellen_label(quelle: str) -> str:
    info = QUELLEN.get(quelle)
    if info is None:
        return quelle
    return info.label + ("" if info.verfuegbar() else " (API-Key fehlt)")


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


def zeit(iso: str | None) -> str:
    return zeitplan.beschreibe(zeitplan.als_datetime(iso)) if iso else "–"


def score_farbe(wert: float) -> str:
    return "green" if wert >= 75 else ("orange" if wert >= 55 else "red")


def hostname(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").removeprefix("www.")


# --------------------------------------------------------------------------- #
# Hintergrund
# --------------------------------------------------------------------------- #

def starte_hintergrund(*argumente: str, meldung: str) -> None:
    hintergrund.starte(*argumente)
    st.toast(meldung, icon="⏳")


def laeuft_etwas(db: Speicher) -> bool:
    return bool(db.laufende_laeufe() or db.vorgaenge(("vorbereitung", "freigegeben", "wird_gesendet")))


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
        teile.append(f"🤖 {autopilot.get('freigegeben', 0) + autopilot.get('mit_frist', 0)} vom Autopilot")
    erkundung = z.get("erkundung") or {}
    if erkundung.get("aktiv"):
        teile.append("neue Suchbegriffe: " + ", ".join(erkundung["aktiv"]))
    if z.get("suchfehler"):
        teile.append(f"⚠️ {len(z['suchfehler'])} Abfrage(n) fehlgeschlagen")
    if lauf.get("fehler"):
        teile.append(f"Fehler: {lauf['fehler'][:120]}")
    return " · ".join(teile)


def zeige_laufstatus(db: Speicher) -> bool:
    """Zeigt laufende Hintergrundarbeiten; True, wenn etwas laeuft."""
    laufend = db.laufende_laeufe()
    for lauf in laufend:
        st.info(f"{LAUF_TEXT.get(lauf['art'], lauf['art'])} (seit {zeit(lauf['gestartet_am'])})", icon="⏳")
    for v in db.vorgaenge(("freigegeben", "wird_gesendet")):
        job = db.job(v.ref)
        if job:
            st.caption(f"📤 {job.titel} – {job.arbeitgeber}: {STATUS_TEXT[v.status]}"
                       + (f" · {v.hinweis}" if v.hinweis else ""))
    return bool(laufend) or laeuft_etwas(db)


# --------------------------------------------------------------------------- #
# Einrichtung
# --------------------------------------------------------------------------- #

def ki_status(profil: Profil) -> tuple[bool, str]:
    """(ok, Text) zum KI-Zugang inkl. Prüfung, ob es das gewählte Modell gibt."""
    anbieter, ziel, hinweis = llm_modul.modell_fuer(profil.bewertung.modell)
    if anbieter is None:
        return False, "Ohne KI-Zugang: Bewertung per Stichworten, Anschreiben aus einer Vorlage. Profil → 🔑 Zugänge."
    if hinweis:
        return False, hinweis
    if llm_modul.modell_fehler.get(anbieter):
        return False, f"KI-Server antwortet nicht ({llm_modul.modell_fehler[anbieter]}). Profil → 🔑 Zugänge."
    return True, f"{anbieter} · {ziel}"


def einrichtung(profil: Profil) -> list[tuple[bool, bool, str, str]]:
    """(ok, pflicht, Titel, Hinweis) je Baustein."""
    p = profil.person
    ki_ok, ki_text = ki_status(profil)
    return [
        (bool(p.name and p.email and p.telefon and p.ort), True, "Persönliche Daten",
         "Name, E-Mail, Telefon und Ort im Profil eintragen."),
        (profil.lebenslauf_pfad() is not None, True, "Lebenslauf",
         "Lebenslauf (PDF) unter Profil → Unterlagen hochladen."),
        (ki_ok, False, "KI für Bewertung & Anschreiben", ki_text),
        (mail.smtp_eingerichtet(), False, "E-Mail-Versand",
         "SMTP-Zugang unter Profil → 🔑 Zugänge eintragen – sonst nur Online-Formulare."),
        (ablauf.browser_verfuegbar(), False, "Online-Formulare (Browser-Agent)",
         'browser-use installieren: pip install -e ".[browser]"'),
        (bool(profil.angaben.anrede and profil.angaben.gehaltsvorstellung), False, "Formular-Angaben",
         "Anrede und Gehaltsvorstellung im Profil ergänzen – Formulare fragen oft danach."),
    ]


def zeige_einrichtung(profil: Profil) -> None:
    punkte = einrichtung(profil)
    fehlt_pflicht = [x for x in punkte if x[1] and not x[0]]
    fehlt_optional = [x for x in punkte if not x[1] and not x[0]]
    if not fehlt_pflicht and not fehlt_optional:
        return
    titel = ("⚠️ Noch nicht startklar – bitte ergänzen" if fehlt_pflicht
             else f"🔧 Einrichtung: {len(fehlt_optional)} Empfehlung(en)")
    with st.expander(titel, expanded=bool(fehlt_pflicht)):
        for ok, pflicht, name, hinweis in punkte:
            symbol = "✅" if ok else ("❌" if pflicht else "➖")
            st.markdown(f"{symbol} **{name}**" + ("" if ok else f" – {hinweis}"))
        st.page_link("seiten/profil.py", label="Zum Profil", icon="👤")


# --------------------------------------------------------------------------- #
# Stellen-Darstellung
# --------------------------------------------------------------------------- #

def job_kopf(job: Job, score: Score | None, *, ueberschrift: str = "####") -> None:
    links, rechts = st.columns([6, 1], vertical_alignment="top")
    with links:
        st.markdown(f"{ueberschrift} {job.titel}")
        ort = job.ort or "Ort unbekannt"
        if job.entfernung_km is not None:
            ort += f" ({job.entfernung_km:.0f} km)"
        st.markdown(
            f"**{job.arbeitgeber or 'Arbeitgeber unbekannt'}** · 📍 {ort} · "
            f"{QUELLE_TEXT.get(job.quelle, job.quelle)}"
            + (f" · seit {job.veroeffentlicht[:10]}" if job.veroeffentlicht else "")
        )
        merkmale = []
        if job.homeoffice:
            merkmale.append("🏠 Homeoffice")
        if job.befristung:
            merkmale.append(f"📄 {job.befristung}")
        if job.verguetung:
            merkmale.append(f"💶 {job.verguetung}")
        if merkmale:
            st.caption(" · ".join(merkmale))
    with rechts:
        if score:
            st.metric("Passung", f"{score.gesamt:.0f}", help=f"Fachlich {score.passung:.0f} · Ton {score.sentiment:.0f}")


def score_gruende(score: Score | None, max_treffer: int = 6) -> None:
    if not score:
        return
    # Die Heuristik-Begruendung wiederholt nur Treffer und Ton - dann kompakt anzeigen
    if score.begruendung and score.bewerter != "heuristik":
        st.caption(score.begruendung)
    zeilen = []
    if score.treffer:
        zeilen.append("✅ " + ", ".join(score.treffer[:max_treffer]))
    if score.bewerter == "heuristik":
        from .scoring.sentiment import SentimentErgebnis

        zeilen.append(f"🗣️ Ton der Anzeige: {SentimentErgebnis(score.sentiment).label}")
    if score.rot:
        zeilen.append("⚠️ " + ", ".join(score.rot[:4]))
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
    st.toast("Gespeichert.", icon="💾")


def _cb_nein(ref: str, praefix: str) -> None:
    profil = lade_profil()
    s = st.session_state
    gruende = s.get(_schluessel(praefix, ref, "grund")) or []
    with oeffne_db() as db:
        ablauf.ablehnen(
            db, profil, ref, grund=", ".join(gruende),
            arbeitgeber_sperren=bool(s.get(_schluessel(praefix, ref, "sperren"))),
        )
    st.toast("Abgelehnt – die Stelle taucht nicht wieder auf.", icon="❌")


def _cb_selbst(ref: str) -> None:
    with oeffne_db() as db:
        ablauf.selbst_beworben(db, ref)
    st.toast("Als „selbst beworben“ eingetragen.", icon="✅")


def _cb_neu_vorbereiten(ref: str) -> None:
    with oeffne_db() as db:
        ablauf.zuruecksetzen(db, ref)
        db.setze_vorgang(Vorgang(ref=ref, status="vorbereitung"))
    starte_hintergrund("vorbereiten", ref, "--ausloeser", "knopf", meldung="Wird neu vorbereitet …")


def _cb_nochmal(ref: str) -> None:
    profil = lade_profil()
    with oeffne_db() as db:
        try:
            ablauf.freigeben(db, profil, ref)
        except ValueError as e:
            st.session_state[f"nochmal_fehler_{ref}"] = str(e)
            return
    starte_hintergrund("senden", "--ausloeser", "ja", meldung="Neuer Versuch läuft …")


# --------------------------------------------------------------------------- #
# Karten
# --------------------------------------------------------------------------- #

def _versandzeile(v: Vorgang) -> str:
    if v.kanal == "email":
        return f"📧 **Versand per E-Mail** an `{v.empfaenger or '?'}` – Anschreiben-PDF und Unterlagen im Anhang"
    if v.kanal == "formular":
        return f"🌐 **Online-Formular** auf `{hostname(v.bewerbungs_url) or '?'}` – der Browser-Agent füllt es aus"
    return "✋ **Selbst bewerben** – kein automatischer Weg gefunden"


def _probelauf_anzeige(v: Vorgang) -> None:
    daten = v.probelauf_daten
    if not daten:
        return
    ausgefuellt = daten.get("ausgefuellte_felder") or []
    fehlend = daten.get("fehlende_angaben") or []
    titel = f"🧪 Probelauf: {len(ausgefuellt)} Felder ausgefüllt"
    if fehlend:
        titel += f", {len(fehlend)} offen"
    with st.expander(titel):
        if ausgefuellt:
            st.markdown("**Ausgefüllt:** " + ", ".join(ausgefuellt))
        if fehlend:
            st.warning("**Konnte nicht ausgefüllt werden:** " + ", ".join(str(f) for f in fehlend))
        if daten.get("hindernis"):
            st.markdown(f"**Hindernis:** {daten['hindernis']}")
        if daten.get("zusammenfassung"):
            st.caption(f"{daten['zusammenfassung']} · {daten.get('schritte', '?')} Schritte")


QUALITAET_HILFE = ("Selbstprüfung des Anschreibens: Sind alle Aussagen durch Lebenslauf/Profil belegt, "
                   "werden die Muss-Anforderungen angesprochen, passen Anrede und Länge?")


def _pruefung_anzeige(v: Vorgang) -> None:
    bericht = v.pruefung_daten
    if v.qualitaet is None and not bericht:
        return
    if v.anschreiben_quelle == "vorlage":
        st.badge("Anschreiben aus Vorlage – bitte prüfen/anpassen", icon="📝", color="orange")
    elif v.qualitaet is not None:
        farbe = score_farbe(v.qualitaet)
        text = f"Anschreiben-Qualität {v.qualitaet:.0f}/100" + (" · überarbeitet" if bericht.get("ueberarbeitet") else "")
        st.badge(text, icon="✍️", color=farbe, help=QUALITAET_HILFE)
    if bericht.get("unbelegt"):
        st.warning("**Nicht durch deinen Lebenslauf belegt:** " + "; ".join(bericht["unbelegt"][:4])
                   + " – bitte im Text korrigieren oder streichen.")
    rest = [*bericht.get("maengel", []), *(f"Nicht angesprochen: {m}" for m in bericht.get("fehlende_muss", [])[:3]),
            *bericht.get("hinweise", [])]
    if rest:
        with st.expander(f"🔎 Prüfbericht ({len(rest)} Punkte)"):
            st.markdown("\n".join(f"- {r}" for r in rest))


def _cb_autopilot_stopp(ref: str) -> None:
    from . import autopilot

    with oeffne_db() as db:
        autopilot.stoppen(db, ref)
    st.toast("Angehalten – diese Bewerbung geht nur mit deinem Ja raus.", icon="✋")


def _autopilot_anzeige(v: Vorgang, praefix: str) -> None:
    info = v.pruefung_daten.get("autopilot") or {}
    if v.auto_senden_ab:
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.info(f"🤖 **Autopilot:** geht **{zeit(v.auto_senden_ab)}** automatisch raus, wenn du nichts tust.")
        c2.button("✋ Stopp", key=f"{praefix}_stopp_{v.ref}", on_click=_cb_autopilot_stopp, args=(v.ref,),
                  width="stretch", help="Nicht automatisch senden – wartet dann auf dein Ja.")
    elif info.get("gruende") and info.get("freigabe") is False:
        st.caption("🤖 Autopilot sendet diese nicht allein: " + "; ".join(info["gruende"][:3]))


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


def angabe_merken(profil: Profil, frage: str, antwort: str) -> str:
    """Speichert eine Formular-Antwort im passenden Profilfeld oder unter `weitere`."""
    import re as _re

    antwort = antwort.strip()
    for muster, feld in _ANGABE_FELDER:
        if _re.search(muster, frage, _re.IGNORECASE):
            setattr(profil.angaben, feld, antwort)
            return feld
    profil.angaben.weitere[frage.strip()] = antwort
    return "weitere"


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
    st.toast(f"{gemerkt} Angabe(n) im Profil gemerkt – wird neu vorbereitet.", icon="💾")


def _fehlende_angaben(v: Vorgang, praefix: str) -> None:
    fehlend = [str(f) for f in (v.probelauf_daten.get("fehlende_angaben") or []) if str(f).strip()]
    if not fehlend:
        return
    with st.container(border=True):
        st.markdown("**📝 Das Formular fragt noch nach:** (einmal beantworten – der Agent merkt es sich)")
        for i, eintrag in enumerate(fehlend[:6]):
            feld, _, frage = eintrag.partition(":")
            st.text_input(frage.strip() or feld.strip(), key=f"{praefix}_angabe_{v.ref}_{i}")
        st.button("💾 Merken & neu vorbereiten", key=f"{praefix}_angaben_{v.ref}", on_click=_cb_angaben,
                  args=(v.ref, praefix, fehlend[:6]))


VORSCHLAG_ICON = {"nachfassen": "📨", "ausschluss": "🚫", "umkreis": "📍", "min_score": "🎯", "arbeitgeber": "🏢",
                  "stil": "✍️", "suchbegriff": "🔍"}


def _cb_vorschlag(vorschlag_id: int, ja: bool, praefix: str) -> None:
    from . import lernen

    profil = lade_profil()
    text = st.session_state.get(f"{praefix}_vtext_{vorschlag_id}")
    with oeffne_db() as db:
        meldung = lernen.wende_an(db, profil, vorschlag_id, ja, text=text)
    st.toast(meldung, icon="✅" if ja else "👌")


def offene_vorschlaege(db: Speicher) -> list[dict]:
    """Offene Vorschlaege des Agenten - unsinnige (z. B. Plattformnamen als Suchbegriff) vorher verwerfen."""
    from . import lernen

    lernen.verwerfe_unsinnige_vorschlaege(db)
    return db.vorschlaege("offen")


def vorschlag_karte(vorschlag: dict, praefix: str = "vk") -> None:
    """Ja/Nein-Karte fuer einen Vorschlag des Agenten (Nachfassen, Profil-Aenderung ...)."""
    with st.container(border=True):
        st.markdown(f"{VORSCHLAG_ICON.get(vorschlag['art'], '💡')} **{vorschlag['titel']}**")
        if vorschlag["art"] == "nachfassen":
            st.caption(f"An {vorschlag['daten'].get('empfaenger', '?')} · Betreff: {vorschlag['daten'].get('betreff', '')}")
            st.text_area("Text", value=vorschlag["text"], key=f"{praefix}_vtext_{vorschlag['id']}", height=200,
                         label_visibility="collapsed")
        elif vorschlag.get("text"):
            st.markdown(vorschlag["text"])
        c1, c2, _ = st.columns([1, 1, 3])
        c1.button("✅ Ja" + (", senden" if vorschlag["art"] == "nachfassen" else ""), type="primary",
                  key=f"{praefix}_vja_{vorschlag['id']}", on_click=_cb_vorschlag, args=(vorschlag["id"], True, praefix),
                  width="stretch")
        c2.button("❌ Nein", key=f"{praefix}_vnein_{vorschlag['id']}", on_click=_cb_vorschlag,
                  args=(vorschlag["id"], False, praefix), width="stretch")


def freigabe_karte(db: Speicher, profil: Profil, v: Vorgang, praefix: str = "fk") -> None:
    """Karte fuer eine vorbereitete Bewerbung mit Ja / Nein."""
    job, score = db.job(v.ref), db.score(v.ref)
    if job is None:
        return
    k = lambda feld: _schluessel(praefix, v.ref, feld)  # noqa: E731
    with st.container(border=True):
        job_kopf(job, score)
        if v.herkunft == "automatik":
            st.badge("automatisch vorgeschlagen", icon="🤖", color="violet")
        score_gruende(score)

        st.markdown(_versandzeile(v))
        if v.hinweis:
            (st.warning if v.kanal == "manuell" or "fehlgeschlagen" in v.hinweis.lower()
             or "unvollständig" in v.hinweis.lower() else st.caption)(v.hinweis)
        _autopilot_anzeige(v, praefix)
        _pruefung_anzeige(v)
        _probelauf_anzeige(v)
        _fehlende_angaben(v, praefix)

        with st.expander("✏️ Anschreiben & Versand prüfen / bearbeiten"):
            kanaele = ["email", "formular"] if v.kanal != "manuell" else ["manuell", "email", "formular"]
            if v.kanal not in kanaele:
                kanaele.insert(0, v.kanal)
            st.segmented_control(
                "Bewerbungsweg", kanaele, default=v.kanal, key=k("kanal"),
                format_func=lambda x: KANAL_TEXT.get(x, x),
            )
            kanal = st.session_state.get(k("kanal")) or v.kanal
            if kanal == "email":
                st.text_input("Empfänger", value=v.empfaenger or "", key=k("empf"),
                              placeholder="bewerbung@firma.de")
                st.text_area("E-Mail-Text", value=v.email_text or "", key=k("mail"), height=180)
                if not mail.smtp_eingerichtet():
                    st.warning("E-Mail-Versand ist noch nicht eingerichtet (Profil → 🔑 Zugänge). "
                               "Ein Ja gilt trotzdem – die Bewerbung geht raus, sobald SMTP eingetragen ist.")
            elif kanal == "formular":
                st.caption(f"Bewerbungsseite: {v.bewerbungs_url or job.bewerbungs_url or '–'}")
            st.text_area("Anschreiben", value=v.anschreiben or "", key=k("text"), height=320)
            spalten = st.columns(2)
            spalten[0].button("💾 Änderungen speichern", key=k("speichern"), on_click=_cb_speichern,
                              args=(v.ref, praefix), width="stretch")
            if v.anschreiben_pdf and Path(v.anschreiben_pdf).exists():
                spalten[1].download_button(
                    "📄 Anschreiben als PDF", Path(v.anschreiben_pdf).read_bytes(),
                    file_name=Path(v.anschreiben_pdf).name, mime="application/pdf",
                    key=k("pdf"), width="stretch",
                )

        fehler = st.session_state.get(k("fehler"))
        if fehler:
            st.error(fehler)

        knoepfe = st.columns([2, 1, 1, 1])
        if v.kanal == "manuell" and (st.session_state.get(k("kanal")) in (None, "manuell")):
            if job.anzeige_url:
                knoepfe[0].link_button("🔗 Zur Bewerbungsseite", job.bewerbungs_url or job.anzeige_url,
                                       width="stretch", type="primary")
            knoepfe[1].button("✅ Erledigt", key=k("selbst"), on_click=_cb_selbst, args=(v.ref,),
                              help="Ich habe mich selbst beworben", width="stretch")
        else:
            knoepfe[0].button("✅ Ja, absenden", key=k("ja"), type="primary", on_click=_cb_ja,
                              args=(v.ref, praefix), width="stretch")
        with knoepfe[2].popover("❌ Nein", width="stretch"):
            st.pills("Warum nicht?", NEIN_GRUENDE, selection_mode="multi", key=k("grund"))
            st.checkbox(f"„{job.arbeitgeber}“ nie wieder vorschlagen", key=k("sperren"))
            st.button("Ablehnen", key=k("nein"), on_click=_cb_nein, args=(v.ref, praefix), type="primary")
        if job.anzeige_url:
            knoepfe[3].link_button("🔗 Anzeige", job.anzeige_url, width="stretch")
        if v.kanal != "manuell":
            knoepfe[1].button("🔄 Neu", key=k("neu"), on_click=_cb_neu_vorbereiten, args=(v.ref,),
                              help="Anschreiben und Probelauf neu erzeugen", width="stretch")


def bewerbungs_email(job: Job, v: Vorgang) -> str | None:
    """Eine Adresse, an die man sich statt des Formulars per E-Mail bewerben kann."""
    from .bewerben.kanal import finde_bewerbungs_email

    kandidaten = [v.probelauf_daten.get("bewerbungs_email"), finde_bewerbungs_email(job.beschreibung)[0],
                  job.kontakt_email]
    return next((k.strip().lower() for k in kandidaten if k and "@" in k), None)


def _cb_per_email(ref: str, adresse: str) -> None:
    profil = lade_profil()
    with oeffne_db() as db:
        try:
            ablauf.freigeben(db, profil, ref, kanal="email", empfaenger=adresse)
        except ValueError as e:
            st.session_state[f"nochmal_fehler_{ref}"] = str(e)
            return
    starte_hintergrund("senden", "--ausloeser", "ja", meldung=f"Bewerbung geht per E-Mail an {adresse} …")


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


def hilfe_karte(db: Speicher, v: Vorgang, praefix: str = "hk") -> None:
    """Fehlgeschlagene Bewerbung: Ursache zeigen und beim Selbstbewerben helfen."""
    job = db.job(v.ref)
    if job is None:
        return
    with st.container(border=True):
        st.markdown(f"**{job.titel}** – {job.arbeitgeber} · {KANAL_TEXT.get(v.kanal, v.kanal)}")
        st.warning(v.hinweis or "Versand fehlgeschlagen.")
        fehler = st.session_state.get(f"nochmal_fehler_{v.ref}")
        if fehler:
            st.error(fehler)
        adresse = bewerbungs_email(job, v) if v.kanal == "formular" else None
        if adresse:
            if mail.smtp_eingerichtet():
                st.button(f"Stattdessen per E-Mail an {adresse} senden", key=f"{praefix}_mail_{v.ref}",
                          on_click=_cb_per_email, args=(v.ref, adresse), type="primary", icon=":material/mail:")
            else:
                st.caption(f"Die Anzeige nennt {adresse} – verbinde dein E-Mail-Konto (Einstellungen → Konten), "
                           "dann geht die Bewerbung auch ohne Formular raus.")
        if v.kanal == "formular" and v.hinweis and ("Browser" in v.hinweis or "browser" in v.hinweis):
            browser_test_anzeige(f"{praefix}_btest_{v.ref}")
        if v.anschreiben:
            with st.expander("📋 Anschreiben zum Kopieren"):
                st.code(v.anschreiben, language=None, wrap_lines=True)
        spalten = st.columns(4)
        spalten[0].button("🔁 Nochmal senden", key=f"{praefix}_nochmal_{v.ref}", on_click=_cb_nochmal,
                          args=(v.ref,), width="stretch")
        if job.bewerbungs_url or job.anzeige_url:
            spalten[1].link_button("🔗 Selbst bewerben", job.bewerbungs_url or job.anzeige_url, width="stretch")
        if v.anschreiben_pdf and Path(v.anschreiben_pdf).exists():
            spalten[2].download_button("📄 PDF", Path(v.anschreiben_pdf).read_bytes(),
                                       file_name=Path(v.anschreiben_pdf).name, mime="application/pdf",
                                       key=f"{praefix}_pdf_{v.ref}", width="stretch")
        spalten[3].button("✅ Selbst erledigt", key=f"{praefix}_selbst_{v.ref}", on_click=_cb_selbst,
                          args=(v.ref,), width="stretch")
