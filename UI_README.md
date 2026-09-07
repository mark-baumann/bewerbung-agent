# 🌐 Bewerbungsagent Web UI

Eine moderne Streamlit-basierte Web-Oberfläche für den Bewerbungsagenten.

## ✨ Features

- **📊 Dashboard** - Übersicht über Jobs, Bewertungen und Bewerbungen
- **🔍 Jobsuche** - Interaktive Suche mit Filtern
- **📊 Bewertung** - Jobs bewerten mit Heuristik und/oder LLM
- **✉️ Bewerbungen** - Anschreiben generieren und Bewerbungen verwalten
- **👤 Profil** - Persönliche Daten, Sucheinstellungen und Unterlagen verwalten

## 🚀 Installation

```bash
# UI-Abhängigkeiten installieren
pip install -e ".[ui]"

# Optional: Auch Browser-Automation
pip install -e ".[ui,browser]"
playwright install chromium
```

## 🖥️ Nutzung

### Starten der Web-UI

```bash
streamlit run app.py
```

Die Anwendung öffnet sich automatisch im Browser unter `http://localhost:8501`.

### Workflow

1. **Dashboard** aufrufen für Übersicht
2. **Jobsuche** - Neue Jobs suchen
3. **Bewertung** - Jobs bewerten lassen
4. **Bewerbungen** - Anschreiben generieren und bewerben

## 📋 Voraussetzungen

### Konfiguration

Die Web-UI nutzt dieselbe Konfiguration wie das CLI:

1. **Profil erstellen:** Öffne in der Web-UI die Seite **👤 Profil** und klicke
   auf **✨ Profil über die UI anlegen**. Anschließend kannst du alle Daten
   direkt in den Formularen ausfüllen und speichern.

2. **API-Key setzen:**
   ```bash
   cp .env.example .env
   # Trage ANTHROPIC_API_KEY in .env ein
   ```

3. **Unterlagen:**
   - Lege Lebenslauf, Zeugnisse etc. im Ordner `unterlagen/` ab
   - Lade die Unterlagen auf der Seite **👤 Profil** hoch

### Datenbank

Die Web-UI nutzt dieselbe SQLite-Datenbank wie das CLI:
- Standardpfad: `~/.bewerbungsagent/jobs.db`
- Wird automatisch beim ersten Gebrauch erstellt
- Kompatibel mit CLI-Befehlen

## 🎨 Seitenübersicht

### 1. Dashboard
- Metriken: Gefundene Jobs, Bewertungen, Bewerbungen
- Neueste Jobs
- Schnellaktionen

### 2. Jobsuche
- Suchformular mit Filtern:
  - Was (Suchbegriffe)
  - Wo (Ort)
  - Umkreis
  - Veröffentlichungsdatum
  - Nur Vollzeit
- Live-Suche über Arbeitsagentur-API
- Vorschau der Ergebnisse
- Automatisches Speichern in DB

### 3. Bewertung
- Optionen:
  - Nur unbewertete Jobs
  - Mit/ohne LLM (Claude)
  - Anzahl Jobs
- Fortschrittsanzeige
- Ergebnisvorschau mit Top-Jobs
- Statistiken

### 4. Bewerbungen
Drei Tabs:

**Anschreiben generieren:**
- Job auswählen
- Mit/ohne LLM generieren
- Anzeigen und speichern

**Bewerbung abschicken:**
- Manuell: URL öffnen und als beworben markieren
- Browser-Automation: (CLI empfohlen)

**Bewerbungsübersicht:**
- Alle Bewerbungen
- Statistiken (Gesamt, Abgeschickt, Probelauf, Fehlgeschlagen)
- Bewerbungsverlauf

### 5. Profil
- Persönliche Daten bearbeiten
- Suchparameter anpassen
- Unterlagen (Lebenslauf, Zeugnisse) direkt hochladen
- API-Keys und Datenbank-Informationen

## 🔄 CLI vs. Web-UI

### Kompatibilität
- Beide nutzen dieselbe Datenbank
- Dieselbe Konfiguration (profil.yaml)
- Können parallel verwendet werden

### Wann CLI nutzen?
- Schnelle Operationen
- Scripting/Automation
- Browser-Automation für Bewerbungen

### Wann Web-UI nutzen?
- Interaktive Exploration
- Übersichtliche Darstellung
- Komfortable Job-Bewertung
- Anschreiben-Vorschau

## 📊 Deployment

### Lokal
```bash
streamlit run app.py
```

### Docker
Die UI kann zusammen mit dem Bewerbungsagenten deployed werden:

```dockerfile
# Im Dockerfile ergänzen:
RUN pip install -e ".[ui]"
CMD ["streamlit", "run", "app.py", "--server.port=8501"]
```

### Empfohlener Port
- Standard: `8501`
- Im infrastruktur-deployment: Port kann angepasst werden

## 🛠️ Entwicklung

### Struktur
```
bewerbung-agent/
├── app.py                    # Hauptseite (aktuell Platzhalter)
├── pages/
│   ├── 1_dashboard.py       # Dashboard
│   ├── 2_jobsuche.py        # Jobsuche
│   ├── 3_bewertung.py       # Bewertung
│   ├── 5_bewerbungen.py     # Bewerbungen
│   └── 6_profil.py          # Profil
└── bewerbungsagent/         # Python-Package (unverändert)
```

### Streamlit Pages
Streamlit nutzt automatisch Dateien in `pages/` als Navigation.
Die Nummerierung bestimmt die Reihenfolge.

### Eigene Anpassungen
- Farben und Styling in den einzelnen Pages
- Zusätzliche Metriken im Dashboard
- Weitere Filteroptionen
- Custom Bewertungslogik

## 🐛 Troubleshooting

### "Datenbank nicht gefunden"
→ Führe zuerst eine Jobsuche durch (CLI oder UI)

### "ANTHROPIC_API_KEY fehlt"
→ Setze den Key in `.env` oder als Umgebungsvariable

### "Profil konnte nicht geladen werden"
→ Öffne die Seite **👤 Profil** und lege das Profil über die UI an

### Browser-Automation funktioniert nicht
→ Nutze das CLI für Browser-Automation: `bewerbungsagent bewerben <ref>`

## 📝 To-Do / Erweiterungen

- [ ] Live-Update der Bewerbungsstatus
- [ ] Export-Funktionen (CSV, PDF)
- [ ] Statistik-Dashboards
- [ ] Email-Benachrichtigungen
- [ ] Multi-User Support
- [ ] Browser-Automation direkt in UI integrieren

## 👤 Autor

**Mark Baumann** — [GitHub](https://github.com/mark-baumann) · [markb.de](https://markb.de)

---

**Hinweis:** Die Web-UI ergänzt das CLI, ersetzt es aber nicht vollständig.
Für volle Funktionalität (besonders Browser-Automation) wird das CLI weiterhin empfohlen.
