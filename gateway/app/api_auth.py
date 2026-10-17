"""Login + API-key management (provider-like token lifecycle)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from .auth import (
    AuthContext,
    AuthError,
    KIND_SCOPES,
    create_api_token,
    login,
    resolve_token,
    token_to_dict,
)
from .config import Settings
from .db import Database

LOGIN_HINT = "Send a valid token (Authorization: Bearer …) or log in with the admin password."


def _error(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message})


def _auth(request: Request) -> AuthContext:
    context = getattr(request.state, "auth", None)
    if context is None:  # pragma: no cover - middleware always sets it
        raise _error("Not authenticated", 401)
    return context


def _require_admin(request: Request) -> AuthContext:
    context = _auth(request)
    if not context.has("admin"):
        raise _error("This token cannot manage API keys (admin scope required)", 403)
    return context


def _bootstrap_entries(settings: Settings) -> list[dict[str, Any]]:
    entries = [
        {
            "id": "bootstrap",
            "name": "bootstrap (server env)",
            "kind": "admin",
            "scopes": list(KIND_SCOPES["admin"]),
            "prefix": settings.pwa_token[:12],
            "createdAt": None,
            "createdBy": "environment",
            "lastUsedAt": None,
            "expiresAt": None,
            "revokedAt": None,
            "status": "active",
            "bootstrap": True,
        }
    ]
    if settings.agent_token != settings.pwa_token:
        entries.append(
            {
                "id": "bootstrap-agent",
                "name": "bootstrap agent (server env)",
                "kind": "agent",
                "scopes": ["agent"],
                "prefix": settings.agent_token[:12],
                "createdAt": None,
                "createdBy": "environment",
                "lastUsedAt": None,
                "expiresAt": None,
                "revokedAt": None,
                "status": "active",
                "bootstrap": True,
            }
        )
    return entries


def build_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["auth"])

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------
    @router.get("/auth/config")
    async def auth_config() -> dict[str, Any]:
        """What the login screen should offer (no secrets)."""
        return {
            "passwordLogin": bool(settings.admin_password_hash),
            "tokenLogin": True,
            "tokenKinds": sorted(KIND_SCOPES),
            "sessionTtlHours": settings.session_ttl_hours,
            "gateway": {"rootPath": settings.root_path, "version": settings_version()},
        }

    @router.post("/auth/login")
    async def auth_login(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        token = (payload or {}).get("token")
        password = (payload or {}).get("password")
        if not token and not password:
            raise _error("Provide 'token' or 'password'")
        try:
            context, issued = await login(
                db, settings, token=token or None, password=password or None
            )
        except AuthError as error:
            raise _error(error.message, error.status_code) from error
        return {
            "token": issued or token,
            "issued": issued is not None,
            "auth": context.as_dict(),
        }

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------
    @router.get("/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        context = _auth(request)
        return {
            "auth": context.as_dict(),
            "canManageTokens": context.has("admin"),
            "canUseTasks": context.has("pwa") or context.has("admin"),
            "canRunAgent": context.has("agent") or context.has("admin"),
        }

    @router.post("/auth/logout")
    async def auth_logout(request: Request) -> dict[str, Any]:
        """Revoke the credential if it is a server-issued session token."""
        context = _auth(request)
        revoked = False
        if context.token_id and context.kind == "session":
            await db.revoke_token(context.token_id)
            revoked = True
        return {"ok": True, "revoked": revoked}

    # ------------------------------------------------------------------
    # API keys (admin only)
    # ------------------------------------------------------------------
    @router.get("/tokens")
    async def list_tokens(request: Request) -> dict[str, Any]:
        _require_admin(request)
        rows = [token_to_dict(row) for row in await db.list_tokens()]
        return {
            "tokens": _bootstrap_entries(settings) + rows,
            "kinds": [
                {"kind": kind, "scopes": list(scopes)}
                for kind, scopes in KIND_SCOPES.items()
                if kind != "session"
            ],
        }

    @router.post("/tokens", status_code=201)
    async def create_token(request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        context = _require_admin(request)
        name = str((payload or {}).get("name") or "").strip()
        kind = str((payload or {}).get("kind") or "pwa").strip().lower()
        expires_in_days = (payload or {}).get("expiresInDays")

        if not name:
            raise _error("A name is required (e.g. 'iPhone-Artur' or 'macbook-agent')")
        if kind not in {"admin", "pwa", "agent"}:
            raise _error(f"Invalid kind '{kind}'. Use admin, pwa or agent.")
        try:
            days = int(expires_in_days) if expires_in_days not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise _error("expiresInDays must be an integer") from None

        raw, row = await create_api_token(
            db,
            name=name,
            kind=kind,
            created_by=context.name,
            expires_in_days=days,
        )
        return {
            "token": raw,
            "warning": "Copy this token now - it is shown only once.",
            "meta": token_to_dict(row),
        }

    @router.post("/tokens/{token_id}/revoke")
    async def revoke_token(request: Request, token_id: str) -> dict[str, Any]:
        _require_admin(request)
        if token_id in {"bootstrap", "bootstrap-agent"}:
            raise _error(
                "Bootstrap tokens live in the server environment; rotate them in "
                "/etc/deepseek-devagent.env instead.",
                409,
            )
        row = await db.revoke_token(token_id)
        if row is None:
            raise _error("Token not found", 404)
        return {"revoked": token_id, "meta": token_to_dict(row)}

    @router.delete("/tokens/{token_id}")
    async def delete_token(request: Request, token_id: str) -> dict[str, Any]:
        _require_admin(request)
        if token_id in {"bootstrap", "bootstrap-agent"}:
            raise _error(
                "Bootstrap tokens live in the server environment; rotate them in "
                "/etc/deepseek-devagent.env instead.",
                409,
            )
        if not await db.delete_token(token_id):
            raise _error("Token not found", 404)
        return {"deleted": token_id}

    return router


def settings_version() -> str:
    from . import __version__

    return __version__
