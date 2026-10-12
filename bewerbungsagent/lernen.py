"""Lernen aus Ja/Nein: Vorlieben erkennen und als Ja/Nein-Vorschlaege anbieten.

Jede Entscheidung liegt in `entscheidungen`. Daraus entsteht
- ein kurzer Text fuer den KI-Bewertungs-Prompt („was die Person bisher
  abgelehnt/gewaehlt hat“),
- ein Bonus/Malus fuer die Reihenfolge der Kandidaten (auch ohne KI),
- Vorschlaege fuer Profil-Aenderungen (Zeitarbeit ausschliessen, Umkreis
  verkleinern, Arbeitgeber sperren, Mindestpassung anheben, Stilregeln,
  bewaehrte Suchbegriffe). Der Agent aendert das Profil nie still - jede
  Aenderung ist eine Ja/Nein-Karte im Cockpit.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .config import Profil, ermittle_profil_pfad, speichere_profil
from .db import Speicher
from .models import Job
from .scoring import llm as llm_modul

log = logging.getLogger(__name__)

AB_ANZAHL = 3          # so oft muss sich ein Muster wiederholen, bevor es ein Vorschlag wird
_STOP = {"m", "w", "d", "und", "oder", "mit", "für", "fuer", "im", "in", "der", "die", "das", "als", "zum", "zur",
         "senior", "junior", "mwd", "gn", "all", "genders"}


def _woerter(titel: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9+#]{3,}", (titel or "").lower()) if w not in _STOP}


@dataclass
class Vorlieben:
    ja: int = 0
    nein: int = 0
    gruende: Counter = field(default_factory=Counter)
    nein_arbeitgeber: Counter = field(default_factory=Counter)
    ja_woerter: Counter = field(default_factory=Counter)
    nein_woerter: Counter = field(default_factory=Counter)
    bearbeitet: int = 0

    def anpassung(self, job: Job) -> float:
        """Bonus/Malus (-10..+10) fuer die Reihenfolge der Kandidaten."""
        if self.ja + self.nein < AB_ANZAHL:
            return 0.0
        woerter = _woerter(job.titel)
        plus = sum(self.ja_woerter[w] for w in woerter)
        minus = sum(self.nein_woerter[w] for w in woerter)
        wert = 3.0 * (plus - minus) / max(1, plus + minus) * min(1.0, (plus + minus) / 3)
        if self.nein_arbeitgeber[(job.arbeitgeber or "").strip()] >= 2:
            wert -= 5
        return max(-10.0, min(10.0, wert))

    def prompt_text(self) -> str:
        """Kurzfassung fuer die KI-Bewertung (leer, solange es zu wenig Daten gibt)."""
        if self.ja + self.nein < AB_ANZAHL:
            return ""
        teile = [f"Bisher {self.ja}x Ja und {self.nein}x Nein zu vorgeschlagenen Stellen."]
        if self.gruende:
            teile.append("Haeufige Nein-Gruende: " + ", ".join(f"{g} ({n}x)" for g, n in self.gruende.most_common(4)))
        if self.ja_woerter:
            teile.append("Typisch fuer gewaehlte Stellen: " + ", ".join(w for w, _ in self.ja_woerter.most_common(6)))
        if self.nein_woerter:
            teile.append("Typisch fuer abgelehnte Stellen: " + ", ".join(w for w, _ in self.nein_woerter.most_common(6)))
        return "Vorlieben aus frueheren Entscheidungen (beruecksichtigen, aber Fakten gehen vor):\n" + "\n".join(teile)


def vorlieben(db: Speicher) -> Vorlieben:
    v = Vorlieben()
    for e in db.entscheidungen(limit=1000):
        if e["art"] in ("ja", "autopilot", "selbst"):
            v.ja += 1
            v.ja_woerter.update(_woerter(e.get("titel")))
            if (e.get("details") or {}).get("bearbeitet"):
                v.bearbeitet += 1
        elif e["art"] == "nein":
            v.nein += 1
            v.nein_woerter.update(_woerter(e.get("titel")))
            v.gruende.update(e.get("gruende") or [])
            if e.get("arbeitgeber"):
                v.nein_arbeitgeber[e["arbeitgeber"].strip()] += 1
    # Woerter, die in beiden Gruppen gleich oft vorkommen, sagen nichts
    for w in set(v.ja_woerter) & set(v.nein_woerter):
        gemeinsam = min(v.ja_woerter[w], v.nein_woerter[w])
        v.ja_woerter[w] -= gemeinsam
        v.nein_woerter[w] -= gemeinsam
    v.ja_woerter, v.nein_woerter = +v.ja_woerter, +v.nein_woerter
    return v


# --------------------------------------------------------------------------- #
# Vorschlaege ableiten und anwenden
# --------------------------------------------------------------------------- #

def leite_vorschlaege_ab(db: Speicher, profil: Profil, *, mit_llm: bool = True) -> int:
    """Erzeugt neue Ja/Nein-Vorschlaege aus den Entscheidungen; liefert die Anzahl neuer."""
    v = vorlieben(db)
    b, s = profil.bewertung, profil.suche
    neu = 0
    ausschluss = {a.casefold() for a in b.ausschluss}
    if v.gruende["Zeitarbeit"] >= AB_ANZAHL and "zeitarbeit" not in ausschluss:
        neu += db.lege_vorschlag_an(
            "ausschluss", "ausschluss:zeitarbeit", "Zeitarbeit künftig ausschließen?",
            f"Du hast {v.gruende['Zeitarbeit']} Stellen wegen Zeitarbeit abgelehnt. Soll der Agent "
            "Zeitarbeit und Personalvermittlung gar nicht mehr vorschlagen?",
            {"ausschluss_hinzu": "Zeitarbeit"},
        )
    if v.gruende["zu weit weg"] >= AB_ANZAHL and s.umkreis > 10:
        kleiner = max(10, int(round(s.umkreis * 0.6 / 5) * 5))
        neu += db.lege_vorschlag_an(
            "umkreis", f"umkreis:{s.umkreis}->{kleiner}", f"Umkreis von {s.umkreis} auf {kleiner} km verkleinern?",
            f"{v.gruende['zu weit weg']} Stellen waren dir zu weit weg.", {"umkreis": kleiner},
        )
    if v.gruende["passt fachlich nicht"] >= AB_ANZAHL + 2 and b.min_score < 80:
        hoeher = min(85, int(b.min_score) + 5)
        neu += db.lege_vorschlag_an(
            "min_score", f"min_score:{int(b.min_score)}->{hoeher}",
            f"Mindest-Passung von {b.min_score:.0f} auf {hoeher} anheben?",
            f"{v.gruende['passt fachlich nicht']} Vorschläge passten fachlich nicht. Strenger filtern?",
            {"min_score": hoeher},
        )
    gesperrt = {a.casefold() for a in b.ausschluss_arbeitgeber}
    for arbeitgeber, anzahl in v.nein_arbeitgeber.items():
        if anzahl >= 2 and arbeitgeber.casefold() not in gesperrt:
            neu += db.lege_vorschlag_an(
                "arbeitgeber", f"arbeitgeber:{arbeitgeber.casefold()}", f"„{arbeitgeber}“ nicht mehr vorschlagen?",
                f"Du hast {anzahl} Stellen von {arbeitgeber} abgelehnt.", {"sperren": arbeitgeber},
            )
    if mit_llm and v.bearbeitet >= 2 and llm_modul.client_verfuegbar():
        neu += _stilregeln_vorschlagen(db, profil)
    return neu


def _stilregeln_vorschlagen(db: Speicher, profil: Profil) -> int:
    paare = []
    for vorgang in db.vorgaenge(("freigegeben", "wird_gesendet", "abgeschickt")):
        if vorgang.anschreiben_original and vorgang.anschreiben and \
                vorgang.anschreiben.strip() != vorgang.anschreiben_original.strip():
            paare.append((vorgang.anschreiben_original[:2500], vorgang.anschreiben[:2500]))
    if len(paare) < 2:
        return 0
    schluessel = f"stil:{len(paare)}"
    if any(x["schluessel"] == schluessel for x in db.vorschlaege(status=None)):
        return 0
    nutzer = "\n\n".join(f"VORHER:\n{a}\n\nNACHHER (vom Bewerber korrigiert):\n{b}" for a, b in paare[-3:])
    try:
        text = llm_modul.chat(
            profil.bewertung.modell,
            system=("Leite aus den Korrekturen des Bewerbers hoechstens drei kurze, allgemeine Stilregeln "
                    "fuer kuenftige Anschreiben ab (je eine Zeile, Imperativ, Deutsch). Nur die Regeln."),
            nutzer=nutzer, max_tokens=2000, effort="low",
        ).strip()
    except Exception as e:
        log.info("Stilregeln nicht ableitbar: %s", e)
        return 0
    regeln = [z.strip(" -•*") for z in text.splitlines() if z.strip(" -•*")][:3]
    if not regeln:
        return 0
    return int(db.lege_vorschlag_an(
        "stil", schluessel, "Stilregeln aus deinen Korrekturen übernehmen?",
        "Du hast Anschreiben vor dem Absenden angepasst. Der Agent würde künftig so schreiben:\n- "
        + "\n- ".join(regeln),
        {"stil_hinzu": regeln},
    ))


def schlage_suchbegriff_vor(db: Speicher, begriff: str, passend: int) -> bool:
    return db.lege_vorschlag_an(
        "suchbegriff", f"suchbegriff:{begriff.casefold()}", f"Suchbegriff „{begriff}“ dauerhaft übernehmen?",
        f"Der Agent hat „{begriff}“ selbst ausprobiert und {passend} passende Stelle(n) gefunden.",
        {"suchbegriff_hinzu": begriff},
    )


def wende_an(db: Speicher, profil: Profil, vorschlag_id: int, ja: bool, *, profil_pfad: str | None = None,
             mail_sender: Any = None) -> str:
    """Ja/Nein zu einem Vorschlag; liefert eine kurze Rueckmeldung fuer die UI."""
    vorschlag = db.vorschlag(vorschlag_id)
    if vorschlag is None or vorschlag["status"] != "offen":
        return "Dieser Vorschlag ist schon erledigt."
    daten = vorschlag["daten"]
    if not ja:
        db.setze_vorschlag(vorschlag_id, "abgelehnt")
        if vorschlag["art"] == "suchbegriff":
            db.merke_suchbegriff(daten["suchbegriff_hinzu"], "erkundung", "verworfen", "vom Nutzer abgelehnt")
        return "Verstanden – nicht übernommen."
    if vorschlag["art"] == "nachfassen":
        from . import nachfassen

        return nachfassen.senden(db, profil, vorschlag, mail_sender=mail_sender)
    b, s = profil.bewertung, profil.suche
    if "ausschluss_hinzu" in daten and daten["ausschluss_hinzu"] not in b.ausschluss:
        b.ausschluss.append(daten["ausschluss_hinzu"])
    if "umkreis" in daten:
        s.umkreis = int(daten["umkreis"])
    if "min_score" in daten:
        b.min_score = float(daten["min_score"])
    if "sperren" in daten and daten["sperren"] not in b.ausschluss_arbeitgeber:
        b.ausschluss_arbeitgeber.append(daten["sperren"])
    if "stil_hinzu" in daten:
        zusatz = "\n".join(f"- {r}" for r in daten["stil_hinzu"])
        profil.unterlagen.anschreiben_stil = (profil.unterlagen.anschreiben_stil.rstrip() + "\n" + zusatz).strip()
    if "suchbegriff_hinzu" in daten and daten["suchbegriff_hinzu"] not in s.was:
        s.was.append(daten["suchbegriff_hinzu"])
        db.merke_suchbegriff(daten["suchbegriff_hinzu"], "erkundung", "aktiv", "ins Profil übernommen")
    speichere_profil(profil, ermittle_profil_pfad(profil_pfad))
    db.setze_vorschlag(vorschlag_id, "angenommen")
    return "Übernommen – gilt ab dem nächsten Lauf."
