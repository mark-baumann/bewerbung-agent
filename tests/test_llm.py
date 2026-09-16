"""Tests fuer die LLM-Anbieter-Auswahl (Anthropic vs. Ollama)."""

import json
from pathlib import Path

import httpx
import pytest

from bewerbungsagent.config import lade_profil
from bewerbungsagent.models import Job
from bewerbungsagent.scoring import llm

JSON_ANTWORT = {
    "choices": [
        {
            "message": {
                "content": (
                    '{"passung": 82, "sentiment": 60, "treffer": ["Python"], '
                    '"gruen": [], "rot": [], "ausgeschlossen": false, '
                    '"ausschlussgrund": null, '
                    '"begruendung": "Starke fachliche Passung."}'
                )
            }
        }
    ]
}


def _profil(tmp_path: Path):
    quelle = Path("config/profil.example.yaml").read_text(encoding="utf-8")
    ziel = tmp_path / "profil.yaml"
    ziel.write_text(quelle, encoding="utf-8")
    return lade_profil(ziel)


@pytest.fixture(autouse=True)
def _ohne_keys(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_ohne_key_kein_provider():
    assert llm.provider() is None
    assert llm.client_verfuegbar() is False


def test_anthropic_key_nutzt_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm.provider() == "anthropic"
    assert llm.provider("claude-opus-5") == "anthropic"
    assert llm.provider("glm-5.3-flash") == "anthropic"
    assert llm.client_verfuegbar() is True


def test_ollama_key_nutzt_ollama(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    assert llm.provider() == "ollama"
    assert llm.provider("glm-5.3-flash") == "ollama"
    assert llm.provider("claude-opus-5") == "ollama"


def test_ollama_base_url_allein_reicht(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert llm.provider("qwen3:latest") == "ollama"


def test_beide_keys_modell_entscheidet(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    assert llm.provider("claude-3-7-sonnet-20250219") == "anthropic"
    assert llm.provider("glm-5.3-flash") == "ollama"


def test_ollama_chat_sendet_request(monkeypatch):
    gespeichert = {}

    def transport(request: httpx.Request) -> httpx.Response:
        gespeichert["url"] = str(request.url)
        gespeichert["headers"] = request.headers
        gespeichert["body"] = request.content.decode()
        return httpx.Response(200, json=JSON_ANTWORT)

    client = llm.OllamaClient(transport=httpx.MockTransport(transport))
    text = client.chat(
        modell="glm-5.3-flash",
        system="Systemtext",
        messages=[{"role": "user", "content": "Hallo"}],
        max_tokens=4000,
        json_schema=llm.SCHEMA,
    )
    assert gespeichert["url"].endswith("/v1/chat/completions")
    assert gespeichert["headers"]["Authorization"] == "Bearer ollama"
    body = json.loads(gespeichert["body"])
    assert body["model"] == "glm-5.3-flash"
    assert any(m["role"] == "system" for m in body["messages"])
    assert any("passung" in m.get("content", "") for m in body["messages"])
    assert "passung" in text


def test_ollama_chat_ersetzt_claude_modell(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "glm-5.3-flash")
    gespeichert = {}

    def transport(request: httpx.Request) -> httpx.Response:
        gespeichert["body"] = request.content.decode()
        return httpx.Response(200, json=JSON_ANTWORT)

    client = llm.OllamaClient(transport=httpx.MockTransport(transport))
    client.chat(
        modell="claude-opus-5",
        system="Systemtext",
        messages=[{"role": "user", "content": "Hallo"}],
        max_tokens=4000,
    )
    assert json.loads(gespeichert["body"])["model"] == "glm-5.3-flash"


def test_llmbewerter_ollama_bewerte(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    monkeypatch.setenv("OLLAMA_MODEL", "glm-5.3-flash")

    class StubOllama:
        def chat(self, **kwargs):
            return JSON_ANTWORT["choices"][0]["message"]["content"]

    monkeypatch.setattr(llm, "OllamaClient", StubOllama)
    bewerter = llm.LLMBewerter(_profil(tmp_path))
    assert bewerter.anbieter == "ollama"
    # claude-opus-5 aus dem Beispielprofil wird auf OLLAMA_MODEL umgestellt
    assert bewerter.modell == "glm-5.3-flash"

    job = Job(ref="X-1", titel="Python Entwickler", arbeitgeber="Beispiel AG",
              beschreibung="Wir suchen Python-Entwickler.")
    score = bewerter.bewerte(job)
    assert score.passung == 82.0
    assert score.sentiment == 60.0
    assert score.treffer == ["Python"]
    assert score.bewerter == "llm:glm-5.3-flash"


def test_llmbewerter_ohne_key_schlaegt_fehl(tmp_path):
    with pytest.raises(llm.LLMNichtVerfuegbar):
        llm.LLMBewerter(_profil(tmp_path))