# 🤖 Bewerbungs-Agent — Automatisierte Jobsuche & Bewerbung

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![OpenAI](https://img.shields.io/badge/LLM-ChatGPT-412991?logo=openai)](https://openai.com)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-black?logo=ollama)](https://ollama.com)
[![Browser-Use](https://img.shields.io/badge/Automation-browser--use-green)](https://github.com/browser-use/browser-use)
[![Tests](https://img.shields.io/badge/Tests-Pytest-green?logo=pytest)](https://pytest.org)

**Vom Suchen bis zum Absenden – du sagst nur noch Ja oder Nein.** Der Agent sucht
Stellen bei über zehn Jobbörsen und direkt bei deinen Wunsch-Arbeitgebern, bewertet
sie gegen Profil und Lebenslauf, schreibt Anschreiben, prüft sie selbst auf
unbelegte Aussagen, testet Online-Formulare im Probelauf und schickt Bewerbungen
nach deinem **Ja** per E-Mail oder Formular ab. Er lernt aus deinen Entscheidungen,
erkennt Antworten im Postfach und schlägt Nachfass-Mails vor. Wer will, schaltet
den **Autopilot** ein – dann bewirbt sich die KI bei sehr gut passenden Stellen allein.

```
Zeitplan (Cron) oder Knopf
  └─ suchen (10+ Jobbörsen, Wunsch-Arbeitgeber, neue Suchbegriffe selbst testen)
       └─ bewerten (KI + Stichworte, gelernte Vorlieben) ─► Stellen-Dossier
            └─ Anschreiben schreiben ─► selbst prüfen & überarbeiten ─► Probelauf
                 └─ Startseite: ✅ Ja / ❌ Nein   (oder Autopilot mit Schutzgrenzen)
                      └─ Versand per E-Mail/Formular ─► Postfach: Eingang, Einladung, Absage
                           └─ Nachfassen nach 14 Tagen (Ja/Nein) · Nachweis fürs Amt
```

---

## ✨ Features

- **🏠 „Heute“ mit Ja/Nein:** Vorbereitete Bewerbungen als kompakte Karten (Passung,
  Versandweg, Formular-Test, Anschreiben-Vorschau zum Lesen/Bearbeiten) – ein Klick schickt ab;
  läuft auch bequem am Handy
- **👋 Erststart-Assistent:** In fünf Schritten startklar – Daten, Jobtitel, Lebenslauf,
  E-Mail-Konto (nur Adresse + Passwort, Server werden automatisch erkannt), los
- **🤖 Automatik per Zeitplan:** Suchen, bewerten und die besten Stellen vorbereiten
  (z. B. täglich 6:00); Modus *Automatisch*, *Nur suchen* oder *Aus*
- **🔍 Selbst suchen:** Suchen → Stellen auswählen; die Entscheidung fällt dann auf „Heute“
- **🌐 Viele Jobbörsen:** Bundesagentur für Arbeit, GET IN IT, Arbeitnow, service.bund.de,
  Interamt, EURES (EU-Ausland), Remote-Börsen (Himalayas, Remotive, Remote OK, Jobicy,
  We Work Remotely), Adzuna und Jooble (mit kostenlosem Key) – Dubletten werden zusammengeführt
- **🏢 Wunsch-Arbeitgeber:** Karriereseite einfügen – der Agent erkennt Greenhouse, Lever,
  SmartRecruiters, Personio, Recruitee oder Ashby und holt neue Stellen direkt von dort
- **💡 Suchvorschläge & Erkundung:** Ähnliche Jobtitel, KI-Ideen und Tipps; die Automatik
  probiert selbst neue Suchbegriffe aus und behält nur, was passende Stellen bringt
- **✍️ Geprüfte Anschreiben:** Stellen-Dossier (Muss-Anforderungen, Ansprechpartner mit
  persönlicher Anrede, Kennziffer, Frist); jede Aussage wird gegen den Lebenslauf geprüft,
  bei Mängeln einmal überarbeitet – mit Qualitätswert auf der Karte
- **🧠 Lernt aus Ja/Nein:** Vorlieben beeinflussen die Reihenfolge; Profiländerungen
  (z. B. „Zeitarbeit ausschließen?“) kommen als Ja/Nein-Vorschläge
- **📥 Postfach & Nachfassen:** Antworten werden per IMAP (nur lesend) erkannt und
  zugeordnet; nach 14 Tagen ohne Antwort liegt eine Nachfass-Mail zur Freigabe bereit
- **🚀 Autopilot (optional, Standard aus):** Die KI bewirbt sich allein – nur bei hoher
  Passung, geprüftem Anschreiben, sicherem Versandweg, mit Tageslimit, höchstens einmal je
  Arbeitgeber in 30 Tagen, wahlweise mit Einspruchsfrist und Stopp-Knopf
- **🔑 Konten in der UI:** E-Mail-Konto mit Adresse + Passwort verbinden (Anbieter wird
  erkannt), KI-Schlüssel, Browser-Test und Jobbörsen-Keys unter Einstellungen → Konten –
  ohne `.env` und ohne Neustart
- **📧 Versand per E-Mail:** Aus deinem Postfach (SMTP) mit Anschreiben-PDF, Lebenslauf
  und Zeugnissen, Kopie per Bcc an dich
- **🌐 Online-Formulare:** browser-use füllt Bewerbungsformulare aus – inklusive
  gängiger Bewerbermanagement-Systeme (softgarden, Personio, Workday, …)
- **📊 Bewertung:** Skill-Matching, Tonalität der Anzeige und LLM-Bewertung (Claude,
  ChatGPT oder Ollama) mit deinem Lebenslauf als Kontext
- **📬 Rückmeldungen & Nachweis:** Antworten auf „Heute“ antippen, Nachfass-Mails als
  Vorschlag, Export der Eigenbemühungen als PDF/CSV für Agentur für Arbeit oder Jobcenter
- **🛡️ Sicherheitsnetze:** Ohne Autopilot geht nichts ohne dein Ja raus, Tageslimit, nie doppelt
  senden (war der Agent schon im Formular, fragt er nach statt zu wiederholen), keine erfundenen Angaben,
  keine Jobbörsen, deren Bedingungen automatische Abfragen verbieten (StepStone, Indeed,
  LinkedIn, XING)

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

Am einfachsten über die Web-UI: Beim ersten Start führt ein Assistent durch Daten,
Lebenslauf, Jobtitel und E-Mail-Konto – aus dem Lebenslauf liest der Agent Kurzprofil, Jobtitel,
Ort und Fähigkeiten selbst (Seite **Lebenslauf**). Alles Weitere unter **Einstellungen** (Über mich ·
Suche · Automatik · Konten) – dort auch KI-Schlüssel eintragen und mit einem Klick testen.
Die Zugänge liegen in `daten/zugangsdaten.json` (nur für dich lesbar, nie im Git) und
haben Vorrang vor der `.env`. Alternativ per Datei:

```bash
cp config/profil.example.yaml config/profil.yaml
cp .env.example .env                 # optional: Zugänge statt über die UI
$EDITOR config/profil.yaml           # Person, Suchbegriffe, Skills, Ausschlüsse
```

### 🤖 LLM-Konfiguration

Entweder OpenAI/ChatGPT, Anthropic oder ein lokaler/Ollama-Server (UI: Einstellungen → Konten):

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# ChatGPT / OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# ODER Ollama (lokal oder zentral)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=                      # nur falls der Server einen Key verlangt
OLLAMA_MODEL=                        # optional; sonst Auswahl in der UI
```

Claude-Modelle (`claude-*`) laufen über Anthropic, GPT-/o-Modelle über die
OpenAI-API und die übrigen Modelle über Ollama. Der Agent fragt beim Anbieter ab,
welche Modelle es wirklich gibt (`bewerbungsagent modelle`): Fehlt das gewählte
Modell (*model not found*), nimmt er ein verfügbares – bei Ollama zuerst `OLLAMA_MODEL`, dann
`gpt-oss:120b`, sonst das größte, nie ein Bezahlmodell eines anderen Anbieters – und schreibt es fest
ins Profil. Ohne Key fällt die Bewertung auf die Heuristik und das Anschreiben auf
die Vorlage zurück.

### 📧 Bewerbungen per E-Mail

Für den E-Mail-Versand verbindest du dein Postfach unter **Einstellungen → Konten**
(Adresse + Passwort genügen, Server und Ports werden automatisch erkannt) – oder trägst
die Zugangsdaten in die `.env` bzw. die Umgebung des Containers ein.
Mit IMAP-Zugang erkennt der Agent außerdem Antworten (Eingangsbestätigung,
Einladung, Absage) selbst:

```bash
SMTP_HOST=smtp.example.de
SMTP_PORT=465                 # 465 = SSL, 587 = STARTTLS
SMTP_BENUTZER=ich@example.de
SMTP_PASSWORT=...
SMTP_ABSENDER=ich@example.de  # optional
IMAP_HOST=imap.example.de     # optional: Antworten erkennen (Login wie SMTP)
```

Unter **Profil → Automatik & Versand** kannst du eine Testmail an dich selbst
schicken. Jede Bewerbung geht als Bcc auch an dich.

### ⏰ Automatik und Zeitplan

Im Docker-Container ruft Cron alle 5 Minuten `bewerbungsagent tick` auf. Der Tick
startet den Automatiklauf, sobald der Zeitplan aus dem Profil fällig ist
(Standard: täglich 06:00 deutscher Zeit), und verschickt freigegebene
Bewerbungen. Modus und Zeitplan änderst du unter **Einstellungen → Automatik** – ohne
Neustart.

```yaml
automatisierung:
  modus: automatisch    # automatisch | nur_suchen | aus
  cron: "0 6 * * *"     # Minute Stunde Tag Monat Wochentag
  max_pro_lauf: 5       # so viele Bewerbungen bereitet ein Lauf vor
  max_pro_tag: 10       # Sicherheitsgrenze für Sendungen pro Tag
  probelauf_vorab: true # Online-Formular vorab testweise ausfüllen
  ziel_pro_woche: 5
  autopilot: aus        # aus | mit_frist | sofort  – die KI bewirbt sich allein
  autopilot_frist_stunden: 2
  erkunden: true        # neue Suchbegriffe selbst ausprobieren
  postfach: true        # Antworten per IMAP erkennen
  nachfassen_tage: 14
```

### 🚀 Autopilot

Standard ist **aus**. Eingeschaltet (Einstellungen → Automatik) verschickt die
KI eine vorbereitete Bewerbung nur, wenn alles stimmt: Passung ≥ 75, KI-Anschreiben
mit Qualität ≥ 80 und ohne unbelegte Aussagen, eindeutige Bewerbungsadresse bzw.
erfolgreicher Formular-Probelauf, Versandweg eingerichtet, Frist nicht abgelaufen,
Tageslimit frei und höchstens eine Bewerbung je Arbeitgeber in 30 Tagen. Im Modus
*mit Einspruchsfrist* steht die Karte erst X Stunden mit **Stopp**-Knopf auf
„Heute“. Alles, was eine Grenze verfehlt, bleibt eine normale Ja/Nein-Karte – mit
der Begründung.

Lokal gibt `bewerbungsagent cron` die passende Crontab-Zeile aus (sie wird nicht
selbst installiert). Jeder Abruf und jeder Lauf wird in der SQLite-Datenbank
protokolliert und auf „Heute“ angezeigt.

---

## 🖥️ Nutzung

### 🌐 Web-UI

```bash
pip install -e ".[ui,browser]"
streamlit run app.py        # oder ./start-ui.sh
```

Öffne `http://localhost:8501`:

1. **Heute** – beim ersten Start der Einrichtungs-Assistent; danach nur Ja/Nein: **Ja** schickt ab,
   **Nein** blendet aus, dazu Ideen des Agenten und deine Bewerbungen mit Rückmeldung. Klappt ein
   Formular nicht, weicht der Agent selbst aus (per E-Mail oder als Karte „Selbst bewerben“)
2. **Suchen** – auf einer Seite suchen (Jobtitel aus dem Lebenslauf) und Treffer ankreuzen
3. **Lebenslauf** – Datei, was der Agent daraus liest (wird automatisch übernommen), Zeugnisse
4. **Einstellungen** – Über mich (mit **Nachweis fürs Amt**) · Suche · Automatik · Konten

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

# Suche verbessern, KI-Modelle, Postfach und Nachweis
bewerbungsagent vorschlaege --ki
bewerbungsagent modelle                  # welche KI-Modelle bietet mein Zugang?
bewerbungsagent postfach                 # Antworten jetzt auswerten
bewerbungsagent suchen --quelle arbeitnow --quelle interamt
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
| **Jobbörsen** | BA-Jobsuche, GET IN IT, Arbeitnow, service.bund.de, Interamt, EURES, Remote-Börsen, Adzuna, Jooble, ATS-APIs |
| **LLM** | Anthropic Claude, OpenAI oder Ollama |
| **Browser** | browser-use + Playwright |
| **Storage** | SQLite |
| **Dokumente** | fpdf2 (PDF), pypdf (Lebenslauf) |
| **Versand** | SMTP, browser-use; IMAP (nur lesend) für Antworten |
| **Konfiguration** | YAML-Profil |
| **Sprache** | Python 3.11+ |

---

## 📁 Projektstruktur

```
bewerbung-agent/
├── app.py                        # Web-UI: Navigation
├── seiten/                       # Heute (cockpit.py), Suchen, Einstellungen (profil.py)
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
│   ├── sources/                  # Jobbörsen-Registry, BA, GET IN IT, Arbeitnow, öffentl. Dienst,
│   │                             # EURES/Adzuna/Jooble, Remote-Börsen, ATS der Wunsch-Arbeitgeber
│   ├── jobsuche.py               # protokollierte Abrufe, Feeds, lokale Filterung
│   ├── scoring/                  # Sentiment, Heuristik, LLM (inkl. Modellprüfung)
│   ├── dossier.py                # Stellen-Dossier: Anforderungen, Anrede, Kennziffer, Frist
│   ├── anschreiben.py            # Anschreiben-Generator (mit Selbstprüfung)
│   ├── pruefung.py               # Belegprüfung & KI-Gutachten der Anschreiben
│   ├── lernen.py                 # Vorlieben aus Ja/Nein, Vorschläge
│   ├── autopilot.py              # optionaler Autopilot mit Schutzgrenzen
│   ├── nachfassen.py             # Nachfass-Mails als Ja/Nein-Karte
│   ├── zugangsdaten.py           # Zugänge aus der UI (daten/zugangsdaten.json)
│   └── bewerben/                 # browser.py (Formulare), mail.py (SMTP), postfach.py (IMAP), kanal.py
├── config/profil.example.yaml
├── tests/
└── pyproject.toml
```

---

## 👤 Autor

**Mark Baumann** — [GitHub](https://github.com/mark-baumann) · [markb.de](https://markb.de)
