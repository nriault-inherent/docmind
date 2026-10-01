"""Parcours de projets et conversations ; contrôles conservés par le serveur principal."""

from contextlib import asynccontextmanager

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field


@asynccontextmanager
async def project_operation(store, project_id, engine_call, conversation_id=None):
    lease = store.operation(project_id, conversation_id)
    await engine_call(lease.__enter__)
    try:
        yield
    finally:
        with anyio.CancelScope(shield=True):
            await engine_call(lease.__exit__, None, None, None)


class RenameInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def workspace_router(store, require_project, engine_call, cleanup):
    router = APIRouter(prefix="/api/projects")

    @router.patch("/{project_id}")
    async def rename(project_id: str, payload: RenameInput, request: Request):
        await require_project(request, project_id)
        return await engine_call(store.rename_project, project_id, payload.name)

    @router.delete("/{project_id}")
    async def delete(project_id: str, request: Request):
        await require_project(request, project_id)
        await engine_call(store.delete_project, project_id, cleanup)
        return {"success": True}

    @router.get("/{project_id}/conversations")
    async def conversations(project_id: str, request: Request):
        await require_project(request, project_id)
        return {"conversations": await engine_call(store.list_conversations, project_id)}

    @router.post("/{project_id}/conversations")
    async def create_conversation(project_id: str, request: Request):
        await require_project(request, project_id)
        return await engine_call(store.create_conversation, project_id)

    @router.get("/{project_id}/conversations/{conversation_id}")
    async def conversation(project_id: str, conversation_id: str, request: Request):
        await require_project(request, project_id)
        return await engine_call(store.get_conversation, project_id, conversation_id)

    return router
