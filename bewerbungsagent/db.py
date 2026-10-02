"""SQLite-Speicher fuer Stellen, Bewertungen und Bewerbungen."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Application, Job, Score

STANDARD_DB = Path("daten/bewerbungen.sqlite3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    recipient_email TEXT,
    zeitpunkt TEXT
);

CREATE TABLE IF NOT EXISTS automated_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    location TEXT,
    radius_km INTEGER DEFAULT 25,
    published_days INTEGER DEFAULT 30,
    only_full_time INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    interval_minutes INTEGER DEFAULT 360,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    location TEXT,
    score REAL,
    query TEXT,
    status TEXT DEFAULT 'new',
    is_read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    matched_location TEXT,
    detail_url TEXT,
    UNIQUE(ref, query)
);

CREATE INDEX IF NOT EXISTS idx_scores_gesamt ON scores(gesamt DESC);
CREATE INDEX IF NOT EXISTS idx_applications_ref ON applications(ref);
CREATE INDEX IF NOT EXISTS idx_automated_searches_next_run ON automated_searches(next_run_at);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status, is_read);
"""


class Speicher:
    def __init__(self, pfad: str | Path | None = None):
        self.pfad = Path(pfad or STANDARD_DB)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.pfad)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        application_columns = {
            row["name"] for row in self.con.execute("PRAGMA table_info(applications)")
        }
        if "recipient_email" not in application_columns:
            self.con.execute("ALTER TABLE applications ADD COLUMN recipient_email TEXT")
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
            "INSERT INTO applications (ref, status, url, anschreiben, ergebnis, schritte, dry_run, recipient_email, zeitpunkt) "
            "VALUES (:ref, :status, :url, :anschreiben, :ergebnis, :schritte, :dry_run, :recipient_email, :zeitpunkt)",
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

    # ---------------- Automatische Suchen / Postfach ----------------

    def ensure_automated_searches(self, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Synchronisiert aktive Suchläufe mit den aktuellen Profileinstellungen."""
        created: list[dict[str, Any]] = []
        self.con.execute("UPDATE automated_searches SET enabled = 0")
        for item in queries:
            row = self.con.execute(
                "SELECT id FROM automated_searches WHERE name = ? AND query = ?",
                (item["name"], item["query"]),
            ).fetchone()
            if row is None:
                now = _now_iso()
                self.con.execute(
                    "INSERT INTO automated_searches (name, query, location, radius_km, published_days, only_full_time, enabled, interval_minutes, next_run_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item["name"],
                        item["query"],
                        item.get("location"),
                        item.get("radius_km", 25),
                        item.get("published_days", 30),
                        int(item.get("only_full_time", 0)),
                        1,
                        item.get("interval_minutes", 360),
                        now,
                        now,
                    ),
                )
                created.append(item)
            else:
                self.con.execute(
                    "UPDATE automated_searches SET location = ?, radius_km = ?, published_days = ?, "
                    "only_full_time = ?, enabled = 1, interval_minutes = ? WHERE id = ?",
                    (
                        item.get("location"),
                        item.get("radius_km", 25),
                        item.get("published_days", 30),
                        int(item.get("only_full_time", 0)),
                        item.get("interval_minutes", 360),
                        row["id"],
                    ),
                )
        self.con.commit()
        return created

    def automated_searches(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM automated_searches ORDER BY enabled DESC, next_run_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def record_search_run(self, search_id: int) -> None:
        now = _now_iso()
        row = self.con.execute(
            "SELECT interval_minutes FROM automated_searches WHERE id = ?",
            (search_id,),
        ).fetchone()
        if row is None:
            return
        interval = int(row["interval_minutes"] or 360)
        future = (datetime.now(timezone.utc).timestamp() + interval * 60)
        future_iso = datetime.fromtimestamp(future, tz=timezone.utc).isoformat(timespec="seconds")
        self.con.execute(
            "UPDATE automated_searches SET last_run_at = ?, next_run_at = ? WHERE id = ?",
            (now, future_iso, search_id),
        )
        self.con.commit()

    def add_inbox_item(self, job: Job, query: str, score: float | None = None, status: str = "new") -> dict[str, Any]:
        existing = self.con.execute(
            "SELECT id FROM inbox WHERE ref = ? AND query = ?",
            (job.ref, query),
        ).fetchone()
        if existing:
            self.con.execute(
                "UPDATE inbox SET title = ?, employer = ?, location = ?, score = ?, status = ?, detail_url = ?, matched_location = ?, is_read = 0 WHERE id = ?",
                (job.titel, job.arbeitgeber, job.ort, score, status, job.bewerbungs_url, job.ort, existing["id"]),
            )
            self.con.commit()
            row = self.con.execute("SELECT * FROM inbox WHERE id = ?", (existing["id"],)).fetchone()
            return dict(row)

        now = _now_iso()
        cur = self.con.execute(
            "INSERT INTO inbox (ref, title, employer, location, score, query, status, is_read, created_at, matched_location, detail_url) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                job.ref,
                job.titel,
                job.arbeitgeber,
                job.ort,
                score,
                query,
                status,
                now,
                job.ort,
                job.bewerbungs_url,
            ),
        )
        self.con.commit()
        row = self.con.execute("SELECT * FROM inbox WHERE id = ?", (int(cur.lastrowid),)).fetchone()
        return dict(row)

    def inbox(self, unread_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM inbox"
        params: list[Any] = []
        if unread_only:
            sql += " WHERE is_read = 0"
        sql += " ORDER BY created_at DESC"
        rows = self.con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_inbox_read(self, item_id: int) -> None:
        self.con.execute("UPDATE inbox SET is_read = 1 WHERE id = ?", (item_id,))
        self.con.commit()

    def clear_inbox(self) -> None:
        self.con.execute("DELETE FROM inbox")
        self.con.commit()


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
