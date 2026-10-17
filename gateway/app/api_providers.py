"""AI provider management (like a provider/keys console).

Providers are *metadata + credentials* used by the agents when they generate
patches. Managing them never touches the agent <-> gateway control channel: the
choice is attached to a task and travels with ``task_assign`` / ``task_resume``.

Reading requires the ``pwa`` scope (the app shows them); every mutation requires
``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from .auth import AuthContext
from .service import ServiceError, TaskService


def _auth(request: Request) -> AuthContext:
    context = getattr(request.state, "auth", None)
    if context is None:  # pragma: no cover - middleware always sets it
        raise HTTPException(status_code=401, detail={"error": "Not authenticated"})
    return context


def _require_admin(request: Request) -> AuthContext:
    context = _auth(request)
    if not context.has("admin"):
        raise HTTPException(
            status_code=403,
            detail={"error": "Admin scope required to manage AI providers"},
        )
    return context


def _raise(error: ServiceError) -> HTTPException:
    payload: dict[str, Any] = {"error": error.message}
    payload.update(error.extra)
    return HTTPException(status_code=error.status_code, detail=payload)


def build_router(service: TaskService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["providers"])

    @router.get("/providers")
    async def list_providers(request: Request) -> dict[str, Any]:
        context = _auth(request)
        return {
            "providers": await service.list_providers(),
            "canManage": context.has("admin"),
        }

    @router.post("/providers", status_code=201)
    async def create_provider(
        request: Request, payload: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        _require_admin(request)
        try:
            provider = await service.create_provider(payload or {})
        except ServiceError as error:
            raise _raise(error) from error
        return {"provider": provider}

    @router.patch("/providers/{provider_id}")
    async def update_provider(
        request: Request, provider_id: str, payload: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        _require_admin(request)
        try:
            provider = await service.update_provider(provider_id, payload or {})
        except ServiceError as error:
            raise _raise(error) from error
        return {"provider": provider}

    @router.delete("/providers/{provider_id}")
    async def delete_provider(request: Request, provider_id: str) -> dict[str, Any]:
        _require_admin(request)
        try:
            await service.delete_provider(provider_id)
        except ServiceError as error:
            raise _raise(error) from error
        return {"deleted": provider_id}

    @router.post("/providers/{provider_id}/default")
    async def set_default_provider(request: Request, provider_id: str) -> dict[str, Any]:
        _require_admin(request)
        try:
            provider = await service.set_default_provider(provider_id)
        except ServiceError as error:
            raise _raise(error) from error
        return {"provider": provider}

    @router.get("/providers/{provider_id}/models")
    async def provider_models(
        request: Request, provider_id: str, refresh: bool = False
    ) -> dict[str, Any]:
        """Live model list for the provider (merged with the configured aliases)."""
        _auth(request)
        try:
            return await service.provider_models(provider_id, refresh=refresh)
        except ServiceError as error:
            raise _raise(error) from error

    return router
