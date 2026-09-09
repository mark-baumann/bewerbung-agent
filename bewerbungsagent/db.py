"""SQLite-Speicher fuer Stellen, Bewertungen und Bewerbungen."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import Application, Job, Score

# "daten/" ist der einzige Pfad, der in Dockerfile/ONBOARDING als Volume-Mount
# dokumentiert ist (`-v $PWD/daten:/app/daten`) und im Deployment-Stack persistent
# gehalten wird. CLI und UI muessen denselben Pfad verwenden, sonst gehen in der
# UI gefundene Jobs bei jedem Container-Neustart verloren (AUG-378).
STANDARD_DB = Path(os.environ.get("BEWERBUNGSAGENT_DB", "daten/bewerbungen.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    ref TEXT PRIMARY KEY,
    titel TEXT NOT NULL,
    arbeitgeber TEXT,
    quelle TEXT,
    beruf TEXT,
    ort TEXT,
    plz TEXT,
    region TEXT,
    entfernung_km REAL,
    veroeffentlicht TEXT,
    eintrittsdatum TEXT,
    externe_url TEXT,
    detail_url TEXT,
    beschreibung TEXT,
    homeoffice INTEGER,
    vollzeit INTEGER,
    teilzeit INTEGER,
    befristung TEXT,
    verguetung TEXT,
    geholt_am TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    ref TEXT PRIMARY KEY REFERENCES jobs(ref) ON DELETE CASCADE,
    gesamt REAL,
    passung REAL,
    sentiment REAL,
    treffer TEXT,
    gruen TEXT,
    rot TEXT,
    begruendung TEXT,
    ausgeschlossen INTEGER,
    ausschlussgrund TEXT,
    bewerter TEXT,
    bewertet_am TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL REFERENCES jobs(ref) ON DELETE CASCADE,
    status TEXT NOT NULL,
    url TEXT,
    anschreiben TEXT,
    ergebnis TEXT,
    schritte INTEGER,
    dry_run INTEGER,
    zeitpunkt TEXT
);

CREATE INDEX IF NOT EXISTS idx_scores_gesamt ON scores(gesamt DESC);
CREATE INDEX IF NOT EXISTS idx_applications_ref ON applications(ref);
"""


class Speicher:
    def __init__(self, pfad: str | Path | None = None):
        self.pfad = Path(pfad or STANDARD_DB)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.pfad)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Speicher":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------- Jobs ----------------

    def speichere_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Fuegt Jobs ein bzw. aktualisiert sie. Gibt (neu, aktualisiert) zurueck."""
        neu = aktualisiert = 0
        for job in jobs:
            vorhanden = self.con.execute(
                "SELECT 1 FROM jobs WHERE ref = ?", (job.ref,)
            ).fetchone()
            d = job.as_dict()
            spalten = ", ".join(d)
            platzhalter = ", ".join(f":{k}" for k in d)
            updates = ", ".join(f"{k}=excluded.{k}" for k in d if k != "ref")
            self.con.execute(
                f"INSERT INTO jobs ({spalten}) VALUES ({platzhalter}) "
                f"ON CONFLICT(ref) DO UPDATE SET {updates}",
                d,
            )
            if vorhanden:
                aktualisiert += 1
            else:
                neu += 1
        self.con.commit()
        return neu, aktualisiert

    def job(self, ref: str) -> Job | None:
        row = self.con.execute("SELECT * FROM jobs WHERE ref = ?", (ref,)).fetchone()
        return _zu_job(row) if row else None

    def jobs(self, nur_ohne_score: bool = False, limit: int | None = None) -> list[Job]:
        sql = "SELECT j.* FROM jobs j"
        if nur_ohne_score:
            sql += " LEFT JOIN scores s ON s.ref = j.ref WHERE s.ref IS NULL"
        sql += " ORDER BY j.veroeffentlicht DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_zu_job(r) for r in self.con.execute(sql)]

    # ---------------- Scores ----------------

    def speichere_score(self, score: Score) -> None:
        d = score.as_dict()
        d["ausgeschlossen"] = int(d["ausgeschlossen"])
        spalten = ", ".join(d)
        platzhalter = ", ".join(f":{k}" for k in d)
        updates = ", ".join(f"{k}=excluded.{k}" for k in d if k != "ref")
        self.con.execute(
            f"INSERT INTO scores ({spalten}) VALUES ({platzhalter}) "
            f"ON CONFLICT(ref) DO UPDATE SET {updates}",
            d,
        )
        self.con.commit()

    def score(self, ref: str) -> Score | None:
        row = self.con.execute("SELECT * FROM scores WHERE ref = ?", (ref,)).fetchone()
        return _zu_score(row) if row else None

    def bestenliste(
        self,
        min_score: float = 0.0,
        limit: int = 20,
        offen: bool = False,
    ) -> list[tuple[Job, Score]]:
        """Bestbewertete Stellen. `offen=True` blendet bereits beworbene aus."""
        sql = """
            SELECT j.*, s.gesamt, s.passung, s.sentiment, s.treffer, s.gruen, s.rot,
                   s.begruendung, s.ausgeschlossen, s.ausschlussgrund, s.bewerter, s.bewertet_am
            FROM jobs j JOIN scores s ON s.ref = j.ref
            WHERE s.ausgeschlossen = 0 AND s.gesamt >= ?
        """
        if offen:
            sql += (
                " AND j.ref NOT IN (SELECT ref FROM applications "
                "WHERE status IN ('abgeschickt', 'uebersprungen'))"
            )
        sql += " ORDER BY s.gesamt DESC LIMIT ?"
        rows = self.con.execute(sql, (min_score, limit)).fetchall()
        return [(_zu_job(r), _zu_score(r)) for r in rows]

    # ---------------- Bewerbungen ----------------

    def speichere_bewerbung(self, app: Application) -> int:
        d = app.as_dict()
        d["dry_run"] = int(d["dry_run"])
        cur = self.con.execute(
            "INSERT INTO applications (ref, status, url, anschreiben, ergebnis, schritte, dry_run, zeitpunkt) "
            "VALUES (:ref, :status, :url, :anschreiben, :ergebnis, :schritte, :dry_run, :zeitpunkt)",
            d,
        )
        self.con.commit()
        return int(cur.lastrowid or 0)

    def bewerbungen(self, ref: str | None = None) -> list[dict[str, Any]]:
        if ref:
            rows = self.con.execute(
                "SELECT * FROM applications WHERE ref = ? ORDER BY zeitpunkt DESC", (ref,)
            )
        else:
            rows = self.con.execute("SELECT * FROM applications ORDER BY zeitpunkt DESC")
        return [dict(r) for r in rows]

    def schon_beworben(self, ref: str) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM applications WHERE ref = ? AND status = 'abgeschickt'", (ref,)
        ).fetchone()
        return row is not None

    def statistik(self) -> dict[str, int]:
        z = lambda sql: int(self.con.execute(sql).fetchone()[0])  # noqa: E731
        return {
            "jobs": z("SELECT COUNT(*) FROM jobs"),
            "bewertet": z("SELECT COUNT(*) FROM scores"),
            "ausgeschlossen": z("SELECT COUNT(*) FROM scores WHERE ausgeschlossen = 1"),
            "abgeschickt": z("SELECT COUNT(*) FROM applications WHERE status='abgeschickt'"),
            "probelaeufe": z("SELECT COUNT(*) FROM applications WHERE status='probelauf'"),
        }


def _zu_job(row: sqlite3.Row) -> Job:
    felder = set(Job.__dataclass_fields__)
    daten = {k: row[k] for k in row.keys() if k in felder}
    for flag in ("homeoffice", "vollzeit", "teilzeit"):
        if daten.get(flag) is not None:
            daten[flag] = bool(daten[flag])
    return Job(**daten)


def _zu_score(row: sqlite3.Row) -> Score:
    return Score(
        ref=row["ref"],
        gesamt=row["gesamt"],
        passung=row["passung"],
        sentiment=row["sentiment"],
        treffer=json.loads(row["treffer"] or "[]"),
        gruen=json.loads(row["gruen"] or "[]"),
        rot=json.loads(row["rot"] or "[]"),
        begruendung=row["begruendung"] or "",
        ausgeschlossen=bool(row["ausgeschlossen"]),
        ausschlussgrund=row["ausschlussgrund"],
        bewerter=row["bewerter"] or "heuristik",
        bewertet_am=row["bewertet_am"] or "",
    )
