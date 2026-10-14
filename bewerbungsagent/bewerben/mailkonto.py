"""E-Mail-Konto mit nur Adresse und Passwort verbinden.

Die Server fuer Versand (SMTP) und Empfang (IMAP) werden automatisch ermittelt:

1. eingebaute Liste verbreiteter Anbieter (web.de, GMX, T-Online, Gmail, one.com ...),
2. die Anbieter-Datenbank von Thunderbird (autoconfig.thunderbird.net),
3. die Autokonfiguration der eigenen Domain (autoconfig.<domain>),
4. der Mailserver (MX-Eintrag) der Domain - so wird z. B. kontakt@markb.de als
   one.com-Postfach erkannt, obwohl die Domain eine eigene ist,
5. sonst eine begruendete Vermutung (smtp.<domain> / imap.<domain>).

`verbinde_konto` testet beide Anmeldungen und speichert die Zugangsdaten nur,
wenn der Versand funktioniert - in `daten/zugangsdaten.json` (nie im Git).
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Server:
    host: str
    port: int

    @property
    def text(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class Konto:
    adresse: str
    anbieter: str
    smtp: list[Server]                  # Kandidaten in Reihenfolge (z. B. 465, dann 587)
    imap: list[Server]
    quelle: str                         # bekannt | thunderbird | autoconfig | mx | vermutung
    hinweis: str = ""                   # z. B. "App-Passwort noetig"

    @property
    def domain(self) -> str:
        return self.adresse.rsplit("@", 1)[-1].lower()


def _s(host: str, *ports: int) -> list[Server]:
    return [Server(host, p) for p in ports]


APP_PASSWORT = ("Dieser Anbieter verlangt für fremde Programme ein App-Passwort "
                "(in den Sicherheitseinstellungen deines Kontos erzeugen) – das normale Passwort wird abgelehnt.")

# Domain des Postfachs bzw. des Mailservers -> (Anbieter, SMTP, IMAP, Hinweis)
BEKANNT: dict[str, tuple[str, list[Server], list[Server], str]] = {
    "one.com": ("one.com", _s("send.one.com", 465, 587), _s("imap.one.com", 993), ""),
    "web.de": ("WEB.DE", _s("smtp.web.de", 587, 465), _s("imap.web.de", 993),
               "Bei WEB.DE muss „POP3/IMAP-Abruf“ in den Einstellungen des Postfachs aktiviert sein."),
    "gmx.de": ("GMX", _s("mail.gmx.net", 587, 465), _s("imap.gmx.net", 993),
               "Bei GMX muss „POP3/IMAP-Abruf“ in den Einstellungen des Postfachs aktiviert sein."),
    "gmx.net": ("GMX", _s("mail.gmx.net", 587, 465), _s("imap.gmx.net", 993),
                "Bei GMX muss „POP3/IMAP-Abruf“ in den Einstellungen des Postfachs aktiviert sein."),
    "t-online.de": ("Telekom", _s("securesmtp.t-online.de", 465, 587), _s("secureimap.t-online.de", 993),
                    "Bei der Telekom wird das separate E-Mail-Passwort (nicht das Kundencenter-Passwort) benötigt."),
    "gmail.com": ("Gmail", _s("smtp.gmail.com", 465, 587), _s("imap.gmail.com", 993), APP_PASSWORT),
    "googlemail.com": ("Gmail", _s("smtp.gmail.com", 465, 587), _s("imap.gmail.com", 993), APP_PASSWORT),
    "google.com": ("Google Workspace", _s("smtp.gmail.com", 465, 587), _s("imap.gmail.com", 993), APP_PASSWORT),
    "outlook.com": ("Microsoft", _s("smtp-mail.outlook.com", 587), _s("outlook.office365.com", 993),
                    "Microsoft erlaubt die Anmeldung per Passwort oft nicht mehr – ggf. App-Passwort verwenden."),
    "hotmail.com": ("Microsoft", _s("smtp-mail.outlook.com", 587), _s("outlook.office365.com", 993),
                    "Microsoft erlaubt die Anmeldung per Passwort oft nicht mehr – ggf. App-Passwort verwenden."),
    "hotmail.de": ("Microsoft", _s("smtp-mail.outlook.com", 587), _s("outlook.office365.com", 993),
                   "Microsoft erlaubt die Anmeldung per Passwort oft nicht mehr – ggf. App-Passwort verwenden."),
    "live.de": ("Microsoft", _s("smtp-mail.outlook.com", 587), _s("outlook.office365.com", 993),
                "Microsoft erlaubt die Anmeldung per Passwort oft nicht mehr – ggf. App-Passwort verwenden."),
    "outlook.de": ("Microsoft", _s("smtp-mail.outlook.com", 587), _s("outlook.office365.com", 993),
                   "Microsoft erlaubt die Anmeldung per Passwort oft nicht mehr – ggf. App-Passwort verwenden."),
    "protection.outlook.com": ("Microsoft 365", _s("smtp.office365.com", 587), _s("outlook.office365.com", 993),
                               "Microsoft 365 erlaubt die Anmeldung per Passwort oft nicht – ggf. App-Passwort."),
    "icloud.com": ("iCloud", _s("smtp.mail.me.com", 587), _s("imap.mail.me.com", 993), APP_PASSWORT),
    "me.com": ("iCloud", _s("smtp.mail.me.com", 587), _s("imap.mail.me.com", 993), APP_PASSWORT),
    "yahoo.com": ("Yahoo", _s("smtp.mail.yahoo.com", 465), _s("imap.mail.yahoo.com", 993), APP_PASSWORT),
    "yahoo.de": ("Yahoo", _s("smtp.mail.yahoo.com", 465), _s("imap.mail.yahoo.com", 993), APP_PASSWORT),
    "aol.com": ("AOL", _s("smtp.aol.com", 465), _s("imap.aol.com", 993), APP_PASSWORT),
    "posteo.de": ("Posteo", _s("posteo.de", 465, 587), _s("posteo.de", 993), ""),
    "mailbox.org": ("mailbox.org", _s("smtp.mailbox.org", 465, 587), _s("imap.mailbox.org", 993), ""),
    "freenet.de": ("freenet", _s("mx.freenet.de", 587, 465), _s("mx.freenet.de", 993), ""),
    "ionos.de": ("IONOS", _s("smtp.ionos.de", 465, 587), _s("imap.ionos.de", 993), ""),
    "kundenserver.de": ("IONOS", _s("smtp.ionos.de", 465, 587), _s("imap.ionos.de", 993), ""),
    "strato.de": ("STRATO", _s("smtp.strato.de", 465, 587), _s("imap.strato.de", 993), ""),
    "rzone.de": ("STRATO", _s("smtp.strato.de", 465, 587), _s("imap.strato.de", 993), ""),
    "your-server.de": ("Hetzner", _s("mail.your-server.de", 465, 587), _s("mail.your-server.de", 993), ""),
    "zoho.eu": ("Zoho", _s("smtp.zoho.eu", 465, 587), _s("imap.zoho.eu", 993), ""),
    "zoho.com": ("Zoho", _s("smtp.zoho.com", 465, 587), _s("imap.zoho.com", 993), ""),
}

# Hoster, deren MX-Server zugleich SMTP/IMAP-Server des Kontos ist
MX_IST_SERVER = ("kasserver.com", "netcup.net", "manitu.net", "hostpoint.ch", "df.eu")


def registrierbar(host: str) -> str:
    """'mx1.pub.mailpod10-cph3.one.com' -> 'one.com' (fuer gaengige Endungen ausreichend)."""
    teile = host.lower().rstrip(".").split(".")
    if len(teile) >= 3 and teile[-2] in ("co", "com", "org", "net") and len(teile[-1]) == 2:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def _http(transport: Any = None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True, transport=transport,
                        headers={"User-Agent": "bewerbungsagent (mail-autoconfig)"})


def mx_hosts(domain: str, http: httpx.Client) -> list[str]:
    """MX-Eintraege per DNS-over-HTTPS (die Standardbibliothek kennt keine MX-Abfrage)."""
    for url in (f"https://dns.google/resolve?name={domain}&type=MX",
                f"https://cloudflare-dns.com/dns-query?name={domain}&type=MX"):
        try:
            antwort = http.get(url, headers={"Accept": "application/dns-json"})
            antwort.raise_for_status()
            eintraege = [a.get("data", "") for a in antwort.json().get("Answer") or [] if a.get("type") == 15]
        except Exception as e:
            log.info("MX-Abfrage %s fehlgeschlagen: %s", url, e)
            continue
        paare = []
        for e in eintraege:
            teile = e.split()
            if len(teile) == 2 and teile[0].isdigit():
                paare.append((int(teile[0]), teile[1].rstrip(".").lower()))
        return [h for _, h in sorted(paare)]
    return []


def aus_autoconfig_xml(text: str, adresse: str) -> tuple[str, list[Server], list[Server]] | None:
    """Thunderbird-Autoconfig (clientConfig v1.1) auswerten."""
    try:
        wurzel = ET.fromstring(text)
    except ET.ParseError:
        return None
    provider = wurzel.find("emailProvider")
    if provider is None:
        return None
    name = (provider.findtext("displayName") or provider.get("id") or "").strip()
    imap, smtp = [], []
    for knoten, ziel, art in ((provider.findall("incomingServer"), imap, "imap"),
                              (provider.findall("outgoingServer"), smtp, "smtp")):
        for s in knoten:
            if art == "imap" and s.get("type") != "imap":
                continue
            host = (s.findtext("hostname") or "").replace("%EMAILDOMAIN%", adresse.rsplit("@", 1)[-1]).strip()
            port = s.findtext("port") or ""
            sock = (s.findtext("socketType") or "").upper()
            if host and port.isdigit() and sock in ("SSL", "STARTTLS"):
                ziel.append(Server(host, int(port)))
    if not smtp or not imap:
        return None
    return name, smtp, imap


def erkenne(adresse: str, *, transport: Any = None) -> Konto:
    """Ermittelt die Server zu einer E-Mail-Adresse (ohne Anmeldung)."""
    adresse = adresse.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", adresse):
        raise ValueError("Das sieht nicht nach einer E-Mail-Adresse aus.")
    domain = adresse.rsplit("@", 1)[-1]
    if domain in BEKANNT:
        name, smtp, imap, hinweis = BEKANNT[domain]
        return Konto(adresse, name, smtp, imap, "bekannt", hinweis)
    with _http(transport) as http:
        for url, quelle in ((f"https://autoconfig.thunderbird.net/v1.1/{domain}", "thunderbird"),
                            (f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={adresse}", "autoconfig"),
                            (f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml", "autoconfig")):
            try:
                antwort = http.get(url)
                if antwort.status_code == 200:
                    gefunden = aus_autoconfig_xml(antwort.text, adresse)
                    if gefunden:
                        return Konto(adresse, gefunden[0] or domain, gefunden[1], gefunden[2], quelle)
            except Exception as e:
                log.info("Autoconfig %s nicht abrufbar: %s", url, e)
        mx = mx_hosts(domain, http)
        if mx:
            basis = registrierbar(mx[0])
            for schluessel, (name, smtp, imap, hinweis) in BEKANNT.items():
                if basis == schluessel or mx[0].endswith("." + schluessel):
                    return Konto(adresse, f"{name} (Mailserver deiner Domain)", smtp, imap, "mx", hinweis)
            if basis.endswith(MX_IST_SERVER):
                return Konto(adresse, basis, _s(mx[0], 465, 587), _s(mx[0], 993), "mx")
            try:
                antwort = http.get(f"https://autoconfig.thunderbird.net/v1.1/{basis}")
                gefunden = aus_autoconfig_xml(antwort.text, adresse) if antwort.status_code == 200 else None
                if gefunden:
                    return Konto(adresse, f"{gefunden[0] or basis} (Mailserver deiner Domain)",
                                 gefunden[1], gefunden[2], "mx")
            except Exception as e:
                log.info("Autoconfig fuer MX %s nicht abrufbar: %s", basis, e)
    return Konto(adresse, domain, _s(f"smtp.{domain}", 465, 587), _s(f"imap.{domain}", 993), "vermutung",
                 "Die Server wurden nur vermutet – falls die Anmeldung scheitert, unter „Server selbst eintragen“ "
                 "die Angaben deines Anbieters verwenden.")


# --------------------------------------------------------------------------- #
# Anmeldung testen und speichern
# --------------------------------------------------------------------------- #

@dataclass
class Ergebnis:
    smtp_ok: bool = False
    imap_ok: bool = False
    smtp: Server | None = None
    imap: Server | None = None
    meldungen: list[str] = field(default_factory=list)

    @property
    def gespeichert(self) -> bool:
        return self.smtp_ok


def _verstaendlich(e: Exception) -> str:
    text = str(e)
    if "auth" in text.lower() or "535" in text or "authenticationfailed" in text.lower().replace(" ", "") \
            or "invalid credentials" in text.lower() or "login failed" in text.lower():
        return "Anmeldung abgelehnt – Adresse oder Passwort stimmt nicht."
    if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "Server antwortet nicht (Zeitüberschreitung)."
    if isinstance(e, (socket.gaierror, ConnectionRefusedError, OSError)) and not isinstance(e, ssl.SSLError):
        return f"Server nicht erreichbar ({text[:80]})."
    return text[:160]


def _smtp_login(server: Server, adresse: str, passwort: str) -> None:
    from . import mail

    verbindung = mail._verbinde(mail.SmtpKonfig(host=server.host, port=server.port, benutzer=adresse,
                                                passwort=passwort, absender=adresse))
    verbindung.quit()


def _imap_login(server: Server, adresse: str, passwort: str) -> None:
    from . import postfach

    with postfach.verbinde(postfach.ImapKonfig(server.host, server.port, adresse, passwort)) as imap:
        postfach.anzahl_posteingang(imap)


def verbinde_konto(
    konto: Konto,
    passwort: str,
    *,
    speichern: bool = True,
    smtp_login: Callable[[Server, str, str], None] = _smtp_login,
    imap_login: Callable[[Server, str, str], None] = _imap_login,
) -> Ergebnis:
    """Testet Versand und Empfang; speichert bei funktionierendem Versand."""
    from .. import zugangsdaten

    if not passwort:
        raise ValueError("Bitte das Passwort des Postfachs eingeben.")
    e = Ergebnis()
    for server in konto.smtp:
        try:
            smtp_login(server, konto.adresse, passwort)
        except Exception as fehler:
            e.meldungen.append(f"Versand über {server.text}: {_verstaendlich(fehler)}")
            if "abgelehnt" in e.meldungen[-1]:
                break  # falsches Passwort - anderer Port hilft nicht
            continue
        e.smtp_ok, e.smtp = True, server
        break
    for server in konto.imap:
        try:
            imap_login(server, konto.adresse, passwort)
        except Exception as fehler:
            e.meldungen.append(f"Empfang über {server.text}: {_verstaendlich(fehler)}")
            continue
        e.imap_ok, e.imap = True, server
        break
    if speichern and e.smtp_ok:
        imap = e.imap or (konto.imap[0] if konto.imap else None)
        zugangsdaten.speichere({
            "SMTP_HOST": e.smtp.host, "SMTP_PORT": str(e.smtp.port), "SMTP_BENUTZER": konto.adresse,
            "SMTP_PASSWORT": passwort, "SMTP_ABSENDER": konto.adresse,
            # IMAP nutzt denselben Login (siehe postfach.imap_konfig)
            "IMAP_HOST": imap.host if imap else None, "IMAP_PORT": str(imap.port) if imap else None,
            "IMAP_BENUTZER": None, "IMAP_PASSWORT": None,
        })
    return e
