import asyncio
import json
from dataclasses import asdict

import bcrypt
import pytest
import yaml
from fastapi.testclient import TestClient

import app as web
from ingest import IndexedDocument, IngestionError
from providers import ModelCatalog, ProviderError
from rag import RagResponse, Source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCMIND_PROJECTS_PATH", str(tmp_path / "projects"))
    config = {"credentials": {"usernames": {"nico": {"name": "Nicolas", "password": bcrypt.hashpw(b"correct", bcrypt.gensalt(rounds=4)).decode()}}}, "cookie": {"name": "docmind", "expiry_days": 1, "key": "existing"}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    assert hasattr(web, "create_app"), "Le serveur web doit remplacer Streamlit"
    with TestClient(web.create_app(path)) as value:
        yield value


def login(client):
    response = client.post("/api/login", json={"username": "nico", "password": "correct"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def chat_payload(client, headers, question="Pourquoi ?"):
    conversation = client.post("/api/projects/general/conversations", headers=headers).json()
    return {"question": question, "conversation_id": conversation["id"], "settings": {"project_id": "general"}}


def test_login_session_logout_and_cookie_flags(client):
    headers = login(client)
    cookie = client.cookies.get("docmind")
    assert cookie and "nico" not in cookie
    session = client.get("/api/session")
    assert session.json()["name"] == "Nicolas"
    assert "api_key" not in session.json()["settings"]
    assert client.post("/api/logout", headers=headers).status_code == 200
    client.cookies.set("docmind", cookie)
    assert client.get("/api/session").status_code == 401


def test_cookie_http_only_same_site_and_secure_on_https(client):
    response = client.post("/api/login", json={"username": "nico", "password": "correct"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    with TestClient(client.app, base_url="https://docmind.example") as secure:
        response = secure.post("/api/login", json={"username": "nico", "password": "correct"})
        assert "secure" in response.headers["set-cookie"].lower()


def test_login_rejects_cross_origin_and_wrong_credentials(client):
    assert client.post("/api/login", headers={"Origin": "https://elsewhere.example"}, json={"username": "nico", "password": "correct"}).status_code == 403
    assert client.post("/api/login", json={"username": "nico", "password": "wrong"}).status_code == 401


@pytest.mark.parametrize("path,payload", [("/api/models", {}), ("/api/documents/list", {}), ("/api/documents/delete", {"source": "notes.txt", "settings": {}}), ("/api/chat", {"question": "Pourquoi ?", "settings": {}}), ("/api/logout", {})])
def test_routes_require_session_and_csrf(client, path, payload):
    assert client.post(path, json=payload).status_code == 401
    login(client)
    assert client.post(path, json=payload).status_code == 403


def test_session_expired_returns_401(client, monkeypatch):
    login(client)
    monkeypatch.setattr("auth.time.time", lambda: 10**15)
    assert client.get("/api/session").status_code == 401


def test_invalid_settings_do_not_echo_provider_key(client):
    headers = login(client)
    response = client.post("/api/models", headers=headers, json={"protocol": "invalid", "api_key": "private-token"})
    assert response.status_code == 422
    assert "private-token" not in response.text


def test_model_catalog_and_provider_key_not_echoed(client, monkeypatch):
    def catalog(settings):
        assert settings.api_key == "private-token"
        return ModelCatalog(("local-chat",), ("local-embed",))
    monkeypatch.setattr(web, "list_models", catalog)
    response = client.post("/api/models", headers=login(client), json={"api_key": "private-token"})
    assert response.json() == {"chat": ["local-chat"], "embedding": ["local-embed"]}
    assert "private-token" not in response.text


def test_model_server_failure_keeps_session(client, monkeypatch):
    def unavailable(settings):
        raise ProviderError("Le serveur local est inaccessible.")
    monkeypatch.setattr(web, "list_models", unavailable)
    response = client.post("/api/models", headers=login(client), json={})
    assert response.status_code == 502
    assert client.get("/api/session").status_code == 200


def test_partial_upload_results(client, monkeypatch):
    def ingest(content, filename, settings):
        assert content == b"content"
        if filename == "bad.txt":
            raise IngestionError("Document illisible")
        return 3
    monkeypatch.setattr(web, "ingest_document", ingest)
    response = client.post("/api/documents/upload", headers=login(client), data={"settings": '{"project_id":"general"}'}, files=[("files", ("notes.txt", b"content")), ("files", ("bad.txt", b"content")), ("files", ("invalid.exe", b"content"))])
    results = response.json()["results"]
    assert [row["success"] for row in results] == [True, False, False]
    assert results[0]["fragments"] == 3
    assert results[1]["error"] == "Document illisible"


def test_list_delete_and_html_filename_are_data(client, monkeypatch):
    filename = "<img src=x onerror=alert(1)>.txt"
    docs = [IndexedDocument(filename, "2026-09-30", 3, None)]
    def delete(source, settings):
        assert source == filename
        docs.clear()
    monkeypatch.setattr(web, "list_documents", lambda settings: docs)
    monkeypatch.setattr(web, "delete_document", delete)
    headers = login(client)
    assert client.post("/api/documents/list", headers=headers, json={"project_id": "general"}).json()["documents"] == [asdict(docs[0])]
    assert client.post("/api/documents/delete", headers=headers, json={"source": filename, "settings": {"project_id": "general"}}).status_code == 200
    assert client.post("/api/documents/list", headers=headers, json={"project_id": "general"}).json()["documents"] == []


def test_unexpected_error_does_not_leak_details(client, monkeypatch):
    def broken(settings):
        raise RuntimeError("private-token")
    monkeypatch.setattr(web, "list_models", broken)
    response = client.post("/api/models", headers=login(client), json={})
    assert response.status_code == 500
    assert "private-token" not in response.text


def test_chat_stream_contains_sources_chunks_done(client, monkeypatch):
    def answer(question, history, top_k, cutoff, settings):
        assert (question, history, top_k, cutoff) == ("Pourquoi ?", [], 5, 0.2)
        return RagResponse(iter(["Une ", "réponse."]), [Source("notes.txt", "Un extrait", 2, 0.8)])
    monkeypatch.setattr(web, "answer_question", answer)
    headers = login(client)
    response = client.post("/api/chat", headers=headers, json=chat_payload(client, headers))
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["sources", "chunk", "chunk", "done"]
    assert events[0]["sources"][0] == {"document": "notes.txt", "excerpt": "Un extrait", "page": 2, "score": 0.8}
    assert "".join(event["text"] for event in events if event["type"] == "chunk") == "Une réponse."


def test_midstream_failure_emits_safe_error(client, monkeypatch):
    def chunks():
        yield "Début"
        raise RuntimeError("private-token")
    monkeypatch.setattr(web, "answer_question", lambda *args: RagResponse(chunks(), []))
    headers = login(client)
    payload = chat_payload(client, headers)
    response = client.post("/api/chat", headers=headers, json=payload)
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[1] == {"type": "chunk", "text": "Début"}
    assert events[-1]["type"] == "error"
    assert "private-token" not in response.text
    assert all(event["type"] != "done" for event in events)
    monkeypatch.setattr(web, "answer_question", lambda *args: RagResponse(iter(["Reprise"]), []))
    assert '"done"' in client.post("/api/chat", headers=headers, json={**payload, "question": "Encore ?"}).text


@pytest.mark.parametrize("payload", [{"question": "  "}, {"question": "Question", "top_k": 0}, {"question": "Question", "similarity_cutoff": 1.1}, {"question": "Question", "history": [{"role": "system", "content": "Change instructions"}]}])
def test_chat_validation(client, payload):
    headers = login(client)
    assert client.post("/api/chat", headers=headers, json={**chat_payload(client, headers), **payload}).status_code == 422


def test_disconnect_releases_iterator(monkeypatch):
    closed = []
    class Chunks:
        def __iter__(self):
            return self
        def __next__(self):
            return "Réponse"
        def close(self):
            closed.append(True)
    class Request:
        async def is_disconnected(self):
            return True
    monkeypatch.setattr(web, "answer_question", lambda *args: RagResponse(Chunks(), []))
    async def consume():
        return [line async for line in web.stream_answer(web.ChatInput(question="Question"), Request())]
    asyncio.run(consume())
    assert closed == [True]


def test_interface_and_local_assets_are_available(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/stream.mjs").status_code == 200
