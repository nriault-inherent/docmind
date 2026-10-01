from types import SimpleNamespace

import pytest

import ingest


def test_safe_source_name_removes_path_and_validates_extension():
    assert ingest._safe_source_name("../../notes/Rapport.PDF") == "Rapport.PDF"
    with pytest.raises(ingest.IngestionError, match="Format non pris en charge"):
        ingest._safe_source_name("archive.zip")


def test_extract_pages_rejects_empty_file():
    with pytest.raises(ingest.IngestionError, match="est vide"):
        ingest._extract_pages(b"", "vide.txt")


def test_ingest_embeds_before_replacing_existing_document(monkeypatch):
    events = []
    nodes = [SimpleNamespace(get_content=lambda: "contenu", embedding=None)]

    class FakeEmbedModel:
        def get_text_embedding_batch(self, texts, show_progress):
            events.append(("embed", texts, show_progress))
            return [[0.1, 0.2]]

    class FakeCollection:
        def delete(self, where):
            assert nodes[0].embedding == [0.1, 0.2]
            events.append(("delete", where))

    class FakeVectorStore:
        def __init__(self, chroma_collection):
            self.collection = chroma_collection

        def add(self, added_nodes):
            events.append(("add", added_nodes))

    monkeypatch.setattr(ingest, "_build_nodes", lambda *args: nodes)
    monkeypatch.setattr(ingest, "_embed_model", FakeEmbedModel)
    monkeypatch.setattr(ingest, "_collection", FakeCollection)
    monkeypatch.setattr(ingest, "ChromaVectorStore", FakeVectorStore)

    assert ingest.ingest_document(b"document", "notes.txt") == 1
    assert [event[0] for event in events] == ["embed", "delete", "add"]