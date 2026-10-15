"""Einstellungen: Über mich, Suche, Automatik, Konten."""

from pathlib import Path

import streamlit as st

from bewerbungsagent import dokumente, ui, ui_einstellungen, zeitplan
from bewerbungsagent.config import MODI, ermittle_profil_pfad, lade_profil, speichere_profil, unterlagen_dir

st.title("Einstellungen")

profil_pfad = ermittle_profil_pfad()
try:
    profil = lade_profil(profil_pfad, pruefen=False)  # auch halb eingerichtet bearbeitbar
except FileNotFoundError:
    st.info("Noch nicht eingerichtet – der Assistent führt dich in fünf kurzen Schritten durch.",
            icon=":material/waving_hand:")
    st.page_link(ui.SEITE_HEUTE, label="Einrichtung starten", icon=":material/rocket_launch:")
    st.stop()
except Exception as e:
    st.error(f"Profil konnte nicht geladen werden: {e}")
    st.stop()


def speichern(meldung: str = "Gespeichert.") -> None:
    speichere_profil(profil, profil_pfad)
    st.success(meldung, icon=":material/check_circle:")


def zeilen(text: str) -> list[str]:
    return [z.strip() for z in text.splitlines() if z.strip()]


tab_ich, tab_suche, tab_automatik, tab_konten = st.tabs(
    ["Über mich", "Suche", "Automatik", "Konten"], key="tab", bind="query-params",
)

# --------------------------------------------------------------------------- #
# Über mich: Kontakt, Formular-Angaben, Unterlagen
# --------------------------------------------------------------------------- #

with tab_ich:
    p, a = profil.person, profil.angaben
    with st.form("profil_person"):
        st.markdown("##### :material/badge: Kontakt")
        c1, c2 = st.columns(2)
        vorname = c1.text_input("Vorname", value=p.vorname)
        nachname = c2.text_input("Nachname", value=p.nachname)
        email = c1.text_input("E-Mail", value=p.email)
        telefon = c2.text_input("Telefon", value=p.telefon)
        strasse = c1.text_input("Straße und Hausnummer", value=p.strasse)
        c3, c4 = c2.columns([1, 2])
        plz = c3.text_input("PLZ", value=p.plz)
        ort = c4.text_input("Ort", value=p.ort)
        with st.expander("Weitere Angaben (Geburtsdatum, Land, LinkedIn …)"):
            e1, e2 = st.columns(2)
            geburtsdatum = e1.text_input("Geburtsdatum", value=p.geburtsdatum)
            land = e2.text_input("Land", value=p.land)
            linkedin = e1.text_input("LinkedIn", value=p.linkedin)
            github = e2.text_input("GitHub / Website", value=p.github or p.website)
        kurzprofil = st.text_area(
            "Kurz über dich – je konkreter, desto besser passen Bewertung und Anschreiben",
            value=profil.kurzprofil, height=110,
            placeholder="z. B. 8 Jahre Buchhaltung im Mittelstand, DATEV und SAP, sucht Teilzeit in Köln",
        )

        st.markdown("##### :material/edit_note: Angaben für Online-Formulare")
        st.caption("Formulare fragen das oft als Pflichtfeld ab. Fehlt etwas, frage ich auf der Karte nach.")
        f1, f2 = st.columns(2)
        anreden = ["", "Herr", "Frau", "divers"]
        anrede = f1.selectbox("Anrede", anreden, index=anreden.index(a.anrede) if a.anrede in anreden else 0)
        gehaltsvorstellung = f2.text_input("Gehaltsvorstellung", value=a.gehaltsvorstellung,
                                           placeholder="z. B. 45.000 EUR brutto/Jahr")
        verfuegbar_ab = f1.text_input("Verfügbar ab", value=a.verfuegbar_ab)
        kuendigungsfrist = f2.text_input("Kündigungsfrist", value=a.kuendigungsfrist)
        with st.expander("Weitere Formular-Angaben"):
            g1, g2 = st.columns(2)
            titel = g1.text_input("Akademischer Titel", value=a.titel, placeholder="z. B. Dr.")
            abschluss = g2.text_input("Höchster Abschluss", value=a.abschluss, placeholder="z. B. Bürokauffrau")
            arbeitserlaubnis = g1.text_input("Arbeitserlaubnis", value=a.arbeitserlaubnis)
            nationalitaet = g2.text_input("Staatsangehörigkeit", value=a.nationalitaet)
            fuehrerschein = g1.text_input("Führerschein", value=a.fuehrerschein)
            sprachen = g2.text_input("Sprachkenntnisse", value=a.sprachen)
            erfahrung = g1.text_input("Berufserfahrung (Jahre)", value=a.berufserfahrung_jahre)
            umzug = g2.text_input("Umzugsbereitschaft", value=a.umzugsbereitschaft, placeholder="z. B. nein")
            weitere_text = st.text_area(
                "Weitere Antworten (eine pro Zeile: Frage = Antwort)",
                value="\n".join(f"{k} = {v}" for k, v in a.weitere.items()), height=90,
                help="Hier landen auch Antworten, die du auf einer Karte gibst.",
            )

        if st.form_submit_button("Speichern", type="primary", icon=":material/save:"):
            p.vorname, p.nachname, p.email, p.telefon, p.geburtsdatum = vorname, nachname, email, telefon, geburtsdatum
            p.strasse, p.plz, p.ort, p.land, p.linkedin, p.github = strasse, plz, ort, land, linkedin, github
            profil.kurzprofil = kurzprofil
            a.anrede, a.verfuegbar_ab, a.kuendigungsfrist = anrede, verfuegbar_ab, kuendigungsfrist
            a.gehaltsvorstellung, a.arbeitserlaubnis = gehaltsvorstellung, arbeitserlaubnis
            a.fuehrerschein, a.sprachen = fuehrerschein, sprachen
            a.titel, a.abschluss, a.nationalitaet = titel, abschluss, nationalitaet
            a.berufserfahrung_jahre, a.umzugsbereitschaft = erfahrung, umzug
            a.weitere = {k.strip(): v.strip() for k, _, v in (z.partition("=") for z in zeilen(weitere_text))
                         if k.strip() and v.strip()}
            speichern("Gespeichert.")

    with st.container(border=True):
        st.markdown("##### :material/description: Unterlagen")
        st.caption("Gehen mit jeder Bewerbung mit. Am besten als PDF.")
        ui_einstellungen.lebenslauf_hochladen(profil)
        zeugnisse_upload = st.file_uploader(
            "Zeugnisse & weitere Unterlagen", type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
            accept_multiple_files=True, key="zeugnisse_upload",
        )
        if zeugnisse_upload:
            ordner = unterlagen_dir()
            ordner.mkdir(parents=True, exist_ok=True)
            neue = []
            for datei in zeugnisse_upload:
                ziel = ordner / datei.name
                ziel.write_bytes(datei.getbuffer())
                neue.append(str(ziel))
            if profil.unterlagen.zeugnisse != neue:
                profil.unterlagen.zeugnisse = neue
                speichern(f"{len(neue)} Datei(en) gespeichert.")
        for pfad in profil.unterlagen.zeugnisse:
            datei = Path(pfad)
            if datei.exists():
                st.caption(f":material/attach_file: {datei.name} ({datei.stat().st_size / 1024:.0f} KB)")
            else:
                st.caption(f":orange[:material/warning:] {datei.name} fehlt – bitte erneut hochladen")
        text = dokumente.lebenslauf_text(profil)
        if text:
            with st.expander(f"Was ich aus deinem Lebenslauf lese ({len(text)} Zeichen)"):
                st.text(text[:5000])
        elif profil.unterlagen.lebenslauf:
            st.caption("Aus dem Lebenslauf ließ sich kein Text lesen (z. B. eingescanntes PDF). "
                       "Dann nutze ich die Angaben „Kurz über dich“.")

    with st.form("anschreiben_stil"):
        st.markdown("##### :material/stylus: Wie deine Anschreiben klingen sollen")
        stil = st.text_area("Stil", value=profil.unterlagen.anschreiben_stil, height=90, label_visibility="collapsed")
        if st.form_submit_button("Speichern", icon=":material/save:"):
            profil.unterlagen.anschreiben_stil = stil
            speichern("Stil gespeichert.")

# --------------------------------------------------------------------------- #
# Suche: Begriffe, Ort, Bewertung, Jobbörsen, Wunsch-Arbeitgeber
# --------------------------------------------------------------------------- #

with tab_suche:
    s, b = profil.suche, profil.bewertung
    st.caption("Damit sucht die Automatik. Unter „Suchen“ kannst du einzelne Suchen anders einstellen.")
    with st.form("profil_suche"):
        was_text = st.text_area("Jobtitel (einer pro Zeile)", value="\n".join(s.was), height=130)
        c1, c2 = st.columns([2, 1])
        wo = c1.text_input("Wo?", value=s.wo or "", help="Leer lassen für ganz Deutschland")
        umkreis = c2.number_input("Umkreis (km)", 0, 200, int(s.umkreis), step=5)
        c3, c4 = st.columns(2)
        nur_vollzeit = c3.checkbox("Nur Vollzeit", value=s.nur_vollzeit)
        nur_homeoffice = c4.checkbox("Homeoffice bevorzugen", value=s.nur_homeoffice)
        min_score = st.slider("Wie gut muss eine Stelle mindestens passen?", 0, 100, int(b.min_score),
                              help="Passung 0–100. Darunter lege ich dir nichts vor.")
        with st.expander("Wie ich Stellen bewerte (Feinheiten)"):
            skills_text = st.text_area("Was du sicher kannst (eins pro Zeile)", value="\n".join(b.skills), height=110)
            wunsch_text = st.text_area("Was dich besonders interessiert (eins pro Zeile)", value="\n".join(b.wunsch),
                                       height=90)
            ausschluss_text = st.text_area("Stellen mit diesen Wörtern nie vorschlagen (eins pro Zeile)",
                                           value="\n".join(b.ausschluss), height=90)
            gesperrt_text = st.text_area("Diese Arbeitgeber nie vorschlagen (einer pro Zeile)",
                                         value="\n".join(b.ausschluss_arbeitgeber), height=80,
                                         help="Füllt sich auch über „Nein – nie wieder vorschlagen“.")
            d1, d2 = st.columns(2)
            tage = d1.number_input("Nur Anzeigen der letzten … Tage", 1, 100, int(s.veroeffentlicht_seit_tagen))
            max_pro_query = d2.number_input("Höchstens Treffer je Jobtitel und Jobbörse", 5, 300, int(s.max_pro_query))
        if st.form_submit_button("Speichern", type="primary", icon=":material/save:"):
            if not zeilen(was_text):
                st.error("Mindestens ein Jobtitel ist nötig.")
            else:
                s.was, s.wo, s.umkreis = zeilen(was_text), wo.strip(), int(umkreis)
                s.veroeffentlicht_seit_tagen, s.max_pro_query = int(tage), int(max_pro_query)
                s.nur_vollzeit, s.nur_homeoffice = nur_vollzeit, nur_homeoffice
                b.skills, b.wunsch = zeilen(skills_text), zeilen(wunsch_text)
                b.ausschluss, b.ausschluss_arbeitgeber = zeilen(ausschluss_text), zeilen(gesperrt_text)
                b.min_score = float(min_score)
                speichern("Sucheinstellungen gespeichert.")

    st.markdown("#### :material/travel_explore: Jobbörsen")
    with ui.oeffne_db() as db:
        ui_einstellungen.zeige_jobboersen(profil, speichern, db)

# --------------------------------------------------------------------------- #
# Automatik: Zeitplan, Grenzen, Autopilot, Dazulernen
# --------------------------------------------------------------------------- #

MODUS_TEXT = {"automatisch": "Suchen & Bewerbungen vorbereiten", "nur_suchen": "Nur suchen (ich wähle selbst aus)",
              "aus": "Aus"}
AUTOPILOT_TEXT = {"aus": "Aus – jede Bewerbung braucht mein Ja", "mit_frist": "Mit Einspruchsfrist",
                  "sofort": "Sofort"}
EIGENER = "Eigener Zeitplan (für Fortgeschrittene)"

with tab_automatik:
    au = profil.automatisierung
    with st.container(border=True):
        st.markdown("##### :material/schedule: Was soll regelmäßig passieren?")
        modus = st.radio("Modus", list(MODI), index=list(MODI).index(au.effektiver_modus), format_func=MODUS_TEXT.get,
                         label_visibility="collapsed")
        voreinstellungen = {**zeitplan.VOREINSTELLUNGEN}
        aktuell = next((name for name, ausdruck in voreinstellungen.items() if ausdruck == au.cron), EIGENER)
        optionen = [*voreinstellungen, EIGENER]
        wahl = st.selectbox("Wann?", optionen, index=optionen.index(aktuell), disabled=modus == "aus")
        ausdruck = voreinstellungen.get(wahl) or st.text_input(
            "Zeitplan (Minute Stunde Tag Monat Wochentag, deutsche Zeit)", value=au.cron,
            help="Beispiel: 0 7,13 * * 1-5 = werktags um 7 und 13 Uhr",
        )
        fehler = zeitplan.pruefe(ausdruck)
        if fehler:
            st.error(fehler)
        elif modus != "aus":
            st.caption(f"Nächstes Mal: **{zeitplan.beschreibe(zeitplan.naechster_termin(ausdruck))}**")
        c1, c2, c3 = st.columns(3)
        max_pro_lauf = c1.number_input("Pro Lauf vorbereiten", 1, 20, int(au.max_pro_lauf),
                                       help="So viele der besten neuen Stellen lege ich dir pro Lauf vor.")
        max_pro_tag = c2.number_input("Höchstens pro Tag senden", 1, 50, int(au.max_pro_tag),
                                      help="Sicherheitsgrenze, falls aus Versehen viele freigegeben werden.")
        ziel = c3.number_input("Wochenziel", 1, 50, int(au.ziel_pro_woche),
                               help="z. B. aus deiner Eingliederungsvereinbarung")
        probelauf = st.checkbox(
            "Online-Formulare schon vorab testweise ausfüllen", value=au.probelauf_vorab,
            help="Dann siehst du vor dem Ja, ob das Formular klappt. Dauert pro Stelle 1–3 Minuten.",
        )

    with st.container(border=True):
        st.markdown("##### :material/smart_toy: Autopilot – darf ich mich allein bewerben?")
        st.caption("Nur bei sehr guter Passung, geprüftem Anschreiben ohne unbelegte Aussagen, sicherem Versandweg und "
                   "erfolgreichem Formular-Test. Höchstens eine Bewerbung pro Arbeitgeber in 30 Tagen.")
        pilot = st.radio("Autopilot", list(AUTOPILOT_TEXT), index=list(AUTOPILOT_TEXT).index(au.effektiver_autopilot),
                         format_func=AUTOPILOT_TEXT.get, horizontal=True, label_visibility="collapsed")
        p1, p2, p3 = st.columns(3)
        frist = p1.number_input("Einspruchsfrist (Stunden)", 0, 72, int(au.autopilot_frist_stunden),
                                disabled=pilot != "mit_frist")
        min_passung = p2.slider("Mindest-Passung", 50, 100, int(au.autopilot_min_passung), disabled=pilot == "aus")
        min_qualitaet = p3.slider("Mindest-Qualität Anschreiben", 50, 100, int(au.autopilot_min_qualitaet),
                                  disabled=pilot == "aus")

    with st.expander("Selbstständig dazulernen", icon=":material/psychology:"):
        erkunden = st.checkbox("Ähnliche Jobtitel selbst ausprobieren", value=au.erkunden,
                               help="Ich teste je Lauf ein paar verwandte Jobtitel und behalte nur, was passt.")
        erkundung_max = st.number_input("Neue Jobtitel pro Lauf", 0, 5, int(au.erkundung_max_begriffe))
        postfach_an = st.checkbox("Antworten im Postfach erkennen (nur lesend)", value=au.postfach)
        nachfassen_tage = st.number_input("Nachfass-Mail vorschlagen nach … Tagen ohne Antwort (0 = nie)", 0, 60,
                                          int(au.nachfassen_tage))
        kosten = st.number_input("Höchstens so viele KI-Bewertungen pro Lauf (Kostenbremse)", 5, 500,
                                 int(profil.bewertung.max_llm_pro_lauf))

    if st.button("Automatik speichern", type="primary", disabled=bool(fehler), icon=":material/save:"):
        au.modus, au.aktiv, au.cron = modus, modus != "aus", ausdruck.strip()
        au.max_pro_lauf, au.max_pro_tag, au.ziel_pro_woche = int(max_pro_lauf), int(max_pro_tag), int(ziel)
        au.probelauf_vorab = probelauf
        au.erkunden, au.erkundung_max_begriffe = erkunden, int(erkundung_max)
        au.postfach, au.nachfassen_tage = postfach_an, int(nachfassen_tage)
        profil.bewertung.max_llm_pro_lauf = int(kosten)
        au.autopilot, au.autopilot_frist_stunden = pilot, int(frist)
        if pilot == "aus":
            from bewerbungsagent import autopilot

            with ui.oeffne_db() as db:
                autopilot.fristen_aufheben(db)
        au.autopilot_min_passung, au.autopilot_min_qualitaet = float(min_passung), float(min_qualitaet)
        speichern("Gespeichert – gilt ab dem nächsten Lauf (spätestens in 5 Minuten).")

# --------------------------------------------------------------------------- #
# Konten
# --------------------------------------------------------------------------- #

with tab_konten:
    ui_einstellungen.zeige_zugaenge(profil, speichern)
