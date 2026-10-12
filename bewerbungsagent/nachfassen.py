"""Nachfassen: nach X Tagen ohne Antwort eine kurze, hoefliche Nachfrage vorschlagen.

Der Tick legt fuer per E-Mail verschickte Bewerbungen ohne Rueckmeldung eine
Ja/Nein-Karte an (Vorschlag „nachfassen“) mit fertigem Text. Ja schickt die
Nachfrage im selben Mail-Thread wie die Bewerbung (In-Reply-To), Nein verwirft
sie. Ohne Ja geht nie etwas raus.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .bewerben import mail
from .config import Profil
from .db import Speicher
from .dossier import Dossier
from .models import Application, Job, Vorgang
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

VORLAGE = """{anrede}

vor {tage} Tagen habe ich mich bei Ihnen als {titel} beworben. Ich wollte mich erkundigen, ob meine Unterlagen vollständig angekommen sind und wie der weitere Ablauf aussieht.

An der Stelle bin ich weiterhin sehr interessiert und stehe für Rückfragen oder ein Gespräch gern zur Verfügung.

Mit freundlichen Grüßen
{name}{kontakt}"""


def _tage_seit(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        zeit = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - zeit).days


def entwurf(job: Job, profil: Profil, v: Vorgang, *, mit_llm: bool = True) -> str:
    d = Dossier.aus_json(job.dossier)
    anrede = d.anrede if d and d.sprache == "de" else "Sehr geehrte Damen und Herren,"
    p = profil.person
    kontakt = "\n".join(x for x in (p.telefon, p.email) if x)
    text = VORLAGE.format(anrede=anrede, tage=_tage_seit(v.gesendet_am), titel=job.titel, name=p.name,
                          kontakt=f"\n{kontakt}" if kontakt else "")
    if not (mit_llm and llm_modul.client_verfuegbar()):
        return text
    try:
        neu = llm_modul.chat(
            profil.bewertung.modell,
            system=("Formuliere eine kurze, freundliche Nachfrage zu einer Bewerbung (hoechstens 90 Woerter, "
                    "Deutsch, keine Floskeln, nichts erfinden). Behalte Anrede und Grussformel bei. "
                    "Gib nur den Mailtext zurueck."),
            nutzer=f"Stelle: {job.titel} bei {job.arbeitgeber}\n\nVorlage:\n{text}",
            max_tokens=2000, effort="low",
        ).strip()
        return neu or text
    except Exception as e:
        log.info("Nachfass-Text per KI fehlgeschlagen (%s) – Vorlage.", e)
        return text


def pruefe(db: Speicher, profil: Profil, *, mit_llm: bool = True) -> int:
    """Legt Nachfass-Vorschlaege fuer faellige Bewerbungen an; liefert die Anzahl neuer."""
    tage = int(profil.automatisierung.nachfassen_tage or 0)
    if tage <= 0:
        return 0
    grenze = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat(timespec="seconds")
    neu = 0
    for v in db.vorgaenge("abgeschickt"):
        if v.kanal != "email" or not v.empfaenger or not v.gesendet_am or v.gesendet_am > grenze:
            continue
        if v.rueckmeldung not in ("", "eingangsbestaetigung"):
            continue
        if v.gesendet_am < (datetime.now(timezone.utc) - timedelta(days=tage * 4)).isoformat():
            continue  # zu alt - dann lieber nicht mehr nachhaken
        job = db.job(v.ref)
        if job is None or db.vorschlag_existiert(f"nachfassen:{v.ref}"):
            continue
        text = entwurf(job, profil, v, mit_llm=mit_llm)
        neu += db.lege_vorschlag_an(
            "nachfassen", f"nachfassen:{v.ref}", f"Bei {job.arbeitgeber} nachfragen?",
            text, {"empfaenger": v.empfaenger, "betreff": f"Re: {v.betreff or dokumente_betreff(job)}",
                   "in_reply_to": v.message_id, "text": text},
            ref=v.ref,
        )
    return neu


def dokumente_betreff(job: Job) -> str:
    from .dokumente import betreff

    return betreff(job)


def senden(db: Speicher, profil: Profil, vorschlag: dict[str, Any], *, mail_sender: Any = None) -> str:
    """Ja zum Nachfassen: Mail im selben Thread verschicken."""
    konfig = mail.smtp_konfig(profil)
    if konfig is None:
        return "E-Mail-Versand ist nicht eingerichtet (Profil → 🔑 Zugänge) – Vorschlag bleibt offen."
    daten = vorschlag["daten"]
    nachricht = mail.baue_nachricht(
        absender=konfig.absender, absender_name=profil.person.name, empfaenger=daten["empfaenger"],
        betreff=daten["betreff"], text=daten.get("text") or vorschlag["text"], anhaenge=[],
        antwort_auf=daten.get("in_reply_to"),
    )
    try:
        (mail_sender or mail.sende)(nachricht, konfig)
    except Exception as e:
        db.setze_vorschlag(vorschlag["id"], "fehler", hinweis=str(e)[:300])
        return f"Nachfrage konnte nicht gesendet werden: {e}"
    db.setze_vorschlag(vorschlag["id"], "erledigt", hinweis="gesendet")
    if vorschlag.get("ref"):
        db.speichere_bewerbung(Application(
            ref=vorschlag["ref"], status="nachgefasst", url=f"mailto:{daten['empfaenger']}",
            ergebnis="Nachfrage gesendet", dry_run=False,
        ))
        db.protokolliere_entscheidung(vorschlag["ref"], "nachgefasst")
    return f"Nachfrage an {daten['empfaenger']} gesendet."
