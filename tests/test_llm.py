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
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    # Kein Netz: ohne eigene Vorgabe kennt der Anbieter keine Modellliste
    llm.vergiss_modelle()
    llm.modell_hinweise.clear()
    monkeypatch.setattr(llm, "_lade_modelle", lambda anbieter, transport=None: [])


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


def test_openai_key_nutzt_chatgpt_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.provider() == "openai"
    assert llm.provider("gpt-4o-mini") == "openai"


def test_openai_chat_sendet_strukturierten_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    gespeichert = {}

    def transport(request: httpx.Request) -> httpx.Response:
        gespeichert["headers"] = request.headers
        gespeichert["body"] = json.loads(request.content)
        return httpx.Response(200, json=JSON_ANTWORT)

    client = llm.OpenAIClient(transport=httpx.MockTransport(transport))
    assert client.chat(modell="gpt-4o-mini", system="System", messages=[], max_tokens=20, json_schema=llm.SCHEMA)
    assert gespeichert["headers"]["Authorization"] == "Bearer sk-test"
    assert gespeichert["body"]["response_format"]["type"] == "json_schema"


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


def test_gpt_oss_laeuft_ueber_ollama(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    assert llm.provider("gpt-oss:120b") == "ollama"
    assert llm.provider("gpt-4o-mini") == "openai"


# --------------------------------------------------------------------------- #
# Modell nicht vorhanden (model_not_found)
# --------------------------------------------------------------------------- #

OLLAMA_LISTE = ["glm-5.2", "kimi-k3", "qwen3:latest"]


def _modelle(monkeypatch, liste):
    llm.vergiss_modelle()
    monkeypatch.setattr(llm, "_lade_modelle", lambda anbieter, transport=None: list(liste))


def test_lade_modelle_parser(monkeypatch):
    import importlib

    modul = importlib.reload(llm)
    try:
        monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")

        def v1(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer ollama-test"
            return httpx.Response(200, json={"data": [{"id": "glm-5.2"}, {"id": "nomic-embed-text"}, {"id": "kimi-k3"}]})

        assert modul._lade_modelle("ollama", transport=httpx.MockTransport(v1)) == ["glm-5.2", "kimi-k3"]

        def nur_tags(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"models": [{"name": "qwen3:latest"}, {"name": "llama3.3:70b"}]})

        assert modul._lade_modelle("ollama", transport=httpx.MockTransport(nur_tags)) == ["llama3.3:70b", "qwen3:latest"]

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        def openai(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "text-embedding-3-small"},
                                                      {"id": "whisper-1"}, {"id": "gpt-4o"}]})

        assert modul._lade_modelle("openai", transport=httpx.MockTransport(openai)) == ["gpt-4o", "gpt-4o-mini"]
    finally:
        importlib.reload(llm)


def test_pruefe_modell_waehlt_ersatz_aus_gleicher_familie(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, OLLAMA_LISTE)
    ziel, hinweis = llm.pruefe_modell("ollama", "glm-5.3-flash")
    assert ziel == "glm-5.2"
    assert "glm-5.3-flash" in hinweis and "glm-5.2" in hinweis
    assert llm.modell_hinweise["ollama"] == hinweis


def test_pruefe_modell_vorhanden_ohne_hinweis(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, OLLAMA_LISTE)
    assert llm.pruefe_modell("ollama", "kimi-k3") == ("kimi-k3", None)
    # Tag ":latest" wird toleriert
    assert llm.pruefe_modell("ollama", "qwen3") == ("qwen3:latest", None)


def test_pruefe_modell_ohne_liste_bleibt_beim_wunsch(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, [])
    assert llm.pruefe_modell("ollama", "glm-5.3-flash") == ("glm-5.3-flash", None)


def test_chat_wiederholt_bei_model_not_found(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    # Liste noch ohne Fehler-Info: zuerst wird der Wunsch angefragt
    _modelle(monkeypatch, [])
    angefragt = []

    def transport(request: httpx.Request) -> httpx.Response:
        modell = json.loads(request.content)["model"]
        angefragt.append(modell)
        if modell == "glm-5.3-flash":
            return httpx.Response(404, json={"error": {
                "message": "The model glm-5.3-flash does not exist or you do not have access to it.",
                "type": "invalid_request_error", "code": "model_not_found"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hallo"}}]})

    echter_client = llm.OllamaClient
    monkeypatch.setattr(llm, "OllamaClient", lambda: echter_client(transport=httpx.MockTransport(transport)))
    # Nach dem 404 wird die Liste neu geladen - jetzt kennt sie den Server
    aufrufe = {"n": 0}

    def liste(anbieter, transport=None):
        aufrufe["n"] += 1
        return [] if aufrufe["n"] == 1 else OLLAMA_LISTE

    monkeypatch.setattr(llm, "_lade_modelle", liste)
    assert llm.chat("glm-5.3-flash", system="S", nutzer="N") == "Hallo"
    assert angefragt == ["glm-5.3-flash", "glm-5.2"]


def test_chat_ohne_ersatz_meldet_klar(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, [])

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "model 'x' not found", "code": "model_not_found"}})

    echter_client = llm.OllamaClient
    monkeypatch.setattr(llm, "OllamaClient", lambda: echter_client(transport=httpx.MockTransport(transport)))
    with pytest.raises(llm.ModellNichtGefunden, match="Einstellungen → Konten"):
        llm.chat("glm-5.3-flash", system="S", nutzer="N")


def test_llmbewerter_nutzt_verfuegbares_modell(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    monkeypatch.setenv("OLLAMA_MODEL", "glm-5.3-flash")
    _modelle(monkeypatch, OLLAMA_LISTE)
    gesehen = []

    class StubOllama:
        def chat(self, **kwargs):
            gesehen.append(kwargs["modell"])
            return "```json\n" + JSON_ANTWORT["choices"][0]["message"]["content"] + "\n```"

    monkeypatch.setattr(llm, "OllamaClient", StubOllama)
    fehler: list[str] = []
    scores = llm.bewerte_jobs([Job(ref="X-1", titel="Python Entwickler", arbeitgeber="A", beschreibung="Python")],
                              _profil(tmp_path), fehler=fehler)
    assert gesehen == ["glm-5.2"]
    assert scores[0].bewerter == "llm:glm-5.2"
    assert any("glm-5.3-flash" in f for f in fehler)


def test_bewerte_jobs_setzt_ki_nach_modellfehler_aus(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, [])
    aufrufe = []

    class KaputtesModell:
        def chat(self, **kwargs):
            aufrufe.append(kwargs["modell"])
            raise llm.ModellNichtGefunden(kwargs["modell"])

    monkeypatch.setattr(llm, "OllamaClient", KaputtesModell)
    jobs = [Job(ref=f"X-{i}", titel="Python Entwickler", arbeitgeber="A", beschreibung="Python") for i in range(4)]
    fehler: list[str] = []
    scores = llm.bewerte_jobs(jobs, _profil(tmp_path), fehler=fehler)
    assert len(scores) == 4 and all(s.bewerter == "heuristik" for s in scores)
    assert len(aufrufe) == 1
    assert any("ausgesetzt" in f for f in fehler)


def test_browser_llm_nutzt_geprueftes_modell(monkeypatch):
    pytest.importorskip("browser_use")
    from bewerbungsagent.bewerben import browser

    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test")
    _modelle(monkeypatch, OLLAMA_LISTE)
    assert browser._browser_llm("glm-5.3-flash").model == "glm-5.2"
    assert browser._browser_llm("glm-5.3-flash", ausser=("glm-5.2",)).model == "kimi-k3"
