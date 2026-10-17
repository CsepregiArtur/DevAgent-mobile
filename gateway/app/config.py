"""Runtime configuration (environment driven, 12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT_PATH = "/mobile"
DEFAULT_WORKSPACES = (
    "~/dev/mobile-agent-platform",
    "~/dev/customer-dashboard",
    "~/dev/infrastructure-iac",
)


class ConfigError(RuntimeError):
    """Raised when the environment is not usable."""


def _env(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - guarded by tests
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable view over the process environment."""

    pwa_token: str
    agent_token: str
    admin_password_hash: str | None
    session_ttl_hours: int
    root_path: str
    host: str
    port: int
    database_path: Path
    redis_url: str | None
    static_dir: Path | None
    agent_offline_seconds: int
    heartbeat_interval_seconds: int
    approval_timeout_seconds: int
    queue_scan_seconds: float
    workspaces: tuple[str, ...]
    default_workspace: str
    deepseek_base_url: str
    default_model: str
    max_events_per_task: int

    # -- derived helpers -------------------------------------------------
    @property
    def event_channel(self) -> str:
        return "devagent:events"

    @property
    def agent_channel(self) -> str:
        return "devagent:agent"

    @staticmethod
    def load() -> "Settings":
        pwa_token = _env("DEVAGENT_TOKEN")
        if not pwa_token:
            raise ConfigError("DEVAGENT_TOKEN must be set (shared token for the PWA)")
        if len(pwa_token) < 16:
            raise ConfigError("DEVAGENT_TOKEN must contain at least 16 characters")

        agent_token = _env("DEVAGENT_AGENT_TOKEN", pwa_token) or pwa_token

        workspaces_raw = _env("DEVAGENT_WORKSPACES")
        workspaces = (
            tuple(w.strip() for w in workspaces_raw.split(",") if w.strip())
            if workspaces_raw
            else DEFAULT_WORKSPACES
        )

        static_raw = _env("DEVAGENT_STATIC_DIR")
        static_dir = Path(static_raw).expanduser() if static_raw else None

        database_raw = _env("DEVAGENT_DATABASE", "/var/lib/devagent/gateway.sqlite3")
        root_path = _env("DEVAGENT_ROOT_PATH", DEFAULT_ROOT_PATH) or DEFAULT_ROOT_PATH
        root_path = "/" + root_path.strip("/")

        return Settings(
            pwa_token=pwa_token,
            agent_token=agent_token,
            # Optional password login (hash generated with `python -m app.cli hash-password`).
            admin_password_hash=_env("DEVAGENT_ADMIN_PASSWORD_HASH"),
            session_ttl_hours=_env_int("DEVAGENT_SESSION_TTL_HOURS", 12),
            root_path=root_path,
            host=_env("DEVAGENT_HOST", "127.0.0.1") or "127.0.0.1",
            port=_env_int("DEVAGENT_PORT", 8082),
            database_path=Path(database_raw).expanduser(),
            redis_url=_env("DEVAGENT_REDIS_URL", "redis://127.0.0.1:6379/0"),
            static_dir=static_dir,
            agent_offline_seconds=_env_int("DEVAGENT_AGENT_OFFLINE_SECONDS", 45),
            heartbeat_interval_seconds=_env_int("DEVAGENT_HEARTBEAT_SECONDS", 10),
            approval_timeout_seconds=_env_int("DEVAGENT_APPROVAL_TIMEOUT_SECONDS", 0),
            queue_scan_seconds=float(_env_int("DEVAGENT_QUEUE_SCAN_MS", 2000)) / 1000.0,
            workspaces=workspaces,
            default_workspace=_env("DEVAGENT_DEFAULT_WORKSPACE", workspaces[0]) or workspaces[0],
            deepseek_base_url=_env("DEVAGENT_DEEPSEEK_BASE_URL", "https://deepseek-api.cs-hub.xyz/v1")
            or "",
            default_model=_env("DEVAGENT_DEFAULT_MODEL", "DeepSeek-Coder-V2.5") or "",
            max_events_per_task=_env_int("DEVAGENT_MAX_EVENTS_PER_TASK", 500),
        )
