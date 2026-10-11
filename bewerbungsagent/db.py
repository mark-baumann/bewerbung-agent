"""SQLite-Speicher fuer Stellen, Bewertungen und Bewerbungen."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Application, Job, Score, Vorgang

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
    geholt_am TEXT,
    anforderungen TEXT,
    team_info TEXT,
    tech_stack TEXT,
    aufgaben TEXT,
    benefits TEXT,
    details_quelle TEXT,
    details_generiert_am TEXT
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

CREATE TABLE IF NOT EXISTS abrufe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gestartet_am TEXT NOT NULL,
    beendet_am TEXT,
    quelle TEXT NOT NULL,
    suchbegriff TEXT NOT NULL,
    parameter TEXT NOT NULL,
    status TEXT NOT NULL,
    treffer INTEGER NOT NULL DEFAULT 0,
    neu INTEGER NOT NULL DEFAULT 0,
    aktualisiert INTEGER NOT NULL DEFAULT 0,
    fehler TEXT
);
CREATE INDEX IF NOT EXISTS idx_abrufe_gestartet_am ON abrufe(gestartet_am DESC);

CREATE TABLE IF NOT EXISTS suchen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeitpunkt TEXT NOT NULL,
    parameter TEXT NOT NULL,
    refs TEXT NOT NULL
);

-- Welche Abfrage hat welche Stelle geliefert (Suchbegriff-Statistik)
CREATE TABLE IF NOT EXISTS abruf_treffer (
    abruf_id INTEGER NOT NULL REFERENCES abrufe(id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    PRIMARY KEY (abruf_id, ref)
);
CREATE INDEX IF NOT EXISTS idx_abruf_treffer_ref ON abruf_treffer(ref);

-- Aktueller Stand der Bewerbung je Stelle (Freigabe-Warteschlange + Tracker)
CREATE TABLE IF NOT EXISTS vorgaenge (
    ref TEXT PRIMARY KEY REFERENCES jobs(ref) ON DELETE CASCADE,
    status TEXT NOT NULL,
    kanal TEXT,
    empfaenger TEXT,
    bewerbungs_url TEXT,
    betreff TEXT,
    anschreiben TEXT,
    anschreiben_pdf TEXT,
    email_text TEXT,
    probelauf TEXT,
    hinweis TEXT,
    herkunft TEXT,
    erstellt_am TEXT,
    aktualisiert_am TEXT,
    freigegeben_am TEXT,
    gesendet_am TEXT,
    rueckmeldung TEXT,
    rueckmeldung_am TEXT,
    notiz TEXT
);
CREATE INDEX IF NOT EXISTS idx_vorgaenge_status ON vorgaenge(status);

-- Protokoll der Hintergrundlaeufe (Automatik, Vorbereiten, Senden)
CREATE TABLE IF NOT EXISTS laeufe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    art TEXT NOT NULL,
    ausloeser TEXT NOT NULL,
    gestartet_am TEXT NOT NULL,
    beendet_am TEXT,
    status TEXT NOT NULL,
    zusammenfassung TEXT,
    fehler TEXT
);
CREATE INDEX IF NOT EXISTS idx_laeufe_art ON laeufe(art, id DESC);
"""

# Spalten, die nach dem urspruenglichen Schema per ALTER TABLE ergaenzt wurden.
# `CREATE TABLE IF NOT EXISTS` legt sie bei bereits existierenden Datenbanken
# nicht nachtraeglich an, daher braucht es diese Migration (AUG-431).
JOBS_MIGRATIONS: dict[str, str] = {
    "anforderungen": "ALTER TABLE jobs ADD COLUMN anforderungen TEXT",
    "team_info": "ALTER TABLE jobs ADD COLUMN team_info TEXT",
    "tech_stack": "ALTER TABLE jobs ADD COLUMN tech_stack TEXT",
    "aufgaben": "ALTER TABLE jobs ADD COLUMN aufgaben TEXT",
    "benefits": "ALTER TABLE jobs ADD COLUMN benefits TEXT",
    "details_quelle": "ALTER TABLE jobs ADD COLUMN details_quelle TEXT",
    "details_generiert_am": "ALTER TABLE jobs ADD COLUMN details_generiert_am TEXT",
}


# Vorgaenge in diesen Status gelten als "erledigt beworben" (Wochenziel, Nachweis).
BEWORBEN_STATUS = ("abgeschickt", "selbst_beworben")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dubletten_schluessel(arbeitgeber: str | None, titel: str | None) -> str:
    """Gleiche Stelle aus zwei Quellen erkennen: Arbeitgeber + Titel ohne (m/w/d)."""

    def norm(text: str | None) -> str:
        text = (text or "").casefold()
        text = re.sub(r"\((?:[mwdfx]\s*/\s*)+[mwdfx]\)|\b[mwd]/[mwd]/[mwd]\b", " ", text)
        text = re.sub(r"\b(gmbh|ag|se|kg|co|mbh|ug|e\.?v\.?)\b", " ", text)
        text = re.sub(r"[^\w]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    return f"{norm(arbeitgeber)}|{norm(titel)}"


class Speicher:
    def __init__(self, pfad: str | Path | None = None):
        self.pfad = Path(pfad or STANDARD_DB)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        # UI, Cron und Hintergrund-Worker greifen parallel zu: WAL + Wartezeit
        # statt sofortigem "database is locked".
        self.con = sqlite3.connect(self.pfad, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        if str(self.pfad) != ":memory:":
            self.con.execute("PRAGMA journal_mode = WAL")
        self.con.executescript(SCHEMA)
        self._migriere_jobs_spalten()
        self.con.commit()

    def _migriere_jobs_spalten(self) -> None:
        vorhanden = {row["name"] for row in self.con.execute("PRAGMA table_info(jobs)")}
        for spalte, ddl in JOBS_MIGRATIONS.items():
            if spalte not in vorhanden:
                self.con.execute(ddl)

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

    def jobs_nach_refs(self, refs: Iterable[str]) -> list[Job]:
        """Liefert die Jobs zu den Referenzen in der uebergebenen Reihenfolge."""
        refs = list(dict.fromkeys(refs))
        if not refs:
            return []
        platzhalter = ", ".join("?" for _ in refs)
        rows = self.con.execute(f"SELECT * FROM jobs WHERE ref IN ({platzhalter})", refs)
        nach_ref = {r["ref"]: _zu_job(r) for r in rows}
        return [nach_ref[r] for r in refs if r in nach_ref]

    # ---------------- Suchen (UI) ----------------

    def speichere_suche(self, parameter: dict[str, Any], refs: Iterable[str]) -> int:
        """Merkt sich eine Suche samt gefundener Jobs, damit sie einen Reload ueberlebt."""
        cur = self.con.execute(
            "INSERT INTO suchen (zeitpunkt, parameter, refs) VALUES (datetime('now'), ?, ?)",
            (
                json.dumps(parameter, ensure_ascii=False, sort_keys=True),
                json.dumps(list(dict.fromkeys(refs)), ensure_ascii=False),
            ),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def letzte_suche(self) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM suchen ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "zeitpunkt": row["zeitpunkt"],
            "parameter": json.loads(row["parameter"]),
            "refs": json.loads(row["refs"]),
        }

    # ---------------- Datenabrufe ----------------

    def starte_abruf(self, quelle: str, suchbegriff: str, parameter: dict[str, Any]) -> int:
        """Protokolliert den Beginn eines Abrufs von einer Jobboerse."""
        cur = self.con.execute(
            "INSERT INTO abrufe (gestartet_am, quelle, suchbegriff, parameter, status) "
            "VALUES (datetime('now'), ?, ?, ?, 'laeuft')",
            (quelle, suchbegriff, json.dumps(parameter, ensure_ascii=False, sort_keys=True)),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def beende_abruf(
        self, abruf_id: int, *, treffer: int = 0, neu: int = 0,
        aktualisiert: int = 0, fehler: str | None = None,
    ) -> None:
        status = "fehler" if fehler else "erfolgreich"
        self.con.execute(
            "UPDATE abrufe SET beendet_am=datetime('now'), status=?, treffer=?, neu=?, "
            "aktualisiert=?, fehler=? WHERE id=?",
            (status, treffer, neu, aktualisiert, fehler, abruf_id),
        )
        self.con.commit()

    def letzte_abrufe(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM abrufe ORDER BY gestartet_am DESC, id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

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

    # ---------------- Abruf-Treffer ----------------

    def merke_abruf_treffer(self, abruf_id: int, refs: Iterable[str]) -> None:
        self.con.executemany(
            "INSERT OR IGNORE INTO abruf_treffer (abruf_id, ref) VALUES (?, ?)",
            [(abruf_id, r) for r in dict.fromkeys(refs)],
        )
        self.con.commit()

    def suchbegriff_statistik(self, min_score: float = 0.0) -> list[dict[str, Any]]:
        """Je Suchbegriff: Abrufe, eindeutige Treffer, passende und beworbene Stellen."""
        rows = self.con.execute(
            """
            SELECT a.suchbegriff AS begriff,
                   COUNT(DISTINCT a.id) AS abrufe,
                   COUNT(DISTINCT t.ref) AS treffer,
                   COUNT(DISTINCT CASE WHEN s.ausgeschlossen = 0 AND s.gesamt >= ?
                                       THEN t.ref END) AS passend,
                   COUNT(DISTINCT CASE WHEN v.status IN ('abgeschickt', 'selbst_beworben',
                                                         'freigegeben', 'wird_gesendet')
                                       THEN t.ref END) AS beworben,
                   MAX(a.gestartet_am) AS zuletzt
            FROM abrufe a
            LEFT JOIN abruf_treffer t ON t.abruf_id = a.id
            LEFT JOIN scores s ON s.ref = t.ref
            LEFT JOIN vorgaenge v ON v.ref = t.ref
            WHERE a.status = 'erfolgreich'
            GROUP BY a.suchbegriff
            ORDER BY passend DESC, treffer DESC
            """,
            (min_score,),
        )
        ergebnis = [dict(r) for r in rows]
        # Treffer des jeweils letzten Abrufs je Begriff (fuer "liefert nichts mehr")
        for zeile in ergebnis:
            letzte = self.con.execute(
                "SELECT COALESCE(SUM(treffer), 0) FROM abrufe WHERE suchbegriff = ? "
                "AND gestartet_am = ?",
                (zeile["begriff"], zeile["zuletzt"]),
            ).fetchone()[0]
            zeile["letzte_treffer"] = int(letzte or 0)
        return ergebnis

    # ---------------- Vorgaenge ----------------

    def setze_vorgang(self, vorgang: Vorgang) -> None:
        vorgang.aktualisiert_am = _jetzt()
        d = vorgang.as_dict()
        spalten = ", ".join(d)
        platzhalter = ", ".join(f":{k}" for k in d)
        updates = ", ".join(f"{k}=excluded.{k}" for k in d if k not in ("ref", "erstellt_am"))
        self.con.execute(
            f"INSERT INTO vorgaenge ({spalten}) VALUES ({platzhalter}) "
            f"ON CONFLICT(ref) DO UPDATE SET {updates}",
            d,
        )
        self.con.commit()

    def aktualisiere_vorgang(self, ref: str, **felder: Any) -> None:
        erlaubt = set(Vorgang.__dataclass_fields__) - {"ref", "erstellt_am"}
        unbekannt = set(felder) - erlaubt
        if unbekannt:
            raise ValueError(f"Unbekannte Vorgangsfelder: {sorted(unbekannt)}")
        felder["aktualisiert_am"] = _jetzt()
        zuweisung = ", ".join(f"{k} = :{k}" for k in felder)
        self.con.execute(f"UPDATE vorgaenge SET {zuweisung} WHERE ref = :ref", {**felder, "ref": ref})
        self.con.commit()

    def vorgang(self, ref: str) -> Vorgang | None:
        row = self.con.execute("SELECT * FROM vorgaenge WHERE ref = ?", (ref,)).fetchone()
        return _zu_vorgang(row) if row else None

    def vorgaenge(self, status: str | Iterable[str] | None = None) -> list[Vorgang]:
        sql = "SELECT * FROM vorgaenge"
        params: list[Any] = []
        if status:
            liste = [status] if isinstance(status, str) else list(status)
            sql += f" WHERE status IN ({', '.join('?' for _ in liste)})"
            params = liste
        sql += " ORDER BY aktualisiert_am DESC"
        return [_zu_vorgang(r) for r in self.con.execute(sql, params)]

    def loesche_vorgang(self, ref: str) -> None:
        self.con.execute("DELETE FROM vorgaenge WHERE ref = ?", (ref,))
        self.con.commit()

    def beanspruche_zum_senden(self, ref: str) -> bool:
        """Atomar: nur ein Worker darf eine freigegebene Bewerbung senden."""
        cur = self.con.execute(
            "UPDATE vorgaenge SET status = 'wird_gesendet', aktualisiert_am = ? "
            "WHERE ref = ? AND status = 'freigegeben'",
            (_jetzt(), ref),
        )
        self.con.commit()
        return cur.rowcount == 1

    def haengende_vorgaenge(self, status: str, minuten: int = 30) -> list[Vorgang]:
        grenze = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat(timespec="seconds")
        rows = self.con.execute(
            "SELECT * FROM vorgaenge WHERE status = ? AND aktualisiert_am < ?", (status, grenze)
        )
        return [_zu_vorgang(r) for r in rows]

    def beworben_seit(self, zeitpunkt_iso: str, status: Iterable[str] = BEWORBEN_STATUS) -> int:
        """Anzahl Bewerbungen mit diesem Status seit dem Zeitpunkt (UTC-ISO)."""
        status = list(status)
        platzhalter = ", ".join("?" for _ in status)
        return int(self.con.execute(
            f"SELECT COUNT(*) FROM vorgaenge WHERE status IN ({platzhalter}) "
            "AND COALESCE(gesendet_am, aktualisiert_am) >= ?",
            (*status, zeitpunkt_iso),
        ).fetchone()[0])

    def kandidaten(
        self,
        min_score: float = 0.0,
        limit: int = 20,
        refs: Iterable[str] | None = None,
        ausschluss_arbeitgeber: Iterable[str] = (),
    ) -> list[tuple[Job, Score]]:
        """Bestbewertete Stellen ohne Vorgang/Bewerbung, Dubletten zusammengefasst."""
        sql = """
            SELECT j.*, s.gesamt, s.passung, s.sentiment, s.treffer, s.gruen, s.rot,
                   s.begruendung, s.ausgeschlossen, s.ausschlussgrund, s.bewerter, s.bewertet_am
            FROM jobs j JOIN scores s ON s.ref = j.ref
            WHERE s.ausgeschlossen = 0 AND s.gesamt >= ?
              AND j.ref NOT IN (SELECT ref FROM vorgaenge)
              AND j.ref NOT IN (SELECT ref FROM applications
                                WHERE status IN ('abgeschickt', 'manuell_beworben', 'uebersprungen'))
        """
        params: list[Any] = [min_score]
        if refs is not None:
            refs = list(dict.fromkeys(refs))
            if not refs:
                return []
            sql += f" AND j.ref IN ({', '.join('?' for _ in refs)})"
            params += refs
        sql += " ORDER BY s.gesamt DESC"
        gesperrt = {a.strip().casefold() for a in ausschluss_arbeitgeber if a.strip()}
        bekannt = {
            dubletten_schluessel(r["arbeitgeber"], r["titel"])
            for r in self.con.execute(
                "SELECT j.arbeitgeber, j.titel FROM vorgaenge v JOIN jobs j ON j.ref = v.ref"
            )
        }
        ergebnis: list[tuple[Job, Score]] = []
        for row in self.con.execute(sql, params):
            job = _zu_job(row)
            if gesperrt and (job.arbeitgeber or "").strip().casefold() in gesperrt:
                continue
            schluessel = dubletten_schluessel(job.arbeitgeber, job.titel)
            if schluessel in bekannt:
                continue
            bekannt.add(schluessel)
            ergebnis.append((job, _zu_score(row)))
            if len(ergebnis) >= limit:
                break
        return ergebnis

    # ---------------- Laeufe ----------------

    def starte_lauf(self, art: str, ausloeser: str) -> int:
        cur = self.con.execute(
            "INSERT INTO laeufe (art, ausloeser, gestartet_am, status) VALUES (?, ?, ?, 'laeuft')",
            (art, ausloeser, _jetzt()),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def beende_lauf(
        self, lauf_id: int, zusammenfassung: dict[str, Any] | None = None, fehler: str | None = None,
    ) -> None:
        self.con.execute(
            "UPDATE laeufe SET beendet_am = ?, status = ?, zusammenfassung = ?, fehler = ? WHERE id = ?",
            (
                _jetzt(),
                "fehler" if fehler else "erfolgreich",
                json.dumps(zusammenfassung or {}, ensure_ascii=False),
                fehler,
                lauf_id,
            ),
        )
        self.con.commit()

    def letzte_laeufe(self, art: str | Iterable[str] | None = None, limit: int = 10) -> list[dict[str, Any]]:
        sql = "SELECT * FROM laeufe"
        params: list[Any] = []
        if art:
            arten = [art] if isinstance(art, str) else list(art)
            sql += f" WHERE art IN ({', '.join('?' for _ in arten)})"
            params = arten
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        ergebnis = []
        for row in self.con.execute(sql, params):
            d = dict(row)
            d["zusammenfassung"] = json.loads(d["zusammenfassung"] or "{}")
            ergebnis.append(d)
        return ergebnis

    def laufende_laeufe(self, art: str | Iterable[str] | None = None, minuten: int = 120) -> list[dict[str, Any]]:
        """Laeufe mit Status 'laeuft', die juenger als `minuten` sind (aeltere gelten als abgestuerzt)."""
        grenze = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat(timespec="seconds")
        return [
            l for l in self.letzte_laeufe(art, limit=20)
            if l["status"] == "laeuft" and l["gestartet_am"] >= grenze
        ]


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


def _zu_vorgang(row: sqlite3.Row) -> Vorgang:
    felder = set(Vorgang.__dataclass_fields__)
    daten = {k: row[k] for k in row.keys() if k in felder}
    daten["rueckmeldung"] = daten.get("rueckmeldung") or ""
    daten["kanal"] = daten.get("kanal") or "manuell"
    daten["herkunft"] = daten.get("herkunft") or "manuell"
    return Vorgang(**daten)
