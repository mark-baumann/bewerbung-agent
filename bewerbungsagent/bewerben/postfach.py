"""Antworten auf Bewerbungen im eigenen Postfach erkennen (IMAP, nur lesend).

Der Tick ruft `pruefe_postfach` auf: neue Mails der letzten Wochen werden den
abgeschickten Bewerbungen zugeordnet (Message-ID, Absender-Domain, Stellentitel)
und als Eingangsbestaetigung, Einladung, Absage, Zusage oder Rueckfrage
eingeordnet. Der Tracker aktualisiert sich dadurch selbst; eine Einladung
erscheint prominent im Cockpit. Mails werden nie veraendert oder geloescht
(IMAP-Ordner nur `readonly` geoeffnet, Abruf per BODY.PEEK).

Konfiguration (Profil -> Zugaenge oder Umgebung):
    IMAP_HOST, IMAP_PORT (993), IMAP_BENUTZER / IMAP_PASSWORT
    (Standard: die SMTP-Zugangsdaten)
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Iterator
from urllib.parse import urlparse

from ..db import Speicher
from ..models import Job, Vorgang

log = logging.getLogger(__name__)

# Staerke einer Rueckmeldung: eine schwaechere ueberschreibt nie eine staerkere
RANG = {"": 0, "eingangsbestaetigung": 1, "rueckfrage": 2, "absage": 3, "einladung": 4, "zusage": 5}

_REGELN: list[tuple[str, re.Pattern[str], float]] = [
    ("zusage", re.compile(
        r"\b(zusage|vertragsangebot|arbeitsvertrag|freuen uns, ihnen (die stelle|eine stelle) anzubieten|"
        r"job offer|offer letter|pleased to offer)\b", re.I), 0.85),
    ("absage", re.compile(
        r"(leider|bedauerlicherweise|nicht berücksichtigen|nicht in die engere (aus)?wahl|"
        r"anderen (bewerber|kandidat)|absage|unfortunately|not to move forward|other candidates|"
        r"we regret|nicht weiter berücksichtig|keine positive (nachricht|rückmeldung))", re.I), 0.8),
    ("einladung", re.compile(
        r"(einladung|vorstellungsgespräch|kennenlerngespräch|bewerbungsgespräch|telefoninterview|"
        r"videointerview|interview|terminvorschl|lernen sie gerne (persönlich )?kennen|"
        r"invite you|schedule a call)", re.I), 0.8),
    ("rueckfrage", re.compile(
        r"(rückfrage|fehlende unterlagen|bitte (senden|schicken|reichen) sie|"
        r"benötigen (wir )?(noch|weitere)|could you (please )?(send|provide))", re.I), 0.6),
    ("eingangsbestaetigung", re.compile(
        r"(eingangsbestätigung|eingang ihrer bewerbung|bewerbung (ist )?(bei uns )?eingegangen|"
        r"bewerbung erhalten|vielen dank für ihre bewerbung|danke für ihre bewerbung|"
        r"received your application|thank you for (your )?appl)", re.I), 0.7),
]


@dataclass
class ImapKonfig:
    host: str
    port: int
    benutzer: str
    passwort: str


@dataclass
class Nachricht:
    uid_key: str
    message_id: str
    bezug: list[str]           # In-Reply-To + References
    datum: str
    absender: str
    absender_domain: str
    betreff: str
    text: str


def imap_konfig() -> ImapKonfig | None:
    host = os.environ.get("IMAP_HOST", "").strip()
    if not host:
        return None
    try:
        port = int(os.environ.get("IMAP_PORT", "993") or 993)
    except ValueError:
        port = 993
    return ImapKonfig(
        host=host, port=port,
        benutzer=(os.environ.get("IMAP_BENUTZER") or os.environ.get("SMTP_BENUTZER") or "").strip(),
        passwort=os.environ.get("IMAP_PASSWORT") or os.environ.get("SMTP_PASSWORT") or "",
    )


@contextmanager
def verbinde(konfig: ImapKonfig) -> Iterator[imaplib.IMAP4]:
    if konfig.port == 143:
        imap: imaplib.IMAP4 = imaplib.IMAP4(konfig.host, konfig.port, timeout=30)
        imap.starttls(ssl_context=ssl.create_default_context())
    else:
        imap = imaplib.IMAP4_SSL(konfig.host, konfig.port, ssl_context=ssl.create_default_context(), timeout=30)
    try:
        imap.login(konfig.benutzer, konfig.passwort)
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def anzahl_posteingang(imap: Any) -> int:
    typ, daten = imap.select("INBOX", readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Posteingang nicht lesbar: {daten}")
    return int(daten[0] or 0)


# --------------------------------------------------------------------------- #
# Lesen
# --------------------------------------------------------------------------- #

def _dekodiere(wert: str | None) -> str:
    if not wert:
        return ""
    try:
        return str(make_header(decode_header(wert)))
    except Exception:
        return wert


def _text(msg: Message, max_zeichen: int = 6000) -> str:
    teile: list[str] = []
    for teil in msg.walk() if msg.is_multipart() else [msg]:
        if teil.get_content_maintype() != "text" or teil.get_filename():
            continue
        try:
            inhalt = teil.get_payload(decode=True) or b""
            text = inhalt.decode(teil.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if teil.get_content_subtype() == "html":
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
        teile.append(text)
        if teil.get_content_subtype() == "plain":
            break
    text = re.sub(r"\s+", " ", " ".join(teile)).strip()
    return text[:max_zeichen]


def nachricht_aus_bytes(roh: bytes, uid_key: str) -> Nachricht:
    msg = email.message_from_bytes(roh)
    name, adresse = parseaddr(_dekodiere(msg.get("From")))
    bezug = re.findall(r"<[^>]+>", f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")
    try:
        datum = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        datum = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Nachricht(
        uid_key=uid_key,
        message_id=(msg.get("Message-ID") or "").strip(),
        bezug=bezug,
        datum=datum,
        absender=f"{name} <{adresse}>" if name else adresse,
        absender_domain=adresse.rsplit("@", 1)[-1].lower() if "@" in adresse else "",
        betreff=_dekodiere(msg.get("Subject")),
        text=_text(msg),
    )


def neue_nachrichten(imap: Any, db: Speicher, *, tage: int = 45, max_anzahl: int = 150) -> list[Nachricht]:
    """Noch nicht ausgewertete Mails im Posteingang (nur lesend)."""
    typ, daten = imap.select("INBOX", readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Posteingang nicht lesbar: {daten}")
    typ, wert = imap.response("UIDVALIDITY")
    gueltig = (wert[0].decode() if wert and wert[0] else "0") if typ == "OK" else "0"
    seit = (date.today() - timedelta(days=tage)).strftime("%d-%b-%Y")
    typ, daten = imap.uid("SEARCH", None, f"SINCE {seit}")
    if typ != "OK":
        return []
    uids = (daten[0] or b"").split()[-max_anzahl:]
    ergebnis = []
    for uid in uids:
        uid_key = f"{gueltig}:{uid.decode()}"
        if db.nachricht_bekannt(uid_key):
            continue
        typ, teile = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK":
            continue
        roh = next((t[1] for t in teile if isinstance(t, tuple) and len(t) > 1), None)
        if roh:
            ergebnis.append(nachricht_aus_bytes(roh, uid_key))
    return ergebnis


# --------------------------------------------------------------------------- #
# Zuordnen & Einordnen
# --------------------------------------------------------------------------- #

def klassifiziere(betreff: str, text: str) -> tuple[str, float]:
    """(art, konfidenz) per Regeln; 'sonstiges' wenn nichts passt."""
    inhalt = f"{betreff}\n{text[:3000]}"
    for art, muster, konfidenz in _REGELN:
        if muster.search(inhalt):
            if art == "einladung" and _REGELN[1][1].search(inhalt):
                # "leider ... kein Interview" ist eine Absage
                return "absage", 0.7
            return art, konfidenz
    return "sonstiges", 0.3


def _domain(url_oder_mail: str | None) -> str:
    if not url_oder_mail:
        return ""
    if "@" in url_oder_mail and "://" not in url_oder_mail:
        host = url_oder_mail.rsplit("@", 1)[-1]
    else:
        host = urlparse(url_oder_mail).hostname or ""
    teile = host.lower().split(".")
    return ".".join(teile[-2:]) if len(teile) >= 2 else host.lower()


_ATS_DOMAINS = {"personio.de", "personio.com", "greenhouse.io", "lever.co", "smartrecruiters.com",
                "recruitee.com", "workday.com", "myworkdayjobs.com", "softgarden.io", "softgarden.de",
                "successfactors.com", "successfactors.eu", "join.com", "ashbyhq.com", "bamboohr.com",
                "rexx-systems.com", "umantis.com", "dvinci.de", "concludis.de", "onlyfy.jobs", "prescreen.io"}


def _normalisiert(text: str | None) -> str:
    text = (text or "").lower()
    text = re.sub(r"\((m|w|d|f|x)[/|,]?.*?\)", " ", text)
    text = re.sub(r"\b(gmbh|ag|se|kg|mbh|co|e\.?v\.?|ug|inc|ltd)\b", " ", text)
    return re.sub(r"[^a-z0-9äöüß]+", " ", text).strip()


def zuordnen(n: Nachricht, kandidaten: list[tuple[Vorgang, Job]]) -> tuple[str | None, float]:
    """Ref der Bewerbung, auf die sich die Mail bezieht, und Sicherheit (0-1)."""
    for v, _ in kandidaten:
        if v.message_id and v.message_id in n.bezug:
            return v.ref, 1.0
    beste: tuple[str | None, float] = (None, 0.0)
    betreff = _normalisiert(n.betreff)
    inhalt = _normalisiert(f"{n.absender} {n.betreff} {n.text[:2000]}")
    for v, job in kandidaten:
        punkte = 0.0
        domains = {_domain(v.empfaenger), _domain(job.externe_url), _domain(v.bewerbungs_url),
                   _domain(job.bewerbungs_url)} - {""}
        if n.absender_domain and _domain("x@" + n.absender_domain) in domains - _ATS_DOMAINS:
            punkte += 0.6
        arbeitgeber = _normalisiert(job.arbeitgeber)
        kern = " ".join(arbeitgeber.split()[:2])
        if kern and len(kern) >= 4 and kern in inhalt:
            punkte += 0.4
        titel = _normalisiert(job.titel)
        if titel and len(titel) >= 6 and (titel in betreff or titel in inhalt):
            punkte += 0.4
        if punkte > beste[1]:
            beste = (v.ref, punkte)
    if beste[1] >= 0.6:
        return beste[0], min(beste[1], 0.95)
    return None, beste[1]


def kandidaten_fuer_zuordnung(db: Speicher) -> list[tuple[Vorgang, Job]]:
    paare = []
    for v in db.vorgaenge(("abgeschickt", "selbst_beworben")):
        job = db.job(v.ref)
        if job:
            paare.append((v, job))
    return paare


def verarbeite(db: Speicher, nachrichten: list[Nachricht]) -> dict[str, Any]:
    """Ordnet Mails zu, speichert sie und aktualisiert Rueckmeldungen; liefert eine Zusammenfassung."""
    kandidaten = kandidaten_fuer_zuordnung(db)
    zusammenfassung: dict[str, Any] = {"gelesen": len(nachrichten), "zugeordnet": 0, "neu": []}
    for n in nachrichten:
        ref, sicherheit = zuordnen(n, kandidaten) if kandidaten else (None, 0.0)
        if ref is None:
            # Nur merken, dass sie gelesen wurde - keine Inhalte fremder Mails speichern
            db.speichere_nachricht(uid_key=n.uid_key, datum=n.datum)
            continue
        art, konfidenz = klassifiziere(n.betreff, n.text)
        db.speichere_nachricht(
            uid_key=n.uid_key, message_id=n.message_id, datum=n.datum, absender=n.absender,
            betreff=n.betreff[:300], ref=ref, art=art, konfidenz=round(min(konfidenz, sicherheit + 0.3), 2),
            auszug=n.text[:600],
        )
        zusammenfassung["zugeordnet"] += 1
        v = db.vorgang(ref)
        if v and art in RANG and RANG[art] > RANG.get(v.rueckmeldung or "", 0) and art != "rueckfrage":
            db.aktualisiere_vorgang(ref, rueckmeldung=art, rueckmeldung_am=n.datum, rueckmeldung_quelle="postfach")
            zusammenfassung["neu"].append({"ref": ref, "art": art, "betreff": n.betreff[:120]})
        elif art == "rueckfrage":
            zusammenfassung["neu"].append({"ref": ref, "art": art, "betreff": n.betreff[:120]})
    return zusammenfassung


def pruefe_postfach(db: Speicher, *, verbindung: Any = None, tage: int = 45) -> dict[str, Any]:
    """Liest neue Mails und wertet sie aus. `verbindung` ersetzt die IMAP-Verbindung (Tests)."""
    if verbindung is not None:
        return verarbeite(db, neue_nachrichten(verbindung, db, tage=tage))
    konfig = imap_konfig()
    if konfig is None:
        return {"gelesen": 0, "zugeordnet": 0, "neu": [], "hinweis": "IMAP nicht eingerichtet"}
    with verbinde(konfig) as imap:
        return verarbeite(db, neue_nachrichten(imap, db, tage=tage))
