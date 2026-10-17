"""ASGI application factory: auth, CORS, routers, static PWA, lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api_agent import build_agent_router
from .api_auth import build_router as build_auth_router
from .api_providers import build_router as build_providers_router
from .api_pwa import build_router
from .auth import resolve_token
from .config import Settings
from .db import Database, utcnow
from .events import EventBus
from .hub import AgentHub
from .runtime import STOPPING
from .scheduler import Scheduler
from .service import TaskService

logger = logging.getLogger("devagent")

# Origins allowed to talk to the API directly: the Capacitor iOS shell, local dev
# servers and the public hostname. The PWA served from the gateway is same-origin.
DEFAULT_CORS_ORIGINS = (
    "capacitor://localhost",
    "ionic://localhost",
    "http://localhost",
    "https://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://deepseek-api.cs-hub.xyz",
)


def is_protected_path(path: str, root_path: str) -> bool:
    """API and agent channels require the shared token; static PWA assets are public."""
    normalized = path.rstrip("/") or "/"
    if normalized == "/healthz":
        return False
    if root_path and (normalized == root_path or normalized.startswith(f"{root_path}/")):
        normalized = normalized[len(root_path) :].rstrip("/") or "/"
    return normalized == "/api" or normalized.startswith("/api/") or normalized == "/agent" or normalized.startswith(
        "/agent/"
    )


# Reachable without a credential: the login screen needs them.
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/config"}


def sub_path(path: str, root_path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if root_path and (normalized == root_path or normalized.startswith(f"{root_path}/")):
        normalized = normalized[len(root_path) :].rstrip("/") or "/"
    return normalized


def allowed_scopes(path: str) -> set[str] | None:
    """Which scopes may reach this path (any of them); ``None`` = no extra check."""
    if path.startswith("/api/tokens"):
        return {"admin"}
    if path.startswith("/api/auth"):
        return None
    if path.startswith("/api/agent"):
        # agent heartbeats (agent scope) and the fleet view (pwa/admin)
        return {"pwa", "agent", "admin"}
    if path.startswith("/api/"):
        return {"pwa", "admin"}
    if path == "/agent" or path.startswith("/agent/"):
        return {"agent"}
    return None


def extract_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header = request.headers.get("x-devagent-token")
    if header:
        return header.strip()
    return (request.query_params.get("token") or "").strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()

    db = Database(settings.database_path)
    bus = EventBus(settings.redis_url, settings.event_channel)
    hub = AgentHub()
    service = TaskService(db, bus, hub, settings)
    service.bind_runtime(version=__version__)
    scheduler = Scheduler(service, db, hub, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.connect()
        await bus.start()
        # A restart kills every socket: reconcile before serving traffic so no agent
        # stays "online" and no task stays "running" without a worker.
        await service.reconcile_on_startup()
        await scheduler.start()

        # Uvicorn waits for open connections *before* running this lifespan
        # shutdown, so SSE/WebSocket clients must be told to leave as soon as the
        # signal arrives; otherwise systemd SIGKILLs the process and the tunnel
        # answers 502 for the whole stop window.
        loop = asyncio.get_running_loop()
        previous_handlers: dict[int, Any] = {}

        def _on_signal(signum: int, frame: Any) -> None:
            STOPPING.set()
            with contextlib.suppress(RuntimeError):
                loop.create_task(hub.close_all())
            previous = previous_handlers.get(signum)
            if callable(previous):
                previous(signum, frame)

        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.getsignal(sig)
            with contextlib.suppress(ValueError):  # not in the main thread
                signal.signal(sig, _on_signal)

        logger.info(
            "devagent gateway %s ready (root=%s, bus=%s, db=%s)",
            __version__,
            settings.root_path,
            bus.backend,
            settings.database_path,
        )
        try:
            yield
        finally:
            # Tell long-lived responses (SSE, agent long-poll) to finish first.
            STOPPING.set()
            await scheduler.stop()
            await hub.close_all()
            await bus.close()
            await db.close()

    app = FastAPI(
        title="DevAgent Gateway",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.bus = bus
    app.state.hub = hub
    app.state.service = service
    app.state.scheduler = scheduler

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEFAULT_CORS_ORIGINS),
        allow_origin_regex=r"^(capacitor|ionic|http|https)://(localhost|127\.0\.0\.1|.*\.cs-hub\.xyz)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if request.method == "OPTIONS" or not is_protected_path(path, settings.root_path):
            return await call_next(request)
        sub = sub_path(path, settings.root_path)
        if sub in PUBLIC_API_PATHS:
            return await call_next(request)

        context = await resolve_token(db, settings, extract_token(request))
        if context is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Invalid or missing credentials",
                    "hint": "Log in at the app, or send Authorization: Bearer <token> / ?token=<token>",
                },
            )

        required = allowed_scopes(sub)
        if required and not any(context.has(scope) for scope in required):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "This token does not have the required scope",
                    "required": sorted(required),
                    "granted": list(context.scopes),
                },
            )

        request.state.auth = context
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "time": utcnow(),
            "version": __version__,
            "agentSockets": hub.connection_count,
            "eventBus": bus.backend,
        }

    app.include_router(build_auth_router(db, settings), prefix=settings.root_path)
    app.include_router(build_providers_router(service), prefix=settings.root_path)
    app.include_router(build_router(service, settings), prefix=settings.root_path)
    agent_router = build_agent_router(service, hub, settings)
    app.include_router(agent_router, prefix=settings.root_path)
    app.include_router(agent_router, prefix=f"{settings.root_path}/api")

    _mount_pwa(app, settings)
    return app


def _mount_pwa(app: FastAPI, settings: Settings) -> None:
    """Serve the built PWA under ``root_path`` with SPA fallback."""
    static_dir = settings.static_dir
    root = settings.root_path
    index = (static_dir / "index.html") if static_dir else None

    if static_dir and static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount(f"{root}/assets", StaticFiles(directory=assets), name="pwa-assets")

    @app.get(root, include_in_schema=False)
    @app.get(f"{root}/", include_in_schema=False)
    async def pwa_root() -> Response:
        return _index_response(index)

    @app.get(f"{root}/{{path:path}}", include_in_schema=False)
    async def pwa_assets(path: str) -> Response:
        if path == "api" or path.startswith("api/") or path == "agent" or path.startswith("agent/"):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        if static_dir and static_dir.exists():
            candidate = (static_dir / path).resolve()
            try:
                candidate.relative_to(static_dir.resolve())
            except ValueError:
                return JSONResponse(status_code=404, content={"error": "Not found"})
            if candidate.is_file():
                return FileResponse(candidate)
        # SPA deep links (protected API paths are registered above and win).
        return _index_response(index)


def _index_response(index: Path | None) -> Response:
    if index is not None and index.exists():
        return FileResponse(index, media_type="text/html")
    return JSONResponse(
        status_code=503,
        content={
            "error": "PWA bundle not built yet",
            "hint": "Run `npm install && npm run build` in the mobile repo and set DEVAGENT_STATIC_DIR to its dist/ folder",
        },
    )


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
