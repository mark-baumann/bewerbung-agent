"""Profil & Einstellungen: Daten, Suche, Unterlagen, Automatik und Versand."""

import os
from pathlib import Path

import streamlit as st

from bewerbungsagent import ablauf, dokumente, zeitplan
from bewerbungsagent.bewerben import mail
from bewerbungsagent.config import MODI, erstelle_profil, ermittle_profil_pfad, lade_profil, speichere_profil
from bewerbungsagent.scoring import llm as llm_modul

st.title("👤 Profil & Einstellungen")

profil_pfad = ermittle_profil_pfad()
unterlagen_dir = Path("unterlagen")
unterlagen_dir.mkdir(exist_ok=True)

try:
    profil = lade_profil(profil_pfad)
except FileNotFoundError:
    st.info("Noch kein Profil angelegt. Du kannst es jetzt direkt hier einrichten.")
    if st.button("✨ Profil anlegen", type="primary"):
        erstelle_profil(profil_pfad)
        st.rerun()
    st.stop()
except Exception as e:
    st.error(f"Profil konnte nicht geladen werden: {e}")
    st.stop()


def speichern(meldung: str = "Gespeichert.") -> None:
    speichere_profil(profil, profil_pfad)
    st.success(f"✅ {meldung}")


def zeilen(text: str) -> list[str]:
    return [z.strip() for z in text.splitlines() if z.strip()]


tab_person, tab_suche, tab_unterlagen, tab_automatik = st.tabs(
    ["👤 Persönliche Daten", "🔍 Suche & Bewertung", "📄 Unterlagen", "🤖 Automatik & Versand"]
)

# --------------------------------------------------------------------------- #
# Persönliche Daten
# --------------------------------------------------------------------------- #

with tab_person:
    p, a = profil.person, profil.angaben
    with st.form("profil_person"):
        st.markdown("##### Kontakt")
        c1, c2 = st.columns(2)
        with c1:
            vorname = st.text_input("Vorname", value=p.vorname)
            nachname = st.text_input("Nachname", value=p.nachname)
            email = st.text_input("E-Mail", value=p.email)
            telefon = st.text_input("Telefon", value=p.telefon)
            geburtsdatum = st.text_input("Geburtsdatum", value=p.geburtsdatum)
        with c2:
            strasse = st.text_input("Straße und Hausnummer", value=p.strasse)
            plz = st.text_input("PLZ", value=p.plz)
            ort = st.text_input("Ort", value=p.ort)
            land = st.text_input("Land", value=p.land)
            linkedin = st.text_input("LinkedIn", value=p.linkedin)
            github = st.text_input("GitHub / Website", value=p.github or p.website)
        kurzprofil = st.text_area(
            "Kurzprofil (geht in Bewertung und Anschreiben ein – je konkreter, desto besser)",
            value=profil.kurzprofil, height=120,
        )

        st.markdown("##### Angaben für Bewerbungsformulare")
        st.caption("Online-Formulare fragen diese Punkte oft als Pflichtfeld ab. Fehlt etwas, "
                   "bleibt der Browser-Agent dort stehen.")
        c3, c4 = st.columns(2)
        with c3:
            anrede = st.selectbox("Anrede", ["", "Herr", "Frau", "divers"],
                                  index=["", "Herr", "Frau", "divers"].index(a.anrede) if a.anrede in ("Herr", "Frau", "divers") else 0)
            verfuegbar_ab = st.text_input("Verfügbar ab", value=a.verfuegbar_ab)
            kuendigungsfrist = st.text_input("Kündigungsfrist", value=a.kuendigungsfrist)
            gehaltsvorstellung = st.text_input("Gehaltsvorstellung", value=a.gehaltsvorstellung,
                                               placeholder="z. B. 55.000 EUR brutto/Jahr")
        with c4:
            arbeitserlaubnis = st.text_input("Arbeitserlaubnis / Staatsangehörigkeit", value=a.arbeitserlaubnis)
            fuehrerschein = st.text_input("Führerschein", value=a.fuehrerschein)
            sprachen = st.text_input("Sprachkenntnisse", value=a.sprachen)

        if st.form_submit_button("💾 Speichern", type="primary"):
            p.vorname, p.nachname, p.email, p.telefon, p.geburtsdatum = vorname, nachname, email, telefon, geburtsdatum
            p.strasse, p.plz, p.ort, p.land, p.linkedin, p.github = strasse, plz, ort, land, linkedin, github
            profil.kurzprofil = kurzprofil
            a.anrede, a.verfuegbar_ab, a.kuendigungsfrist = anrede, verfuegbar_ab, kuendigungsfrist
            a.gehaltsvorstellung, a.arbeitserlaubnis = gehaltsvorstellung, arbeitserlaubnis
            a.fuehrerschein, a.sprachen = fuehrerschein, sprachen
            speichern("Persönliche Daten gespeichert.")

# --------------------------------------------------------------------------- #
# Suche & Bewertung
# --------------------------------------------------------------------------- #

with tab_suche:
    s, b = profil.suche, profil.bewertung
    st.caption("Diese Einstellungen nutzt die Automatik. In „Schritt für Schritt“ kannst du sie für eine "
               "einzelne Suche anpassen.")
    with st.form("profil_suche"):
        c1, c2 = st.columns(2)
        with c1:
            was_text = st.text_area("Suchbegriffe (einer pro Zeile)", value="\n".join(s.was), height=140)
            wo = st.text_input("Ort", value=s.wo or "")
            umkreis = st.slider("Umkreis (km)", 0, 200, int(s.umkreis))
        with c2:
            tage = st.number_input("Veröffentlicht seit (Tagen)", 1, 100, int(s.veroeffentlicht_seit_tagen))
            max_pro_query = st.number_input("Max. Treffer pro Suchbegriff", 5, 300, int(s.max_pro_query))
            nur_vollzeit = st.checkbox("Nur Vollzeit", value=s.nur_vollzeit)
            nur_homeoffice = st.checkbox("Homeoffice bevorzugen", value=s.nur_homeoffice)
            quellen = st.multiselect("Jobbörsen", ["arbeitsagentur", "get-in-it"], default=s.quellen)

        st.markdown("##### Bewertung")
        c3, c4 = st.columns(2)
        with c3:
            skills_text = st.text_area("Pflicht-Skills (einer pro Zeile)", value="\n".join(b.skills), height=140)
            wunsch_text = st.text_area("Wunschthemen (eines pro Zeile)", value="\n".join(b.wunsch), height=110)
        with c4:
            ausschluss_text = st.text_area("Ausschlusskriterien (eines pro Zeile)", value="\n".join(b.ausschluss),
                                           height=110)
            gesperrt_text = st.text_area("Gesperrte Arbeitgeber (einer pro Zeile)",
                                         value="\n".join(b.ausschluss_arbeitgeber), height=80,
                                         help="Wird auch über „Nein – nie wieder vorschlagen“ gefüllt.")
            min_score = st.slider("Mindest-Passung für Vorschläge", 0, 100, int(b.min_score))

        if st.form_submit_button("💾 Speichern", type="primary"):
            if not zeilen(was_text):
                st.error("Mindestens ein Suchbegriff ist nötig.")
            else:
                s.was, s.wo, s.umkreis = zeilen(was_text), wo.strip(), int(umkreis)
                s.veroeffentlicht_seit_tagen, s.max_pro_query = int(tage), int(max_pro_query)
                s.nur_vollzeit, s.nur_homeoffice = nur_vollzeit, nur_homeoffice
                s.quellen = quellen or ["arbeitsagentur"]
                b.skills, b.wunsch = zeilen(skills_text), zeilen(wunsch_text)
                b.ausschluss, b.ausschluss_arbeitgeber = zeilen(ausschluss_text), zeilen(gesperrt_text)
                b.min_score = float(min_score)
                speichern("Such- und Bewertungseinstellungen gespeichert.")

# --------------------------------------------------------------------------- #
# Unterlagen
# --------------------------------------------------------------------------- #

with tab_unterlagen:
    st.caption("Lebenslauf und Zeugnisse werden jeder Bewerbung angehängt. Am besten als PDF.")
    c1, c2 = st.columns(2)
    with c1:
        lebenslauf_upload = st.file_uploader("📄 Lebenslauf", type=["pdf", "docx", "txt", "md"], key="lebenslauf_upload")
        if lebenslauf_upload is not None:
            ziel = unterlagen_dir / lebenslauf_upload.name
            ziel.write_bytes(lebenslauf_upload.getbuffer())
            if profil.unterlagen.lebenslauf != str(ziel):
                profil.unterlagen.lebenslauf = str(ziel)
                speichern(f"Lebenslauf gespeichert: {ziel.name}")
    with c2:
        zeugnisse_upload = st.file_uploader(
            "🎓 Zeugnisse & weitere Unterlagen", type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
            accept_multiple_files=True, key="zeugnisse_upload",
        )
        if zeugnisse_upload:
            neue = []
            for datei in zeugnisse_upload:
                ziel = unterlagen_dir / datei.name
                ziel.write_bytes(datei.getbuffer())
                neue.append(str(ziel))
            if profil.unterlagen.zeugnisse != neue:
                profil.unterlagen.zeugnisse = neue
                speichern(f"{len(neue)} Datei(en) gespeichert.")

    st.markdown("##### Aktuelle Unterlagen")
    eintraege = [("Lebenslauf", profil.unterlagen.lebenslauf)] + [("Zeugnis", z) for z in profil.unterlagen.zeugnisse]
    if not any(pfad for _, pfad in eintraege):
        st.info("Noch keine Unterlagen hinterlegt.")
    for art, pfad in eintraege:
        if not pfad:
            continue
        datei = Path(pfad)
        if datei.exists():
            st.markdown(f"✅ **{art}:** `{datei.name}` ({datei.stat().st_size / 1024:.0f} KB)")
        else:
            st.markdown(f"❌ **{art}:** `{pfad}` – Datei nicht gefunden")

    text = dokumente.lebenslauf_text(profil)
    if text:
        with st.expander(f"Was die KI aus deinem Lebenslauf liest ({len(text)} Zeichen)"):
            st.text(text[:5000])
    elif profil.unterlagen.lebenslauf:
        st.caption("Aus dem Lebenslauf ließ sich kein Text lesen (z. B. eingescanntes PDF oder DOCX). "
                   "Das Kurzprofil wird trotzdem genutzt.")

    with st.form("anschreiben_stil"):
        stil = st.text_area("✍️ Stil der Anschreiben", value=profil.unterlagen.anschreiben_stil, height=90)
        if st.form_submit_button("💾 Speichern"):
            profil.unterlagen.anschreiben_stil = stil
            speichern("Anschreiben-Stil gespeichert.")

# --------------------------------------------------------------------------- #
# Automatik & Versand
# --------------------------------------------------------------------------- #

MODUS_TEXT = {"automatisch": "🤖 Automatisch vorbereiten", "nur_suchen": "🔍 Nur suchen & bewerten", "aus": "⏸️ Aus"}
EIGENER = "Eigener Zeitplan (Cron-Ausdruck)"

with tab_automatik:
    au = profil.automatisierung
    st.markdown("##### Automatik")
    modus = st.radio("Was soll nach Zeitplan passieren?", list(MODI), index=list(MODI).index(au.effektiver_modus),
                     format_func=MODUS_TEXT.get, horizontal=True)
    voreinstellungen = {**zeitplan.VOREINSTELLUNGEN}
    aktuell = next((name for name, ausdruck in voreinstellungen.items() if ausdruck == au.cron), EIGENER)
    optionen = [*voreinstellungen, EIGENER]
    wahl = st.selectbox("Zeitplan", optionen, index=optionen.index(aktuell))
    ausdruck = voreinstellungen.get(wahl) or st.text_input(
        "Cron-Ausdruck (Minute Stunde Tag Monat Wochentag, deutsche Zeit)", value=au.cron,
        help="Beispiel: 0 7,13 * * 1-5 = werktags um 7 und 13 Uhr",
    )
    fehler = zeitplan.pruefe(ausdruck)
    if fehler:
        st.error(fehler)
    elif modus != "aus":
        st.caption(f"Nächster Lauf: **{zeitplan.beschreibe(zeitplan.naechster_termin(ausdruck))}**")

    c1, c2, c3 = st.columns(3)
    max_pro_lauf = c1.number_input("Bewerbungen pro Lauf vorbereiten", 1, 20, int(au.max_pro_lauf),
                                   help="So viele der besten neuen Stellen landen pro Lauf bei dir zur Entscheidung.")
    max_pro_tag = c2.number_input("Höchstens versenden pro Tag", 1, 50, int(au.max_pro_tag),
                                  help="Sicherheitsgrenze, falls aus Versehen viele freigegeben werden.")
    ziel = c3.number_input("Wochenziel (Bewerbungen)", 1, 50, int(au.ziel_pro_woche),
                           help="z. B. aus deiner Eingliederungsvereinbarung")
    probelauf = st.checkbox(
        "Online-Formulare beim Vorbereiten testweise ausfüllen (Probelauf)", value=au.probelauf_vorab,
        help="Du siehst dann schon vor dem Ja, welche Felder klappen. Kostet pro Stelle 1–3 Minuten und KI-Tokens.",
    )
    if st.button("💾 Automatik speichern", type="primary", disabled=bool(fehler)):
        au.modus, au.aktiv, au.cron = modus, modus != "aus", ausdruck.strip()
        au.max_pro_lauf, au.max_pro_tag, au.ziel_pro_woche = int(max_pro_lauf), int(max_pro_tag), int(ziel)
        au.probelauf_vorab = probelauf
        speichern("Automatik gespeichert – gilt ab dem nächsten Tick (spätestens in 5 Minuten).")

    st.divider()
    st.markdown("##### Versandwege")
    v1, v2 = st.columns(2)
    with v1, st.container(border=True):
        st.markdown("**📧 E-Mail (SMTP)**")
        konfig = mail.smtp_konfig(profil)
        if konfig:
            st.success(f"Eingerichtet: `{konfig.host}:{konfig.port}` als `{konfig.absender}`")
            if st.button("Testmail an mich senden"):
                try:
                    mail.testmail(profil)
                    st.success(f"Testmail an {konfig.absender} verschickt – bitte Postfach prüfen.")
                except Exception as e:
                    st.error(f"Testmail fehlgeschlagen: {e}")
        else:
            st.warning("Nicht eingerichtet – Bewerbungen per E-Mail sind dann nicht möglich.")
            st.code("SMTP_HOST=smtp.example.de\nSMTP_PORT=465\nSMTP_BENUTZER=ich@example.de\n"
                    "SMTP_PASSWORT=...\n# optional: SMTP_ABSENDER=ich@example.de", language="bash")
            st.caption("In die `.env` (lokal) bzw. die Deployment-Umgebung eintragen und neu starten.")
    with v2, st.container(border=True):
        st.markdown("**🌐 Online-Formulare (Browser-Agent)**")
        if ablauf.browser_verfuegbar():
            st.success("browser-use ist installiert.")
        else:
            st.warning('Nicht installiert: `pip install -e ".[browser]"` und `playwright install chromium`.')
        if profil.ba_benutzer and profil.ba_passwort:
            st.caption("BA-Login ist hinterlegt (für „Online bewerben“ bei der Arbeitsagentur).")
        else:
            st.caption("Optional: BA_BENUTZER / BA_PASSWORT für Bewerbungen über das Konto der Arbeitsagentur.")

    st.divider()
    st.markdown("##### 🤖 KI-Modell")
    schluessel = [n for n in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY", "OLLAMA_BASE_URL") if os.getenv(n)]
    if schluessel:
        st.success(f"KI verfügbar über {llm_modul.provider(profil.bewertung.modell)} ({', '.join(schluessel)} gesetzt)")
    else:
        st.warning("Kein KI-Zugang gesetzt – Bewertung per Stichworten, Anschreiben aus einer Vorlage.")
    modelle = [
        "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
        "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", *llm_modul.OLLAMA_MODELLE,
    ]
    if profil.bewertung.modell not in modelle:
        modelle.insert(0, profil.bewertung.modell)
    with st.form("llm_modell"):
        gewaehlt = st.selectbox("Modell für Bewertung, Anschreiben und Browser-Agent", modelle,
                                index=modelle.index(profil.bewertung.modell))
        if st.form_submit_button("💾 Modell speichern"):
            profil.bewertung.modell = gewaehlt
            speichern(f"Modell auf {gewaehlt} gesetzt.")
