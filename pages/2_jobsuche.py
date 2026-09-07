"""Jobsuche - Jobs von der Arbeitsagentur suchen."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.sources.arbeitsagentur import ArbeitsagenturClient
from bewerbungsagent.models import Job

st.set_page_config(page_title="Jobsuche", page_icon="🔍", layout="wide")

st.title("🔍 Jobsuche")

# Datenbank-Pfad
db_path = Path.home() / ".bewerbungsagent" / "jobs.db"

# Lade Profil für Defaults
try:
    profil = lade_profil()
    default_begriffe = profil.suche.was
    default_ort = profil.suche.wo or ""
    default_umkreis = profil.suche.umkreis
    default_tage = profil.suche.veroeffentlicht_seit_tagen
    default_max = profil.suche.max_pro_query
    nur_vollzeit_default = profil.suche.nur_vollzeit
except Exception as e:
    st.warning(f"Profil konnte nicht geladen werden: {e}")
    default_begriffe = ["Python Developer"]
    default_ort = ""
    default_umkreis = 50
    default_tage = 7
    default_max = 50
    nur_vollzeit_default = False

st.markdown("""
Suche nach Jobs in der [Jobbörse der Bundesagentur für Arbeit](https://www.arbeitsagentur.de/jobsuche/).
""")

# Suchformular
with st.form("search_form"):
    col1, col2 = st.columns(2)

    with col1:
        was_input = st.text_input(
            "Was (Suchbegriffe, kommagetrennt)",
            value=", ".join(default_begriffe),
            help="z.B. 'Python Developer, Data Engineer'"
        )

        umkreis = st.slider(
            "Umkreis (km)",
            min_value=0,
            max_value=200,
            value=default_umkreis,
            step=10,
            help="0 = bundesweit"
        )

    with col2:
        wo = st.text_input(
            "Wo (Ort)",
            value=default_ort,
            help="z.B. 'Hamburg', 'Berlin' - leer für ganz Deutschland"
        )

        tage = st.slider(
            "Veröffentlicht seit (Tagen)",
            min_value=1,
            max_value=30,
            value=default_tage,
            help="Nur Jobs der letzten X Tage"
        )

    col3, col4 = st.columns(2)

    with col3:
        max_treffer = st.number_input(
            "Max. Treffer pro Suchbegriff",
            min_value=1,
            max_value=500,
            value=default_max,
            step=10
        )

    with col4:
        nur_vollzeit = st.checkbox("Nur Vollzeit", value=nur_vollzeit_default)

    mit_details = st.checkbox("Mit Details laden (langsamer, aber vollständiger)", value=True)

    submitted = st.form_submit_button("🔍 Jobs suchen", use_container_width=True)

if submitted:
    # Parse Suchbegriffe
    begriffe = [b.strip() for b in was_input.split(",") if b.strip()]

    if not begriffe:
        st.error("Bitte mindestens einen Suchbegriff eingeben!")
    else:
        alle_jobs = {}
        progress_bar = st.progress(0)
        status_text = st.empty()

        with ArbeitsagenturClient() as client:
            for idx, begriff in enumerate(begriffe):
                status_text.write(f"🔍 Suche '{begriff}' in '{wo or 'ganz Deutschland'}' ...")

                try:
                    jobs = client.hole_jobs(
                        was=begriff,
                        wo=wo if wo else None,
                        umkreis=umkreis,
                        veroeffentlicht_seit_tagen=tage,
                        nur_vollzeit=nur_vollzeit,
                        max_treffer=max_treffer,
                        mit_details=mit_details,
                    )

                    status_text.write(f"✅ {len(jobs)} Treffer für '{begriff}'")

                    for job in jobs:
                        alle_jobs.setdefault(job.ref, job)

                except Exception as e:
                    st.error(f"❌ Fehler bei '{begriff}': {e}")

                progress_bar.progress((idx + 1) / len(begriffe))

        progress_bar.empty()
        status_text.empty()

        if alle_jobs:
            st.success(f"✅ {len(alle_jobs)} eindeutige Jobs gefunden!")

            # In Datenbank speichern
            with st.spinner("Speichere Jobs in Datenbank..."):
                with Speicher(str(db_path)) as db:
                    neu, aktualisiert = db.speichere_jobs(alle_jobs.values())

                st.info(f"💾 {neu} neue und {aktualisiert} aktualisierte Jobs gespeichert.")

            # Vorschau der Ergebnisse
            st.subheader("📋 Gefundene Jobs (Vorschau)")

            for idx, job in enumerate(list(alle_jobs.values())[:10]):
                with st.expander(f"**{job.titel}** - {job.arbeitgeber}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"📍 **Ort:** {job.ort or 'Unbekannt'}")
                        st.write(f"🏢 **Arbeitgeber:** {job.arbeitgeber}")
                        st.write(f"📅 **Veröffentlicht:** {job.veroeffentlicht or 'Unbekannt'}")
                    with col2:
                        st.write(f"💼 **Vertrag:** {job.befristung or 'Unbefristet'}")
                        st.write(f"🏠 **Homeoffice:** {'Ja' if job.homeoffice else 'Nein'}")
                        st.write(f"💰 **Vergütung:** {job.verguetung or 'Nicht angegeben'}")

                    if job.beschreibung:
                        st.markdown("**Beschreibung:**")
                        st.text(job.beschreibung[:300] + "..." if len(job.beschreibung) > 300 else job.beschreibung)

                    st.code(f"Referenz: {job.ref}", language=None)

            if len(alle_jobs) > 10:
                st.info(f"... und {len(alle_jobs) - 10} weitere Jobs. Bewerte die Jobs, um die besten Treffer zu sehen.")

            # Next steps
            st.markdown("---")
            st.subheader("✅ Nächste Schritte")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📊 Jobs jetzt bewerten", use_container_width=True):
                    st.switch_page("pages/3_bewertung.py")
            with col2:
                if st.button("📋 Zurück zum Dashboard", use_container_width=True):
                    st.switch_page("pages/1_dashboard.py")

        else:
            st.warning("⚠️ Keine Jobs gefunden. Versuche andere Suchbegriffe oder erweitere den Suchradius.")
