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

-- Dauerhaftes Protokoll jeder Entscheidung des Nutzers (Lernen aus Ja/Nein)
CREATE TABLE IF NOT EXISTS entscheidungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeitpunkt TEXT NOT NULL,
    ref TEXT NOT NULL,
    art TEXT NOT NULL,          -- ja | nein | nicht_interessant | selbst | bearbeitet | autopilot
    gruende TEXT,               -- JSON-Liste
    titel TEXT,
    arbeitgeber TEXT,
    ort TEXT,
    quelle TEXT,
    score REAL,
    details TEXT                -- JSON
);
CREATE INDEX IF NOT EXISTS idx_entscheidungen_art ON entscheidungen(art);

-- Weitere Ja/Nein-Fragen des Agenten (Nachfassen, Profil-/Suchvorschlaege ...)
CREATE TABLE IF NOT EXISTS vorschlaege (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    art TEXT NOT NULL,          -- nachfassen | ausschluss | suchbegriff | umkreis | stil | ...
    ref TEXT,
    schluessel TEXT NOT NULL,   -- verhindert doppelte Vorschlaege
    titel TEXT NOT NULL,
    text TEXT,
    daten TEXT,                 -- JSON
    status TEXT NOT NULL,       -- offen | angenommen | abgelehnt | erledigt | fehler
    erstellt_am TEXT NOT NULL,
    entschieden_am TEXT,
    hinweis TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vorschlaege_schluessel ON vorschlaege(schluessel);

-- Suchbegriffe mit Ertrag (selbststaendige Erkundung)
CREATE TABLE IF NOT EXISTS suchbegriffe (
    begriff TEXT PRIMARY KEY,
    herkunft TEXT NOT NULL,     -- profil | erkundung | nutzer
    status TEXT NOT NULL,       -- aktiv | test | verworfen
    versuche INTEGER NOT NULL DEFAULT 0,
    treffer INTEGER NOT NULL DEFAULT 0,
    passend INTEGER NOT NULL DEFAULT 0,
    grund TEXT,
    erstellt_am TEXT NOT NULL,
    zuletzt TEXT
);

-- Ausgewertete E-Mails aus dem Postfach (idempotent ueber uid_key)
CREATE TABLE IF NOT EXISTS nachrichten (
    uid_key TEXT PRIMARY KEY,
    message_id TEXT,
    datum TEXT,
    absender TEXT,
    betreff TEXT,
    ref TEXT,
    art TEXT,                   -- eingangsbestaetigung | einladung | absage | zusage | rueckfrage | sonstiges
    konfidenz REAL,
    auszug TEXT,
    verarbeitet_am TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nachrichten_ref ON nachrichten(ref);
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
    "dubletten_schluessel": "ALTER TABLE jobs ADD COLUMN dubletten_schluessel TEXT",
}


def _spaltentyp(typ: Any) -> str:
    text = str(typ)
    if "float" in text:
        return "REAL"
    if "bool" in text or text.startswith("int"):
        return "INTEGER"
    return "TEXT"


# Vorgaenge in diesen Status gelten als "erledigt beworben" (Wochenziel, Nachweis).
BEWORBEN_STATUS = ("abgeschickt", "selbst_beworben")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Stellen, die nur als Rohbestand eines Feeds/einer Karriereseite gespeichert sind
# (Abruf "(Feed)", "(Firma) ..."), aber zu keiner Suche passten, sind keine Kandidaten:
# Sie koennen an ganz anderen Orten oder in anderen Berufen liegen.
SUCHTREFFER = (
    "(NOT EXISTS (SELECT 1 FROM abruf_treffer t JOIN abrufe a ON a.id = t.abruf_id "
    "WHERE t.ref = j.ref AND a.suchbegriff LIKE '(%') "
    "OR EXISTS (SELECT 1 FROM abruf_treffer t JOIN abrufe a ON a.id = t.abruf_id "
    "WHERE t.ref = j.ref AND a.suchbegriff NOT LIKE '(%'))"
)


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
        self._migriere_dataclass("jobs", Job)
        self._migriere_dataclass("vorgaenge", Vorgang)
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dubletten ON jobs(dubletten_schluessel)")
        self.con.commit()

    def _migriere_jobs_spalten(self) -> None:
        vorhanden = {row["name"] for row in self.con.execute("PRAGMA table_info(jobs)")}
        for spalte, ddl in JOBS_MIGRATIONS.items():
            if spalte not in vorhanden:
                self.con.execute(ddl)

    def _migriere_dataclass(self, tabelle: str, klasse: Any) -> None:
        """Neue Dataclass-Felder als Spalten ergaenzen (bestehende Datenbanken bleiben nutzbar)."""
        vorhanden = {row["name"] for row in self.con.execute(f"PRAGMA table_info({tabelle})")}
        for name, feld in klasse.__dataclass_fields__.items():
            if name not in vorhanden:
                self.con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {_spaltentyp(feld.type)}")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Speicher":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------- Jobs ----------------

    def speichere_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Fuegt Jobs ein bzw. aktualisiert sie. Gibt (neu, aktualisiert) zurueck.

        Leere Werte eines erneuten Abrufs (z. B. ohne Detailseite) ueberschreiben
        vorhandene Beschreibung/Anreicherung nicht; die erste Quelle bleibt erhalten.
        """
        neu = aktualisiert = 0
        for job in jobs:
            vorhanden = self.con.execute(
                "SELECT 1 FROM jobs WHERE ref = ?", (job.ref,)
            ).fetchone()
            d = job.as_dict()
            for k, v in d.items():
                if v == "" and k not in ("ref", "titel", "arbeitgeber"):
                    d[k] = None
            d["details_quelle"] = d.get("details_quelle") or None
            d["dubletten_schluessel"] = dubletten_schluessel(job.arbeitgeber, job.titel)
            spalten = ", ".join(d)
            platzhalter = ", ".join(f":{k}" for k in d)

            def neuer_wert(k: str) -> str:
                if k == "geholt_am":
                    return f"{k}=excluded.{k}"
                if k == "quelle":
                    return f"{k}=COALESCE(jobs.{k}, excluded.{k})"
                return f"{k}=COALESCE(excluded.{k}, jobs.{k})"

            updates = ", ".join(neuer_wert(k) for k in d if k != "ref")
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
            sql += f" LEFT JOIN scores s ON s.ref = j.ref WHERE s.ref IS NULL AND {SUCHTREFFER}"
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
        sql += f" AND {SUCHTREFFER}"
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
            WHERE a.status = 'erfolgreich' AND a.suchbegriff NOT LIKE '(%'
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
        sql += f" AND {SUCHTREFFER}"
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
        heute = datetime.now(timezone.utc).date().isoformat()
        for row in self.con.execute(sql, params):
            job = _zu_job(row)
            if gesperrt and (job.arbeitgeber or "").strip().casefold() in gesperrt:
                continue
            frist = (job.bewerbungsfrist or "")[:10]
            if len(frist) == 10 and frist[4] == "-" and frist < heute:
                continue  # Bewerbungsfrist abgelaufen
            schluessel = dubletten_schluessel(job.arbeitgeber, job.titel)
            if schluessel in bekannt:
                continue
            bekannt.add(schluessel)
            ergebnis.append((job, _zu_score(row)))
            if len(ergebnis) >= limit:
                break
        return ergebnis

    def heuristisch_bewertet(self, min_gesamt: float = 0.0, limit: int = 20) -> list[str]:
        """Offene Stellen, die bisher nur per Stichworten bewertet wurden (neueste zuerst)."""
        rows = self.con.execute(
            f"""
            SELECT s.ref FROM scores s JOIN jobs j ON j.ref = s.ref
            WHERE s.bewerter = 'heuristik' AND s.ausgeschlossen = 0 AND s.gesamt >= ?
              AND j.ref NOT IN (SELECT ref FROM vorgaenge) AND {SUCHTREFFER}
            ORDER BY j.geholt_am DESC, s.gesamt DESC LIMIT ?
            """,
            (min_gesamt, limit),
        )
        return [r["ref"] for r in rows]

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

    # ---------------- Quellen-Hilfen ----------------

    def letzter_abruf(self, quelle: str, suchbegriff: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM abrufe WHERE quelle = ? AND status = 'erfolgreich'"
        params: list[Any] = [quelle]
        if suchbegriff is not None:
            sql += " AND suchbegriff = ?"
            params.append(suchbegriff)
        row = self.con.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None

    def letzter_abruf_stand(self, quelle: str) -> dict[str, Any] | None:
        """Letzter Abruf einer Quelle, egal ob erfolgreich (fuer die Quellenuebersicht)."""
        row = self.con.execute(
            "SELECT * FROM abrufe WHERE quelle = ? AND status != 'laeuft' ORDER BY id DESC LIMIT 1", (quelle,)
        ).fetchone()
        return dict(row) if row else None

    def jobs_der_quelle(self, quelle: str, seit: str | None = None, praefix: str | None = None) -> list[Job]:
        """Gespeicherte Stellen einer Quelle (optional nur ab Veroeffentlichungsdatum JJJJ-MM-TT)."""
        sql = "SELECT * FROM jobs WHERE quelle = ?"
        params: list[Any] = [quelle]
        if praefix:
            sql += " AND ref LIKE ?"
            params.append(praefix + "%")
        if seit:
            sql += " AND COALESCE(substr(veroeffentlicht, 1, 10), substr(geholt_am, 1, 10)) >= ?"
            params.append(seit)
        return [_zu_job(r) for r in self.con.execute(sql + " ORDER BY veroeffentlicht DESC", params)]

    def orte_im_umkreis(self, umkreis_km: float) -> set[str]:
        """Orte, die die Arbeitsagentur fuer die Suche als im Umkreis liegend gemeldet hat."""
        rows = self.con.execute(
            "SELECT DISTINCT ort FROM jobs WHERE quelle = 'arbeitsagentur' AND ort IS NOT NULL "
            "AND entfernung_km IS NOT NULL AND entfernung_km <= ?",
            (umkreis_km,),
        )
        return {r["ort"] for r in rows}

    def score_einer_dublette(self, job: Job) -> Score | None:
        """Vorhandene Bewertung derselben Stelle aus einer anderen Quelle (spart LLM-Aufrufe)."""
        row = self.con.execute(
            "SELECT s.* FROM scores s JOIN jobs j ON j.ref = s.ref "
            "WHERE j.dubletten_schluessel = ? AND j.ref != ? ORDER BY s.bewertet_am DESC LIMIT 1",
            (dubletten_schluessel(job.arbeitgeber, job.titel), job.ref),
        ).fetchone()
        return _zu_score(row) if row else None

    # ---------------- Entscheidungen (Lernen) ----------------

    def protokolliere_entscheidung(
        self, ref: str, art: str, gruende: Iterable[str] = (), details: dict[str, Any] | None = None,
    ) -> None:
        job = self.job(ref)
        score = self.score(ref)
        self.con.execute(
            "INSERT INTO entscheidungen (zeitpunkt, ref, art, gruende, titel, arbeitgeber, ort, quelle, score, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _jetzt(), ref, art, json.dumps(list(gruende), ensure_ascii=False),
                job.titel if job else None, job.arbeitgeber if job else None, job.ort if job else None,
                job.quelle if job else None, score.gesamt if score else None,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        self.con.commit()

    def entscheidungen(self, art: str | Iterable[str] | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM entscheidungen"
        params: list[Any] = []
        if art:
            arten = [art] if isinstance(art, str) else list(art)
            sql += f" WHERE art IN ({', '.join('?' for _ in arten)})"
            params = arten
        rows = self.con.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
        ergebnis = []
        for r in rows:
            d = dict(r)
            d["gruende"] = json.loads(d["gruende"] or "[]")
            d["details"] = json.loads(d["details"] or "{}")
            ergebnis.append(d)
        return ergebnis

    # ---------------- Vorschlaege (weitere Ja/Nein-Fragen) ----------------

    def lege_vorschlag_an(
        self, art: str, schluessel: str, titel: str, text: str = "",
        daten: dict[str, Any] | None = None, ref: str | None = None,
    ) -> bool:
        """Legt einen Vorschlag an; liefert False, wenn es ihn (auch erledigt) schon gab."""
        cur = self.con.execute(
            "INSERT OR IGNORE INTO vorschlaege (art, ref, schluessel, titel, text, daten, status, erstellt_am) "
            "VALUES (?, ?, ?, ?, ?, ?, 'offen', ?)",
            (art, ref, schluessel, titel, text, json.dumps(daten or {}, ensure_ascii=False), _jetzt()),
        )
        self.con.commit()
        return cur.rowcount == 1

    def vorschlag_existiert(self, schluessel: str) -> bool:
        return self.con.execute("SELECT 1 FROM vorschlaege WHERE schluessel = ?", (schluessel,)).fetchone() is not None

    def vorschlaege(self, status: str | Iterable[str] | None = "offen") -> list[dict[str, Any]]:
        sql = "SELECT * FROM vorschlaege"
        params: list[Any] = []
        if status:
            liste = [status] if isinstance(status, str) else list(status)
            sql += f" WHERE status IN ({', '.join('?' for _ in liste)})"
            params = liste
        ergebnis = []
        for r in self.con.execute(sql + " ORDER BY id", params):
            d = dict(r)
            d["daten"] = json.loads(d["daten"] or "{}")
            ergebnis.append(d)
        return ergebnis

    def vorschlag(self, vorschlag_id: int) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM vorschlaege WHERE id = ?", (vorschlag_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["daten"] = json.loads(d["daten"] or "{}")
        return d

    def setze_vorschlag(self, vorschlag_id: int, status: str, hinweis: str | None = None,
                        daten: dict[str, Any] | None = None) -> None:
        felder = "status = ?, entschieden_am = ?, hinweis = ?"
        params: list[Any] = [status, _jetzt(), hinweis]
        if daten is not None:
            felder += ", daten = ?"
            params.append(json.dumps(daten, ensure_ascii=False))
        self.con.execute(f"UPDATE vorschlaege SET {felder} WHERE id = ?", (*params, vorschlag_id))
        self.con.commit()

    # ---------------- Suchbegriffe (Erkundung) ----------------

    def suchbegriff(self, begriff: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM suchbegriffe WHERE begriff = ?", (begriff,)).fetchone()
        return dict(row) if row else None

    def merke_suchbegriff(self, begriff: str, herkunft: str, status: str, grund: str | None = None) -> None:
        self.con.execute(
            "INSERT INTO suchbegriffe (begriff, herkunft, status, grund, erstellt_am) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(begriff) DO UPDATE SET status = excluded.status, grund = COALESCE(excluded.grund, suchbegriffe.grund)",
            (begriff, herkunft, status, grund, _jetzt()),
        )
        self.con.commit()

    def suchbegriff_ertrag(self, begriff: str, treffer: int, passend: int) -> None:
        self.con.execute(
            "UPDATE suchbegriffe SET versuche = versuche + 1, treffer = treffer + ?, passend = passend + ?, "
            "zuletzt = ? WHERE begriff = ?",
            (treffer, passend, _jetzt(), begriff),
        )
        self.con.commit()

    def suchbegriffe(self, status: str | Iterable[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM suchbegriffe"
        params: list[Any] = []
        if status:
            liste = [status] if isinstance(status, str) else list(status)
            sql += f" WHERE status IN ({', '.join('?' for _ in liste)})"
            params = liste
        return [dict(r) for r in self.con.execute(sql + " ORDER BY passend DESC, begriff", params)]

    # ---------------- Postfach ----------------

    def nachricht_bekannt(self, uid_key: str) -> bool:
        return self.con.execute("SELECT 1 FROM nachrichten WHERE uid_key = ?", (uid_key,)).fetchone() is not None

    def speichere_nachricht(self, **felder: Any) -> None:
        felder.setdefault("verarbeitet_am", _jetzt())
        spalten = ", ".join(felder)
        platzhalter = ", ".join(f":{k}" for k in felder)
        self.con.execute(f"INSERT OR IGNORE INTO nachrichten ({spalten}) VALUES ({platzhalter})", felder)
        self.con.commit()

    def nachrichten(self, ref: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if ref:
            rows = self.con.execute(
                "SELECT * FROM nachrichten WHERE ref = ? ORDER BY datum DESC LIMIT ?", (ref, limit))
        else:
            rows = self.con.execute("SELECT * FROM nachrichten ORDER BY datum DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def _zu_job(row: sqlite3.Row) -> Job:
    felder = set(Job.__dataclass_fields__)
    daten = {k: row[k] for k in row.keys() if k in felder}
    daten["details_quelle"] = daten.get("details_quelle") or "keine"
    daten["details_generiert_am"] = daten.get("details_generiert_am") or ""
    daten["quelle"] = daten.get("quelle") or ""
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
