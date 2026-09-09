"""Profil - Persönliche Daten, Sucheinstellungen und Unterlagen verwalten."""

import os
import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import (
    lade_profil,
    speichere_profil,
    ermittle_profil_pfad,
    erstelle_profil,
)

st.set_page_config(page_title="Profil", page_icon="👤", layout="wide")

st.title("👤 Profil")

st.markdown("""
Verwalte dein Bewerbungsprofil, Sucheinstellungen und lade deine Unterlagen
(Lebenslauf, Zeugnisse, etc.) direkt hier hoch.
""")

# Profil-Pfad
profil_pfad = ermittle_profil_pfad()

unterlagen_dir = Path("unterlagen")
unterlagen_dir.mkdir(exist_ok=True)


def _lade():
    return lade_profil(profil_pfad)


# Tabs
tab1, tab2, tab3 = st.tabs(["👤 Persönliche Daten", "🔍 Sucheinstellungen", "📄 Unterlagen"])

# Tab 1: Persönliche Daten
with tab1:
    st.subheader("👤 Persönliche Daten")

    try:
        profil = _lade()
    except Exception as e:
        st.info("Noch kein Profil angelegt. Du kannst es jetzt direkt hier einrichten.")
        if st.button("✨ Profil über die UI anlegen", type="primary"):
            erstelle_profil(profil_pfad)
            st.rerun()
        profil = None

    if profil is not None:
        with st.form("profil_person"):
            col1, col2 = st.columns(2)

            with col1:
                vorname = st.text_input("Vorname", value=profil.person.vorname)
                nachname = st.text_input("Nachname", value=profil.person.nachname)
                email = st.text_input("Email", value=profil.person.email)
                telefon = st.text_input("Telefon", value=profil.person.telefon)
                geburtsdatum = st.text_input("Geburtsdatum", value=profil.person.geburtsdatum)

            with col2:
                strasse = st.text_input("Straße", value=profil.person.strasse)
                plz = st.text_input("PLZ", value=profil.person.plz)
                ort = st.text_input("Ort", value=profil.person.ort)
                land = st.text_input("Land", value=profil.person.land)
                linkedin = st.text_input("LinkedIn", value=profil.person.linkedin)
                github = st.text_input("GitHub", value=profil.person.github)

            kurzprofil = st.text_area(
                "Kurzprofil (geht in Bewertung und Anschreiben ein)",
                value=profil.kurzprofil,
                height=120,
            )

            gespeichert = st.form_submit_button("💾 Speichern", type="primary")

        if gespeichert:
            profil.person.vorname = vorname
            profil.person.nachname = nachname
            profil.person.email = email
            profil.person.telefon = telefon
            profil.person.geburtsdatum = geburtsdatum
            profil.person.strasse = strasse
            profil.person.plz = plz
            profil.person.ort = ort
            profil.person.land = land
            profil.person.linkedin = linkedin
            profil.person.github = github
            profil.kurzprofil = kurzprofil
            speichere_profil(profil, profil_pfad)
            st.success("✅ Profil gespeichert!")

# Tab 2: Sucheinstellungen
with tab2:
    st.subheader("🔍 Sucheinstellungen")

    try:
        profil = _lade()
    except Exception as e:
        st.error(f"Fehler: {e}")
        profil = None

    if profil is not None:
        with st.form("profil_suche"):
            col1, col2 = st.columns(2)

            with col1:
                was_text = st.text_area(
                    "Suchbegriffe (ein Begriff pro Zeile)",
                    value="\n".join(profil.suche.was),
                    height=120,
                )
                wo = st.text_input("Ort", value=profil.suche.wo or "")
                umkreis = st.slider("Umkreis (km)", 0, 200, profil.suche.umkreis)

            with col2:
                veroeffentlicht_seit_tagen = st.number_input(
                    "Veröffentlicht seit (Tagen)",
                    value=profil.suche.veroeffentlicht_seit_tagen,
                )
                max_pro_query = st.number_input(
                    "Max. Treffer pro Suchbegriff",
                    value=profil.suche.max_pro_query,
                )
                nur_vollzeit = st.checkbox("Nur Vollzeit", value=profil.suche.nur_vollzeit)
                nur_homeoffice = st.checkbox("Nur Homeoffice", value=profil.suche.nur_homeoffice)

            st.markdown("### 📊 Bewertungsparameter")

            col3, col4 = st.columns(2)

            with col3:
                skills_text = st.text_area(
                    "Pflicht-Skills (ein Skill pro Zeile)",
                    value="\n".join(profil.bewertung.skills),
                    height=120,
                )
                wunsch_text = st.text_area(
                    "Wunschthemen (ein Thema pro Zeile)",
                    value="\n".join(profil.bewertung.wunsch),
                    height=120,
                )

            with col4:
                ausschluss_text = st.text_area(
                    "Ausschlusskriterien (ein Kriterium pro Zeile)",
                    value="\n".join(profil.bewertung.ausschluss),
                    height=120,
                )
                min_score = st.slider(
                    "Minimaler Score für Top-Liste",
                    0,
                    100,
                    int(profil.bewertung.min_score),
                )

            gespeichert = st.form_submit_button("💾 Speichern", type="primary")

        if gespeichert:
            profil.suche.was = [w.strip() for w in was_text.splitlines() if w.strip()]
            profil.suche.wo = wo
            profil.suche.umkreis = int(umkreis)
            profil.suche.veroeffentlicht_seit_tagen = int(veroeffentlicht_seit_tagen)
            profil.suche.max_pro_query = int(max_pro_query)
            profil.suche.nur_vollzeit = nur_vollzeit
            profil.suche.nur_homeoffice = nur_homeoffice
            profil.bewertung.skills = [s.strip() for s in skills_text.splitlines() if s.strip()]
            profil.bewertung.wunsch = [w.strip() for w in wunsch_text.splitlines() if w.strip()]
            profil.bewertung.ausschluss = [a.strip() for a in ausschluss_text.splitlines() if a.strip()]
            profil.bewertung.min_score = float(min_score)
            speichere_profil(profil, profil_pfad)
            st.success("✅ Sucheinstellungen gespeichert!")

# Tab 3: Unterlagen
with tab3:
    st.subheader("📄 Unterlagen")

    st.markdown("""
    Lade deinen Lebenslauf, Zeugnisse und weitere Unterlagen direkt hier hoch.
    Die Dateien werden im Ordner `unterlagen/` gespeichert und automatisch in
    dein Profil eingetragen.
    """)

    try:
        profil = _lade()
    except Exception as e:
        st.error(f"Fehler: {e}")
        profil = None

    # Lebenslauf hochladen
    st.markdown("### 📄 Lebenslauf")
    lebenslauf_upload = st.file_uploader(
        "Lebenslauf hochladen (PDF, DOCX, TXT)",
        type=["pdf", "docx", "txt", "md"],
        key="lebenslauf_upload",
    )
    if lebenslauf_upload is not None:
        ziel = unterlagen_dir / lebenslauf_upload.name
        ziel.write_bytes(lebenslauf_upload.getbuffer())
        if profil is not None:
            profil.unterlagen.lebenslauf = str(ziel)
            speichere_profil(profil, profil_pfad)
        st.success(f"✅ Lebenslauf gespeichert: `{ziel}`")

    # Zeugnisse hochladen
    st.markdown("### 🎓 Zeugnisse & weitere Unterlagen")
    zeugnisse_upload = st.file_uploader(
        "Zeugnisse hochladen (mehrere Dateien möglich)",
        type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="zeugnisse_upload",
    )
    if zeugnisse_upload:
        neue_zeugnisse = []
        for datei in zeugnisse_upload:
            ziel = unterlagen_dir / datei.name
            ziel.write_bytes(datei.getbuffer())
            neue_zeugnisse.append(str(ziel))
        if profil is not None:
            profil.unterlagen.zeugnisse = neue_zeugnisse
            speichere_profil(profil, profil_pfad)
        st.success(f"✅ {len(neue_zeugnisse)} Datei(en) gespeichert!")

    st.markdown("---")

    # Aktuelle Unterlagen anzeigen
    st.markdown("### 📁 Aktuelle Unterlagen")

    if profil is not None:
        lebenslauf = profil.unterlagen.lebenslauf
        if lebenslauf:
            p = Path(lebenslauf)
            if p.exists():
                st.success(f"✅ **Lebenslauf:** `{lebenslauf}` ({p.stat().st_size / 1024:.1f} KB)")
            else:
                st.error(f"❌ **Lebenslauf:** `{lebenslauf}` (nicht gefunden)")
        else:
            st.info("ℹ️ Noch kein Lebenslauf hinterlegt.")

        zeugnisse = profil.unterlagen.zeugnisse
        if zeugnisse:
            for z in zeugnisse:
                p = Path(z)
                if p.exists():
                    st.success(f"✅ **Zeugnis:** `{z}` ({p.stat().st_size / 1024:.1f} KB)")
                else:
                    st.error(f"❌ **Zeugnis:** `{z}` (nicht gefunden)")
        else:
            st.info("ℹ️ Noch keine Zeugnisse hinterlegt.")
    else:
        st.warning("Profil nicht geladen - Unterlagen können nicht angezeigt werden.")

    # Anschreiben-Stil
    if profil is not None:
        st.markdown("### ✍️ Anschreiben-Stil")
        with st.form("anschreiben_stil"):
            stil = st.text_area(
                "Stil-Vorgabe für generierte Anschreiben",
                value=profil.unterlagen.anschreiben_stil,
                height=100,
            )
            gespeichert = st.form_submit_button("💾 Speichern", type="primary")
        if gespeichert:
            profil.unterlagen.anschreiben_stil = stil
            speichere_profil(profil, profil_pfad)
            st.success("✅ Anschreiben-Stil gespeichert!")

    st.markdown("---")

    # API-Konfiguration
    st.markdown("### 🔑 API-Konfiguration")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        st.success("✅ ANTHROPIC_API_KEY ist gesetzt")
        st.code(f"ANTHROPIC_API_KEY={anthropic_key[:10]}...{anthropic_key[-4:]}", language=None)
    else:
        st.error("❌ ANTHROPIC_API_KEY nicht gesetzt")
        st.markdown("""
        **API-Key setzen:**
        ```bash
        echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
        ```
        Ohne API-Key funktioniert nur die heuristische Bewertung, nicht die LLM-basierte.
        """)

# Footer
st.markdown("---")
st.markdown("""
**Bewerbungsagent** | [GitHub](https://github.com/mark-baumann/bewerbung-agent) | [Dokumentation](../README.md)
""")
