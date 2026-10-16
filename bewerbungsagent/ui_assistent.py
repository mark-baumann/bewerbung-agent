"""Erststart-Assistent: in fuenf kurzen Schritten startklar.

Wer bist du → Lebenslauf → Was & wo → E-Mail-Konto → Los geht's. Aus dem Lebenslauf
lese ich Jobtitel, Ort und Faehigkeiten, so ist „Was & wo“ schon vorbelegt. Das Profil
entsteht leer (kein Beispielprofil) und wird nach jedem Schritt gespeichert;
die Automatik startet erst, wenn der letzte Schritt bestaetigt ist.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from .config import Profil, Suche, daten_dir, ermittle_profil_pfad, lade_profil, speichere_profil

SCHRITTE = ["Wer bist du?", "Lebenslauf", "Was suchst du?", "E-Mail-Konto", "Los geht's"]
SCHLUESSEL = "assistent"


def _fortschritt_datei() -> Path:
    # Ueberlebt Neu-Laden der Seite (am Handy z. B. nach dem Datei-Dialog fuer den Lebenslauf)
    return daten_dir() / "assistent.json"


def _gespeicherter_schritt() -> int | None:
    try:
        schritt = int(json.loads(_fortschritt_datei().read_text(encoding="utf-8")).get("schritt", 0))
    except (OSError, ValueError, AttributeError):
        return None
    return schritt if 1 <= schritt <= len(SCHRITTE) else None


def _eingerichtet(profil: Profil | None) -> bool:
    """Fertig eingerichtet: Jobtitel vorhanden und die Automatik eingeschaltet (macht „Fertig“ oder
    Einstellungen → Automatik). Dann ist ein gemerkter Assistenten-Schritt nur noch ein Rest."""
    return profil is not None and bool(profil.suche.was) and profil.automatisierung.aktiv


def aktiv(profil: Profil | None) -> bool:
    """Assistent zeigen, solange kein Profil existiert oder er noch nicht abgeschlossen ist."""
    s = st.session_state
    if SCHLUESSEL not in s:
        if _eingerichtet(profil):
            _fortschritt_datei().unlink(missing_ok=True)
            return False
        gespeichert = _gespeicherter_schritt()
        if gespeichert:
            s[SCHLUESSEL] = gespeichert
        elif profil is None:
            s[SCHLUESSEL] = 1
    return SCHLUESSEL in s


def _weiter(schritt: int) -> None:
    st.session_state[SCHLUESSEL] = schritt
    if _eingerichtet(_profil()):
        return  # anderswo schon fertig eingerichtet (z. B. am Handy): nichts mehr merken
    try:
        _fortschritt_datei().parent.mkdir(parents=True, exist_ok=True)
        _fortschritt_datei().write_text(json.dumps({"schritt": schritt}), encoding="utf-8")
    except OSError:
        pass


def _abschliessen() -> None:
    st.session_state.pop(SCHLUESSEL, None)
    _fortschritt_datei().unlink(missing_ok=True)


def _profil() -> Profil:
    try:
        return lade_profil(pruefen=False)
    except FileNotFoundError:
        profil = Profil(suche=Suche(was=[], wo=""))
        profil.automatisierung.modus, profil.automatisierung.aktiv = "aus", False
        return profil


def _speichern(profil: Profil) -> None:
    speichere_profil(profil, ermittle_profil_pfad())


def _schritt_person() -> None:
    profil = _profil()
    p = profil.person
    st.markdown("Damit ich Anschreiben und Formulare in deinem Namen ausfüllen kann.")
    with st.form("assistent_person", border=False):
        c1, c2 = st.columns(2)
        vorname = c1.text_input("Vorname", value=p.vorname, key="assistent_vorname")
        nachname = c2.text_input("Nachname", value=p.nachname, key="assistent_nachname")
        email = st.text_input("E-Mail", value=p.email, key="assistent_email", autocomplete="email")
        telefon = st.text_input("Telefon", value=p.telefon, key="assistent_telefon", autocomplete="tel")
        c3, c4 = st.columns([1, 2])
        plz = c3.text_input("PLZ", value=p.plz, key="assistent_plz")
        ort = c4.text_input("Ort", value=p.ort, key="assistent_ort")
        strasse = st.text_input("Straße und Hausnummer", value=p.strasse, key="assistent_strasse")
        los = st.form_submit_button("Weiter", type="primary", icon=":material/arrow_forward:")
    if los:
        if not (vorname.strip() and nachname.strip() and email.strip()):
            st.error("Bitte mindestens Vorname, Nachname und E-Mail eintragen.")
            return
        p.vorname, p.nachname, p.email = vorname.strip(), nachname.strip(), email.strip()
        p.telefon, p.plz, p.ort, p.strasse = telefon.strip(), plz.strip(), ort.strip(), strasse.strip()
        if not profil.suche.wo:
            profil.suche.wo = p.ort
        _speichern(profil)
        _weiter(2)
        st.rerun()


def _schritt_suche() -> None:
    profil = _profil()
    s = profil.suche
    st.markdown("Das habe ich aus deinem Lebenslauf gelesen – passe es gern an. Je mehr Jobtitel, desto mehr "
                "Treffer; ähnliche finde ich später selbst dazu." if s.was else
                "Nenne ein paar Jobtitel – je mehr, desto mehr Treffer. Ich finde später selbst ähnliche dazu.")
    with st.form("assistent_suche", border=False):
        was = st.text_area("Jobtitel (einer pro Zeile)", value="\n".join(s.was), height=120, key="assistent_was",
                           placeholder="z. B.\nSachbearbeiter Buchhaltung\nBuchhalter")
        c1, c2 = st.columns([2, 1])
        wo = c1.text_input("Wo?", value=s.wo or profil.person.ort, key="assistent_wo",
                           help="Leer lassen für ganz Deutschland")
        umkreis = c2.number_input("Umkreis (km)", 0, 200, int(s.umkreis), step=5, key="assistent_umkreis")
        remote = st.checkbox("Auch Stellen im Homeoffice/remote zeigen", value=s.remote, key="assistent_remote")
        with st.container(horizontal=True):
            zurueck = st.form_submit_button("Zurück", icon=":material/arrow_back:")
            los = st.form_submit_button("Weiter", type="primary", icon=":material/arrow_forward:")
    if zurueck:
        _weiter(2)
        st.rerun()
    if los:
        begriffe = [z.strip() for z in was.splitlines() if z.strip()]
        if not begriffe:
            st.error("Bitte mindestens einen Jobtitel eintragen.")
            return
        s.was, s.wo, s.umkreis, s.remote = begriffe, wo.strip(), int(umkreis), bool(remote)
        _speichern(profil)
        _weiter(4)
        st.rerun()


def _schritt_lebenslauf() -> None:
    from . import lebenslauf
    from .ui_einstellungen import lebenslauf_hochladen

    st.markdown("Dein Lebenslauf geht mit jeder Bewerbung mit. Ich lese daraus, wonach ich suchen soll und was du "
                "kannst – so schreibe ich keine Aussagen ins Anschreiben, die nicht stimmen.")
    vorhanden = lebenslauf_hochladen(_profil(), schluessel="assistent_lebenslauf")
    if vorhanden:
        profil = _profil()
        with st.spinner("Ich lese deinen Lebenslauf …"):
            lebenslauf.aktualisieren(profil, ermittle_profil_pfad())
        if profil.suche.was:
            st.markdown(f"Ich suche nach **{' · '.join(profil.suche.was)}**"
                        + (f" in **{profil.suche.wo}**" if profil.suche.wo else "") + " – im nächsten Schritt "
                        "kannst du das anpassen.")
    with st.container(horizontal=True):
        st.button("Zurück", on_click=_weiter, args=(1,), icon=":material/arrow_back:", key="assistent_z3")
        st.button("Weiter" if vorhanden else "Später hochladen", on_click=_weiter, args=(3,),
                  type="primary" if vorhanden else "secondary", icon=":material/arrow_forward:", key="assistent_w3")


def _schritt_mail() -> None:
    from .ui_einstellungen import mailkonto_verbinden

    st.markdown("Über dein Postfach verschicke ich Bewerbungen per E-Mail und erkenne Antworten "
                "(Eingangsbestätigung, Einladung, Absage) von selbst.")
    verbunden = mailkonto_verbinden(_profil(), schluessel="assistent_mail")
    with st.container(horizontal=True):
        st.button("Zurück", on_click=_weiter, args=(3,), icon=":material/arrow_back:", key="assistent_z4")
        st.button("Weiter" if verbunden else "Überspringen", on_click=_weiter, args=(5,),
                  type="primary" if verbunden else "secondary", icon=":material/arrow_forward:", key="assistent_w4")


MODI = {"automatisch": "Automatisch vorbereiten (empfohlen)", "nur_suchen": "Nur suchen, ich wähle selbst aus"}


def _schritt_los() -> None:
    from . import ui

    profil = _profil()
    a = profil.automatisierung
    st.markdown(
        f"Ich suche **{ui.zeitplan_text(a.cron)}** nach **{', '.join(profil.suche.was[:3]) or '–'}**"
        f"{' in ' + profil.suche.wo if profil.suche.wo else ''}, schreibe für die besten Stellen ein Anschreiben "
        "und lege sie dir unter **Heute** vor. **Raus geht nur, was du mit Ja bestätigst.**"
    )
    modus = st.radio("Wie soll ich arbeiten?", list(MODI), format_func=MODI.get, key="assistent_modus",
                     index=list(MODI).index(a.modus) if a.modus in MODI else 0)
    ziel = st.number_input("Wie viele Bewerbungen pro Woche? (z. B. aus deiner Eingliederungsvereinbarung)", 1, 50,
                           int(a.ziel_pro_woche), key="assistent_ziel")
    with st.container(horizontal=True):
        if st.button("Zurück", icon=":material/arrow_back:", key="assistent_z5"):
            _weiter(4)
            st.rerun()
        if st.button("Fertig – jetzt erste Suche starten", type="primary", icon=":material/rocket_launch:",
                     key="assistent_fertig"):
            a.modus, a.aktiv, a.ziel_pro_woche = modus, True, int(ziel)
            _speichern(profil)
            _abschliessen()
            ui.starte_hintergrund("automatik", "--ausloeser", "knopf",
                                  meldung="Die erste Suche läuft – das dauert ein paar Minuten.")
            st.rerun()


def zeige() -> None:
    schritt = int(st.session_state.get(SCHLUESSEL, 1))
    st.title("Willkommen!")
    st.caption("In fünf kurzen Schritten bin ich startklar und bewerbe mich für dich – nur mit deinem Ja.")
    st.progress(schritt / len(SCHRITTE), text=f"Schritt {schritt} von {len(SCHRITTE)}: {SCHRITTE[schritt - 1]}")
    with st.container(border=True):
        st.subheader(SCHRITTE[schritt - 1])
        [_schritt_person, _schritt_lebenslauf, _schritt_suche, _schritt_mail, _schritt_los][schritt - 1]()
    if schritt > 3:  # Jobtitel stehen: man kann auch ohne Assistent weitermachen
        st.button("Assistent beenden – den Rest stelle ich unter Einstellungen ein", type="tertiary",
                  key="assistent_beenden", on_click=_abschliessen, icon=":material/close:")
