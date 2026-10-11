"""Geführter manueller Prozess: Suchen → Auswählen → Prüfen & freigeben → Status."""

import streamlit as st

from bewerbungsagent import ablauf, ui, vorschlaege
from bewerbungsagent.config import ermittle_profil_pfad, speichere_profil
from bewerbungsagent.models import Vorgang
from bewerbungsagent.scoring import llm as llm_modul

SCHRITTE = {1: "🔍 Suchen", 2: "☑️ Auswählen", 3: "✉️ Prüfen & freigeben", 4: "📤 Status"}
TAGE = [7, 14, 30, 100]

profil = ui.profil_laden()
if profil is None:
    st.warning("Bitte zuerst das Profil anlegen.")
    st.page_link("seiten/profil.py", label="Profil einrichten", icon="👤")
    st.stop()

s = st.session_state
s.setdefault("sfs_schritt", 1)
# Streamlit verwirft Widget-Zustand, sobald ein Widget nicht gerendert wird
# (z. B. das Suchformular in Schritt 2). Erneutes Zuweisen haelt die Werte.
for _schluessel in ("sfs_begriffe", "sfs_wo", "sfs_umkreis", "sfs_tage", "sfs_max", "sfs_vollzeit",
                    "sfs_quellen", "sfs_min", "sfs_umfang", "sfs_profil_merken"):
    if _schluessel in s:
        s[_schluessel] = s[_schluessel]


def gehe_zu(schritt: int) -> None:
    st.session_state["sfs_schritt"] = schritt


# --------------------------------------------------------------------------- #
# Suchformular vorbelegen (letzte Suche > Profil)
# --------------------------------------------------------------------------- #

with ui.oeffne_db() as db:
    letzte_suche = db.letzte_suche()
if "sfs_begriffe" not in s:
    lp = (letzte_suche or {}).get("parameter", {})
    p = profil.suche
    s["sfs_begriffe"] = list(lp.get("begriffe") or p.was)
    s["sfs_wo"] = lp.get("wo", p.wo or "")
    s["sfs_umkreis"] = int(min(200, max(0, lp.get("umkreis", p.umkreis))))
    tage = int(lp.get("tage", p.veroeffentlicht_seit_tagen))
    s["sfs_tage"] = min(TAGE, key=lambda t: abs(t - tage))
    s["sfs_max"] = int(min(300, max(5, lp.get("max_treffer", p.max_pro_query))))
    s["sfs_vollzeit"] = bool(lp.get("nur_vollzeit", p.nur_vollzeit))
    s["sfs_quellen"] = list(lp.get("quellen") or p.quellen)
s.setdefault("sfs_min", int(profil.bewertung.min_score))


# --------------------------------------------------------------------------- #
# Fortschrittsleiste
# --------------------------------------------------------------------------- #

st.title("🧭 Schritt für Schritt zur Bewerbung")
with ui.oeffne_db() as db:
    anzahl = {
        2: len(db.kandidaten(min_score=s.get("sfs_min", profil.bewertung.min_score), limit=200,
                             ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)),
        3: len(db.vorgaenge(("vorbereitung", "wartet"))),
        4: len(db.vorgaenge(("freigegeben", "wird_gesendet", "fehlgeschlagen"))),
    }
spalten = st.columns(len(SCHRITTE))
for nummer, name in SCHRITTE.items():
    label = f"{nummer}. {name}"
    if anzahl.get(nummer):
        label += f" ({anzahl[nummer]})"
    spalten[nummer - 1].button(
        label, key=f"sfs_nav_{nummer}", width="stretch", on_click=gehe_zu, args=(nummer,),
        type="primary" if nummer == s["sfs_schritt"] else "secondary",
    )
st.progress((s["sfs_schritt"] - 1) / (len(SCHRITTE) - 1))
schritt = s["sfs_schritt"]


# --------------------------------------------------------------------------- #
# 1 · Suchen
# --------------------------------------------------------------------------- #

def vorschlag_hinzufuegen() -> None:
    wahl = st.session_state.get("sfs_vorschlag")
    if wahl and wahl not in st.session_state["sfs_begriffe"]:
        st.session_state["sfs_begriffe"] = [*st.session_state["sfs_begriffe"], wahl]
    st.session_state["sfs_vorschlag"] = None


def tipp_uebernehmen(aenderung: dict) -> None:
    zuordnung = {"umkreis": "sfs_umkreis", "tage": "sfs_tage", "nur_vollzeit": "sfs_vollzeit", "quellen": "sfs_quellen"}
    for feld, wert in aenderung.items():
        if feld in zuordnung:
            st.session_state[zuordnung[feld]] = wert


def ki_vorschlaege_holen() -> None:
    with ui.oeffne_db() as db_:
        gute = [j.titel for j, _ in db_.bestenliste(min_score=profil.bewertung.min_score, limit=20)]
    try:
        st.session_state["sfs_ki"] = vorschlaege.per_llm(profil, gute)
        st.session_state.pop("sfs_ki_fehler", None)
    except Exception as e:
        st.session_state["sfs_ki_fehler"] = str(e)


def suche_starten() -> None:
    begriffe = [b.strip() for b in s["sfs_begriffe"] if b.strip()]
    if not begriffe:
        st.error("Bitte mindestens einen Suchbegriff angeben.")
        return
    if s.get("sfs_profil_merken"):
        profil.suche.was = begriffe
        profil.suche.wo = s["sfs_wo"].strip()
        profil.suche.umkreis = int(s["sfs_umkreis"])
        profil.suche.veroeffentlicht_seit_tagen = int(s["sfs_tage"])
        profil.suche.max_pro_query = int(s["sfs_max"])
        profil.suche.nur_vollzeit = bool(s["sfs_vollzeit"])
        profil.suche.quellen = list(s["sfs_quellen"]) or ["arbeitsagentur"]
        speichere_profil(profil, ermittle_profil_pfad())
    with st.status("Suche läuft …", expanded=True) as status, ui.oeffne_db() as db_:
        balken = st.progress(0.0)
        zeile = st.empty()

        def fortschritt(meldung: str, anteil: float) -> None:
            zeile.write(meldung)
            balken.progress(min(1.0, max(0.0, anteil)))

        ergebnis = ablauf.suche_ausfuehren(
            db_, profil, begriffe=begriffe, wo=s["sfs_wo"].strip(), umkreis=int(s["sfs_umkreis"]),
            tage=int(s["sfs_tage"]), max_treffer=int(s["sfs_max"]), nur_vollzeit=bool(s["sfs_vollzeit"]),
            quellen=list(s["sfs_quellen"]) or ["arbeitsagentur"], fortschritt=fortschritt,
        )
        status.update(label=f"{len(ergebnis.refs)} Stellen gefunden – bewerte …")
        mit_llm = llm_modul.client_verfuegbar()
        scores = ablauf.bewerte_neue(db_, profil, refs=ergebnis.refs, mit_llm=mit_llm, fortschritt=fortschritt)
        passend = sum(1 for sc in scores if not sc.ausgeschlossen and sc.gesamt >= s["sfs_min"])
        status.update(label="Fertig", state="complete")
    s["sfs_meldung"] = (
        f"✅ {len(ergebnis.refs)} Stellen gefunden ({ergebnis.neu} neu), {len(scores)} neu bewertet"
        f"{' mit KI' if mit_llm else ' per Stichworten'}, davon {passend} passend."
        + (f" ⚠️ {len(ergebnis.fehler)} Abfrage(n) fehlgeschlagen: " + "; ".join(ergebnis.fehler[:3])
           if ergebnis.fehler else "")
    )
    s["sfs_umfang"] = "letzte"
    s["sfs_schritt"] = 2
    st.rerun()


if schritt == 1:
    links, rechts = st.columns([3, 2], gap="large")
    with ui.oeffne_db() as db:
        aehnliche = vorschlaege.aus_treffern(db, profil)
        tipps = vorschlaege.such_tipps(db, profil)
        statistik = db.suchbegriff_statistik(profil.bewertung.min_score)
    ki = [v for v in s.get("sfs_ki", []) if v.begriff not in s["sfs_begriffe"]]
    alle_vorschlaege = [v for v in [*aehnliche, *ki] if v.begriff not in s["sfs_begriffe"]]

    with links:
        st.subheader("Wonach suchst du?")
        st.caption("Mehrere Jobtitel erhöhen die Trefferzahl. Nach der Suche wird automatisch bewertet, "
                   "wie gut jede Stelle zu dir passt.")
        optionen = list(dict.fromkeys([*s["sfs_begriffe"], *profil.suche.was, *(v.begriff for v in alle_vorschlaege)]))
        st.multiselect("Suchbegriffe", optionen, key="sfs_begriffe", accept_new_options=True,
                       placeholder="Jobtitel eintippen und Enter drücken")
        c1, c2 = st.columns(2)
        c1.text_input("Ort", key="sfs_wo", help="Leer lassen für ganz Deutschland")
        c2.slider("Umkreis (km)", 0, 200, step=5, key="sfs_umkreis")
        c3, c4 = st.columns(2)
        c3.select_slider("Veröffentlicht in den letzten …", TAGE, key="sfs_tage", format_func=lambda t: f"{t} Tagen")
        c4.number_input("Max. Treffer je Begriff und Jobbörse", 5, 300, step=5, key="sfs_max")
        c5, c6 = st.columns(2)
        c5.multiselect("Jobbörsen", ["arbeitsagentur", "get-in-it"], key="sfs_quellen",
                       format_func=lambda q: ui.QUELLE_TEXT.get(q, q))
        c6.checkbox("Nur Vollzeit", key="sfs_vollzeit")
        st.checkbox("Diese Einstellungen auch für die Automatik übernehmen", key="sfs_profil_merken",
                    help="Speichert Suchbegriffe, Ort und Filter im Profil.")
        if st.button("🔍 Suchen & bewerten", type="primary", width="stretch"):
            suche_starten()

    with rechts:
        st.subheader("💡 Ideen für deine Suche")
        for i, tipp in enumerate(tipps):
            with st.container(border=True):
                st.markdown(tipp.text)
                if tipp.aenderung:
                    st.button("Übernehmen", key=f"sfs_tipp_{i}", on_click=tipp_uebernehmen, args=(tipp.aenderung,))
        if alle_vorschlaege:
            st.markdown("**Ähnliche Suchbegriffe** – antippen zum Hinzufügen")
            st.pills("Vorschläge", [v.begriff for v in alle_vorschlaege], key="sfs_vorschlag",
                     on_change=vorschlag_hinzufuegen, label_visibility="collapsed")
            with st.expander("Warum diese Vorschläge?"):
                for v in alle_vorschlaege:
                    st.markdown(f"- **{v.begriff}** – {v.grund} ({'KI' if v.quelle == 'ki' else 'aus deinen Treffern'})")
        elif not tipps:
            st.caption("Nach den ersten Suchen erscheinen hier ähnliche Jobtitel aus gut passenden Stellen.")
        st.button("✨ Weitere Ideen von der KI", on_click=ki_vorschlaege_holen,
                  disabled=not llm_modul.client_verfuegbar(),
                  help="Synonyme, Nachbarrollen und englische Titel aus Profil und Lebenslauf"
                  if llm_modul.client_verfuegbar() else "Dafür wird ein LLM-API-Key benötigt.")
        if s.get("sfs_ki_fehler"):
            st.warning(f"KI-Vorschläge nicht verfügbar: {s['sfs_ki_fehler']}")
        if statistik:
            st.markdown("**Was bringen deine Suchbegriffe?**")
            st.dataframe(
                [{"Suchbegriff": z["begriff"], "Treffer": z["treffer"], "passend": z["passend"],
                  "beworben": z["beworben"], "zuletzt": z["letzte_treffer"]} for z in statistik],
                hide_index=True, width="stretch",
            )


# --------------------------------------------------------------------------- #
# 2 · Auswählen
# --------------------------------------------------------------------------- #

def nicht_interessant(ref: str) -> None:
    with ui.oeffne_db() as db_:
        ablauf.ablehnen(db_, profil, ref, grund="nicht interessant")


def alle_markieren(refs: list[str], wert: bool) -> None:
    for ref in refs:
        st.session_state[f"sfs_sel_{ref}"] = wert


def vorbereiten_starten(refs: list[str], probelauf: bool) -> None:
    with ui.oeffne_db() as db_:
        for ref in refs:
            db_.setze_vorgang(Vorgang(ref=ref, status="vorbereitung", herkunft="manuell"))
    argumente = ["vorbereiten", *refs, "--ausloeser", "knopf"]
    if not probelauf:
        argumente.append("--ohne-probelauf")
    ui.starte_hintergrund(*argumente, meldung=f"{len(refs)} Bewerbung(en) werden vorbereitet …")
    for ref in refs:
        st.session_state.pop(f"sfs_sel_{ref}", None)
    st.session_state["sfs_schritt"] = 3


def jetzt_bewerten(refs: list[str]) -> None:
    with ui.oeffne_db() as db_:
        ablauf.bewerte_neue(db_, profil, refs=refs, mit_llm=llm_modul.client_verfuegbar())


if schritt == 2:
    if s.get("sfs_meldung"):
        st.success(s["sfs_meldung"])
    st.subheader("Welche Stellen willst du?")
    st.caption("Hake die Stellen an, auf die du dich bewerben möchtest. Für jede wird im nächsten Schritt "
               "ein Anschreiben erstellt – abgeschickt wird erst nach deinem Ja.")
    f1, f2 = st.columns([2, 3])
    s.setdefault("sfs_umfang", "letzte" if letzte_suche else "alle")
    f1.segmented_control("Stellen aus", ["letzte", "alle"], key="sfs_umfang",
                         format_func={"letzte": "letzter Suche", "alle": "allen Suchen"}.get)
    f2.slider("Mindest-Passung", 0, 100, step=5, key="sfs_min")

    with ui.oeffne_db() as db:
        refs = letzte_suche["refs"] if s.get("sfs_umfang") == "letzte" and letzte_suche else None
        kandidaten = db.kandidaten(min_score=s["sfs_min"], limit=100, refs=refs,
                                   ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)
        unbewertet = [r for r in (refs or []) if db.score(r) is None]
        if unbewertet:
            st.info(f"{len(unbewertet)} Stellen der letzten Suche sind noch nicht bewertet.")
            st.button("📊 Jetzt bewerten", on_click=jetzt_bewerten, args=(unbewertet,))
        if not kandidaten:
            schwaechere = db.kandidaten(min_score=0, limit=200, refs=refs,
                                        ausschluss_arbeitgeber=profil.bewertung.ausschluss_arbeitgeber)
            wartend = len(db.vorgaenge(("vorbereitung", "wartet")))
            text = f"Keine offenen Stellen mit mindestens {s['sfs_min']} Punkten."
            if wartend:
                text += f" {wartend} Bewerbung(en) warten bereits in Schritt 3 auf dich."
            st.info(text)
            b1, b2, b3 = st.columns(3)
            if schwaechere:
                # So weit senken, dass etwa zehn Stellen zur Auswahl stehen
                zehnte = schwaechere[min(9, len(schwaechere) - 1)][1].gesamt
                neu_min = max(0, int(zehnte // 5) * 5)
                anzahl_neu = sum(1 for _, sc in schwaechere if sc.gesamt >= neu_min)
                b1.button(f"Auf {neu_min} Punkte senken ({anzahl_neu} Stellen)",
                          on_click=lambda: st.session_state.update(sfs_min=neu_min), width="stretch")
            b2.button("← Neue Suche", on_click=gehe_zu, args=(1,), width="stretch")
            if wartend:
                b3.button("Zu Schritt 3 →", on_click=gehe_zu, args=(3,), width="stretch", type="primary")
        else:
            alle_refs = [j.ref for j, _ in kandidaten]
            m1, m2, _ = st.columns([1, 1, 4])
            m1.button("Alle auswählen", on_click=alle_markieren, args=(alle_refs, True), width="stretch")
            m2.button("Keine", on_click=alle_markieren, args=(alle_refs, False), width="stretch")
            for job, score in kandidaten:
                with st.container(border=True):
                    auswahl, inhalt = st.columns([1, 14], vertical_alignment="top")
                    auswahl.checkbox("Bewerben", key=f"sfs_sel_{job.ref}", label_visibility="collapsed")
                    with inhalt:
                        ui.job_kopf(job, score, ueberschrift="#####")
                        ui.score_gruende(score, max_treffer=5)
                        d1, d2, d3 = st.columns([2, 2, 3])
                        with d1.popover("📄 Anzeige lesen", width="stretch"):
                            st.markdown(f"**{job.titel}** – {job.arbeitgeber}")
                            st.text((job.beschreibung or "Keine Beschreibung geladen.")[:6000])
                        if job.anzeige_url:
                            d2.link_button("🔗 Original", job.anzeige_url, width="stretch")
                        d3.button("👎 Nicht interessant", key=f"sfs_weg_{job.ref}", on_click=nicht_interessant,
                                  args=(job.ref,), width="stretch")

            gewaehlt = [r for r in alle_refs if s.get(f"sfs_sel_{r}")]
            st.divider()
            probelauf = st.checkbox(
                "Online-Formulare vorab testweise ausfüllen (Probelauf)",
                value=profil.automatisierung.probelauf_vorab and ablauf.browser_verfuegbar(),
                disabled=not ablauf.browser_verfuegbar(),
                help="Dauert pro Stelle 1–3 Minuten. Du siehst dann vor dem Ja, ob das Formular klappt.",
            )
            st.button(
                f"Weiter: {len(gewaehlt)} Bewerbung(en) vorbereiten →", type="primary", width="stretch",
                disabled=not gewaehlt, on_click=vorbereiten_starten, args=(gewaehlt, probelauf),
            )


# --------------------------------------------------------------------------- #
# 3 · Prüfen & freigeben
# --------------------------------------------------------------------------- #

if schritt == 3:
    st.subheader("Prüfen & freigeben")
    st.caption("Lies jedes Anschreiben kurz gegen. **Ja** schickt die Bewerbung ab, **Nein** verwirft sie.")

    @st.fragment(run_every=5)
    def vorbereitung_beobachten():
        with ui.oeffne_db() as db_:
            in_arbeit = db_.vorgaenge("vorbereitung")
            if in_arbeit:
                st.info(f"⏳ {len(in_arbeit)} Bewerbung(en) werden vorbereitet – Anschreiben"
                        f"{' und Probelauf' if profil.automatisierung.probelauf_vorab else ''} …", icon="⏳")
                for v in in_arbeit:
                    job = db_.job(v.ref)
                    if job:
                        st.caption(f"• {job.titel} – {job.arbeitgeber}")
                st.session_state["sfs_bereitet_vor"] = True
            elif st.session_state.pop("sfs_bereitet_vor", False):
                st.rerun()

    with ui.oeffne_db() as db:
        if db.vorgaenge("vorbereitung"):
            vorbereitung_beobachten()
            with st.expander("Protokoll des Hintergrundprozesses"):
                st.code(ui.hintergrund.letzte_logzeilen() or "(noch keine Ausgabe)", language=None)
        wartend = db.vorgaenge("wartet")
        if not wartend and not db.vorgaenge("vorbereitung"):
            st.info("Nichts zu prüfen. Wähle im Schritt 2 Stellen aus.")
            st.button("← Stellen auswählen", on_click=gehe_zu, args=(2,))
        for v in reversed(wartend):
            ui.freigabe_karte(db, profil, v, praefix="sfs")
    if wartend:
        st.button("Weiter zum Status →", on_click=gehe_zu, args=(4,), width="stretch")


# --------------------------------------------------------------------------- #
# 4 · Status
# --------------------------------------------------------------------------- #

if schritt == 4:
    st.subheader("Was ist unterwegs?")

    @st.fragment(run_every=5)
    def versand_beobachten():
        with ui.oeffne_db() as db_:
            unterwegs = db_.vorgaenge(("freigegeben", "wird_gesendet"))
            if unterwegs:
                for v in unterwegs:
                    job = db_.job(v.ref)
                    if job:
                        st.info(f"{ui.STATUS_TEXT[v.status]}: **{job.titel}** – {job.arbeitgeber}"
                                + (f"  \n{v.hinweis}" if v.hinweis else ""), icon="📤")
                st.session_state["sfs_sendet"] = True
            elif st.session_state.pop("sfs_sendet", False):
                st.rerun()
            else:
                st.caption("Gerade wird nichts verschickt.")

    versand_beobachten()

    with ui.oeffne_db() as db:
        heute = [v for v in db.vorgaenge(("abgeschickt", "selbst_beworben"))
                 if (v.gesendet_am or "") >= ablauf.tagesbeginn_utc()]
        if heute:
            st.success(f"🎉 Heute {len(heute)} Bewerbung(en) verschickt – stark!")
            for v in heute:
                job = db.job(v.ref)
                if job:
                    st.markdown(f"- **{job.titel}** – {job.arbeitgeber} · {ui.KANAL_TEXT.get(v.kanal, v.kanal)}"
                                + (f" · {v.hinweis}" if v.hinweis and v.kanal == "email" else ""))
        fehlgeschlagen = db.vorgaenge("fehlgeschlagen")
        if fehlgeschlagen:
            st.subheader("⚠️ Braucht deine Hilfe")
            for v in fehlgeschlagen:
                ui.hilfe_karte(db, v, praefix="sfs")

    st.divider()
    n1, n2 = st.columns(2)
    n1.button("🔍 Nächste Suche starten", on_click=gehe_zu, args=(1,), width="stretch")
    n2.page_link("seiten/bewerbungen.py", label="Alle Bewerbungen & Rückmeldungen", icon="📬")
