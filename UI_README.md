# 🌐 Bewerbungsagent – Web-UI

Streamlit-Oberfläche für den kompletten Bewerbungsablauf. Ziel: Der Agent erledigt
Suche, Bewertung, Anschreiben und Versand – du entscheidest nur noch **Ja** oder **Nein**.
Gebaut für Desktop **und** Handy: fünf Ziele in einer Leiste oben (Heute · Suchen · Jobs · Lebenslauf ·
Einstellungen; am Handy ohne Symbole, damit alle in eine Zeile passen), Karten mit fester
Anatomie, Material-Icons, ein ruhiges Teal-Farbschema (`.streamlit/config.toml`).

## 🚀 Start

```bash
pip install -e ".[ui,browser]"     # browser = Online-Formulare per browser-use
streamlit run app.py               # http://localhost:8501
```

Beim ersten Start führt ein **Assistent** in fünf Schritten durch die Einrichtung:
Wer bist du → Lebenslauf → Was suchst du → E-Mail-Konto → Los geht's. Aus dem Lebenslauf
liest der Agent Jobtitel, Ort und Fähigkeiten – „Was suchst du“ ist dann schon ausgefüllt.
Erst danach startet die Automatik.

Zugangsdaten (E-Mail-Konto, KI, BA-Login, Jobbörsen-Keys) liegen in
`daten/zugangsdaten.json` (Rechte 0600, nicht im Profil, nicht im Git), gelten sofort für
UI, Hintergrund-Worker und Cron und haben Vorrang vor `.env` bzw. Docker-Umgebung.

## 🧭 Seiten

### Heute (Startseite) – nur Ja/Nein
- **Deine Entscheidung:** pro vorbereiteter Bewerbung eine Karte: Titel, Firma · Ort · Quelle,
  eine Zeile Badges (Passung, Versandweg, Qualität des Anschreibens, Formular-Test),
  Hinweise nur wenn nötig (unbelegte Aussagen, fehlende Formular-Angaben – vorbelegt aus dem
  Profil), eine Vorschau des Anschreibens und unten **[Lesen & bearbeiten] [Mehr] [Nein] [Ja, absenden]**.
  Geht es nur von Hand, heißt die Karte **„Selbst bewerben“**: **[Nein] [Zur Bewerbung ↗] [Ja, erledigt]** –
  das Anschreiben ist fertig (zum Kopieren unter „Lesen & bearbeiten“).
- **Ideen von mir:** Nachfass-Mails (Text bearbeitbar) und gelernte Änderungen als Ja/Nein; neue
  Suchbegriffe gebündelt in einer Karte zum Antippen
- **Deine Bewerbungen:** was gerade rausgeht, darunter verschickte Bewerbungen mit Rückmeldung zum
  Antippen (Eingang, Einladung, Absage, Zusage); Einladungen und Zusagen stehen oben und sind markiert
- Sonst nichts: keine Fehlerkarten, keine Einrichtungs-Checkliste. Fehlt der Lebenslauf, steht oben
  eine Zeile mit dem Weg dorthin.

**Klappt ein Online-Formular nicht**, weicht der Agent selbst aus: Gibt es eine sichere
Bewerbungsadresse und ist das E-Mail-Konto verbunden, geht die Bewerbung per E-Mail raus (sie hatte
ja schon dein Ja). Sonst kommt sie als Karte „Selbst bewerben“ zurück. War der Agent schon im
Formular, fragt die Karte nach einer Eingangsbestätigung – nichts wird doppelt geschickt; kam nichts an,
startet „Mehr → Nicht angekommen – automatisch neu bewerben“ einen neuen Versuch (das ist dein Ja). Geht eine
E-Mail nicht raus (z. B. Passwort geändert), kommt sie als Karte mit dem Grund zurück; „Ja“ versucht es erneut. Antwortet
die KI nicht (z. B. Konto ohne Guthaben) oder startet der Browser nicht, ist nichts passiert: die
Bewerbung bleibt freigegeben, ein neuer Versuch folgt nach 30 Minuten, nach 48 Stunden wird daraus
eine Karte „Selbst bewerben“.

### Suchen – eine Seite
Oben Jobtitel (aus dem Lebenslauf vorbelegt, ähnliche Jobtitel zum Antippen), Wo, Umkreis und
„Mehr Filter“ (Zeitraum, Treffer je Jobbörse, Mindest-Passung, Jobbörsen, Vollzeit, für die Automatik
übernehmen). Darunter direkt die Treffer nach Passung zum Ankreuzen – passt nichts über der
Mindest-Passung, zeigt die Seite die besten darunter. **„Weiter: N Bewerbung(en) vorbereiten“**
startet die Anschreiben im Hintergrund und springt nach **Heute**.

### Jobs (`/jobs`)
Alle Stellen, die deine Suchen gefunden haben – ohne Dubletten und ohne reine Feed-Einträge, mit Passung,
Arbeitgeber, Ort (mit Entfernung), Jobbörse, Funddatum und Link zur Anzeige. Oben Suche (Titel, Arbeitgeber,
Ort), Sortierung (Neueste · Beste Passung) und der Stand: offen, auf „Heute“, beworben, Nein gesagt, passt
nicht. Zeilen auswählen → **„Für N Stelle(n) Bewerbung vorbereiten“** schreibt die Anschreiben im
Hintergrund und springt nach **Heute** (nur für Stellen, die noch nicht bearbeitet sind). Von hier geht nie
etwas raus – abgeschickt wird erst mit deinem Ja.

### Lebenslauf
Die gespeicherte Datei (Name, Größe, Datum, Herunterladen, „Anderen Lebenslauf hochladen“; PDF, Word
oder Text, gespeichert im Datenordner). Darunter **„Das lese ich aus deinem Lebenslauf“** – daraus entstehen
die **Suchkriterien**:
- Jobtitel aus allen Stationen und Fähigkeiten.
- Suchort: die nächste größere Stadt im Pendelbereich, z. B. Giebing → München mit 30 km; ohne KI der
  Wohnort mit 50 km.
- Fähigkeiten und Themen für die Passung.
- Kurzprofil und Werdegang für das Anschreiben.

So wird ausgelesen und übernommen:
- Mit KI (bei Ollama bevorzugt `gpt-oss:120b`) einmal je neuem Inhalt; ohne KI nach Abschnitten (Über mich,
  Fähigkeiten, Erfahrung).
- Antwortet die KI nicht, steht der Grund da (z. B. „kein Guthaben“), und es folgt später ein neuer Versuch.
- Übernommen wird, ohne deine Änderungen zu überschreiben: eigene Jobtitel, Fähigkeiten und Themen bleiben
  dahinter erhalten (und werden nie abgeschnitten); ein von dir geänderter Ort oder Umkreis bleibt, auch ein
  bewusst leerer Ort (ganz Deutschland) – außer bei „Neu auslesen“.
- Werdegang: Rolle und Arbeitgeber werden nur genutzt, wenn die Zuordnung sicher ist (und bei der KI so im
  Lebenslauf steht); unsichere Stationen erscheinen grau und kommen nicht ins Anschreiben.
- Danach werden offene Stellen neu bewertet.

Knöpfe:
- **Jetzt damit suchen** startet die Suche.
- **Probe-Anschreiben** zeigt ein Anschreiben für die passendste offene Stelle (nur zum Ansehen).

Ganz unten Zeugnisse und weitere Unterlagen.

### Einstellungen
- **Über mich:** Kontakt, Angaben für Online-Formulare und **Nachweis fürs Amt** (Zeitraum wählen,
  PDF oder Excel)
- **Suche:** Jobtitel, Ort, Umkreis, Mindest-Passung, Feinheiten der Bewertung; Jobbörsen
  (an/aus mit letztem Abruf, Remote, Ausbildung, Zeitarbeit, EURES) und Wunsch-Arbeitgeber
- **Automatik:** was regelmäßig passiert, wann (Klartext-Auswahl, eigener Zeitplan nur für
  Fortgeschrittene), letzter Lauf, Grenzen, Wochenziel, Formular-Test, Autopilot mit Schutzgrenzen,
  Dazulernen
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
├── cockpit.py               # Heute: Ja/Nein-Karten, Ideen, deine Bewerbungen
├── schritt_fuer_schritt.py  # Suchen: Formular und Treffer auf einer Seite
├── jobs.py                  # Jobs: alle gefundenen Stellen mit Passung und Stand
├── lebenslauf.py            # Lebenslauf: Datei, was daraus gelesen wird, Zeugnisse
└── profil.py                # Einstellungen: Über mich · Suche · Automatik · Konten
bewerbungsagent/ui.py              # gemeinsame Bausteine (Karten, Dialoge, Laufstatus)
bewerbungsagent/lebenslauf.py      # Lebenslauf auslesen (KI/Heuristik) und ins Profil übernehmen
bewerbungsagent/ui_assistent.py    # Erststart-Assistent
bewerbungsagent/ui_einstellungen.py  # Konten, Lebenslauf-Upload, Jobbörsen
```

Die Seiten liegen bewusst nicht in `pages/`, damit Streamlit nur die Navigation aus
`app.py` verwendet.

## 🐛 Troubleshooting

- **„Dein E-Mail-Konto ist noch nicht verbunden“** → Einstellungen → Konten → Adresse und
  Passwort eingeben. Bereits freigegebene Bewerbungen gehen dann automatisch raus.
- **„The model … does not exist“ / model not found** → nichts zu tun: Der Agent nimmt selbst ein
  verfügbares Modell (bei Ollama zuerst `OLLAMA_MODEL`, dann `gpt-oss:120b`, sonst das größte; nie
  ein Bezahlmodell eines anderen Anbieters wie `gpt-4o-mini`) und schreibt es fest ins Profil.
  Ändern unter Einstellungen → Konten (`bewerbungsagent modelle` listet die Modelle deines Zugangs).
- **Online-Formulare scheitern** → Einstellungen → Konten → „Browser testen“ (oder
  `bewerbungsagent browser-test`). Startet der Browser nicht, bleiben Formular-Bewerbungen
  freigegeben und gehen beim nächsten Lauf raus – mit sicherer Adresse sofort per E-Mail.
- **Formular braucht Login/Captcha** → Die Stelle kommt als Karte „Selbst bewerben“; „Zur Bewerbung“
  öffnen, Anschreiben aus „Lesen & bearbeiten“ kopieren, danach „Ja, erledigt“.
- **Es passiert nichts nach „Ja“** → `daten/worker.log` bzw. `daten/cron.log` ansehen.
- **Keine Vorschläge auf Heute** → Lebenslauf prüfen (daraus kommen die Jobtitel), unter „Suchen“
  ähnliche Jobtitel antippen oder unter „Mehr Filter“ die Mindest-Passung senken.
