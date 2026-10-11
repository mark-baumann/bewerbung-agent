from datetime import datetime, timezone

import pytest

from bewerbungsagent import zeitplan as z


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_einfacher_tagestermin_in_deutscher_zeit():
    # 11.10.2026 ist Sommerzeit (UTC+2): 06:00 Berlin = 04:00 UTC
    assert z.passt("0 6 * * *", utc(2026, 10, 11, 4, 0))
    assert not z.passt("0 6 * * *", utc(2026, 10, 11, 6, 0))


def test_winterzeit_wird_beruecksichtigt():
    # 01.12.2026 ist Winterzeit (UTC+1): 06:00 Berlin = 05:00 UTC
    assert z.passt("0 6 * * *", utc(2026, 12, 1, 5, 0))


def test_listen_bereiche_und_schritte():
    plan = z.parse("*/15 8-18/2 * * 1-5")
    assert plan.minuten == {0, 15, 30, 45}
    assert plan.stunden == {8, 10, 12, 14, 16, 18}
    assert plan.wochentage == {1, 2, 3, 4, 5}
    assert z.parse("0 6,14 * * *").stunden == {6, 14}


def test_sonntag_als_0_und_7():
    # 11.10.2026 ist ein Sonntag
    assert z.passt("0 6 * * 0", utc(2026, 10, 11, 4, 0))
    assert z.passt("0 6 * * 7", utc(2026, 10, 11, 4, 0))
    assert not z.passt("0 6 * * 1-5", utc(2026, 10, 11, 4, 0))


def test_tag_und_wochentag_verhalten_sich_wie_cron():
    # Tag 1 ODER Montag
    plan = "0 6 1 * 1"
    assert z.passt(plan, utc(2026, 10, 1, 4, 0))   # Donnerstag, 1.
    assert z.passt(plan, utc(2026, 10, 12, 4, 0))  # Montag
    assert not z.passt(plan, utc(2026, 10, 13, 4, 0))


@pytest.mark.parametrize("ausdruck", ["", "0 6 * *", "60 6 * * *", "0 25 * * *", "a b c d e", "5-1 * * * *"])
def test_ungueltige_ausdruecke(ausdruck):
    assert z.pruefe(ausdruck) is not None
    with pytest.raises(z.ZeitplanFehler):
        z.parse(ausdruck)


def test_faellig_seit_letztem_lauf():
    plan = "0 6 * * *"
    jetzt = utc(2026, 10, 11, 4, 3)
    assert z.faellig(plan, utc(2026, 10, 10, 4, 0), jetzt)
    # Schon nach dem heutigen Termin gelaufen -> nicht erneut
    assert not z.faellig(plan, utc(2026, 10, 11, 4, 1), jetzt)


def test_faellig_ohne_letzten_lauf_holt_keine_alten_termine_nach():
    plan = "0 6 * * *"
    assert z.faellig(plan, None, utc(2026, 10, 11, 4, 5))
    assert not z.faellig(plan, None, utc(2026, 10, 11, 9, 0))


def test_naechster_termin():
    n = z.naechster_termin("0 7 * * 1-5", utc(2026, 10, 10, 8, 0))  # Samstag
    assert (n.weekday(), n.hour, n.minute) == (0, 7, 0)
    assert z.beschreibe(n) == "Mo 12.10. 07:00"


def test_naechster_termin_ueber_zeitumstellung():
    # Umstellung auf Winterzeit am 25.10.2026
    n = z.naechster_termin("30 2 * * *", utc(2026, 10, 24, 12, 0))
    assert (n.day, n.hour, n.minute) == (25, 2, 30)


def test_als_datetime_liest_beide_formate():
    assert z.als_datetime("2026-10-11T08:00:00+00:00") == utc(2026, 10, 11, 8, 0)
    assert z.als_datetime("2026-10-11 08:00:00") == utc(2026, 10, 11, 8, 0)
    assert z.als_datetime(None) is None
