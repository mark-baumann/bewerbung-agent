"""Bewerbungen: Überblick, Rückmeldungen eintragen, nachfassen und Nachweis fürs Amt."""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from bewerbungsagent import ablauf, dokumente, ui, zeitplan
from bewerbungsagent.models import RUECKMELDUNGEN

st.title("📬 Meine Bewerbungen")

profil = ui.profil_laden()
if profil is None:
    st.warning("Bitte zuerst das Profil anlegen.")
    st.page_link("seiten/profil.py", label="Profil einrichten", icon="👤")
    st.stop()

NACHFASSEN_TAGE = 14
LABEL_ZU_RUECKMELDUNG = {ui.RUECKMELDUNG_TEXT[r]: r for r in RUECKMELDUNGEN}


def rueckmeldung_setzen(ref: str, wert: str) -> None:
    with ui.oeffne_db() as db_:
        ablauf.setze_rueckmeldung(db_, ref, wert)
    st.toast("Rückmeldung gespeichert.", icon="💾")


def tabelle_speichern() -> None:
    """Aenderungen aus der Tabelle speichern; Zeilen ueber die beim Rendern gemerkte Reihenfolge zuordnen."""
    zustand = st.session_state.get(f"bw_tabelle_{st.session_state['bw_version']}") or {}
    refs = st.session_state.get("bw_refs", [])
    with ui.oeffne_db() as db_:
        for zeile, felder in (zustand.get("edited_rows") or {}).items():
            if int(zeile) >= len(refs):
                continue
            ref = refs[int(zeile)]
            update = {}
            if "Rückmeldung" in felder:
                wert = LABEL_ZU_RUECKMELDUNG.get(felder["Rückmeldung"] or "", "")
                update.update(rueckmeldung=wert, rueckmeldung_am=ablauf._jetzt() if wert else None)
            if "Notiz" in felder:
                update["notiz"] = felder["Notiz"] or None
            if update:
                db_.aktualisiere_vorgang(ref, **update)
    # Neuer Schluessel = frischer Editor auf dem gespeicherten Stand
    st.session_state["bw_version"] += 1
    st.toast("Gespeichert.", icon="💾")


def zuruecksetzen(ref: str) -> None:
    with ui.oeffne_db() as db_:
        ablauf.zuruecksetzen(db_, ref)
    st.toast("Zurückgesetzt – die Stelle erscheint wieder in der Auswahl.", icon="↩️")


uebersicht, nachfassen, nachweis = st.tabs(["📋 Übersicht", "🔔 Nachfassen", "📄 Nachweis fürs Amt"])

with ui.oeffne_db() as db:
    alle = db.vorgaenge()
    jobs = {v.ref: db.job(v.ref) for v in alle}
    alle = [v for v in alle if jobs.get(v.ref)]

    # ------------------------------------------------------------------ Übersicht
    with uebersicht:
        beworben = [v for v in alle if v.status in ("abgeschickt", "selbst_beworben")]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Beworben insgesamt", len(beworben))
        k2.metric("Diese Woche", db.beworben_seit(ablauf.wochenbeginn_utc()))
        k3.metric("Rückmeldungen", sum(1 for v in beworben if v.rueckmeldung))
        k4.metric("Einladungen", sum(1 for v in beworben if v.rueckmeldung in ("einladung", "zusage")))

        status_auswahl = st.pills(
            "Status", list(ui.STATUS_TEXT), selection_mode="multi",
            default=["wartet", "freigegeben", "wird_gesendet", "abgeschickt", "fehlgeschlagen", "selbst_beworben"],
            format_func=ui.STATUS_TEXT.get, key="bw_status",
        )
        # Stabile Reihenfolge (nach Anlage), damit Bearbeitungen nicht verrutschen
        sichtbar = sorted((v for v in alle if v.status in (status_auswahl or [])),
                          key=lambda v: v.erstellt_am, reverse=True)
        if not sichtbar:
            st.info("Noch keine Bewerbungen in dieser Auswahl. Starte im Cockpit oder unter „Schritt für Schritt“.")
        else:
            tabelle = pd.DataFrame(
                [{
                    "Datum": ui.zeit(v.gesendet_am or v.aktualisiert_am),
                    "Stelle": jobs[v.ref].titel,
                    "Arbeitgeber": jobs[v.ref].arbeitgeber,
                    "Status": ui.STATUS_TEXT.get(v.status, v.status),
                    "Rückmeldung": ui.RUECKMELDUNG_TEXT.get(v.rueckmeldung or "", ""),
                    "Notiz": v.notiz or "",
                    "Weg": ui.KANAL_TEXT.get(v.kanal, v.kanal),
                    "Ort": jobs[v.ref].ort or "",
                    "Anzeige": jobs[v.ref].anzeige_url or "",
                } for v in sichtbar],
                index=[v.ref for v in sichtbar],
            )
            st.session_state.setdefault("bw_version", 0)
            st.session_state["bw_refs"] = list(tabelle.index)
            st.caption("Rückmeldung und Notiz kannst du direkt in der Tabelle eintragen.")
            st.data_editor(
                tabelle, hide_index=True, width="stretch", key=f"bw_tabelle_{st.session_state['bw_version']}",
                on_change=tabelle_speichern,
                disabled=["Datum", "Status", "Stelle", "Arbeitgeber", "Ort", "Weg", "Anzeige"],
                column_config={
                    "Stelle": st.column_config.TextColumn(width="medium"),
                    "Arbeitgeber": st.column_config.TextColumn(width="small"),
                    "Rückmeldung": st.column_config.SelectboxColumn(options=list(LABEL_ZU_RUECKMELDUNG), width="medium"),
                    "Anzeige": st.column_config.LinkColumn(display_text="öffnen"),
                    "Notiz": st.column_config.TextColumn(help="z. B. Ansprechpartner, Gesprächstermin"),
                },
            )

            st.markdown("#### Details")
            auswahl = st.selectbox(
                "Bewerbung", [v.ref for v in sichtbar], key="bw_detail",
                format_func=lambda r: f"{jobs[r].titel} – {jobs[r].arbeitgeber}",
            )
            v = next((x for x in sichtbar if x.ref == auswahl), None)
            if v:
                job = jobs[v.ref]
                with st.container(border=True):
                    ui.job_kopf(job, db.score(v.ref))
                    st.markdown(f"**Status:** {ui.STATUS_TEXT.get(v.status, v.status)} · "
                                f"**Weg:** {ui.KANAL_TEXT.get(v.kanal, v.kanal)}"
                                + (f" an `{v.empfaenger}`" if v.kanal == "email" and v.empfaenger else ""))
                    if v.hinweis:
                        st.caption(v.hinweis)
                    if v.anschreiben:
                        with st.expander("Anschreiben"):
                            st.code(v.anschreiben, language=None, wrap_lines=True)
                    versuche = db.bewerbungen(v.ref)
                    if versuche:
                        with st.expander(f"Protokoll ({len(versuche)} Einträge)"):
                            for b in versuche:
                                st.markdown(f"- {ui.zeit(b['zeitpunkt'])} · **{b['status']}** · {b['ergebnis'] or ''}")
                    if v.status in ("abgelehnt", "fehlgeschlagen", "wartet"):
                        st.button("↩️ Zurücksetzen", key=f"bw_reset_{v.ref}", on_click=zuruecksetzen, args=(v.ref,),
                                  help="Vorgang verwerfen – die Stelle kann neu ausgewählt und vorbereitet werden.")

    # ------------------------------------------------------------------ Nachfassen
    with nachfassen:
        grenze = (datetime.now(timezone.utc) - timedelta(days=NACHFASSEN_TAGE)).isoformat(timespec="seconds")
        faellig = [v for v in alle if v.status in ("abgeschickt", "selbst_beworben")
                   and not v.rueckmeldung and (v.gesendet_am or "") < grenze]
        st.caption(f"Bewerbungen ohne Rückmeldung nach {NACHFASSEN_TAGE} Tagen. Eine freundliche Nachfrage "
                   "zeigt Interesse und bringt oft Bewegung in den Prozess.")
        if not faellig:
            st.success("Gerade ist kein Nachfassen fällig.")
        for v in faellig:
            job = jobs[v.ref]
            gesendet = zeitplan.als_datetime(v.gesendet_am)
            tage = (datetime.now(timezone.utc) - gesendet).days if gesendet else NACHFASSEN_TAGE
            with st.container(border=True):
                st.markdown(f"**{job.titel}** – {job.arbeitgeber} · beworben vor {tage} Tagen "
                            f"({ui.KANAL_TEXT.get(v.kanal, v.kanal)})")
                text = (
                    f"Sehr geehrte Damen und Herren,\n\nam {gesendet.astimezone(zeitplan.ZEITZONE):%d.%m.%Y} "
                    f"habe ich mich bei Ihnen als {job.titel} beworben. Ich interessiere mich weiterhin sehr "
                    "für die Stelle und wollte mich erkundigen, wie der aktuelle Stand ist.\n\n"
                    f"Mit freundlichen Grüßen\n{profil.person.name}"
                ) if gesendet else ""
                with st.expander("✉️ Vorschlag für eine Nachfrage"):
                    st.code(text, language=None, wrap_lines=True)
                    if v.empfaenger:
                        from urllib.parse import quote

                        st.link_button("Im Mailprogramm öffnen",
                                       f"mailto:{v.empfaenger}?subject={quote('Nachfrage: ' + (v.betreff or job.titel))}"
                                       f"&body={quote(text)}")
                c1, c2, c3 = st.columns(3)
                c1.button("📨 Eingang bestätigt", key=f"nf_e_{v.ref}", on_click=rueckmeldung_setzen,
                          args=(v.ref, "eingangsbestaetigung"), width="stretch")
                c2.button("🎉 Einladung", key=f"nf_i_{v.ref}", on_click=rueckmeldung_setzen,
                          args=(v.ref, "einladung"), width="stretch")
                c3.button("👎 Absage", key=f"nf_a_{v.ref}", on_click=rueckmeldung_setzen,
                          args=(v.ref, "absage"), width="stretch")

    # ------------------------------------------------------------------ Nachweis
    with nachweis:
        st.caption("Liste deiner Bewerbungen als Nachweis der Eigenbemühungen für Agentur für Arbeit "
                   "oder Jobcenter.")
        heute = date.today()
        zeitraum = st.date_input("Zeitraum", value=(heute.replace(day=1), heute), format="DD.MM.YYYY",
                                 key="bw_zeitraum")
        if isinstance(zeitraum, (tuple, list)) and len(zeitraum) == 2:
            von, bis = zeitraum
            zeilen = dokumente.nachweis_zeilen(db, von, bis)
            st.markdown(f"**{len(zeilen)} Bewerbung(en)** vom {von:%d.%m.%Y} bis {bis:%d.%m.%Y}")
            if zeilen:
                st.dataframe(
                    [{"Datum": z["datum"].strftime("%d.%m.%Y"), "Arbeitgeber": z["arbeitgeber"],
                      "Stelle": z["stelle"], "Ort": z["ort"], "Weg": z["weg"], "Stand": z["stand"]} for z in zeilen],
                    hide_index=True, width="stretch",
                )
                d1, d2 = st.columns(2)
                d1.download_button("📄 Als PDF herunterladen", dokumente.nachweis_pdf(zeilen, profil, von, bis),
                                   file_name=f"Eigenbemuehungen_{von}_{bis}.pdf", mime="application/pdf",
                                   width="stretch", type="primary")
                d2.download_button("📊 Als CSV (Excel)", dokumente.nachweis_csv(zeilen).encode("utf-8-sig"),
                                   file_name=f"Eigenbemuehungen_{von}_{bis}.csv", mime="text/csv", width="stretch")
