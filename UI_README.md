# 🌐 Bewerbungsagent – Web-UI

Streamlit-Oberfläche für den kompletten Bewerbungsablauf. Ziel: Der Agent erledigt
Suche, Bewertung, Anschreiben und Versand – du entscheidest nur noch **Ja** oder **Nein**.
Gebaut für Desktop **und** Handy: drei Ziele in einer Leiste oben, Karten mit fester
Anatomie, Material-Icons, ein ruhiges Teal-Farbschema (`.streamlit/config.toml`).

## 🚀 Start

```bash
pip install -e ".[ui,browser]"     # browser = Online-Formulare per browser-use
streamlit run app.py               # http://localhost:8501
```

Beim ersten Start führt ein **Assistent** in fünf Schritten durch die Einrichtung:
Wer bist du → Was suchst du → Lebenslauf → E-Mail-Konto → Los geht's. Erst danach
startet die Automatik.

Zugangsdaten (E-Mail-Konto, KI, BA-Login, Jobbörsen-Keys) liegen in
`daten/zugangsdaten.json` (Rechte 0600, nicht im Profil, nicht im Git), gelten sofort für
UI, Hintergrund-Worker und Cron und haben Vorrang vor `.env` bzw. Docker-Umgebung.

## 🧭 Seiten

### Heute (Startseite)
- **Begrüßung + Statuszeile:** wie viele warten auf dein Ja, Wochenziel, Einladungen, Autopilot
- **Startklar x von 5:** was noch fehlt (Kontaktdaten, Lebenslauf, E-Mail-Konto, KI,
  Online-Formulare) – jeweils mit Knopf, der direkt einen Dialog öffnet (hochladen,
  verbinden, einrichten, Browser testen)
- **Einladungen zuerst:** vom Postfach erkannte Einladungen/Zusagen
- **Deine Entscheidung:** pro vorbereiteter Bewerbung eine Karte: Titel, Firma · Ort · Quelle,
  eine Zeile Badges (Passung, Versandweg, Qualität des Anschreibens, Formular-Test),
  Warnungen nur wenn nötig (unbelegte Aussagen, fehlende Formular-Angaben – vorbelegt aus dem
  Profil), eine Vorschau des Anschreibens und unten **[Lesen & bearbeiten] [Mehr] [Nein] [Ja, absenden]**.
  „Lesen & bearbeiten“ öffnet einen Dialog mit Versandweg, Empfänger, E-Mail-Text und
  Anschreiben; „Mehr“ enthält Anzeige, „Neu schreiben lassen“ (mit Hinweis) und „Selbst beworben“.
- **Braucht deine Hilfe:** fehlgeschlagene Sendungen mit Ursache und dem einfachsten Ausweg
  (per E-Mail statt Formular, nochmal versuchen, selbst bewerben, Browser testen)
- **Ideen von mir:** Nachfass-Mails (Text bearbeitbar) und gelernte Änderungen; neue Suchbegriffe
  gebündelt in einer Karte zum Antippen
- **Deine Bewerbungen:** verschickte Bewerbungen mit Rückmeldung zum Antippen (Eingang,
  Einladung, Absage, Zusage) und **Nachweis fürs Amt** (Zeitraum wählen, PDF oder Excel)
- **Automatik:** in Klartext („Ich suche und bereite vor täglich 6:00 · nächstes Mal …“),
  letzter Lauf, „Ändern“ und „Jetzt suchen“

### Suchen
1. **Suchen** – Jobtitel (mit ähnlichen Jobtiteln zum Antippen und KI-Ideen), Ort, Umkreis;
   weitere Filter eingeklappt. Tipps und die Auswertung „Was bringen deine Jobtitel?“ darunter.
2. **Auswählen** – Stellen nach Passung, Anzeige lesen, anhaken oder „Nicht interessant“.
   **„Weiter: N Bewerbung(en) vorbereiten“** startet Anschreiben (und Formular-Test) im
   Hintergrund und springt nach **Heute** – dort wird entschieden.

### Einstellungen
- **Über mich:** Kontakt, „Kurz über dich“, Angaben für Online-Formulare, Unterlagen
  (Lebenslauf, Zeugnisse – gespeichert im Datenordner, überleben Updates), Stil der Anschreiben
- **Suche:** Jobtitel, Ort, Umkreis, Mindest-Passung, Feinheiten der Bewertung; Jobbörsen
  (an/aus mit letztem Abruf, Remote, Ausbildung, Zeitarbeit, EURES) und Wunsch-Arbeitgeber
- **Automatik:** was regelmäßig passiert, wann (Klartext-Auswahl, eigener Zeitplan nur für
  Fortgeschrittene), Grenzen, Wochenziel, Formular-Test, Autopilot mit Schutzgrenzen, Dazulernen
- **Konten:** E-Mail-Konto (Adresse + Passwort, Anbieter wird erkannt, Testmail), KI (Schlüssel,
  Test, Modellwahl), Online-Formulare (Browser-Test), Arbeitsagentur-Login, Jobbörsen-Keys;
  Mail-Server von Hand nur eingeklappt für Fortgeschrittene

## ⚙️ Wie Hintergrundarbeit läuft

Vorbereiten (mit Formular-Test) und Versand dauern Minuten. Die UI startet dafür
`bewerbungsagent vorbereiten …` bzw. `bewerbungsagent senden` als eigenen Prozess
(Ausgabe in `daten/worker.log`) und aktualisiert die Anzeige automatisch. Der
Cron-Tick (alle 5 Minuten) verschickt freigegebene Bewerbungen auch dann, wenn die
Seite geschlossen ist. Dateisperren verhindern doppelte Läufe; jede Bewerbung kann
nur einmal beansprucht und gesendet werden.

## 🛠️ Struktur

```
app.py                       # Navigation (versteckt) + eigene Leiste oben
.streamlit/config.toml       # Farben, Rundungen, schlanke Werkzeugleiste
seiten/
├── cockpit.py               # Heute: Entscheidungen, Hilfe, Bewerbungen, Nachweis, Automatik
├── schritt_fuer_schritt.py  # Suchen: Suchen → Auswählen
└── profil.py                # Einstellungen: Über mich · Suche · Automatik · Konten
bewerbungsagent/ui.py              # gemeinsame Bausteine (Karten, Dialoge, Startklar, Laufstatus)
bewerbungsagent/ui_assistent.py    # Erststart-Assistent
bewerbungsagent/ui_einstellungen.py  # Konten, Lebenslauf-Upload, Jobbörsen
```

Die Seiten liegen bewusst nicht in `pages/`, damit Streamlit nur die Navigation aus
`app.py` verwendet.

## 🐛 Troubleshooting

- **„Dein E-Mail-Konto ist noch nicht verbunden“** → Einstellungen → Konten → Adresse und
  Passwort eingeben. Bereits freigegebene Bewerbungen gehen dann automatisch raus.
- **„The model … does not exist“ / model not found** → Einstellungen → Konten → KI-Modell aus
  der Liste wählen. Der Agent weicht selbst auf ein verfügbares Modell aus und zeigt das an
  (`bewerbungsagent modelle` listet die Modelle deines Zugangs).
- **Online-Formulare scheitern** → Einstellungen → Konten → „Browser testen“ (oder
  `bewerbungsagent browser-test`). Startet der Browser nicht, bleiben Formular-Bewerbungen
  freigegeben und gehen beim nächsten Lauf raus.
- **Formular braucht Login/Captcha** → Die Karte schlägt „Selbst bewerben“ vor; Link öffnen,
  Anschreiben kopieren, danach „Habe mich selbst beworben“.
- **Es passiert nichts nach „Ja“** → `daten/worker.log` bzw. `daten/cron.log` ansehen.
- **Keine Vorschläge auf Heute** → Mindest-Passung unter Einstellungen → Suche senken oder
  unter „Suchen“ die ähnlichen Jobtitel nutzen.
