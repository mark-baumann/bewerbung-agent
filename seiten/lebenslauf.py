"""Lebenslauf: dauerhaft gespeichert – daraus lese ich Kurzprofil, Jobtitel, Ort und Fähigkeiten."""

from datetime import datetime
from pathlib import Path

import streamlit as st

from bewerbungsagent import dokumente, lebenslauf, ui, ui_einstellungen, zeitplan
from bewerbungsagent.config import ermittle_profil_pfad, finde_datei, lade_profil, speichere_profil, unterlagen_dir

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
                          + (f" (+{profil.suche.umkreis} km)" if profil.suche.wo else ""))
            if profil.bewertung.skills:
                zeilen.append(f"**Was du kannst:** {', '.join(profil.bewertung.skills)}")
            st.markdown("  \n".join(zeilen))
            if ableitung.quelle != "ki":
                st.caption("Ohne KI lese ich nur deine Kontaktdaten. Sobald die KI verbunden ist "
                           "(Einstellungen → Konten), lese ich auch Jobtitel und Fähigkeiten.")
            else:
                st.caption("Gilt für Suche, Bewertung und Anschreiben. Jobtitel und Ort kannst du unter "
                           "„Suchen“ jederzeit ändern.")
        with st.container(horizontal=True, vertical_alignment="center"):
            st.page_link(ui.SEITE_SUCHEN, label="Jobtitel & Ort ändern", icon=":material/search:")
            if ableitung is not None and st.button("Neu auslesen", icon=":material/refresh:", type="tertiary",
                                                    key="lebenslauf_neu_knopf"):
                st.session_state["lebenslauf_neu"] = True
                st.rerun()
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
