"""Bewerbung per E-Mail ueber das eigene Postfach (SMTP).

Konfiguration ausschliesslich aus der Umgebung (.env), nie aus der Profil-YAML:

    SMTP_HOST=smtp.example.de
    SMTP_PORT=465              # 465 = SSL, 587 = STARTTLS
    SMTP_BENUTZER=ich@example.de
    SMTP_PASSWORT=...
    SMTP_ABSENDER=ich@example.de   # optional, Standard: SMTP_BENUTZER bzw. Profil-E-Mail

Jede Bewerbung geht zusaetzlich als Bcc an die Absenderadresse, damit sie im
eigenen Postfach nachvollziehbar bleibt.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from ..config import Profil
from ..models import Job

MAX_ANHANG_MB = 15.0
WARN_ANHANG_MB = 10.0


@dataclass
class SmtpKonfig:
    host: str
    port: int
    benutzer: str
    passwort: str
    absender: str

    @property
    def ssl(self) -> bool:
        return self.port == 465


def smtp_konfig(profil: Profil | None = None) -> SmtpKonfig | None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    benutzer = os.environ.get("SMTP_BENUTZER", "").strip()
    absender = (
        os.environ.get("SMTP_ABSENDER", "").strip()
        or benutzer
        or (profil.person.email if profil else "")
    )
    try:
        port = int(os.environ.get("SMTP_PORT", "465") or 465)
    except ValueError:
        port = 465
    return SmtpKonfig(
        host=host, port=port, benutzer=benutzer,
        passwort=os.environ.get("SMTP_PASSWORT", ""), absender=absender,
    )


def smtp_eingerichtet() -> bool:
    return bool(os.environ.get("SMTP_HOST", "").strip())


def begleittext(
    job: Job, profil: Profil, anhaenge: list[str] | None = None, *,
    anrede: str | None = None, referenz: str | None = None,
) -> str:
    """Kurzer Mailtext; das eigentliche Anschreiben haengt als PDF an."""
    p = profil.person
    if referenz:
        ref = f" (Kennziffer {referenz})"
    else:
        ref = f" (Referenznummer {job.ref})" if job.quelle == "arbeitsagentur" and job.ref else ""
    unterlagen = "mein Anschreiben, meinen Lebenslauf und weitere Unterlagen"
    if anhaenge is not None and len(anhaenge) <= 1:
        unterlagen = "mein Anschreiben"
    kontakt = "\n".join(x for x in (p.telefon, p.email) if x)
    return (
        f"{anrede or 'Sehr geehrte Damen und Herren,'}\n\n"
        f"anbei sende ich Ihnen meine Bewerbung als {job.titel}{ref}. "
        f"Im Anhang finden Sie {unterlagen}.\n\n"
        "Über eine Einladung zu einem persönlichen Gespräch freue ich mich.\n\n"
        f"Mit freundlichen Grüßen\n{p.name}"
        + (f"\n{kontakt}" if kontakt else "")
    )


def baue_nachricht(
    *,
    absender: str,
    absender_name: str,
    empfaenger: str,
    betreff: str,
    text: str,
    anhaenge: list[str | Path],
    bcc_selbst: bool = True,
    antwort_auf: str | None = None,
) -> EmailMessage:
    gesamt = sum(Path(a).stat().st_size for a in anhaenge) / 1024 / 1024
    if gesamt > MAX_ANHANG_MB:
        raise ValueError(
            f"Anhänge sind zusammen {gesamt:.1f} MB groß (maximal {MAX_ANHANG_MB:.0f} MB). "
            "Bitte Unterlagen verkleinern."
        )
    msg = EmailMessage()
    msg["From"] = formataddr((absender_name, absender)) if absender_name else absender
    msg["To"] = empfaenger
    if bcc_selbst:
        msg["Bcc"] = absender
    msg["Subject"] = betreff
    msg["Message-ID"] = make_msgid(domain=absender.split("@")[-1] if "@" in absender else None)
    if antwort_auf:
        # Nachfassen im selben Thread wie die Bewerbung
        msg["In-Reply-To"] = antwort_auf
        msg["References"] = antwort_auf
    msg.set_content(text)
    for anhang in anhaenge:
        pfad = Path(anhang)
        typ, _ = mimetypes.guess_type(pfad.name)
        haupt, unter = (typ or "application/octet-stream").split("/", 1)
        msg.add_attachment(pfad.read_bytes(), maintype=haupt, subtype=unter, filename=pfad.name)
    return msg


class NichtGesendet(Exception):
    """Die E-Mail hat den Server sicher nicht erreicht oder wurde abgelehnt – sie darf erneut gesendet werden."""


# Antworten des Servers, mit denen er die Nachricht nicht angenommen hat (nichts ist beim Empfaenger angekommen)
_ABGELEHNT = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)


def vor_dem_senden(fehler: BaseException) -> bool:
    """True, wenn der Fehler sicher vor der Zustellung lag (Verbindung, Anmeldung, vom Server abgelehnt).

    Alles andere – Abbruch oder Zeitueberschreitung waehrend der Uebertragung, Fehler danach – ist unklar:
    die E-Mail kann schon raus sein und darf nicht automatisch noch einmal gehen.
    """
    return isinstance(fehler, (NichtGesendet, *_ABGELEHNT))


def _verbinde(konfig: SmtpKonfig) -> smtplib.SMTP:
    kontext = ssl.create_default_context()
    if konfig.ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(konfig.host, konfig.port, timeout=60, context=kontext)
    else:
        server = smtplib.SMTP(konfig.host, konfig.port, timeout=60)
        server.ehlo()
        server.starttls(context=kontext)
        server.ehlo()
    if konfig.benutzer:
        server.login(konfig.benutzer, konfig.passwort)
    return server


def sende(nachricht: EmailMessage, konfig: SmtpKonfig) -> str:
    """Verschickt die Nachricht und liefert ihre Message-ID.

    Fehler beim Verbinden und Anmelden kommen als `NichtGesendet` – dann ist sicher nichts rausgegangen.
    """
    try:
        server = _verbinde(konfig)
    except OSError as e:  # smtplib.SMTPException ist ein OSError
        raise NichtGesendet(str(e) or type(e).__name__) from e
    try:
        server.send_message(nachricht)
    except _ABGELEHNT as e:
        raise NichtGesendet(str(e) or type(e).__name__) from e
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass
    return str(nachricht["Message-ID"])


def testmail(profil: Profil) -> str:
    """Schickt eine Testmail an die eigene Adresse; wirft bei Fehlern."""
    konfig = smtp_konfig(profil)
    if konfig is None:
        raise RuntimeError("SMTP ist nicht eingerichtet (SMTP_HOST fehlt in der .env).")
    nachricht = baue_nachricht(
        absender=konfig.absender,
        absender_name=profil.person.name,
        empfaenger=konfig.absender,
        betreff="Bewerbungsagent: Testmail",
        text="Der E-Mail-Versand des Bewerbungsagenten funktioniert.",
        anhaenge=[],
        bcc_selbst=False,
    )
    return sende(nachricht, konfig)
