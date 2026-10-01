import json

import httpx
import pytest

import providers


def install_transport(monkeypatch, handler):
    original = httpx.Client
    providers._client.cache_clear()
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_discovery_separates_chat_embeddings_and_rerankers(monkeypatch):
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11435/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "Nail-Qwen3.6"},
                    {"id": "Qwen3-Embedding-4B-4bit-DWQ"},
                    {"id": "bge-reranker"},
                    {"id": "custom", "model_type": "embedding"},
                ]
            },
        )

    install_transport(monkeypatch, handler)
    models = providers.list_models(providers.ModelSettings())
    assert models.chat == ("Nail-Qwen3.6",)
    assert models.embedding == ("Qwen3-Embedding-4B-4bit-DWQ", "custom")


@pytest.mark.parametrize(
    "protocol,path", [("openai", "/v1/chat/completions"), ("anthropic", "/v1/messages")]
)
def test_completion_and_stream_use_selected_protocol(monkeypatch, protocol, path):
    def handler(request):
        assert request.url.path == path
        payload = json.loads(request.content)
        assert payload["model"] == "Nail-Qwen3.6"
        assert payload["max_tokens"] == 256
        if protocol == "anthropic":
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert payload["thinking"] == {"type": "disabled"}
        else:
            assert payload["enable_thinking"] is False
        if payload["stream"]:
            events = (
                [
                    {"choices": [{"delta": {"reasoning_content": "secret"}}]},
                    {"choices": [{"delta": {"content": "Bonjour"}}]},
                ]
                if protocol == "openai"
                else [
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "secret"},
                    },
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Bonjour"},
                    },
                ]
            )
            return httpx.Response(
                200,
                text="".join(f"data: {json.dumps(e)}\n\n" for e in events)
                + ("data: [DONE]\n\n" if protocol == "openai" else 'data: {"type":"message_stop"}\n\n'),
            )
        return httpx.Response(
            200,
            json=(
                {"choices": [{"message": {"content": "Bonjour"}}]}
                if protocol == "openai"
                else {"content": [{"type": "text", "text": "Bonjour"}]}
            ),
        )

    install_transport(monkeypatch, handler)
    llm = providers.LocalLLM(
        providers.ModelSettings(
            protocol=protocol, llm_model="Nail-Qwen3.6", max_tokens=256
        )
    )
    assert llm.complete("Bonjour ?").text == "Bonjour"
    assert "".join(r.delta for r in llm.stream_complete("Bonjour ?")) == "Bonjour"


def test_embeddings_use_openai_endpoint_and_restore_response_order(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content)["input"] == ["premier", "second"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    install_transport(monkeypatch, handler)
    embed = providers.embedding_model(
        providers.ModelSettings(protocol="anthropic", embed_model="embedding")
    )
    assert embed.get_text_embedding_batch(["premier", "second"]) == [
        [1.0, 0.0],
        [0.0, 1.0],
    ]


def test_server_error_is_presentable_without_leaking_response(monkeypatch):
    install_transport(
        monkeypatch, lambda request: httpx.Response(401, text="private-token")
    )
    with pytest.raises(providers.ProviderError, match="401") as error:
        providers.list_models(providers.ModelSettings())
    assert "private-token" not in str(error.value)


def test_stream_error_is_not_silently_accepted(monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, text='data: {"error":{"message":"failed"}}\n\n'
        ),
    )
    with pytest.raises(providers.ProviderError):
        list(
            providers.LocalLLM(
                providers.ModelSettings(llm_model="chat")
            ).stream_complete("question")
        )


def test_empty_stream_is_reported_instead_of_an_empty_answer(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, text="data: [DONE]\n\n"))
    with pytest.raises(providers.ProviderError, match="texte"):
        list(providers.LocalLLM(providers.ModelSettings(llm_model="chat")).stream_complete("question"))


def test_collection_keeps_legacy_data_and_isolates_embedding_models(
    tmp_path, monkeypatch
):
    import ingest

    monkeypatch.setattr(ingest, "CHROMA_PATH", tmp_path)
    legacy = ingest._collection()
    legacy.add(ids=["old"], embeddings=[[1.0, 0.0]], documents=["legacy"])
    first = providers.ModelSettings(embed_model="embed-a")
    second = providers.ModelSettings(embed_model="embed-b")
    new = ingest._collection(first)
    new.add(ids=["new"], embeddings=[[0.0, 1.0]], documents=["new"])
    assert ingest._collection(second).count() == 0
    assert (
        ingest._collection(
            providers.ModelSettings(protocol="anthropic", embed_model="embed-a")
        ).count()
        == 1
    )
    assert ingest._collection().get()["documents"] == ["legacy"]
