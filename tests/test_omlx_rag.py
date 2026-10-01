import json

import bcrypt
import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

import app
import ingest
import providers
import rag


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "ollama"])
def test_selected_models_cover_ingestion_retrieval_sources_and_deletion(
    tmp_path, monkeypatch, protocol
):
    original_client = httpx.Client
    providers._client.cache_clear()

    def handler(request):
        body = json.loads(request.content)
        if request.url.path in {"/v1/embeddings", "/api/embed"}:
            assert body["model"] == "embed-selected"
            return httpx.Response(
                200,
                json={"embeddings": [[1.0, 0.0] for _ in body["input"]]}
                if protocol == "ollama"
                else {
                    "data": [
                        {"index": i, "embedding": [1.0, 0.0]}
                        for i, _ in enumerate(body["input"])
                    ]
                },
            )
        assert body["model"] == "chat-selected"
        assert request.url.path == (
            "/v1/messages"
            if protocol == "anthropic"
            else "/api/chat"
            if protocol == "ollama"
            else "/v1/chat/completions"
        )
        if not body["stream"]:
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "Quel est le code ?"}]}
                if protocol == "anthropic"
                else {"message": {"content": "Quel est le code ?"}, "done": True}
                if protocol == "ollama"
                else {"choices": [{"message": {"content": "Quel est le code ?"}}]},
            )
        assert "AZUR-742" in body["messages"][0]["content"]
        if protocol == "ollama":
            return httpx.Response(
                200,
                text='{"message":{"content":"AZUR-742 [controle.txt]"},"done":true}\n',
            )
        event = (
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "AZUR-742 [controle.txt]"},
            }
            if protocol == "anthropic"
            else {"choices": [{"delta": {"content": "AZUR-742 [controle.txt]"}}]}
        )
        terminal = (
            "data: [DONE]\n\n"
            if protocol == "openai"
            else 'data: {"type":"message_stop"}\n\n'
        )
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n" + terminal)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    monkeypatch.setattr(ingest, "CHROMA_PATH", tmp_path)
    settings = providers.ModelSettings(
        protocol=protocol,
        llm_model="chat-selected",
        embed_model="embed-selected",
        base_url="http://test:11434"
        if protocol == "ollama"
        else "http://test:11435/v1",
    )
    assert (
        ingest.ingest_document(
            b"Le code du projet est AZUR-742.", "controle.txt", settings
        )
        == 1
    )
    response = rag.answer_question(
        "Et le code ?",
        [{"role": "user", "content": "Quel projet ?"}],
        similarity_cutoff=0,
        settings=settings,
    )
    assert "".join(response.chunks) == "AZUR-742 [controle.txt]"
    assert response.sources[0].document == "controle.txt"
    assert ingest.list_documents(settings)[0].source == "controle.txt"
    ingest.delete_document("controle.txt", settings)
    assert not ingest.list_documents(settings)


def test_web_selects_available_models_and_switches_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCMIND_PROJECTS_PATH", str(tmp_path / "projects"))
    monkeypatch.setattr(
        app,
        "list_models",
        lambda settings: providers.ModelCatalog(
            ("chat-a", "chat-b"), ("embed-a", "embed-b")
        ),
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "credentials": {
                    "usernames": {
                        "test": {
                            "password": bcrypt.hashpw(
                                b"test", bcrypt.gensalt(rounds=4)
                            ).decode()
                        }
                    }
                },
                "cookie": {"name": "test", "expiry_days": 1},
            }
        )
    )

    def answer(question, history, top_k, cutoff, settings):
        return rag.RagResponse(
            iter([f"{settings.protocol} {settings.llm_model} {settings.embed_model}"]),
            [],
        )

    monkeypatch.setattr(app, "answer_question", answer)
    with TestClient(app.create_app(config)) as client:
        session = client.post(
            "/api/login", json={"username": "test", "password": "test"}
        ).json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        conversation = client.post(
            "/api/projects/general/conversations", headers=headers
        ).json()["id"]
        for protocol in ("openai", "anthropic"):
            settings = {
                "protocol": protocol,
                "llm_model": "chat-b",
                "embed_model": "embed-b",
                "project_id": "general",
            }
            catalog = client.post("/api/models", headers=headers, json=settings).json()
            assert catalog == {
                "chat": ["chat-a", "chat-b"],
                "embedding": ["embed-a", "embed-b"],
            }
            response = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "question": "Question",
                    "conversation_id": conversation,
                    "settings": settings,
                },
            )
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[1]["text"] == f"{protocol} chat-b embed-b"
