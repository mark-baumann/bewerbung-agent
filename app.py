"""Streamlit UI und Navigation des Bewerbungsagenten."""

import logging
import sys
from pathlib import Path
from threading import Event, Thread

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from bewerbungsagent.config import STANDARD_SUCHORT, ensure_seeded_profil, profil_ist_beispiel
from bewerbungsagent.db import Speicher

log = logging.getLogger(__name__)

st.set_page_config(
    page_title="Bewerbungsagent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _db_path() -> Path:
    db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _seed_app_state():
    profil = ensure_seeded_profil()
    if profil_ist_beispiel(profil):
        with Speicher(_db_path()) as db:
            db.ensure_automated_searches([])
        return profil

    ort = profil.suche.wo.strip() or profil.person.ort.strip() or STANDARD_SUCHORT
    search_queries = [
        {
            "name": f"{term} @ {ort}",
            "query": term,
            "location": ort,
            "radius_km": profil.suche.umkreis,
            "published_days": profil.suche.veroeffentlicht_seit_tagen,
            "only_full_time": int(profil.suche.nur_vollzeit),
            "interval_minutes": 360,
        }
        for term in profil.suche.was[:3]
    ]
    with Speicher(_db_path()) as db:
        db.ensure_automated_searches(search_queries)
    return profil


def _run_due_automated_searches() -> None:
    from datetime import datetime, timezone

    from bewerbungsagent.scoring.heuristik import bewerte
    from bewerbungsagent.sources.arbeitsagentur import ArbeitsagenturClient

    profil = ensure_seeded_profil()
    if profil_ist_beispiel(profil) or not profil.suche.was:
        return

    with Speicher(_db_path()) as db:
        for plan in db.automated_searches():
            if not plan.get("enabled", True):
                continue
            next_run = plan.get("next_run_at")
            if next_run:
                try:
                    next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                    if next_dt > datetime.now(timezone.utc):
                        continue
                except ValueError:
                    log.warning("Ungueltiger naechster Lauf fuer Suche %s: %s", plan["id"], next_run)
            try:
                with ArbeitsagenturClient() as client:
                    jobs = client.hole_jobs(
                        was=plan["query"],
                        wo=plan.get("location") or profil.person.ort or profil.suche.wo,
                        umkreis=int(plan.get("radius_km", profil.suche.umkreis or 25)),
                        veroeffentlicht_seit_tagen=int(
                            plan.get("published_days", profil.suche.veroeffentlicht_seit_tagen or 30)
                        ),
                        nur_vollzeit=bool(plan.get("only_full_time", profil.suche.nur_vollzeit)),
                        max_treffer=10,
                        mit_details=True,
                    )
                if jobs:
                    db.speichere_jobs(jobs)
                    for job in jobs:
                        score = bewerte(job, profil)
                        db.speichere_score(score)
                        if not score.ausgeschlossen and score.gesamt >= profil.bewertung.min_score:
                            db.add_inbox_item(job, plan["query"], score=score.gesamt)
            except Exception:
                log.exception("Automatische Jobsuche fehlgeschlagen: %s", plan["name"])
            finally:
                db.record_search_run(plan["id"])


@st.cache_resource
def _start_search_scheduler() -> Event:
    stop_event = Event()

    def worker():
        while not stop_event.wait(60):
            try:
                _seed_app_state()
                _run_due_automated_searches()
            except Exception:
                log.exception("Automatischer Suchdienst konnte nicht ausgefuehrt werden.")

    Thread(target=worker, name="bewerbungsagent-search-scheduler", daemon=True).start()
    return stop_event


_seed_app_state()
_run_due_automated_searches()
_start_search_scheduler()

navigation = st.navigation(
    {
        "Workflow": [
            st.Page(
                "pages/1_dashboard.py",
                title="Dashboard",
                icon="🏠",
                url_path="dashboard",
                default=True,
            ),
            st.Page("pages/2_jobsuche.py", title="Jobsuche", icon="🔍", url_path="jobsuche"),
            st.Page("pages/3_bewertung.py", title="Bewertung", icon="📊", url_path="bewertung"),
            st.Page("pages/5_bewerbungen.py", title="Bewerbungen", icon="✉️", url_path="bewerbungen"),
        ],
        "Profil": [
            st.Page("pages/6_profil.py", title="Profil & Einstellungen", icon="👤", url_path="profil"),
        ],
    },
    position="sidebar",
)
navigation.run()
