"""Geladene Stellen ansehen, bewerten und direkt ein Anschreiben vorbereiten."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.anschreiben import erzeuge as erzeuge_anschreiben
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.scoring import llm as llm_modul

st.title("📊 Stellen bewerten")
st.caption("Prüfe die geladenen Stellen, starte die Bewertung und erstelle danach mit einem Klick ein passendes Anschreiben.")

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
try:
    profil = lade_profil()
except (OSError, ValueError) as exc:
    st.error(f"Profil konnte nicht geladen werden: {exc}")
    st.page_link("pages/6_profil.py", label="Profil öffnen", icon="👤")
    st.stop()

with Speicher(db_path) as db:
    alle_jobs = db.jobs(limit=10000)
    jobs_ohne_score = db.jobs(nur_ohne_score=True, limit=10000)
    vorhandene_scores = {job.ref: db.score(job.ref) for job in alle_jobs}

col1, col2, col3 = st.columns(3)
col1.metric("Geladene Stellen", len(alle_jobs))
col2.metric("Noch unbewertet", len(jobs_ohne_score))
col3.metric("Bereits bewertet", len(alle_jobs) - len(jobs_ohne_score))

st.subheader("1 · Stellen prüfen")
if not alle_jobs:
    st.info("Hier erscheinen deine gespeicherten Treffer. Starte zuerst eine Jobsuche.")
else:
    filter_status = st.radio(
        "Treffer anzeigen",
        ["Alle", "Unbewertet", "Bewertet"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if filter_status == "Unbewertet":
        sichtbare_jobs = [job for job in alle_jobs if vorhandene_scores[job.ref] is None]
    elif filter_status == "Bewertet":
        sichtbare_jobs = [job for job in alle_jobs if vorhandene_scores[job.ref] is not None]
    else:
        sichtbare_jobs = alle_jobs

    page_size = 25
    page_count = max(1, (len(sichtbare_jobs) + page_size - 1) // page_size)
    page = st.selectbox(
        f"{len(sichtbare_jobs)} Stellen · Seite",
        options=range(1, page_count + 1),
        format_func=lambda value: f"{value} von {page_count}",
    )
    page_jobs = sichtbare_jobs[(page - 1) * page_size : page * page_size]

    st.dataframe(
        [
            {
                "Quelle": f"{job.quelle_icon} {job.quelle_label}",
                "Stelle": job.titel,
                "Arbeitgeber": job.arbeitgeber,
                "Ort": job.ort or "Nicht angegeben",
                "Veröffentlicht": job.veroeffentlicht or "Unbekannt",
                "Bewertung": (
                    f"{vorhandene_scores[job.ref].gesamt:.0f}/100"
                    if vorhandene_scores[job.ref]
                    else "Ausstehend"
                ),
            }
            for job in page_jobs
        ],
        use_container_width=True,
        hide_index=True,
    )
    for job in page_jobs:
        score = vorhandene_scores[job.ref]
        status = f"{score.gesamt:.0f}/100" if score else "noch unbewertet"
        with st.expander(f"{job.quelle_icon} {job.titel} · {job.arbeitgeber} · {status}"):
            st.caption(
                f"{job.quelle_label} · 📍 {job.ort or 'Ort unbekannt'} · "
                f"veröffentlicht {job.veroeffentlicht or 'unbekannt'}"
            )
            if job.beschreibung:
                st.markdown("**Stellenbeschreibung**")
                st.write(job.beschreibung)
            else:
                st.info("Für diese Stelle wurde kein Beschreibungstext mitgeliefert.")
            if score:
                st.progress(min(max(score.gesamt / 100, 0.0), 1.0), text=f"Passung {score.gesamt:.0f}/100")
                st.write(score.begruendung)
                if score.treffer:
                    st.success(f"Skills: {', '.join(score.treffer)}")
                if score.rot:
                    st.warning(f"Warnsignale: {', '.join(score.rot)}")
            if job.bewerbungs_url:
                st.link_button("Stelle bei der Quelle öffnen", job.bewerbungs_url)

st.markdown("---")
st.subheader("2 · Bewertung starten")
with st.form("bewertung_form"):
    col1, col2 = st.columns(2)
    with col1:
        nur_neue = st.checkbox("Nur unbewertete Stellen", value=True)
        max_jobs = max(1, len(alle_jobs))
        limit = st.number_input(
            "Anzahl Stellen",
            min_value=1,
            max_value=max_jobs,
            value=max(1, min(len(jobs_ohne_score), max_jobs)),
            step=min(25, max_jobs),
            help="Die Stellen werden anhand von Titel, Ort, Arbeitgeber und Beschreibung bewertet.",
        )
    with col2:
        llm_verfuegbar = llm_modul.client_verfuegbar()
        mit_llm = st.checkbox(
            "Mit Claude genauer bewerten",
            value=llm_verfuegbar,
            disabled=not llm_verfuegbar,
            help="Optional; ohne API-Key steht die Bewertung mit Skill-Abgleich und Anzeigentext zur Verfügung.",
        )
        if not llm_verfuegbar:
            st.caption("Kein API-Key erkannt – die lokale Bewertung funktioniert trotzdem.")

    submitted = st.form_submit_button("📊 Bewertung starten", type="primary", use_container_width=True)

if submitted:
    with Speicher(db_path) as db:
        jobs = db.jobs(nur_ohne_score=nur_neue, limit=int(limit))
        if not jobs:
            st.warning("Keine passenden Stellen zur Bewertung. Suche erst nach Stellen oder ändere den Filter.")
        else:
            progress = st.progress(0, text=f"Bewerte {len(jobs)} Stellen …")
            rated_scores = llm_modul.bewerte_jobs(jobs, profil, mit_llm=mit_llm)
            for index, score in enumerate(rated_scores, start=1):
                db.speichere_score(score)
                progress.progress(index / len(rated_scores), text=f"Bewertet {index} von {len(rated_scores)}")
            st.session_state["rating_result_refs"] = [score.ref for score in rated_scores]
            st.session_state.pop("rating_letter_job", None)
            st.success(f"{len(rated_scores)} Stellen bewertet. Passende Treffer und der Anschreiben-Schritt stehen unten.")
            st.rerun()

result_refs = st.session_state.get("rating_result_refs", [])
if result_refs:
    with Speicher(db_path) as db:
        result_jobs = [
            (job, db.score(job.ref))
            for ref in result_refs
            if (job := db.job(ref)) is not None and db.score(ref) is not None
        ]

    suitable_jobs = [
        (job, score)
        for job, score in result_jobs
        if not score.ausgeschlossen and score.gesamt >= profil.bewertung.min_score
    ]
    st.markdown("---")
    st.subheader("3 · Ergebnisse und Anschreiben")
    if not suitable_jobs:
        st.info("Bei diesen Stellen lag keine über deinem Profil-Mindestwert passende Stelle dabei. Du kannst die Bewertung oben anpassen oder weitere Jobs suchen.")
    else:
        suitable_jobs.sort(key=lambda pair: pair[1].gesamt, reverse=True)
        st.success(f"{len(suitable_jobs)} passende Stellen gefunden – die beste Passung zuerst.")
        for job, score in suitable_jobs:
            with st.expander(f"⭐ {score.gesamt:.0f}/100 · {job.titel} · {job.arbeitgeber}"):
                st.caption(f"{job.quelle_icon} {job.quelle_label} · 📍 {job.ort or 'Ort unbekannt'}")
                if job.bewerbungs_url:
                    st.link_button("🔗 Stelle bei der Quelle öffnen", job.bewerbungs_url)
                st.write(score.begruendung)
                if score.treffer:
                    st.success(f"Passende Skills: {', '.join(score.treffer)}")
                if job.beschreibung:
                    st.markdown("**Aus der Stellenanzeige**")
                    st.write(job.beschreibung)

        option_labels = [
            f"{score.gesamt:.0f}/100 · {job.titel} · {job.arbeitgeber} · {job.ref}"
            for job, score in suitable_jobs
        ]
        selected_label = st.selectbox("Anschreiben für welche Stelle?", option_labels, key="rating_letter_selection")
        selected_index = option_labels.index(selected_label)
        selected_job, selected_score = suitable_jobs[selected_index]
        use_llm_letter = st.checkbox(
            "Anschreiben mit Claude formulieren",
            value=llm_modul.client_verfuegbar(),
            disabled=not llm_modul.client_verfuegbar(),
            help="Ohne API-Key erstellt die App ein editierbares Basisanschreiben.",
            key="rating_letter_llm",
        )
        if st.button("✍️ Anschreiben erstellen", type="primary", use_container_width=True):
            with st.spinner("Lese Stellenanzeige und formuliere ein individuelles Anschreiben …"):
                letter = erzeuge_anschreiben(selected_job, profil, selected_score, mit_llm=use_llm_letter)
            st.session_state["selected_job_ref"] = selected_job.ref
            st.session_state["generiertes_anschreiben"] = letter
            st.session_state["generiertes_anschreiben_ref"] = selected_job.ref
            st.rerun()

        if st.session_state.get("generiertes_anschreiben_ref") == selected_job.ref:
            st.success("Anschreiben erstellt. Prüfe und bearbeite es vor dem Versand.")
            letter_key = f"rating_letter_text_{selected_job.ref}"
            if letter_key not in st.session_state:
                st.session_state[letter_key] = st.session_state["generiertes_anschreiben"]
            edited_letter = st.text_area("Anschreiben bearbeiten", key=letter_key, height=320)
            st.session_state["generiertes_anschreiben"] = edited_letter
            col_download, col_continue = st.columns(2)
            with col_download:
                st.download_button(
                    "⬇️ Anschreiben herunterladen",
                    data=edited_letter,
                    file_name=f"anschreiben-{selected_job.ref}.txt",
                    mime="text/plain",
                    use_container_width=True,
                )
            with col_continue:
                if st.button("Weiter zur Bewerbung", type="primary", use_container_width=True):
                    st.switch_page("pages/5_bewerbungen.py")
