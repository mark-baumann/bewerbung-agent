"""Semantische Bewertung mit Claude, ChatGPT/OpenAI oder Ollama.

Die Heuristik sieht nur Stringtreffer. Der LLM-Bewerter beurteilt zusaetzlich,
ob die Rolle inhaltlich zum Profil passt (auch bei anderer Wortwahl) und wie
die Anzeige tonal einzuordnen ist. Ohne API-Key faellt alles auf die Heuristik
zurueck.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import textwrap
import time
from typing import Any, Callable

import httpx

from ..config import Profil
from ..models import Job, Score
from . import heuristik

log = logging.getLogger(__name__)

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
# Nur ein Wunsch: ob es das Modell beim Server gibt, prueft `pruefe_modell`.
OLLAMA_DEFAULT_MODEL = "gpt-oss:120b"
# Wenn das gewuenschte Modell fehlt, wird - falls vorhanden - eines davon genommen.
BEVORZUGTE_MODELLE = {
    "anthropic": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-haiku-5-5"),
    "openai": ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"),
    # Der Nutzer arbeitet mit dem grossen offenen Modell (gpt-oss 120b) - das zuerst
    "ollama": (
        "gpt-oss:120b", "glm-5.3", "glm-5.2", "deepseek-v4.1-flash", "kimi-k3",
        "qwen3", "llama3.3", "gemma4:31b", "gpt-oss:20b",
    ),
}
MODELL_CACHE_SEKUNDEN = 600
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


class ModellNichtGefunden(LLMNichtVerfuegbar):
    """Der Anbieter kennt das angefragte Modell nicht (HTTP 404 / model_not_found)."""

    def __init__(self, modell: str, antwort: str = ""):
        super().__init__(
            f"Das Modell „{modell}“ gibt es bei deinem KI-Zugang nicht. "
            "Wähle unter Einstellungen → Konten ein verfügbares Modell."
            + (f" (Antwort: {antwort[:200]})" if antwort else "")
        )
        self.modell = modell


def _modell_fehlt(status: int, text: str) -> bool:
    t = text.lower()
    if "model_not_found" in t:
        return True
    return status in (400, 404) and "model" in t and ("not found" in t or "does not exist" in t)


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
    laufen ueber Anthropic, GPT-Modelle ueber OpenAI, alles andere ueber Ollama).
    Sonst: Anthropic, dann Ollama, dann OpenAI.
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
    # Eigener/offener Ollama-Zugang vor einem OpenAI-Konto, wenn das Modell nicht ausdruecklich
    # ein OpenAI-Modell ist (sonst landet z. B. das Standardmodell bei einem Konto ohne Guthaben)
    if ollama_ok:
        return "ollama"
    if openai_ok:
        return "openai"
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
        if antwort.status_code >= 400 and _modell_fehlt(antwort.status_code, antwort.text):
            raise ModellNichtGefunden(effektives_modell, antwort.text)
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
        if response.status_code >= 400 and _modell_fehlt(response.status_code, response.text):
            raise ModellNichtGefunden(payload["model"], response.text)
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


# --------------------------------------------------------------------------- #
# Verfuegbare Modelle: nie ein Modell anfragen, das der Server nicht kennt
# --------------------------------------------------------------------------- #

_modell_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
# Letzter Hinweis bzw. Fehler je Anbieter (fuer Cockpit und Profil)
modell_hinweise: dict[str, str] = {}
modell_fehler: dict[str, str] = {}


def _cache_schluessel(anbieter: str) -> tuple[str, str]:
    if anbieter == "anthropic":
        geheim = _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN") or ""
        ort = _env("ANTHROPIC_BASE_URL") or ""
    elif anbieter == "openai":
        geheim, ort = _env("OPENAI_API_KEY") or "", _env("OPENAI_BASE_URL") or OPENAI_BASE_URL
    else:
        geheim, ort = _env("OLLAMA_API_KEY") or "", _env("OLLAMA_BASE_URL") or OLLAMA_DEFAULT_BASE_URL
    return anbieter, ort + "|" + hashlib.sha256(geheim.encode()).hexdigest()[:12]


def _chat_tauglich(modell: str) -> bool:
    m = modell.lower()
    return not any(w in m for w in ("embed", "whisper", "tts", "dall-e", "moderation", "realtime",
                                     "transcribe", "audio", "image", "search"))


def _lade_modelle(anbieter: str, transport: Any = None) -> list[str]:
    """Fragt die Modellliste beim Anbieter ab (wirft bei Netz-/Zugangsfehlern)."""
    if anbieter == "anthropic":
        import anthropic

        return [m.id for m in anthropic.Anthropic().models.list(limit=100)]
    if anbieter == "openai":
        basis = (_env("OPENAI_BASE_URL") or OPENAI_BASE_URL).rstrip("/")
        kopf = {"Authorization": f"Bearer {_env('OPENAI_API_KEY') or ''}"}
    else:
        basis = (_env("OLLAMA_BASE_URL") or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        kopf = {"Authorization": f"Bearer {_env('OLLAMA_API_KEY') or 'ollama'}"}
    with httpx.Client(base_url=basis, timeout=httpx.Timeout(10.0, connect=5.0), transport=transport) as c:
        antwort = c.get("/v1/models", headers=kopf)
        ids: list[str] = []
        if antwort.status_code < 400:
            ids = [str(m.get("id")) for m in (antwort.json().get("data") or []) if m.get("id")]
        if not ids and anbieter == "ollama":
            # Aeltere Ollama-Server kennen nur die eigene API
            tags = c.get("/api/tags", headers=kopf)
            tags.raise_for_status()
            ids = [str(m.get("name") or m.get("model")) for m in (tags.json().get("models") or [])]
        elif not ids:
            antwort.raise_for_status()
    ids = [i for i in ids if i and _chat_tauglich(i)]
    if anbieter == "openai":
        ids = [i for i in ids if ist_openai_modell(i) or i.startswith("chatgpt")]
    return sorted(dict.fromkeys(ids))


def verfuegbare_modelle(anbieter: str | None = None, *, neu_laden: bool = False) -> list[str]:
    """Modelle, die der eingerichtete KI-Zugang wirklich anbietet (10 Minuten gemerkt).

    Leere Liste, wenn kein Zugang eingerichtet ist oder der Server nicht antwortet.
    """
    anbieter = anbieter or provider()
    if anbieter is None:
        return []
    schluessel = _cache_schluessel(anbieter)
    jetzt = time.monotonic()
    gemerkt = _modell_cache.get(schluessel)
    if gemerkt and not neu_laden and jetzt < gemerkt[0]:
        return list(gemerkt[1])
    try:
        modelle = _lade_modelle(anbieter)
        modell_fehler.pop(anbieter, None)
        gueltig_bis = jetzt + MODELL_CACHE_SEKUNDEN
    except Exception as e:
        log.info("Modellliste von %s nicht abrufbar: %s", anbieter, e)
        modell_fehler[anbieter] = f"Modellliste nicht abrufbar: {str(e)[:200]}"
        modelle, gueltig_bis = [], jetzt + 60
    _modell_cache[schluessel] = (gueltig_bis, modelle)
    return list(modelle)


def vergiss_modelle() -> None:
    _modell_cache.clear()


def _familie(modell: str) -> str:
    return re.split(r"[-:._/ ]", modell.lower(), maxsplit=1)[0]


def _norm(modell: str) -> str:
    """Vergleichsform: „openai/gpt-oss-120b“, „gpt-oss:120b-cloud“ und „gpt-oss:120b“ sind gleich."""
    n = modell.strip().lower().rsplit("/", 1)[-1]
    n = re.sub(r"[-:_. ]", "", n)
    return re.sub(r"(latest|cloud)$", "", n)


def _groesse(modell: str) -> float:
    """Parametergroesse aus dem Namen (z. B. 120b -> 120), 0 wenn unbekannt."""
    treffer = re.findall(r"(\d+(?:\.\d+)?)\s*b(?![a-z])", modell.lower())
    return max((float(z) for z in treffer), default=0.0)


def _fremdes_bezahlmodell(anbieter: str, modell: str) -> bool:
    """Ueber einen Ollama-/Gateway-Zugang angebotene Bezahlmodelle anderer Anbieter (z. B. gpt-4o-mini).

    Die nie als stillen Ersatz nehmen: sie laufen oft ueber ein fremdes Konto (ohne Guthaben).
    """
    return anbieter == "ollama" and (ist_openai_modell(modell) or ist_claude_modell(modell))


def _finde(wunsch: str, liste: list[str]) -> str | None:
    w = wunsch.strip().lower()
    for m in liste:
        if m.lower() == w:
            return m
    for m in liste:
        name, _, tag = m.lower().partition(":")
        # "qwen3" passt zu "qwen3:latest", "glm-5.3" zu "glm-5.3:cloud"
        if ":" not in w and name == w and tag in ("latest", "cloud", ""):
            return m
        if w.endswith(":latest") and name == w.removesuffix(":latest"):
            return m
    gesucht = _norm(wunsch)
    for m in liste:
        if gesucht and _norm(m) == gesucht:
            return m
    return None


_UMGEBUNG_MODELL = {"ollama": ("OLLAMA_MODEL", "LLM_MODEL"), "openai": ("OPENAI_MODEL", "LLM_MODEL"),
                    "anthropic": ("ANTHROPIC_MODEL",)}


def waehle_ersatz(anbieter: str, wunsch: str, liste: list[str]) -> str | None:
    """Bestes verfuegbares Ausweichmodell.

    Reihenfolge: das in der Umgebung eingestellte Modell (z. B. OLLAMA_MODEL) → bei Ollama gpt-oss:120b →
    bevorzugte Modelle derselben Familie → dieselbe Familie → bevorzugte Modelle → das groesste
    verfuegbare Modell. Bezahlmodelle fremder Anbieter (gpt-4o-mini ueber einen Gateway) nur als
    allerletzte Moeglichkeit.
    """
    if not liste:
        return None
    eigene = [m for m in liste if not _fremdes_bezahlmodell(anbieter, m)] or liste
    for name in _UMGEBUNG_MODELL.get(anbieter, ()):
        aus_umgebung = _env(name)
        treffer = _finde(aus_umgebung, eigene) if aus_umgebung else None
        if treffer and treffer.lower() != wunsch.strip().lower():
            return treffer
    if anbieter == "ollama" and (gross := _finde(BEVORZUGTE_MODELLE["ollama"][0], eigene)):
        return gross  # der Nutzer arbeitet mit gpt-oss 120b: wenn es da ist, immer das
    bevorzugt = [b for b in BEVORZUGTE_MODELLE.get(anbieter, ()) if _finde(b, eigene)]
    familie = _familie(wunsch)
    for b in bevorzugt:
        if _familie(b) == familie:
            return _finde(b, eigene)
    gleiche = sorted((m for m in eigene if _familie(m) == familie), key=lambda m: (_groesse(m), m), reverse=True)
    if gleiche:
        return gleiche[0]
    if bevorzugt:
        return _finde(bevorzugt[0], eigene)
    return max(eigene, key=lambda m: (_groesse(m), -eigene.index(m)))


def pruefe_modell(anbieter: str, modell: str | None, *, ausser: tuple[str, ...] = ()) -> tuple[str, str | None]:
    """(Modell, das wirklich angefragt wird, Hinweis oder None).

    Gibt es das gewuenschte Modell beim Anbieter nicht, wird ein verfuegbares
    gewaehlt und ein verstaendlicher Hinweis erzeugt. Ist die Modellliste nicht
    abrufbar, bleibt es beim Wunsch.
    """
    wunsch = effektives_modell(anbieter, modell)
    liste = [m for m in verfuegbare_modelle(anbieter) if m not in ausser]
    if not liste:
        return wunsch, None
    treffer = _finde(wunsch, liste) if wunsch not in ausser else None
    if treffer:
        if not ausser:
            modell_hinweise.pop(anbieter, None)
        return treffer, None
    ersatz = waehle_ersatz(anbieter, wunsch, liste) or wunsch
    hinweis = (
        f"Das Modell „{wunsch}“ gibt es bei deinem KI-Zugang nicht – ich nutze stattdessen „{ersatz}“. "
        "Unter Einstellungen → Konten kannst du ein verfügbares Modell fest auswählen."
    )
    if modell_hinweise.get(anbieter) != hinweis:
        log.warning(hinweis)
    modell_hinweise[anbieter] = hinweis
    return ersatz, hinweis


def modell_festschreiben(profil: Any, pfad: Any = None) -> str | None:
    """Fehlt das Profil-Modell beim Anbieter, den gewaehlten Ersatz einmal ins Profil schreiben.

    Danach entsteht kein Hinweis mehr. Nur wenn der Ersatz beim selben Anbieter laeuft (sonst
    koennte ein Modellname den Anbieter wechseln, z. B. zu einem OpenAI-Konto ohne Guthaben).
    """
    from ..config import ermittle_profil_pfad, speichere_profil

    anbieter, ziel, hinweis = modell_fuer(profil.bewertung.modell)
    if not (anbieter and ziel and hinweis) or ziel == profil.bewertung.modell or provider(ziel) != anbieter:
        return None
    profil.bewertung.modell = ziel
    speichere_profil(profil, pfad or ermittle_profil_pfad())
    modell_hinweise.pop(anbieter, None)
    log.warning("KI-Modell fest auf %s gestellt (das bisherige gab es nicht).", ziel)
    return ziel


def modell_fuer(modell: str | None) -> tuple[str | None, str | None, str | None]:
    """(Anbieter, geprueftes Modell, Hinweis) fuer das Profil-Modell."""
    anbieter = provider(modell)
    if anbieter is None:
        return None, None, None
    ziel, hinweis = pruefe_modell(anbieter, modell)
    return anbieter, ziel, hinweis


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
        try:
            antwort = client.messages.create(**params)
        except Exception as e:
            if type(e).__name__ == "NotFoundError" or _modell_fehlt(getattr(e, "status_code", 0) or 0, str(e)):
                raise ModellNichtGefunden(modell, str(e)) from e
            raise
    if antwort.stop_reason == "refusal":
        raise LLMNichtVerfuegbar(f"Anfrage abgelehnt: {antwort.stop_details}")
    text = "\n".join(b.text for b in antwort.content if b.type == "text").strip()
    if not text:
        raise LLMNichtVerfuegbar("Leere Antwort vom Modell")
    return text


def _chat_einmal(
    anbieter: str, ziel: str, *, system: str, nutzer: str, max_tokens: int,
    json_schema: dict | None, effort: str,
) -> str:
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


def mit_ausweichmodell(anbieter: str, modell: str | None, ziel: str, anfrage: Callable[[str], str]) -> tuple[str, str]:
    """Fuehrt `anfrage(ziel)` aus; kennt der Server das Modell nicht, einmal mit Ersatz.

    Liefert (Antworttext, tatsaechlich genutztes Modell).
    """
    try:
        return anfrage(ziel), ziel
    except ModellNichtGefunden:
        vergiss_modelle()
        ersatz, _ = pruefe_modell(anbieter, modell, ausser=(ziel,))
        if ersatz == ziel:
            raise
        log.warning("Modell %s nicht gefunden - neuer Versuch mit %s.", ziel, ersatz)
        return anfrage(ersatz), ersatz


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
    ziel, _ = pruefe_modell(anbieter, modell)
    text, _ = mit_ausweichmodell(
        anbieter, modell, ziel,
        lambda m: _chat_einmal(anbieter, m, system=system, nutzer=nutzer, max_tokens=max_tokens,
                               json_schema=json_schema, effort=effort),
    )
    return text


def json_aus_text(text: str) -> dict:
    """JSON-Objekt aus einer Modellantwort, auch mit Markdown-Zaun oder Vorrede."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    zaun = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if zaun:
        try:
            return json.loads(zaun.group(1))
        except json.JSONDecodeError:
            pass
    start, ende = text.find("{"), text.rfind("}")
    if start >= 0 and ende > start:
        return json.loads(text[start:ende + 1])
    raise LLMNichtVerfuegbar(f"Antwort ist kein JSON: {text[:120]}")


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
        self.modell, self.hinweis = pruefe_modell(self.anbieter, profil.bewertung.modell)
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

        def anfrage(modell: str) -> str:
            self.modell = modell
            if self.anbieter == "anthropic":
                return self._anthropic_bewerten(inhalt)
            if self.anbieter == "openai":
                return self._openai_bewerten(inhalt)
            return self._ollama_bewerten(inhalt)

        text, self.modell = mit_ausweichmodell(self.anbieter, self.profil.bewertung.modell, self.modell, anfrage)
        daten = json_aus_text(text)

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
    fehler: list[str] | None = None,
) -> list[Score]:
    """Bewertet alle Jobs. Faellt pro Job auf die Heuristik zurueck, wenn das LLM ausfaellt.

    `fortschritt(erledigt, gesamt)` wird nach jedem Job aufgerufen (fuer die UI).
    Gruende fuer das Ausweichen auf die Heuristik landen in `fehler` (fuer die
    Lauf-Zusammenfassung). Scheitert das LLM dreimal in Folge, wird der Rest
    direkt heuristisch bewertet, statt jeden Job einzeln scheitern zu lassen.
    """
    fehler = fehler if fehler is not None else []
    bewerter: LLMBewerter | None = None
    if mit_llm:
        try:
            bewerter = LLMBewerter(profil)
            if bewerter.hinweis and bewerter.hinweis not in fehler:
                fehler.append(bewerter.hinweis)
        except LLMNichtVerfuegbar as e:
            log.warning("LLM-Bewertung nicht aktiv (%s) - nutze Heuristik.", e)
            if client_verfuegbar():
                fehler.append(f"KI-Bewertung nicht aktiv: {e}")

    ergebnisse: list[Score] = []
    in_folge = 0
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
                in_folge = 0
            except Exception as e:  # Netzwerk, Rate-Limit, kaputtes JSON
                log.warning("LLM-Bewertung fuer %s fehlgeschlagen (%s) - Heuristik.", job.ref, e)
                ergebnisse.append(basis)
                in_folge += 1
                meldung = f"KI-Bewertung fehlgeschlagen ({type(e).__name__}): {str(e)[:200]}"
                if meldung not in fehler:
                    fehler.append(meldung)
                if in_folge >= 3 or isinstance(e, ModellNichtGefunden):
                    fehler.append("KI-Bewertung für den Rest dieses Laufs ausgesetzt – Stichwort-Bewertung.")
                    bewerter = None
        if fortschritt:
            fortschritt(nummer, len(jobs))
    return ergebnisse
