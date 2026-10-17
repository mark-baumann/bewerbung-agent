"""Lebenslauf: dauerhaft gespeichert – daraus lese ich Kurzprofil, Jobtitel, Ort und Fähigkeiten."""

from datetime import datetime
from pathlib import Path

import streamlit as st

from bewerbungsagent import ablauf, anschreiben, dokumente, lebenslauf, ui, ui_einstellungen, zeitplan
from bewerbungsagent.config import ermittle_profil_pfad, finde_datei, lade_profil, speichere_profil, unterlagen_dir
from bewerbungsagent.scoring import llm as llm_modul

st.title("Lebenslauf")

profil_pfad = ermittle_profil_pfad()
try:
    profil = lade_profil(profil_pfad, pruefen=False)
except FileNotFoundError:
    st.info("Noch nicht eingerichtet – der Assistent führt dich in fünf kurzen Schritten durch.",
            icon=":material/waving_hand:")
    st.page_link(ui.SEITE_HEUTE, label="Einrichtung starten", icon=":material/rocket_launch:")
    st.stop()
except Exception as e:
    st.error(f"Profil konnte nicht geladen werden: {e}")
    st.stop()


def _gespeichert(schluessel: str, meldung: str) -> None:
    # Neuer Schluessel = leerer Uploader (sonst wuerde die Datei bei jedem Neuladen erneut gespeichert)
    st.session_state[schluessel] = st.session_state.get(schluessel, 0) + 1
    st.session_state["lebenslauf_meldung"] = meldung
    st.rerun()


if meldung := st.session_state.pop("lebenslauf_meldung", None):
    st.toast(meldung, icon=":material/check_circle:")


def _groesse(pfad: Path) -> str:
    return f"{max(1, round(pfad.stat().st_size / 1024))} KB"


def _datum(pfad: Path) -> str:
    return datetime.fromtimestamp(pfad.stat().st_mtime, zeitplan.ZEITZONE).strftime("%d.%m.%Y")


def _station(station: dict) -> str:
    text = station["rolle"] + (f" ({station['arbeitgeber']})" if station.get("arbeitgeber") else "")
    # Unsicher zugeordnet (Rolle oder Arbeitgeber?): grau – die nutze ich nicht im Anschreiben
    return text if station.get("sicher") is True else f":gray[{text}]"


# --------------------------------------------------------------------------- #
# Die Datei
# --------------------------------------------------------------------------- #

datei = profil.lebenslauf_pfad()
with st.container(border=True):
    if datei is not None:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**:material/description: {datei.name}**  \n"
                        f":gray[{_groesse(datei)} · hochgeladen am {_datum(datei)} · "
                        "geht mit jeder Bewerbung mit]")
            st.download_button("Herunterladen", data=datei.read_bytes(), file_name=datei.name,
                               icon=":material/download:", key="lebenslauf_download")
        bereich = st.expander("Anderen Lebenslauf hochladen", icon=":material/upload:")
    else:
        if profil.unterlagen.lebenslauf:
            st.warning(f"Die Datei „{Path(profil.unterlagen.lebenslauf).name}“ fehlt – bitte erneut hochladen.",
                       icon=":material/warning:")
        else:
            st.markdown("Lade deinen Lebenslauf hoch (am besten als PDF). Er geht mit jeder Bewerbung mit – und ich "
                        "lese daraus, wonach ich für dich suche.")
        bereich = st.container()
    with bereich:
        upload = st.file_uploader("Lebenslauf (PDF, Word oder Text)", type=ui_einstellungen.LEBENSLAUF_TYPEN,
                                  key=f"lebenslauf_upload_{st.session_state.get('lebenslauf_n', 0)}")
    if upload is not None:
        ziel, _ = ui_einstellungen.lebenslauf_speichern(profil, upload)
        _gespeichert("lebenslauf_n", f"Lebenslauf gespeichert: {ziel.name}")

# --------------------------------------------------------------------------- #
# Was ich daraus lese (automatisch übernommen)
# --------------------------------------------------------------------------- #

if datei is not None:
    neu_auslesen = st.session_state.pop("lebenslauf_neu", False)
    with st.spinner("Ich lese deinen Lebenslauf …"):
        ableitung, uebernommen = lebenslauf.aktualisieren(profil, profil_pfad, erzwingen=neu_auslesen)
    if uebernommen:
        if {"Fähigkeiten", "Jobtitel", "Themen"} & set(uebernommen):
            with ui.oeffne_db() as db:  # Passung offener Stellen zum neuen Lebenslauf
                ablauf.stichwort_bewertungen_auffrischen(db, profil)
        st.toast("Aus dem Lebenslauf übernommen: " + ", ".join(uebernommen), icon=":material/auto_awesome:")

    with st.container(border=True):
        st.markdown("##### :material/auto_awesome: Das lese ich aus deinem Lebenslauf")
        if ableitung is None:
            st.markdown("Aus der Datei ließ sich kein Text lesen (z. B. ein eingescanntes Bild). Lade sie am besten "
                        "als PDF aus Word oder einem Schreibprogramm hoch – oder trage deine Jobtitel unter "
                        "**Suchen** ein.")
        else:
            if profil.kurzprofil:
                st.markdown(profil.kurzprofil)
            zeilen = []
            if profil.suche.was:
                zeilen.append(f"**Jobtitel:** {' · '.join(profil.suche.was)}")
            zeilen.append(f"**Wo:** {profil.suche.wo or 'ganz Deutschland'}"
                          + (f" (+{profil.suche.umkreis} km)" if profil.suche.wo else "")
                          + (f" · Wohnort {ableitung.ort}" if ableitung.ort and ableitung.ort != profil.suche.wo else ""))
            if profil.bewertung.skills:
                zeilen.append(f"**Was du kannst:** {', '.join(profil.bewertung.skills)}")
            if ableitung.themen:  # nur, wenn sie wirklich aus dem Lebenslauf kommen
                zeilen.append(f"**Themen:** {', '.join(ableitung.themen)}")
            if ableitung.stationen:
                zeilen.append("**Werdegang:** " + " · ".join(
                    _station(x) for x in lebenslauf.sortiert(ableitung.stationen)[:4]))
            st.markdown("  \n".join(zeilen))
            if neu_auslesen and ableitung.quelle == "ki" and ableitung.fehler:  # die KI-Auswertung blieb stehen
                st.info(f"Neu auslesen ging gerade nicht – {ableitung.fehler}. Es gilt weiter die letzte Auswertung "
                        "mit KI.", icon=":material/info:")
            elif ableitung.quelle == "ki":
                st.caption("Mit KI gelesen. Gilt für Suche, Bewertung und Anschreiben – Jobtitel und Ort kannst du "
                           "unter „Suchen“ jederzeit ändern.")
            elif ableitung.fehler:
                st.info(f"Die KI hat gerade nicht geantwortet – {ableitung.fehler}. Ich habe deinen Lebenslauf ohne KI "
                        "gelesen und versuche es später automatisch wieder (oder jetzt mit „Neu auslesen“).",
                        icon=":material/info:")
            elif not llm_modul.client_verfuegbar():
                st.caption("Ohne KI-Zugang lese ich die Abschnitte (Über mich, Fähigkeiten, Erfahrung) selbst. Mit KI "
                           "(Einstellungen → Konten) werden Jobtitel und Kurzprofil genauer.")
        with st.container(horizontal=True, vertical_alignment="center"):
            if ableitung is not None and profil.suche.was:
                with ui.oeffne_db() as db:
                    laeuft = bool(db.laufende_laeufe(("automatik",)))
                st.button("Jetzt damit suchen", type="primary", icon=":material/travel_explore:", disabled=laeuft,
                          key="lebenslauf_suchen", on_click=ui.starte_hintergrund,
                          args=("automatik", "--ausloeser", "knopf"),
                          kwargs={"meldung": "Ich suche mit den Kriterien aus deinem Lebenslauf – die besten "
                                             "Stellen lege ich dir unter „Heute“ vor."})
            st.page_link(ui.SEITE_SUCHEN, label="Jobtitel & Ort ändern", icon=":material/search:")
            if ableitung is not None and st.button("Neu auslesen", icon=":material/refresh:", type="tertiary",
                                                    key="lebenslauf_neu_knopf"):
                st.session_state["lebenslauf_neu"] = True
                st.rerun()
            if ableitung is not None and st.button("Probe-Anschreiben", icon=":material/edit_note:",
                                                    type="tertiary", key="lebenslauf_probe_knopf",
                                                    help="Schreibt mit deinem Lebenslauf ein Anschreiben für die "
                                                         "passendste offene Stelle – nur zum Ansehen, nichts geht raus."):
                with ui.oeffne_db() as db:  # die passendste Stelle, fuer die es noch keine Karte gibt
                    beste = db.kandidaten(min_score=0, limit=1,
                                          ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)
                if not beste:
                    st.session_state["lebenslauf_probe"] = None
                else:
                    job, score = beste[0]
                    with st.spinner(f"Ich schreibe ein Probe-Anschreiben für „{job.titel}“ …"):
                        ergebnis = anschreiben.erzeuge_geprueft(job, profil, score)
                    st.session_state["lebenslauf_probe"] = {
                        "titel": job.titel, "arbeitgeber": job.arbeitgeber, "text": ergebnis.text,
                        "quelle": ergebnis.quelle, "grund": ergebnis.bericht.get("ki_fehler", ""),
                        "unbelegt": ergebnis.bericht.get("unbelegt") or [],
                    }
        if "lebenslauf_probe" in st.session_state:
            probe = st.session_state["lebenslauf_probe"]
            if probe is None:
                st.caption("Noch keine bewertete Stelle – such erst unter „Suchen“, dann zeige ich hier eine Probe.")
            else:
                with st.container(border=True):
                    st.markdown(f"**Probe für „{probe['titel']}“** – {probe['arbeitgeber']}")
                    st.caption("Mit KI aus deinem Lebenslauf geschrieben." if probe["quelle"] == "llm" else
                               "Aus der Vorlage mit Angaben aus deinem Lebenslauf"
                               + (f" – die KI hat nicht geholfen, weil {probe['grund']}." if probe["grund"] else "."))
                    st.text(probe["text"])
                    if probe["unbelegt"]:
                        st.warning("Nicht durch deinen Lebenslauf belegt: " + "; ".join(probe["unbelegt"][:4]),
                                   icon=":material/fact_check:")
        text = dokumente.lebenslauf_text(profil)
        if text:
            with st.expander(f"Text aus der Datei ({len(text)} Zeichen)"):
                st.text(text[:5000])

# --------------------------------------------------------------------------- #
# Zeugnisse & weitere Unterlagen
# --------------------------------------------------------------------------- #

with st.container(border=True):
    st.markdown("##### :material/folder_open: Zeugnisse & weitere Unterlagen")
    st.caption("Gehen zusammen mit dem Lebenslauf an jede Bewerbung. Am besten als PDF.")
    for i, pfad in enumerate(list(profil.unterlagen.zeugnisse)):
        z, gefunden = Path(pfad), finde_datei(pfad)
        with st.container(horizontal=True, vertical_alignment="center"):
            if gefunden:
                st.markdown(f":material/attach_file: {z.name} :gray[({_groesse(gefunden)})]")
            else:
                st.markdown(f":orange[:material/warning:] {z.name} fehlt – bitte erneut hochladen")
            if st.button("Entfernen", key=f"zeugnis_weg_{i}", icon=":material/close:", type="tertiary"):
                profil.unterlagen.zeugnisse = [x for x in profil.unterlagen.zeugnisse if x != pfad]
                speichere_profil(profil, profil_pfad)
                st.rerun()
    neue = st.file_uploader(
        "Dateien hinzufügen", type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"], accept_multiple_files=True,
        key=f"zeugnisse_upload_{st.session_state.get('zeugnisse_n', 0)}",
    )
    if neue:
        ordner = unterlagen_dir()
        ordner.mkdir(parents=True, exist_ok=True)
        for upload in neue:
            ziel = ordner / Path(upload.name).name
            ziel.write_bytes(upload.getvalue())
            if str(ziel) not in profil.unterlagen.zeugnisse:
                profil.unterlagen.zeugnisse.append(str(ziel))
        speichere_profil(profil, profil_pfad)
        _gespeichert("zeugnisse_n", f"{len(neue)} Datei(en) gespeichert.")
