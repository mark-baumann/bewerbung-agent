"""Einstieg der Web-UI: Heute (Entscheidungen), Suchen und Einstellungen."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from bewerbungsagent import zugangsdaten  # noqa: E402
from bewerbungsagent.config import lade_env  # noqa: E402

# Lokal liegen API- und SMTP-Zugangsdaten in einer .env (Docker setzt sie direkt);
# in der UI gespeicherte Zugaenge (Profil -> Zugaenge) haben Vorrang.
lade_env()
zugangsdaten.anwenden()


@st.cache_resource
def _unterlagen_einmal_sichern() -> None:
    """Profil und Unterlagen beim Start in den dauerhaften Datenordner sichern (nur Kopien)."""
    from bewerbungsagent.config import lade_profil, profil_sichern, unterlagen_sichern

    try:
        profil_sichern()
        unterlagen_sichern(lade_profil(pruefen=False))
    except Exception:
        pass


_unterlagen_einmal_sichern()

st.set_page_config(page_title="Bewerbungsagent", page_icon=":material/work:", layout="centered")

from bewerbungsagent import ui  # noqa: E402

# Eigene Leiste oben statt Seitenleiste: am Handy immer sichtbar, nur drei Ziele.
seiten = st.navigation([
    st.Page(ui.SEITE_HEUTE, title="Heute", icon=":material/today:", default=True),
    st.Page(ui.SEITE_SUCHEN, title="Suchen", icon=":material/search:", url_path="suchen"),
    st.Page(ui.SEITE_EINSTELLUNGEN, title="Einstellungen", icon=":material/settings:", url_path="einstellungen"),
], position="hidden")
ui.neuer_seitenlauf()
ui.navleiste()
seiten.run()
