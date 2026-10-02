"""Jobquellen und Fabrik fuer die im Profil aktivierten Quellen."""

from __future__ import annotations

from .arbeitsagentur import ArbeitsagenturClient
from .get_in_it import GetInItClient

QUELLEN = {"arbeitsagentur": ArbeitsagenturClient, "get-in-it": GetInItClient}


def client_fuer(quelle: str):
    try:
        return QUELLEN[quelle]()
    except KeyError as exc:
        raise ValueError(f"Unbekannte Jobquelle: {quelle}. Erlaubt: {', '.join(QUELLEN)}") from exc
