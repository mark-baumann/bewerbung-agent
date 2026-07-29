"""Sentimentanalyse deutscher Stellenanzeigen.

Kein allgemeines Sprach-Sentiment, sondern domaenenspezifisch: bewertet die
Tonalitaet und die impliziten Arbeitsbedingungen einer Anzeige. Ein Text kann
sprachlich euphorisch sein ("junges, dynamisches Team, wir suchen Macher!")
und trotzdem negativ bewertet werden, weil diese Formulierungen empirisch mit
Ueberstunden, Fluktuation und Altersdiskriminierung korrelieren.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Gewichte: wie stark ein Signal den Sentimentwert verschiebt.
GRUEN: dict[str, float] = {
    r"unbefristet": 3.0,
    r"work[- ]life[- ]balance": 2.5,
    r"tarif(vertrag|lich|gebunden)": 2.5,
    r"gleitzeit|vertrauensarbeitszeit": 2.0,
    r"30 tage urlaub|30 urlaubstage|mehr als 30 tage": 2.0,
    r"weiterbildung|fortbildung|schulungsbudget|lernbudget": 2.0,
    r"home[- ]?office|remote|mobiles arbeiten|hybrid": 2.0,
    r"betriebliche altersvorsorge|bav": 1.5,
    r"betriebsrat|mitbestimmung": 1.5,
    r"onboarding|einarbeitung|mentor|patenprogramm": 1.5,
    r"4[- ]tage[- ]woche|viertagewoche": 3.0,
    r"gehaltsspanne|gehalt.{0,20}(eur|€|\d{2}\.\d{3})|verg[uü]tung.{0,20}\d": 2.5,
    r"jobticket|deutschlandticket|jobrad|fahrradleasing": 1.0,
    r"eltern(zeit|freundlich)|kinderbetreuung|familienfreundlich": 1.5,
    r"diversit|inklusion|schwerbehinder": 1.0,
    r"sabbatical|zeitkonto|[uü]berstundenausgleich": 1.5,
    r"planbare arbeitszeiten|keine [uü]berstunden": 2.0,
}

ROT: dict[str, float] = {
    r"junges?,? dynamisches? team": 2.5,
    r"belastbar(keit)?|stressresistent|hohe? einsatzbereitschaft": 3.0,
    r"hands[- ]on[- ]mentalit": 2.0,
    r"[uü]berstunden|mehrarbeit erforderlich": 2.5,
    r"wie eine familie|wir sind eine familie|familienunternehmen mit herz": 2.0,
    r"macher(typ|mentalit)|anpacker|[aä]rmel hochkrempeln": 2.0,
    r"eigenverantwortliches arbeiten in einem schnelllebigen": 1.5,
    r"rockstar|ninja|guru|allrounder f[uü]r alles": 2.0,
    r"leistungsorientierte? (bezahlung|verg[uü]tung)|provision": 1.5,
    r"reiseberei(tschaft|t).{0,30}(100|80|hoch)": 2.0,
    r"befristet(er)? (vertrag|arbeitsvertrag)|zunaechst befristet": 2.0,
    r"probezeit von (9|neun|12|zw[oö]lf)": 1.5,
    r"bewerbungen? (nur )?[uü]ber unser portal.{0,40}pflicht": 0.5,
    r"quereinsteiger willkommen.{0,60}(provision|erfolgsbeteiligung)": 2.5,
    r"praktikum|werkstudent|ausbildung": 1.0,
    r"zeitarbeit|arbeitnehmer[uü]berlassung|personaldienstleist": 2.5,
    r"wachse[nd].{0,20}[uü]ber sich hinaus|grenzen verschieben": 1.5,
    r"unbezahlt|auf provisionsbasis|selbst[aä]ndig(er)? basis": 3.0,
}


@dataclass
class SentimentErgebnis:
    wert: float  # 0..100, 50 = neutral
    gruen: list[str] = field(default_factory=list)
    rot: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.wert >= 70:
            return "positiv"
        if self.wert >= 55:
            return "eher positiv"
        if self.wert > 45:
            return "neutral"
        if self.wert > 30:
            return "eher kritisch"
        return "kritisch"


def _treffer(text: str, muster: dict[str, float]) -> tuple[float, list[str]]:
    punkte = 0.0
    gefunden: list[str] = []
    for regex, gewicht in muster.items():
        m = re.search(regex, text, flags=re.IGNORECASE)
        if m:
            punkte += gewicht
            gefunden.append(m.group(0).strip().lower())
    return punkte, gefunden


def analysiere(text: str | None) -> SentimentErgebnis:
    """Bewertet den Ton einer Anzeige auf einer Skala von 0 (kritisch) bis 100."""
    if not text or len(text.strip()) < 40:
        return SentimentErgebnis(wert=50.0)

    plus, gruen = _treffer(text, GRUEN)
    minus, rot = _treffer(text, ROT)

    # Differenz um 50 zentrieren, mit saettigender Kennlinie statt harter Kappung:
    # jeder weitere Treffer wirkt weniger als der vorherige.
    differenz = plus - minus
    wert = 50.0 + 45.0 * (differenz / (abs(differenz) + 6.0))
    return SentimentErgebnis(wert=round(max(0.0, min(100.0, wert)), 1), gruen=gruen, rot=rot)
