"""Cockpit (Startseite): Automatik steuern und vorbereitete Bewerbungen mit Ja/Nein freigeben."""

from datetime import datetime, timezone

import streamlit as st

from bewerbungsagent import ablauf, ui, zeitplan
from bewerbungsagent.config import ermittle_profil_pfad, speichere_profil

MODI = {"automatisch": "🤖 Automatisch", "nur_suchen": "🔍 Nur suchen", "aus": "⏸️ Aus"}
MODUS_HILFE = {
    "automatisch": "Sucht nach Zeitplan, bewertet und bereitet die besten Stellen vor. "
                   "Raus geht nur, was du hier mit Ja bestätigst.",
    "nur_suchen": "Sucht und bewertet nach Zeitplan; welche Stellen du nimmst, wählst du unter "
                  "„Schritt für Schritt“.",
    "aus": "Kein Zeitplan. Du startest Suche und Bewerbungen selbst.",
}

st.title("📬 Bewerbungsagent")

profil = ui.profil_laden()
if profil is None:
    st.info(
        "### 👋 Willkommen!\n"
        "Lege zuerst dein Profil an: persönliche Daten, Suchbegriffe und deinen Lebenslauf. "
        "Danach sucht der Agent passende Stellen, schreibt Anschreiben und schickt Bewerbungen "
        "nach deinem **Ja** ab."
    )
    st.page_link("seiten/profil.py", label="Profil einrichten", icon="👤")
    st.stop()

a = profil.automatisierung
jetzt = datetime.now(timezone.utc)

with ui.oeffne_db() as db:
    wartend = db.vorgaenge("wartet")
    fehlgeschlagen = db.vorgaenge("fehlgeschlagen")
    diese_woche = db.beworben_seit(ablauf.wochenbeginn_utc(jetzt))
    rueckmeldungen = [v for v in db.vorgaenge(("abgeschickt", "selbst_beworben")) if v.rueckmeldung]
    einladungen = sum(1 for v in rueckmeldungen if v.rueckmeldung in ("einladung", "zusage"))
    offen = len(db.kandidaten(min_score=profil.bewertung.min_score, limit=200,
                              ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber))
    letzte_automatik = db.letzte_laeufe("automatik", limit=1)

    ui.zeige_einrichtung(profil)

    # ------------------------------------------------------------------ Kennzahlen
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("✋ Warten auf dich", len(wartend))
    k2.metric("📤 Diese Woche beworben", f"{diese_woche} / {a.ziel_pro_woche}")
    k3.metric("💬 Rückmeldungen", len(rueckmeldungen),
              delta=f"{einladungen} Einladung(en)" if einladungen else None)
    k4.metric("🎯 Weitere passende Stellen", offen,
              help=f"Mindestens {profil.bewertung.min_score:.0f} Punkte, aber noch nicht vorbereitet – "
                   "der nächste Lauf oder „Schritt für Schritt“ nimmt sie auf.")
    st.progress(min(1.0, diese_woche / max(1, a.ziel_pro_woche)),
                text=f"Wochenziel: {diese_woche} von {a.ziel_pro_woche} Bewerbungen")

    # ------------------------------------------------------------------ Automatik
    with st.container(border=True):
        links, rechts = st.columns([3, 2], vertical_alignment="center")
        with links:
            modus = st.segmented_control(
                "Automatik", list(MODI), default=a.effektiver_modus, format_func=MODI.get, key="cockpit_modus",
            )
            if modus and modus != a.effektiver_modus:
                a.modus, a.aktiv = modus, modus != "aus"
                speichere_profil(profil, ermittle_profil_pfad())
                st.toast(f"Modus: {MODI[modus]}", icon="💾")
            st.caption(MODUS_HILFE[a.effektiver_modus])
            naechster = ablauf.naechster_automatik_termin(profil)
            zeile = f"Zeitplan `{a.cron}`"
            if naechster:
                zeile += f" · nächster Lauf **{zeitplan.beschreibe(naechster)}**"
            if letzte_automatik:
                zeile += f"  \nLetzter Lauf: {ui.lauf_zusammenfassung(letzte_automatik[0])}"
            st.markdown(zeile)
        with rechts:
            laeuft = bool(db.laufende_laeufe(("automatik", "vorbereiten")))
            if st.button("▶️ Jetzt suchen & vorbereiten", type="primary", width="stretch", disabled=laeuft,
                         help="Sucht mit den Begriffen aus deinem Profil, bewertet und bereitet "
                              f"bis zu {a.max_pro_lauf} Bewerbungen vor."):
                ui.starte_hintergrund("automatik", "--ausloeser", "knopf",
                                      meldung="Suche gestartet – das dauert ein paar Minuten.")
                st.rerun()
            st.page_link("seiten/schritt_fuer_schritt.py", label="Lieber selbst suchen & auswählen",
                         icon="🧭")

    # ------------------------------------------------------------------ Laufender Stand
    @st.fragment(run_every=5 if ui.laeuft_etwas(db) else None)
    def laufstatus():
        with ui.oeffne_db() as frisch:
            if not ui.zeige_laufstatus(frisch):
                if st.session_state.get("cockpit_lief"):
                    st.session_state["cockpit_lief"] = False
                    st.rerun()
            else:
                st.session_state["cockpit_lief"] = True

    laufstatus()

    # ------------------------------------------------------------------ Entscheidungen
    st.subheader(f"✋ Deine Entscheidung ({len(wartend)})")
    if not wartend:
        st.info(
            "Gerade wartet keine Bewerbung auf dich. "
            + (f"Der nächste automatische Lauf ist am **{zeitplan.beschreibe(naechster)}**. " if naechster else "")
            + "Du kannst auch jetzt suchen oder Stellen selbst auswählen."
        )
    else:
        st.caption("Prüfe Anschreiben und Versandweg. **Ja** schickt die Bewerbung sofort ab, "
                   "**Nein** blendet die Stelle dauerhaft aus.")
        for v in reversed(wartend):  # aelteste zuerst
            ui.freigabe_karte(db, profil, v, praefix="cockpit")

    # ------------------------------------------------------------------ Hilfe bei Fehlschlaegen
    if fehlgeschlagen:
        st.subheader(f"⚠️ Braucht deine Hilfe ({len(fehlgeschlagen)})")
        st.caption("Diese Bewerbungen konnten nicht automatisch verschickt werden.")
        for v in fehlgeschlagen:
            ui.hilfe_karte(db, v, praefix="cockpit")

    # ------------------------------------------------------------------ Zuletzt verschickt
    zuletzt = db.vorgaenge(("abgeschickt", "selbst_beworben"))[:5]
    if zuletzt:
        st.subheader("✅ Zuletzt beworben")
        for v in zuletzt:
            job = db.job(v.ref)
            if job:
                st.markdown(
                    f"- **{job.titel}** – {job.arbeitgeber} · {ui.KANAL_TEXT.get(v.kanal, v.kanal)} · "
                    f"{ui.zeit(v.gesendet_am)}"
                    + (f" · {ui.RUECKMELDUNG_TEXT[v.rueckmeldung]}" if v.rueckmeldung else "")
                )
        st.page_link("seiten/bewerbungen.py", label="Alle Bewerbungen & Rückmeldungen", icon="📬")
