"""Dashboard mit geführtem Bewerbungs-Workflow."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import ensure_seeded_profil, profil_ist_beispiel
from bewerbungsagent.db import Speicher

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
profil = ensure_seeded_profil()

st.title("🏠 Deine Jobsuche")
st.caption("Ein klarer Ablauf: Profil vervollständigen, passende Stellen finden und Bewerbungen vorbereiten.")

demo_profile = profil_ist_beispiel(profil)
if demo_profile:
    st.warning(
        "Das gespeicherte Profil enthält noch Beispieldaten. Bitte ersetze sie durch deine echten Angaben "
        "und lade deinen Lebenslauf hoch, bevor du Anschreiben erstellst oder dich bewirbst.",
        icon="⚠️",
    )

cv_path = Path(profil.unterlagen.lebenslauf).expanduser() if profil.unterlagen.lebenslauf else None
profile_steps = [
    bool(profil.person.vorname.strip() and profil.person.nachname.strip()),
    bool(profil.person.ort.strip() or profil.suche.wo.strip()),
    bool(profil.suche.was),
    bool(cv_path and cv_path.is_file()),
]
completed_steps = sum(profile_steps)

with Speicher(db_path) as db:
    stats = db.statistik()
    inbox_items = db.inbox(unread_only=True)
    inbox_sources = {}
    for item in inbox_items:
        job = db.job(item["ref"])
        if job is not None:
            inbox_sources[item["id"]] = (job.quelle_icon, job.quelle_label)

cols = st.columns(4)
for col, label, value in zip(
    cols,
    ("Stellen gefunden", "Bewertet", "Neue Treffer", "Bewerbungen"),
    (stats["jobs"], stats["bewertet"], len(inbox_items), stats["abgeschickt"] + stats["probelaeufe"]),
):
    col.metric(label, value)

st.markdown("---")
st.subheader("Dein nächster Schritt")
if completed_steps < len(profile_steps):
    st.info(
        f"Profil eingerichtet: **{completed_steps} von {len(profile_steps)} Angaben**. "
        "Vervollständige dein Profil, damit die Suche Wohnort und Qualifikationen berücksichtigen kann."
    )
    if st.button("1 · Profil vervollständigen", type="primary", use_container_width=True):
        st.switch_page("pages/6_profil.py")
else:
    st.success("Dein Profil hat die wichtigsten Angaben für eine personalisierte Suche.")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("1 · Jobs suchen", type="primary", use_container_width=True):
            st.switch_page("pages/2_jobsuche.py")
    with col2:
        if st.button("2 · Treffer bewerten", use_container_width=True):
            st.switch_page("pages/3_bewertung.py")
    with col3:
        if st.button("3 · Postfach öffnen", use_container_width=True):
            st.switch_page("pages/6_profil.py")

with st.expander("Profil-Check", expanded=completed_steps < len(profile_steps)):
    labels = ("Name", "Wohnort", "Suchbegriffe", "Lebenslauf")
    for label, complete in zip(labels, profile_steps):
        st.write(f"{'✅' if complete else '⬜'} {label}")

st.markdown("---")
col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("📬 Neue Treffer")
    if inbox_items:
        for item in inbox_items[:5]:
            with st.container(border=True):
                source_icon, source_label = inbox_sources.get(item["id"], ("🔎", "Quelle unbekannt"))
                st.markdown(
                    f"{source_icon} **{item['title'] or 'Stelle ohne Titel'}** · "
                    f"{item['employer'] or 'Arbeitgeber unbekannt'}"
                )
                st.caption(
                    f"{source_label}  ·  📍 {item['location'] or 'Ort nicht angegeben'}  ·  "
                    f"Suchbegriff: {item['query']}  ·  "
                    f"Score: {item['score'] if item['score'] is not None else 'noch nicht bewertet'}"
                )
                if item.get("detail_url"):
                    st.link_button("Stelle ansehen", item["detail_url"])
    else:
        st.info("Noch keine neuen Treffer. Starte eine Suche oder prüfe deine Suchbegriffe und den Wohnort im Profil.")

with col2:
    st.subheader("Schnellzugriff")
    if st.button("🔍 Jobsuche", use_container_width=True):
        st.switch_page("pages/2_jobsuche.py")
    if st.button("📊 Bewertungen", use_container_width=True):
        st.switch_page("pages/3_bewertung.py")
    if st.button("✉️ Bewerbungen", use_container_width=True):
        st.switch_page("pages/5_bewerbungen.py")
    if st.button("👤 Profil & Postfach", use_container_width=True):
        st.switch_page("pages/6_profil.py")
