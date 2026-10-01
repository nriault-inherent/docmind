import pytest

from workspace import WorkspaceError, WorkspaceStore


def test_projects_and_messages_survive_reopening(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    store = WorkspaceStore(path)
    assert [p["name"] for p in store.list_projects()] == ["Général"]
    a = store.create_project("Dossier A")
    b = store.create_project("Dossier B")
    conversation = store.create_conversation(a["id"])
    turn, history = store.start_turn(a["id"], conversation["id"], "Quel code ?")
    assert history == []
    store.finish_turn(turn, "AZUR", [{"document": "notes.txt"}], "complete")
    reopened = WorkspaceStore(path)
    assert reopened.list_conversations(b["id"]) == []
    messages = reopened.get_conversation(a["id"], conversation["id"])["messages"]
    assert [m["content"] for m in messages] == ["Quel code ?", "AZUR"]
    assert messages[1]["sources"] == [{"document": "notes.txt"}]
    assert reopened.list_conversations(a["id"])[0]["title"] == "Quel code ?"


def test_foreign_conversation_and_incomplete_history_are_excluded(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    a, b = store.create_project("A"), store.create_project("B")
    c = store.create_conversation(a["id"])
    with pytest.raises(WorkspaceError) as error:
        store.start_turn(b["id"], c["id"], "Interdit")
    assert error.value.status_code == 404
    turn, _ = store.start_turn(a["id"], c["id"], "Question perdue")
    store.finish_turn(turn, "Partiel", [], "interrupted", "Réponse interrompue.")
    turn, history = store.start_turn(a["id"], c["id"], "Nouvelle question")
    assert history == []
    store.finish_turn(turn, "Complet", [], "complete")
    _, history = store.start_turn(a["id"], c["id"], "Suite")
    assert history == [{"role": "user", "content": "Nouvelle question"}, {"role": "assistant", "content": "Complet"}]


def test_deletion_refuses_active_operation_and_can_retry_cleanup(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    a, b = store.create_project("A"), store.create_project("B")
    c = store.create_conversation(a["id"])
    with store.operation(a["id"], c["id"]):
        with pytest.raises(WorkspaceError) as error:
            store.delete_project(a["id"], lambda _: pytest.fail("Cleanup during generation"))
        assert error.value.status_code == 409
        with pytest.raises(WorkspaceError), store.operation(a["id"], c["id"]):
            pytest.fail("Parallel generation")
    def fail_cleanup(_):
        raise RuntimeError("Chroma unavailable")
    with pytest.raises(RuntimeError):
        store.delete_project(a["id"], fail_cleanup)
    assert next(p for p in store.list_projects() if p["id"] == a["id"])["deleting"]
    with pytest.raises(WorkspaceError), store.operation(a["id"]):
        pytest.fail("Project still deleting")
    cleaned = []
    store.delete_project(a["id"], cleaned.append)
    assert cleaned == [a["id"]]
    assert store.list_conversations(b["id"]) == []
    assert all(p["id"] != a["id"] for p in store.list_projects())
    with pytest.raises(WorkspaceError):
        store.get_conversation(a["id"], c["id"])


def test_empty_workspace_does_not_recreate_general_and_names_are_validated(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    store = WorkspaceStore(path)
    store.delete_project("general", lambda _: None)
    assert WorkspaceStore(path).list_projects() == []
    for name in ("  ", "x" * 121):
        with pytest.raises(WorkspaceError):
            store.create_project(name)
    p = store.create_project("  A  ")
    store.rename_project(p["id"], "Nouveau")
    assert store.list_projects()[0]["name"] == "Nouveau"


def test_operations_protect_projects_between_store_instances(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    first, second = WorkspaceStore(path), WorkspaceStore(path)
    project = first.create_project("A")
    conversation = first.create_conversation(project["id"])
    with first.operation(project["id"], conversation["id"]):
        with pytest.raises(WorkspaceError) as error:
            second.delete_project(project["id"], lambda _: None)
        assert error.value.status_code == 409
        with pytest.raises(WorkspaceError), second.operation(project["id"], conversation["id"]):
            pytest.fail("Parallel generation in another worker")
    second.delete_project(project["id"], lambda _: None)
