"""Jobs: alle Stellen, die ich für dich gefunden habe – mit Passung und Stand. Auswählen und vorbereiten lassen."""

from collections import Counter

import streamlit as st

from bewerbungsagent import ui, zeitplan

STAND_TEXT = {"offen": "offen", "heute": "auf „Heute“", "unterwegs": "in Arbeit", "beworben": "beworben",
              "abgelehnt": "Nein gesagt", "ausgeschlossen": "passt nicht", "unbewertet": "noch nicht geprüft"}
FILTER = {"alle": "Alle", "offen": "Offen", "heute": "Auf „Heute“", "beworben": "Beworben", "abgelehnt": "Nein gesagt",
          "ausgeschlossen": "Passt nicht"}
# Dafuer kann ich noch eine Bewerbung vorbereiten
VORBEREITBAR = ("offen", "unbewertet", "ausgeschlossen")
MAX_ZEILEN = 500

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
if s.pop("jobs_zu_heute", False):  # nach „vorbereiten“ wird auf „Heute“ entschieden
    st.switch_page(ui.SEITE_HEUTE)

st.title("Gefundene Jobs")
with ui.oeffne_db() as db:
    jobs = db.gefundene_jobs()

if not jobs:
    st.info("Noch keine Stellen gefunden – starte eine Suche, dann siehst du hier alles, was ich finde.",
            icon=":material/travel_explore:")
    with st.container(horizontal=True):
        st.page_link(ui.SEITE_SUCHEN, label="Suchen", icon=":material/search:")
        st.page_link(ui.SEITE_LEBENSLAUF, label="Mit dem Lebenslauf suchen", icon=":material/description:")
    st.stop()

zahlen = Counter(j["stand"] for j in jobs)
st.caption(f"{len(jobs)} Stellen aus deinen Suchen · {zahlen['offen']} offen · {zahlen['heute']} auf „Heute“ · "
           f"{zahlen['beworben']} beworben")

# --------------------------------------------------------------------------- #
# Filtern
# --------------------------------------------------------------------------- #

c1, c2 = st.columns([3, 2], vertical_alignment="center")
text = c1.text_input("Suchen", placeholder="Titel, Arbeitgeber oder Ort", key="jobs_text",
                     label_visibility="collapsed", icon=":material/search:")
sortierung = c2.segmented_control("Sortierung", ["neu", "passung"], default="neu", key="jobs_sort",
                                  format_func={"neu": "Neueste", "passung": "Beste Passung"}.get,
                                  label_visibility="collapsed")
stand = st.pills("Stand", list(FILTER), default="alle", key="jobs_stand", label_visibility="collapsed",
                 format_func=lambda k: FILTER[k] + (f" ({zahlen[k]})" if k != "alle" and zahlen[k] else ""))

auswahl = jobs
if stand and stand != "alle":
    auswahl = [j for j in auswahl if j["stand"] == stand]
if text.strip():
    gesucht = text.strip().casefold()
    auswahl = [j for j in auswahl if any(gesucht in (j[f] or "").casefold() for f in ("titel", "arbeitgeber", "ort"))]
if sortierung == "passung":
    auswahl = sorted(auswahl, key=lambda j: j["gesamt"] if j["gesamt"] is not None else -1, reverse=True)
if len(auswahl) > MAX_ZEILEN:
    st.caption(f"Die ersten {MAX_ZEILEN} von {len(auswahl)} – grenze mit der Suche oben weiter ein.")
    auswahl = auswahl[:MAX_ZEILEN]

# --------------------------------------------------------------------------- #
# Tabelle
# --------------------------------------------------------------------------- #


def _datum(iso: str | None) -> str:
    zeit = zeitplan.als_datetime(iso)
    return zeit.astimezone(zeitplan.ZEITZONE).strftime("%d.%m.%Y") if zeit else ""


def _ort(job: dict) -> str:
    ort = job["ort"] or ""
    return f"{ort} ({job['entfernung_km']:.0f} km)" if ort and job["entfernung_km"] is not None else ort


if not auswahl:
    st.caption("Keine Stelle passt zu diesem Filter.")
    st.stop()

zeilen = [{
    "Passung": round(j["gesamt"]) if j["gesamt"] is not None else None,
    "Stelle": j["titel"],
    "Arbeitgeber": j["arbeitgeber"] or "",
    "Ort": _ort(j),
    "Stand": STAND_TEXT.get(j["stand"], j["stand"]),
    "Jobbörse": ui.quellen_label(j["quelle"] or ""),
    "Gefunden": _datum(j["geholt_am"]),
    "Anzeige": j["anzeige"],
} for j in auswahl]
ereignis = st.dataframe(
    zeilen, hide_index=True, width="stretch", height=min(38 * len(zeilen) + 40, 600), key="jobs_tabelle",
    on_select="rerun", selection_mode="multi-row",
    column_config={
        "Passung": st.column_config.ProgressColumn("Passung", min_value=0, max_value=100, format="%d",
                                                   help="Wie gut die Stelle zu deinem Lebenslauf passt (0–100)"),
        "Stelle": st.column_config.TextColumn("Stelle", width="large"),
        "Anzeige": st.column_config.LinkColumn("Anzeige", display_text="öffnen"),
    },
)

gewaehlt = [auswahl[i] for i in (ereignis.selection.rows if ereignis else []) if i < len(auswahl)]
moeglich = [j for j in gewaehlt if j["stand"] in VORBEREITBAR]
if gewaehlt:
    if len(moeglich) < len(gewaehlt):
        st.caption(f"{len(gewaehlt) - len(moeglich)} der gewählten Stellen sind schon bearbeitet (Heute, beworben "
                   "oder Nein) – die lasse ich aus.")
    if st.button(f"Für {len(moeglich)} Stelle(n) Bewerbung vorbereiten", type="primary", width="stretch",
                 disabled=not moeglich, icon=":material/arrow_forward:", icon_position="right", key="jobs_vorbereiten"):
        ui.vorbereitung_starten(profil, [j["ref"] for j in moeglich])
        s["jobs_zu_heute"] = True
        st.rerun()
else:
    st.caption("Tippe links auf Zeilen, um Stellen auszuwählen – ich schreibe dann die Anschreiben, und du "
               "entscheidest auf „Heute“ mit Ja oder Nein.")
