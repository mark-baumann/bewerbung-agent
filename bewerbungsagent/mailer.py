"""Versendet manuell freigegebene Bewerbungen per SMTP."""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from .config import Profil
from .models import Job

KEYRING_SERVICE = "bewerbungsagent.smtp"


def _secret_account(profil: Profil) -> str:
    return profil.smtp.username.strip() or profil.smtp.sender_email.strip()


def passwort_gesetzt(profil: Profil) -> bool:
    account = _secret_account(profil)
    if not account:
        return False
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return False
    try:
        return bool(keyring.get_password(KEYRING_SERVICE, account))
    except KeyringError as exc:
        raise RuntimeError(f"Windows-Schlüsselbund nicht verfügbar: {exc}") from exc


def speichere_passwort(profil: Profil, passwort: str) -> None:
    if not passwort:
        return
    account = _secret_account(profil)
    if not account:
        raise ValueError("Bitte SMTP-Benutzername oder Absenderadresse eintragen.")
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise RuntimeError(
            "Sichere Passwortablage fehlt. Installiere die UI-Abhängigkeiten erneut."
        ) from exc
    try:
        keyring.set_password(KEYRING_SERVICE, account, passwort)
    except KeyringError as exc:
        raise RuntimeError(f"Passwort konnte nicht sicher gespeichert werden: {exc}") from exc


def _smtp_passwort(profil: Profil) -> str | None:
    account = _secret_account(profil)
    if not account:
        return None
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise RuntimeError(
            "Sichere Passwortablage fehlt. Installiere die UI-Abhängigkeiten erneut."
        ) from exc
    try:
        return keyring.get_password(KEYRING_SERVICE, account)
    except KeyringError as exc:
        raise RuntimeError(f"Windows-Schlüsselbund nicht verfügbar: {exc}") from exc


def _validiere(profil: Profil, empfaenger: str) -> str:
    settings = profil.smtp
    if not settings.host.strip():
        raise ValueError("SMTP-Server fehlt. Trage ihn zuerst im Profil unter E-Mail ein.")
    if not 1 <= int(settings.port) <= 65535:
        raise ValueError("SMTP-Port muss zwischen 1 und 65535 liegen.")
    absender = parseaddr(settings.sender_email.strip())[1]
    if "@" not in absender:
        raise ValueError("Bitte eine gültige Absender-E-Mail-Adresse konfigurieren.")
    empfaenger_email = parseaddr(empfaenger.strip())[1]
    if empfaenger_email != empfaenger.strip() or "@" not in empfaenger_email:
        raise ValueError("Bitte eine gültige Empfänger-E-Mail-Adresse eintragen.")
    if settings.use_ssl and settings.starttls:
        raise ValueError("Wähle entweder SSL/TLS oder STARTTLS, nicht beides.")
    return empfaenger_email


def _verbinde(profil: Profil):
    settings = profil.smtp
    username = settings.username.strip()
    password = _smtp_passwort(profil) if username else None
    if username and not password:
        raise ValueError(
            "Für diesen SMTP-Benutzer ist kein Passwort im Windows-Schlüsselbund gespeichert."
        )

    context = ssl.create_default_context()
    server = None
    try:
        if settings.use_ssl:
            server = smtplib.SMTP_SSL(
                settings.host.strip(),
                int(settings.port),
                timeout=30,
                context=context,
            )
        else:
            server = smtplib.SMTP(settings.host.strip(), int(settings.port), timeout=30)
            server.ehlo()
            if settings.starttls:
                server.starttls(context=context)
                server.ehlo()
        if username:
            server.login(username, password)
    except (smtplib.SMTPException, OSError):
        if server is not None:
            server.close()
        raise
    return server


def teste_verbindung(profil: Profil) -> None:
    """Testet TLS und Anmeldung, ohne eine E-Mail zu versenden."""
    if not profil.smtp.host.strip() or not profil.smtp.sender_email.strip():
        raise ValueError("Bitte SMTP-Server und Absenderadresse ausfüllen.")
    server = _verbinde(profil)
    server.close()


def sende_bewerbung(
    profil: Profil,
    job: Job,
    empfaenger: str,
    anschreiben: str,
) -> None:
    """Sendet eine einzelne Bewerbung samt Lebenslauf und hinterlegten Zeugnissen."""
    recipient = _validiere(profil, empfaenger)
    if not anschreiben.strip():
        raise ValueError("Das Anschreiben ist leer. Erstelle und prüfe es zuerst.")

    message = EmailMessage()
    message["From"] = profil.smtp.sender_email.strip()
    message["To"] = recipient
    message["Subject"] = f"Bewerbung: {job.titel} – {job.arbeitgeber or 'Unternehmen'}"
    message.set_content(anschreiben.strip())

    for attachment in profil.anhaenge():
        path = Path(attachment)
        mime_type, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    server = _verbinde(profil)
    try:
        server.send_message(message)
    finally:
        server.close()
