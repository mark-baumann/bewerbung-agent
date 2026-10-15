"""Kommandozeile des Bewerbungsagenten."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import textwrap
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import ablauf
from .anschreiben import erzeuge as erzeuge_anschreiben
from .config import lade_env, lade_profil
from .db import Speicher
from .hintergrund import sperre
from .models import Application
from .scoring import llm as llm_modul
from .sources import QUELLEN

console = Console()


# --------------------------------------------------------------------------- #
# suchen
# --------------------------------------------------------------------------- #

def cmd_suchen(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        ergebnis = ablauf.suche_ausfuehren(
            db, profil,
            begriffe=args.was, wo=args.wo, umkreis=args.umkreis, tage=args.tage,
            max_treffer=args.limit, quellen=args.quelle, mit_details=not args.ohne_details,
            fortschritt=lambda text, _anteil: console.print(f"[cyan]{text}[/]"),
        )
    for fehler in ergebnis.fehler:
        console.print(f"[red]Suche fehlgeschlagen:[/] {fehler}")
    for begriff, anzahl in ergebnis.je_begriff.items():
        console.print(f"  {begriff}: {anzahl} Treffer")
    if not ergebnis.refs:
        console.print("[yellow]Keine Stellen gefunden.[/]")
        return 1
    console.print(f"[green]{ergebnis.neu} neue[/] und {ergebnis.aktualisiert} aktualisierte Stellen "
                  f"gespeichert ({len(ergebnis.refs)} eindeutige Treffer).")
    return 0


# --------------------------------------------------------------------------- #
# bewerten
# --------------------------------------------------------------------------- #

def cmd_bewerten(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        mit_llm = not args.ohne_llm
        if mit_llm and not llm_modul.client_verfuegbar():
            console.print("[yellow]Kein LLM-API-Key gesetzt (ANTHROPIC_API_KEY/OPENAI_API_KEY/OLLAMA_API_KEY) - bewerte rein heuristisch.[/]")
            mit_llm = False

        scores = ablauf.bewerte_neue(db, profil, alle=args.alle, mit_llm=mit_llm, limit=args.limit)
        if not scores:
            console.print("[yellow]Nichts zu bewerten. Erst 'suchen' ausfuehren.[/]")
            return 1
        console.print(f"{len(scores)} Stellen bewertet ({'LLM + Heuristik' if mit_llm else 'Heuristik'}).")

        ausgeschlossen = sum(1 for s in scores if s.ausgeschlossen)
        ueber_schwelle = sum(
            1 for s in scores if not s.ausgeschlossen and s.gesamt >= profil.bewertung.min_score
        )
    console.print(
        f"[green]Fertig.[/] {ueber_schwelle} ueber Schwelle "
        f"({profil.bewertung.min_score}), {ausgeschlossen} ausgeschlossen."
    )
    return 0


# --------------------------------------------------------------------------- #
# top
# --------------------------------------------------------------------------- #

def cmd_top(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    min_score = args.min if args.min is not None else profil.bewertung.min_score
    with Speicher(args.db) as db:
        treffer = db.bestenliste(min_score=min_score, limit=args.limit, offen=args.offen)

    if not treffer:
        console.print("[yellow]Keine Stelle ueber der Schwelle. 'bewerten' ausfuehren "
                      "oder --min senken.[/]")
        return 1

    tabelle = Table(title=f"Beste Treffer (min {min_score})", show_lines=False)
    tabelle.add_column("Score", justify="right", style="bold")
    tabelle.add_column("Pass", justify="right")
    tabelle.add_column("Ton", justify="right")
    tabelle.add_column("Titel", max_width=40, overflow="ellipsis")
    tabelle.add_column("Arbeitgeber", max_width=22, overflow="ellipsis")
    tabelle.add_column("Ort", max_width=14, overflow="ellipsis")
    # Referenz muss vollstaendig lesbar bleiben - sie ist die Eingabe der
    # Folgebefehle 'zeigen' und 'bewerben'.
    tabelle.add_column("Referenz", style="dim", no_wrap=True, overflow="fold")

    for job, score in treffer:
        tabelle.add_row(
            f"{score.gesamt:.0f}",
            f"{score.passung:.0f}",
            _ton_farbe(score.sentiment),
            job.titel,
            job.arbeitgeber or "-",
            job.ort or "-",
            job.ref,
        )
    console.print(tabelle)
    console.print("[dim]Details: bewerbungsagent zeigen <Referenz>[/]")
    return 0


def _ton_farbe(wert: float) -> str:
    if wert >= 65:
        return f"[green]{wert:.0f}[/]"
    if wert >= 45:
        return f"{wert:.0f}"
    return f"[red]{wert:.0f}[/]"


# --------------------------------------------------------------------------- #
# zeigen
# --------------------------------------------------------------------------- #

def cmd_zeigen(args: argparse.Namespace) -> int:
    with Speicher(args.db) as db:
        job = db.job(args.ref)
        if not job:
            console.print(f"[red]Unbekannte Referenz:[/] {args.ref}")
            return 1
        score = db.score(job.ref)
        bewerbungen = db.bewerbungen(job.ref)

    kopf = [
        f"[bold]{job.titel}[/]",
        f"{job.arbeitgeber or '-'} | {job.ort or '-'} {f'({job.entfernung_km:.0f} km)' if job.entfernung_km is not None else ''}",
        f"Veroeffentlicht: {job.veroeffentlicht or '-'} | Eintritt: {job.eintrittsdatum or '-'}",
        f"Vertrag: {job.befristung or '-'} | Homeoffice: {job.homeoffice} | Verguetung: {job.verguetung or '-'}",
        f"Bewerbung: {job.bewerbungs_url or '-'}",
    ]
    console.print(Panel("\n".join(kopf), title=job.ref))

    if score:
        zeilen = [
            f"Gesamt [bold]{score.gesamt:.0f}[/] (Passung {score.passung:.0f}, Ton {score.sentiment:.0f}) "
            f"- {score.bewerter}",
            f"Begruendung: {score.begruendung}",
        ]
        if score.treffer:
            zeilen.append(f"[green]Treffer:[/] {', '.join(score.treffer[:12])}")
        if score.gruen:
            zeilen.append(f"[green]Positiv:[/] {', '.join(score.gruen[:10])}")
        if score.rot:
            zeilen.append(f"[red]Warnsignale:[/] {', '.join(score.rot[:10])}")
        if score.ausgeschlossen:
            zeilen.append(f"[red]AUSGESCHLOSSEN:[/] {score.ausschlussgrund}")
        console.print(Panel("\n".join(zeilen), title="Bewertung"))

    if bewerbungen:
        t = Table(title="Bewerbungsverlauf")
        for spalte in ("Zeitpunkt", "Status", "Schritte", "Ergebnis"):
            t.add_column(spalte)
        for b in bewerbungen:
            t.add_row(b["zeitpunkt"], b["status"], str(b["schritte"]),
                      textwrap.shorten(b["ergebnis"] or "", 80))
        console.print(t)

    if job.beschreibung and not args.kurz:
        console.print(Panel(job.beschreibung[:4000], title="Anzeigentext"))
    return 0


# --------------------------------------------------------------------------- #
# anschreiben
# --------------------------------------------------------------------------- #

def cmd_anschreiben(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        job = db.job(args.ref)
        if not job:
            console.print(f"[red]Unbekannte Referenz:[/] {args.ref}")
            return 1
        score = db.score(job.ref)

    text = erzeuge_anschreiben(job, profil, score, mit_llm=not args.ohne_llm)
    if args.ausgabe:
        Path(args.ausgabe).write_text(text, encoding="utf-8")
        console.print(f"[green]Gespeichert:[/] {args.ausgabe}")
    else:
        console.print(Panel(text, title=f"Anschreiben - {job.titel}"))
    return 0


# --------------------------------------------------------------------------- #
# bewerben
# --------------------------------------------------------------------------- #

def cmd_bewerben(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)

    fehlend = profil.fehlende_anhaenge()
    if fehlend:
        console.print(f"[yellow]Warnung: Unterlagen nicht gefunden:[/] {', '.join(fehlend)}")

    with Speicher(args.db) as db:
        if args.ref:
            job = db.job(args.ref)
            if not job:
                console.print(f"[red]Unbekannte Referenz:[/] {args.ref}")
                return 1
            aufgaben = [(job, db.score(job.ref))]
        else:
            min_score = args.min if args.min is not None else profil.bewertung.min_score
            aufgaben = db.bestenliste(min_score=min_score, limit=args.top, offen=True)
            if not aufgaben:
                console.print("[yellow]Keine offenen Stellen ueber der Schwelle.[/]")
                return 1

        absenden = args.absenden
        modus = "[red bold]ECHTES ABSENDEN[/]" if absenden else "[cyan]Probelauf (kein Absenden)[/]"
        console.print(f"Modus: {modus} | {len(aufgaben)} Stelle(n)")

        if absenden and not args.ja:
            console.print("\n".join(f"  - {j.titel} @ {j.arbeitgeber} ({j.ref})" for j, _ in aufgaben))
            antwort = console.input(
                "[bold red]Bewerbungen wirklich verbindlich absenden? [ja/nein]: [/]"
            )
            if antwort.strip().lower() not in ("ja", "j", "yes", "y"):
                console.print("Abgebrochen.")
                return 130

        try:
            from .bewerben.browser import BewerbungsBrowser, BrowserUseFehlt
        except Exception as e:
            console.print(f"[red]{e}[/]")
            return 2

        try:
            browser = BewerbungsBrowser(
                profil=profil,
                headless=args.headless,
                max_schritte=args.schritte,
                domains_offen=args.domains_offen,
                aufzeichnung=args.aufzeichnung,
            )
        except BrowserUseFehlt as e:
            console.print(f"[red]{e}[/]")
            return 2

        fehler = 0
        for job, score in aufgaben:
            if not job.bewerbungs_url:
                console.print(f"[yellow]Ueberspringe {job.ref}: keine Bewerbungs-URL.[/]")
                db.speichere_bewerbung(
                    Application(ref=job.ref, status="uebersprungen",
                                ergebnis="keine Bewerbungs-URL", dry_run=not absenden)
                )
                continue

            console.print(Panel(f"{job.titel}\n{job.arbeitgeber} | {job.ort}\n{job.bewerbungs_url}",
                                title=f"Bewerbung {job.ref}"))
            text = erzeuge_anschreiben(job, profil, score, mit_llm=not args.ohne_llm)
            console.print(Panel(text, title="Anschreiben", style="dim"))

            try:
                lauf = asyncio.run(browser.bewerbe(job, text, dry_run=not absenden))
            except KeyboardInterrupt:
                console.print("[yellow]Abgebrochen.[/]")
                return 130
            except Exception as e:
                fehler += 1
                console.print(f"[red]Browser-Agent fehlgeschlagen:[/] {e}")
                db.speichere_bewerbung(
                    Application(ref=job.ref, status="fehlgeschlagen", url=job.bewerbungs_url,
                                anschreiben=text, ergebnis=str(e)[:1000], dry_run=not absenden)
                )
                continue

            app = lauf.als_application(text)
            db.speichere_bewerbung(app)
            farbe = "green" if lauf.abgeschickt else ("cyan" if lauf.dry_run else "red")
            console.print(f"[{farbe}]{app.status}[/] nach {lauf.schritte} Schritten: "
                          f"{lauf.zusammenfassung}")

    return 1 if fehler else 0


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def cmd_status(args: argparse.Namespace) -> int:
    with Speicher(args.db) as db:
        stat = db.statistik()
        letzte = db.bewerbungen()[: args.limit]

    t = Table(title="Bestand")
    t.add_column("Kennzahl")
    t.add_column("Wert", justify="right")
    for k, v in stat.items():
        t.add_row(k, str(v))
    console.print(t)

    if letzte:
        t2 = Table(title="Letzte Bewerbungsvorgaenge")
        for spalte in ("Zeitpunkt", "Referenz", "Status", "Ergebnis"):
            t2.add_column(spalte)
        for b in letzte:
            t2.add_row(b["zeitpunkt"], b["ref"], b["status"],
                       textwrap.shorten(b["ergebnis"] or "", 70))
        console.print(t2)
    return 0


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #

def cmd_pipeline(args: argparse.Namespace) -> int:
    such_limit = args.limit
    code = cmd_suchen(args)
    if code != 0:
        return code
    args.limit = None          # alles Unbewertete bewerten
    cmd_bewerten(args)
    args.limit = 20            # Anzeigelimit der Bestenliste
    try:
        return cmd_top(args)
    finally:
        args.limit = such_limit


def cmd_cron(args: argparse.Namespace) -> int:
    """Gibt eine sichere, installierbare Cron-Zeile aus (keine Crontab-Aenderung).

    Cron ruft alle 5 Minuten `tick` auf; ob der im Profil hinterlegte Zeitplan
    faellig ist, entscheidet `tick` selbst. So greifen Aenderungen aus der UI sofort.
    """
    profil_obj = lade_profil(args.profil)
    if profil_obj.automatisierung.effektiver_modus == "aus":
        console.print("[yellow]Automatisierung ist im Profil ausgeschaltet (automatisierung.modus: aus).[/]")
        return 1
    executable = args.executable or "bewerbungsagent"
    profil = f" --profil {args.profil}" if args.profil else ""
    db = f" --db {args.db}" if args.db else ""
    console.print("Cron installiert sich nicht selbst. Fuege diese Zeile mit `crontab -e` ein "
                  f"(Arbeitsverzeichnis: {Path.cwd()}):")
    console.print(f"*/5 * * * * cd {Path.cwd()} && {executable}{profil}{db} tick >> daten/cron.log 2>&1",
                  soft_wrap=True)
    console.print(f"[dim]Zeitplan laut Profil: {profil_obj.automatisierung.cron} "
                  f"(Modus {profil_obj.automatisierung.effektiver_modus})[/]")
    return 0


# --------------------------------------------------------------------------- #
# Automatik, Freigabe und Versand
# --------------------------------------------------------------------------- #

def _zeige_zusammenfassung(titel: str, daten: dict) -> None:
    zeilen = [f"{k}: {v}" for k, v in daten.items() if v not in (None, [], "")]
    console.print(Panel("\n".join(zeilen) or "-", title=titel))


def cmd_automatik(args: argparse.Namespace) -> int:
    """Ein kompletter Lauf jetzt: suchen, bewerten, Bewerbungen vorbereiten."""
    profil = lade_profil(args.profil)
    with sperre("automatik") as frei:
        if not frei:
            console.print("[yellow]Ein Automatiklauf läuft bereits.[/]")
            return 1
        with Speicher(args.db) as db:
            # „Nur suchen“ aus dem Profil gilt auch fuer Laeufe per Knopf (Assistent, „Jetzt suchen“)
            nur_suchen = args.nur_suchen or profil.automatisierung.effektiver_modus == "nur_suchen"
            ergebnis = ablauf.automatik_lauf(
                db, profil, ausloeser=args.ausloeser, nur_suchen=nur_suchen,
                mit_llm=not args.ohne_llm,
            )
    _zeige_zusammenfassung("Automatiklauf", ergebnis)
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    """Cron-Einstieg: faelligen Automatiklauf starten und Freigaben senden."""
    profil = lade_profil(args.profil)
    try:  # Sicherungen frisch halten (nur Kopien, das Profil bleibt unveraendert)
        from .config import profil_sichern, unterlagen_sichern

        profil_sichern()
        unterlagen_sichern(profil)
    except Exception as e:  # Sicherung darf den Tick nie aufhalten
        console.print(f"[yellow]Sicherung der Unterlagen fehlgeschlagen: {e}[/]")
    with Speicher(args.db) as db:
        ergebnis = ablauf.tick(db, profil)
    # Nur protokollieren, wenn etwas passiert ist (cron.log klein halten)
    senden = ergebnis.get("senden") or {}
    if any(ergebnis.get(k) for k in ergebnis if k != "senden") or any(
        v for k, v in senden.items() if k != "abgeschickt" or v
    ):
        console.print(ergebnis)
    return 0


def cmd_modelle(args: argparse.Namespace) -> int:
    """KI-Modelle, die der eingerichtete Zugang wirklich anbietet."""
    profil = lade_profil(args.profil)
    anbieter = llm_modul.provider(profil.bewertung.modell)
    if anbieter is None:
        console.print("[yellow]Kein KI-Zugang eingerichtet (Einstellungen → Konten oder .env).[/]")
        return 1
    modelle = llm_modul.verfuegbare_modelle(anbieter, neu_laden=True)
    if not modelle:
        console.print(f"[red]{llm_modul.modell_fehler.get(anbieter) or 'Keine Modelle gemeldet.'}[/]")
        return 1
    ziel, hinweis = llm_modul.pruefe_modell(anbieter, profil.bewertung.modell)
    for m in modelle:
        console.print(("→ " if m == ziel else "  ") + m)
    console.print(f"\nAnbieter: {anbieter} · Profil-Modell: {profil.bewertung.modell} · genutzt: {ziel}")
    if hinweis:
        console.print(f"[yellow]{hinweis}[/]")
    return 0


def cmd_browser_test(args: argparse.Namespace) -> int:
    """Startet den Browser einmal (ohne KI) und meldet, ob Online-Formulare moeglich sind."""
    from .bewerben import browser

    ergebnis = browser.selbsttest()
    console.print(("[green]✓ " if ergebnis.ok else "[red]✗ ") + ergebnis.meldung + "[/]")
    if ergebnis.pfad:
        console.print(f"Browser: {ergebnis.pfad}")
    return 0 if ergebnis.ok else 1


def cmd_postfach(args: argparse.Namespace) -> int:
    """Antworten im Postfach jetzt auswerten (IMAP, nur lesend)."""
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        ergebnis = ablauf.postfach_lauf(db, profil, ausloeser="knopf", mindestabstand_min=0)
    if ergebnis is None:
        console.print("[yellow]Postfach-Auswertung ist aus oder IMAP nicht eingerichtet.[/]")
        return 1
    console.print(ergebnis)
    return 0


def cmd_vorbereiten(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    # Wartet, bis ein laufender Automatiklauf fertig ist, statt abzubrechen
    with sperre("automatik", warten=True):
        with Speicher(args.db) as db:
            ergebnis = ablauf.vorbereiten_lauf(
                db, profil, args.refs, ausloeser=args.ausloeser, mit_llm=not args.ohne_llm,
                probelauf=False if args.ohne_probelauf else None,
            )
    _zeige_zusammenfassung("Vorbereitung", ergebnis)
    return 0


def cmd_senden(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        ergebnis = ablauf.senden_lauf(db, profil, ausloeser=args.ausloeser)
    _zeige_zusammenfassung("Versand", ergebnis)
    return 1 if ergebnis.get("fehlgeschlagen") else 0


def cmd_warteschlange(args: argparse.Namespace) -> int:
    with Speicher(args.db) as db:
        vorgaenge = db.vorgaenge(args.status or None)
        t = Table(title="Bewerbungsvorgänge")
        for spalte in ("Status", "Kanal", "Titel", "Arbeitgeber", "Referenz", "Hinweis"):
            t.add_column(spalte, overflow="fold")
        for v in vorgaenge:
            job = db.job(v.ref)
            t.add_row(v.status, v.kanal, job.titel if job else "-", job.arbeitgeber if job else "-",
                      v.ref, textwrap.shorten(v.hinweis or "", 60))
    console.print(t)
    return 0


def cmd_freigeben(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        for ref in args.refs:
            try:
                ablauf.freigeben(db, profil, ref)
                console.print(f"[green]Freigegeben:[/] {ref}")
            except ValueError as e:
                console.print(f"[red]{ref}:[/] {e}")
    if args.senden:
        return cmd_senden(args)
    return 0


def cmd_ablehnen(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        for ref in args.refs:
            ablauf.ablehnen(db, profil, ref, grund=args.grund or "",
                            arbeitgeber_sperren=args.arbeitgeber_sperren, profil_pfad=args.profil)
            console.print(f"Abgelehnt: {ref}")
    return 0


def cmd_vorschlaege(args: argparse.Namespace) -> int:
    from . import vorschlaege

    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        liste = vorschlaege.aus_treffern(db, profil)
        if args.ki:
            gute = [j.titel for j, _ in db.bestenliste(min_score=profil.bewertung.min_score, limit=20)]
            try:
                liste += vorschlaege.per_llm(profil, gute)
            except Exception as e:
                console.print(f"[yellow]KI-Vorschläge nicht verfügbar:[/] {e}")
        tipps = vorschlaege.such_tipps(db, profil)
    t = Table(title="Vorschläge für weitere Suchbegriffe")
    t.add_column("Suchbegriff")
    t.add_column("Warum")
    t.add_column("Quelle", style="dim")
    for v in liste:
        t.add_row(v.begriff, v.grund, v.quelle)
    console.print(t)
    for tipp in tipps:
        console.print(f"[cyan]Tipp:[/] {tipp.text}")
    return 0


def cmd_nachweis(args: argparse.Namespace) -> int:
    from datetime import date, timedelta

    from . import dokumente

    profil = lade_profil(args.profil)
    bis = date.fromisoformat(args.bis) if args.bis else date.today()
    von = date.fromisoformat(args.von) if args.von else bis - timedelta(days=30)
    with Speicher(args.db) as db:
        zeilen = dokumente.nachweis_zeilen(db, von, bis)
    ziel = Path(args.ausgabe or f"daten/nachweis_{von}_{bis}.{args.format}")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "csv":
        ziel.write_text(dokumente.nachweis_csv(zeilen), encoding="utf-8-sig")
    else:
        ziel.write_bytes(dokumente.nachweis_pdf(zeilen, profil, von, bis))
    console.print(f"[green]{len(zeilen)} Bewerbungen[/] von {von:%d.%m.%Y} bis {bis:%d.%m.%Y} → {ziel}")
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bewerbungsagent",
        description="Stellen aus mehreren Quellen suchen, bewerten und per browser-use bewerben.",
    )
    p.add_argument("--profil", default=None, help="Pfad zur Profil-YAML (Standard: config/profil.yaml)")
    p.add_argument("--db", default=None, help="Pfad zur SQLite-Datei")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="befehl", required=True)

    s = sub.add_parser("suchen", help="Stellen aus den Profilquellen holen und speichern")
    s.add_argument("--was", action="append", help="Suchbegriff (mehrfach moeglich)")
    s.add_argument("--wo", default=None, help="Ort oder PLZ")
    s.add_argument("--umkreis", type=int, default=None)
    s.add_argument("--tage", type=int, default=None, help="Nur Anzeigen der letzten N Tage")
    s.add_argument("--limit", type=int, default=None, help="Max. Treffer pro Suchbegriff")
    s.add_argument("--ohne-details", action="store_true",
                   help="Kein Volltext laden (schneller, aber Sentiment wird ungenau)")
    s.add_argument("--quelle", action="append", choices=sorted(QUELLEN),
                   help="Nur diese Quelle nutzen (mehrfach moeglich; Standard: Profilquellen)")
    s.set_defaults(func=cmd_suchen)

    b = sub.add_parser("bewerten", help="Gespeicherte Stellen bewerten")
    b.add_argument("--alle", action="store_true", help="Auch bereits bewertete neu bewerten")
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--ohne-llm", action="store_true", help="Nur Heuristik, kein API-Aufruf")
    b.set_defaults(func=cmd_bewerten)

    t = sub.add_parser("top", help="Bestbewertete Stellen anzeigen")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--min", type=float, default=None)
    t.add_argument("--offen", action="store_true", help="Bereits beworbene ausblenden")
    t.set_defaults(func=cmd_top)

    z = sub.add_parser("zeigen", help="Eine Stelle im Detail anzeigen")
    z.add_argument("ref")
    z.add_argument("--kurz", action="store_true", help="Ohne Anzeigentext")
    z.set_defaults(func=cmd_zeigen)

    a = sub.add_parser("anschreiben", help="Anschreiben fuer eine Stelle erzeugen")
    a.add_argument("ref")
    a.add_argument("--ausgabe", default=None, help="In Datei schreiben")
    a.add_argument("--ohne-llm", action="store_true")
    a.set_defaults(func=cmd_anschreiben)

    w = sub.add_parser("bewerben", help="Bewerbung per Browser-Agent ausfuellen/absenden")
    w.add_argument("ref", nargs="?", help="Referenznummer; ohne Angabe die Top-Treffer")
    w.add_argument("--top", type=int, default=1, help="Anzahl Stellen aus der Bestenliste")
    w.add_argument("--min", type=float, default=None)
    w.add_argument("--absenden", action="store_true",
                   help="Bewerbung wirklich abschicken (Standard: nur Probelauf)")
    w.add_argument("--ja", action="store_true", help="Sicherheitsabfrage ueberspringen")
    w.add_argument("--headless", action="store_true", help="Browser unsichtbar starten")
    w.add_argument("--schritte", type=int, default=40, help="Max. Agentenschritte pro Bewerbung")
    w.add_argument("--domains-offen", action="store_true",
                   help="Domain-Beschraenkung des Browsers aufheben")
    w.add_argument("--aufzeichnung", default=None, help="Pfad fuer den Agenten-Gespraechsverlauf")
    w.add_argument("--ohne-llm", action="store_true", help="Anschreiben aus Vorlage statt LLM")
    w.set_defaults(func=cmd_bewerben)

    st = sub.add_parser("status", help="Bestand und Bewerbungsverlauf")
    st.add_argument("--limit", type=int, default=15)
    st.set_defaults(func=cmd_status)

    pl = sub.add_parser("pipeline", help="suchen + bewerten + top in einem Lauf")
    pl.add_argument("--was", action="append")
    pl.add_argument("--wo", default=None)
    pl.add_argument("--umkreis", type=int, default=None)
    pl.add_argument("--tage", type=int, default=None)
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--ohne-details", action="store_true")
    pl.add_argument("--quelle", action="append", choices=sorted(QUELLEN))
    pl.add_argument("--alle", action="store_true")
    pl.add_argument("--ohne-llm", action="store_true")
    pl.add_argument("--min", type=float, default=None)
    pl.add_argument("--offen", action="store_true")
    pl.set_defaults(func=cmd_pipeline)

    cr = sub.add_parser("cron", help="Cron-Zeile fuer den regelmaessigen Tick ausgeben")
    cr.add_argument("--executable", default="bewerbungsagent", help="Absoluter Pfad zur CLI, falls noetig")
    cr.set_defaults(func=cmd_cron)

    au = sub.add_parser("automatik", help="Jetzt suchen, bewerten und Bewerbungen vorbereiten")
    au.add_argument("--nur-suchen", action="store_true", help="Nur suchen und bewerten, nichts vorbereiten")
    au.add_argument("--ohne-llm", action="store_true")
    au.add_argument("--ausloeser", default="cli", help=argparse.SUPPRESS)
    au.set_defaults(func=cmd_automatik)

    ti = sub.add_parser("tick", help="Cron-Einstieg: faelligen Lauf starten, Freigaben senden")
    ti.set_defaults(func=cmd_tick)

    vb = sub.add_parser("vorbereiten", help="Bewerbungen fuer diese Referenzen vorbereiten")
    vb.add_argument("refs", nargs="+")
    vb.add_argument("--ohne-llm", action="store_true")
    vb.add_argument("--ohne-probelauf", action="store_true", help="Formular nicht testweise ausfuellen")
    vb.add_argument("--ausloeser", default="cli", help=argparse.SUPPRESS)
    vb.set_defaults(func=cmd_vorbereiten)

    se = sub.add_parser("senden", help="Alle freigegebenen Bewerbungen verschicken")
    se.add_argument("--ausloeser", default="cli", help=argparse.SUPPRESS)
    se.set_defaults(func=cmd_senden)

    ws = sub.add_parser("warteschlange", help="Bewerbungsvorgaenge anzeigen")
    ws.add_argument("--status", action="append", help="Nur diese Status (mehrfach moeglich)")
    ws.set_defaults(func=cmd_warteschlange)

    fg = sub.add_parser("freigeben", help="Vorbereitete Bewerbungen freigeben (Ja)")
    fg.add_argument("refs", nargs="+")
    fg.add_argument("--senden", action="store_true", help="Direkt im Anschluss verschicken")
    fg.add_argument("--ausloeser", default="cli", help=argparse.SUPPRESS)
    fg.set_defaults(func=cmd_freigeben)

    ab = sub.add_parser("ablehnen", help="Vorgeschlagene Stellen ablehnen (Nein)")
    ab.add_argument("refs", nargs="+")
    ab.add_argument("--grund", default=None)
    ab.add_argument("--arbeitgeber-sperren", action="store_true", help="Arbeitgeber nie wieder vorschlagen")
    ab.set_defaults(func=cmd_ablehnen)

    vs = sub.add_parser("vorschlaege", help="Weitere Suchbegriffe und Such-Tipps")
    vs.add_argument("--ki", action="store_true", help="Zusaetzlich Vorschlaege vom LLM holen")
    vs.set_defaults(func=cmd_vorschlaege)

    mo = sub.add_parser("modelle", help="Verfuegbare KI-Modelle des eingerichteten Zugangs anzeigen")
    mo.set_defaults(func=cmd_modelle)

    bt = sub.add_parser("browser-test", help="Pruefen, ob der Browser fuer Online-Formulare startet")
    bt.set_defaults(func=cmd_browser_test)

    pf = sub.add_parser("postfach", help="Antworten im Postfach jetzt auswerten (IMAP)")
    pf.set_defaults(func=cmd_postfach)

    nw = sub.add_parser("nachweis", help="Nachweis der Eigenbemuehungen exportieren")
    nw.add_argument("--von", default=None, help="Startdatum JJJJ-MM-TT (Standard: vor 30 Tagen)")
    nw.add_argument("--bis", default=None, help="Enddatum JJJJ-MM-TT (Standard: heute)")
    nw.add_argument("--format", choices=["csv", "pdf"], default="pdf")
    nw.add_argument("--ausgabe", default=None)
    nw.set_defaults(func=cmd_nachweis)
    return p


def main(argv: list[str] | None = None) -> int:
    lade_env()
    # In der Web-UI gespeicherte Zugaenge gelten auch fuer CLI, Worker und Cron-Tick
    from . import zugangsdaten

    zugangsdaten.anwenden()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        return 2
    except ValueError as e:
        console.print(f"[red]Konfigurationsfehler:[/] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("\n[yellow]Abgebrochen.[/]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
