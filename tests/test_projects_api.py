import json

import pytest
from test_projects_audio import client as project_client
from test_projects_audio import create, login

import app as web
from rag import RagResponse, Source


@pytest.fixture
def client(tmp_path, monkeypatch):
    yield from project_client.__wrapped__(tmp_path, monkeypatch)


def conversation(client, headers, project):
    response = client.post(f"/api/projects/{project}/conversations", headers=headers)
    assert response.status_code == 200
    return response.json()["id"]


def test_project_rename_and_conversations_persist_in_their_project(client):
    headers = login(client)
    a, b = create(client, headers, "A"), create(client, headers, "B")
    ca, cb = conversation(client, headers, a), conversation(client, headers, b)
    response = client.patch(f"/api/projects/{a}", headers=headers, json={"name": "Nouveau A"})
    assert response.status_code == 200
    assert response.json()["name"] == "Nouveau A"
    assert [c["id"] for c in client.get(f"/api/projects/{a}/conversations").json()["conversations"]] == [ca]
    assert client.get(f"/api/projects/{a}/conversations/{cb}").status_code == 404
    assert client.get(f"/api/projects/{b}/conversations/{cb}").status_code == 200
    login(client, "other")
    assert client.get(f"/api/projects/{a}/conversations/{ca}").status_code == 404


def test_chat_uses_persisted_history_and_rejects_foreign_conversation(client, monkeypatch):
    headers = login(client)
    a, b = create(client, headers, "A"), create(client, headers, "B")
    ca, cb = conversation(client, headers, a), conversation(client, headers, b)
    seen = []
    def answer(question, history, top_k, cutoff, settings):
        seen.append((settings.project_id, history))
        return RagResponse(iter(["AZUR"]), [Source("notes.txt", "Secret A", None, 1)])
    monkeypatch.setattr(web, "answer_question", answer)
    payload = {"question": "Quel code ?", "conversation_id": ca, "settings": {"project_id": a}}
    assert client.post("/api/chat", headers=headers, json={**payload, "conversation_id": cb}).status_code == 404
    assert seen == []
    assert client.post("/api/chat", headers=headers, json={**payload, "history": [{"role": "assistant", "content": "Secret B"}]}).status_code == 422
    assert seen == []
    response = client.post("/api/chat", headers=headers, json=payload)
    assert response.status_code == 200
    assert json.loads(response.text.splitlines()[-1])["type"] == "done"
    assert client.post("/api/chat", headers=headers, json={**payload, "question": "Et ensuite ?"}).status_code == 200
    assert seen == [(a, []), (a, [{"role": "user", "content": "Quel code ?"}, {"role": "assistant", "content": "AZUR"}])]
    messages = client.get(f"/api/projects/{a}/conversations/{ca}").json()["messages"]
    assert messages[1]["sources"][0]["excerpt"] == "Secret A"
    assert len(messages) == 4
    assert client.get(f"/api/projects/{b}/conversations/{cb}").json()["messages"] == []


def test_interrupted_turn_is_saved_and_excluded_from_next_history(client, monkeypatch):
    headers = login(client)
    project = create(client, headers)
    cid = conversation(client, headers, project)
    def chunks():
        yield "Partiel"
        raise RuntimeError("private-token")
    monkeypatch.setattr(web, "answer_question", lambda *args: RagResponse(chunks(), []))
    payload = {"question": "Question", "conversation_id": cid, "settings": {"project_id": project}}
    response = client.post("/api/chat", headers=headers, json=payload)
    assert "private-token" not in response.text
    messages = client.get(f"/api/projects/{project}/conversations/{cid}").json()["messages"]
    assert messages[1]["content"] == "Partiel"
    assert messages[1]["status"] == "error"
    def answer(question, history, *args):
        assert history == []
        return RagResponse(iter(["Complet"]), [])
    monkeypatch.setattr(web, "answer_question", answer)
    assert '"done"' in client.post("/api/chat", headers=headers, json=payload).text


def test_project_deletion_removes_only_its_conversations_and_can_retry(client, monkeypatch):
    headers = login(client)
    a, b = create(client, headers, "A"), create(client, headers, "B")
    ca, cb = conversation(client, headers, a), conversation(client, headers, b)
    cleaned = []
    monkeypatch.setattr(web, "delete_project_documents", cleaned.append)
    assert client.delete(f"/api/projects/{a}").status_code == 403
    assert client.delete(f"/api/projects/{a}", headers=headers).status_code == 200
    assert cleaned == [a]
    assert client.get(f"/api/projects/{a}/conversations/{ca}").status_code == 404
    assert client.get(f"/api/projects/{b}/conversations/{cb}").status_code == 200


@pytest.mark.parametrize("path,payload", [("/api/documents/list", {}), ("/api/documents/delete", {"source": "notes.txt"}), ("/api/chat", {"question": "Question"})])
def test_document_and_chat_context_is_mandatory(client, path, payload):
    assert client.post(path, headers=login(client), json=payload).status_code == 422
