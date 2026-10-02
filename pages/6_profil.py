"""Profil - Persönliche Daten, Sucheinstellungen und Unterlagen verwalten."""

import os
import smtplib
import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import (
    STANDARD_SUCHORT,
    SMTPSettings,
    ensure_seeded_profil,
    lade_profil,
    profil_ist_beispiel,
    speichere_profil,
    ermittle_profil_pfad,
    erstelle_profil,
)
from bewerbungsagent.db import Speicher
from bewerbungsagent.mailer import passwort_gesetzt, speichere_passwort, teste_verbindung

st.title("👤 Profil")
st.caption(f"Dein Profil wird dauerhaft in `{ermittle_profil_pfad()}` gespeichert.")

st.markdown("""
Ergänze deine echten Angaben und Unterlagen. Daraus entstehen passende Suchläufe;
neue Treffer erscheinen anschließend hier im Postfach.
""")

try:
    current_profile = ensure_seeded_profil()
    if profil_ist_beispiel(current_profile):
        st.warning(
            "Dieses Profil enthält noch automatisch erzeugte Beispieldaten. Ersetze sie im Tab "
            "„Persönliche Daten“ und lade deinen echten Lebenslauf hoch; bis dahin sind automatische Suchen pausiert.",
            icon="⚠️",
        )
except (OSError, ValueError) as exc:
    st.error(f"Profil konnte nicht geladen werden: {exc}")

# Profil-Pfad
profil_pfad = ermittle_profil_pfad()

unterlagen_dir = Path("unterlagen")
unterlagen_dir.mkdir(exist_ok=True)


def _lade():
    return ensure_seeded_profil(profil_pfad)


def get_db_path() -> Path:
    db_path = Path.home() / ".bewerbungsagent" / "jobs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def render_postfach(profil):
    ort = profil.suche.wo.strip() or profil.person.ort.strip() or STANDARD_SUCHORT
    searches = []
    if ort:
        for term in profil.suche.was[:3]:
            searches.append(
                {
                    "name": f"{term} @ {ort}",
                    "query": term,
                    "location": ort,
                    "radius_km": profil.suche.umkreis,
                    "published_days": profil.suche.veroeffentlicht_seit_tagen,
                    "only_full_time": int(profil.suche.nur_vollzeit),
                    "interval_minutes": 360,
                }
            )

    with Speicher(get_db_path()) as db:
        db.ensure_automated_searches(searches)
        st.markdown("### 🕒 Automatische Suchläufe")
        if not ort or not profil.suche.was:
            st.info("Trage zuerst mindestens einen Suchbegriff und deinen Wohn-/Suchort ein. Bis dahin laufen keine automatischen Suchen.")
        auto_rows = db.automated_searches()
        active_rows = [row for row in auto_rows if row["enabled"]]
        if active_rows:
            st.caption("Die Suche wird alle 6 Stunden geprüft, solange die App läuft.")
            for row in active_rows:
                st.write(
                    f"- **{row['name']}** · Ort: {row['location'] or 'Deutschland'} · alle {row['interval_minutes']} min · "
                    f"letzter Lauf: {row['last_run_at'] or 'nie'}"
                )
        else:
            st.info("Noch keine automatischen Suchläufe angelegt.")

        st.markdown("### 📥 Postfach")
        inbox = db.inbox(unread_only=False)
        if inbox:
            for item in inbox[:10]:
                job = db.job(item["ref"])
                source = (
                    f"{job.quelle_icon} {job.quelle_label}"
                    if job is not None
                    else "🔎 Quelle unbekannt"
                )
                st.markdown(
                    f"{source}  \n**{item['title']}** · {item['employer']}  \n"
                    f"📍 {item['location'] or 'nicht angegeben'} · "
                    f"Passung: {item['score'] if item['score'] is not None else '–'}  \n"
                    f"Abfrage: `{item['query']}` · Status: `{item['status']}`"
                )
                if item.get("detail_url"):
                    st.markdown(f"[Job öffnen]({item['detail_url']})")
                if st.button(f"Als gelesen markieren #{item['id']}", key=f"read_{item['id']}"):
                    db.mark_inbox_read(item["id"])
                    st.rerun()
        else:
            st.info("Noch keine passenden Jobs im Postfach. Die nächste automatische Suche füllt es auf.")


# Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["👤 Person", "🔍 Suche", "📄 Unterlagen", "📬 Postfach", "✉️ E-Mail"]
)

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
            st.success(f"✅ Profil dauerhaft gespeichert: `{profil_pfad}`")

# Tab 2: Sucheinstellungen
with tab2:
    st.subheader("🔍 Sucheinstellungen")
    st.caption("Lege fest, welche Stellen gesucht werden. Standard-Suchort ist München; du kannst ihn jederzeit ändern.")

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
                wo = st.text_input(
                    "Suchort",
                    value=profil.suche.wo or profil.person.ort or STANDARD_SUCHORT,
                    help="Standard: München. Der Suchort muss nicht deinem Wohnort entsprechen.",
                )
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
            profil.suche.umkreis = umkreis
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

# Tab 4: Postfach und automatische Suchen
with tab4:
    st.subheader("📬 Postfach & automatische Suche")
    try:
        profil = _lade()
    except Exception:
        profil = None

    if profil is None:
        st.warning("Noch kein Profil vorhanden. Bitte erst anlegen.")
    else:
        render_postfach(profil)

# Tab 5: SMTP-Konfiguration
with tab5:
    st.subheader("✉️ Bewerbungen per E-Mail versenden")
    st.caption(
        "Serverdaten werden mit deinem Profil gespeichert. Das Passwort bleibt getrennt davon "
        "im Windows-Schlüsselbund und wird nicht in YAML oder SQLite geschrieben."
    )
    try:
        profil = _lade()
    except (OSError, ValueError) as exc:
        st.error(f"Profil konnte nicht geladen werden: {exc}")
        profil = None

    if profil is not None:
        smtp = profil.smtp
        with st.form("smtp_settings"):
            col1, col2 = st.columns(2)
            with col1:
                smtp_host = st.text_input(
                    "SMTP-Server",
                    value=smtp.host,
                    placeholder="smtp.gmail.com",
                )
                smtp_port = st.number_input(
                    "Port",
                    min_value=1,
                    max_value=65535,
                    value=int(smtp.port),
                )
                smtp_username = st.text_input("SMTP-Benutzername", value=smtp.username)
            with col2:
                smtp_sender = st.text_input(
                    "Absender-E-Mail",
                    value=smtp.sender_email or profil.person.email,
                    placeholder="dein.name@example.com",
                )
                security = st.selectbox(
                    "Verschlüsselung",
                    ["STARTTLS (empfohlen)", "SSL/TLS", "Keine (nicht empfohlen)"],
                    index=1 if smtp.use_ssl else (0 if smtp.starttls else 2),
                )
                smtp_password = st.text_input(
                    "SMTP-Passwort oder App-Passwort",
                    type="password",
                    help="Leer lassen, wenn das gespeicherte Passwort unverändert bleiben soll.",
                )
            save_smtp = st.form_submit_button("💾 E-Mail-Einstellungen speichern", type="primary")

        if save_smtp:
            profil.smtp = SMTPSettings(
                host=smtp_host.strip(),
                port=int(smtp_port),
                username=smtp_username.strip(),
                sender_email=smtp_sender.strip(),
                starttls=security == "STARTTLS (empfohlen)",
                use_ssl=security == "SSL/TLS",
            )
            try:
                speichere_profil(profil, profil_pfad)
                if smtp_password:
                    speichere_passwort(profil, smtp_password)
                smtp = profil.smtp
                st.success(f"E-Mail-Einstellungen gespeichert. Profil: `{profil_pfad}`")
            except (OSError, RuntimeError, ValueError) as exc:
                st.error(f"Einstellungen konnten nicht vollständig gespeichert werden: {exc}")

        try:
            has_password = not smtp.username.strip() or passwort_gesetzt(profil)
        except RuntimeError as exc:
            has_password = False
            st.error(f"Windows-Schlüsselbund ist nicht verfügbar: {exc}")

        if smtp.host and smtp.sender_email:
            if not smtp.username.strip():
                auth_status = "ohne SMTP-Anmeldung"
            else:
                auth_status = "Passwort sicher gespeichert" if has_password else "Passwort fehlt noch"
            st.success(f"SMTP-Daten hinterlegt · {auth_status}")
        else:
            st.info("Hinterlege deine SMTP-Serverdaten und speichere sie zuerst.")

        if st.button(
            "🔌 Verbindung testen",
            disabled=not (smtp.host and smtp.sender_email),
            use_container_width=True,
        ):
            try:
                teste_verbindung(profil)
                st.success("SMTP-Verbindung und Anmeldung erfolgreich. Es wurde keine E-Mail versendet.")
            except (OSError, RuntimeError, ValueError, smtplib.SMTPException) as exc:
                st.error(f"SMTP-Verbindung fehlgeschlagen: {exc}")

# Footer
st.markdown("---")
st.markdown("""
**Bewerbungsagent** | [GitHub](https://github.com/mark-baumann/bewerbung-agent) | [Dokumentation](../README.md)
""")
