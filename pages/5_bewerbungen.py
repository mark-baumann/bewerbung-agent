"""Bewerbungen - Anschreiben generieren und Bewerbungen abschicken."""

import streamlit as st
from pathlib import Path
import sys
import asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil
from bewerbungsagent.db import Speicher
from bewerbungsagent.anschreiben import erzeuge as erzeuge_anschreiben
from bewerbungsagent.models import Application

st.set_page_config(page_title="Bewerbungen", page_icon="✉️", layout="wide")

st.title("✉️ Bewerbungen")

db_path = Path.home() / ".bewerbungsagent" / "jobs.db"

try:
    profil = lade_profil()
except Exception as e:
    st.warning(f"Profil konnte nicht geladen werden: {e}")
    st.page_link("pages/6_profil.py", label="👤 Profil jetzt über die UI einrichten", icon="👤")
    st.stop()

# Session State für ausgewählten Job
if "selected_job_ref" not in st.session_state:
    st.session_state["selected_job_ref"] = None

st.markdown("""
Generiere Anschreiben und schicke Bewerbungen ab (manuell oder per Browser-Automation).
""")

# Tab-Navigation
tab1, tab2, tab3 = st.tabs(["📝 Anschreiben generieren", "📤 Bewerbung abschicken", "📊 Bewerbungsübersicht"])

# Tab 1: Anschreiben generieren
with tab1:
    st.subheader("📝 Anschreiben generieren")

    with Speicher(str(db_path)) as db:
        # Job-Auswahl
        bewertete = db.bestenliste(min_score=0, limit=1000, offen=False)

        if not bewertete:
            st.warning("⚠️ Keine bewerteten Jobs gefunden. Führe zuerst eine Bewertung durch!")
        else:
            # Dropdown mit Jobs
            job_optionen = {
                f"{score.gesamt:.0f} - {job.titel} ({job.arbeitgeber})": job.ref
                for job, score in bewertete[:50]
            }

            # Vorauswahl wenn über selected_job_ref
            default_idx = 0
            if st.session_state["selected_job_ref"]:
                try:
                    refs = list(job_optionen.values())
                    default_idx = refs.index(st.session_state["selected_job_ref"])
                except ValueError:
                    pass

            ausgewaehlter_job_text = st.selectbox(
                "Job auswählen",
                options=list(job_optionen.keys()),
                index=default_idx
            )

            job_ref = job_optionen[ausgewaehlter_job_text]
            job = db.job(job_ref)
            score = db.score(job_ref)

            if job:
                # Job-Info
                with st.expander("ℹ️ Job-Details"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Titel:** {job.titel}")
                        st.write(f"**Arbeitgeber:** {job.arbeitgeber}")
                        st.write(f"**Ort:** {job.ort or 'Unbekannt'}")
                    with col2:
                        if score:
                            st.metric("Score", f"{score.gesamt:.0f}")
                            st.write(f"Passung: {score.passung:.0f} | Ton: {score.sentiment:.0f}")

                # Optionen
                col1, col2 = st.columns(2)
                with col1:
                    mit_llm = st.checkbox(
                        "Mit LLM generieren (Claude)",
                        value=True,
                        help="Nutze Claude für personalisiertes Anschreiben"
                    )
                with col2:
                    ausgabe_datei = st.text_input(
                        "Optional: Als Datei speichern",
                        value="",
                        placeholder="z.B. anschreiben.txt"
                    )

                if st.button("✨ Anschreiben generieren", type="primary", use_container_width=True):
                    with st.spinner("Generiere Anschreiben..."):
                        try:
                            anschreiben = erzeuge_anschreiben(job, profil, score, mit_llm=mit_llm)

                            st.success("✅ Anschreiben erfolgreich generiert!")

                            # Anzeigen
                            st.text_area(
                                "Anschreiben",
                                value=anschreiben,
                                height=400,
                                label_visibility="collapsed"
                            )

                            # Optional speichern
                            if ausgabe_datei:
                                Path(ausgabe_datei).write_text(anschreiben, encoding="utf-8")
                                st.info(f"💾 Gespeichert als: {ausgabe_datei}")

                            # In Session für nächsten Tab
                            st.session_state["generiertes_anschreiben"] = anschreiben
                            st.session_state["selected_job_ref"] = job_ref

                        except Exception as e:
                            st.error(f"❌ Fehler beim Generieren: {e}")

# Tab 2: Bewerbung abschicken
with tab2:
    st.subheader("📤 Bewerbung abschicken")

    if "selected_job_ref" not in st.session_state or not st.session_state["selected_job_ref"]:
        st.info("Wähle zuerst einen Job im Tab 'Anschreiben generieren'")
    else:
        with Speicher(str(db_path)) as db:
            job = db.job(st.session_state["selected_job_ref"])
            score = db.score(st.session_state["selected_job_ref"])

            if job:
                st.write(f"**Job:** {job.titel} - {job.arbeitgeber}")
                st.write(f"**Bewerbungs-URL:** {job.bewerbungs_url or 'Nicht verfügbar'}")

                if score:
                    st.metric("Score", f"{score.gesamt:.0f}")

                st.markdown("---")

                # Browser-Automation Hinweis
                st.warning("""
                ⚠️ **Browser-Automation**

                Die automatische Bewerbung nutzt `browser-use` und Playwright.
                Dies ist experimentell und funktioniert nur bei unterstützten Portalen.

                **Alternativen:**
                - **Manuell:** Öffne die Bewerbungs-URL und bewirb dich manuell
                - **Probelauf:** Teste die Automation ohne tatsächliches Absenden
                """)

                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("🖐️ Manuelle Bewerbung")
                    if job.bewerbungs_url:
                        st.markdown(f"[🌐 Bewerbungsseite öffnen]({job.bewerbungs_url})")

                        if st.button("✅ Als beworben markieren", use_container_width=True):
                            bewerbung = Application(
                                ref=job.ref,
                                status="manuell_beworben",
                                url=job.bewerbungs_url,
                                anschreiben=st.session_state.get("generiertes_anschreiben", None),
                                ergebnis="Manuelle Bewerbung durch Nutzer",
                                dry_run=False
                            )
                            db.speichere_bewerbung(bewerbung)
                            st.success("✅ Als beworben markiert!")
                    else:
                        st.error("Keine Bewerbungs-URL verfügbar")

                with col2:
                    st.subheader("🤖 Browser-Automation")

                    st.info("""
                    **Voraussetzungen:**
                    - `pip install -e ".[browser]"`
                    - `playwright install chromium`
                    - Bewerbungsformular muss unterstützt werden
                    """)

                    dry_run = st.checkbox(
                        "Probelauf (nicht absenden)",
                        value=True,
                        help="Führt die Automation durch ohne tatsächlich abzusenden"
                    )

                    if st.button("🚀 Browser-Automation starten", use_container_width=True, disabled=True):
                        st.warning("Browser-Automation ist in der UI derzeit deaktiviert. Nutze das CLI: `bewerbungsagent bewerben <ref>`")

                # Bewerbungsverlauf für diesen Job
                st.markdown("---")
                st.subheader("📜 Bewerbungsverlauf")

                bewerbungen = db.bewerbungen(job.ref)
                if bewerbungen:
                    for idx, bew in enumerate(bewerbungen, 1):
                        with st.expander(f"#{idx} - {bew['status']} ({bew['zeitpunkt']})"):
                            st.write(f"**Status:** {bew['status']}")
                            st.write(f"**Zeitpunkt:** {bew['zeitpunkt']}")
                            st.write(f"**Schritte:** {bew['schritte']}")
                            if bew['url']:
                                st.write(f"**URL:** {bew['url']}")
                            if bew['ergebnis']:
                                st.text_area("Ergebnis", value=bew['ergebnis'], disabled=True, height=100)
                else:
                    st.info("Noch keine Bewerbungen für diesen Job.")

# Tab 3: Bewerbungsübersicht
with tab3:
    st.subheader("📊 Bewerbungsübersicht")

    try:
        with Speicher(str(db_path)) as db:
            alle_jobs = db.jobs(limit=10000)
            alle_bewerbungen = []

            for job in alle_jobs:
                bewerbungen = db.bewerbungen(job.ref)
                for bew in bewerbungen:
                    alle_bewerbungen.append({
                        "job": job,
                        "bewerbung": bew
                    })

            if alle_bewerbungen:
                st.success(f"✅ {len(alle_bewerbungen)} Bewerbungen gefunden")

                # Statistiken
                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    st.metric("Gesamt", len(alle_bewerbungen))
                with col2:
                    abgeschickt = sum(1 for b in alle_bewerbungen if b["bewerbung"]["status"] == "abgeschickt")
                    st.metric("Abgeschickt", abgeschickt)
                with col3:
                    probelauf = sum(1 for b in alle_bewerbungen if b["bewerbung"]["status"] == "probelauf")
                    st.metric("Probelauf", probelauf)
                with col4:
                    fehlgeschlagen = sum(1 for b in alle_bewerbungen if b["bewerbung"]["status"] == "fehlgeschlagen")
                    st.metric("Fehlgeschlagen", fehlgeschlagen)

                st.markdown("---")

                # Liste
                for item in sorted(alle_bewerbungen, key=lambda x: x["bewerbung"]["zeitpunkt"], reverse=True)[:20]:
                    job = item["job"]
                    bew = item["bewerbung"]

                    status_icon = {
                        "abgeschickt": "✅",
                        "probelauf": "🔵",
                        "fehlgeschlagen": "❌",
                        "manuell_beworben": "🖐️",
                        "vorbereitet": "⏳"
                    }.get(bew["status"], "❓")

                    with st.expander(f"{status_icon} {job.titel} - {job.arbeitgeber} ({bew['zeitpunkt']})"):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.write(f"**Job:** {job.titel}")
                            st.write(f"**Arbeitgeber:** {job.arbeitgeber}")
                            st.write(f"**Ort:** {job.ort or 'Unbekannt'}")
                            st.write(f"**Ref:** `{job.ref}`")

                        with col2:
                            st.write(f"**Status:** {bew['status']}")
                            st.write(f"**Zeitpunkt:** {bew['zeitpunkt']}")
                            st.write(f"**Schritte:** {bew['schritte']}")
                            st.write(f"**Dry Run:** {'Ja' if bew.get('dry_run', True) else 'Nein'}")

                        if bew['ergebnis']:
                            st.text_area("Ergebnis", value=bew['ergebnis'], disabled=True, height=80, label_visibility="collapsed")

            else:
                st.info("📭 Noch keine Bewerbungen vorhanden. Starte deine erste Bewerbung!")
    except Exception as e:
        st.error(f"Fehler: {e}")
        import traceback
        st.code(traceback.format_exc())
