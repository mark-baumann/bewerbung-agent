"""Cron-Ausdruecke auswerten, ohne zusaetzliche Abhaengigkeit.

Der Container ruft alle paar Minuten `bewerbungsagent tick` auf. Ob der im
Profil hinterlegte Zeitplan seit dem letzten Lauf faellig geworden ist,
entscheidet dieses Modul. So greifen Aenderungen aus der Profil-UI sofort,
ohne die Crontab neu zu schreiben oder den Container neu zu starten.

Unterstuetzt die fuenf Standardfelder (Minute Stunde Tag Monat Wochentag) mit
`*`, `*/n`, `a-b`, `a-b/n` und Listen `a,b`. Wochentag 0 und 7 sind Sonntag.
Sind Tag und Wochentag beide eingeschraenkt, reicht wie bei cron ein Treffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ZEITZONE = ZoneInfo("Europe/Berlin")

# Voreinstellungen fuer die UI (Anzeige -> Ausdruck)
VOREINSTELLUNGEN: dict[str, str] = {
    "Täglich 6:00": "0 6 * * *",
    "2× täglich (6:00 und 14:00)": "0 6,14 * * *",
    "Werktags 7:00": "0 7 * * 1-5",
    "Stündlich 8–18 Uhr, werktags": "0 8-18 * * 1-5",
    "Täglich 2:30 (nachts)": "30 2 * * *",
}

_BEREICHE = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
_NAMEN = ["Minute", "Stunde", "Tag", "Monat", "Wochentag"]
_WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


class ZeitplanFehler(ValueError):
    pass


@dataclass(frozen=True)
class Zeitplan:
    minuten: frozenset[int]
    stunden: frozenset[int]
    tage: frozenset[int]
    monate: frozenset[int]
    wochentage: frozenset[int]  # 0 = Sonntag
    tag_eingeschraenkt: bool
    wochentag_eingeschraenkt: bool

    def passt(self, zeitpunkt: datetime) -> bool:
        z = _lokal(zeitpunkt)
        if z.minute not in self.minuten or z.hour not in self.stunden or z.month not in self.monate:
            return False
        tag_ok = z.day in self.tage
        wochentag_ok = (z.isoweekday() % 7) in self.wochentage
        if self.tag_eingeschraenkt and self.wochentag_eingeschraenkt:
            return tag_ok or wochentag_ok
        return tag_ok and wochentag_ok


def _feld(text: str, index: int) -> tuple[frozenset[int], bool]:
    unten, oben = _BEREICHE[index]
    werte: set[int] = set()
    eingeschraenkt = text.strip() != "*"
    for teil in text.split(","):
        teil = teil.strip()
        if not teil:
            raise ZeitplanFehler(f"Leerer Eintrag im Feld {_NAMEN[index]}")
        schritt = 1
        if "/" in teil:
            teil, schritt_text = teil.split("/", 1)
            if not schritt_text.isdigit() or int(schritt_text) < 1:
                raise ZeitplanFehler(f"Ungueltige Schrittweite im Feld {_NAMEN[index]}: {schritt_text!r}")
            schritt = int(schritt_text)
        if teil == "*":
            von, bis = unten, oben
        elif "-" in teil:
            a, b = teil.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ZeitplanFehler(f"Ungueltiger Bereich im Feld {_NAMEN[index]}: {teil!r}")
            von, bis = int(a), int(b)
        elif teil.isdigit():
            von = bis = int(teil)
            if schritt > 1:
                bis = oben
        else:
            raise ZeitplanFehler(f"Ungueltiger Wert im Feld {_NAMEN[index]}: {teil!r}")
        if von < unten or bis > oben or von > bis:
            raise ZeitplanFehler(f"{_NAMEN[index]} ausserhalb {unten}-{oben}: {teil!r}")
        werte.update(range(von, bis + 1, schritt))
    if index == 4 and 7 in werte:
        werte.discard(7)
        werte.add(0)
    return frozenset(werte), eingeschraenkt


def parse(ausdruck: str) -> Zeitplan:
    felder = (ausdruck or "").split()
    if len(felder) != 5:
        raise ZeitplanFehler(
            "Ein Zeitplan braucht genau 5 Felder: Minute Stunde Tag Monat Wochentag (z. B. '0 6 * * *')."
        )
    teile = [_feld(f, i) for i, f in enumerate(felder)]
    return Zeitplan(
        minuten=teile[0][0],
        stunden=teile[1][0],
        tage=teile[2][0],
        monate=teile[3][0],
        wochentage=teile[4][0],
        tag_eingeschraenkt=teile[2][1],
        wochentag_eingeschraenkt=teile[4][1],
    )


def pruefe(ausdruck: str) -> str | None:
    """Fehlermeldung fuer die UI oder None, wenn der Ausdruck gueltig ist."""
    try:
        parse(ausdruck)
    except ZeitplanFehler as e:
        return str(e)
    return None


def _lokal(zeitpunkt: datetime) -> datetime:
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=timezone.utc)
    return zeitpunkt.astimezone(ZEITZONE)


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def passt(ausdruck: str, zeitpunkt: datetime) -> bool:
    return parse(ausdruck).passt(zeitpunkt)


def faellig(ausdruck: str, letzter_lauf: datetime | None, jetzt_: datetime | None = None) -> bool:
    """True, wenn seit dem letzten Lauf (exklusiv) bis jetzt (inklusiv) ein Termin lag.

    Ohne bisherigen Lauf zaehlen nur die letzten 10 Minuten, damit ein frisch
    gestarteter Container nicht sofort einen alten Termin nachholt. Nachgeholt
    werden hoechstens 24 Stunden.
    """
    plan = parse(ausdruck)
    ende = (jetzt_ or jetzt()).astimezone(timezone.utc).replace(second=0, microsecond=0)
    if letzter_lauf is None:
        start = ende - timedelta(minutes=10)
    else:
        start = max(
            letzter_lauf.astimezone(timezone.utc).replace(second=0, microsecond=0),
            ende - timedelta(hours=24),
        )
    t = start + timedelta(minutes=1)
    while t <= ende:
        if plan.passt(t):
            return True
        t += timedelta(minutes=1)
    return False


def naechster_termin(ausdruck: str, ab: datetime | None = None) -> datetime | None:
    """Naechster Termin nach `ab` in deutscher Zeit (sucht hoechstens ein Jahr voraus)."""
    plan = parse(ausdruck)
    t = (ab or jetzt()).astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    grenze = t + timedelta(days=366)
    while t < grenze:
        lokal = _lokal(t)
        if plan.passt(t):
            return lokal
        if lokal.month not in plan.monate:
            # zum Monatsanfang des naechsten Monats springen
            sprung = (lokal.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif lokal.hour not in plan.stunden:
            sprung = lokal.replace(minute=0) + timedelta(hours=1)
        else:
            sprung = t + timedelta(minutes=1)
        sprung = sprung.astimezone(timezone.utc)
        # Sommer-/Winterzeit: niemals rueckwaerts laufen
        t = sprung if sprung > t else t + timedelta(minutes=1)
    return None


def beschreibe(zeitpunkt: datetime | None) -> str:
    """'Mo 12.10. 06:00' fuer die Anzeige."""
    if zeitpunkt is None:
        return "–"
    z = _lokal(zeitpunkt)
    return f"{_WOCHENTAGE[z.weekday()]} {z:%d.%m. %H:%M}"


def als_datetime(iso: str | None) -> datetime | None:
    """Liest ISO-Zeitstempel aus der Datenbank (mit oder ohne Zeitzone, dann UTC)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
