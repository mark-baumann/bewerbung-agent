"""Einstellungen - Profil und Konfiguration verwalten."""

import streamlit as st
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import lade_profil

st.set_page_config(page_title="Einstellungen", page_icon="⚙️", layout="wide")

st.title("⚙️ Einstellungen")

st.markdown("""
Verwalte dein Bewerbungsprofil, Sucheinstellungen und API-Keys.
""")

# Profil-Pfad
profil_pfad = Path("config/profil.yaml")
if not profil_pfad.exists():
    profil_pfad = Path.home() / ".config" / "bewerbungsagent" / "profil.yaml"

# Tabs
tab1, tab2, tab3 = st.tabs(["👤 Profil", "🔍 Sucheinstellungen", "🔑 API & Dateien"])

# Tab 1: Profil
with tab1:
    st.subheader("👤 Persönliches Profil")

    try:
        profil = lade_profil()

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Persönliche Daten")
            st.text_input("Name", value=getattr(profil.person, 'name', ''), disabled=True)
            st.text_input("Email", value=getattr(profil.person, 'email', ''), disabled=True)
            st.text_input("Telefon", value=getattr(profil.person, 'telefon', ''), disabled=True)
            st.text_input("Adresse", value=getattr(profil.person, 'adresse', ''), disabled=True)

        with col2:
            st.markdown("### Qualifikationen")
            if hasattr(profil.person, 'abschluss'):
                st.text_input("Abschluss", value=profil.person.abschluss, disabled=True)
            if hasattr(profil.person, 'berufserfahrung_jahre'):
                st.number_input("Berufserfahrung (Jahre)", value=profil.person.berufserfahrung_jahre, disabled=True)

        # Skills
        st.markdown("### 💡 Skills")
        if hasattr(profil.person, 'skills'):
            skills_text = ", ".join(profil.person.skills)
            st.text_area("Deine Skills", value=skills_text, height=100, disabled=True)

        # Ausschlusskriterien
        st.markdown("### 🚫 Ausschlusskriterien")
        if hasattr(profil.bewertung, 'ausschluss'):
            ausschluss_text = "\n".join([f"- {a}" for a in profil.bewertung.ausschluss])
            st.text_area("Diese Begriffe führen zum Ausschluss", value=ausschluss_text, height=100, disabled=True)

        st.info("""
        💡 **Profil bearbeiten:**
        Bearbeite die Datei `config/profil.yaml` oder `~/.config/bewerbungsagent/profil.yaml` direkt.

        Siehe auch: `config/profil.example.yaml` für ein Beispiel.
        """)

    except Exception as e:
        st.error(f"Fehler beim Laden des Profils: {e}")
        st.markdown("""
        ⚠️ **Profil nicht gefunden**

        Erstelle eine Profil-Datei:
        ```bash
        cp config/profil.example.yaml config/profil.yaml
        # oder
        mkdir -p ~/.config/bewerbungsagent
        cp config/profil.example.yaml ~/.config/bewerbungsagent/profil.yaml
        ```

        Bearbeite dann die Datei mit deinen Daten.
        """)

# Tab 2: Sucheinstellungen
with tab2:
    st.subheader("🔍 Sucheinstellungen")

    try:
        profil = lade_profil()

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Standard-Suchparameter")

            was = st.text_area(
                "Suchbegriffe (ein Begriff pro Zeile)",
                value="\n".join(profil.suche.was),
                height=100,
                disabled=True
            )

            wo = st.text_input("Ort", value=profil.suche.wo or "", disabled=True)
            umkreis = st.slider("Umkreis (km)", 0, 200, profil.suche.umkreis, disabled=True)

        with col2:
            st.markdown("### Filter")

            veroeffentlicht_seit_tagen = st.number_input(
                "Veröffentlicht seit (Tagen)",
                value=profil.suche.veroeffentlicht_seit_tagen,
                disabled=True
            )

            max_pro_query = st.number_input(
                "Max. Treffer pro Suchbegriff",
                value=profil.suche.max_pro_query,
                disabled=True
            )

            nur_vollzeit = st.checkbox(
                "Nur Vollzeit",
                value=profil.suche.nur_vollzeit,
                disabled=True
            )

        st.markdown("### 📊 Bewertungsparameter")

        col3, col4 = st.columns(2)

        with col3:
            min_score = st.slider(
                "Minimaler Score für Top-Liste",
                0,
                100,
                profil.bewertung.min_score,
                disabled=True
            )

        with col4:
            passung_gewicht = st.slider(
                "Gewichtung Passung",
                0.0,
                1.0,
                0.7,
                disabled=True,
                help="Gesamt-Score = Passung × 0.7 + Ton × 0.3"
            )

        st.info("""
        💡 **Einstellungen bearbeiten:**
        Diese Einstellungen werden aus `config/profil.yaml` geladen.
        Bearbeite die Datei direkt, um Änderungen vorzunehmen.
        """)

    except Exception as e:
        st.error(f"Fehler: {e}")

# Tab 3: API & Dateien
with tab3:
    st.subheader("🔑 API-Keys und Unterlagen")

    # API Keys
    st.markdown("### 🔑 API-Konfiguration")

    import os

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        st.success("✅ ANTHROPIC_API_KEY ist gesetzt")
        st.code(f"ANTHROPIC_API_KEY={anthropic_key[:10]}...{anthropic_key[-4:]}", language=None)
    else:
        st.error("❌ ANTHROPIC_API_KEY nicht gesetzt")
        st.markdown("""
        **API-Key setzen:**
        ```bash
        # In .env Datei
        echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

        # Oder als Umgebungsvariable
        export ANTHROPIC_API_KEY=sk-ant-...
        ```

        Ohne API-Key funktioniert nur die heuristische Bewertung, nicht die LLM-basierte.
        """)

    st.markdown("---")

    # Unterlagen
    st.markdown("### 📄 Bewerbungsunterlagen")

    try:
        profil = lade_profil()

        if hasattr(profil, 'unterlagen'):
            st.write("**Konfigurierte Unterlagen:**")

            unterlagen_dir = Path("unterlagen")

            for name, pfad in profil.unterlagen.items():
                vollstaendiger_pfad = unterlagen_dir / pfad
                if vollstaendiger_pfad.exists():
                    st.success(f"✅ **{name}:** `{pfad}` ({vollstaendiger_pfad.stat().st_size / 1024:.1f} KB)")
                else:
                    st.error(f"❌ **{name}:** `{pfad}` (nicht gefunden)")

            st.info("""
            💡 **Unterlagen verwalten:**
            - Lege deine Dateien im Ordner `unterlagen/` ab
            - Konfiguriere die Pfade in `config/profil.yaml` unter `unterlagen:`

            Beispiel:
            ```yaml
            unterlagen:
              lebenslauf: "lebenslauf.pdf"
              zeugnisse: "zeugnisse.pdf"
              anschreiben_vorlage: "anschreiben_vorlage.txt"
            ```
            """)
        else:
            st.warning("Keine Unterlagen konfiguriert")

    except Exception as e:
        st.error(f"Fehler: {e}")

    st.markdown("---")

    # Datenbank
    st.markdown("### 💾 Datenbank")

    db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
    if db_path.exists():
        st.success(f"✅ Datenbank: `{db_path}` ({db_path.stat().st_size / 1024:.1f} KB)")

        if st.button("🗑️ Datenbank zurücksetzen", type="secondary"):
            st.warning("⚠️ Dies würde alle Jobs, Bewertungen und Bewerbungen löschen!")
            st.info("Zum Zurücksetzen: `rm ~/.bewerbungsagent/jobs.db`")
    else:
        st.info(f"ℹ️ Datenbank wird beim ersten Gebrauch erstellt: `{db_path}`")

# Footer
st.markdown("---")
st.markdown("""
**Bewerbungsagent** | [GitHub](https://github.com/mark-baumann/bewerbung-agent) | [Dokumentation](../README.md)
""")
