"""Suchen: eine Seite – oben suchen, darunter die Treffer ankreuzen. Entschieden wird auf „Heute“."""

import streamlit as st

from bewerbungsagent import ablauf, ui, vorschlaege
from bewerbungsagent.config import ermittle_profil_pfad, speichere_profil
from bewerbungsagent.models import Vorgang
from bewerbungsagent.scoring import llm as llm_modul

TAGE = [7, 14, 30, 100]

profil, fehler = ui.profil_status()
if fehler:
    ui.profil_fehler(fehler)
    st.stop()
if profil is None:
    st.info("Noch nicht eingerichtet – der Assistent führt dich in fünf kurzen Schritten durch.",
            icon=":material/waving_hand:")
    st.page_link(ui.SEITE_HEUTE, label="Einrichtung starten", icon=":material/rocket_launch:")
    st.stop()

s = st.session_state
# Nach „Weiter: … vorbereiten“ geht es auf „Heute“ weiter – dort wird entschieden.
if s.pop("sfs_zu_heute", False):
    st.switch_page(ui.SEITE_HEUTE)

# Vorbelegt aus dem Profil – Jobtitel und Ort kommen aus dem Lebenslauf (Seite „Lebenslauf“). Aendert sich das
# Profil (neu ausgelesen oder unter Einstellungen gespeichert), gelten die neuen Werte sofort.
_quelle = (tuple(profil.suche.was), profil.suche.wo, int(profil.suche.umkreis))
if "sfs_begriffe" not in s or s.get("sfs_quelle") != _quelle:
    s["sfs_quelle"] = _quelle
    p = profil.suche
    s["sfs_begriffe"] = list(p.was)
    s["sfs_wo"] = p.wo or ""
    s["sfs_umkreis"] = int(min(200, max(0, p.umkreis)))
    s["sfs_tage"] = min(TAGE, key=lambda t: abs(t - int(p.veroeffentlicht_seit_tagen)))
    s["sfs_max"] = int(min(300, max(5, p.max_pro_query)))
    s["sfs_vollzeit"] = bool(p.nur_vollzeit)
    s["sfs_quellen"] = ui.quellen_auswahl(p.quellen)
    s["sfs_min"] = int(profil.bewertung.min_score)


def vorschlag_hinzufuegen() -> None:
    wahl = st.session_state.get("sfs_vorschlag")
    if wahl and wahl not in st.session_state["sfs_begriffe"]:
        st.session_state["sfs_begriffe"] = [*st.session_state["sfs_begriffe"], wahl]
    st.session_state["sfs_vorschlag"] = None


def ki_vorschlaege_holen() -> None:
    with ui.oeffne_db() as db_:
        gute = [j.titel for j, _ in db_.bestenliste(min_score=profil.bewertung.min_score, limit=20)]
    try:
        st.session_state["sfs_ki"] = vorschlaege.per_llm(profil, gute)
        st.session_state.pop("sfs_ki_fehler", None)
    except Exception as e:
        st.session_state["sfs_ki_fehler"] = str(e)


def suche_starten() -> None:
    begriffe = [b.strip() for b in s["sfs_begriffe"] if b.strip()]
    if not begriffe:
        st.error("Bitte mindestens einen Jobtitel angeben.")
        return
    if s.get("sfs_profil_merken"):
        profil.suche.was = begriffe
        profil.suche.wo = s["sfs_wo"].strip()
        profil.suche.umkreis = int(s["sfs_umkreis"])
        profil.suche.veroeffentlicht_seit_tagen = int(s["sfs_tage"])
        profil.suche.max_pro_query = int(s["sfs_max"])
        profil.suche.nur_vollzeit = bool(s["sfs_vollzeit"])
        profil.suche.quellen = list(s["sfs_quellen"]) or ["arbeitsagentur"]
        profil.bewertung.min_score = float(s["sfs_min"])
        speichere_profil(profil, ermittle_profil_pfad())
    with st.status("Ich suche …", expanded=True) as status, ui.oeffne_db() as db_:
        balken = st.progress(0.0)
        zeile = st.empty()

        def fortschritt(meldung: str, anteil: float) -> None:
            zeile.write(meldung)
            balken.progress(min(1.0, max(0.0, anteil)))

        ergebnis = ablauf.suche_ausfuehren(
            db_, profil, begriffe=begriffe, wo=s["sfs_wo"].strip(), umkreis=int(s["sfs_umkreis"]),
            tage=int(s["sfs_tage"]), max_treffer=int(s["sfs_max"]), nur_vollzeit=bool(s["sfs_vollzeit"]),
            quellen=list(s["sfs_quellen"]) or ["arbeitsagentur"], fortschritt=fortschritt,
        )
        status.update(label=f"{len(ergebnis.refs)} Stellen gefunden – ich prüfe, wie gut sie passen …")
        ablauf.bewerte_neue(db_, profil, refs=ergebnis.refs, mit_llm=llm_modul.client_verfuegbar(),
                            fortschritt=fortschritt)
        status.update(label="Fertig", state="complete")
    s["sfs_meldung"] = f"{len(ergebnis.refs)} Stellen gefunden, davon {ergebnis.neu} neu."
    if ergebnis.fehler:
        s["sfs_meldung"] += f" {len(ergebnis.fehler)} Jobbörse(n) gerade nicht erreichbar."
    st.rerun()


# --------------------------------------------------------------------------- #
# Suchen
# --------------------------------------------------------------------------- #

st.title("Stellen suchen")

with ui.oeffne_db() as db:
    letzte_suche = db.letzte_suche()
    aehnliche = vorschlaege.aus_treffern(db, profil)
ki = list(s.get("sfs_ki", []))
alle_vorschlaege = list({v.begriff: v for v in [*aehnliche, *ki] if v.begriff not in s["sfs_begriffe"]}.values())

with st.container(border=True):
    optionen = list(dict.fromkeys([*s["sfs_begriffe"], *profil.suche.was, *(v.begriff for v in alle_vorschlaege)]))
    st.multiselect("Jobtitel", optionen, key="sfs_begriffe", accept_new_options=True,
                   placeholder="Jobtitel eintippen und Enter drücken",
                   help="Kommen aus deinem Lebenslauf. Mehrere Jobtitel bringen mehr Treffer.")
    if alle_vorschlaege:
        st.pills("Ähnliche Jobtitel – antippen zum Hinzufügen", [v.begriff for v in alle_vorschlaege],
                 key="sfs_vorschlag", on_change=vorschlag_hinzufuegen,
                 help="\n".join(f"{v.begriff}: {v.grund}" for v in alle_vorschlaege))
    c1, c2 = st.columns([2, 1])
    c1.text_input("Wo?", key="sfs_wo", help="Leer lassen für ganz Deutschland")
    c2.number_input("Umkreis (km)", 0, 200, step=5, key="sfs_umkreis")
    with st.expander("Mehr Filter", icon=":material/tune:"):
        c3, c4 = st.columns(2)
        c3.select_slider("Veröffentlicht in den letzten …", TAGE, key="sfs_tage", format_func=lambda t: f"{t} Tagen")
        c4.number_input("Höchstens Treffer je Jobtitel und Jobbörse", 5, 300, step=5, key="sfs_max")
        st.slider("Mindest-Passung", 0, 100, step=5, key="sfs_min",
                  help="Passung 0–100. Stellen darunter zeige ich nur, wenn sonst nichts passt.")
        st.multiselect("Jobbörsen", ui.quellen_optionen(), key="sfs_quellen", format_func=ui.quellen_label)
        st.checkbox("Nur Vollzeit", key="sfs_vollzeit")
        st.checkbox("Diese Einstellungen auch für die Automatik übernehmen", key="sfs_profil_merken")
        if llm_modul.client_verfuegbar():
            st.button("Mehr ähnliche Jobtitel finden", on_click=ki_vorschlaege_holen, icon=":material/auto_awesome:",
                      type="tertiary")
            if s.get("sfs_ki_fehler"):
                st.caption(f"Gerade keine Ideen von der KI: {s['sfs_ki_fehler'][:200]}")
    if st.button("Suchen", type="primary", icon=":material/search:", width="stretch", key="sfs_suchen"):
        suche_starten()


# --------------------------------------------------------------------------- #
# Treffer ankreuzen
# --------------------------------------------------------------------------- #

def nicht_interessant(ref: str) -> None:
    with ui.oeffne_db() as db_:
        ablauf.ablehnen(db_, profil, ref, grund="nicht interessant")


def alle_markieren(refs: list[str], wert: bool) -> None:
    for ref in refs:
        st.session_state[f"sfs_sel_{ref}"] = wert


def vorbereiten_starten(refs: list[str]) -> None:
    with ui.oeffne_db() as db_:
        for ref in refs:
            db_.setze_vorgang(Vorgang(ref=ref, status="vorbereitung", herkunft="manuell"))
    argumente = ["vorbereiten", *refs, "--ausloeser", "knopf"]
    if not (profil.automatisierung.probelauf_vorab and ablauf.browser_verfuegbar()):
        argumente.append("--ohne-probelauf")
    ui.starte_hintergrund(*argumente, meldung=f"{len(refs)} Bewerbung(en) werden vorbereitet …")
    for ref in refs:
        st.session_state.pop(f"sfs_sel_{ref}", None)
    st.session_state["sfs_zu_heute"] = True


def jetzt_bewerten(refs: list[str]) -> None:
    with ui.oeffne_db() as db_:
        ablauf.bewerte_neue(db_, profil, refs=refs, mit_llm=llm_modul.client_verfuegbar())


def treffer_karte(job, score) -> None:
    with st.container(border=True):
        auswahl, inhalt = st.columns([1, 12], vertical_alignment="top", gap="small")
        auswahl.checkbox("Bewerben", key=f"sfs_sel_{job.ref}", label_visibility="collapsed")
        with inhalt:
            ui.job_kopf(job, score, ueberschrift="#####")
            if score and score.treffer:
                st.caption(":material/check: Passt bei " + ", ".join(score.treffer[:5])
                           + (f" · :material/warning: {', '.join(score.rot[:2])}" if score.rot else ""))
            with st.container(horizontal=True, gap="small"):
                with st.popover("Anzeige lesen", icon=":material/article:"):
                    st.markdown(f"**{job.titel}** – {job.arbeitgeber}")
                    st.text((job.beschreibung or "Keine Beschreibung geladen.")[:6000])
                if job.anzeige_url:
                    st.link_button("Original", job.anzeige_url, icon=":material/open_in_new:")
                st.button("Nicht interessant", key=f"sfs_weg_{job.ref}", on_click=nicht_interessant,
                          args=(job.ref,), icon=":material/thumb_down:", type="tertiary")


with ui.oeffne_db() as db:
    refs = letzte_suche["refs"] if letzte_suche else None  # Treffer der letzten Suche, sonst alle offenen
    sperre = profil.bewertung.ausschluss_arbeitgeber
    kandidaten = db.kandidaten(min_score=s["sfs_min"], limit=100, refs=refs, ausschluss_arbeitgeber=sperre)
    weniger = [] if kandidaten else db.kandidaten(min_score=0, limit=10, refs=refs, ausschluss_arbeitgeber=sperre)
    unbewertet = [r for r in (refs or []) if db.score(r) is None]

if s.get("sfs_meldung"):
    st.caption(f":material/check_circle: {s['sfs_meldung']}")
if unbewertet:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"{len(unbewertet)} Treffer sind noch nicht geprüft.")
        st.button("Jetzt prüfen", on_click=jetzt_bewerten, args=(unbewertet,), icon=":material/grading:",
                  type="tertiary")

if kandidaten or weniger:
    st.subheader(f":material/checklist: Treffer ({len(kandidaten)})" if kandidaten
                 else ":material/checklist: Weniger passende Treffer")
    st.caption("Hake an, wofür ich ein Anschreiben schreiben soll. Abgeschickt wird erst nach deinem Ja auf „Heute“."
               if kandidaten else f"Keine Stelle passt zu mindestens {s['sfs_min']} Punkten – das sind die besten "
               "darunter.")
    liste = kandidaten or weniger
    alle_refs = [j.ref for j, _ in liste]
    if len(liste) > 3:
        with st.container(horizontal=True):
            st.button("Alle auswählen", on_click=alle_markieren, args=(alle_refs, True), icon=":material/done_all:",
                      type="tertiary")
            st.button("Keine", on_click=alle_markieren, args=(alle_refs, False), icon=":material/remove_done:",
                      type="tertiary")
    for job, score in liste:
        treffer_karte(job, score)
    gewaehlt = [r for r in alle_refs if s.get(f"sfs_sel_{r}")]
    st.button(f"Weiter: {len(gewaehlt)} Bewerbung(en) vorbereiten", type="primary", width="stretch",
              disabled=not gewaehlt, on_click=vorbereiten_starten, args=(gewaehlt,), key="sfs_weiter",
              icon=":material/arrow_forward:", icon_position="right")
elif letzte_suche:
    st.caption("Keine offenen Treffer aus der letzten Suche – probiere andere Jobtitel oder einen größeren Umkreis.")
