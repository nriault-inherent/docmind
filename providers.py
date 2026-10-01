"""Clients HTTP OpenAI, Anthropic et Ollama, locaux ou distants."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.llms import CompletionResponse


class ProviderError(RuntimeError):
    """Erreur du serveur local, sans contenu confidentiel de sa réponse."""


@dataclass(frozen=True)
class ModelSettings:
    protocol: str = "openai"
    base_url: str = "http://127.0.0.1:11435/v1"
    llm_model: str = ""
    embed_model: str = ""
    api_key: str = field(default="", repr=False)
    max_tokens: int = 2048
    project_id: str | None = None
    embed_base_url: str = ""
    embed_protocol: str = ""
    embed_api_key: str = field(default="", repr=False)
    llm_options: dict = field(
        default_factory=dict, compare=False, hash=False, repr=False
    )

    def __post_init__(self):
        if self.project_id:
            try:
                object.__setattr__(self, "project_id", UUID(self.project_id).hex)
            except ValueError as exc:
                raise ValueError("Identifiant de projet invalide.") from exc
        if self.protocol not in {"openai", "anthropic", "ollama"}:
            raise ProviderError("Protocole inconnu.")
        url = self.base_url.strip().rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ProviderError("Adresse du serveur invalide.")
        if self.protocol != "ollama" and parsed.path in {"", "/"}:
            url += "/v1"
        object.__setattr__(self, "base_url", url)
        if self.embed_protocol not in {"", "openai", "ollama"}:
            raise ProviderError("Protocole d'embeddings inconnu.")
        if not isinstance(self.llm_options, dict) or any(
            key in self.llm_options for key in ("model", "messages", "stream")
        ):
            raise ProviderError(
                "Options LLM invalides : model, messages et stream sont réservés."
            )
        if self.embed_base_url:
            # Valide aussi l'adresse secondaire, sans envoyer la clé du chat.
            normalized = ModelSettings(
                protocol=self.embed_protocol or "openai", base_url=self.embed_base_url
            )
            object.__setattr__(self, "embed_base_url", normalized.base_url)
        if not 1 <= self.max_tokens <= 32768:
            raise ProviderError(
                "La limite de réponse doit être comprise entre 1 et 32768 tokens."
            )

    @property
    def collection_name(self) -> str:
        identity = f"{self.embedding_settings.base_url}\n{self.embed_model}"
        if self.project_id:
            identity += f"\nproject:{self.project_id}"
        prefix = f"docmind_p_{self.project_id}_" if self.project_id else "docmind_"
        return prefix + hashlib.sha256(identity.encode()).hexdigest()[:24]

    @property
    def embedding_settings(self) -> ModelSettings:
        return replace(
            self,
            protocol=self.embed_protocol
            or (
                "ollama"
                if self.protocol == "ollama" and not self.embed_base_url
                else "openai"
            ),
            base_url=self.embed_base_url or self.base_url,
            api_key=self.embed_api_key if self.embed_base_url else self.api_key,
            embed_base_url="",
            embed_protocol="",
            embed_api_key="",
            llm_options={},
        )


@dataclass(frozen=True)
class ModelCatalog:
    chat: tuple[str, ...]
    embedding: tuple[str, ...]
    tts: tuple[str, ...] = ()


@lru_cache(maxsize=8)
def _client(settings: ModelSettings) -> httpx.Client:
    # Keep-alive évite de recréer une connexion pour chaque fragment ou token.
    headers = (
        {"anthropic-version": "2023-06-01"} if settings.protocol == "anthropic" else {}
    )
    if settings.api_key:
        if settings.protocol == "anthropic":
            headers["x-api-key"] = settings.api_key
        else:
            headers["Authorization"] = f"Bearer {settings.api_key}"
    return httpx.Client(
        headers=headers, timeout=httpx.Timeout(180.0, connect=5.0), trust_env=False
    )


def _check(response: httpx.Response) -> None:
    if response.is_error:
        raise ProviderError(
            f"Le serveur LLM renvoie HTTP {response.status_code}. Vérifiez son adresse, sa clé et le modèle choisi."
        )


def _json(settings: ModelSettings, path: str, payload: dict | None = None) -> dict:
    try:
        client = _client(settings)
        url = settings.base_url + path
        response = (
            client.get(url) if payload is None else client.post(url, json=payload)
        )
        _check(response)
        result = response.json()
        if not isinstance(result, dict) or result.get("error"):
            raise ProviderError("Réponse invalide du serveur local.")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(
            "Impossible de joindre le serveur local ou de lire sa réponse. Vérifiez qu'il est lancé."
        ) from exc


def list_models(settings: ModelSettings) -> ModelCatalog:
    chat, embeddings, tts = [], [], []
    native = settings.protocol == "ollama"
    result = _json(settings, "/api/tags" if native else "/models")
    rows = result.get("models" if native else "data")
    if not isinstance(rows, list):
        raise ProviderError("Le serveur ne fournit pas de liste de modèles valide.")
    for row in rows:
        if native and isinstance(row, dict):
            row = {**row, "id": row.get("name")}
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        name = row["id"]
        kind = str(row.get("model_type", "")).lower()
        if "tts" in kind or "fishaudio" in name.lower() or "fish-audio" in name.lower():
            tts.append(name)
            continue
        if "audio" in kind or "stt" in kind or "sts" in kind:
            continue
        if "rerank" in kind or "rerank" in name.lower():
            continue
        target = embeddings if "embed" in kind or "embed" in name.lower() else chat
        target.append(name)
    if settings.embed_base_url:
        embeddings = list(list_models(settings.embedding_settings).embedding)
    # Un catalogue ne décrit pas toujours les capacités. Le choix explicite reste valide.
    if settings.llm_model and settings.llm_model not in chat:
        chat.append(settings.llm_model)
    if settings.embed_model and settings.embed_model not in embeddings:
        embeddings.append(settings.embed_model)
    return ModelCatalog(
        tuple(sorted(set(chat))),
        tuple(sorted(set(embeddings))),
        tuple(sorted(set(tts))),
    )


class LocalLLM:
    def __init__(self, settings: ModelSettings):
        if not settings.llm_model:
            raise ProviderError("Choisissez un modèle de génération.")
        self.settings = settings
        self.model = settings.llm_model

    def _request(self, prompt: str, stream: bool):
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.settings.max_tokens,
            "temperature": 0.1,
            "stream": stream,
        }
        path = (
            "/messages"
            if self.settings.protocol == "anthropic"
            else "/chat/completions"
        )
        # Le RAG documentaire privilégie une réponse directe ; le raisonnement
        # peut épuiser max_tokens avant le premier token visible sur Qwen.
        if self.settings.protocol == "ollama":
            payload.pop("max_tokens")
            payload.pop("temperature")
            payload["options"] = {
                "num_predict": self.settings.max_tokens,
                "temperature": 0.1,
            }
            payload["think"] = False
            path = "/api/chat"
        elif self.settings.protocol == "anthropic":
            payload["thinking"] = {"type": "disabled"}
        elif (
            urlsplit(self.settings.base_url).hostname in {"localhost", "127.0.0.1"}
            and urlsplit(self.settings.base_url).port == 11435
        ):
            payload["enable_thinking"] = False
        for key, value in self.settings.llm_options.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        return path, payload

    def complete(self, prompt: str) -> CompletionResponse:
        path, payload = self._request(prompt, False)
        result = _json(self.settings, path, payload)
        try:
            if self.settings.protocol == "anthropic":
                text = "".join(
                    block["text"]
                    for block in result["content"]
                    if block.get("type") == "text"
                )
            elif self.settings.protocol == "ollama":
                text = result["message"]["content"] or ""
            else:
                text = result["choices"][0]["message"]["content"] or ""
            if not text.strip():
                raise ProviderError(
                    "Le modèle n'a renvoyé aucun texte. Augmentez la limite de tokens ou choisissez un autre modèle."
                )
            return CompletionResponse(text=text)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Réponse de génération invalide.") from exc

    def stream_complete(self, prompt: str):
        path, payload = self._request(prompt, True)
        has_text = False
        completed = False
        try:
            with _client(self.settings).stream(
                "POST", self.settings.base_url + path, json=payload
            ) as response:
                _check(response)
                for line in response.iter_lines():
                    native = self.settings.protocol == "ollama"
                    if not native and not line.startswith("data:"):
                        continue
                    data = line.strip() if native else line[5:].strip()
                    if data == "[DONE]":
                        completed = self.settings.protocol == "openai"
                        break
                    if not data:
                        continue
                    event = json.loads(data)
                    if event.get("error") or event.get("type") == "error":
                        raise ProviderError("Le serveur a interrompu la génération.")
                    if self.settings.protocol == "anthropic":
                        if event.get("type") == "message_stop":
                            completed = True
                        delta = event.get("delta", {})
                        text = (
                            delta.get("text", "")
                            if delta.get("type") == "text_delta"
                            else ""
                        )
                    elif native:
                        completed = event.get("done") is True
                        text = event.get("message", {}).get("content", "")
                    else:
                        choices = event.get("choices") or []
                        if choices and choices[0].get("finish_reason") is not None:
                            completed = True
                        text = (
                            choices[0].get("delta", {}).get("content")
                            if choices
                            else ""
                        )
                    if text:
                        has_text = True
                        yield CompletionResponse(text=text, delta=text)
                    if native and completed:
                        break
                if not has_text:
                    raise ProviderError(
                        "Le modèle n'a renvoyé aucun texte. Augmentez la limite de tokens ou choisissez un autre modèle."
                    )
                if not completed:
                    raise ProviderError(
                        "Le flux a été interrompu avant la fin de la réponse. Réessayez la question."
                    )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                "La réponse du serveur local a été interrompue ou est invalide."
            ) from exc


class LocalEmbedding(BaseEmbedding):
    settings: ModelSettings

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        native = self.settings.protocol == "ollama"
        result = _json(
            self.settings,
            "/api/embed" if native else "/embeddings",
            {"model": self.model_name, "input": texts},
        )
        try:
            rows = (
                [
                    {"index": i, "embedding": vector}
                    for i, vector in enumerate(result["embeddings"])
                ]
                if native
                else sorted(result["data"], key=lambda item: item["index"])
            )
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("missing embeddings")
            vectors = [row["embedding"] for row in rows]
            if any(
                not vector or not all(isinstance(x, (int, float)) for x in vector)
                for vector in vectors
            ):
                raise ValueError("invalid vectors")
            return vectors
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "Le serveur ne fournit pas tous les embeddings attendus."
            ) from exc

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embeddings([query])[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embeddings([text])[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)


def embedding_model(settings: ModelSettings) -> LocalEmbedding:
    if not settings.embed_model:
        raise ProviderError("Choisissez un modèle d'embeddings.")
    return LocalEmbedding(
        model_name=settings.embed_model,
        settings=settings.embedding_settings,
        embed_batch_size=16,
    )


def default_settings() -> ModelSettings:
    protocol = os.getenv("DOCMIND_PROTOCOL", "openai")
    native = protocol == "ollama"
    try:
        options = json.loads(os.getenv("DOCMIND_LLM_OPTIONS", "{}"))
    except ValueError as exc:
        raise ProviderError(
            "DOCMIND_LLM_OPTIONS doit être un objet JSON valide."
        ) from exc
    return ModelSettings(
        protocol=protocol,
        base_url=os.getenv(
            "DOCMIND_BASE_URL",
            os.getenv(
                "BASE_URL" if native else "OMLX_BASE_URL",
                "http://localhost:11434" if native else "http://127.0.0.1:11435/v1",
            ),
        ),
        llm_model=os.getenv(
            "DOCMIND_LLM_MODEL",
            os.getenv("OLLAMA_LLM_MODEL" if native else "OMLX_LLM_MODEL", ""),
        ),
        embed_model=os.getenv(
            "DOCMIND_EMBED_MODEL",
            os.getenv("EMBED_MODEL" if native else "OMLX_EMBED_MODEL", ""),
        ),
        api_key=os.getenv(
            "DOCMIND_API_KEY", "" if native else os.getenv("OMLX_API_KEY", "")
        ),
        embed_base_url=os.getenv("DOCMIND_EMBED_BASE_URL", ""),
        embed_protocol=os.getenv("DOCMIND_EMBED_PROTOCOL", ""),
        embed_api_key=os.getenv("DOCMIND_EMBED_API_KEY", ""),
        llm_options=options,
    )
