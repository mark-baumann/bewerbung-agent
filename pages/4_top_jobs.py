"""Top Jobs - Bestenliste der am besten bewerteten Jobs."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import Speicher

st.set_page_config(page_title="Top Jobs", page_icon="⭐", layout="wide")

st.title("⭐ Top Jobs")

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"

try:
    profil = lade_profil()
    default_min_score = profil.bewertung.min_score
except:
    default_min_score = 70

st.markdown("""
Die am besten bewerteten Jobs basierend auf Skill-Matching und Sentiment-Analyse.
""")

# Filter
col1, col2, col3 = st.columns(3)

with col1:
    min_score = st.slider(
        "Minimaler Score",
        min_value=0,
        max_value=100,
        value=default_min_score,
        step=5
    )

with col2:
    limit = st.number_input(
        "Anzahl Jobs",
        min_value=1,
        max_value=100,
        value=20,
        step=5
    )

with col3:
    nur_offen = st.checkbox(
        "Nur offene (ohne Bewerbung)",
        value=True,
        help="Zeige nur Jobs für die noch keine Bewerbung vorliegt"
    )

st.markdown("---")

try:
    with Speicher(str(db_path)) as db:
        treffer = db.bestenliste(min_score=min_score, limit=limit, offen=nur_offen)

        if not treffer:
            st.warning(f"⚠️ Keine Jobs über Score {min_score} gefunden. Senke den Schwellenwert oder bewerte mehr Jobs.")
        else:
            st.success(f"✅ {len(treffer)} Jobs gefunden")

            # Detaillierte Job-Liste
            for idx, (job, score) in enumerate(treffer, 1):
                # Farbcodierung
                if score.gesamt >= 85:
                    icon = "🟢"
                    color_style = "background-color: #d4edda; padding: 1rem; border-radius: 0.5rem;"
                elif score.gesamt >= 70:
                    icon = "🟡"
                    color_style = "background-color: #fff3cd; padding: 1rem; border-radius: 0.5rem;"
                else:
                    icon = "🟠"
                    color_style = "background-color: #f8d7da; padding: 1rem; border-radius: 0.5rem;"

                with st.container():
                    st.markdown(f'<div style="{color_style}">', unsafe_allow_html=True)

                    col_title, col_score = st.columns([4, 1])

                    with col_title:
                        st.subheader(f"{icon} #{idx}. {job.titel}")
                        st.write(f"🏢 **{job.arbeitgeber}** | 📍 {job.ort or 'Unbekannt'}")

                    with col_score:
                        st.metric("Gesamt", f"{score.gesamt:.0f}")
                        st.caption(f"Passung: {score.passung:.0f}")
                        st.caption(f"Ton: {score.sentiment:.0f}")

                    # Details in Expander
                    with st.expander("📋 Details anzeigen"):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.markdown("### 📝 Job-Details")
                            st.write(f"**Veröffentlicht:** {job.veroeffentlicht or 'Unbekannt'}")
                            st.write(f"**Eintrittsdatum:** {job.eintrittsdatum or 'Nach Vereinbarung'}")
                            st.write(f"**Vertrag:** {job.befristung or 'Unbefristet'}")
                            st.write(f"**Homeoffice:** {'Ja' if job.homeoffice else 'Nein'}")
                            st.write(f"**Vergütung:** {job.verguetung or 'Nicht angegeben'}")

                            if job.entfernung_km is not None:
                                st.write(f"**Entfernung:** {job.entfernung_km:.1f} km")

                        with col2:
                            st.markdown("### ⭐ Bewertungs-Details")
                            st.write(f"**Bewerter:** {score.bewerter}")

                            if score.begruendung:
                                st.info(f"💡 **Begründung:** {score.begruendung}")

                            if score.treffer:
                                st.success(f"✅ **Skill-Treffer:** {', '.join(score.treffer)}")

                            if score.gruen:
                                st.success(f"🟢 **Positive Signale:** {', '.join(score.gruen)}")

                            if score.rot:
                                st.warning(f"🔴 **Warnsignale:** {', '.join(score.rot)}")

                        # Beschreibung
                        if job.beschreibung:
                            st.markdown("### 📄 Stellenbeschreibung")
                            st.text_area(
                                "Beschreibung",
                                value=job.beschreibung,
                                height=200,
                                disabled=True,
                                label_visibility="collapsed"
                            )

                        # Links
                        st.markdown("### 🔗 Links")
                        if job.bewerbungs_url:
                            st.markdown(f"[🌐 Bewerbungsseite öffnen]({job.bewerbungs_url})")

                        st.code(f"Referenz: {job.ref}", language=None)

                        # Bewerbungsaktionen
                        st.markdown("---")
                        col_a, col_b = st.columns(2)

                        with col_a:
                            if st.button(f"✉️ Anschreiben generieren", key=f"anschreiben_{job.ref}"):
                                st.session_state["selected_job_ref"] = job.ref
                                st.switch_page("pages/5_bewerbungen.py")

                        with col_b:
                            # Prüfe ob schon Bewerbung existiert
                            bewerbungen = db.bewerbungen(job.ref)
                            if bewerbungen:
                                st.caption(f"✅ {len(bewerbungen)} Bewerbung(en) vorhanden")
                            else:
                                if st.button(f"📤 Jetzt bewerben", key=f"bewerben_{job.ref}", type="primary"):
                                    st.session_state["selected_job_ref"] = job.ref
                                    st.switch_page("pages/5_bewerbungen.py")

                    st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown("---")

            # Zusammenfassung
            st.subheader("📊 Zusammenfassung")
            col1, col2, col3 = st.columns(3)

            avg_score = sum(s.gesamt for _, s in treffer) / len(treffer)
            avg_passung = sum(s.passung for _, s in treffer) / len(treffer)
            avg_sentiment = sum(s.sentiment for _, s in treffer) / len(treffer)

            with col1:
                st.metric("Ø Score", f"{avg_score:.1f}")
            with col2:
                st.metric("Ø Passung", f"{avg_passung:.1f}")
            with col3:
                st.metric("Ø Ton", f"{avg_sentiment:.1f}")

except Exception as e:
    st.error(f"Fehler: {e}")
    st.info("Möglicherweise existiert die Datenbank noch nicht. Führe zuerst eine Jobsuche und Bewertung durch!")
