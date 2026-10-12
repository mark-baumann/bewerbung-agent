"""Gemeinsame Test-Einstellungen."""

import pytest

from bewerbungsagent.scoring import llm


@pytest.fixture(autouse=True)
def _keine_modellliste_aus_dem_netz(monkeypatch):
    """Tests fragen nie echte KI-Anbieter nach ihren Modellen."""
    llm.vergiss_modelle()
    llm.modell_hinweise.clear()
    llm.modell_fehler.clear()
    monkeypatch.setattr(llm, "_lade_modelle", lambda anbieter, transport=None: [])
