"""Heute: was gerade ansteht – Entscheidungen, Hilfe, Antworten, Nachweis und Automatik."""

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

a = profil.automatisierung
jetzt = datetime.now(timezone.utc)
stunde = jetzt.astimezone(zeitplan.ZEITZONE).hour
gruss = "Guten Morgen" if stunde < 11 else ("Guten Abend" if stunde >= 18 else "Hallo")
st.title(f"{gruss}, {profil.person.vorname}" if profil.person.vorname else gruss)

AUTOPILOT_TEXT = {
    "mit_frist": "Sehr gut passende, geprüfte Bewerbungen gehen nach {stunden} h von selbst raus, wenn du nicht "
                 "widersprichst. Alles andere wartet auf dein Ja.",
    "sofort": "Sehr gut passende, geprüfte Bewerbungen schicke ich sofort ab. Alles andere wartet auf dein Ja.",
}


def jetzt_suchen() -> None:
    ui.starte_hintergrund("automatik", "--ausloeser", "knopf", meldung="Ich suche – das dauert ein paar Minuten.")


with ui.oeffne_db() as db:
    wartend = db.vorgaenge("wartet")
    fehlgeschlagen = db.vorgaenge("fehlgeschlagen")
    beworben = db.vorgaenge(("abgeschickt", "selbst_beworben"))
    diese_woche = db.beworben_seit(ablauf.wochenbeginn_utc(jetzt))
    einladungen = [v for v in beworben if v.rueckmeldung in ("einladung", "zusage")]
    vorschlaege = ui.offene_vorschlaege(db)
    letzte_automatik = db.letzte_laeufe("automatik", limit=1)
    laeuft = bool(db.laufende_laeufe(("automatik", "vorbereiten")))
    naechster = ablauf.naechster_automatik_termin(profil)

    # ------------------------------------------------------------------ Statuszeile
    teile = [f"**{len(wartend)}** warten auf dein Ja" if wartend else "Gerade wartet nichts auf dich",
             f"diese Woche **{diese_woche} von {a.ziel_pro_woche}** beworben"]
    if einladungen:
        teile.append(f"**{len(einladungen)}** Einladung(en)")
    if a.effektiver_autopilot != "aus":
        teile.append(":violet-badge[:material/smart_toy: Autopilot an]")
    st.markdown(" · ".join(teile))
    st.progress(min(1.0, diese_woche / max(1, a.ziel_pro_woche)))

    ui.zeige_einrichtung(profil)

    # ------------------------------------------------------------------ Einladungen zuerst
    for v in einladungen:
        if v.rueckmeldung_am and (jetzt - (zeitplan.als_datetime(v.rueckmeldung_am) or jetzt)).days <= 21:
            job = db.job(v.ref)
            if job:
                nachricht = next((n for n in db.nachrichten(v.ref, limit=5) if n.get("art") == v.rueckmeldung), None)
                st.success(
                    f"**{ui.RUECKMELDUNG_TEXT[v.rueckmeldung]}: {job.titel} – {job.arbeitgeber}**"
                    + (f"  \n„{nachricht['betreff']}“ von {nachricht['absender']}" if nachricht else ""),
                    icon=":material/celebration:",
                )

    # ------------------------------------------------------------------ Laufender Stand
    @st.fragment(run_every=5 if ui.laeuft_etwas(db) else None)
    def laufstatus():
        with ui.oeffne_db() as frisch:
            if not ui.zeige_laufstatus(frisch):
                # Fertig: Seite neu aufbauen – aber nie, solange ein Dialog offen ist (er wuerde sich schliessen)
                if st.session_state.get("heute_lief") and not ui.dialog_offen():
                    st.session_state["heute_lief"] = False
                    st.rerun()
            else:
                st.session_state["heute_lief"] = True

    laufstatus()

    # ------------------------------------------------------------------ Entscheidungen
    st.subheader(f":material/how_to_reg: Deine Entscheidung ({len(wartend)})")
    if not wartend:
        with st.container(border=True):
            st.markdown("Gerade wartet keine Bewerbung auf dich.")
            if naechster and a.effektiver_modus != "aus":
                st.caption(f"Ich suche wieder **{zeitplan.beschreibe(naechster)}** – oder jetzt sofort.")
            with st.container(horizontal=True):
                st.button("Jetzt suchen", type="primary", icon=":material/travel_explore:", disabled=laeuft,
                          on_click=jetzt_suchen, key="heute_suchen_leer")
                st.page_link(ui.SEITE_SUCHEN, label="Selbst suchen & auswählen", icon=":material/search:")
    else:
        st.caption("**Ja** schickt die Bewerbung ab. **Nein** blendet die Stelle aus – mit Grund lerne ich dazu.")
        for v in reversed(wartend):  # aelteste zuerst
            ui.freigabe_karte(db, profil, v, praefix="cockpit")

    # ------------------------------------------------------------------ Hilfe bei Fehlschlaegen
    if fehlgeschlagen:
        st.subheader(f":material/support: Braucht deine Hilfe ({len(fehlgeschlagen)})")
        st.caption("Diese Bewerbungen konnten nicht automatisch verschickt werden.")
        for v in fehlgeschlagen:
            ui.hilfe_karte(db, v, praefix="cockpit")

    # ------------------------------------------------------------------ Vorschlaege des Agenten
    if vorschlaege:
        st.subheader(f":material/lightbulb: Ideen von mir ({len(vorschlaege)})")
        st.caption("Gelernt aus deinen Entscheidungen. Nichts davon passiert ohne dein Ja.")
        ui.suchbegriffe_karte([x for x in vorschlaege if x["art"] == "suchbegriff"], praefix="cockpit")
        for vorschlag in vorschlaege:
            if vorschlag["art"] != "suchbegriff":
                ui.vorschlag_karte(vorschlag, praefix="cockpit")

    # ------------------------------------------------------------------ Bewerbungen & Nachweis
    st.subheader(":material/outbox: Deine Bewerbungen")
    if not beworben:
        st.caption("Hier erscheinen deine verschickten Bewerbungen – mit Antworten und dem Nachweis fürs Amt.")
    else:
        st.caption("Tippe an, was zurückkam. Antworten im Postfach erkenne ich selbst, wenn dein E-Mail-Konto "
                   "verbunden ist.")
        for v in beworben[:5]:
            ui.beworben_zeile(db, v, praefix="heute")
        if len(beworben) > 5:
            with st.expander(f"Ältere Bewerbungen ({len(beworben) - 5})", icon=":material/history:"):
                for v in beworben[5:]:
                    ui.beworben_zeile(db, v, praefix="heute")
    if st.button("Nachweis fürs Amt", icon=":material/description:", key="heute_nachweis",
                 help="Liste deiner Bewerbungen für Agentur für Arbeit oder Jobcenter (PDF oder Excel)."):
        ui.nachweis_dialog()

    # ------------------------------------------------------------------ Automatik
    with st.container(border=True):
        st.markdown("**:material/schedule: Automatik**")
        if a.effektiver_modus == "aus":
            st.markdown("Ist aus – ich suche nur, wenn du es sagst.")
        else:
            was = "suche und bereite vor" if a.effektiver_modus == "automatisch" else "suche"
            st.markdown(f"Ich {was} **{ui.zeitplan_text(a.cron)}**"
                        + (f" · nächstes Mal **{zeitplan.beschreibe(naechster)}**" if naechster else ""))
        if letzte_automatik:
            st.caption(f"Zuletzt: {ui.lauf_zusammenfassung(letzte_automatik[0])}")
        if a.effektiver_autopilot != "aus":
            st.caption(":material/smart_toy: **Autopilot:** "
                       + AUTOPILOT_TEXT[a.effektiver_autopilot].format(stunden=a.autopilot_frist_stunden))
        with st.container(horizontal=True, vertical_alignment="center"):
            st.page_link(ui.SEITE_EINSTELLUNGEN, label="Ändern", icon=":material/tune:",
                         query_params={"tab": "Automatik"})
            if wartend:
                st.button("Jetzt suchen", icon=":material/travel_explore:", disabled=laeuft, on_click=jetzt_suchen,
                          key="heute_suchen")
