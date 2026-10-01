from dataclasses import replace
from uuid import uuid4

import pytest
from llama_index.core.embeddings import MockEmbedding

import ingest
import rag
from providers import ModelSettings


@pytest.fixture
def local_index(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "CHROMA_PATH", tmp_path / "chroma")
    embedding = MockEmbedding(embed_dim=2)
    monkeypatch.setattr(ingest, "_embed_model", lambda *args: embedding)
    monkeypatch.setattr(rag, "_models", lambda *args: (embedding, None))
    prompts = []
    def completion(llm, prompt):
        prompts.append(prompt)
        return iter(["Réponse"])
    monkeypatch.setattr(rag, "_stream_completion", completion)
    return prompts


def test_homonymous_files_retrieval_and_deletion_stay_in_their_project(local_index):
    a, b = str(uuid4()), str(uuid4())
    settings = ModelSettings(embed_model="embed-A")
    ingest.ingest_document(b"Le secret est AZUR-742.", "notes.txt", replace(settings, project_id=a))
    ingest.ingest_document(b"Le secret est CORAIL-915.", "notes.txt", replace(settings, project_id=b))
    assert ingest.list_documents(replace(settings, project_id=a))[0].source == "notes.txt"
    for project, own, foreign in ((a, "AZUR-742", "CORAIL-915"), (b, "CORAIL-915", "AZUR-742")):
        result = rag.answer_question("Quel secret ?", [], similarity_cutoff=0, settings=replace(settings, project_id=project))
        assert list(result.chunks) == ["Réponse"]
        assert own in result.sources[0].excerpt
        assert foreign not in result.sources[0].excerpt
        assert own in local_index[-1]
        assert foreign not in local_index[-1]
        lexical = rag._bm25_nodes(ingest._collection(replace(settings, project_id=project)))
        assert all(foreign not in node.get_content() for node in lexical)
    ingest.delete_document("notes.txt", replace(settings, project_id=a))
    assert ingest.list_documents(replace(settings, project_id=a)) == []
    assert len(ingest.list_documents(replace(settings, project_id=b))) == 1
    ingest.delete_project_documents(a)
    assert len(ingest.list_documents(replace(settings, project_id=b))) == 1


def test_general_preserves_legacy_and_project_cleanup_covers_all_models(local_index):
    settings_a = ModelSettings(embed_model="embed-A")
    settings_b = ModelSettings(embed_model="embed-B")
    project = str(uuid4())
    ingest.ingest_document(b"Texte historique.", "legacy.txt", settings_a)
    assert ingest.list_documents(settings_a)[0].source == "legacy.txt"
    ingest.ingest_document(b"Modele A.", "a.txt", replace(settings_a, project_id=project))
    ingest.ingest_document(b"Modele B.", "b.txt", replace(settings_b, project_id=project))
    assert [d.source for d in ingest.list_documents(replace(settings_a, project_id=project))] == ["a.txt"]
    assert [d.source for d in ingest.list_documents(replace(settings_b, project_id=project))] == ["b.txt"]
    ingest.delete_project_documents(project)
    assert ingest.list_documents(replace(settings_a, project_id=project)) == []
    assert ingest.list_documents(replace(settings_b, project_id=project)) == []
    assert ingest.list_documents(settings_a)[0].source == "legacy.txt"
    ingest.ingest_document(b"Autre projet.", "new.txt", replace(settings_a, project_id=str(uuid4())))
    ingest.delete_project_documents("general")
    assert ingest.list_documents(settings_a) == []


def test_invalid_project_cannot_address_a_collection(local_index):
    with pytest.raises(ValueError):
        ingest._collection(ModelSettings(embed_model="embed-A", project_id="../general"))
