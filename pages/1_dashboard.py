"""Dashboard - Übersicht über Jobs, Bewertungen und Bewerbungen."""

import streamlit as st
from pathlib import Path
import sys

# Import bewerbungsagent modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.db import Speicher
from bewerbungsagent.config import lade_profil

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")

st.title("📊 Dashboard")

# Datenbank-Pfad
db_path = Path.home() / ".bewerbungsagent" / "jobs.db"

try:
    with Speicher(str(db_path)) as db:
        # Statistiken abrufen
        alle_jobs = db.jobs(limit=10000)
        jobs_mit_score = [j for j in alle_jobs if db.score(j.ref) is not None]

        # Metrics in Spalten
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Gefundene Jobs", len(alle_jobs))

        with col2:
            st.metric("Bewertete Jobs", len(jobs_mit_score))

        with col3:
            # Count applications
            alle_bewerbungen = []
            for job in alle_jobs:
                bewerbungen = db.bewerbungen(job.ref)
                alle_bewerbungen.extend(bewerbungen)
            st.metric("Bewerbungen", len(alle_bewerbungen))

        with col4:
            # Erfolgreiche Bewerbungen (abgeschickt)
            erfolgreiche = sum(1 for b in alle_bewerbungen if b["status"] == "abgeschickt")
            st.metric("Abgeschickt", erfolgreiche)

        st.markdown("---")

        # Letzte Aktivitäten
        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("📝 Neueste Jobs")
            neueste = sorted(alle_jobs, key=lambda j: j.geholt_am, reverse=True)[:5]
            if neueste:
                for job in neueste:
                    score = db.score(job.ref)
                    score_text = f"Score: {score.gesamt:.0f}" if score else "Nicht bewertet"

                    with st.expander(f"**{job.titel}** - {job.arbeitgeber}"):
                        st.write(f"📍 {job.ort or 'Unbekannt'}")
                        st.write(f"📅 {job.veroeffentlicht or 'Unbekannt'}")
                        st.write(f"⭐ {score_text}")
                        st.write(f"🔗 Ref: `{job.ref}`")
            else:
                st.info("Noch keine Jobs gefunden. Starte eine Suche!")

        with col_right:
            st.subheader("⭐ Top bewertete Jobs")
            if jobs_mit_score:
                # Lade Profil für min_score
                try:
                    profil = lade_profil()
                    min_score = profil.bewertung.min_score
                except:
                    min_score = 70

                top_treffer = db.bestenliste(min_score=min_score, limit=5, offen=True)

                if top_treffer:
                    for job, score in top_treffer:
                        with st.expander(f"**{score.gesamt:.0f}** - {job.titel}"):
                            st.write(f"🏢 {job.arbeitgeber}")
                            st.write(f"📍 {job.ort or 'Unbekannt'}")
                            st.write(f"✅ Passung: {score.passung:.0f} | Ton: {score.sentiment:.0f}")
                            if score.begruendung:
                                st.write(f"💡 {score.begruendung}")
                            st.write(f"🔗 Ref: `{job.ref}`")
                else:
                    st.info(f"Keine Jobs über dem Schwellenwert ({min_score})")
            else:
                st.info("Noch keine bewerteten Jobs. Führe erst eine Bewertung durch!")

        st.markdown("---")

        # Quick Actions
        st.subheader("🚀 Schnellaktionen")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            if st.button("🔍 Neue Jobsuche", use_container_width=True):
                st.switch_page("pages/2_jobsuche.py")

        with col2:
            if st.button("📊 Jobs bewerten", use_container_width=True):
                st.switch_page("pages/3_bewertung.py")

        with col3:
            if st.button("⭐ Top Jobs ansehen", use_container_width=True):
                st.switch_page("pages/4_top_jobs.py")

        with col4:
            if st.button("✉️ Bewerbungen", use_container_width=True):
                st.switch_page("pages/5_bewerbungen.py")

except Exception as e:
    st.error(f"Fehler beim Laden der Datenbank: {e}")
    st.info("Möglicherweise existiert die Datenbank noch nicht. Führe zuerst eine Jobsuche durch!")
