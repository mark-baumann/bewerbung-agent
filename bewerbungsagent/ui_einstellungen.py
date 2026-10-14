"""Profil-Tabs „Zugänge“ und „Jobbörsen & Wunsch-Arbeitgeber“.

Zugangsdaten landen in `daten/zugangsdaten.json` (siehe `zugangsdaten.py`),
nie in der Profil-YAML. Geheime Felder werden nie im Klartext angezeigt: ein
leeres Passwortfeld bedeutet „unverändert“.
"""

from __future__ import annotations

import os
from typing import Callable

import streamlit as st

from . import zugangsdaten
from .config import Profil
from .db import Speicher
from .scoring import llm as llm_modul
from .sources import QUELLEN
from .sources.ats import ATS_NAMEN, erkenne_ats, kurzbeschreibung

HERKUNFT_TEXT = {"ui": "hier gespeichert", "umgebung": "aus .env/Umgebung", None: "nicht gesetzt"}
TEST = {
    "smtp": ("🔌 Anmeldung testen", lambda profil: zugangsdaten.teste_smtp()),
    "imap": ("🔌 Postfach testen", lambda profil: zugangsdaten.teste_imap()),
    "ki": ("🔌 KI testen", lambda profil: zugangsdaten.teste_ki(profil.bewertung.modell)),
}
LAENDER = {"at": "Österreich", "ch": "Schweiz", "nl": "Niederlande", "lu": "Luxemburg", "be": "Belgien",
           "dk": "Dänemark", "fr": "Frankreich", "it": "Italien", "es": "Spanien", "pl": "Polen",
           "cz": "Tschechien", "se": "Schweden", "no": "Norwegen", "ie": "Irland", "pt": "Portugal"}
ART_TEXT = {"suche": "🔎 Stichwortsuche", "feed": "📰 Feed (lokal gefiltert)", "arbeitgeber": "🏢 Karriereseiten"}


def _testergebnis(schluessel: str) -> None:
    ergebnis = st.session_state.get(f"zugang_test_{schluessel}")
    if ergebnis:
        ok, text = ergebnis
        (st.success if ok else st.error)(text)


def _gruppe(gruppe: str, profil: Profil) -> None:
    felder = zugangsdaten.felder(gruppe)
    gespeichert = zugangsdaten.lade()
    with st.form(f"zugang_{gruppe}"):
        werte: dict[str, str] = {}
        spalten = st.columns(2)
        for i, f in enumerate(felder):
            herkunft = zugangsdaten.herkunft(f.name)
            aktuell = os.environ.get(f.name, "")
            hilfe = (f.hilfe + " · " if f.hilfe else "") + f"Status: {HERKUNFT_TEXT[herkunft]}"
            with spalten[i % 2]:
                if f.geheim:
                    werte[f.name] = st.text_input(
                        f.label, value="", type="password", help=hilfe,
                        placeholder=f"gespeichert {zugangsdaten.maskiert(aktuell)}" if aktuell else "nicht gesetzt",
                    )
                else:
                    werte[f.name] = st.text_input(f.label, value=aktuell, help=hilfe)
        if st.form_submit_button("💾 Speichern", type="primary"):
            aenderungen: dict[str, str | None] = {}
            for f in felder:
                neu = (werte[f.name] or "").strip()
                if f.geheim:
                    if neu:
                        aenderungen[f.name] = neu
                elif neu != os.environ.get(f.name, ""):
                    aenderungen[f.name] = neu or None
            if aenderungen:
                zugangsdaten.speichere(aenderungen)
                llm_modul.vergiss_modelle()
                st.session_state.pop(f"zugang_test_{gruppe}", None)
                st.success("Gespeichert – gilt sofort, auch für Automatik und Versand.")
            else:
                st.info("Keine Änderungen.")
    knoepfe = st.columns([1, 2, 2])
    if gruppe in TEST:
        text, funktion = TEST[gruppe]
        if knoepfe[0].button(text, key=f"zugang_testen_{gruppe}"):
            with st.spinner("Teste …"):
                st.session_state[f"zugang_test_{gruppe}"] = funktion(profil)
    hier = [f.name for f in felder if f.name in gespeichert]
    if hier and knoepfe[1].button("🗑️ Hier gespeicherte löschen", key=f"zugang_loeschen_{gruppe}",
                                   help="Danach gelten wieder die Werte aus .env/Umgebung (falls vorhanden)."):
        zugangsdaten.speichere({name: None for name in hier})
        llm_modul.vergiss_modelle()
        st.rerun()
    _testergebnis(gruppe)


def _ki_modell(profil: Profil, speichern: Callable[[str], None]) -> None:
    anbieter = llm_modul.provider(profil.bewertung.modell)
    if anbieter is None:
        st.info("Trage oben einen Schlüssel für Claude, ChatGPT oder einen Ollama-Server ein – ohne KI wird per "
                "Stichworten bewertet und das Anschreiben aus einer Vorlage erstellt.")
        return
    modelle = llm_modul.verfuegbare_modelle(anbieter)
    ziel, hinweis = llm_modul.pruefe_modell(anbieter, profil.bewertung.modell)
    st.markdown(f"**Aktiver Anbieter:** {anbieter} · **genutztes Modell:** `{ziel}`")
    if hinweis:
        st.warning(hinweis)
    if not modelle:
        st.warning(llm_modul.modell_fehler.get(anbieter) or "Der KI-Server hat keine Modellliste geliefert.")
        with st.form("ki_modell_frei"):
            name = st.text_input("Modellname", value=profil.bewertung.modell)
            if st.form_submit_button("💾 Modell speichern") and name.strip():
                profil.bewertung.modell = name.strip()
                speichern(f"Modell auf {name.strip()} gesetzt.")
        return
    optionen = list(modelle)
    index = optionen.index(ziel) if ziel in optionen else 0
    with st.form("ki_modell"):
        gewaehlt = st.selectbox(
            f"Modell für Bewertung, Anschreiben und Browser-Agent ({len(optionen)} bei deinem Zugang verfügbar)",
            optionen, index=index,
        )
        aufwand = st.segmented_control(
            "Denkaufwand (nur Claude)", ["low", "medium", "high"], default=profil.bewertung.effort,
            format_func={"low": "niedrig", "medium": "mittel", "high": "hoch"}.get,
        )
        if st.form_submit_button("💾 Modell speichern"):
            profil.bewertung.modell = gewaehlt
            profil.bewertung.effort = aufwand or profil.bewertung.effort
            llm_modul.modell_hinweise.pop(anbieter, None)
            speichern(f"Modell auf {gewaehlt} gesetzt.")
    if st.button("🔄 Modellliste neu laden"):
        llm_modul.verfuegbare_modelle(anbieter, neu_laden=True)
        st.rerun()


# --------------------------------------------------------------------------- #
# E-Mail-Konto mit Adresse + Passwort verbinden
# --------------------------------------------------------------------------- #

def mail_status() -> tuple[bool, str]:
    """(verbunden, Text) fuer Einrichtungs-Check und Zugaenge."""
    host, benutzer = os.environ.get("SMTP_HOST", ""), os.environ.get("SMTP_BENUTZER", "")
    if not host:
        return False, "Noch kein E-Mail-Konto verbunden."
    text = f"Verbunden: **{benutzer or '?'}** über `{host}`"
    if os.environ.get("IMAP_HOST"):
        text += " · Antworten werden erkannt"
    return True, text


def mailkonto_verbinden(profil: Profil, *, schluessel: str = "mailkonto") -> bool:
    """Ein-Schritt-Dialog: Adresse + Passwort, Rest automatisch. True, wenn verbunden."""
    from .bewerben import mailkonto

    verbunden, status = mail_status()
    ergebnis = st.session_state.get(f"{schluessel}_ergebnis")
    if verbunden and not st.session_state.get(f"{schluessel}_neu"):
        st.success(status, icon=":material/mark_email_read:")
        if ergebnis:
            for meldung in ergebnis:
                st.caption(meldung)
        if st.button("Anderes Konto verbinden", key=f"{schluessel}_wechseln", icon=":material/swap_horiz:"):
            st.session_state[f"{schluessel}_neu"] = True
            st.rerun()
        return True

    st.markdown("Gib die Adresse und das Passwort deines Postfachs ein – Server und Einstellungen "
                "sucht der Agent selbst heraus und prüft die Anmeldung sofort.")
    vorschlag = os.environ.get("SMTP_BENUTZER") or profil.person.email
    with st.form(f"{schluessel}_form", border=False):
        adresse = st.text_input("E-Mail-Adresse", value=vorschlag, placeholder="vorname@beispiel.de",
                                autocomplete="email")
        passwort = st.text_input("Passwort des Postfachs", type="password", autocomplete="current-password")
        los = st.form_submit_button("Verbinden & testen", type="primary", icon=":material/link:")
    if not los:
        return False
    try:
        with st.spinner("Suche die Server deines Anbieters …"):
            konto = mailkonto.erkenne(adresse)
        with st.spinner(f"Melde mich bei {konto.anbieter} an …"):
            e = mailkonto.verbinde_konto(konto, passwort)
    except ValueError as fehler:
        st.error(str(fehler))
        return False
    if e.smtp_ok:
        st.session_state.pop(f"{schluessel}_neu", None)
        meldungen = [f"Anbieter: {konto.anbieter} · Versand über {e.smtp.text}"]
        meldungen.append(f"Antworten erkennen über {e.imap.text}" if e.imap_ok
                         else "Antworten-Erkennung (IMAP) klappt noch nicht: " + "; ".join(
                             m for m in e.meldungen if m.startswith("Empfang")))
        st.session_state[f"{schluessel}_ergebnis"] = meldungen
        if not profil.person.email:
            profil.person.email = konto.adresse
        st.success(f"Dein Postfach {konto.adresse} ist verbunden – Bewerbungen gehen ab jetzt von dort raus.",
                   icon=":material/check_circle:")
        st.rerun()
        return True
    st.error("Verbinden hat nicht geklappt:\n\n" + "\n".join(f"- {m}" for m in e.meldungen))
    if konto.hinweis:
        st.info(konto.hinweis)
    st.caption(f"Erkannt: {konto.anbieter} · Versand {', '.join(s.text for s in konto.smtp)} · "
               f"Empfang {', '.join(s.text for s in konto.imap)}")
    return False


def zeige_zugaenge(profil: Profil, speichern: Callable[[str], None]) -> None:
    st.caption(
        "Zugangsdaten werden nur auf diesem Gerät gespeichert (`daten/zugangsdaten.json`, nur für dich lesbar) – "
        "nie im Profil und nie im Git. Was du hier einträgst, hat Vorrang vor der `.env` und gilt sofort, "
        "auch für Automatik und Versand. Passwortfelder leer lassen = unverändert."
    )
    with st.container(border=True):
        st.markdown("##### :material/mail: Dein E-Mail-Konto")
        mailkonto_verbinden(profil)
    with st.container(border=True):
        from .ui import browser_test_anzeige

        st.markdown("##### :material/language: Online-Formulare (Browser auf dem Server)")
        browser_test_anzeige("zugang_browser_test")
    for gruppe, titel in zugangsdaten.GRUPPEN.items():
        if gruppe in ("smtp", "imap"):
            continue
        with st.container(border=True):
            st.markdown(f"##### {titel}")
            if gruppe == "imap":
                st.caption("Damit erkennt der Agent Eingangsbestätigungen, Einladungen und Absagen selbst. "
                           "Mails werden nur gelesen, nie verändert. Benutzer/Passwort leer = wie SMTP.")
            if gruppe == "ba":
                st.caption("Für „Online bewerben“ auf arbeitsagentur.de. Der Browser-Agent bekommt die Daten nur als "
                           "Platzhalter – das KI-Modell sieht sie nie im Klartext.")
            _gruppe(gruppe, profil)
            if gruppe == "ki":
                st.divider()
                _ki_modell(profil, speichern)
            if gruppe == "quellen":
                for name in ("adzuna", "jooble"):
                    info = QUELLEN[name]
                    status = "✅ aktiv nutzbar" if info.verfuegbar() else "➖ Schlüssel fehlt"
                    c1, c2 = st.columns([3, 1])
                    c1.caption(f"**{info.label}**: {status}")
                    if info.verfuegbar() and c2.button("🔌 Testen", key=f"quelle_test_{name}"):
                        st.session_state[f"zugang_test_{name}"] = zugangsdaten.teste_quelle(name)
                    _testergebnis(name)
    with st.expander("Mail-Server selbst eintragen (für Fortgeschrittene)", icon=":material/tune:"):
        for gruppe in ("smtp", "imap"):
            st.markdown(f"**{zugangsdaten.GRUPPEN[gruppe]}**")
            _gruppe(gruppe, profil)


# --------------------------------------------------------------------------- #
# Jobbörsen & Wunsch-Arbeitgeber
# --------------------------------------------------------------------------- #

def _letzter_abruf_text(db: Speicher, quelle: str) -> str:
    abruf = db.letzter_abruf_stand(quelle)
    if not abruf:
        return "noch nie abgefragt"
    from .ui import zeit

    if abruf.get("fehler"):
        return f"⚠️ letzter Abruf {zeit(abruf['gestartet_am'])} fehlgeschlagen: {str(abruf['fehler'])[:120]}"
    return f"zuletzt {zeit(abruf['gestartet_am'])} · {abruf.get('treffer') or 0} Treffer"


def zeige_jobboersen(profil: Profil, speichern: Callable[[str], None], db: Speicher) -> None:
    s, b = profil.suche, profil.bewertung
    st.caption("Der Agent fragt alle aktivierten Jobbörsen bei jeder Suche ab und führt doppelte Anzeigen zusammen. "
               "StepStone, Indeed, LinkedIn und XING sind bewusst nicht dabei – deren Bedingungen verbieten "
               "automatische Abfragen.")
    with st.form("jobboersen"):
        aktiv: dict[str, bool] = {}
        for name, info in QUELLEN.items():
            c1, c2 = st.columns([1, 3], vertical_alignment="center")
            hinweis = "" if info.verfuegbar() else " · API-Key unter 🔑 Zugänge eintragen"
            aktiv[name] = c1.toggle(info.label, value=name in s.quellen, key=f"quelle_{name}",
                                    disabled=not info.verfuegbar() and name not in s.quellen)
            c2.caption(f"{ART_TEXT.get(info.art, info.art)} · {info.beschreibung}{hinweis}  \n"
                       f"{_letzter_abruf_text(db, name)}")
        st.divider()
        c1, c2 = st.columns(2)
        remote = c1.checkbox("Auch Remote-Stellen (ganz Deutschland/EU) vorschlagen", value=s.remote)
        ausbildung = c1.checkbox("Zusätzlich Ausbildungsplätze suchen (Arbeitsagentur)", value=s.ausbildung)
        zeitarbeit_aus = c1.checkbox("Zeitarbeit/Personalvermittlung ausschließen",
                                     value=any(a.lower() == "zeitarbeit" for a in b.ausschluss))
        ausland = c2.multiselect("EURES: auch in diesen Ländern suchen", list(LAENDER), default=list(s.ausland),
                                 format_func=lambda k: LAENDER.get(k, k))
        if st.form_submit_button("💾 Jobbörsen speichern", type="primary"):
            s.quellen = [n for n, an in aktiv.items() if an] or ["arbeitsagentur"]
            s.remote, s.ausbildung, s.ausland = remote, ausbildung, list(ausland)
            ohne = [a for a in b.ausschluss if a.lower() != "zeitarbeit"]
            b.ausschluss = [*ohne, "Zeitarbeit"] if zeitarbeit_aus else ohne
            speichern("Jobbörsen gespeichert.")

    st.markdown("##### 🏢 Wunsch-Arbeitgeber")
    st.caption("Füge die Karriereseite einer Firma ein – der Agent erkennt das Bewerbermanagement "
               f"({', '.join(ATS_NAMEN.values())}) und holt neue passende Stellen direkt von dort.")
    with st.form("zielfirma_neu", clear_on_submit=True):
        c1, c2 = st.columns([2, 3])
        name = c1.text_input("Firma", placeholder="z. B. Musterfirma GmbH")
        url = c2.text_input("Karriereseite", placeholder="https://musterfirma.de/karriere")
        if st.form_submit_button("➕ Hinzufügen & erkennen"):
            if not url.strip():
                st.error("Bitte die Adresse der Karriereseite angeben.")
            else:
                with st.spinner("Erkenne Bewerbermanagement …"):
                    try:
                        erkannt = erkenne_ats(url.strip()) or {}
                    except Exception:
                        erkannt = {}
                firma = {"name": name.strip() or erkannt.get("kennung") or url.strip(), "url": url.strip(), **erkannt}
                profil.zielfirmen = [*profil.zielfirmen, firma]
                if "wunscharbeitgeber" not in s.quellen:
                    s.quellen = [*s.quellen, "wunscharbeitgeber"]
                if erkannt:
                    speichern(f"{kurzbeschreibung(firma)} hinzugefügt.")
                else:
                    speichern(f"{firma['name']} hinzugefügt – Bewerbermanagement noch nicht erkannt. "
                              "Tipp: den Link „Alle Stellen“ oder einen Stellenlink der Firma einfügen.")
    if not profil.zielfirmen:
        st.info("Noch keine Wunsch-Arbeitgeber.")
    for i, firma in enumerate(profil.zielfirmen):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        erkannt = bool(firma.get("ats") and firma.get("kennung"))
        c1.markdown(("✅ " if erkannt else "❔ ") + (kurzbeschreibung(firma) if erkannt else firma.get("name", "?"))
                    + (f"  \n[{firma['url']}]({firma['url']})" if firma.get("url") else ""))
        if c2.button("Entfernen", key=f"zielfirma_weg_{i}"):
            profil.zielfirmen = [f for j, f in enumerate(profil.zielfirmen) if j != i]
            speichern("Entfernt.")
            st.rerun()
