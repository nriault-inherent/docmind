"""Serveur de l’atelier DocMind : interface locale, sessions et API documentaire."""

import json
import os
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from audio_summary import DEFAULT_TTS_MODEL, generate_audio_summary
from auth import AuthConfigError, SessionStore, load_auth_config, verify_credentials
from ingest import (
    EMBED_MODEL,
    OLLAMA_BASE_URL,
    IngestionError,
    delete_document,
    delete_project_documents,
    ingest_document,
    list_documents,
)
from projects import ProjectStore
from providers import ModelSettings, ProviderError, default_settings, list_models
from rag import LLM_MODEL, RagError, answer_question
from workspace import WorkspaceError
from workspace_api import project_operation, workspace_router

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["openai", "anthropic", "ollama"] | None = None
    base_url: str | None = None
    llm_model: str | None = None
    embed_model: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    embed_base_url: str | None = None
    embed_protocol: Literal["", "openai", "ollama"] | None = None
    embed_api_key: str | None = Field(default=None, repr=False)
    llm_options: dict | None = Field(default=None, repr=False)
    max_tokens: int = Field(default=2048, ge=256, le=8192)
    project_id: str | None = Field(default=None, pattern=r"^(general|[a-f0-9]{32})$")

    def resolve(self) -> ModelSettings:
        defaults = default_settings()
        values = asdict(defaults)
        values.update(self.model_dump(exclude_none=True))
        if values.get("project_id") == "general":
            values["project_id"] = None
        if values["protocol"] == "ollama" and self.base_url is None:
            values["base_url"] = OLLAMA_BASE_URL
        try:
            resolved = ModelSettings(**values)
            if self.api_key is None and (resolved.base_url != defaults.base_url or resolved.protocol != defaults.protocol):
                values["api_key"] = ""
            if self.embed_api_key is None and resolved.embedding_settings.base_url != defaults.embedding_settings.base_url:
                values["embed_api_key"] = ""
            return ModelSettings(**values)
        except ProviderError as exc:
            raise HTTPException(422, str(exc)) from exc


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000, repr=False)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError('Nom vide')
        return value.strip()


class AudioInput(BaseModel):
    detail: Literal['brief', 'standard', 'detailed'] = 'standard'
    tts_model: str = Field(default=DEFAULT_TTS_MODEL, min_length=1, max_length=200)
    settings: SettingsInput = Field(default_factory=SettingsInput)


class DeleteInput(BaseModel):
    source: str = Field(min_length=1)
    settings: SettingsInput = Field(default_factory=SettingsInput)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatInput(BaseModel):
    question: str = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)
    conversation_id: str | None = None
    settings: SettingsInput = Field(default_factory=SettingsInput)
    top_k: int = Field(default=5, ge=1, le=15)
    similarity_cutoff: float = Field(default=0.2, ge=0, le=1)

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value):
        if not value.strip():
            raise ValueError("Question vide")
        return value.strip()


def _next_chunk(iterator):
    # StopIteration ne doit pas traverser une Future asyncio.
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


async def stream_answer(payload: ChatInput, request: Request, store=None, turn_id=None, history=None, resolved=None):
    iterator = None
    content = ""
    sources = []
    status = "interrupted"
    error_message = "Réponse interrompue."
    last_saved = time.monotonic()

    def event(kind, **data):
        return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"

    try:
        response = await run_in_threadpool(
            answer_question, payload.question,
            history or [],
            payload.top_k, payload.similarity_cutoff, resolved or payload.settings.resolve(),
        )
        iterator = iter(response.chunks)
        sources = [asdict(source) for source in response.sources]
        yield event("sources", sources=sources)
        while not await request.is_disconnected():
            has_next, text = await run_in_threadpool(_next_chunk, iterator)
            if not has_next:
                if store:
                    await run_in_threadpool(store.finish_turn, turn_id, content, sources, "complete")
                status = "complete"
                yield event("done")
                break
            content += text
            if store and time.monotonic() - last_saved >= 1:
                await run_in_threadpool(store.save_progress, turn_id, content, sources)
                last_saved = time.monotonic()
            yield event("chunk", text=text)
    except (RagError, ProviderError) as exc:
        status, error_message = "error", str(exc)
        yield event("error", message=error_message)
    except Exception:  # noqa: BLE001 - Toute erreur du moteur doit rester sans détail confidentiel.
        status, error_message = "error", "La réponse a été interrompue. Vérifiez le serveur local et réessayez."
        yield event("error", message=error_message)
    finally:
        with anyio.CancelScope(shield=True):
            if store and status != "complete":
                await run_in_threadpool(store.finish_turn, turn_id, content, sources, status, error_message)
            if iterator is not None and hasattr(iterator, "close"):
                await run_in_threadpool(iterator.close)


async def engine_call(function, *args):
    """Le moteur synchrone s’exécute hors de la boucle HTTP."""
    try:
        return await run_in_threadpool(function, *args)
    except (IngestionError, ProviderError) as exc:
        raise HTTPException(502, str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "Cette opération a échoué. Réessayez ou vérifiez le serveur local.") from exc


def create_app(config_path: Path | None = None) -> FastAPI:
    application = FastAPI(title="DocMind", docs_url=None, redoc_url=None, openapi_url=None)
    application.state.sessions = SessionStore()
    application.state.config = None
    store = ProjectStore(Path(os.getenv('DOCMIND_PROJECTS_PATH', str(ROOT / 'data/projects'))))
    application.state.projects = store
    path = config_path or Path(os.getenv("DOCMIND_CONFIG", str(ROOT / "config.yaml")))

    async def auth_config():
        if application.state.config is None:
            try:
                application.state.config = await run_in_threadpool(load_auth_config, path)
            except AuthConfigError as exc:
                raise HTTPException(503, str(exc)) from exc
        return application.state.config

    async def require_project(request: Request, project_id: str):
        if not await run_in_threadpool(store.owns, request.state.session.username, project_id):
            raise HTTPException(404, 'Projet introuvable.')

    async def project_settings(payload: SettingsInput, request: Request):
        if not payload.project_id:
            raise HTTPException(422, 'Sélectionnez un projet pour cette opération.')
        await require_project(request, payload.project_id)
        return payload.resolve()

    @application.middleware("http")
    async def protect_api(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Origine de la requête non autorisée."}, status_code=403)
            if request.url.path != "/api/login":
                try:
                    config = await auth_config()
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                token = request.cookies.get(config["cookie"]["name"], "")
                session = application.state.sessions.get(token)
                if session is None:
                    return JSONResponse({"detail": "Votre session a expiré. Reconnectez-vous."}, status_code=401)
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf_token):
                    return JSONResponse({"detail": "Session de sécurité invalide. Reconnectez-vous."}, status_code=403)
                request.state.session = session
                request.state.session_token = token
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Les erreurs brutes peuvent inclure les mots de passe ou clés.
        return JSONResponse({"detail": "Les informations envoyées sont invalides. Vérifiez les champs."}, status_code=422)

    @application.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @application.post("/api/login")
    async def login(payload: LoginInput, request: Request):
        config = await auth_config()
        identity = await run_in_threadpool(verify_credentials, config, payload.username, payload.password)
        if identity is None:
            raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
        cookie = config["cookie"]
        token, session = application.state.sessions.create(*identity, float(cookie["expiry_days"]))
        response = JSONResponse({"username": session.username, "name": session.name, "csrf_token": session.csrf_token})
        response.set_cookie(cookie["name"], token, max_age=int(float(cookie["expiry_days"]) * 86400), httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return response

    @application.get("/api/session")
    async def session(request: Request):
        settings = asdict(default_settings())
        settings.pop("api_key", None)
        settings.pop("embed_api_key", None)
        settings["has_embed_api_key"] = bool(default_settings().embed_api_key)
        settings["has_api_key"] = bool(default_settings().api_key)
        return {"username": request.state.session.username, "name": request.state.session.name, "csrf_token": request.state.session.csrf_token, "settings": settings, "ollama_settings": {"base_url": OLLAMA_BASE_URL, "embed_model": EMBED_MODEL, "llm_model": LLM_MODEL}}

    @application.post("/api/logout")
    async def logout(request: Request):
        config = await auth_config()
        application.state.sessions.revoke(request.state.session_token)
        response = JSONResponse({"success": True})
        response.delete_cookie(config["cookie"]["name"], httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return response

    @application.post("/api/models")
    async def models(payload: SettingsInput):
        catalog = await engine_call(list_models, payload.resolve())
        result = asdict(catalog)
        if not catalog.tts:
            result.pop('tts')
        return result

    @application.get('/api/projects')
    async def projects(request: Request):
        return {'projects': await engine_call(store.list_projects, request.state.session.username), 'general': await engine_call(store.general_project)}

    @application.post('/api/projects')
    async def create_project(payload: ProjectInput, request: Request):
        return await engine_call(store.create, request.state.session.username, payload.name)

    @application.get('/api/projects/{project_id}/audio')
    async def audio_history(project_id: str, request: Request):
        await require_project(request, project_id)
        return {'summaries': await engine_call(store.summaries, project_id)}

    @application.post('/api/projects/{project_id}/audio')
    async def create_audio(project_id: str, payload: AudioInput, request: Request):
        await require_project(request, project_id)
        if payload.settings.project_id and payload.settings.project_id != project_id:
            raise HTTPException(422, 'Le projet sélectionné ne correspond pas à la demande.')
        settings = await project_settings(payload.settings.model_copy(update={'project_id': project_id}), request)
        async with project_operation(store, project_id, engine_call):
            result = await engine_call(generate_audio_summary, settings, payload.detail, payload.tts_model)
            return await engine_call(store.save_summary, project_id, payload.detail, result, settings, payload.tts_model)

    @application.get('/api/projects/{project_id}/audio/{summary_id}.wav')
    async def audio_file(project_id: str, summary_id: str, request: Request):
        await require_project(request, project_id)
        path = await engine_call(store.audio_path, project_id, summary_id)
        if path is None or not path.is_file():
            raise HTTPException(404, 'Résumé audio introuvable.')
        return FileResponse(path, media_type='audio/wav', filename=f'resume-{summary_id}.wav', content_disposition_type='inline')

    @application.post("/api/documents/list")
    async def documents(payload: SettingsInput, request: Request):
        settings = await project_settings(payload, request)
        async with project_operation(store, payload.project_id, engine_call):
            rows = await engine_call(list_documents, settings)
        return {"documents": [asdict(row) for row in rows]}

    @application.post("/api/documents/upload")
    async def upload(request: Request, files: Annotated[list[UploadFile], File()], settings: Annotated[str, Form()] = "{}"):
        try:
            resolved = await project_settings(SettingsInput.model_validate_json(settings), request)
        except ValidationError as exc:
            raise HTTPException(422, "Réglages de modèle invalides.") from exc
        results = []
        for file in files:
            name = file.filename or "document"
            try:
                if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise IngestionError("Format non pris en charge. Utilisez PDF, DOCX, TXT, Markdown ou HTML.")
                content = await file.read(MAX_UPLOAD_BYTES + 1)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise IngestionError("Ce fichier dépasse la limite de 200 Mo.")
                project_id = SettingsInput.model_validate_json(settings).project_id
                async with project_operation(store, project_id, engine_call):
                    count = await engine_call(ingest_document, content, name, resolved)
                results.append({"name": name, "success": True, "fragments": count})
            except (IngestionError, HTTPException) as exc:
                results.append({"name": name, "success": False, "error": exc.detail if isinstance(exc, HTTPException) else str(exc)})
            finally:
                await file.close()
        return {"results": results}

    @application.post("/api/documents/delete")
    async def delete(payload: DeleteInput, request: Request):
        settings = await project_settings(payload.settings, request)
        async with project_operation(store, payload.settings.project_id, engine_call):
            await engine_call(delete_document, payload.source, settings)
        return {"success": True}

    @application.post("/api/chat")
    async def chat(payload: ChatInput, request: Request):
        settings = await project_settings(payload.settings, request)
        if not payload.conversation_id:
            raise HTTPException(422, 'Sélectionnez une conversation pour cette discussion.')
        if payload.history:
            raise HTTPException(422, 'L’historique de la conversation est conservé par le serveur.')
        lease = store.operation(payload.settings.project_id, payload.conversation_id)
        await engine_call(lease.__enter__)
        try:
            turn_id, history = await engine_call(store.start_turn, payload.settings.project_id, payload.conversation_id, payload.question)
        except BaseException:
            await run_in_threadpool(lease.__exit__, None, None, None)
            raise

        async def release():
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(lease.__exit__, None, None, None)

        async def stream():
            try:
                async for line in stream_answer(payload, request, store, turn_id, history, settings):
                    yield line
            finally:
                await release()

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"}, background=BackgroundTask(release))

    application.include_router(workspace_router(store, require_project, engine_call, lambda project: delete_project_documents(project)))

    application.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return application


app = create_app()
