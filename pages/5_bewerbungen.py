"""Eine Seite zum Vorbereiten und Absenden einer Bewerbung."""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.anschreiben import erzeuge as erzeuge_anschreiben
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.mailer import sende_bewerbung
from bewerbungsagent.models import Application
from bewerbungsagent.scoring.llm import client_verfuegbar

st.title("✉️ Bewerbung vorbereiten und absenden")
st.caption("Stelle prüfen, Anschreiben bearbeiten, Empfänger und Anhänge kontrollieren – alles auf dieser Seite.")

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
try:
    profil = lade_profil()
except (OSError, ValueError) as exc:
    st.error(f"Profil konnte nicht geladen werden: {exc}")
    st.page_link("pages/6_profil.py", label="Profil und SMTP-Einstellungen öffnen", icon="👤")
    st.stop()

with Speicher(db_path) as db:
    jobs = db.jobs()
    scores = db.scores_by_ref()

if not jobs:
    st.info("Noch keine Stellen gespeichert. Starte zuerst eine Jobsuche.")
    st.page_link("pages/2_jobsuche.py", label="Jobsuche öffnen", icon="🔍")
    st.stop()

job_by_ref = {job.ref: job for job in jobs}
selected_ref = st.session_state.get("selected_job_ref")
if selected_ref not in job_by_ref:
    selected_ref = jobs[0].ref

selected_job = st.selectbox(
    "Stelle",
    options=[job.ref for job in jobs],
    index=[job.ref for job in jobs].index(selected_ref),
    format_func=lambda ref: (
        f"{job_by_ref[ref].titel} · {job_by_ref[ref].arbeitgeber or 'Arbeitgeber unbekannt'}"
    ),
    key="application_job_select",
)
st.session_state["selected_job_ref"] = selected_job
job = job_by_ref[selected_job]
score = scores.get(selected_job)

with st.container(border=True):
    st.subheader(job.titel)
    st.caption(
        f"{job.arbeitgeber or 'Arbeitgeber unbekannt'} · 📍 {job.ort or 'Ort nicht angegeben'} · "
        f"{job.quelle_icon} {job.quelle_label}"
    )
    if job.bewerbungs_url:
        st.link_button("Originalanzeige öffnen", job.bewerbungs_url)
    if score:
        st.metric("Passung", f"{score.gesamt:.0f}/100")
        st.write(score.begruendung)
    st.markdown("### Stellenbeschreibung")
    st.write(job.beschreibung or "Für diese Stelle liegt keine Beschreibung vor.")

st.markdown("---")
st.subheader("1 · Anschreiben")

letter_state_key = f"application_letter_{job.ref}"
letter_editor_key = f"application_letter_editor_{job.ref}"
if letter_state_key not in st.session_state and st.session_state.get("generiertes_anschreiben_ref") == job.ref:
    st.session_state[letter_state_key] = st.session_state.get("generiertes_anschreiben", "")

llm_available = client_verfuegbar()
use_llm = st.checkbox(
    "Mit Claude personalisieren",
    value=llm_available,
    disabled=not llm_available,
    key=f"application_llm_{job.ref}",
)
if not llm_available:
    st.caption("Ohne API-Key wird ein bearbeitbares Anschreiben aus einer Vorlage erstellt.")

if st.button("✍️ Anschreiben erstellen", type="secondary", key=f"generate_letter_{job.ref}"):
    with st.spinner("Anschreiben wird erstellt …"):
        st.session_state[letter_state_key] = erzeuge_anschreiben(
            job,
            profil,
            score,
            mit_llm=use_llm,
        )
    st.session_state.pop(letter_editor_key, None)
    st.rerun()

if letter_state_key not in st.session_state:
    st.session_state[letter_state_key] = ""
if letter_editor_key not in st.session_state:
    st.session_state[letter_editor_key] = st.session_state[letter_state_key]

letter = st.text_area(
    "Anschreiben prüfen und bearbeiten",
    key=letter_editor_key,
    height=320,
    placeholder="Erstelle ein Anschreiben oder schreibe es hier.",
)
st.session_state[letter_state_key] = letter

st.markdown("---")
st.subheader("2 · Empfänger und Unterlagen")
st.caption(f"Absender: {profil.smtp.sender_email or 'nicht eingerichtet'} · BCC: kontakt@markb.de")

recipient = st.text_input(
    "Empfängeradresse des Arbeitgebers",
    placeholder="bewerbungen@unternehmen.de",
    key=f"application_recipient_{job.ref}",
)

attachments = profil.anhaenge()
if attachments:
    st.write("**Diese Dateien werden angehängt:**")
    for attachment in attachments:
        path = Path(attachment)
        st.write(f"- {path.name} · {path.stat().st_size / 1024:.0f} KB")
else:
    st.warning("Im Profil sind keine vorhandenen Bewerbungsunterlagen hinterlegt.")

missing_attachments = profil.fehlende_anhaenge()
if missing_attachments:
    st.error("Diese Profil-Dateien fehlen: " + ", ".join(missing_attachments))

if not profil.smtp.host or not profil.smtp.sender_email:
    st.warning("SMTP ist noch nicht vollständig eingerichtet.")
    st.page_link("pages/6_profil.py", label="SMTP-Einstellungen öffnen", icon="⚙️")

if st.button(
    "📨 Bewerbung absenden",
    type="primary",
    use_container_width=True,
    disabled=not (profil.smtp.host and profil.smtp.sender_email and bool(letter.strip())),
    key=f"send_application_{job.ref}",
):
    try:
        result = sende_bewerbung(profil, job, recipient, letter)
    except (OSError, RuntimeError, ValueError, smtplib.SMTPException) as exc:
        with Speicher(db_path) as db:
            db.speichere_bewerbung(
                Application(
                    ref=job.ref,
                    status="fehlgeschlagen",
                    anschreiben=letter,
                    ergebnis=f"SMTP-Versand fehlgeschlagen: {exc}",
                    dry_run=False,
                    recipient_email=recipient,
                )
            )
        st.error(f"Bewerbung konnte nicht versendet werden: {exc}")
    else:
        with Speicher(db_path) as db:
            db.speichere_bewerbung(
                Application(
                    ref=job.ref,
                    status="abgeschickt",
                    anschreiben=letter,
                    ergebnis=(
                        "SMTP-Server hat die Nachricht zur Zustellung angenommen. "
                        "Die Zustellung ins Postfach ist nicht bestätigt. "
                        f"Message-ID: {result['message_id']}; Empfänger: {result['recipients']}."
                    ),
                    dry_run=False,
                    recipient_email=recipient,
                )
            )
        st.success(
            f"Der SMTP-Server hat die Bewerbung an {recipient} und "
            "deine BCC-Adresse zur Zustellung angenommen."
        )
