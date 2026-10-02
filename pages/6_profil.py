"""Profil - Persönliche Daten, Sucheinstellungen und Unterlagen verwalten."""

import os
import smtplib
import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from bewerbungsagent.config import (
    SMTPSettings,
    ensure_seeded_profil,
    lade_profil,
    profil_ist_beispiel,
    speichere_profil,
    ermittle_profil_pfad,
    erstelle_profil,
)
from bewerbungsagent.mailer import (
    passwort_gesetzt,
    sende_testmail,
    speichere_passwort,
    teste_verbindung,
)

st.title("👤 Profil")
st.caption(f"Dein Profil wird dauerhaft in `{ermittle_profil_pfad()}` gespeichert.")

st.markdown("""
Ergänze deine persönlichen Angaben, Sucheinstellungen und Unterlagen.
Alle geladenen Stellen findest du auf der Startseite.
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


# Tabs
tab1, tab2, tab3, tab4 = st.tabs(
    ["👤 Person", "🔍 Suche", "📄 Unterlagen", "✉️ E-Mail"]
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

# Tab 4: SMTP-Konfiguration
with tab4:
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
        if not hasattr(profil, "smtp"):
            profil.smtp = SMTPSettings()
        smtp = profil.smtp
        with st.form("smtp_settings"):
            col1, col2 = st.columns(2)
            with col1:
                smtp_host = st.text_input(
                    "SMTP-Server",
                    value=smtp.host,
                    placeholder="smtp.gmail.com oder send.one.com",
                    help="Verwende den ausgehenden SMTP-Server deines Mailanbieters, nicht den IMAP-Server.",
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

        st.markdown("### 📬 Test-E-Mail")
        st.caption(
            "Sendet nur nach deiner Bestätigung eine einfache Testnachricht direkt an dein eigenes Postfach. "
            "So prüfst du die Zustellung, nicht nur die SMTP-Anmeldung."
        )
        with st.form("smtp_test_email"):
            test_recipient = st.text_input(
                "Test-E-Mail-Adresse",
                value=smtp.sender_email or profil.person.email,
            )
            confirm_test = st.checkbox(
                "Ich möchte jetzt eine Test-E-Mail an diese Adresse senden.",
            )
            send_test = st.form_submit_button(
                "📨 Test-E-Mail senden",
                disabled=not (smtp.host and smtp.sender_email),
            )
        if send_test:
            if not confirm_test:
                st.error("Bitte bestätige den Testversand.")
            else:
                try:
                    message_id = sende_testmail(profil, test_recipient)
                except (OSError, RuntimeError, ValueError, smtplib.SMTPException) as exc:
                    st.error(f"Test-E-Mail konnte nicht angenommen werden: {exc}")
                else:
                    st.success(
                        "Der SMTP-Server hat die Test-E-Mail angenommen. "
                        f"Message-ID: {message_id}"
                    )
                    st.info(
                        "Prüfe Posteingang, Spam und gegebenenfalls die Quarantäne deines Mailanbieters."
                    )

# Footer
st.markdown("---")
st.markdown("""
**Bewerbungsagent** | [GitHub](https://github.com/mark-baumann/bewerbung-agent) | [Dokumentation](../README.md)
""")
