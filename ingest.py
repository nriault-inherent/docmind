"""Extraction, découpage et indexation locale des documents."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chromadb
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from unstructured.partition.auto import partition

from providers import ModelSettings, embedding_model

CHROMA_PATH = Path(os.getenv("DOCMIND_CHROMA_PATH", "data/chroma_db"))
COLLECTION_NAME = "docmind"
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:8b")
OLLAMA_BASE_URL = os.getenv("BASE_URL", "http://localhost:11434")
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"}


class IngestionError(RuntimeError):
    """Erreur présentable à l'utilisateur pendant l'ingestion."""


@dataclass(frozen=True)
class IndexedDocument:
    source: str
    added_at: str
    chunk_count: int
    page_count: int | None


def _project_settings(settings: ModelSettings | None, project_id: str | None) -> ModelSettings | None:
    if project_id is None:
        return settings
    if project_id == 'general':
        return replace(settings, project_id=None) if settings else None
    legacy = settings or ModelSettings(protocol='ollama', base_url=OLLAMA_BASE_URL, embed_model=EMBED_MODEL)
    return replace(legacy, project_id=project_id)


def _collection(settings: ModelSettings | None = None, project_id: str | None = None) -> Any:
    settings = _project_settings(settings, project_id)
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_or_create_collection(
        settings.collection_name
        if settings and (settings.project_id or settings.embedding_settings.protocol != "ollama" or settings.embedding_settings.base_url != OLLAMA_BASE_URL.rstrip("/") or settings.embed_model != EMBED_MODEL)
        else COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _embed_model(settings: ModelSettings | None = None) -> Any:
    if settings:
        return embedding_model(settings)
    return OllamaEmbedding(
        model_name=EMBED_MODEL,
        base_url=OLLAMA_BASE_URL,
        request_timeout=120.0,
    )


def _safe_source_name(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        formats = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise IngestionError(
            f"Format non pris en charge. Formats acceptés : {formats}."
        )
    return name


def _extract_pages(content: bytes, source: str) -> list[tuple[int | None, str]]:
    if not content:
        raise IngestionError(f"Le fichier « {source} » est vide.")

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=Path(source).suffix, delete=False
        ) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name
        elements = partition(filename=temp_path)
    except Exception as exc:
        raise IngestionError(
            f"Impossible d'extraire « {source} ». Le fichier est peut-être corrompu "
            "ou une dépendance système est absente."
        ) from exc
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)

    grouped: dict[int | None, list[str]] = defaultdict(list)
    for element in elements:
        text = str(element).strip()
        if not text:
            continue
        metadata = getattr(element, "metadata", None)
        page_number = getattr(metadata, "page_number", None)
        grouped[int(page_number) if page_number is not None else None].append(text)

    pages = [(page, "\n\n".join(texts)) for page, texts in grouped.items() if texts]
    if not pages:
        raise IngestionError(
            f"Aucun texte exploitable n'a été trouvé dans « {source} »."
        )
    return sorted(pages, key=lambda item: item[0] if item[0] is not None else 0)


def _build_nodes(content: bytes, source: str, added_at: str) -> list[Any]:
    file_hash = hashlib.sha256(content).hexdigest()
    documents: list[Document] = []
    for page_number, text in _extract_pages(content, source):
        metadata: dict[str, str | int] = {
            "source": source,
            "added_at": added_at,
            "file_hash": file_hash,
        }
        if page_number is not None:
            metadata["page"] = page_number
        documents.append(Document(text=text, metadata=metadata))

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)
    nodes = splitter.get_nodes_from_documents(documents, show_progress=False)
    if not nodes:
        raise IngestionError(
            f"Le découpage de « {source} » n'a produit aucun fragment."
        )
    return nodes


def ingest_document(
    content: bytes, filename: str, settings: ModelSettings | None = None, project_id: str | None = None
) -> int:
    """Extrait et indexe un document; une source homonyme est remplacée."""
    settings = _project_settings(settings, project_id)
    source = _safe_source_name(filename)
    added_at = datetime.now(UTC).isoformat(timespec="seconds")
    nodes = _build_nodes(content, source, added_at)

    try:
        model = _embed_model(settings) if settings else _embed_model()
        embeddings = model.get_text_embedding_batch(
            [node.get_content() for node in nodes],
            show_progress=True,
        )
    except Exception as exc:
        raise IngestionError(
            f"Impossible de générer les embeddings avec « {settings.embed_model if settings and settings.protocol != 'ollama' else EMBED_MODEL} ». "
            "Vérifiez le serveur local et le modèle sélectionné."
        ) from exc

    for node, embedding in zip(nodes, embeddings, strict=True):
        node.embedding = embedding

    collection = _collection(settings) if settings else _collection()
    vector_store = ChromaVectorStore(chroma_collection=collection)
    try:
        collection.delete(where={"source": source})
        vector_store.add(nodes)
    except Exception as exc:
        raise IngestionError(
            f"Échec de l'écriture de « {source} » dans ChromaDB."
        ) from exc
    return len(nodes)


def list_documents(settings: ModelSettings | None = None, project_id: str | None = None) -> list[IndexedDocument]:
    settings = _project_settings(settings, project_id)
    try:
        collection = _collection(settings) if settings else _collection()
        result = collection.get(include=["metadatas"])
    except Exception as exc:
        raise IngestionError("Impossible de lire les documents dans ChromaDB.") from exc
    grouped: dict[str, dict[str, Any]] = {}
    for metadata in result.get("metadatas") or []:
        if not metadata or "source" not in metadata:
            continue
        source = str(metadata["source"])
        entry = grouped.setdefault(
            source,
            {
                "added_at": str(metadata.get("added_at", "")),
                "chunks": 0,
                "pages": set(),
            },
        )
        entry["chunks"] += 1
        if metadata.get("page") is not None:
            entry["pages"].add(int(metadata["page"]))

    return [
        IndexedDocument(
            source=source,
            added_at=values["added_at"],
            chunk_count=values["chunks"],
            page_count=len(values["pages"]) or None,
        )
        for source, values in sorted(grouped.items())
    ]


def delete_document(source: str, settings: ModelSettings | None = None, project_id: str | None = None) -> None:
    settings = _project_settings(settings, project_id)
    source = Path(source).name
    try:
        collection = _collection(settings) if settings else _collection()
        collection.delete(where={"source": source})
    except Exception as exc:
        raise IngestionError(f"Impossible de supprimer « {source} ».") from exc


def delete_project_documents(project_id: str) -> None:
    """Nettoie tous les index d'un projet, quel que soit le modèle d'embedding."""
    settings = _project_settings(None, project_id)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    prefix = f'docmind_p_{settings.project_id}_' if settings and settings.project_id else None
    for collection in client.list_collections():
        name = collection if isinstance(collection, str) else collection.name
        if (prefix and name.startswith(prefix)) or (prefix is None and (name == COLLECTION_NAME or (name.startswith('docmind_') and not name.startswith('docmind_p_')))):
            client.delete_collection(name)
