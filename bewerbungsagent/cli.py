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

from .anschreiben import erzeuge as erzeuge_anschreiben
from .config import lade_profil
from .db import Speicher
from .models import Application, Job, Score
from .scoring import llm as llm_modul
from .sources.arbeitsagentur import ArbeitsagenturClient

console = Console()


# --------------------------------------------------------------------------- #
# suchen
# --------------------------------------------------------------------------- #

def cmd_suchen(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    s = profil.suche
    begriffe = args.was or s.was
    ort = args.wo if args.wo is not None else s.wo

    alle: dict[str, Job] = {}
    with ArbeitsagenturClient() as client:
        for begriff in begriffe:
            console.print(f"[cyan]Suche[/] '{begriff}' in '{ort or 'ganz DE'}' ...")
            try:
                jobs = client.hole_jobs(
                    was=begriff,
                    wo=ort,
                    umkreis=args.umkreis or s.umkreis,
                    veroeffentlicht_seit_tagen=args.tage or s.veroeffentlicht_seit_tagen,
                    nur_vollzeit=s.nur_vollzeit,
                    max_treffer=args.limit or s.max_pro_query,
                    mit_details=not args.ohne_details,
                )
            except Exception as e:
                console.print(f"[red]Suche fehlgeschlagen:[/] {e}")
                continue
            console.print(f"  {len(jobs)} Treffer")
            for j in jobs:
                alle.setdefault(j.ref, j)

    if not alle:
        console.print("[yellow]Keine Stellen gefunden.[/]")
        return 1

    with Speicher(args.db) as db:
        neu, akt = db.speichere_jobs(alle.values())
    console.print(f"[green]{neu} neue[/] und {akt} aktualisierte Stellen gespeichert "
                  f"({len(alle)} eindeutige Treffer).")
    return 0


# --------------------------------------------------------------------------- #
# bewerten
# --------------------------------------------------------------------------- #

def cmd_bewerten(args: argparse.Namespace) -> int:
    profil = lade_profil(args.profil)
    with Speicher(args.db) as db:
        jobs = db.jobs(nur_ohne_score=not args.alle, limit=args.limit)
        if not jobs:
            console.print("[yellow]Nichts zu bewerten. Erst 'suchen' ausfuehren.[/]")
            return 1

        mit_llm = not args.ohne_llm
        if mit_llm and not llm_modul.client_verfuegbar():
            console.print("[yellow]ANTHROPIC_API_KEY fehlt - bewerte rein heuristisch.[/]")
            mit_llm = False

        console.print(f"Bewerte {len(jobs)} Stellen ({'LLM + Heuristik' if mit_llm else 'Heuristik'}) ...")
        scores = llm_modul.bewerte_jobs(jobs, profil, mit_llm=mit_llm)
        for score in scores:
            db.speichere_score(score)

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


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bewerbungsagent",
        description="Stellen der Arbeitsagentur suchen, bewerten und per browser-use bewerben.",
    )
    p.add_argument("--profil", default=None, help="Pfad zur Profil-YAML (Standard: config/profil.yaml)")
    p.add_argument("--db", default=None, help="Pfad zur SQLite-Datei")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="befehl", required=True)

    s = sub.add_parser("suchen", help="Stellen von der Arbeitsagentur holen und speichern")
    s.add_argument("--was", action="append", help="Suchbegriff (mehrfach moeglich)")
    s.add_argument("--wo", default=None, help="Ort oder PLZ")
    s.add_argument("--umkreis", type=int, default=None)
    s.add_argument("--tage", type=int, default=None, help="Nur Anzeigen der letzten N Tage")
    s.add_argument("--limit", type=int, default=None, help="Max. Treffer pro Suchbegriff")
    s.add_argument("--ohne-details", action="store_true",
                   help="Kein Volltext laden (schneller, aber Sentiment wird ungenau)")
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
    pl.add_argument("--alle", action="store_true")
    pl.add_argument("--ohne-llm", action="store_true")
    pl.add_argument("--min", type=float, default=None)
    pl.add_argument("--offen", action="store_true")
    pl.set_defaults(func=cmd_pipeline)
    return p


def main(argv: list[str] | None = None) -> int:
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
