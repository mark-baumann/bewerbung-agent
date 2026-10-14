"""Suchen: selbst suchen und Stellen auswählen. Geprüft und freigegeben wird auf „Heute“."""

import streamlit as st

from bewerbungsagent import ablauf, ui, vorschlaege
from bewerbungsagent.config import ermittle_profil_pfad, speichere_profil
from bewerbungsagent.models import Vorgang
from bewerbungsagent.scoring import llm as llm_modul

SCHRITTE = {1: "Suchen", 2: "Auswählen"}
TAGE = [7, 14, 30, 100]

profil = ui.profil_laden()
if profil is None:
    st.info("Noch nicht eingerichtet – der Assistent führt dich in fünf kurzen Schritten durch.",
            icon=":material/waving_hand:")
    st.page_link(ui.SEITE_HEUTE, label="Einrichtung starten", icon=":material/rocket_launch:")
    st.stop()

s = st.session_state
# Nach „Weiter: … vorbereiten“ geht es auf „Heute“ weiter – dort wird entschieden.
if s.pop("sfs_zu_heute", False):
    st.switch_page(ui.SEITE_HEUTE)
s.setdefault("sfs_schritt", 1)
if s["sfs_schritt"] not in SCHRITTE:
    s["sfs_schritt"] = 2
# Streamlit verwirft Widget-Zustand, sobald ein Widget nicht gerendert wird
# (z. B. das Suchformular in Schritt 2). Erneutes Zuweisen haelt die Werte.
for _schluessel in ("sfs_begriffe", "sfs_wo", "sfs_umkreis", "sfs_tage", "sfs_max", "sfs_vollzeit",
                    "sfs_quellen", "sfs_min", "sfs_umfang", "sfs_profil_merken"):
    if _schluessel in s:
        s[_schluessel] = s[_schluessel]


def gehe_zu(schritt: int) -> None:
    st.session_state["sfs_schritt"] = schritt


# --------------------------------------------------------------------------- #
# Suchformular vorbelegen (letzte Suche > Profil)
# --------------------------------------------------------------------------- #

with ui.oeffne_db() as db:
    letzte_suche = db.letzte_suche()
if "sfs_begriffe" not in s:
    lp = (letzte_suche or {}).get("parameter", {})
    p = profil.suche
    s["sfs_begriffe"] = list(lp.get("begriffe") or p.was)
    s["sfs_wo"] = lp.get("wo", p.wo or "")
    s["sfs_umkreis"] = int(min(200, max(0, lp.get("umkreis", p.umkreis))))
    tage = int(lp.get("tage", p.veroeffentlicht_seit_tagen))
    s["sfs_tage"] = min(TAGE, key=lambda t: abs(t - tage))
    s["sfs_max"] = int(min(300, max(5, lp.get("max_treffer", p.max_pro_query))))
    s["sfs_vollzeit"] = bool(lp.get("nur_vollzeit", p.nur_vollzeit))
    s["sfs_quellen"] = ui.quellen_auswahl(lp.get("quellen") or p.quellen)
s.setdefault("sfs_min", int(profil.bewertung.min_score))


# --------------------------------------------------------------------------- #
# Kopf: zwei Schritte
# --------------------------------------------------------------------------- #

st.title("Stellen suchen")
with ui.oeffne_db() as db:
    anzahl_auswahl = len(db.kandidaten(min_score=s.get("sfs_min", profil.bewertung.min_score), limit=200,
                                       ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber))
with st.container(horizontal=True, gap="small"):
    for nummer, name in SCHRITTE.items():
        label = f"{nummer}. {name}" + (f" ({anzahl_auswahl})" if nummer == 2 and anzahl_auswahl else "")
        st.button(label, key=f"sfs_nav_{nummer}", on_click=gehe_zu, args=(nummer,),
                  icon=":material/search:" if nummer == 1 else ":material/checklist:",
                  type="primary" if nummer == s["sfs_schritt"] else "secondary")
schritt = s["sfs_schritt"]


# --------------------------------------------------------------------------- #
# 1 · Suchen
# --------------------------------------------------------------------------- #

def vorschlag_hinzufuegen() -> None:
    wahl = st.session_state.get("sfs_vorschlag")
    if wahl and wahl not in st.session_state["sfs_begriffe"]:
        st.session_state["sfs_begriffe"] = [*st.session_state["sfs_begriffe"], wahl]
    st.session_state["sfs_vorschlag"] = None


def tipp_uebernehmen(aenderung: dict) -> None:
    zuordnung = {"umkreis": "sfs_umkreis", "tage": "sfs_tage", "nur_vollzeit": "sfs_vollzeit", "quellen": "sfs_quellen"}
    for feld, wert in aenderung.items():
        if feld in zuordnung:
            st.session_state[zuordnung[feld]] = wert


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
        st.error("Bitte mindestens einen Suchbegriff angeben.")
        return
    if s.get("sfs_profil_merken"):
        profil.suche.was = begriffe
        profil.suche.wo = s["sfs_wo"].strip()
        profil.suche.umkreis = int(s["sfs_umkreis"])
        profil.suche.veroeffentlicht_seit_tagen = int(s["sfs_tage"])
        profil.suche.max_pro_query = int(s["sfs_max"])
        profil.suche.nur_vollzeit = bool(s["sfs_vollzeit"])
        profil.suche.quellen = list(s["sfs_quellen"]) or ["arbeitsagentur"]
        speichere_profil(profil, ermittle_profil_pfad())
    with st.status("Suche läuft …", expanded=True) as status, ui.oeffne_db() as db_:
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
        status.update(label=f"{len(ergebnis.refs)} Stellen gefunden – bewerte …")
        mit_llm = llm_modul.client_verfuegbar()
        scores = ablauf.bewerte_neue(db_, profil, refs=ergebnis.refs, mit_llm=mit_llm, fortschritt=fortschritt)
        passend = sum(1 for sc in scores if not sc.ausgeschlossen and sc.gesamt >= s["sfs_min"])
        status.update(label="Fertig", state="complete")
    s["sfs_meldung"] = (
        f"{len(ergebnis.refs)} Stellen gefunden ({ergebnis.neu} neu), {len(scores)} neu bewertet"
        f"{' mit KI' if mit_llm else ' per Stichworten'}, davon {passend} passend."
        + (f" {len(ergebnis.fehler)} Jobbörsen-Abfrage(n) fehlgeschlagen: " + "; ".join(ergebnis.fehler[:3])
           if ergebnis.fehler else "")
    )
    s["sfs_umfang"] = "letzte"
    s["sfs_schritt"] = 2
    st.rerun()


if schritt == 1:
    with ui.oeffne_db() as db:
        aehnliche = vorschlaege.aus_treffern(db, profil)
        tipps = vorschlaege.such_tipps(db, profil)
        statistik = db.suchbegriff_statistik(profil.bewertung.min_score)
    ki = [v for v in s.get("sfs_ki", []) if v.begriff not in s["sfs_begriffe"]]
    alle_vorschlaege = [v for v in [*aehnliche, *ki] if v.begriff not in s["sfs_begriffe"]]

    with st.container(border=True):
        optionen = list(dict.fromkeys([*s["sfs_begriffe"], *profil.suche.was, *(v.begriff for v in alle_vorschlaege)]))
        st.multiselect("Jobtitel", optionen, key="sfs_begriffe", accept_new_options=True,
                       placeholder="Jobtitel eintippen und Enter drücken",
                       help="Mehrere Jobtitel bringen mehr Treffer. Danach bewerte ich, wie gut jede Stelle passt.")
        if alle_vorschlaege:
            st.pills("Ähnliche Jobtitel – antippen zum Hinzufügen", [v.begriff for v in alle_vorschlaege],
                     key="sfs_vorschlag", on_change=vorschlag_hinzufuegen,
                     help="\n".join(f"{v.begriff}: {v.grund}" for v in alle_vorschlaege))
        c1, c2 = st.columns([2, 1])
        c1.text_input("Wo?", key="sfs_wo", help="Leer lassen für ganz Deutschland")
        c2.number_input("Umkreis (km)", 0, 200, step=5, key="sfs_umkreis")
        with st.expander("Mehr Filter", icon=":material/tune:"):
            c3, c4 = st.columns(2)
            c3.select_slider("Veröffentlicht in den letzten …", TAGE, key="sfs_tage",
                             format_func=lambda t: f"{t} Tagen")
            c4.number_input("Höchstens Treffer je Jobtitel und Jobbörse", 5, 300, step=5, key="sfs_max")
            st.multiselect("Jobbörsen", ui.quellen_optionen(), key="sfs_quellen", format_func=ui.quellen_label)
            st.checkbox("Nur Vollzeit", key="sfs_vollzeit")
            st.checkbox("Diese Einstellungen auch für die Automatik übernehmen", key="sfs_profil_merken",
                        help="Speichert Jobtitel, Ort und Filter in deinen Einstellungen.")
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
            st.button("Ideen von der KI", on_click=ki_vorschlaege_holen, icon=":material/auto_awesome:",
                      disabled=not llm_modul.client_verfuegbar(),
                      help="Ähnliche Jobtitel, Nachbarrollen und englische Titel aus deinem Lebenslauf"
                      if llm_modul.client_verfuegbar() else "Dafür brauche ich eine KI (Einstellungen → Konten).")
            if st.button("Suchen & bewerten", type="primary", icon=":material/search:"):
                suche_starten()
        if s.get("sfs_ki_fehler"):
            st.warning(f"KI-Ideen gerade nicht verfügbar: {s['sfs_ki_fehler']}")

    for i, tipp in enumerate(tipps):
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.markdown(f":material/lightbulb: {tipp.text}", width="stretch")
            if tipp.aenderung:
                st.button("Übernehmen", key=f"sfs_tipp_{i}", on_click=tipp_uebernehmen, args=(tipp.aenderung,))
    if statistik:
        with st.expander("Was bringen deine Jobtitel?", icon=":material/insights:"):
            st.dataframe(
                [{"Jobtitel": z["begriff"], "Treffer": z["treffer"], "passend": z["passend"],
                  "beworben": z["beworben"], "zuletzt": z["letzte_treffer"]} for z in statistik],
                hide_index=True, width="stretch",
            )


# --------------------------------------------------------------------------- #
# 2 · Auswählen
# --------------------------------------------------------------------------- #

def nicht_interessant(ref: str) -> None:
    with ui.oeffne_db() as db_:
        ablauf.ablehnen(db_, profil, ref, grund="nicht interessant")


def alle_markieren(refs: list[str], wert: bool) -> None:
    for ref in refs:
        st.session_state[f"sfs_sel_{ref}"] = wert


def vorbereiten_starten(refs: list[str], probelauf: bool) -> None:
    with ui.oeffne_db() as db_:
        for ref in refs:
            db_.setze_vorgang(Vorgang(ref=ref, status="vorbereitung", herkunft="manuell"))
    argumente = ["vorbereiten", *refs, "--ausloeser", "knopf"]
    if not probelauf:
        argumente.append("--ohne-probelauf")
    ui.starte_hintergrund(*argumente, meldung=f"{len(refs)} Bewerbung(en) werden vorbereitet …")
    for ref in refs:
        st.session_state.pop(f"sfs_sel_{ref}", None)
    st.session_state["sfs_zu_heute"] = True


def jetzt_bewerten(refs: list[str]) -> None:
    with ui.oeffne_db() as db_:
        ablauf.bewerte_neue(db_, profil, refs=refs, mit_llm=llm_modul.client_verfuegbar())


if schritt == 2:
    if s.get("sfs_meldung"):
        st.success(s["sfs_meldung"], icon=":material/check_circle:")
    st.caption("Hake an, wofür ich ein Anschreiben schreiben soll. Abgeschickt wird erst nach deinem Ja auf „Heute“.")
    f1, f2 = st.columns([2, 3], vertical_alignment="bottom")
    s.setdefault("sfs_umfang", "letzte" if letzte_suche else "alle")
    f1.segmented_control("Stellen aus", ["letzte", "alle"], key="sfs_umfang",
                         format_func={"letzte": "letzter Suche", "alle": "allen Suchen"}.get)
    f2.slider("Mindest-Passung", 0, 100, step=5, key="sfs_min")

    with ui.oeffne_db() as db:
        refs = letzte_suche["refs"] if s.get("sfs_umfang") == "letzte" and letzte_suche else None
        kandidaten = db.kandidaten(min_score=s["sfs_min"], limit=100, refs=refs,
                                   ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)
        unbewertet = [r for r in (refs or []) if db.score(r) is None]
        if unbewertet:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.info(f"{len(unbewertet)} Stellen der letzten Suche sind noch nicht bewertet.")
                st.button("Jetzt bewerten", on_click=jetzt_bewerten, args=(unbewertet,), icon=":material/grading:")
        if not kandidaten:
            schwaechere = db.kandidaten(min_score=0, limit=200, refs=refs,
                                        ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)
            st.info(f"Keine offenen Stellen mit mindestens {s['sfs_min']} Punkten Passung.")
            with st.container(horizontal=True):
                if schwaechere:
                    # So weit senken, dass etwa zehn Stellen zur Auswahl stehen
                    zehnte = schwaechere[min(9, len(schwaechere) - 1)][1].gesamt
                    neu_min = max(0, int(zehnte // 5) * 5)
                    anzahl_neu = sum(1 for _, sc in schwaechere if sc.gesamt >= neu_min)
                    st.button(f"Auf {neu_min} senken ({anzahl_neu} Stellen)",
                              on_click=lambda: st.session_state.update(sfs_min=neu_min))
                st.button("Neue Suche", on_click=gehe_zu, args=(1,), icon=":material/search:")
        else:
            alle_refs = [j.ref for j, _ in kandidaten]
            with st.container(horizontal=True):
                st.button("Alle auswählen", on_click=alle_markieren, args=(alle_refs, True), icon=":material/done_all:")
                st.button("Keine", on_click=alle_markieren, args=(alle_refs, False), icon=":material/remove_done:")
            for job, score in kandidaten:
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

            gewaehlt = [r for r in alle_refs if s.get(f"sfs_sel_{r}")]
            with st.container(border=True):
                probelauf = st.checkbox(
                    "Online-Formulare vorab testweise ausfüllen",
                    value=profil.automatisierung.probelauf_vorab and ablauf.browser_verfuegbar(),
                    disabled=not ablauf.browser_verfuegbar(),
                    help="Dauert pro Stelle 1–3 Minuten. Du siehst dann vor dem Ja, ob das Formular klappt.",
                )
                st.button(
                    f"Weiter: {len(gewaehlt)} Bewerbung(en) vorbereiten", type="primary", width="stretch",
                    disabled=not gewaehlt, on_click=vorbereiten_starten, args=(gewaehlt, probelauf),
                    icon=":material/arrow_forward:", icon_position="right",
                )
