"""Jobquellen: Registry mit Metadaten und Fabrik.

Alle Stellen der UI, CLI und Automatik, die Quellen auflisten, lesen diese
Registry - eine neue Quelle braucht nur einen Eintrag hier.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .aggregatoren import AdzunaClient, EuresClient, JoobleClient
from .arbeitnow import ArbeitnowClient
from .arbeitsagentur import ArbeitsagenturClient
from .ats import WunschArbeitgeberClient
from .get_in_it import GetInItClient
from .oeffentlicher_dienst import InteramtClient, ServiceBundClient
from .remote import RemoteClient


@dataclass(frozen=True)
class QuellenInfo:
    name: str
    klasse: type
    label: str
    art: str                        # suche | feed | arbeitgeber
    beschreibung: str
    schluessel: tuple[str, ...] = field(default_factory=tuple)  # benoetigte Umgebungsvariablen
    min_abstand_min: int = 0        # Feeds: Mindestabstand zwischen zwei Abrufen

    def verfuegbar(self) -> bool:
        return all(os.environ.get(k) for k in self.schluessel)


_LISTE = [
    QuellenInfo("arbeitsagentur", ArbeitsagenturClient, "Bundesagentur für Arbeit", "suche",
                "Größte Jobbörse Deutschlands, alle Berufe, Suche mit Umkreis."),
    QuellenInfo("get-in-it", GetInItClient, "GET IN IT", "suche", "IT-Stellen für Einsteiger und Erfahrene."),
    QuellenInfo("arbeitnow", ArbeitnowClient, "Arbeitnow", "feed",
                "Stellen direkt aus Bewerbermanagementsystemen, oft englischsprachig.", min_abstand_min=60),
    QuellenInfo("service-bund", ServiceBundClient, "service.bund.de", "feed",
                "Öffentlicher Dienst von Bund, Ländern und Kommunen.", min_abstand_min=60),
    QuellenInfo("interamt", InteramtClient, "Interamt", "suche",
                "Öffentlicher Dienst mit Ansprechpartner und Bewerbungsfrist."),
    QuellenInfo("remote", RemoteClient, "Remote-Jobbörsen", "feed",
                "Himalayas, Remotive, Remote OK, Jobicy, We Work Remotely – nur für Deutschland offene Stellen.",
                min_abstand_min=360),
    QuellenInfo("wunscharbeitgeber", WunschArbeitgeberClient, "Wunsch-Arbeitgeber", "arbeitgeber",
                "Stellen direkt von den Karriereseiten deiner Wunsch-Arbeitgeber.", min_abstand_min=720),
    QuellenInfo("eures", EuresClient, "EURES (EU-Ausland)", "suche",
                "Stellen im europäischen Ausland, z. B. Österreich oder Schweiz (Länder im Profil wählen)."),
    QuellenInfo("adzuna", AdzunaClient, "Adzuna", "suche", "Großer Aggregator (kostenloser API-Key).",
                schluessel=("ADZUNA_APP_ID", "ADZUNA_APP_KEY")),
    QuellenInfo("jooble", JoobleClient, "Jooble", "suche", "Großer Aggregator (kostenloser API-Key).",
                schluessel=("JOOBLE_API_KEY",)),
]

QUELLEN: dict[str, QuellenInfo] = {q.name: q for q in _LISTE}


def label(quelle: str | None) -> str:
    info = QUELLEN.get(quelle or "")
    return info.label if info else (quelle or "unbekannt")


def bekannte(quellen: list[str] | None) -> list[str]:
    """Nur registrierte Quellen (alte/unbekannte Namen in Profil oder letzter Suche ignorieren)."""
    return [q for q in (quellen or []) if q in QUELLEN]


def client_fuer(quelle: str, profil=None):
    try:
        info = QUELLEN[quelle]
    except KeyError as exc:
        raise ValueError(f"Unbekannte Jobquelle: {quelle}. Erlaubt: {', '.join(QUELLEN)}") from exc
    return info.klasse(profil=profil)
