"""Heute: nur Ja/Nein – Bewerbungen und Ideen – und darunter deine Bewerbungen."""

from datetime import datetime, timezone

import streamlit as st

from bewerbungsagent import ablauf, ui, ui_assistent, zeitplan

profil, fehler = ui.profil_status()
if fehler:
    ui.profil_fehler(fehler)
    st.stop()
if ui_assistent.aktiv(profil):
    ui_assistent.zeige()
    st.stop()

stunde = datetime.now(timezone.utc).astimezone(zeitplan.ZEITZONE).hour
gruss = "Guten Morgen" if stunde < 11 else ("Guten Abend" if stunde >= 18 else "Hallo")
st.title(f"{gruss}, {profil.person.vorname}" if profil.person.vorname else gruss)

with ui.oeffne_db() as db:
    # Fehlerkarten frueherer Versionen gibt es nicht mehr: per E-Mail, erneut oder als Karte „Selbst bewerben“
    ablauf.fehlgeschlagene_umleiten(db, profil)
    wartend = db.vorgaenge("wartet")
    unterwegs = db.vorgaenge(("freigegeben", "wird_gesendet"))
    beworben = db.vorgaenge(("abgeschickt", "selbst_beworben"))
    vorschlaege = ui.offene_vorschlaege(db)

    if profil.lebenslauf_pfad() is None:
        # Das Einzige, was den Versand wirklich aufhaelt
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.markdown(":material/description: **Ohne Lebenslauf kann ich mich nicht bewerben.**")
            st.page_link(ui.SEITE_LEBENSLAUF, label="Hochladen", icon=":material/upload:")

    st.session_state["heute_stand"] = ui.stand(db)  # Stand dieses Seitenaufbaus

    @st.fragment(run_every=ui.auffrischen_alle(db))
    def laufstatus():
        with ui.oeffne_db() as frisch:
            ui.zeige_laufstatus(frisch)
            neu = ui.stand(frisch)
        # Etwas vorbereitet oder verschickt? Dann die ganze Seite neu – nie, solange ein Dialog offen ist
        if neu != st.session_state.get("heute_stand") and not ui.dialog_offen():
            st.rerun()

    laufstatus()

    # ------------------------------------------------------------------ Ja / Nein
    st.subheader(f":material/how_to_reg: Deine Entscheidung ({len(wartend)})")
    if not wartend:
        with st.container(border=True):
            st.markdown("Gerade nichts zu entscheiden – ich melde mich, sobald es passende Stellen gibt.")
            naechster = ablauf.naechster_automatik_termin(profil)
            if naechster and profil.automatisierung.effektiver_modus != "aus":
                st.caption(f"Ich suche wieder **{zeitplan.beschreibe(naechster)}**.")
    else:
        st.caption("**Ja** schickt die Bewerbung ab. **Nein** blendet die Stelle aus – mit Grund lerne ich dazu.")
        for v in reversed(wartend):  # aelteste zuerst
            ui.freigabe_karte(db, profil, v, praefix="cockpit")

    if vorschlaege:
        st.subheader(f":material/lightbulb: Ideen von mir ({len(vorschlaege)})")
        ui.suchbegriffe_karte([x for x in vorschlaege if x["art"] == "suchbegriff"], praefix="cockpit")
        for vorschlag in vorschlaege:
            if vorschlag["art"] != "suchbegriff":
                ui.vorschlag_karte(vorschlag, praefix="cockpit")

    # ------------------------------------------------------------------ Bisherige Bewerbungen
    st.subheader(":material/outbox: Deine Bewerbungen")
    if not (beworben or unterwegs):
        st.caption("Hier erscheinen deine Bewerbungen – mit den Antworten der Arbeitgeber.")
    for v in unterwegs:
        ui.unterwegs_zeile(db, v)
    # Einladungen und Zusagen zuerst
    beworben.sort(key=lambda v: v.rueckmeldung not in ("einladung", "zusage"))
    for v in beworben[:8]:
        ui.beworben_zeile(db, v, praefix="heute")
    if len(beworben) > 8:
        with st.expander(f"Ältere Bewerbungen ({len(beworben) - 8})", icon=":material/history:"):
            for v in beworben[8:]:
                ui.beworben_zeile(db, v, praefix="heute")
