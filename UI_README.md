# 🌐 Bewerbungsagent – Web-UI

Streamlit-Oberfläche für den kompletten Bewerbungsablauf. Ziel: Der Agent erledigt
Suche, Bewertung, Anschreiben und Versand – du entscheidest nur noch **Ja** oder **Nein**.

## 🚀 Start

```bash
pip install -e ".[ui,browser]"     # browser = Online-Formulare per browser-use
playwright install chromium
streamlit run app.py               # http://localhost:8501
```

Zugangsdaten (KI, SMTP, IMAP, BA-Login, Jobbörsen-Keys) trägst du unter
**Profil → 🔑 Zugänge** ein. Sie liegen in `daten/zugangsdaten.json` (Rechte 0600, nicht
im Profil, nicht im Git), gelten sofort für UI, Hintergrund-Worker und Cron und haben
Vorrang vor `.env` bzw. Docker-Umgebung.

## 🧭 Seiten

### 🏠 Cockpit (Startseite)
- **Einladungen zuerst:** vom Postfach erkannte Einladungen/Zusagen stehen ganz oben
- **Einrichtungs-Check:** zeigt, was noch fehlt (Lebenslauf, KI-Zugang inkl. Modellprüfung, SMTP,
  Browser-Agent, Formular-Angaben)
- **Kennzahlen:** wartet auf dich · diese Woche beworben (mit Wochenziel) · Rückmeldungen · weitere passende Stellen
- **Automatik:** Modus *Automatisch* / *Nur suchen* / *Aus*, Zeitplan mit nächstem Lauf,
  Knopf **„Jetzt suchen & vorbereiten“** (läuft im Hintergrund) und der **Autopilot**
  (*Aus* / *Mit Einspruchsfrist* / *Sofort*)
- **Deine Entscheidung:** pro vorbereiteter Bewerbung eine Karte mit Passung, Gründen,
  Versandweg (E-Mail-Empfänger bzw. Formular-Domain), Probelauf-Ergebnis und
  bearbeitbarem Anschreiben. Dazu die **Qualität des Anschreibens** aus der Selbstprüfung,
  Warnungen zu Aussagen, die der Lebenslauf nicht belegt, und – falls das Formular noch etwas
  wissen will – Eingabefelder für genau diese **fehlenden Angaben** (werden im Profil gemerkt).
  **✅ Ja, absenden** verschickt sofort, **❌ Nein** (mit Grund, optional „Arbeitgeber nie
  wieder“) blendet die Stelle aus, **🔄 Neu** erzeugt Anschreiben und Probelauf neu. Hat der
  Autopilot die Karte mit Einspruchsfrist versehen, hält **✋ Stopp** sie an.
- **Der Agent schlägt vor:** Nachfass-Mails (Text bearbeitbar, Ja sendet im selben Thread)
  und gelernte Änderungen wie „Zeitarbeit ausschließen?“ oder „Suchbegriff übernehmen?“.
- **Braucht deine Hilfe:** fehlgeschlagene Sendungen mit Ursache, „Nochmal senden“,
  Link zum Selbstbewerben, Anschreiben zum Kopieren und „Selbst erledigt“.

### 🧭 Schritt für Schritt
Der manuelle Weg als geführter Prozess mit Fortschrittsleiste:
1. **Suchen** – Suchbegriffe, Ort, Umkreis, Zeitraum, Jobbörsen. Daneben **Ideen für deine Suche**:
   ähnliche Jobtitel aus gut passenden Treffern, KI-Vorschläge, Tipps (z. B. Umkreis erweitern)
   und eine Auswertung, welche Suchbegriffe passende Stellen bringen. Nach der Suche wird
   automatisch bewertet.
2. **Auswählen** – offene Stellen nach Passung, Anzeige lesen, 👍 anhaken oder 👎 „nicht interessant“.
3. **Prüfen & freigeben** – Anschreiben und Probelauf entstehen im Hintergrund; danach dieselben
   Ja/Nein-Karten wie im Cockpit.
4. **Status** – was gerade verschickt wird, was heute rausging, was Hilfe braucht.

### 📬 Bewerbungen
- **Übersicht:** alle Vorgänge mit Status; Rückmeldung (Eingang, Einladung, Absage, Zusage)
  und Notiz direkt in der Tabelle eintragen; Details mit Anschreiben und Protokoll.
- **Nachfassen:** Bewerbungen ohne Rückmeldung nach X Tagen (Profil), mit fertigem Text – bei
  E-Mail-Bewerbungen per Ja direkt im selben Thread.
- **Antworten im Postfach:** in den Details jeder Bewerbung, sofern IMAP eingerichtet ist.
- **Nachweis fürs Amt:** Eigenbemühungen eines Zeitraums als PDF oder CSV.

### 👤 Profil & Einstellungen
- **Persönliche Daten** inkl. **Angaben für Formulare** (Anrede, Titel, verfügbar ab, Kündigungsfrist,
  Gehaltsvorstellung, Arbeitserlaubnis, Staatsangehörigkeit, Abschluss, Berufserfahrung,
  Umzugsbereitschaft, Führerschein, Sprachen, weitere gelernte Antworten)
- **Suche & Bewertung** (Suchbegriffe, Skills, Ausschlüsse, gesperrte Arbeitgeber, Mindest-Passung)
- **🌐 Jobbörsen** (jede Quelle an/aus mit letztem Abruf, Remote, Ausbildung, Zeitarbeit,
  EURES-Länder) und **Wunsch-Arbeitgeber** (Karriereseite einfügen → Bewerbermanagement
  wird erkannt)
- **Unterlagen** (Lebenslauf, Zeugnisse, Anschreiben-Stil; Vorschau des gelesenen Lebenslauftexts)
- **Automatik & Versand** (Modus, Zeitplan, Limits, Probelauf, Wochenziel, SMTP-Testmail,
  selbstständiges Dazulernen, Kostenbremse, Autopilot mit Schutzgrenzen)
- **🔑 Zugänge** (SMTP, IMAP, KI, BA-Login, Jobbörsen-Keys – maskiert, mit Verbindungstest;
  KI-Modell aus der Liste, die dein Anbieter wirklich anbietet)

## ⚙️ Wie Hintergrundarbeit läuft

Vorbereiten (mit Probelauf) und Versand dauern Minuten. Die UI startet dafür
`bewerbungsagent vorbereiten …` bzw. `bewerbungsagent senden` als eigenen Prozess
(Ausgabe in `daten/worker.log`) und aktualisiert die Anzeige automatisch. Der
Cron-Tick (alle 5 Minuten) verschickt freigegebene Bewerbungen auch dann, wenn die
Seite geschlossen ist. Dateisperren verhindern doppelte Läufe; jede Bewerbung kann
nur einmal beansprucht und gesendet werden.

## 🛠️ Struktur

```
app.py                       # Navigation (st.navigation)
seiten/
├── cockpit.py               # Startseite: Automatik + Ja/Nein
├── schritt_fuer_schritt.py  # geführter manueller Prozess
├── bewerbungen.py           # Tracker, Nachfassen, Nachweis
└── profil.py                # Profil & Einstellungen
bewerbungsagent/ui.py        # gemeinsame Bausteine (Karten, Einrichtungs-Check, Laufstatus)
bewerbungsagent/ui_einstellungen.py  # Tabs „Jobbörsen“ und „Zugänge“
```

Die Seiten liegen bewusst nicht in `pages/`, damit Streamlit nur die Navigation aus
`app.py` verwendet.

## 🐛 Troubleshooting

- **„E-Mail-Versand ist nicht eingerichtet“** → Profil → 🔑 Zugänge → SMTP eintragen und testen.
  Bereits freigegebene Bewerbungen gehen dann automatisch raus.
- **„The model … does not exist“ / model not found** → Profil → 🔑 Zugänge → KI-Modell aus der
  Liste wählen. Der Agent weicht inzwischen selbst auf ein verfügbares Modell aus und zeigt das
  im Einrichtungs-Check an (`bewerbungsagent modelle` listet die Modelle deines Zugangs).
- **„Browser-Agent ist nicht installiert“** → `pip install -e ".[browser]"` und `playwright install chromium`.
- **Formular braucht Login/Captcha** → Die Karte schlägt „Selbst bewerben“ vor; Link öffnen,
  Anschreiben kopieren, danach „Erledigt“.
- **Es passiert nichts nach „Ja“** → `daten/worker.log` bzw. `daten/cron.log` ansehen.
- **Keine Vorschläge im Cockpit** → Mindest-Passung im Profil senken oder unter „Schritt für Schritt“
  die Suchvorschläge nutzen.
