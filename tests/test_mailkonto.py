"""E-Mail-Konto mit Adresse + Passwort verbinden (ohne Netz)."""

from __future__ import annotations

import os
import smtplib

import httpx
import pytest

from bewerbungsagent import zugangsdaten
from bewerbungsagent.bewerben import mailkonto
from bewerbungsagent.bewerben.mailkonto import Server

@pytest.fixture(autouse=True)
def _ohne_zugangsdaten(tmp_path, monkeypatch):
    for name in zugangsdaten.NAMEN:
        monkeypatch.delenv(name, raising=False)
    yield
    zugangsdaten.anwenden(tmp_path / "gibt-es-nicht.json")


ONE_COM_XML = """<clientConfig version="1.1"><emailProvider id="one.com">
<displayName>one.com</displayName>
<incomingServer type="imap"><hostname>imap.one.com</hostname><port>993</port><socketType>SSL</socketType></incomingServer>
<outgoingServer type="smtp"><hostname>send.one.com</hostname><port>465</port><socketType>SSL</socketType></outgoingServer>
</emailProvider></clientConfig>"""


def test_bekannte_anbieter_ohne_netz():
    k = mailkonto.erkenne("Max@Web.de", transport=httpx.MockTransport(lambda r: pytest.fail("kein Netz noetig")))
    assert k.adresse == "max@web.de" and k.anbieter == "WEB.DE" and k.quelle == "bekannt"
    assert k.smtp[0] == Server("smtp.web.de", 587) and k.imap[0] == Server("imap.web.de", 993)
    with pytest.raises(ValueError):
        mailkonto.erkenne("keine-adresse")


def test_eigene_domain_wird_ueber_mx_als_one_com_erkannt():
    def antwort(request: httpx.Request) -> httpx.Response:
        if request.url.host == "dns.google":
            return httpx.Response(200, json={"Answer": [
                {"type": 15, "data": "10 mx2.pub.mailpod10-cph3.one.com."},
                {"type": 15, "data": "10 mx1.pub.mailpod10-cph3.one.com."}]})
        return httpx.Response(404)

    k = mailkonto.erkenne("kontakt@markb.de", transport=httpx.MockTransport(antwort))
    assert k.quelle == "mx" and k.anbieter.startswith("one.com")
    assert [s.text for s in k.smtp] == ["send.one.com:465", "send.one.com:587"]
    assert [s.text for s in k.imap] == ["imap.one.com:993"]


def test_thunderbird_autoconfig_wird_genutzt():
    def antwort(request: httpx.Request) -> httpx.Response:
        if request.url.host == "autoconfig.thunderbird.net" and request.url.path.endswith("/beispiel.de"):
            return httpx.Response(200, text=ONE_COM_XML)
        return httpx.Response(404)

    k = mailkonto.erkenne("a@beispiel.de", transport=httpx.MockTransport(antwort))
    assert k.quelle == "thunderbird" and k.smtp == [Server("send.one.com", 465)]


def test_ohne_treffer_wird_vermutet():
    k = mailkonto.erkenne("a@unbekannt-xyz.de", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert k.quelle == "vermutung" and k.smtp[0].host == "smtp.unbekannt-xyz.de" and k.hinweis


def _konto():
    return mailkonto.Konto("kontakt@markb.de", "one.com", [Server("send.one.com", 465), Server("send.one.com", 587)],
                           [Server("imap.one.com", 993)], "mx")


def test_verbinden_probiert_ports_und_speichert(tmp_path, monkeypatch):
    monkeypatch.setattr(zugangsdaten, "DATEI", tmp_path / "zugangsdaten.json")
    versuche = []

    def smtp(server, adresse, passwort):
        versuche.append(server.port)
        if server.port == 465:
            raise TimeoutError("timed out")

    e = mailkonto.verbinde_konto(_konto(), "geheim", smtp_login=smtp, imap_login=lambda *a: None)
    assert e.smtp_ok and e.imap_ok and versuche == [465, 587]
    gespeichert = zugangsdaten.lade(tmp_path / "zugangsdaten.json")
    assert gespeichert["SMTP_HOST"] == "send.one.com" and gespeichert["SMTP_PORT"] == "587"
    assert gespeichert["SMTP_BENUTZER"] == gespeichert["SMTP_ABSENDER"] == "kontakt@markb.de"
    assert gespeichert["IMAP_HOST"] == "imap.one.com" and "IMAP_PASSWORT" not in gespeichert
    assert os.environ["SMTP_HOST"] == "send.one.com"


def test_falsches_passwort_wird_klar_gemeldet_und_nicht_gespeichert(tmp_path, monkeypatch):
    monkeypatch.setattr(zugangsdaten, "DATEI", tmp_path / "zugangsdaten.json")
    versuche = []

    def smtp(server, adresse, passwort):
        versuche.append(server.port)
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

    e = mailkonto.verbinde_konto(_konto(), "falsch", smtp_login=smtp, imap_login=lambda *a: None)
    assert not e.gespeichert and versuche == [465]  # falsches Passwort: kein zweiter Port
    assert any("Passwort" in m for m in e.meldungen)
    assert zugangsdaten.lade(tmp_path / "zugangsdaten.json") == {}
    with pytest.raises(ValueError):
        mailkonto.verbinde_konto(_konto(), "")
