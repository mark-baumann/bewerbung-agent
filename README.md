# 🤖 Bewerbungs-Agent — Automatisierte Jobsuche & Bewerbung

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![Claude](https://img.shields.io/badge/LLM-Claude-orange?logo=anthropic)](https://anthropic.com)
[![Browser-Use](https://img.shields.io/badge/Automation-browser--use-green)](https://github.com/browser-use/browser-use)
[![Tests](https://img.shields.io/badge/Tests-Pytest-green?logo=pytest)](https://pytest.org)

**Vollautomatischer Bewerbungs-Workflow:** Stellen der Bundesagentur für Arbeit suchen, semantisch bewerten, Anschreiben generieren und per Browser-Agent bewerben — alles per CLI.

```
suchen  ──►  bewerten  ──►  top / zeigen  ──►  anschreiben  ──►  bewerben
 (API)      (Sentiment       (Auswahl)         (Claude)        (browser-use)
             + Claude)
```

---

## ✨ Features

- **🔍 Jobsuche:** Echte Stellen von der [Jobbörse der Bundesagentur für Arbeit](https://www.arbeitsagentur.de/jobsuche/) per API
- **📊 Intelligente Bewertung:** Skill-Matching + Sentiment-Analyse speziell für deutsche Stellenanzeigen
- **🤖 LLM-unterstützt:** Claude bewertet semantische Passung und generiert Anschreiben
- **🌐 Browser-Automation:** browser-use füllt Bewerbungsformulare automatisch aus
- **🛡️ Sicherheitsnetze:** Probelauf-Standard, Domain-Schranke, keine erfundenen Angaben
- **💾 SQLite-Speicher:** Alle Jobs, Scores und Bewerbungen persistent
- **📋 Pipeline-Modus:** `suchen → bewerten → top` in einem Befehl

---

## 🚀 Installation

```bash
git clone https://github.com/mark-baumann/bewerbung-agent.git
cd bewerbung-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[browser]"          # ohne [browser] läuft alles außer 'bewerben'
playwright install chromium
```

## ⚙️ Einrichten

```bash
cp config/profil.example.yaml config/profil.yaml
cp .env.example .env                 # ANTHROPIC_API_KEY eintragen
$EDITOR config/profil.yaml           # Person, Suchbegriffe, Skills, Ausschlüsse
```

---

## 🖥️ Nutzung

```bash
# 1. Stellen suchen
bewerbungsagent suchen
bewerbungsagent suchen --was "Data Engineer" --wo Hamburg --umkreis 50 --tage 7

# 2. Bewerten: Skill-Passung + Tonalität
bewerbungsagent bewerten
bewerbungsagent bewerten --ohne-llm      # rein heuristisch, ohne API-Kosten

# 3. Bestenliste
bewerbungsagent top --limit 15 --offen

# 4. Details + Anschreiben
bewerbungsagent zeigen 10001-1003353506-S
bewerbungsagent anschreiben 10001-1003353506-S

# 5. Bewerben — Probelauf (Standard)
bewerbungsagent bewerben 10001-1003353506-S

# 6. Bewerben — echtes Absenden
bewerbungsagent bewerben 10001-1003353506-S --absenden

# Alles in einem Lauf
bewerbungsagent pipeline

# Übersicht
bewerbungsagent status
```

---

## 📊 Bewertungssystem

Der Gesamtscore ist `Passung × 0,7 + Ton × 0,3`.

| Signal | Wirkung |
|---|---|
| unbefristet, Tarifvertrag, Gleitzeit, 30 Tage Urlaub | **positiv** |
| Homeoffice, Weiterbildung, Betriebsrat, 4-Tage-Woche | **positiv** |
| „junges dynamisches Team", „Macher gesucht" | **negativ** |
| „hohe Belastbarkeit", „Überstunden gehören dazu" | **negativ** |
| Zeitarbeit, Provisionsbasis | **stark negativ** |

---

## 🧱 Tech-Stack

| Komponente | Technologie |
|---|---|
| **CLI** | Python argparse + Rich |
| **API** | Bundesagentur für Arbeit Jobsuche-API |
| **LLM** | Anthropic Claude |
| **Browser** | browser-use + Playwright |
| **Storage** | SQLite |
| **Konfiguration** | YAML-Profil |
| **Sprache** | Python 3.11+ |

---

## 📁 Projektstruktur

```
bewerbung-agent/
├── bewerbungsagent/
│   ├── cli.py                    # Kommandozeile
│   ├── config.py                 # Profil-YAML
│   ├── models.py                 # Job, Score, Application
│   ├── db.py                     # SQLite-Speicher
│   ├── sources/arbeitsagentur.py # API-Client
│   ├── scoring/
│   │   ├── sentiment.py          # Lexikon für Anzeigen-Tonalität
│   │   ├── heuristik.py          # Skill-Match, Ausschlüsse
│   │   └── llm.py                # Semantische Bewertung (Claude)
│   ├── anschreiben.py            # Anschreiben-Generator
│   └── bewerben/browser.py       # browser-use-Agent
├── config/profil.example.yaml
├── tests/
└── pyproject.toml
```

---

## 👤 Autor

**Mark Baumann** — [GitHub](https://github.com/mark-baumann) · [markb.de](https://markb.de)
