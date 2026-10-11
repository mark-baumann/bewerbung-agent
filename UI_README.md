# 🌐 Bewerbungsagent – Web-UI

Streamlit-Oberfläche für den kompletten Bewerbungsablauf. Ziel: Der Agent erledigt
Suche, Bewertung, Anschreiben und Versand – du entscheidest nur noch **Ja** oder **Nein**.

## 🚀 Start

```bash
pip install -e ".[ui,browser]"     # browser = Online-Formulare per browser-use
playwright install chromium
streamlit run app.py               # http://localhost:8501
```

Zugangsdaten (LLM, SMTP, optional BA-Login) liegen in der `.env` im Projektordner;
die UI liest sie beim Start ein. Im Docker-Container kommen sie aus der Umgebung.

## 🧭 Seiten

### 🏠 Cockpit (Startseite)
- **Einrichtungs-Check:** zeigt, was noch fehlt (Lebenslauf, KI-Key, SMTP, Browser-Agent, Formular-Angaben)
- **Kennzahlen:** wartet auf dich · diese Woche beworben (mit Wochenziel) · Rückmeldungen · weitere passende Stellen
- **Automatik:** Modus *Automatisch* / *Nur suchen* / *Aus*, Zeitplan mit nächstem Lauf,
  Knopf **„Jetzt suchen & vorbereiten“** (läuft im Hintergrund)
- **Deine Entscheidung:** pro vorbereiteter Bewerbung eine Karte mit Passung, Gründen,
  Versandweg (E-Mail-Empfänger bzw. Formular-Domain), Probelauf-Ergebnis und
  bearbeitbarem Anschreiben. **✅ Ja, absenden** verschickt sofort, **❌ Nein** (mit Grund,
  optional „Arbeitgeber nie wieder“) blendet die Stelle aus, **🔄 Neu** erzeugt Anschreiben
  und Probelauf neu.
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
- **Nachfassen:** Bewerbungen ohne Rückmeldung nach 14 Tagen, mit Textvorschlag für die Nachfrage.
- **Nachweis fürs Amt:** Eigenbemühungen eines Zeitraums als PDF oder CSV.

### 👤 Profil & Einstellungen
- **Persönliche Daten** inkl. **Angaben für Formulare** (Anrede, verfügbar ab, Kündigungsfrist,
  Gehaltsvorstellung, Arbeitserlaubnis, Führerschein, Sprachen)
- **Suche & Bewertung** (Suchbegriffe, Skills, Ausschlüsse, gesperrte Arbeitgeber, Mindest-Passung)
- **Unterlagen** (Lebenslauf, Zeugnisse, Anschreiben-Stil; Vorschau des gelesenen Lebenslauftexts)
- **Automatik & Versand** (Modus, Zeitplan, Limits, Probelauf, Wochenziel, SMTP-Testmail, KI-Modell)

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
```

Die Seiten liegen bewusst nicht in `pages/`, damit Streamlit nur die Navigation aus
`app.py` verwendet.

## 🐛 Troubleshooting

- **„E-Mail-Versand ist nicht eingerichtet“** → `SMTP_*` in der `.env` setzen, unter Profil Testmail senden.
- **„Browser-Agent ist nicht installiert“** → `pip install -e ".[browser]"` und `playwright install chromium`.
- **Formular braucht Login/Captcha** → Die Karte schlägt „Selbst bewerben“ vor; Link öffnen,
  Anschreiben kopieren, danach „Erledigt“.
- **Es passiert nichts nach „Ja“** → `daten/worker.log` bzw. `daten/cron.log` ansehen.
- **Keine Vorschläge im Cockpit** → Mindest-Passung im Profil senken oder unter „Schritt für Schritt“
  die Suchvorschläge nutzen.
