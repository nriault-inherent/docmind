"""HTTP contract tests: no real credentials or running model required."""

import json

import httpx
import pytest
from test_providers import install_transport

import providers
from app import SettingsInput


def test_generic_defaults_and_legacy_environment(monkeypatch):
    monkeypatch.setenv("OMLX_BASE_URL", "http://legacy:11435/v1")
    monkeypatch.setenv("DOCMIND_BASE_URL", "https://gateway.example/api")
    monkeypatch.setenv("DOCMIND_LLM_MODEL", "chosen")
    monkeypatch.setenv("DOCMIND_API_KEY", "test-key")
    settings = providers.default_settings()
    assert settings.base_url == "https://gateway.example/api"
    assert settings.llm_model == "chosen"
    assert settings.api_key == "test-key"


def test_remote_openai_does_not_receive_omlx_extensions(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert "enable_thinking" not in body
        assert "x-api-key" not in request.headers
        assert "anthropic-version" not in request.headers
        assert request.headers["authorization"] == "Bearer test-key"
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert body["max_completion_tokens"] == 500
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    install_transport(monkeypatch, handler)
    settings = providers.ModelSettings(
        base_url="https://gateway.example/v1",
        llm_model="chat",
        api_key="test-key",
        llm_options={
            "max_tokens": None,
            "temperature": None,
            "max_completion_tokens": 500,
        },
    )
    assert providers.LocalLLM(settings).complete("question").text == "ok"


def test_separate_embeddings_use_their_own_url_and_key(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://embeddings.example/v1/embeddings"
        assert request.headers["authorization"] == "Bearer embedding-test-key"
        assert "chat-test-key" not in str(request.headers)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )

    install_transport(monkeypatch, handler)
    settings = providers.ModelSettings(
        protocol="anthropic",
        base_url="https://chat.example/v1",
        api_key="chat-test-key",
        embed_model="embed",
        embed_base_url="https://embeddings.example/v1",
        embed_api_key="embedding-test-key",
    )
    assert providers.embedding_model(settings).get_text_embedding("text") == [1.0, 0.0]
    other = providers.ModelSettings(
        base_url="https://other-chat.example/v1",
        embed_model="embed",
        embed_base_url=settings.embed_base_url,
    )
    assert settings.collection_name == other.collection_name


def test_ollama_selection_is_not_overridden_by_server_defaults():
    settings = SettingsInput(
        protocol="ollama",
        base_url="http://ollama.example:11434",
        llm_model="selected",
        embed_model="selected-embed",
    ).resolve()
    assert settings.base_url == "http://ollama.example:11434"
    assert settings.embed_model == "selected-embed"


def test_ollama_native_chat_embeddings_catalog_and_stream(monkeypatch):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "chat"}, {"name": "embed"}]}
            )
        body = json.loads(request.content)
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})
        assert request.url.path == "/api/chat"
        assert body["model"] == "chat"
        assert body["options"]["num_predict"] == 512
        if body["stream"]:
            return httpx.Response(
                200,
                text='{"message":{"content":"ok"},"done":false}\n{"message":{"content":""},"done":true}\n',
            )
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

    install_transport(monkeypatch, handler)
    settings = providers.ModelSettings(
        protocol="ollama",
        base_url="http://ollama.example:11434",
        llm_model="chat",
        embed_model="embed",
        max_tokens=512,
    )
    assert providers.list_models(settings).embedding == ("embed",)
    assert providers.LocalLLM(settings).complete("question").text == "ok"
    assert (
        "".join(
            r.delta for r in providers.LocalLLM(settings).stream_complete("question")
        )
        == "ok"
    )
    assert providers.embedding_model(settings).get_text_embedding("text") == [1.0, 0.0]


def test_ollama_truncated_stream_is_rejected(monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, text='{"message":{"content":"partial"},"done":false}\n'
        ),
    )
    settings = providers.ModelSettings(
        protocol="ollama", base_url="http://local:11434", llm_model="chat"
    )
    with pytest.raises(providers.ProviderError, match="interrompu"):
        list(providers.LocalLLM(settings).stream_complete("question"))


def test_server_credentials_are_not_forwarded_to_another_endpoint(monkeypatch):
    monkeypatch.setenv("DOCMIND_BASE_URL", "https://original.example/v1")
    monkeypatch.setenv("DOCMIND_API_KEY", "server-test-key")
    monkeypatch.setenv("DOCMIND_EMBED_BASE_URL", "https://original-embed.example/v1")
    monkeypatch.setenv("DOCMIND_EMBED_API_KEY", "server-embed-test-key")
    settings = SettingsInput(
        base_url="https://new.example/v1", embed_base_url="https://new-embed.example/v1"
    ).resolve()
    assert settings.api_key == ""
    assert settings.embed_api_key == ""


def test_legacy_omlx_key_is_not_used_for_native_ollama(monkeypatch):
    monkeypatch.setenv("DOCMIND_PROTOCOL", "ollama")
    monkeypatch.delenv("DOCMIND_API_KEY", raising=False)
    monkeypatch.setenv("OMLX_API_KEY", "omlx-test-key")
    assert providers.default_settings().api_key == ""
