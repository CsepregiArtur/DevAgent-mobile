"""Authentication: hashed API tokens, scopes and session tokens.

Model (deliberately provider-like):

* every credential is a token: ``dvk_…`` for long lived keys, ``dvs_…`` for
  sessions issued by the password login. Only a SHA-256 hash is stored;
  the plaintext is shown exactly once, at creation.
* kinds map to scopes:

  =========  =========================================
  kind       scopes
  =========  =========================================
  admin      admin, pwa, agent  (manage keys + full API)
  pwa        pwa                (browser / iOS app)
  agent      agent              (MacBook / .exe worker)
  session    admin, pwa         (password login, expiring)
  =========  =========================================

* the values from the environment (``DEVAGENT_TOKEN``, ``DEVAGENT_AGENT_TOKEN``)
  keep working as **bootstrap** credentials with admin / agent scopes, so an
  existing installation never locks itself out. They are not stored in the DB and
  cannot be revoked from the UI - rotate them by editing the env file.

Passwords use ``hashlib.scrypt`` (stdlib, no extra dependency). Generate a hash:

    python -m app.cli hash-password
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .db import Database, parse_ts, utcnow

TOKEN_PREFIX = "dvk_"
SESSION_PREFIX = "dvs_"

KIND_SCOPES: dict[str, tuple[str, ...]] = {
    "admin": ("admin", "pwa", "agent"),
    "pwa": ("pwa",),
    "agent": ("agent",),
    "session": ("admin", "pwa"),
}


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class AuthContext:
    token_id: str | None
    name: str
    kind: str
    scopes: tuple[str, ...] = field(default_factory=tuple)
    is_bootstrap: bool = False

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokenId": self.token_id,
            "name": self.name,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "bootstrap": self.is_bootstrap,
        }


# ----------------------------------------------------------------------
# primitives
# ----------------------------------------------------------------------
def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_prefix(raw: str) -> str:
    return raw[:12]


def generate_token(kind: str) -> str:
    prefix = SESSION_PREFIX if kind == "session" else TOKEN_PREFIX
    return prefix + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:44]


def scopes_for_kind(kind: str) -> tuple[str, ...]:
    try:
        return KIND_SCOPES[kind]
    except KeyError as exc:  # pragma: no cover - guarded by the API layer
        raise AuthError(f"Unknown token kind: {kind}", 400) from exc


def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n,
        r,
        p,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def _is_expired(row: dict[str, Any]) -> bool:
    expires = parse_ts(row.get("expires_at"))
    return expires is not None and expires <= datetime.now(timezone.utc)


def _is_revoked(row: dict[str, Any]) -> bool:
    return bool(row.get("revoked_at"))


# ----------------------------------------------------------------------
# resolution
# ----------------------------------------------------------------------
async def resolve_token(db: Database, settings: Settings, raw: str) -> AuthContext | None:
    """Map a bearer token to an :class:`AuthContext`, or ``None`` when invalid."""
    if not raw:
        return None

    # Bootstrap credentials from the environment.
    if hmac.compare_digest(raw, settings.pwa_token):
        return AuthContext(
            token_id=None, name="bootstrap (env)", kind="admin", scopes=KIND_SCOPES["admin"], is_bootstrap=True
        )
    if hmac.compare_digest(raw, settings.agent_token):
        return AuthContext(
            token_id=None, name="bootstrap agent (env)", kind="agent", scopes=("agent",), is_bootstrap=True
        )

    row = await db.find_token_by_hash(hash_token(raw))
    if row is None or _is_revoked(row) or _is_expired(row):
        return None

    # Throttle writes: at most one update per minute per token.
    last_used = parse_ts(row.get("last_used_at"))
    if last_used is None or (datetime.now(timezone.utc) - last_used).total_seconds() > 60:
        await db.touch_token(row["id"])

    scopes = tuple(scope for scope in str(row["scope"]).split() if scope)
    return AuthContext(
        token_id=row["id"], name=row["name"], kind=row["kind"], scopes=scopes
    )


# ----------------------------------------------------------------------
# login
# ----------------------------------------------------------------------
async def login(
    db: Database, settings: Settings, *, token: str | None = None, password: str | None = None
) -> tuple[AuthContext, str | None]:
    """Authenticate with either an existing token or the admin password.

    Returns ``(context, issued_token)``; ``issued_token`` is only set for a
    password login (the freshly minted session token, shown once).
    """
    if token:
        context = await resolve_token(db, settings, token.strip())
        if context is None:
            raise AuthError("Invalid or revoked token")
        return context, None

    if password:
        if not verify_password(password, settings.admin_password_hash):
            raise AuthError("Invalid password")
        raw = generate_token("session")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        await db.create_token(
            name="password session",
            kind="session",
            scope=" ".join(KIND_SCOPES["session"]),
            prefix=token_prefix(raw),
            token_hash=hash_token(raw),
            created_by="admin-password",
            expires_at=expires_at,
        )
        context = await resolve_token(db, settings, raw)
        assert context is not None
        return context, raw

    raise AuthError("Provide a token or the admin password", 400)


async def create_api_token(
    db: Database,
    *,
    name: str,
    kind: str,
    created_by: str,
    expires_in_days: int | None = None,
) -> tuple[str, dict[str, Any]]:
    scopes = scopes_for_kind(kind)
    raw = generate_token(kind)
    expires_at = None
    if expires_in_days:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    row = await db.create_token(
        name=name,
        kind=kind,
        scope=" ".join(scopes),
        prefix=token_prefix(raw),
        token_hash=hash_token(raw),
        created_by=created_by,
        expires_at=expires_at,
    )
    return raw, row


def token_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "scopes": str(row["scope"]).split(),
        "prefix": row["prefix"],
        "createdAt": row["created_at"],
        "createdBy": row.get("created_by"),
        "lastUsedAt": row.get("last_used_at"),
        "expiresAt": row.get("expires_at"),
        "revokedAt": row.get("revoked_at"),
        "status": "revoked"
        if row.get("revoked_at")
        else ("expired" if _is_expired(row) else "active"),
    }


def now_iso() -> str:
    return utcnow()
