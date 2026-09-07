"""Streamlit UI für den Bewerbungsagenten."""

import streamlit as st
from pathlib import Path

# Seitenkonfiguration
st.set_page_config(
    page_title="Bewerbungsagent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

def main():
    st.title("🤖 Bewerbungsagent")

    st.markdown("""
    # Willkommen beim Bewerbungsagenten!

    Automatisierte Jobsuche und Bewerbung mit KI-Unterstützung.

    ## 📋 Workflow

    1. **🔍 Jobsuche** - Stellen von der Bundesagentur für Arbeit suchen
    2. **📊 Bewertung** - Jobs automatisch bewerten (Skill-Match + Sentiment)
    3. **✉️ Bewerbungen** - Anschreiben generieren und bewerben

    ## 🚀 Los geht's

    Wähle eine Seite aus der Navigation links oder nutze die Schnellzugriffe:
    """)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("📊 Dashboard", use_container_width=True, type="primary"):
            st.switch_page("pages/1_dashboard.py")

    with col2:
        if st.button("🔍 Jobsuche", use_container_width=True):
            st.switch_page("pages/2_jobsuche.py")

    with col3:
        if st.button("✉️ Bewerbungen", use_container_width=True):
            st.switch_page("pages/5_bewerbungen.py")

    with col4:
        if st.button("👤 Profil", use_container_width=True):
            st.switch_page("pages/6_profil.py")

    st.markdown("---")

    # Info-Boxen
    col1, col2 = st.columns(2)

    with col1:
        st.info("""
        ### ℹ️ Erste Schritte

        1. **Profil ausfüllen** unter 👤 Profil
        2. **Unterlagen hochladen** (Lebenslauf, Zeugnisse)
        3. **API-Key setzen** (`.env` Datei)
        4. **Jobsuche starten** und Jobs finden
        5. **Bewerten** und die besten Jobs ansehen
        """)

    with col2:
        st.success("""
        ### ✨ Features

        - 🔍 Echte Jobs von der Arbeitsagentur
        - 🤖 KI-gestützte Bewertung mit Claude
        - 📊 Skill-Matching und Sentiment-Analyse
        - ✉️ Automatische Anschreiben-Generierung
        - 🌐 Browser-Automation (CLI)
        - 💾 Persistent in SQLite
        """)

    st.markdown("---")

    # Stats (wenn DB existiert)
    db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
    if db_path.exists():
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from bewerbungsagent.db import Speicher

            with Speicher(str(db_path)) as db:
                alle_jobs = db.jobs(limit=10000)
                jobs_mit_score = [j for j in alle_jobs if db.score(j.ref) is not None]

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Gefundene Jobs", len(alle_jobs))
                with col2:
                    st.metric("Bewertete Jobs", len(jobs_mit_score))
                with col3:
                    alle_bewerbungen = []
                    for job in alle_jobs:
                        alle_bewerbungen.extend(db.bewerbungen(job.ref))
                    st.metric("Bewerbungen", len(alle_bewerbungen))

        except Exception as e:
            st.caption(f"Statistiken konnten nicht geladen werden: {e}")
    else:
        st.info("💡 **Tipp:** Starte mit einer Jobsuche, um loszulegen!")

    st.markdown("---")
    st.markdown("""
    **Bewerbungsagent** | [GitHub](https://github.com/mark-baumann/bewerbung-agent) | [Dokumentation](README.md) | [UI Anleitung](UI_README.md)

    Erstellt von **Mark Baumann**
    """)


if __name__ == "__main__":
    main()
