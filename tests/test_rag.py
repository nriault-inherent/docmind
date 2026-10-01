from types import SimpleNamespace

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

import rag


class FakeCollection:
    def __init__(self, count, rows=None):
        self._count = count
        self._rows = rows or {"ids": [], "documents": [], "metadatas": []}

    def count(self):
        return self._count

    def get(self, include=None):
        return self._rows


def _fake_index_class(retrieved, captured_queries):
    class FakeIndex:
        @classmethod
        def from_vector_store(cls, vector_store, embed_model):
            return cls()

        def as_retriever(self, similarity_top_k):
            def retrieve(query):
                captured_queries.append(query)
                return retrieved

            return SimpleNamespace(retrieve=retrieve)

    return FakeIndex


def _patch_pipeline(monkeypatch, collection, llm, fake_index_class):
    monkeypatch.setattr(rag, "_collection", lambda: collection)
    monkeypatch.setattr(rag, "_models", lambda: (object(), llm))
    monkeypatch.setattr(rag, "ChromaVectorStore", lambda chroma_collection: object())
    monkeypatch.setattr(rag, "VectorStoreIndex", fake_index_class)


def test_empty_collection_returns_guardrail_without_loading_models(monkeypatch):
    monkeypatch.setattr(rag, "_collection", lambda: FakeCollection(0))
    monkeypatch.setattr(
        rag,
        "_models",
        lambda: pytest.fail("Les modèles ne doivent pas être chargés sans document"),
    )

    response = rag.answer_question("Une question ?", [])

    assert "Je ne sais pas" in "".join(response.chunks)
    assert response.sources == []


def test_retrieval_uses_rewritten_question_and_prompt_uses_original(monkeypatch):
    node = TextNode(
        text="Le délai de retour est de trente jours.",
        metadata={"source": "guide.pdf", "page": 2},
    )
    retrieved = [NodeWithScore(node=node, score=0.9)]
    queries = []
    prompts = []
    llm = SimpleNamespace(
        stream_complete=lambda prompt: (prompts.append(prompt), iter([SimpleNamespace(delta="Réponse locale.")]))[1],
        complete=lambda prompt: SimpleNamespace(text="Quel est le délai de retour ?"),
    )
    rows = {
        "ids": ["n1"],
        "documents": [node.get_content()],
        "metadatas": [dict(node.metadata)],
    }
    _patch_pipeline(
        monkeypatch,
        FakeCollection(1, rows),
        llm,
        _fake_index_class(retrieved, queries),
    )

    history = [
        {"role": "user", "content": "Quel est le délai de livraison ?"},
        {"role": "assistant", "content": "Cinq jours."},
    ]
    response = rag.answer_question("Et pour le retour ?", history)
    answer = "".join(response.chunks)

    assert queries == ["Quel est le délai de retour ?"]
    assert "Et pour le retour ?" in prompts[0]
    assert answer == "Réponse locale."


def test_rewrite_failure_falls_back_to_original_question(monkeypatch):
    node = TextNode(text="Un texte.", metadata={"source": "guide.pdf"})
    retrieved = [NodeWithScore(node=node, score=0.9)]
    queries = []

    def failing_complete(prompt):
        raise ConnectionError("Ollama indisponible")

    llm = SimpleNamespace(
        stream_complete=lambda prompt: iter([SimpleNamespace(delta="ok")]),
        complete=failing_complete,
    )
    rows = {
        "ids": ["n1"],
        "documents": ["Un texte."],
        "metadatas": [{"source": "guide.pdf"}],
    }
    _patch_pipeline(
        monkeypatch,
        FakeCollection(1, rows),
        llm,
        _fake_index_class(retrieved, queries),
    )

    history = [
        {"role": "user", "content": "une question"},
        {"role": "assistant", "content": "une réponse"},
    ]
    rag.answer_question("Et alors ?", history)

    assert queries == ["Et alors ?"]


def test_no_rewrite_without_history(monkeypatch):
    node = TextNode(text="Un texte.", metadata={"source": "guide.pdf"})
    retrieved = [NodeWithScore(node=node, score=0.9)]
    queries = []
    llm = SimpleNamespace(
        stream_complete=lambda prompt: iter([SimpleNamespace(delta="ok")]),
        complete=lambda prompt: pytest.fail("Sans historique, la question ne doit pas être réécrite"),
    )
    rows = {
        "ids": ["n1"],
        "documents": ["Un texte."],
        "metadatas": [{"source": "guide.pdf"}],
    }
    _patch_pipeline(
        monkeypatch,
        FakeCollection(1, rows),
        llm,
        _fake_index_class(retrieved, queries),
    )

    rag.answer_question("Une question ?", [])

    assert queries == ["Une question ?"]


def test_hybrid_retrieval_prefers_chunks_found_by_both_methods():
    node_a = TextNode(text="alpha", metadata={"source": "a.pdf"})
    node_b = TextNode(text="beta gamma", metadata={"source": "b.pdf"})
    node_c = TextNode(text="gamma", metadata={"source": "c.pdf"})
    vector_results = [
        NodeWithScore(node=node_a, score=0.9),
        NodeWithScore(node=node_b, score=0.8),
    ]
    vector_retriever = SimpleNamespace(retrieve=lambda query: vector_results)

    results = rag._hybrid_retrieve(vector_retriever, [node_a, node_b, node_c], "gamma", top_k=3)

    assert results[0].node is node_b
    assert {item.node.hash for item in results[1:]} == {node_a.hash, node_c.hash}
    assert results[0].score == pytest.approx(1.0, abs=0.03)
    assert all(item.score == pytest.approx(0.5) for item in results[1:])


def test_low_fused_score_is_filtered_by_cutoff(monkeypatch):
    node = TextNode(text="Un texte.", metadata={"source": "guide.pdf"})
    retrieved = [NodeWithScore(node=node, score=0.9)]
    llm = SimpleNamespace(
        stream_complete=lambda prompt: pytest.fail("Le LLM ne doit pas répondre sous le seuil"),
        complete=lambda prompt: SimpleNamespace(text="Une question ?"),
    )
    _patch_pipeline(
        monkeypatch,
        FakeCollection(1),
        llm,
        _fake_index_class(retrieved, []),
    )

    response = rag.answer_question("Une question ?", [], similarity_cutoff=0.6)

    assert "Je ne sais pas" in "".join(response.chunks)
    assert response.sources == []


def test_relevant_chunk_is_streamed_and_exposed_as_source(monkeypatch):
    node = TextNode(
        text="La réponse est locale.",
        metadata={"source": "guide.pdf", "page": 3},
    )
    retrieved = [NodeWithScore(node=node, score=0.8)]

    class FakeIndex:
        @classmethod
        def from_vector_store(cls, vector_store, embed_model):
            return cls()

        def as_retriever(self, similarity_top_k):
            assert similarity_top_k == 15
            return SimpleNamespace(retrieve=lambda question: retrieved)

    fake_llm = SimpleNamespace(
        stream_complete=lambda prompt: iter(
            [SimpleNamespace(delta="Réponse "), SimpleNamespace(delta="locale.")]
        ),
        complete=lambda prompt: SimpleNamespace(text="Où est la réponse ?"),
    )
    rows = {
        "ids": ["n1"],
        "documents": ["La réponse est locale."],
        "metadatas": [{"source": "guide.pdf", "page": 3}],
    }
    _patch_pipeline(monkeypatch, FakeCollection(1, rows), fake_llm, FakeIndex)

    response = rag.answer_question("Où est la réponse ?", [])

    assert "".join(response.chunks) == "Réponse locale."
    assert response.sources[0].document == "guide.pdf"
    assert response.sources[0].page == 3


def test_bm25_nodes_built_from_collection_rows():
    rows = {
        "ids": ["a", "b"],
        "documents": ["Un texte", None],
        "metadatas": [{"source": "x.pdf"}, {"source": "y.pdf"}],
    }

    nodes = rag._bm25_nodes(FakeCollection(2, rows))

    assert [node.get_content() for node in nodes] == ["Un texte"]
    assert nodes[0].metadata == {"source": "x.pdf"}
    assert nodes[0].id_ == "a"
