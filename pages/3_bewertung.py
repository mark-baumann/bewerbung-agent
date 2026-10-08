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
- **LLM-Bewertung** (optional): Semantische Analyse durch Claude oder Ollama
""")

with Speicher(str(db_path)) as db:
    jobs_ohne_score = db.jobs(nur_ohne_score=True, limit=10000)
    alle_jobs = db.jobs(limit=10000)
    letzte_suche = db.letzte_suche()
    jobs_letzte_suche = db.jobs_nach_refs(letzte_suche["refs"]) if letzte_suche else []
    alle_scores = {job.ref: db.score(job.ref) for job in alle_jobs}

unbewertet_letzte_suche = [j for j in jobs_letzte_suche if alle_scores.get(j.ref) is None]

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Gesamt Jobs", len(alle_jobs))
with col2:
    st.metric("Unbewertete Jobs", len(jobs_ohne_score))
with col3:
    st.metric(
        "Letzte Suche",
        len(jobs_letzte_suche),
        delta=f"{len(unbewertet_letzte_suche)} unbewertet",
        delta_color="off",
    )

if not alle_jobs:
    st.info("Noch keine Jobs gefunden. Starte zuerst eine Jobsuche.")
    st.page_link("pages/2_jobsuche.py", label="🔍 Zur Jobsuche", icon="🔍")

st.markdown("---")

AUSWAHL_LETZTE = "Jobs der letzten Suche"
AUSWAHL_ALLE = "Alle gefundenen Jobs"

# Bewertungsformular
with st.form("bewertung_form"):
    st.subheader("⚙️ Bewertungsoptionen")

    col1, col2 = st.columns(2)

    with col1:
        auswahl = st.radio(
            "Welche Jobs bewerten?",
            [AUSWAHL_LETZTE, AUSWAHL_ALLE],
            index=0 if jobs_letzte_suche else 1,
            help="Bezieht sich auf die bereits gefundenen und gespeicherten Jobs",
        )

        nur_neue = st.checkbox(
            "Nur unbewertete Jobs",
            value=True,
            help="Jobs die bereits bewertet wurden überspringen"
        )

        limit = st.number_input(
            "Max. Anzahl zu bewerten",
            min_value=1,
            max_value=10000,
            value=max(len(jobs_letzte_suche) if jobs_letzte_suche else min(len(alle_jobs), 50), 1),
            step=10
        )

    with col2:
        mit_llm = st.checkbox(
            "LLM-Bewertung aktivieren (KI)",
            value=True,
            help="Semantische Bewertung durch Claude oder Ollama - benötigt ANTHROPIC_API_KEY oder OLLAMA_API_KEY"
        )

        if mit_llm:
            llm_verfuegbar = llm_modul.client_verfuegbar()
            if llm_verfuegbar:
                anbieter = llm_modul.provider()
                st.success(f"✅ KI verfügbar ({anbieter})")
            else:
                st.error("❌ Kein LLM-API-Key gesetzt (ANTHROPIC_API_KEY oder OLLAMA_API_KEY)")
                mit_llm = False

    submitted = st.form_submit_button("📊 Bewertung starten", use_container_width=True)

if submitted:
    if not profil:
        st.error("❌ Profil konnte nicht geladen werden. Bitte Profil unter 👤 Profil konfigurieren.")
    else:
        try:
            with Speicher(str(db_path)) as db:
                if auswahl == AUSWAHL_LETZTE:
                    kandidaten = db.jobs_nach_refs(letzte_suche["refs"]) if letzte_suche else []
                else:
                    kandidaten = db.jobs(limit=10000)
                if nur_neue:
                    kandidaten = [j for j in kandidaten if db.score(j.ref) is None]
                jobs = kandidaten[: int(limit)]

                if not jobs:
                    st.warning("⚠️ Keine (unbewerteten) Jobs in der Auswahl. Führe eine neue Jobsuche durch oder wähle 'Alle gefundenen Jobs'.")
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

                                if job.anzeige_url:
                                    st.markdown(f"[🔗 Zum Jobangebot öffnen]({job.anzeige_url})")
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
                                    if job.anzeige_url:
                                        st.markdown(f"[🔗 Zum Jobangebot öffnen]({job.anzeige_url})")

                    # Next steps
                    st.markdown("---")
                    st.subheader("✅ Nächste Schritte")
                    if st.button("📋 Zurück zum Dashboard", use_container_width=True):
                        st.switch_page("app.py")
        except Exception as e:
            st.error(f"Fehler: {e}")
            st.info("Möglicherweise existiert die Datenbank noch nicht. Führe zuerst eine Jobsuche durch!")

# Übersicht über alle bereits gefundenen Jobs inkl. gespeicherter Bewertung –
# bleibt nach Reload erhalten, weil alles aus der Datenbank kommt.
with Speicher(str(db_path)) as db:
    uebersicht_jobs = (
        db.jobs_nach_refs(letzte_suche["refs"]) if auswahl == AUSWAHL_LETZTE and letzte_suche
        else db.jobs(limit=10000)
    )
    uebersicht_scores = {job.ref: db.score(job.ref) for job in uebersicht_jobs}

if uebersicht_jobs:
    st.markdown("---")
    st.subheader(f"📋 Gefundene Jobs ({auswahl})")
    zeilen = []
    for job in uebersicht_jobs:
        sc = uebersicht_scores.get(job.ref)
        zeilen.append({
            "Score": None if sc is None or sc.ausgeschlossen else round(sc.gesamt),
            "Status": "unbewertet" if sc is None else ("ausgeschlossen" if sc.ausgeschlossen else "bewertet"),
            "Titel": job.titel,
            "Arbeitgeber": job.arbeitgeber,
            "Ort": job.ort or "",
            "Quelle": job.quelle or "",
            "Veröffentlicht": job.veroeffentlicht or "",
            "Link": job.anzeige_url or "",
        })
    zeilen.sort(key=lambda z: (z["Score"] is None, -(z["Score"] or 0)))
    st.dataframe(
        zeilen,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%d"),
            "Link": st.column_config.LinkColumn(display_text="öffnen"),
        },
    )
