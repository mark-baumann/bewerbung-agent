"""Lange Arbeiten (Probelauf, Versand, Automatiklauf) ausserhalb der UI ausfuehren.

Die Streamlit-UI startet dafuer einen eigenen CLI-Prozess, der die Umgebung
(API-/SMTP-Keys) erbt und unabhaengig von der Browsersitzung weiterlaeuft.
Dateisperren verhindern, dass Cron und UI dieselbe Arbeit doppelt erledigen.
"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .db import STANDARD_DB

DATEN_DIR = STANDARD_DB.parent
WORKER_LOG = DATEN_DIR / "worker.log"


@contextmanager
def sperre(name: str, verzeichnis: str | Path | None = None, warten: bool = False) -> Iterator[bool]:
    """Dateisperre. Liefert False, wenn sie schon gehalten wird (oder wartet mit `warten=True`)."""
    pfad = Path(verzeichnis or DATEN_DIR) / f"{name}.lock"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: ohne Sperre weiterarbeiten
        yield True
        return
    with open(pfad, "w") as datei:
        try:
            fcntl.flock(datei.fileno(), fcntl.LOCK_EX | (0 if warten else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(datei.fileno(), fcntl.LOCK_UN)


def starte(*argumente: str) -> int:
    """Startet `bewerbungsagent <argumente>` als losgeloesten Prozess; liefert die PID."""
    WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    befehl = [sys.executable, "-m", "bewerbungsagent.cli", *argumente]
    with open(WORKER_LOG, "a", encoding="utf-8") as log:
        prozess = subprocess.Popen(
            befehl,
            cwd=os.getcwd(),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return prozess.pid


def letzte_logzeilen(anzahl: int = 40) -> str:
    try:
        zeilen = WORKER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(zeilen[-anzahl:])
