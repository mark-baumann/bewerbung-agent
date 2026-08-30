# 🚀 Onboarding — Bewerbungsagent

Dieses Dokument beschreibt, wie der Bewerbungsagent eingerichtet, gebaut und
ausgeführt wird. Es ist ein **CLI-Werkzeug** (kein Dauerläufer), daher gibt es
keinen Pi-Deploy wie bei den Streamlit-Diensten — stattdessen ein
reproduzierbares Docker-Image in der GitHub Container Registry.

---

## 1. Was ist der Bewerbungsagent?

Ein Python-CLI, das Stellen der Bundesagentur für Arbeit sucht, semantisch
bewertet, Anschreiben generiert und Bewerbungen per Browser-Automation
(browser-use) ausfüllt.

```
suchen → bewerten → top → anschreiben → bewerben
```

---

## 2. Voraussetzungen

| Komponente | Anforderung |
|---|---|
| Python | 3.11+ |
| LLM | Anthropic API-Key (`ANTHROPIC_API_KEY`) |
| Browser | Chromium (via Playwright, nur für `bewerben`) |
| Optional | BA-Login (`BA_BENUTZER` / `BA_PASSWORT`) für „Online bewerben" |

---

## 3. Lokale Installation

```bash
git clone https://github.com/mark-baumann/bewerbung-agent.git
cd bewerbung-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[browser]"          # ohne [browser] läuft alles außer 'bewerben'
playwright install chromium
```

Konfiguration:

```bash
cp config/profil.example.yaml config/profil.yaml
cp .env.example .env                 # ANTHROPIC_API_KEY eintragen
$EDITOR config/profil.yaml           # Person, Suchbegriffe, Skills, Ausschlüsse
```

---

## 4. Docker (empfohlen für reproduzierbare Ausführung)

### Build

```bash
docker build -t bewerbungsagent .
```

### Ausführen

```bash
docker run --rm -it \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -v "$PWD/config:/app/config" \
  -v "$PWD/unterlagen:/app/unterlagen" \
  -v "$PWD/daten:/app/daten" \
  bewerbungsagent suchen --was "Data Engineer" --wo Berlin
```

Weitere Befehle: `bewerten`, `top`, `zeigen <ref>`, `anschreiben <ref>`,
`bewerben <ref>` (Standard: Probelauf), `status`, `pipeline`.

> **Hinweis:** `bewerben` startet Chromium. Im Container läuft es als root,
> daher deaktiviert der Code die Chromium-Sandbox automatisch
> (`chromium_sandbox=False`). Lokal als normaler Nutzer bleibt die Sandbox an.

---

## 5. CI/CD

Der Workflow `.github/workflows/build.yml` baut das Image bei jedem Push auf
`main`/`master` und stellt es in der GHCR bereit:

```
ghcr.io/mark-baumann/bewerbung-agent:latest
```

- **Push auf `main`** → Build + Push (amd64 + arm64)
- **Pull Request** → Build (ohne Push) als CI-Check
- **`workflow_dispatch`** → manueller Build

---

## 6. Sicherheitsnetze

- **Probelauf-Standard:** `bewerben` schickt ohne `--absenden` nichts ab.
- **Domain-Schranke:** Der Browser-Agent ist auf die Ziel-Domain + Arbeitsagentur
  beschränkt (`--domains-offen` hebt das auf).
- **Keine erfundenen Angaben:** Der Agent füllt nur Profildaten ein; fehlende
  Pflichtfelder werden gemeldet statt erfunden.
- **Zugangsdaten:** `BA_BENUTZER`/`BA_PASSWORT` erreichen das Modell nie im
  Klartext (browser-use setzt Platzhalter erst im Browser ein).

---

## 7. Tests

```bash
pip install -e ".[dev]"
pytest
```

Die Tests decken Normalisierung, Sentiment, Heuristik, SQLite-Roundtrip und die
Browser-Agent-Konfiguration (ohne echten Browser) ab.

---

## 8. Projektstruktur

```
bewerbung-agent/
├── bewerbungsagent/
│   ├── cli.py                    # Kommandozeile
│   ├── config.py                 # Profil-YAML
│   ├── models.py                 # Job, Score, Application
│   ├── db.py                     # SQLite-Speicher
│   ├── sources/arbeitsagentur.py # API-Client
│   ├── scoring/                  # sentiment, heuristik, llm
│   ├── anschreiben.py            # Anschreiben-Generator
│   └── bewerben/browser.py       # browser-use-Agent
├── config/profil.example.yaml
├── unterlagen/                   # Lebenslauf, Zeugnisse (nicht im Git)
├── Dockerfile
├── .github/workflows/build.yml
└── tests/
```
