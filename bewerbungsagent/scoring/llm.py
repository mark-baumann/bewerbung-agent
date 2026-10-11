"""Semantische Bewertung mit Claude, ChatGPT/OpenAI oder Ollama.

Die Heuristik sieht nur Stringtreffer. Der LLM-Bewerter beurteilt zusaetzlich,
ob die Rolle inhaltlich zum Profil passt (auch bei anderer Wortwahl) und wie
die Anzeige tonal einzuordnen ist. Ohne API-Key faellt alles auf die Heuristik
zurueck.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from typing import Any, Callable

import httpx

from ..config import Profil
from ..models import Job, Score
from . import heuristik

log = logging.getLogger(__name__)

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "glm-5.3-flash"
# Modelle der Ollama-Cloud-API (Stand: ollama.com/v1/models, Oktober 2026)
OLLAMA_MODELLE = [
    "glm-5.3-flash", "glm-5.3", "glm-5.2",
    "deepseek-v4.1-flash", "deepseek-v4-pro:0813",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6",
    "minimax-m3", "minimax-m2.7",
    "mistral-large-4", "mistral-large-3:675b",
    "nemotron-3-ultra", "nemotron-3-super", "nemotron-3-nano:30b",
    "gpt-oss:120b", "gpt-oss:20b",
    "gemma4:31b",
]
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_BASE_URL = "https://api.openai.com"
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"
# Claude-Modelle, bei denen die API eine abgelehnte Anfrage serverseitig auf
# einem Ausweichmodell wiederholt (Beta "server-side-fallback").
FALLBACK_MODELLE = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SCHEMA = {
    "type": "object",
    "properties": {
        "passung": {
            "type": "integer",
            "description": "0-100: fachliche Passung der Rolle zum Bewerberprofil",
        },
        "sentiment": {
            "type": "integer",
            "description": (
                "0-100: Tonalitaet und implizite Arbeitsbedingungen der Anzeige. "
                "50 = neutral, <40 = Warnsignale (Ueberstundenkultur, Floskeln, "
                "Zeitarbeit), >70 = konkrete gute Bedingungen"
            ),
        },
        "treffer": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Konkrete Anforderungen aus der Anzeige, die das Profil erfuellt",
        },
        "gruen": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Positive Signale der Anzeige",
        },
        "rot": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Warnsignale oder fehlende Anforderungen",
        },
        "ausgeschlossen": {
            "type": "boolean",
            "description": "true, wenn die Stelle ein Ausschlusskriterium des Profils verletzt",
        },
        "ausschlussgrund": {"type": ["string", "null"]},
        "begruendung": {
            "type": "string",
            "description": "Zwei bis drei Saetze, konkret, ohne Floskeln",
        },
    },
    "required": [
        "passung",
        "sentiment",
        "treffer",
        "gruen",
        "rot",
        "ausgeschlossen",
        "ausschlussgrund",
        "begruendung",
    ],
    "additionalProperties": False,
}

SYSTEM = """Du bewertest Stellenanzeigen fuer genau einen Bewerber.

Bewerberprofil:
{profil}

Pflicht-Skills: {skills}
Wunschthemen: {wunsch}
Ausschlusskriterien: {ausschluss}
Ort/Umkreis: {ort} / {umkreis} km
{lebenslauf}
Bewerte streng und ehrlich. Eine Anzeige, die nur zufaellig ein Stichwort
enthaelt, ist keine Passung. Sprachlich euphorische Anzeigen ("junges
dynamisches Team", "Macher gesucht", "hohe Belastbarkeit") signalisieren
haeufig Ueberstundenkultur und hohe Fluktuation - werte das im Sentiment
negativ, unabhaengig davon wie positiv der Text klingt. Konkrete Angaben
(Gehaltsspanne, Tarifbindung, unbefristet, Gleitzeit) werte positiv.
Antworte ausschliesslich im vorgegebenen JSON-Schema."""


class LLMNichtVerfuegbar(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def anthropic_verfuegbar() -> bool:
    return bool(_env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"))


def ollama_verfuegbar() -> bool:
    return bool(_env("OLLAMA_BASE_URL") or _env("OLLAMA_API_KEY") or _env("OLLAMA_MODEL"))


def openai_verfuegbar() -> bool:
    return bool(_env("OPENAI_API_KEY"))


def ist_claude_modell(modell: str | None) -> bool:
    return bool(modell and modell.strip().lower().startswith("claude"))


def ist_openai_modell(modell: str | None) -> bool:
    m = (modell or "").strip().lower()
    # gpt-oss ist ein offenes Modell und laeuft ueber Ollama
    return m.startswith(("gpt-", "o1", "o3", "o4")) and not m.startswith("gpt-oss")


def provider(modell: str | None = None) -> str | None:
    """Waehlt den LLM-Anbieter: 'anthropic', 'openai' oder 'ollama'.

    Bei gesetztem Key gewinnt das zum Modell passende Backend (Claude-Modelle
    laufen ueber Anthropic, alles andere ueber Ollama). Sonst wird der Key
    genutzt, der gesetzt ist.
    """
    anthropic_ok = anthropic_verfuegbar()
    openai_ok = openai_verfuegbar()
    ollama_ok = ollama_verfuegbar()
    if modell:
        if ist_claude_modell(modell) and anthropic_ok:
            return "anthropic"
        if ist_openai_modell(modell) and openai_ok:
            return "openai"
        if not ist_claude_modell(modell) and ollama_ok:
            return "ollama"
    if anthropic_ok:
        return "anthropic"
    if openai_ok:
        return "openai"
    if ollama_ok:
        return "ollama"
    return None


def client_verfuegbar() -> bool:
    return provider() is not None


class OllamaClient:
    """Minimaler OpenAI-kompatibler Chat-Client fuer einen Ollama-Server."""

    def __init__(self, transport=None):
        self.base_url = (_env("OLLAMA_BASE_URL") or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        self.api_key = _env("OLLAMA_API_KEY") or "ollama"
        self.modell = _env("OLLAMA_MODEL") or _env("LLM_MODEL") or OLLAMA_DEFAULT_MODEL
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            transport=transport,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        *,
        modell: str | None = None,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_schema: dict | None = None,
    ) -> str:
        effektives_modell = modell or self.modell
        if ist_claude_modell(effektives_modell):
            effektives_modell = self.modell
        payload_messages = [{"role": "system", "content": system}, *messages]
        if json_schema:
            payload_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Antworte ausschliesslich mit einem JSON-Objekt, das genau "
                        "diesem Schema folgt (kein Markdown, kein Kommentar):\n"
                        + json.dumps(json_schema, ensure_ascii=False)
                    ),
                }
            )
        payload = {
            "model": effektives_modell,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        antwort = self._client.post(
            "/v1/chat/completions",
            json=payload,
            headers=self._headers(),
        )
        antwort.raise_for_status()
        daten = antwort.json()
        if daten.get("error"):
            raise LLMNichtVerfuegbar(f"Ollama-Fehler: {daten['error']}")
        try:
            return daten["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMNichtVerfuegbar("Leere Antwort vom Ollama-Modell") from e


class OpenAIClient:
    """Minimaler Client fuer die offizielle ChatGPT/OpenAI-API."""

    def __init__(self, transport=None):
        self.base_url = (_env("OPENAI_BASE_URL") or OPENAI_BASE_URL).rstrip("/")
        self.api_key = _env("OPENAI_API_KEY")
        self.modell = _env("OPENAI_MODEL") or _env("LLM_MODEL") or OPENAI_DEFAULT_MODEL
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            transport=transport,
        )

    def chat(
        self, *, modell: str | None = None, system: str,
        messages: list[dict[str, str]], max_tokens: int, json_schema: dict | None = None,
    ) -> str:
        if not self.api_key:
            raise LLMNichtVerfuegbar("OPENAI_API_KEY ist nicht gesetzt")
        payload: dict = {
            "model": modell or self.modell,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "job_bewertung", "strict": True, "schema": json_schema},
            }
        response = self._client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMNichtVerfuegbar("Leere Antwort von der OpenAI-API") from e


def effektives_modell(anbieter: str, modell: str | None) -> str:
    """Modell, das beim gewaehlten Anbieter tatsaechlich angefragt wird."""
    if anbieter == "anthropic":
        return modell if ist_claude_modell(modell) else (_env("ANTHROPIC_MODEL") or ANTHROPIC_DEFAULT_MODEL)
    if anbieter == "openai":
        return modell if ist_openai_modell(modell) else (
            _env("OPENAI_MODEL") or _env("LLM_MODEL") or OPENAI_DEFAULT_MODEL
        )
    if not modell or ist_claude_modell(modell):
        return _env("OLLAMA_MODEL") or _env("LLM_MODEL") or OLLAMA_DEFAULT_MODEL
    return modell


def anthropic_text(
    client: Any,
    *,
    modell: str,
    system: str,
    nutzer: str,
    max_tokens: int,
    effort: str = "medium",
    json_schema: dict | None = None,
) -> str:
    """Eine Claude-Anfrage mit adaptivem Denken; liefert den Antworttext."""
    output_config: dict[str, Any] = {"effort": effort}
    if json_schema:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    params: dict[str, Any] = {
        "model": modell,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": nutzer}],
    }
    antwort = None
    if modell in FALLBACK_MODELLE:
        import anthropic

        try:
            antwort = client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        except anthropic.BadRequestError as e:
            # Ausweichlogik nicht verfuegbar (Plattform/Konto) - normale Anfrage
            log.info("Server-Fallback nicht nutzbar (%s) - normale Anfrage.", e)
    if antwort is None:
        antwort = client.messages.create(**params)
    if antwort.stop_reason == "refusal":
        raise LLMNichtVerfuegbar(f"Anfrage abgelehnt: {antwort.stop_details}")
    text = "\n".join(b.text for b in antwort.content if b.type == "text").strip()
    if not text:
        raise LLMNichtVerfuegbar("Leere Antwort vom Modell")
    return text


def chat(
    modell: str | None,
    *,
    system: str,
    nutzer: str,
    max_tokens: int = 4000,
    json_schema: dict | None = None,
    effort: str = "medium",
) -> str:
    """Eine einzelne Anfrage an den konfigurierten Anbieter (Claude, OpenAI oder Ollama)."""
    anbieter = provider(modell)
    if anbieter is None:
        raise LLMNichtVerfuegbar("Kein LLM-API-Key gesetzt (ANTHROPIC_API_KEY, OPENAI_API_KEY oder OLLAMA_*).")
    ziel = effektives_modell(anbieter, modell)
    if anbieter == "anthropic":
        import anthropic

        return anthropic_text(
            anthropic.Anthropic(), modell=ziel, system=system, nutzer=nutzer,
            max_tokens=max_tokens, effort=effort, json_schema=json_schema,
        )
    nachrichten = [{"role": "user", "content": nutzer}]
    if anbieter == "openai":
        return OpenAIClient().chat(
            modell=ziel, system=system, messages=nachrichten, max_tokens=max_tokens, json_schema=json_schema,
        )
    return OllamaClient().chat(
        modell=ziel, system=system, messages=nachrichten, max_tokens=max_tokens, json_schema=json_schema,
    )


def lebenslauf_block(profil: Profil, max_zeichen: int = 6000) -> str:
    """Lebenslauftext fuer System-Prompts (leer, wenn keiner hinterlegt ist)."""
    from ..dokumente import lebenslauf_auszug

    text = lebenslauf_auszug(profil, max_zeichen=max_zeichen)
    return f"\nLebenslauf (Auszug):\n{text}\n" if text else ""


class LLMBewerter:
    def __init__(self, profil: Profil):
        self.anbieter = provider(profil.bewertung.modell)
        if self.anbieter is None:
            raise LLMNichtVerfuegbar(
                "Kein LLM-API-Key gesetzt (ANTHROPIC_API_KEY, OPENAI_API_KEY oder OLLAMA_API_KEY) - "
                "Bewertung laeuft nur heuristisch."
            )
        self.profil = profil
        self.effort = profil.bewertung.effort
        self.modell = effektives_modell(self.anbieter, profil.bewertung.modell)
        self._system = SYSTEM.format(
            profil=profil.kurzprofil or "(kein Kurzprofil hinterlegt)",
            skills=", ".join(profil.bewertung.skills) or "-",
            wunsch=", ".join(profil.bewertung.wunsch) or "-",
            ausschluss=", ".join(profil.bewertung.ausschluss) or "-",
            ort=profil.suche.wo or "egal",
            umkreis=profil.suche.umkreis,
            lebenslauf=lebenslauf_block(profil),
        )
        if self.anbieter == "anthropic":
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise LLMNichtVerfuegbar("Paket 'anthropic' nicht installiert") from e
            self._anthropic = anthropic.Anthropic()
        elif self.anbieter == "openai":
            self._openai = OpenAIClient()
        else:
            self._ollama = OllamaClient()

    def bewerte(self, job: Job) -> Score:
        anzeige = textwrap.shorten(
            job.beschreibung or "(keine Beschreibung verfuegbar)",
            width=12000,
            placeholder=" [...]",
        )
        inhalt = (
            f"Titel: {job.titel}\n"
            f"Arbeitgeber: {job.arbeitgeber}\n"
            f"Beruf: {job.beruf or '-'}\n"
            f"Ort: {job.ort or '-'} ({job.plz or '-'})"
            f"{f', {job.entfernung_km} km entfernt' if job.entfernung_km is not None else ''}\n"
            f"Homeoffice: {job.homeoffice}\n"
            f"Vertrag: {job.befristung or 'unbekannt'}\n"
            f"Verguetung: {job.verguetung or 'keine Angabe'}\n\n"
            f"Anzeigentext:\n{anzeige}"
        )

        if self.anbieter == "anthropic":
            text = self._anthropic_bewerten(inhalt)
        elif self.anbieter == "openai":
            text = self._openai_bewerten(inhalt)
        else:
            text = self._ollama_bewerten(inhalt)
        daten = json.loads(text)

        passung = float(daten["passung"])
        sent = float(daten["sentiment"])
        gewicht = 0.3 if job.beschreibung else 0.1
        return Score(
            ref=job.ref,
            gesamt=round(passung * (1 - gewicht) + sent * gewicht, 1),
            passung=passung,
            sentiment=sent,
            treffer=list(daten.get("treffer") or []),
            gruen=list(daten.get("gruen") or []),
            rot=list(daten.get("rot") or []),
            begruendung=daten.get("begruendung", ""),
            ausgeschlossen=bool(daten.get("ausgeschlossen")),
            ausschlussgrund=daten.get("ausschlussgrund"),
            bewerter=f"llm:{self.modell}",
        )

    def _anthropic_bewerten(self, inhalt: str) -> str:
        return anthropic_text(
            self._anthropic,
            modell=self.modell,
            system=self._system,
            nutzer=inhalt,
            max_tokens=16000,
            effort=self.effort,
            json_schema=SCHEMA,
        )

    def _ollama_bewerten(self, inhalt: str) -> str:
        text = self._ollama.chat(
            modell=self.modell,
            system=self._system,
            messages=[{"role": "user", "content": inhalt}],
            max_tokens=4000,
            json_schema=SCHEMA,
        )
        if not text:
            raise LLMNichtVerfuegbar("Leere Antwort vom Modell")
        return text

    def _openai_bewerten(self, inhalt: str) -> str:
        return self._openai.chat(
            modell=self.modell,
            system=self._system,
            messages=[{"role": "user", "content": inhalt}],
            max_tokens=4000,
            json_schema=SCHEMA,
        )


def bewerte_jobs(
    jobs: list[Job],
    profil: Profil,
    mit_llm: bool = True,
    fortschritt: Callable[[int, int], None] | None = None,
) -> list[Score]:
    """Bewertet alle Jobs. Faellt pro Job auf die Heuristik zurueck, wenn das LLM ausfaellt.

    `fortschritt(erledigt, gesamt)` wird nach jedem Job aufgerufen (fuer die UI).
    """
    bewerter: LLMBewerter | None = None
    if mit_llm:
        try:
            bewerter = LLMBewerter(profil)
        except LLMNichtVerfuegbar as e:
            log.warning("LLM-Bewertung nicht aktiv (%s) - nutze Heuristik.", e)

    ergebnisse: list[Score] = []
    for nummer, job in enumerate(jobs, 1):
        # Harte Ausschluesse gelten immer und sparen einen LLM-Aufruf.
        basis = heuristik.bewerte(job, profil)
        if bewerter is None or basis.ausgeschlossen:
            ergebnisse.append(basis)
        else:
            try:
                score = bewerter.bewerte(job)
                # Regex-Warnsignale ergaenzen, die das Modell uebersehen hat.
                score.rot = sorted(set(score.rot) | set(basis.rot))
                ergebnisse.append(score)
            except Exception as e:  # Netzwerk, Rate-Limit, kaputtes JSON
                log.warning("LLM-Bewertung fuer %s fehlgeschlagen (%s) - Heuristik.", job.ref, e)
                ergebnisse.append(basis)
        if fortschritt:
            fortschritt(nummer, len(jobs))
    return ergebnisse
