"""Jobsuche in den im Profil aktivierten Quellen."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import STANDARD_DB, Speicher
from bewerbungsagent.models import Job
from bewerbungsagent.jobsuche import hole_jobs

st.set_page_config(page_title="Jobsuche", page_icon="🔍", layout="wide")

st.title("🔍 Jobsuche")

# Datenbank-Pfad (identisch mit der CLI, damit Daten den Container-Neustart
# ueberleben, siehe AUG-378)
db_path = STANDARD_DB

STANDARD_ORT = "München"

# Lade Profil für Defaults
profil = None
try:
    profil = lade_profil()
    default_begriffe = profil.suche.was
    default_ort = profil.suche.wo or STANDARD_ORT
    default_umkreis = int(profil.suche.umkreis)
    default_tage = int(profil.suche.veroeffentlicht_seit_tagen)
    default_max = int(profil.suche.max_pro_query)
    nur_vollzeit_default = profil.suche.nur_vollzeit
except Exception as e:
    st.warning(f"Profil konnte nicht geladen werden: {e}")
    st.page_link("pages/6_profil.py", label="👤 Profil jetzt über die UI einrichten", icon="👤")
    default_begriffe = ["Python Developer"]
    default_ort = STANDARD_ORT
    default_umkreis = 50
    default_tage = 7
    default_max = 50
    nur_vollzeit_default = False

default_mit_details = True

# Die zuletzt ausgeführte Suche ist persistent in der Datenbank gespeichert und
# geht dem Profil vor, damit das Formular nach Reload/Neustart gleich bleibt.
with Speicher(str(db_path)) as db:
    letzte_suche = db.letzte_suche()
if letzte_suche:
    lp = letzte_suche["parameter"]
    default_begriffe = lp.get("begriffe") or default_begriffe
    default_ort = lp.get("wo", default_ort)
    default_umkreis = int(lp.get("umkreis", default_umkreis))
    default_tage = int(lp.get("tage", default_tage))
    default_max = int(lp.get("max_treffer", default_max))
    nur_vollzeit_default = bool(lp.get("nur_vollzeit", nur_vollzeit_default))
    default_mit_details = bool(lp.get("mit_details", default_mit_details))

st.markdown("""
Suche nach Jobs der Bundesagentur für Arbeit und GET IN IT. Ort und Umkreis
kommen direkt aus deinem Profil, z. B. München + 30 km.
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
            value=min(max(default_umkreis, 0), 200),
            step=10,
            help="0 = bundesweit"
        )

    with col2:
        wo = st.text_input(
            "Wo (Ort)",
            value=default_ort,
            help="Standard: München - leer für ganz Deutschland"
        )

        tage = st.slider(
            "Veröffentlicht seit (Tagen)",
            min_value=1,
            max_value=30,
            value=min(max(default_tage, 1), 30),
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

    mit_details = st.checkbox("Mit Details laden (langsamer, aber vollständiger)", value=default_mit_details)

    submitted = st.form_submit_button("🔍 Jobs suchen", use_container_width=True)

if submitted:
    # Parse Suchbegriffe
    begriffe = [b.strip() for b in was_input.split(",") if b.strip()]

    if not begriffe:
        st.error("Bitte mindestens einen Suchbegriff eingeben!")
    else:
        alle_jobs = {}
        neu = aktualisiert = 0
        progress_bar = st.progress(0)
        status_text = st.empty()

        quellen = profil.suche.quellen if profil else ["arbeitsagentur"]
        with Speicher(str(db_path)) as db:
            for quelle in quellen:
                for idx, begriff in enumerate(begriffe):
                    status_text.write(f"🔍 Suche bei {quelle}: '{begriff}' in '{wo or 'ganz Deutschland'}' ...")

                    try:
                        jobs, neue, aktualisierte = hole_jobs(
                            db,
                            quelle=quelle,
                            suchbegriff=begriff,
                            wo=(wo or "").strip(),
                            umkreis=int(umkreis),
                            tage=int(tage),
                            nur_vollzeit=nur_vollzeit,
                            max_treffer=int(max_treffer),
                            mit_details=mit_details,
                        )

                        status_text.write(f"✅ {len(jobs)} Treffer für '{begriff}' ({quelle})")

                        for job in jobs:
                            alle_jobs.setdefault(job.ref, job)
                        neu += neue
                        aktualisiert += aktualisierte

                    except Exception as e:
                        st.error(f"❌ Fehler bei {quelle}, '{begriff}': {e}")

                    progress_bar.progress((idx + 1) / len(begriffe))

            # Suche samt Treffern persistent ablegen (überlebt Reload/Neustart)
            db.speichere_suche(
                {
                    "begriffe": begriffe,
                    "wo": (wo or "").strip(),
                    "umkreis": int(umkreis),
                    "tage": int(tage),
                    "max_treffer": int(max_treffer),
                    "nur_vollzeit": bool(nur_vollzeit),
                    "mit_details": bool(mit_details),
                    "quellen": list(quellen),
                },
                alle_jobs.keys(),
            )

        progress_bar.empty()
        status_text.empty()

        if alle_jobs:
            st.success(f"✅ {len(alle_jobs)} eindeutige Jobs gefunden!")
            st.info(f"💾 {neu} neue und {aktualisiert} aktualisierte Jobs gespeichert.")
        else:
            st.warning("⚠️ Keine Jobs gefunden. Versuche andere Suchbegriffe oder erweitere den Suchradius.")

# Ergebnisse der letzten (persistent gespeicherten) Suche anzeigen – auch nach
# einem Reload der Seite oder einem Neustart des Containers.
with Speicher(str(db_path)) as db:
    letzte_suche = db.letzte_suche()
    gefundene_jobs = db.jobs_nach_refs(letzte_suche["refs"]) if letzte_suche else []
    scores = {job.ref: db.score(job.ref) for job in gefundene_jobs}

if letzte_suche and gefundene_jobs:
    lp = letzte_suche["parameter"]
    st.markdown("---")
    st.subheader(f"📋 Ergebnisse der letzten Suche ({len(gefundene_jobs)} Jobs)")
    st.caption(
        f"Gesucht am {letzte_suche['zeitpunkt']} (UTC) · "
        f"{', '.join(lp.get('begriffe', []))} · "
        f"{lp.get('wo') or 'ganz Deutschland'}"
        + (f" + {lp.get('umkreis')} km" if lp.get("wo") else "")
    )

    bewertet = sum(1 for s in scores.values() if s is not None)
    st.write(f"📊 {bewertet} von {len(gefundene_jobs)} Jobs bereits bewertet.")

    for job in gefundene_jobs[:20]:
        score = scores.get(job.ref)
        praefix = f"**{score.gesamt:.0f}** · " if score and not score.ausgeschlossen else (
            "🚫 " if score else ""
        )
        with st.expander(f"{praefix}**{job.titel}** - {job.arbeitgeber}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"📍 **Ort:** {job.ort or 'Unbekannt'}")
                st.write(f"🏢 **Arbeitgeber:** {job.arbeitgeber}")
                st.write(f"🗃️ **Quelle:** {job.quelle or 'Unbekannt'}")
                st.write(f"📅 **Veröffentlicht:** {job.veroeffentlicht or 'Unbekannt'}")
            with col2:
                st.write(f"💼 **Vertrag:** {job.befristung or 'Unbefristet'}")
                st.write(f"🏠 **Homeoffice:** {'Ja' if job.homeoffice else 'Nein'}")
                st.write(f"💰 **Vergütung:** {job.verguetung or 'Nicht angegeben'}")

            if job.beschreibung:
                st.markdown("**Beschreibung:**")
                st.text(job.beschreibung[:300] + "..." if len(job.beschreibung) > 300 else job.beschreibung)

            if job.anzeige_url:
                st.markdown(f"[🔗 Zum Jobangebot öffnen]({job.anzeige_url})")
            st.code(f"Referenz: {job.ref}", language=None)

    if len(gefundene_jobs) > 20:
        st.info(f"... und {len(gefundene_jobs) - 20} weitere Jobs. Bewerte die Jobs, um die besten Treffer zu sehen.")

    # Next steps
    st.markdown("---")
    st.subheader("✅ Nächste Schritte")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📊 Jobs jetzt bewerten", use_container_width=True):
            st.switch_page("pages/3_bewertung.py")
    with col2:
        if st.button("📋 Zurück zum Dashboard", use_container_width=True):
            st.switch_page("app.py")
