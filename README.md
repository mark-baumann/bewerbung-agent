# 🤖 Bewerbungs-Agent — Automatisierte Jobsuche & Bewerbung

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![OpenAI](https://img.shields.io/badge/LLM-ChatGPT-412991?logo=openai)](https://openai.com)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-black?logo=ollama)](https://ollama.com)
[![Browser-Use](https://img.shields.io/badge/Automation-browser--use-green)](https://github.com/browser-use/browser-use)
[![Tests](https://img.shields.io/badge/Tests-Pytest-green?logo=pytest)](https://pytest.org)

**Vom Suchen bis zum Absenden – du sagst nur noch Ja oder Nein.** Der Agent sucht
Stellen bei der Bundesagentur für Arbeit und GET IN IT, bewertet sie gegen dein
Profil und deinen Lebenslauf, schreibt Anschreiben, testet Online-Formulare im
Probelauf und schickt Bewerbungen nach deinem **Ja** per E-Mail oder Formular ab.

```
Zeitplan (Cron) oder Knopf
  └─ suchen ─► bewerten ─► beste Stellen vorbereiten (Anschreiben, PDF, Versandweg, Probelauf)
        └─ Startseite: ✅ Ja / ❌ Nein ─► Versand per E-Mail oder Online-Formular
              └─ Tracker: Rückmeldungen, Nachfassen, Nachweis der Eigenbemühungen
```

---

## ✨ Features

- **🏠 Cockpit mit Ja/Nein:** Vorbereitete Bewerbungen als Karten mit Passung, Gründen,
  Versandweg, Probelauf-Ergebnis und bearbeitbarem Anschreiben – ein Klick schickt ab
- **🤖 Automatik per Zeitplan:** Suchen, bewerten und die besten Stellen vorbereiten
  (z. B. täglich 6:00); Modus *Automatisch*, *Nur suchen* oder *Aus*
- **🧭 Schritt für Schritt:** Geführter manueller Prozess Suchen → Auswählen →
  Prüfen & freigeben → Status
- **💡 Suchvorschläge:** Ähnliche Jobtitel aus gut passenden Treffern, KI-Ideen
  (Synonyme, Nachbarrollen, englische Titel) und Tipps, welche Suchbegriffe nichts bringen
- **📧 Versand per E-Mail:** Aus deinem Postfach (SMTP) mit Anschreiben-PDF, Lebenslauf
  und Zeugnissen, Kopie per Bcc an dich
- **🌐 Online-Formulare:** browser-use füllt Bewerbungsformulare aus – inklusive
  gängiger Bewerbermanagement-Systeme (softgarden, Personio, Workday, …)
- **📊 Bewertung:** Skill-Matching, Tonalität der Anzeige und LLM-Bewertung (Claude,
  ChatGPT oder Ollama) mit deinem Lebenslauf als Kontext
- **📬 Tracker & Nachweis:** Rückmeldungen eintragen, fällige Nachfragen, Export der
  Eigenbemühungen als PDF/CSV für Agentur für Arbeit oder Jobcenter
- **🛡️ Sicherheitsnetze:** Nichts geht ohne dein Ja raus, Tageslimit, kein automatisches
  Wiederholen fehlgeschlagener Sendungen, keine erfundenen Angaben

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
cp .env.example .env                 # LLM-Key eintragen (Anthropic ODER Ollama)
$EDITOR config/profil.yaml           # Person, Suchbegriffe, Skills, Ausschlüsse
```

### 🤖 LLM-Konfiguration

Entweder OpenAI/ChatGPT, Anthropic oder ein lokaler/Ollama-Server:

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# ChatGPT / OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# ODER Ollama (lokal oder zentral)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=                      # nur falls der Server einen Key verlangt
OLLAMA_MODEL=glm-5.3-flash           # z.B. glm-5.3-flash, deepseek-v4.1-flash, kimi-k3, gpt-oss:120b
```

Claude-Modelle (`claude-*`) laufen über Anthropic, GPT-/o-Modelle über die
OpenAI-API und die übrigen Modelle über Ollama. Ohne Key fällt die Bewertung
auf die Heuristik und das Anschreiben auf die Vorlage zurück.

### 📧 Bewerbungen per E-Mail

Für den E-Mail-Versand trägst du die Zugangsdaten deines Postfachs in die `.env`
ein (bzw. in die Umgebung des Containers):

```bash
SMTP_HOST=smtp.example.de
SMTP_PORT=465                 # 465 = SSL, 587 = STARTTLS
SMTP_BENUTZER=ich@example.de
SMTP_PASSWORT=...
SMTP_ABSENDER=ich@example.de  # optional
```

Unter **Profil → Automatik & Versand** kannst du eine Testmail an dich selbst
schicken. Jede Bewerbung geht als Bcc auch an dich.

### ⏰ Automatik und Zeitplan

Im Docker-Container ruft Cron alle 5 Minuten `bewerbungsagent tick` auf. Der Tick
startet den Automatiklauf, sobald der Zeitplan aus dem Profil fällig ist
(Standard: täglich 06:00 deutscher Zeit), und verschickt freigegebene
Bewerbungen. Modus und Zeitplan änderst du im Cockpit bzw. unter
**Profil → Automatik & Versand** – ohne Neustart.

```yaml
automatisierung:
  modus: automatisch    # automatisch | nur_suchen | aus
  cron: "0 6 * * *"     # Minute Stunde Tag Monat Wochentag
  max_pro_lauf: 5       # so viele Bewerbungen bereitet ein Lauf vor
  max_pro_tag: 10       # Sicherheitsgrenze für Sendungen pro Tag
  probelauf_vorab: true # Online-Formular vorab testweise ausfüllen
  ziel_pro_woche: 5
```

Lokal gibt `bewerbungsagent cron` die passende Crontab-Zeile aus (sie wird nicht
selbst installiert). Jeder Abruf und jeder Lauf wird in der SQLite-Datenbank
protokolliert und im Cockpit angezeigt.

---

## 🖥️ Nutzung

### 🌐 Web-UI

```bash
pip install -e ".[ui,browser]"
streamlit run app.py        # oder ./start-ui.sh
```

Öffne `http://localhost:8501`:

1. **👤 Profil & Einstellungen** – Daten, Formular-Angaben, Lebenslauf, Suchbegriffe, Automatik
2. **🏠 Cockpit** – hier wartet jeden Morgen die Auswahl: **✅ Ja** schickt ab, **❌ Nein** blendet aus
3. **🧭 Schritt für Schritt** – selbst suchen (mit Vorschlägen), Stellen auswählen, freigeben
4. **📬 Bewerbungen** – Rückmeldungen, Nachfassen, Nachweis der Eigenbemühungen

Siehe [UI_README.md](UI_README.md) für Details.

### 💻 CLI (Kommandozeile)

```bash
# Automatik: suchen, bewerten, beste Stellen vorbereiten
bewerbungsagent automatik
bewerbungsagent warteschlange            # was wartet auf dein Ja?
bewerbungsagent freigeben <ref> --senden # Ja + sofort verschicken
bewerbungsagent ablehnen <ref> --grund "zu weit weg"

# Einzelschritte
bewerbungsagent suchen --was "Data Engineer" --wo Hamburg --umkreis 50
bewerbungsagent bewerten [--ohne-llm]
bewerbungsagent top --limit 15 --offen
bewerbungsagent vorbereiten <ref> [<ref> ...]
bewerbungsagent senden

# Suche verbessern und Nachweis
bewerbungsagent vorschlaege --ki
bewerbungsagent nachweis --von 2026-10-01 --bis 2026-10-31 --format pdf

# Browser-Agent direkt (Standard: Probelauf, mit --absenden echt)
bewerbungsagent bewerben <ref> [--absenden]

# Zeitplan
bewerbungsagent tick                     # Cron-Einstieg
bewerbungsagent cron                     # Crontab-Zeile ausgeben
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
| **UI** | Streamlit |
| **CLI** | Python argparse + Rich |
| **API** | Bundesagentur für Arbeit Jobsuche-API |
| **LLM** | Anthropic Claude, OpenAI oder Ollama |
| **Browser** | browser-use + Playwright |
| **Storage** | SQLite |
| **Dokumente** | fpdf2 (PDF), pypdf (Lebenslauf) |
| **Versand** | SMTP, browser-use |
| **Konfiguration** | YAML-Profil |
| **Sprache** | Python 3.11+ |

---

## 📁 Projektstruktur

```
bewerbung-agent/
├── app.py                        # Web-UI: Navigation
├── seiten/                       # Cockpit, Schritt für Schritt, Bewerbungen, Profil
├── bewerbungsagent/
│   ├── ablauf.py                 # suchen → bewerten → vorbereiten → freigeben → senden
│   ├── zeitplan.py               # Cron-Ausdrücke (Tick-Logik)
│   ├── vorschlaege.py            # Suchvorschläge und Such-Tipps
│   ├── dokumente.py              # Anschreiben-PDF, Lebenslauftext, Nachweis
│   ├── hintergrund.py            # Hintergrundprozesse und Sperren
│   ├── ui.py                     # gemeinsame UI-Bausteine (Ja/Nein-Karte …)
│   ├── cli.py                    # Kommandozeile
│   ├── config.py                 # Profil-YAML
│   ├── models.py                 # Job, Score, Application, Vorgang
│   ├── db.py                     # SQLite-Speicher
│   ├── sources/                  # Bundesagentur für Arbeit, GET IN IT
│   ├── scoring/                  # Sentiment, Heuristik, LLM
│   ├── anschreiben.py            # Anschreiben-Generator
│   └── bewerben/                 # browser.py (Formulare), mail.py (SMTP), kanal.py
├── config/profil.example.yaml
├── tests/
└── pyproject.toml
```

---

## 👤 Autor

**Mark Baumann** — [GitHub](https://github.com/mark-baumann) · [markb.de](https://markb.de)
