"""Recherche hybride et génération locale avec oMLX ou Ollama."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore

from bm25 import Bm25Index
from ingest import EMBED_MODEL, OLLAMA_BASE_URL, _collection
from providers import LocalLLM, ModelSettings, ProviderError, embedding_model

LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "maternion/minicpm5:2b")
NO_ANSWER = (
    "Je ne sais pas : cette information ne figure pas dans les documents fournis."
)
SYSTEM_PROMPT = """Tu es l'assistant d'une base documentaire privée locale.
Réponds uniquement à partir du CONTEXTE fourni. N'utilise aucune connaissance externe.
Si le contexte ne permet pas de répondre, réponds exactement :
« Je ne sais pas : cette information ne figure pas dans les documents fournis. »
Réponds en français, de façon précise et concise. Cite les sources dans le texte sous la
forme [nom du fichier, p. X] lorsque la page est disponible, sinon [nom du fichier].

HISTORIQUE RÉCENT :
{history}

CONTEXTE :
{context}

QUESTION :
{question}

RÉPONSE :
"""


REWRITE_PROMPT = """Réécris la question de l'utilisateur pour qu'elle soit complète et autonome,
en résolvant les pronoms et les références à l'historique de conversation.
Réponds uniquement avec la question réécrite.

HISTORIQUE :
{history}

QUESTION :
{question}

QUESTION RÉÉCRITE :
"""

_RRF_K = 60.0
_FANOUT = 3


class RagError(RuntimeError):
    """Erreur présentable à l'utilisateur pendant une requête RAG."""


@dataclass(frozen=True)
class Source:
    document: str
    excerpt: str
    page: int | None
    score: float | None


@dataclass(frozen=True)
class RagResponse:
    chunks: Iterable[str]
    sources: list[Source]


def _models(settings: ModelSettings | None = None) -> tuple[Any, Any]:
    if settings:
        return embedding_model(settings), LocalLLM(settings)
    embed_model = OllamaEmbedding(
        model_name=EMBED_MODEL,
        base_url=OLLAMA_BASE_URL,
        request_timeout=120.0,
    )
    llm = Ollama(
        model=LLM_MODEL,
        base_url=OLLAMA_BASE_URL,
        request_timeout=180.0,
        context_window=8192,
    )
    return embed_model, llm


def _history_text(history: Sequence[dict[str, Any]], limit: int = 6) -> str:
    lines: list[str] = []
    for message in history[-limit:]:
        role = "Utilisateur" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content", "")).strip()
        if content:
            lines.append(f"{role} : {content}")
    return "\n".join(lines) or "(aucun)"


def _rewrite_question(
    llm: Any, question: str, history: Sequence[dict[str, Any]]
) -> str:
    if not history:
        return question
    try:
        result = llm.complete(
            REWRITE_PROMPT.format(history=_history_text(history), question=question)
        )
    except (OSError, ValueError):
        return question
    rewritten = str(getattr(result, "text", "") or "").strip()
    return rewritten or question


def _bm25_nodes(collection: Any) -> list[TextNode]:
    result = collection.get(include=["documents", "metadatas"])
    nodes: list[TextNode] = []
    for node_id, text, metadata in zip(
        result.get("ids") or [],
        result.get("documents") or [],
        result.get("metadatas") or [],
    ):
        if not text:
            continue
        nodes.append(
            TextNode(id_=str(node_id), text=str(text), metadata=metadata or {})
        )
    return nodes


def _hybrid_retrieve(
    vector_retriever: Any,
    nodes: Sequence[TextNode],
    query: str,
    top_k: int,
) -> list[NodeWithScore]:
    fused: dict[int, tuple[NodeWithScore, float]] = {}

    def add(node_with_score: NodeWithScore, rank: int) -> None:
        key = node_with_score.node.hash
        score = 1.0 / (_RRF_K + rank)
        previous = fused.get(key)
        fused[key] = (node_with_score, (previous[1] if previous else 0.0) + score)

    for rank, node_with_score in enumerate(vector_retriever.retrieve(query)):
        add(node_with_score, rank)

    bm25_scores = Bm25Index([node.get_content() for node in nodes]).scores(query)
    for rank, index in enumerate(
        sorted(
            range(len(bm25_scores)),
            key=lambda position: bm25_scores[position],
            reverse=True,
        )
    ):
        if bm25_scores[index] <= 0.0:
            break
        add(NodeWithScore(node=nodes[index]), rank)

    ranked = sorted(fused.values(), key=lambda entry: entry[1], reverse=True)[:top_k]
    max_score = 2.0 / _RRF_K
    return [
        NodeWithScore(node=node_with_score.node, score=fused_score / max_score)
        for node_with_score, fused_score in ranked
    ]


def _sources(nodes: Sequence[Any]) -> list[Source]:
    sources: list[Source] = []
    seen: set[tuple[str, int | None, str]] = set()
    for item in nodes:
        metadata = item.node.metadata
        text = item.node.get_content().strip()
        page_value = metadata.get("page")
        page = int(page_value) if page_value is not None else None
        key = (str(metadata.get("source", "Document inconnu")), page, text)
        if key in seen:
            continue
        seen.add(key)
        excerpt = text if len(text) <= 600 else f"{text[:597].rstrip()}…"
        sources.append(Source(key[0], excerpt, page, item.score))
    return sources


def _context(nodes: Sequence[Any]) -> str:
    blocks: list[str] = []
    for item in nodes:
        metadata = item.node.metadata
        label = str(metadata.get("source", "Document inconnu"))
        if metadata.get("page") is not None:
            label += f", p. {metadata['page']}"
        blocks.append(f"SOURCE [{label}]\n{item.node.get_content()}")
    return "\n\n---\n\n".join(blocks)


def _stream_completion(llm: Any, prompt: str) -> Iterable[str]:
    try:
        for response in llm.stream_complete(prompt):
            if response.delta:
                yield response.delta
    except Exception as exc:
        if isinstance(exc, ProviderError):
            raise RagError(str(exc)) from exc
        raise RagError(
            f"Le serveur local ne peut pas répondre avec « {getattr(llm, 'model', LLM_MODEL)} ». "
            "Vérifiez le service et le modèle sélectionné."
        ) from exc


def answer_question(
    question: str,
    history: Sequence[dict[str, Any]],
    top_k: int = 5,
    similarity_cutoff: float = 0.2,
    settings: ModelSettings | None = None,
    project_id: str | None = None,
) -> RagResponse:
    if project_id is not None:
        from ingest import _project_settings
        settings = _project_settings(settings, project_id)
    question = question.strip()
    if not question:
        raise RagError("La question ne peut pas être vide.")

    try:
        collection = _collection(settings) if settings else _collection()
    except Exception as exc:
        raise RagError("Impossible de lire l'index documentaire.") from exc
    if collection.count() == 0:
        return RagResponse(iter([NO_ANSWER]), [])

    try:
        embed_model, llm = _models(settings) if settings else _models()
        vector_store = ChromaVectorStore(chroma_collection=collection)
        index = VectorStoreIndex.from_vector_store(
            vector_store, embed_model=embed_model
        )
        vector_retriever = index.as_retriever(similarity_top_k=top_k * _FANOUT)
        bm25_nodes = _bm25_nodes(collection)
        query = _rewrite_question(llm, question, history)
        retrieved = _hybrid_retrieve(vector_retriever, bm25_nodes, query, top_k)
    except Exception as exc:
        raise RagError(
            f"Recherche impossible avec « {settings.embed_model if settings and settings.protocol != 'ollama' else EMBED_MODEL} ». Vérifiez le serveur local et ChromaDB."
        ) from exc

    relevant = [
        item
        for item in retrieved
        if item.score is None or float(item.score) >= similarity_cutoff
    ]
    if not relevant:
        return RagResponse(iter([NO_ANSWER]), [])

    prompt = SYSTEM_PROMPT.format(
        history=_history_text(history),
        context=_context(relevant),
        question=question,
    )
    return RagResponse(_stream_completion(llm, prompt), _sources(relevant))
