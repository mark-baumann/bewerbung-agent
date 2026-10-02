"""Startseite mit allen gespeicherten Stellenangeboten."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.db import Speicher

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
st.title("💼 Stellenangebote")

with Speicher(db_path) as db:
    jobs = db.jobs()
    job_scores = db.scores_by_ref()
if not jobs:
    st.info("Noch keine Stellen geladen. Starte eine Jobsuche, damit alle Treffer hier erscheinen.")
    if st.button("🔍 Jobsuche öffnen", type="primary"):
        st.switch_page("pages/2_jobsuche.py")
else:
    filter_col, page_col = st.columns([3, 1])
    query = filter_col.text_input(
        "Stellen durchsuchen",
        placeholder="Titel, Arbeitgeber oder Ort",
        key="dashboard_job_filter",
    ).casefold().strip()
    status_filter = page_col.selectbox(
        "Bewertungsstatus",
        ["Alle Stellen", "Unbewertet", "Bewertet"],
        key="dashboard_job_status",
    )
    visible_jobs = jobs
    if status_filter == "Unbewertet":
        visible_jobs = [job for job in visible_jobs if job_scores.get(job.ref) is None]
    elif status_filter == "Bewertet":
        visible_jobs = [job for job in visible_jobs if job_scores.get(job.ref) is not None]
    if query:
        visible_jobs = [
            job for job in visible_jobs
            if query in " ".join((job.titel, job.arbeitgeber, job.ort or "")).casefold()
        ]

    page_size = 10
    page_count = max(1, (len(visible_jobs) + page_size - 1) // page_size)
    if st.session_state.get("dashboard_job_page", 1) > page_count:
        st.session_state["dashboard_job_page"] = 1
    page = st.selectbox(
        f"{len(visible_jobs)} Stellen · Seite",
        range(1, page_count + 1),
        format_func=lambda number: f"{number} von {page_count}",
        key="dashboard_job_page",
    )
    for job in visible_jobs[(page - 1) * page_size : page * page_size]:
        score = job_scores.get(job.ref)
        title = f"{job.titel} · {job.arbeitgeber or 'Arbeitgeber unbekannt'}"
        is_open = st.session_state.get("dashboard_open_job") == job.ref
        with st.expander(title, expanded=is_open):
            st.caption(
                f"{job.quelle_icon} {job.quelle_label} · 📍 {job.ort or 'Ort nicht angegeben'} · "
                f"Veröffentlicht: {job.veroeffentlicht or 'unbekannt'}"
            )
            if score:
                st.metric("Passung", f"{score.gesamt:.0f}/100")
                st.write(score.begruendung)
            if job.beschreibung:
                st.markdown("### Stellenbeschreibung")
                st.write(job.beschreibung)
            else:
                st.info("Für diese Stelle liegt keine Beschreibung vor.")
            if score:
                if score.treffer:
                    st.success(f"Passende Kenntnisse: {', '.join(score.treffer)}")
                if score.rot:
                    st.warning(f"Hinweise: {', '.join(score.rot)}")

            action_col, link_col = st.columns([1, 2])
            with action_col:
                if st.button(
                    "✉️ Apply",
                    type="primary",
                    use_container_width=True,
                    key=f"apply_{job.ref}",
                ):
                    st.session_state["dashboard_open_job"] = job.ref
                    st.session_state["selected_job_ref"] = job.ref
                    st.switch_page("pages/5_bewerbungen.py")
            with link_col:
                if job.bewerbungs_url:
                    st.link_button(
                        "Originalanzeige öffnen",
                        job.bewerbungs_url,
                        use_container_width=True,
                        key=f"dashboard_job_link_{job.ref}",
                    )
