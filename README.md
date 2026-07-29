# Bewerbungsagent

Holt echte Stellenangebote von der **Jobbörse der Bundesagentur für Arbeit**,
bewertet sie **semantisch und nach der Tonalität der Anzeige**, speichert alles
in SQLite und füllt die Bewerbung anschließend mit **browser-use** in einem
echten Browser aus – auf Wunsch inklusive Absenden.

```
suchen  ──►  bewerten  ──►  top / zeigen  ──►  anschreiben  ──►  bewerben
 (API)      (Sentiment       (Auswahl)         (Claude)        (browser-use)
             + Claude)
```

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[browser]"          # ohne [browser] läuft alles außer 'bewerben'
playwright install chromium          # nur falls noch kein Chromium vorhanden
```

## Einrichten

```bash
cp config/profil.example.yaml config/profil.yaml
cp .env.example .env                 # ANTHROPIC_API_KEY eintragen
$EDITOR config/profil.yaml           # Person, Suchbegriffe, Skills, Ausschlüsse
```

Lebenslauf und Zeugnisse nach `unterlagen/` legen und die Pfade in
`profil.yaml` unter `unterlagen:` eintragen – der Browser-Agent lädt genau
diese Dateien hoch.

Ohne `ANTHROPIC_API_KEY` funktionieren Suche, heuristische Bewertung und
Anschreiben-Vorlage weiterhin; die semantische Bewertung und der Browser-Agent
brauchen ihn.

## Benutzung

```bash
# 1. Stellen holen (Suchbegriffe und Ort kommen aus dem Profil)
bewerbungsagent suchen
bewerbungsagent suchen --was "Data Engineer" --wo Hamburg --umkreis 50 --tage 7

# 2. Bewerten: Skill-Passung + Tonalität der Anzeige
bewerbungsagent bewerten                 # nur neue Stellen
bewerbungsagent bewerten --ohne-llm      # rein heuristisch, ohne API-Kosten

# 3. Auswählen
bewerbungsagent top --limit 15 --offen
bewerbungsagent zeigen 10001-1003353506-S

# 4. Anschreiben ansehen
bewerbungsagent anschreiben 10001-1003353506-S

# 5. Bewerben – erst Probelauf, dann echt
bewerbungsagent bewerben 10001-1003353506-S              # füllt aus, sendet NICHT
bewerbungsagent bewerben 10001-1003353506-S --absenden   # sendet nach Rückfrage

# Alles in einem Lauf (ohne Bewerben)
bewerbungsagent pipeline

# Übersicht
bewerbungsagent status
```

## Wie bewertet wird

Der Gesamtscore ist `Passung × 0,7 + Ton × 0,3` (ohne Anzeigentext zählt der
Ton nur zu 10 %, weil dann kaum Signal vorliegt).

**Passung** – deckt die Anzeige die Skills aus dem Profil ab? Die Heuristik
matcht mit Wortgrenzen (`Java` trifft nicht `JavaScript`, `C++` und `C#`
funktionieren). Mit API-Key beurteilt Claude zusätzlich inhaltlich, auch bei
abweichender Wortwahl.

**Ton** – `scoring/sentiment.py` ist bewusst kein allgemeines Sprach-Sentiment,
sondern ein Lexikon für deutsche Stellenanzeigen. Eine Anzeige kann euphorisch
klingen und trotzdem schlecht abschneiden:

| Signal | Wirkung |
|---|---|
| unbefristet, Tarifvertrag, Gleitzeit, 30 Tage Urlaub, Gehaltsspanne genannt | **positiv** |
| Homeoffice, Weiterbildung, Betriebsrat, 4-Tage-Woche | **positiv** |
| „junges dynamisches Team", „Macher gesucht", „Hands-on-Mentalität" | **negativ** |
| „hohe Belastbarkeit", „Überstunden gehören dazu" | **negativ** |
| Zeitarbeit / Arbeitnehmerüberlassung, Provisionsbasis | **stark negativ** |

Die Begriffe unter `bewertung.ausschluss` verwerfen eine Stelle hart – ohne
LLM-Aufruf, das spart Kosten.

## Bewerben mit browser-use

`bewerbungsagent bewerben` startet einen browser-use-Agenten, der die
Bewerbungsseite öffnet, dem „Jetzt bewerben"-Weg folgt (auch über
Weiterleitungen ins Bewerbermanagementsystem des Arbeitgebers), das Formular
aus den Profildaten füllt, das Anschreiben einfügt und den Lebenslauf hochlädt.

Sicherheitsnetze:

- **Probelauf ist Standard.** Ohne `--absenden` füllt der Agent alles aus und
  hält vor dem finalen Absende-Klick an. Erst `--absenden` sendet wirklich, und
  auch dann erst nach einer Rückfrage auf der Konsole (`--ja` überspringt sie).
- **Keine erfundenen Angaben.** Felder, die sich nicht aus dem Profil befüllen
  lassen (Gehaltsvorstellung, Verfügbarkeit), bleiben leer und werden als
  `fehlende_angaben` gemeldet.
- **Domain-Schranke.** Der Browser darf nur auf die Domain der Stellenanzeige
  und arbeitsagentur.de zugreifen (`--domains-offen` hebt das auf, falls ein
  Portal auf einen fremden Anbieter weiterleitet).
- **Abbruch statt Herumraten** bei Login-Pflicht ohne Zugangsdaten, Captcha,
  reiner E-Mail-Bewerbung oder Zahlungsaufforderung.
- **Zugangsdaten** (`BA_BENUTZER`/`BA_PASSWORT`) gehen über `sensitive_data`
  von browser-use: das Modell sieht nur Platzhalter, eingesetzt wird erst im
  Browser.

Jeder Versuch landet mit Status, Schrittzahl und Ergebnis in der Tabelle
`applications`. `top --offen` blendet bereits beworbene Stellen aus.

Nützliche Schalter: `--headless` (unsichtbar), `--schritte 60` (mehr
Agentenschritte für lange Formulare), `--aufzeichnung pfad/` (Gesprächsverlauf
des Agenten mitschreiben), `--top 3` (die drei besten offenen Stellen).

Zum Browser: als root (Container, CI) startet Chromium nur ohne Sandbox – das
erkennt der Agent selbst und schaltet sie dann ab, lokal als normaler Nutzer
bleibt sie an. Einen abweichenden Browser setzt du über
`BROWSER_EXECUTABLE_PATH=/pfad/zu/chrome`.

## Datenquelle

Öffentliche Jobsuche-API der Bundesagentur für Arbeit:

- `GET /pc/v4/jobs` – Suche (Treffer ohne Volltext)
- `GET /pc/v3/jobdetails/{base64(refnr)}` – Detail inkl. Anzeigentext

Nur der Detail-Endpunkt liefert den Text, auf dem die Sentimentanalyse
arbeitet. `suchen --ohne-details` spart Requests, macht die Tonbewertung aber
blind.

## Projektstruktur

```
bewerbungsagent/
  cli.py                    Kommandozeile
  config.py                 Profil-YAML (Zugangsdaten nur aus der Umgebung)
  models.py                 Job, Score, Application
  db.py                     SQLite-Speicher
  sources/arbeitsagentur.py API-Client + Normalisierung
  scoring/sentiment.py      Lexikon für Anzeigen-Tonalität
  scoring/heuristik.py      Skill-Match, Ausschlüsse, Gesamtscore
  scoring/llm.py            Semantische Bewertung mit Claude
  anschreiben.py            Anschreiben (Claude, sonst Vorlage)
  bewerben/browser.py       browser-use-Agent
```

## Hinweise

Der Agent bewirbt sich in deinem Namen mit deinen Daten – prüfe Anschreiben und
Probelauf, bevor du `--absenden` benutzt. Massenbewerbungen ohne Prüfung sind
weder im Interesse der Arbeitgeber noch deins; die Voreinstellungen sind
deshalb auf einzelne, geprüfte Bewerbungen ausgelegt.

Tests: `pytest -q`
