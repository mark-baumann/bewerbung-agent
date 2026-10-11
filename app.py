"""Einstieg der Web-UI: Navigation zwischen Cockpit, Prozess, Tracker und Profil."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from bewerbungsagent.config import lade_env  # noqa: E402

# Lokal liegen API- und SMTP-Zugangsdaten in einer .env (Docker setzt sie direkt)
lade_env()

st.set_page_config(page_title="Bewerbungsagent", page_icon="📬", layout="wide")

seiten = st.navigation([
    st.Page("seiten/cockpit.py", title="Cockpit", icon="🏠", default=True),
    st.Page("seiten/schritt_fuer_schritt.py", title="Schritt für Schritt", icon="🧭", url_path="schritte"),
    st.Page("seiten/bewerbungen.py", title="Bewerbungen", icon="📬", url_path="bewerbungen"),
    st.Page("seiten/profil.py", title="Profil & Einstellungen", icon="👤", url_path="profil"),
])
seiten.run()
