"""Dokumente rund um die Bewerbung: Anschreiben-PDF, Lebenslauftext, Nachweis.

- Das Anschreiben geht als PDF an E-Mail-Bewerbungen und in Upload-Felder.
- Der Text des Lebenslaufs wird einmal ausgelesen und gekuerzt in die
  LLM-Prompts gegeben, damit Bewertung und Anschreiben mehr als das
  Kurzprofil kennen.
- Der Nachweis der Eigenbemuehungen listet alle Bewerbungen eines Zeitraums
  fuer Agentur fuer Arbeit / Jobcenter.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import Profil
from .db import BEWORBEN_STATUS, STANDARD_DB, Speicher
from .models import Job
from .zeitplan import ZEITZONE, als_datetime

log = logging.getLogger(__name__)

DATEN_DIR = STANDARD_DB.parent
ANSCHREIBEN_DIR = DATEN_DIR / "anschreiben"

_SCHRIFTEN = [
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
]

_ERSATZ = {
    "–": "-", "—": "-", "„": '"', "“": '"', "”": '"',
    "‚": "'", "‘": "'", "’": "'", "…": "...", "€": "EUR",
    "•": "-", " ": " ",
}

RUECKMELDUNG_TEXT = {
    "": "offen",
    "eingangsbestaetigung": "Eingang bestätigt",
    "einladung": "Einladung zum Gespräch",
    "absage": "Absage",
    "zusage": "Zusage",
}

KANAL_TEXT = {"email": "E-Mail", "formular": "Online-Formular", "manuell": "selbst"}


# --------------------------------------------------------------------------- #
# Anschreiben
# --------------------------------------------------------------------------- #

def betreff(job: Job, referenz: str | None = None) -> str:
    teile = f"Bewerbung als {job.titel}".strip()
    if referenz:
        teile += f" – Kennziffer {referenz}"
    elif job.quelle == "arbeitsagentur" and job.ref:
        teile += f" (Referenznummer {job.ref})"
    return teile


def _pdf_mit_schrift(orientation: str = "P"):
    from fpdf import FPDF

    pdf = FPDF(orientation=orientation, unit="mm", format="A4")
    for normal, fett in _SCHRIFTEN:
        if Path(normal).exists() and Path(fett).exists():
            pdf.add_font("Brief", "", normal)
            pdf.add_font("Brief", "B", fett)
            return pdf, "Brief", lambda t: t
    # Kernschrift kann nur Latin-1: typografische Zeichen ersetzen
    def latin1(text: str) -> str:
        for alt, neu in _ERSATZ.items():
            text = text.replace(alt, neu)
        return text.encode("latin-1", "replace").decode("latin-1")

    return pdf, "Helvetica", latin1


def anschreiben_pdf(job: Job, profil: Profil, text: str, ziel: str | Path | None = None) -> Path:
    """Setzt das Anschreiben als Brief (angelehnt an DIN 5008) und speichert es als PDF."""
    pdf, schrift, t = _pdf_mit_schrift()
    pdf.set_margins(25, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()
    p = profil.person

    # Absender
    pdf.set_font(schrift, "B", 12)
    pdf.cell(0, 6, t(p.name or " "), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(schrift, "", 9.5)
    absender = [p.strasse, " ".join(x for x in (p.plz, p.ort) if x),
                " · ".join(x for x in (p.telefon, p.email) if x)]
    for zeile in absender:
        if zeile.strip():
            pdf.cell(0, 4.6, t(zeile), new_x="LMARGIN", new_y="NEXT")

    # Empfaenger
    pdf.ln(12)
    pdf.set_font(schrift, "", 11)
    kontakt = f"z. Hd. {job.ansprechpartner}" if job.ansprechpartner else ""
    for zeile in (job.arbeitgeber or "", kontakt, job.strasse or "",
                  " ".join(x for x in (job.plz or "", job.ort or "") if x)):
        if zeile.strip():
            pdf.cell(0, 5.5, t(zeile), new_x="LMARGIN", new_y="NEXT")

    # Ort, Datum
    pdf.ln(10)
    heute = datetime.now(ZEITZONE).strftime("%d.%m.%Y")
    pdf.cell(0, 5.5, t(f"{p.ort + ', ' if p.ort else ''}{heute}"), align="R", new_x="LMARGIN", new_y="NEXT")

    # Betreff
    pdf.ln(6)
    pdf.set_font(schrift, "B", 11)
    pdf.multi_cell(0, 5.5, t(betreff(job)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Brieftext
    pdf.set_font(schrift, "", 11)
    rumpf = text.strip()
    letzte_zeilen = "\n".join(rumpf.splitlines()[-2:])
    if p.name and p.name not in letzte_zeilen:
        rumpf += f"\n{p.name}"
    for absatz in rumpf.split("\n"):
        if absatz.strip():
            pdf.multi_cell(0, 5.5, t(absatz.strip()), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(3)

    anhaenge = [Path(a).name for a in profil.anhaenge()]
    if anhaenge:
        pdf.ln(6)
        pdf.set_font(schrift, "", 9)
        pdf.multi_cell(0, 4.6, t("Anlagen: " + ", ".join(anhaenge)), new_x="LMARGIN", new_y="NEXT")

    pfad = Path(ziel) if ziel else ANSCHREIBEN_DIR / f"{_dateiname(job.ref)}.pdf"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pfad))
    return pfad


def _dateiname(ref: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ref)[:80] or "anschreiben"


# --------------------------------------------------------------------------- #
# Lebenslauf
# --------------------------------------------------------------------------- #

def _docx_text(pfad: Path) -> str:
    """Text aus einer Word-Datei (ohne Zusatzpaket: DOCX ist ein ZIP mit XML)."""
    import re
    import zipfile
    from html import unescape

    with zipfile.ZipFile(pfad) as archiv:
        xml = archiv.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>|<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", " ", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml))


def lebenslauf_text(profil: Profil, cache_dir: str | Path | None = None) -> str:
    """Text des hinterlegten Lebenslaufs (PDF/DOCX/TXT/MD), gecacht nach Aenderungszeit."""
    pfad = profil.lebenslauf_pfad()
    if not pfad:
        return ""
    cache = Path(cache_dir or DATEN_DIR) / "lebenslauf_cache.json"
    stempel = f"{pfad.resolve()}|{pfad.stat().st_mtime_ns}"
    try:
        gespeichert = json.loads(cache.read_text(encoding="utf-8"))
        if gespeichert.get("stempel") == stempel:
            return gespeichert.get("text", "")
    except (OSError, ValueError):
        pass

    text = ""
    try:
        if pfad.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((seite.extract_text() or "") for seite in PdfReader(str(pfad)).pages)
        elif pfad.suffix.lower() == ".docx":
            text = _docx_text(pfad)
        elif pfad.suffix.lower() in (".txt", ".md"):
            text = pfad.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # kaputtes PDF darf nichts blockieren
        log.warning("Lebenslauf %s nicht lesbar: %s", pfad, e)
        return ""
    text = "\n".join(z.strip() for z in text.splitlines() if z.strip())
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stempel": stempel, "text": text}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return text


def lebenslauf_auszug(profil: Profil, max_zeichen: int = 6000) -> str:
    text = lebenslauf_text(profil)
    if len(text) <= max_zeichen:
        return text
    return text[:max_zeichen].rsplit("\n", 1)[0] + "\n[...]"


# --------------------------------------------------------------------------- #
# Nachweis der Eigenbemuehungen
# --------------------------------------------------------------------------- #

def nachweis_zeilen(db: Speicher, von: date, bis: date) -> list[dict[str, Any]]:
    """Alle Bewerbungen im Zeitraum (inklusive), aelteste zuerst."""
    zeilen: list[dict[str, Any]] = []
    mit_vorgang: set[str] = set()
    for v in db.vorgaenge(BEWORBEN_STATUS):
        mit_vorgang.add(v.ref)
        zeitpunkt = als_datetime(v.gesendet_am or v.aktualisiert_am)
        if zeitpunkt is None:
            continue
        tag = zeitpunkt.astimezone(ZEITZONE).date()
        if not (von <= tag <= bis):
            continue
        job = db.job(v.ref)
        if not job:
            continue
        zeilen.append({
            "datum": tag,
            "arbeitgeber": job.arbeitgeber or "",
            "stelle": job.titel,
            "ort": job.ort or "",
            "weg": "selbst" if v.status == "selbst_beworben" else KANAL_TEXT.get(v.kanal, v.kanal),
            "stand": RUECKMELDUNG_TEXT.get(v.rueckmeldung or "", v.rueckmeldung),
            "referenz": job.ref if job.quelle == "arbeitsagentur" else "",
            "link": job.anzeige_url or "",
        })
    # Aeltere Bewerbungen aus der Zeit vor den Vorgaengen (CLI/alte UI)
    for b in db.bewerbungen():
        if b["ref"] in mit_vorgang or b["status"] not in ("abgeschickt", "manuell_beworben"):
            continue
        zeitpunkt = als_datetime(b["zeitpunkt"])
        if zeitpunkt is None:
            continue
        tag = zeitpunkt.astimezone(ZEITZONE).date()
        job = db.job(b["ref"])
        if not job or not (von <= tag <= bis):
            continue
        mit_vorgang.add(b["ref"])
        zeilen.append({
            "datum": tag, "arbeitgeber": job.arbeitgeber or "", "stelle": job.titel,
            "ort": job.ort or "", "weg": "Online-Formular" if b["status"] == "abgeschickt" else "selbst",
            "stand": "offen", "referenz": job.ref if job.quelle == "arbeitsagentur" else "",
            "link": job.anzeige_url or "",
        })
    zeilen.sort(key=lambda z: z["datum"])
    return zeilen


_SPALTEN = [
    ("datum", "Datum"), ("arbeitgeber", "Arbeitgeber"), ("stelle", "Stelle"), ("ort", "Ort"),
    ("weg", "Bewerbungsweg"), ("stand", "Stand"), ("referenz", "BA-Referenz"), ("link", "Link"),
]


def nachweis_csv(zeilen: list[dict[str, Any]]) -> str:
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")
    schreiber.writerow([titel for _, titel in _SPALTEN])
    for z in zeilen:
        schreiber.writerow([
            z[k].strftime("%d.%m.%Y") if isinstance(z[k], date) else z[k] for k, _ in _SPALTEN
        ])
    return puffer.getvalue()


def nachweis_pdf(zeilen: list[dict[str, Any]], profil: Profil, von: date, bis: date) -> bytes:
    pdf, schrift, t = _pdf_mit_schrift(orientation="L")
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    pdf.set_font(schrift, "B", 14)
    pdf.cell(0, 8, t("Nachweis der Eigenbemühungen"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(schrift, "", 10)
    p = profil.person
    kopf = f"{p.name}{', ' + p.strasse if p.strasse else ''}{', ' + p.plz + ' ' + p.ort if p.ort else ''}"
    pdf.cell(0, 5.5, t(kopf), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0, 5.5,
        t(f"Zeitraum: {von:%d.%m.%Y} – {bis:%d.%m.%Y} · {len(zeilen)} Bewerbung(en)"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(4)
    pdf.set_font(schrift, "", 8.5)
    spalten = _SPALTEN[:7]
    with pdf.table(col_widths=(20, 55, 75, 35, 30, 30, 22), line_height=5) as tabelle:
        kopfzeile = tabelle.row()
        for _, titel in spalten:
            kopfzeile.cell(t(titel))
        for z in zeilen:
            reihe = tabelle.row()
            for k, _ in spalten:
                wert = z[k].strftime("%d.%m.%Y") if isinstance(z[k], date) else str(z[k] or "")
                reihe.cell(t(wert))
    if not zeilen:
        pdf.ln(4)
        pdf.cell(0, 6, t("Im Zeitraum wurden keine Bewerbungen erfasst."))
    return bytes(pdf.output())
