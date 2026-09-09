"""Bewertung - Jobs mit Heuristik und/oder LLM bewerten."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import STANDARD_DB, Speicher
from bewerbungsagent.scoring import llm as llm_modul

st.set_page_config(page_title="Bewertung", page_icon="📊", layout="wide")

st.title("📊 Job-Bewertung")

# Datenbank-Pfad (identisch mit der CLI, damit Daten den Container-Neustart
# ueberleben, siehe AUG-378)
db_path = STANDARD_DB

try:
    profil = lade_profil()
    min_score = profil.bewertung.min_score
except Exception as e:
    st.warning(f"Profil konnte nicht geladen werden: {e}")
    profil = None
    min_score = 70

st.markdown("""
Bewerte gefundene Jobs anhand von:
- **Skill-Matching**: Abgleich mit deinem Profil
- **Sentiment-Analyse**: Analyse der Stellenanzeige auf positive/negative Signale
- **LLM-Bewertung** (optional): Semantische Analyse durch Claude
""")

with Speicher(str(db_path)) as db:
    jobs_ohne_score = db.jobs(nur_ohne_score=True, limit=10000)
    alle_jobs = db.jobs(limit=10000)

col1, col2 = st.columns(2)
with col1:
    st.metric("Gesamt Jobs", len(alle_jobs))
with col2:
    st.metric("Unbewertete Jobs", len(jobs_ohne_score))

st.markdown("---")

# Bewertungsformular
with st.form("bewertung_form"):
    st.subheader("⚙️ Bewertungsoptionen")

    col1, col2 = st.columns(2)

    with col1:
        nur_neue = st.checkbox(
            "Nur unbewertete Jobs",
            value=True,
            help="Jobs die bereits bewertet wurden überspringen"
        )

        limit = st.number_input(
            "Max. Anzahl zu bewerten",
            min_value=1,
            max_value=1000,
            value=max(min(len(jobs_ohne_score) if nur_neue else len(alle_jobs), 50), 1),
            step=10
        )

    with col2:
        mit_llm = st.checkbox(
            "LLM-Bewertung aktivieren (Claude)",
            value=True,
            help="Semantische Bewertung durch Claude - benötigt ANTHROPIC_API_KEY"
        )

        if mit_llm:
            llm_verfuegbar = llm_modul.client_verfuegbar()
            if llm_verfuegbar:
                st.success("✅ Claude API verfügbar")
            else:
                st.error("❌ ANTHROPIC_API_KEY nicht gesetzt")
                mit_llm = False

    submitted = st.form_submit_button("📊 Bewertung starten", use_container_width=True)

if submitted:
    if not profil:
        st.error("❌ Profil konnte nicht geladen werden. Bitte Profil unter 👤 Profil konfigurieren.")
    else:
        try:
            with Speicher(str(db_path)) as db:
                jobs = db.jobs(nur_ohne_score=nur_neue, limit=limit)

                if not jobs:
                    st.warning("⚠️ Keine Jobs zu bewerten. Führe zuerst eine Jobsuche durch.")
                else:
                    st.info(f"🔄 Bewerte {len(jobs)} Jobs ({'LLM + Heuristik' if mit_llm else 'nur Heuristik'})...")

                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    results_container = st.container()

                    scores = llm_modul.bewerte_jobs(jobs, profil, mit_llm=mit_llm)

                    ausgeschlossen = 0
                    ueber_schwelle = 0
                    top_jobs = []

                    for idx, score in enumerate(scores):
                        db.speichere_score(score)

                        if score.ausgeschlossen:
                            ausgeschlossen += 1
                        elif score.gesamt >= min_score:
                            ueber_schwelle += 1
                            # Hole Job-Details für Top-Liste
                            job = next((j for j in jobs if j.ref == score.ref), None)
                            if job:
                                top_jobs.append((job, score))

                        progress_bar.progress((idx + 1) / len(scores))
                        status_text.write(f"Bewertet: {idx + 1}/{len(scores)}")

                    progress_bar.empty()
                    status_text.empty()

                    st.success(f"✅ Bewertung abgeschlossen!")

                    # Statistiken
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Bewertet", len(scores))
                    with col2:
                        st.metric(f"Über Schwelle ({min_score})", ueber_schwelle)
                    with col3:
                        st.metric("Ausgeschlossen", ausgeschlossen)

                    # Top bewertete Jobs anzeigen
                    if top_jobs:
                        st.markdown("---")
                        st.subheader(f"⭐ Top {len(top_jobs)} Jobs über der Schwelle")

                        # Nach Score sortieren
                        top_jobs.sort(key=lambda x: x[1].gesamt, reverse=True)

                        for job, score in top_jobs[:10]:
                            score_color = "🟢" if score.gesamt >= 80 else "🟡"

                            with st.expander(f"{score_color} **{score.gesamt:.0f}** - {job.titel}"):
                                col1, col2 = st.columns([2, 1])

                                with col1:
                                    st.write(f"🏢 **{job.arbeitgeber}**")
                                    st.write(f"📍 {job.ort or 'Unbekannt'}")
                                    st.write(f"📅 {job.veroeffentlicht or 'Unbekannt'}")

                                    if score.begruendung:
                                        st.markdown(f"**Begründung:** {score.begruendung}")

                                    if score.treffer:
                                        st.success(f"✅ **Skills:** {', '.join(score.treffer[:8])}")

                                    if score.gruen:
                                        st.info(f"🟢 **Positiv:** {', '.join(score.gruen[:6])}")

                                    if score.rot:
                                        st.warning(f"🔴 **Warnsignale:** {', '.join(score.rot[:6])}")

                                with col2:
                                    st.metric("Gesamt", f"{score.gesamt:.0f}")
                                    st.metric("Passung", f"{score.passung:.0f}")
                                    st.metric("Ton", f"{score.sentiment:.0f}")
                                    st.caption(f"Bewerter: {score.bewerter}")

                                st.code(f"Referenz: {job.ref}", language=None)

                    # Ausgeschlossene Jobs (Beispiele)
                    if ausgeschlossen > 0:
                        st.markdown("---")
                        st.subheader(f"❌ Ausgeschlossene Jobs ({ausgeschlossen})")

                        ausgeschlossene = [s for s in scores if s.ausgeschlossen][:5]
                        for score in ausgeschlossene:
                            job = next((j for j in jobs if j.ref == score.ref), None)
                            if job:
                                with st.expander(f"🚫 {job.titel}"):
                                    st.write(f"**Grund:** {score.ausschlussgrund}")
                                    st.write(f"🏢 {job.arbeitgeber}")

                    # Next steps
                    st.markdown("---")
                    st.subheader("✅ Nächste Schritte")
                    if st.button("📋 Zurück zum Dashboard", use_container_width=True):
                        st.switch_page("app.py")
        except Exception as e:
            st.error(f"Fehler: {e}")
            st.info("Möglicherweise existiert die Datenbank noch nicht. Führe zuerst eine Jobsuche durch!")
