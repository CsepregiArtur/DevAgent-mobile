#!/usr/bin/env python3
"""DevAgent macOS reference agent.

Runs on the MacBook, connects *outbound* to the gateway WebSocket
(``wss://<host>/mobile/agent/ws``) and executes coding tasks:

    queued  ->  running  ->  awaiting_approval  ->  completed / failed

Design goals
------------
* **Isolated edits**: never touch the user's working tree; every task gets its own
  ``git worktree`` on a throw-away branch under ``DEVAGENT_STATE_DIR/worktrees``.
* **Nothing is applied before approval**: the generated patch is sent to the
  gateway as an approval request (hash protected); only after ``approve`` is it
  applied - optionally only the approved files/hunks.
* **Safe resume**: after each meaningful stage a checkpoint is pushed to the
  gateway *and* persisted locally, so a dropped connection (or a rebooted laptop)
  resumes the task instead of redoing it.
* **No inbound ports**: only outbound TLS to the gateway.
* **No hidden shell**: arbitrary commands run only when
  ``DEVAGENT_APPROVE_COMMANDS=1`` and the operator approves them.

Requirements: Python 3.9+, ``pip3 install websockets``, ``git``, optionally
``gh`` (for pull requests) and an LLM endpoint (the private DeepSeek proxy).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:  # websockets is the only third-party runtime dependency
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover - guided error
    print(
        "Missing dependency: websockets\n  python3 -m pip install --user websockets",
        file=sys.stderr,
    )
    raise SystemExit(2)

LOG = logging.getLogger("devagent.agent")
ACK_TYPES = {
    "event_ack",
    "progress_ack",
    "log_ack",
    "checkpoint_ack",
    "approval_ack",
    "result_ack",
    "heartbeat_ack",
    "pong",
    "cancel_ack",
}
CLIENT_VERSION = "v1.1.6"


# ----------------------------------------------------------------------
# TLS trust store (the one-file binary ships no CA bundle of its own)
# ----------------------------------------------------------------------
def _ca_bundle_candidates() -> list[Path]:
    """Places a CA bundle may live, most specific first."""
    candidates: list[Path] = []
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:  # PyInstaller one-file extraction directory
        candidates.append(Path(bundled) / "cacert.pem")
        candidates.append(Path(bundled) / "certifi" / "cacert.pem")
    here = Path(sys.executable).resolve().parent
    candidates.append(here / "cacert.pem")
    try:  # pip-installed certifi (not present in the binary unless collected)
        import certifi

        candidates.append(Path(certifi.where()))
    except Exception:  # pragma: no cover - optional dependency
        pass
    if platform.system() == "Darwin":
        candidates.extend(
            [
                Path("/etc/ssl/cert.pem"),
                Path("/usr/local/etc/openssl/cert.pem"),
                Path("/opt/homebrew/etc/openssl@3/cert.pem"),
            ]
        )
    elif platform.system() == "Windows":
        candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/mingw64/ssl/certs/ca-bundle.crt")
    else:
        candidates.extend(
            [
                Path("/etc/ssl/certs/ca-certificates.crt"),
                Path("/etc/pki/tls/certs/ca-bundle.crt"),
                Path("/etc/ssl/cert.pem"),
            ]
        )
    return candidates


def _ensure_ca_bundle() -> str | None:
    """Point Python at a usable CA bundle (no-op when one is already configured)."""
    current = os.environ.get("SSL_CERT_FILE")
    if current and Path(current).exists():
        return current
    for candidate in _ca_bundle_candidates():
        try:
            if candidate.is_file() and candidate.stat().st_size > 1024:
                os.environ["SSL_CERT_FILE"] = str(candidate)
                LOG.debug("using CA bundle %s", candidate)
                return str(candidate)
        except OSError:  # pragma: no cover - permissions
            continue
    LOG.debug("no CA bundle found; TLS verification may fail")
    return None


def _launch_command(env_path: Path) -> str:
    """Command line that starts this agent again (binary vs. script)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --env-file "{env_path}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --env-file "{env_path}"'


def _service_program(env_path: Path) -> list[str]:
    """argv launchd/systemd should run (no shell, absolute paths)."""
    # Keep the venv interpreter as-is: resolving the symlink would drop its packages.
    argv = [str(Path(sys.executable))]
    if not getattr(sys, "frozen", False):
        argv.append(str(Path(__file__).resolve()))
    argv.extend(["--env-file", str(env_path)])
    return argv


def install_service(env_path: Path) -> int:
    """Write + load a login service: launchd (macOS), systemd user (Linux)."""
    from xml.sax.saxutils import escape

    argv = _service_program(env_path)
    state_dir = Path.home() / ".devagent"
    state_dir.mkdir(parents=True, exist_ok=True)
    system = platform.system()

    if system == "Darwin":
        label = "com.devagent.agent"
        target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        arguments = "".join(f"\n        <string>{escape(arg)}</string>" for arg in argv)
        target.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            f"    <key>Label</key>\n    <string>{label}</string>\n"
            f"    <key>ProgramArguments</key>\n    <array>{arguments}\n    </array>\n"
            "    <key>RunAtLoad</key>\n    <true/>\n"
            "    <key>KeepAlive</key>\n    <dict>\n        <key>SuccessfulExit</key>\n        <false/>\n    </dict>\n"
            "    <key>ThrottleInterval</key>\n    <integer>15</integer>\n"
            f"    <key>StandardOutPath</key>\n    <string>{escape(str(state_dir / 'agent.out.log'))}</string>\n"
            f"    <key>StandardErrorPath</key>\n    <string>{escape(str(state_dir / 'agent.err.log'))}</string>\n"
            "    <key>ProcessType</key>\n    <string>Background</string>\n"
            "</dict>\n</plist>\n",
            encoding="utf-8",
        )
        domain = f"gui/{os.getuid()}"
        print(f"wrote {target}")
        for command in (
            ["launchctl", "bootout", f"{domain}/{label}"],
            ["launchctl", "bootstrap", domain, str(target)],
            ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
        ):
            result = subprocess.run(command, capture_output=True, text=True)
            state = "ok" if result.returncode == 0 else f"exit {result.returncode}"
            print(f"  {' '.join(command)} -> {state}")
            if result.returncode != 0 and "bootstrap" in command:
                print(f"    {result.stderr.strip() or result.stdout.strip()}")
        print(f"logs: tail -f {state_dir / 'agent.out.log'} {state_dir / 'agent.err.log'}")
        print(f"stop: launchctl bootout {domain}/{label}")
        print(f"start: launchctl bootstrap {domain} {target}")
        return 0

    if system == "Linux":
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit = unit_dir / "devagent-agent.service"
        unit.write_text(
            "[Unit]\nDescription=DevAgent worker\nAfter=network-online.target\n\n"
            "[Service]\n"
            f"ExecStart={' '.join(argv)}\n"
            "Restart=always\nRestartSec=15\n"
            f"StandardOutput=append:{state_dir / 'agent.out.log'}\n"
            f"StandardError=append:{state_dir / 'agent.err.log'}\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        print(f"wrote {unit}")
        for command in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "devagent-agent"],
        ):
            result = subprocess.run(command, capture_output=True, text=True)
            state = "ok" if result.returncode == 0 else f"exit {result.returncode}"
            print(f"  {' '.join(command)} -> {state}")
        print("hint: run `loginctl enable-linger $USER` so it also starts without a login")
        return 0

    if system == "Windows":
        command = (
            f'schtasks /Create /F /SC ONLOGON /TN DevAgent /TR "{" ".join(argv)}"'
        )
        print("run this in an elevated PowerShell:\n  " + command)
        return 0

    print(f"unsupported platform: {system}", file=sys.stderr)
    return 2


# ----------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------
def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    gateway_url: str
    token: str
    agent_id: str
    workspaces: tuple[str, ...]
    state_dir: Path
    heartbeat_seconds: float
    llm_base_url: str | None
    llm_token: str | None
    model: str
    approve_commands: bool
    push_branch: bool
    create_pr: bool
    test_command: str | None
    commit_gate: bool
    dry_run: bool
    command_timeout: float
    llm_timeout: float
    browser_headers: bool = True
    origin: str | None = None
    # ws | http | auto  (auto = websocket, then HTTPS long-poll if refused with 403)
    transport: str = "auto"
    # Safety prompt for shell commands: give up (skip the command) after this long.
    command_approval_wait_seconds: float = 900.0
    # Testing/CI: ignore the provider the gateway attaches to a task and use the
    # local env instead (keeps the end-to-end test independent of any LLM).
    ignore_provider: bool = False

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if env is None else env)
        token = env.get("DEVAGENT_AGENT_TOKEN", "").strip()
        if not token:
            raise SystemExit("DEVAGENT_AGENT_TOKEN is required (see agent.env.example)")
        gateway = env.get("DEVAGENT_GATEWAY_URL", "ws://127.0.0.1:8082/mobile/agent/ws")
        raw_workspaces = env.get("DEVAGENT_WORKSPACES", str(Path.home() / "dev"))
        workspaces = tuple(
            str(Path(part.strip()).expanduser()) for part in raw_workspaces.split(",") if part.strip()
        )
        state_dir = Path(
            env.get("DEVAGENT_STATE_DIR", str(Path.home() / ".devagent"))
        ).expanduser()
        return Config(
            gateway_url=gateway,
            token=token,
            agent_id=env.get("DEVAGENT_AGENT_ID", socket.gethostname().split(".")[0]),
            workspaces=workspaces,
            state_dir=state_dir,
            heartbeat_seconds=float(env.get("DEVAGENT_HEARTBEAT_SECONDS", "10")),
            llm_base_url=env.get("DEVAGENT_LLM_BASE_URL") or None,
            llm_token=env.get("DEVAGENT_LLM_TOKEN") or None,
            model=env.get("DEVAGENT_MODEL", "DeepSeek-Coder-V2.5"),
            approve_commands=_bool(env.get("DEVAGENT_APPROVE_COMMANDS"), True),
            push_branch=_bool(env.get("DEVAGENT_PUSH_BRANCH"), False),
            create_pr=_bool(env.get("DEVAGENT_CREATE_PR"), False),
            test_command=env.get("DEVAGENT_TEST_COMMAND") or None,
            commit_gate=_bool(env.get("DEVAGENT_COMMIT_GATE"), False),
            dry_run=_bool(env.get("DEVAGENT_DRY_RUN"), False),
            command_timeout=float(env.get("DEVAGENT_COMMAND_TIMEOUT", "900")),
            llm_timeout=float(env.get("DEVAGENT_LLM_TIMEOUT", "180")),
            # Cloudflare's Browser Integrity Check rejects handshakes that do not look
            # like a browser (403), so the agent sends a normal header set by default.
            browser_headers=_bool(env.get("DEVAGENT_BROWSER_HEADERS"), True),
            origin=env.get("DEVAGENT_ORIGIN") or None,
            transport=(env.get("DEVAGENT_TRANSPORT") or "auto").strip().lower(),
            command_approval_wait_seconds=float(
                env.get("DEVAGENT_COMMAND_APPROVAL_WAIT_SECONDS", "900") or 900
            ),
            ignore_provider=_bool(env.get("DEVAGENT_IGNORE_PROVIDER"), False),
        )


# ----------------------------------------------------------------------
# small process helpers
# ----------------------------------------------------------------------
async def run_command(
    command: str, cwd: Path | None = None, *, timeout: float = 900, check: bool = True
) -> tuple[int, str, str]:
    LOG.debug("run: %s (cwd=%s)", command, cwd)
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        raise RuntimeError(f"Command timed out after {timeout:.0f}s: {command}")
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if check and process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {command}\n{err.strip() or out.strip()}"
        )
    return process.returncode or 0, out, err


def which(binary: str) -> str | None:
    return shutil.which(binary)


def _command_available(command: str, cwd: Path | None = None) -> bool:
    """True when the first word of ``command`` can actually be executed here."""
    try:
        parts = shlex.split(command)
    except ValueError:  # unbalanced quotes: let the shell report it
        return True
    if not parts:
        return False
    head = parts[0]
    if head in {"cd", "echo", "export", "source", ".", "true", "false"}:
        return True
    if which(head) is not None:
        return True
    candidate = Path(head).expanduser()
    if not candidate.is_absolute() and cwd is not None:
        candidate = cwd / candidate
    return candidate.exists()


def detect_test_command(worktree: Path) -> str | None:
    """Best-effort test command detection (opt out with DEVAGENT_TEST_COMMAND=)."""
    if (worktree / "package.json").exists():
        try:
            data = json.loads((worktree / "package.json").read_text())
        except (OSError, ValueError):
            data = {}
        scripts = data.get("scripts", {})
        runner = "npm"
        if (worktree / "bun.lock").exists() or (worktree / "bun.lockb").exists():
            runner = "bun"
        elif (worktree / "pnpm-lock.yaml").exists():
            runner = "pnpm"
        elif (worktree / "yarn.lock").exists():
            runner = "yarn"
        if "test" in scripts:
            return f"{runner} run test" if runner != "yarn" else "yarn test"
        if (worktree / "vitest.config.ts").exists() or (worktree / "vite.config.ts").exists():
            return f"{runner} run lint" if "lint" in scripts else None
        return None
    if (worktree / "go.mod").exists():
        return "go test ./..."
    if (worktree / "pytest.ini").exists() or (worktree / "pyproject.toml").exists():
        return "python3 -m pytest -q"
    if (worktree / "Cargo.toml").exists():
        return "cargo test"
    if (worktree / "Makefile").exists():
        return "make test"
    return None


def battery_level() -> int | None:
    if platform.system() != "Darwin" or which("pmset") is None:
        return None
    try:
        output = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - macOS only
        return None
    match = re.search(r"(\d{1,3})%", output)
    return int(match.group(1)) if match else None


def diff_files(diff: str) -> list[dict[str, Any]]:
    """Summarise a unified diff into the ``proposedChanges`` shape used by the PWA."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                continue
            current = {
                "path": path[2:] if path.startswith("b/") else path,
                "status": "modified",
                "additions": 0,
                "deletions": 0,
            }
            files.append(current)
        elif line.startswith("new file mode"):
            if current:
                current["status"] = "created"
        elif line.startswith("deleted file mode"):
            if current:
                current["status"] = "deleted"
        elif current is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current["additions"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                current["deletions"] += 1
    return files


def hash_diff(diff: str) -> str:
    return "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest()


def filter_diff_by_decisions(diff: str, decisions: dict[str, Any]) -> str:
    """Keep only files/hunks the operator approved (per-file + per-hunk granularity)."""
    if not decisions:
        return diff
    approved_files = {
        path
        for path, entry in decisions.items()
        if not path.startswith("__")
        and isinstance(entry, dict)
        and entry.get("decision") == "approve"
    }
    if not approved_files:
        return ""
    keep_whole = {
        path
        for path in approved_files
        if not (decisions.get(path) or {}).get("hunks")
    }
    keep_hunks = {
        path: set((decisions.get(path) or {}).get("hunks") or []) for path in approved_files
    }

    output: list[str] = []
    file_header: list[str] = []
    current_path: str | None = None
    hunk_lines: list[str] = []
    hunk_index = 0
    hunk_header: str | None = None

    def flush_hunk() -> None:
        nonlocal hunk_lines, hunk_header
        if hunk_header is None:
            return
        if current_path in keep_whole or hunk_index in keep_hunks.get(current_path or "", set()):
            output.extend(file_header)
            output.append(hunk_header)
            output.extend(hunk_lines)
        hunk_lines = []

    def flush_file() -> None:
        nonlocal file_header, current_path
        file_header = []
        current_path = None

    for line in diff.splitlines():
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            flush_hunk()
            if line.startswith("diff --git "):
                flush_file()
                file_header = [line]
            else:
                file_header.append(line)
                if line.startswith("+++ "):
                    path = line[4:].strip()
                    current_path = path[2:] if path.startswith("b/") else path
                    hunk_index = 0
                    hunk_header = None
            continue
        if line.startswith("@@"):
            flush_hunk()
            if current_path is None:
                continue
            hunk_index += 1
            hunk_header = line
            continue
        if hunk_header is not None:
            hunk_lines.append(line)
        else:
            file_header.append(line)
    flush_hunk()
    return "\n".join(output) + ("\n" if output else "")


# ----------------------------------------------------------------------
# gateway client
# ----------------------------------------------------------------------
class GatewayClient:
    """Persistent, self-healing WebSocket connection to the gateway."""

    def __init__(self, config: Config, runner: "TaskRunner") -> None:
        self.config = config
        self.runner = runner
        self.websocket: Any = None
        self.connected = asyncio.Event()
        self.stopping = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self.last_welcome: dict[str, Any] = {}
        allowed = {"ws", "http", "auto"}
        self.requested_transport = config.transport if config.transport in allowed else "auto"
        self.transport = "http" if self.requested_transport == "http" else "ws"

    # -- HTTPS long-poll fallback ---------------------------------------------
    def http_base(self) -> str:
        """Derive the HTTPS API root from the WebSocket URL (…/agent/ws)."""
        parsed = urllib.parse.urlparse(self.config.gateway_url)
        scheme = "https" if parsed.scheme in {"wss", "https"} else "http"
        path = parsed.path or ""
        if path.endswith("/agent/ws"):
            path = path[: -len("/agent/ws")]
        return f"{scheme}://{parsed.netloc}{path}".rstrip("/")

    def _http_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
        }
        headers.update(self.handshake_headers() or {})
        return headers

    def _http_call_sync(self, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = f"{self.http_base()}/api/agent/{endpoint}"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=self._http_headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = ""
            with contextlib.suppress(Exception):
                body = exc.read(300).decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {exc.code} on /api/agent/{endpoint}: {body[:200]}") from exc

    async def _http_call(
        self, endpoint: str, payload: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._http_call_sync, endpoint, payload, timeout)

    @property
    def url(self) -> str:
        separator = "&" if "?" in self.config.gateway_url else "?"
        return (
            f"{self.config.gateway_url}{separator}token={urllib.parse.quote(self.config.token)}"
            f"&agentId={urllib.parse.quote(self.config.agent_id)}"
        )

    def handshake_headers(self) -> dict[str, str] | None:
        """Browser-like headers for the upgrade request.

        Cloudflare (and some corporate proxies) run a Browser Integrity Check that
        answers ``403`` to handshakes without Accept/Accept-Language/User-Agent.
        Set ``DEVAGENT_BROWSER_HEADERS=false`` when talking to a bare gateway.
        """
        if not self.config.browser_headers:
            return None
        parsed = urllib.parse.urlparse(self.config.gateway_url)
        scheme = "https" if parsed.scheme in {"wss", "https"} else "http"
        origin = self.config.origin or f"{scheme}://{parsed.netloc}"
        return {
            "User-Agent": (
                f"DevAgent-Agent/{CLIENT_VERSION} ({platform.system()} {platform.release()}; "
                f"{platform.machine()})"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": origin,
        }

    async def _connect(self):
        """Open the websocket, tolerating both websockets kwargs spellings."""
        headers = self.handshake_headers()
        kwargs: dict[str, Any] = {
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": 8 * 1024 * 1024,
        }
        if headers:
            kwargs["additional_headers"] = headers
        try:
            return await websockets.connect(self.url, **kwargs)
        except TypeError:  # websockets < 14 uses extra_headers
            if "additional_headers" in kwargs:
                kwargs["extra_headers"] = kwargs.pop("additional_headers")
            return await websockets.connect(self.url, **kwargs)

    async def send(self, payload: dict[str, Any]) -> bool:
        if self.transport == "http":
            try:
                data = await self._http_call(
                    "send", {"agentId": self.config.agent_id, "message": payload}, timeout=30
                )
            except Exception as exc:
                LOG.debug("http send failed: %s", exc)
                return False
            for message in data.get("messages") or []:
                await self._handle(message)
            return True
        if self.websocket is None:
            return False
        async with self._send_lock:
            try:
                await self.websocket.send(json.dumps(payload))
                return True
            except ConnectionClosed:
                return False
            except Exception:  # pragma: no cover - transport noise
                LOG.debug("send failed", exc_info=True)
                return False

    async def run_forever(self) -> None:
        if self.transport == "http":
            return await self._run_http()
        delay = 1.0
        while not self.stopping.is_set():
            try:
                LOG.info("connecting to %s", self.config.gateway_url)
                websocket = await self._connect()
                async with websocket:
                    self.websocket = websocket
                    self.connected.set()
                    delay = 1.0
                    await self._hello()
                    heartbeat = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in websocket:
                            await self._handle_raw(raw)
                    finally:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except Exception as exc:
                if self.requested_transport == "auto" and "403" in str(exc):
                    LOG.warning(
                        "websocket refused with 403 (proxy/WAF?) — switching to HTTPS long-poll"
                    )
                    self.transport = "http"
                    return await self._run_http()
                LOG.warning("connection lost (%s); retrying in %.0fs", exc, delay)
            finally:
                self.websocket = None
                self.connected.clear()
                self.runner.on_disconnect()

            if self.stopping.is_set():
                break
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 30.0)

    async def _run_http(self) -> None:
        """Same protocol over POST /api/agent/send + /api/agent/poll."""
        delay = 1.0
        while not self.stopping.is_set():
            try:
                LOG.info("connecting to %s over HTTPS long-poll", self.http_base())
                self.connected.set()
                delay = 1.0
                await self._hello()
                heartbeat = asyncio.create_task(self._heartbeat_loop())
                try:
                    while not self.stopping.is_set():
                        data = await self._http_call(
                            "poll", {"agentId": self.config.agent_id, "wait": 25}, timeout=45
                        )
                        if data.get("replaced"):
                            LOG.warning("another session took over agent id %s", self.config.agent_id)
                            self.stopping.set()
                            return
                        for message in data.get("messages") or []:
                            await self._handle(message)
                finally:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except Exception as exc:
                LOG.warning("long-poll failed (%s); retrying in %.0fs", exc, delay)
            finally:
                self.connected.clear()
                self.runner.on_disconnect()

            if self.stopping.is_set():
                break
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 30.0)

    def hello_payload(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "agentId": self.config.agent_id,
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "version": CLIENT_VERSION,
            "workspace": str(self.config.workspaces[0]) if self.config.workspaces else "~",
            "capabilities": self.capabilities(),
            "batteryLevel": battery_level(),
            "activeTaskId": self.runner.active_task_id,
        }

    async def _hello(self) -> None:
        await self.send(self.hello_payload())

    def capabilities(self) -> list[str]:
        caps = ["git", "worktree", "checkpoint", "approval"]
        if self.config.llm_base_url:
            caps.append("llm")
        if self.config.approve_commands:
            caps.append("shell-approved")
        if which("gh") and self.config.create_pr:
            caps.append("pull-request")
        if platform.system() == "Darwin":
            caps.append("macos")
        return caps

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            await self.send(
                {
                    "type": "heartbeat",
                    "agentId": self.config.agent_id,
                    "hostname": socket.gethostname(),
                    "os": f"{platform.system()} {platform.release()}",
                    "version": CLIENT_VERSION,
                    "workspace": str(self.runner.current_workspace or ""),
                    "activeTaskId": self.runner.active_task_id,
                    "batteryLevel": battery_level(),
                    "stats": self.runner.stats(),
                }
            )

    async def _handle_raw(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            LOG.warning("ignoring malformed gateway message")
            return
        await self._handle(message)

    async def _handle(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        LOG.debug("gateway -> %s", message_type)
        if message_type == "welcome":
            self.last_welcome = message
            interval = float(message.get("heartbeatIntervalSeconds") or self.config.heartbeat_seconds)
            self.config.heartbeat_seconds = interval
            LOG.info(
                "enrolled as %s (resumed=%s, nextTask=%s)",
                message.get("agentId"),
                message.get("resumedTasks"),
                message.get("nextTaskId"),
            )
        elif message_type in {"task_assign", "task_resume"}:
            await self.runner.accept_task(message)
        elif message_type == "approval_decision":
            await self.runner.handle_approval_decision(message)
        elif message_type == "cancel":
            await self.runner.cancel(message.get("taskId"), message.get("reason", "cancelled by operator"))
        elif message_type == "ping":
            await self.send({"type": "pong", "serverTime": time.time()})
        elif message_type == "error":
            LOG.warning("gateway error: %s %s", message.get("error"), message.get("details") or "")
        elif message_type in ACK_TYPES:
            # Acknowledgements are informational: the gateway answered the message
            # we just sent. Nothing to do, and no reason to call them "unhandled".
            LOG.debug("gateway ack: %s", message_type)
        else:
            LOG.debug("unhandled gateway message: %s", message_type)

    async def event(
        self,
        task_id: str,
        message: str,
        *,
        event_type: str = "log",
        details: dict[str, Any] | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        current_step: str | None = None,
    ) -> None:
        await self.send(
            {
                "type": "event",
                "taskId": task_id,
                "eventType": event_type,
                "message": message,
                "details": details,
                "stepNumber": step_number,
                "totalSteps": total_steps,
                "currentStep": current_step,
            }
        )

    async def checkpoint(self, task_id: str, name: str, payload: dict[str, Any], key: str) -> None:
        await self.send(
            {
                "type": "checkpoint",
                "taskId": task_id,
                "name": name,
                "payload": payload,
                "idempotencyKey": key,
            }
        )

    async def request_approval(
        self, task_id: str, gate: str, summary: str, diff: str, files: list[dict[str, Any]]
    ) -> None:
        await self.send(
            {
                "type": "approval_request",
                "taskId": task_id,
                "gate": gate,
                "summary": summary,
                "diff": diff,
                "hash": hash_diff(diff),
                "files": files,
            }
        )

    async def result(self, task_id: str, status: str, **payload: Any) -> None:
        await self.send({"type": "result", "taskId": task_id, "status": status, **payload})


# ----------------------------------------------------------------------
# task execution
# ----------------------------------------------------------------------
class TaskAborted(Exception):
    pass


@dataclass
class TaskContext:
    task: dict[str, Any]
    worktree: Path
    branch: str
    state_file: Path
    stage: str = "start"
    diff: str = ""
    approvals: list[str] = field(default_factory=list)
    decisions: dict[str, Any] = field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return self.task["id"]

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(
                {
                    "taskId": self.task_id,
                    "worktree": str(self.worktree),
                    "branch": self.branch,
                    "stage": self.stage,
                    "diff": self.diff,
                    "approvals": self.approvals,
                    "decisions": self.decisions,
                },
                indent=2,
            )
        )

    @staticmethod
    def load(state_file: Path) -> dict[str, Any] | None:
        if not state_file.exists():
            return None
        try:
            return json.loads(state_file.read_text())
        except (OSError, ValueError):
            return None


class TaskRunner:
    """Executes one task at a time and resumes interrupted work from checkpoints."""

    def __init__(self, config: Config, client: "GatewayClient | None" = None) -> None:
        self.config = config
        self.client = client
        self.active_task_id: str | None = None
        self.current_workspace: Path | None = None
        self._current: TaskContext | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_data: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.resumed_tasks = 0
        # Per-task provider override sent by the gateway with task_assign/resume.
        # The control connection is never affected by provider changes.
        self._provider: dict[str, Any] | None = None
        self._task_model: str | None = None

    # -- lifecycle -------------------------------------------------------
    def stats(self) -> dict[str, int]:
        return {
            "completed": self.completed_tasks,
            "failed": self.failed_tasks,
            "resumed": self.resumed_tasks,
        }

    async def accept_task(self, message: dict[str, Any]) -> None:
        task = message.get("task") or {}
        task_id = task.get("id")
        if not task_id:
            return
        action = message.get("action") or "start"
        if self._current_task is not None and not self._current_task.done():
            LOG.warning("busy with %s, ignoring %s (%s)", self.active_task_id, task_id, action)
            return
        self.active_task_id = task_id
        self._current_task = asyncio.create_task(self._run_task(message, action))
        self._current_task.add_done_callback(self._task_done)

    def _task_done(self, finished: asyncio.Task[None]) -> None:
        task_id = self.active_task_id
        error: BaseException | None = None
        with contextlib.suppress(asyncio.CancelledError):
            error = finished.exception()
        if error is not None:
            # Errors raised before/around the task's own handlers (workspace not
            # allowed, worktree creation, ...) must still close the task: without
            # this the gateway keeps it `running` forever.
            LOG.error("task execution crashed: %s", error)
            if task_id and self.client is not None:
                asyncio.get_event_loop().create_task(self._report_crash(task_id, error))
        self._current_task = None
        self.active_task_id = None

    async def _report_crash(self, task_id: str, error: BaseException) -> None:
        message = f"Agent error: {error}"
        with contextlib.suppress(Exception):
            await self.client.event(
                task_id, message, event_type="error", details={"error": str(error)}
            )
        with contextlib.suppress(Exception):
            await self.client.result(task_id, "failed", error=message)

    def on_disconnect(self) -> None:
        """The socket dropped: abort the in-flight task locally, it will be resumed."""
        if self._current_task is not None and not self._current_task.done():
            LOG.warning("connection lost: aborting local execution, will resume on reconnect")
            self._current_task.cancel()

    async def cancel(self, task_id: str | None, reason: str) -> None:
        if task_id:
            self._cancelled.add(task_id)
        if self._current is not None and (task_id is None or task_id == self._current.task_id):
            if self._current_task is not None:
                self._current_task.cancel()
        LOG.info("task %s cancelled: %s", task_id, reason)

    async def handle_approval_decision(self, message: dict[str, Any]) -> None:
        task_id = message.get("taskId")
        if not task_id:
            return
        self._approval_data[task_id] = message
        event = self._approval_events.get(task_id)
        if event is not None:
            event.set()
        else:
            LOG.info("approval for %s arrived while idle (will be replayed)", task_id)

    # -- main pipeline ---------------------------------------------------
    async def _run_task(self, message: dict[str, Any], action: str) -> None:
        task = message["task"]
        task_id = task["id"]
        self.active_task_id = task_id
        workspace = self._resolve_workspace(task.get("workspace"))
        self.current_workspace = workspace
        checkpoint = message.get("checkpoint") or {}
        gates = message.get("gates") or ["diff"]
        LOG.info("task %s: action=%s workspace=%s gates=%s", task_id, action, workspace, gates)
        self._cancelled.discard(task_id)

        provider = message.get("provider") or {}
        self._provider = (
            provider if provider.get("baseUrl") and not self.config.ignore_provider else None
        )
        self._task_model = message.get("model") or None
        if self._provider:
            await self.client.event(
                task_id,
                f"AI provider: {provider.get('name')} · model "
                f"{self._task_model or provider.get('defaultModel') or '?'}",
                event_type="log",
                details={
                    "providerId": provider.get("id"),
                    "baseUrl": provider.get("baseUrl"),
                    "model": self._task_model or provider.get("defaultModel"),
                },
            )

        context = await self._prepare_context(task, workspace, checkpoint, action)
        self._current = context
        try:
            if checkpoint:
                self.resumed_tasks += 1
            if action == "await_approval":
                await self._await_approval(message.get("approval") or {})
            elif action == "apply_approved":
                await self._apply_and_finish(message.get("approval") or {}, gates)
            else:
                await self._execute(context, gates, resume=action == "resume")
            self.completed_tasks += 1
        except asyncio.CancelledError:
            LOG.info("task %s interrupted locally (will be resumed)", task_id)
            context.save()
            raise
        except TaskAborted as aborted:
            LOG.info("task %s aborted: %s", task_id, aborted)
            context.save()
        except Exception as exc:  # noqa: BLE001 - report anything to the operator
            LOG.exception("task %s failed", task_id)
            await self.client.event(
                task_id, f"Agent error: {exc}", event_type="error", details={"error": str(exc)}
            )
            await self.client.result(task_id, "failed", error=str(exc))
            context.save()
            self.failed_tasks += 1
        finally:
            self._current = None

    def _resolve_workspace(self, requested: str | None) -> Path:
        raw = (requested or "").strip() or str(self.config.workspaces[0])
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (self.config.workspaces[0] / candidate)
        candidate = candidate.resolve()
        allowed = [Path(root).expanduser().resolve() for root in self.config.workspaces]
        for root in allowed:
            if candidate == root or root in candidate.parents:
                if not candidate.exists():
                    raise RuntimeError(
                        f"workspace {candidate} does not exist on this machine.\n"
                        f"      DEVAGENT_WORKSPACES = {[str(r) for r in allowed]}\n"
                        "      Fix the path (check for typos) in the agent env file and restart,"
                        " or create the task with a folder that exists."
                    )
                return candidate
        raise RuntimeError(
            f"workspace {candidate} is outside the allowed roots {[str(r) for r in allowed]}.\n"
            "      Fix DEVAGENT_WORKSPACES in the agent env file (or create the task with a"
            " path inside one of those roots) and restart the agent."
        )

    async def _prepare_context(
        self, task: dict[str, Any], workspace: Path, checkpoint: dict[str, Any], action: str
    ) -> TaskContext:
        state_file = self.config.state_dir / "state" / f"{task['id']}.json"
        state = TaskContext.load(state_file) or {}
        worktree = Path(state.get("worktree") or (self.config.state_dir / "worktrees" / task["id"]))
        branch = state.get("branch") or f"agent/{task['id']}"
        if checkpoint.get("payload", {}).get("worktree"):
            worktree = Path(checkpoint["payload"]["worktree"])
        if checkpoint.get("payload", {}).get("branch"):
            branch = checkpoint["payload"]["branch"]

        context = TaskContext(
            task=task,
            worktree=worktree,
            branch=branch,
            state_file=state_file,
            stage=state.get("stage", "start"),
            diff=state.get("diff", ""),
            approvals=state.get("approvals", []),
            decisions=state.get("decisions", {}),
        )
        if action == "apply_approved" and not worktree.exists():
            await self._create_worktree(context, workspace)
        return context

    async def _create_worktree(self, context: TaskContext, workspace: Path) -> None:
        worktree = context.worktree
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if (workspace / ".git").exists() or (workspace / ".git").is_file():
            if not worktree.exists():
                await run_command(
                    f"git worktree add -f {shlex.quote(str(worktree))} -B {shlex.quote(context.branch)}",
                    cwd=workspace,
                )
        else:  # not a git repo: fall back to a plain copy so the flow still works
            if not worktree.exists():
                shutil.copytree(workspace, worktree, symlinks=True, ignore=shutil.ignore_patterns(".venv", "node_modules", ".git"))

    async def _execute(self, context: TaskContext, gates: list[str], *, resume: bool) -> None:
        task_id = context.task_id
        instruction = context.task["instruction"]
        workspace = self.current_workspace or context.worktree

        await self.client.event(
            task_id,
            f"Starting task on {socket.gethostname()} ({platform.system()} {platform.release()})",
            event_type="status_change",
            step_number=1,
            total_steps=4,
            current_step="Preparing isolated worktree",
        )
        await self._create_worktree(context, workspace)
        context.stage = "workspace-ready"
        context.save()
        await self.client.checkpoint(
            task_id,
            "workspace-ready",
            {"worktree": str(context.worktree), "branch": context.branch, "stage": context.stage},
            key=f"{task_id}:workspace-ready",
        )

        if resume and context.diff:
            await self.client.event(
                task_id,
                "Resuming after interruption with the previously generated patch.",
                event_type="log",
                details={"resumeStage": context.stage},
            )
        else:
            await self.client.event(
                task_id,
                "Analysing the repository and generating changes.",
                event_type="step",
                step_number=2,
                total_steps=4,
                current_step="Generating changes",
            )
            context.diff = await self._generate_patch(context, instruction)
            context.stage = "diff-ready"
            context.save()
            await self.client.checkpoint(
                task_id,
                "diff-ready",
                {
                    "worktree": str(context.worktree),
                    "branch": context.branch,
                    "stage": context.stage,
                    "diffHash": hash_diff(context.diff),
                },
                key=f"{task_id}:diff-ready",
            )

        if not context.diff.strip():
            await self.client.event(
                task_id,
                "The agent produced no changes for this instruction; nothing to review.",
                event_type="log",
            )
            await self.client.result(
                task_id,
                "completed",
                result={
                    "summary": "No changes were necessary.",
                    "filesModified": [],
                    "testsPassed": True,
                    "branchName": context.branch,
                },
            )
            return

        if self.config.dry_run:
            await self.client.event(
                task_id,
                "DRY-RUN: patch generated but not applied (DEVAGENT_DRY_RUN=1).",
                event_type="log",
                details={"diff": context.diff},
            )
            await self.client.result(
                task_id,
                "completed",
                result={
                    "summary": "Dry run: proposed changes were generated but not applied.",
                    "filesModified": [f["path"] for f in diff_files(context.diff)],
                    "testsPassed": True,
                    "branchName": context.branch,
                },
            )
            return

        if "plan" in gates and "plan" not in context.approvals:
            await self._wait_for_approval(
                context,
                gate="plan",
                summary=f"Execution plan for: {instruction[:140]}",
                diff=context.diff,
                step=2,
            )
        else:
            await self._request_approval(context, "diff", step=3)

        await self._apply_and_finish({}, gates)

    async def _generate_patch(self, context: TaskContext, instruction: str) -> str:
        provider = self._provider or {}
        if provider.get("baseUrl") or self.config.llm_base_url:
            try:
                return await self._llm_patch(context, instruction)
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                LOG.warning("LLM planning failed: %s", exc)
                await self.client.event(
                    context.task_id,
                    f"LLM planning failed ({exc}); falling back to a manual plan.",
                    event_type="log",
                )
        return self._manual_plan(context, instruction)

    async def _llm_patch(self, context: TaskContext, instruction: str) -> str:
        worktree = context.worktree
        listing = await run_command(
            "git ls-files | head -200", cwd=worktree, check=False, timeout=60
        )
        files = listing[1].strip().splitlines()
        snippets: list[str] = []
        for relative in files[:12]:
            path = worktree / relative
            try:
                if path.is_file() and path.stat().st_size < 40000:
                    content = path.read_text(errors="replace")[:4000]
                    snippets.append(f"### {relative}\n{content}")
            except OSError:
                continue

        system = (
            "You are a senior engineer working inside an isolated git worktree. "
            "Reply with ONLY a unified diff (git format, 'diff --git' headers). "
            "Do not add explanations, no markdown fences. Keep the change minimal."
        )
        user = (
            f"Task: {instruction}\n\nRepository files (truncated):\n"
            + "\n\n".join(snippets)
            + "\n\nReturn the unified diff that implements the task."
        )
        payload = {
            "model": self._llm_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "stream": False,
        }
        request = urllib.request.Request(
            self._llm_base_url().rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._llm_token()}",
            },
            method="POST",
        )
        raw = await asyncio.to_thread(self._urlopen, request)
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        diff = _extract_diff(content)
        if not diff.strip():
            raise RuntimeError("LLM returned no usable diff")
        LOG.info("LLM generated a %d byte patch", len(diff))
        return diff

    def _llm_base_url(self) -> str:
        provider = self._provider or {}
        return str(provider.get("baseUrl") or self.config.llm_base_url or "")

    def _llm_token(self) -> str:
        provider = self._provider or {}
        return str(provider.get("apiKey") or self.config.llm_token or "")

    def _llm_model(self) -> str:
        provider = self._provider or {}
        return str(
            self._task_model
            or provider.get("defaultModel")
            or self.config.model
        )

    def _urlopen(self, request: urllib.request.Request) -> str:
        try:
            with urllib.request.urlopen(request, timeout=self.config.llm_timeout) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:  # pragma: no cover - network path
            raise RuntimeError(f"LLM endpoint error {error.code}: {error.read()[:200]!r}") from error

    def _manual_plan(self, context: TaskContext, instruction: str) -> str:
        """No LLM configured: produce a reviewable plan file instead of guessing.

        The file is *not* written here - the returned patch is what materialises it
        once the operator approves, so nothing changes before approval.
        """
        plan = (
            f"# Proposed plan (generated by the DevAgent reference agent)\n\n"
            f"Task: {instruction}\n\n"
            "No LLM endpoint is configured (DEVAGENT_LLM_BASE_URL missing), so the agent did not "
            "modify source files automatically. Approving this proposal commits the plan file to "
            "the task branch so a human can pick it up.\n"
        )
        context.stage = "plan-file"
        return (
            "diff --git a/DEVAGENT_PLAN.md b/DEVAGENT_PLAN.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/DEVAGENT_PLAN.md\n"
            + "".join(f"+{line}\n" for line in plan.splitlines())
        )

    async def _request_approval(self, context: TaskContext, gate: str, *, step: int) -> None:
        files = diff_files(context.diff)
        await self.client.event(
            context.task_id,
            f"Proposed changes ready for review ({gate} gate).",
            event_type="diff_ready",
            step_number=step,
            total_steps=4,
            current_step="Awaiting operator approval",
            details={"files": files},
        )
        await self.client.request_approval(
            context.task_id,
            gate,
            f"Generated proposed changes for: {context.task['instruction'][:140]}",
            context.diff,
            files,
        )
        context.stage = f"awaiting-{gate}"
        context.save()
        await self._wait_for_approval(
            context, gate=gate, summary="", diff=context.diff, step=step, already_requested=True
        )

    async def _wait_for_approval(
        self,
        context: TaskContext,
        *,
        gate: str,
        summary: str,
        diff: str,
        step: int,
        already_requested: bool = False,
        wait_timeout: float = 0.0,
    ) -> None:
        if not already_requested:
            await self._request_approval(context, gate, step=step)
            return
        task_id = context.task_id
        event = self._approval_events.setdefault(task_id, asyncio.Event())
        event.clear()
        LOG.info("task %s: waiting for the operator decision on gate '%s'", task_id, gate)
        # While waiting we still need to notice a dropped connection: the abort is
        # driven by the websocket loop calling on_disconnect(). A decision that
        # never arrives (e.g. the proposal was refused by the gateway) must not
        # hang the task forever.
        limit = wait_timeout
        started = time.monotonic()
        while not event.is_set():
            if not self.client.connected.is_set():
                raise asyncio.CancelledError()
            if limit and time.monotonic() - started > limit:
                raise TaskAborted(
                    f"no operator decision on the {gate} gate after {limit / 60:.0f} minutes"
                )
            await asyncio.sleep(0.5)

        decision = self._approval_data.pop(task_id, {})
        approved = decision.get("decision") == "approve"
        await self.client.event(
            task_id,
            f"Operator {'approved' if approved else 'rejected'} the {gate} proposal"
            + (f": {decision.get('comment')}" if decision.get("comment") else "."),
            event_type="approval_resolved",
            details={"gate": gate, "decision": decision.get("decision")},
        )
        if not approved:
            raise TaskAborted(f"operator rejected the {gate} proposal")
        context.approvals.append(gate)
        context.decisions = decision.get("decisions") or {}
        context.stage = f"approved-{gate}"
        context.save()

    async def _await_approval(self, approval: dict[str, Any]) -> None:
        """Resume path: the gateway still holds a pending proposal for this task."""
        context = self._current
        assert context is not None
        gate = approval.get("gate") or "diff"
        if approval.get("state") == "pending":
            await self.client.event(
                context.task_id,
                f"Reconnected; the {gate} proposal is still awaiting your decision.",
                event_type="log",
            )
            await self._wait_for_approval(
                context, gate=gate, summary="", diff=context.diff, step=3, already_requested=True
            )
            await self._apply_and_finish({}, ["diff"])
        else:
            await self.client.result(
                context.task_id,
                "failed",
                error=f"Proposal is already {approval.get('state')}; nothing to apply.",
            )

    async def _apply_and_finish(self, approval: dict[str, Any], gates: list[str]) -> None:
        context = self._current
        assert context is not None
        task_id = context.task_id
        decisions = (approval.get("decisions") or context.decisions or {})
        if approval.get("items"):
            LOG.info("approval carries %d review items", len(approval["items"]))

        diff = context.diff or (approval.get("diff") or "")
        selected = filter_diff_by_decisions(diff, decisions) if decisions else diff
        if decisions and not selected.strip():
            await self.client.result(
                task_id,
                "completed",
                result={
                    "summary": "All proposed changes were rejected per file/hunk; nothing applied.",
                    "filesModified": [],
                    "testsPassed": True,
                    "branchName": context.branch,
                },
            )
            return

        await self.client.event(
            task_id,
            f"Applying {len(diff_files(selected))} approved file(s) in {context.worktree}",
            event_type="step",
            step_number=4,
            total_steps=4,
            current_step="Applying changes and running tests",
        )
        patch_file = Path(tempfile.mkstemp(suffix=".patch")[1])
        patch_file.write_text(selected)
        try:
            await run_command(f"git apply --whitespace=nowarn {shlex.quote(str(patch_file))}", cwd=context.worktree)
        except RuntimeError as error:
            await self.client.result(task_id, "failed", error=f"Patch application failed: {error}")
            self.failed_tasks += 1
            return
        finally:
            patch_file.unlink(missing_ok=True)

        context.stage = "applied"
        context.save()
        await self.client.checkpoint(
            task_id,
            "patch-applied",
            {
                "worktree": str(context.worktree),
                "branch": context.branch,
                "stage": context.stage,
                "files": [f["path"] for f in diff_files(selected)],
            },
            key=f"{task_id}:patch-applied",
        )

        tests_passed, test_output = await self._run_tests(context)

        if self.config.commit_gate and "commit" in gates and "commit" not in context.approvals:
            committed = await run_command("git add -A && git diff --cached", cwd=context.worktree)
            await self._request_approval(context, "commit", step=4)
            if not committed[1].strip():
                LOG.info("nothing staged for the commit gate")
        await self._commit(context)

        summary = (
            f"Applied {len(diff_files(selected))} file(s) on branch {context.branch}; "
            + (
                "tests passed."
                if tests_passed
                else ("tests failed." if tests_passed is False else "verification skipped.")
            )
        )
        result = {
            "summary": summary,
            "filesModified": [f["path"] for f in diff_files(selected)],
            "testsPassed": bool(tests_passed),
            "testsRan": tests_passed is not None,
            "testOutput": test_output[-4000:] if test_output else "",
            "branchName": context.branch,
        }
        if tests_passed is False:
            self.failed_tasks += 1
            await self.client.result(
                task_id, "failed", result=result, error="Automated tests failed after applying the patch"
            )
        else:
            self.completed_tasks += 1
            await self.client.result(task_id, "completed", result=result)

    async def _run_tests(self, context: TaskContext) -> tuple[bool | None, str]:
        command = (self.config.test_command or "").strip()
        if command and not _command_available(command, context.worktree):
            fallback = detect_test_command(context.worktree)
            await self.client.event(
                context.task_id,
                f"Test command '{command}' is not installed on this machine"
                + (f"; using the detected runner '{fallback}'." if fallback else "; skipping verification."),
                event_type="log",
                details={"configured": command, "fallback": fallback},
            )
            command = fallback or ""
        command = command or detect_test_command(context.worktree)
        if not command:
            await self.client.event(
                context.task_id,
                "No test runner detected (set DEVAGENT_TEST_COMMAND to enable verification).",
                event_type="log",
            )
            return True, "no test runner configured"
        if self.config.approve_commands and "shell" not in context.approvals:
            approved = await self._approve_command(context, command, purpose="verification")
            if not approved:
                return None, f"skipped: the operator did not approve running: {command}"
            context.approvals.append("shell")
        await self.client.event(
            context.task_id,
            f"Running verification: {command}",
            event_type="command",
            details={"command": command, "cwd": str(context.worktree)},
        )
        code, out, err = await run_command(
            command, cwd=context.worktree, check=False, timeout=self.config.command_timeout
        )
        output = (out + "\n" + err).strip()
        message = f"Test runner finished with exit code {code}"
        if code == 127:
            message += " (command not found — check DEVAGENT_TEST_COMMAND)"
        await self.client.event(
            context.task_id,
            message,
            event_type="test_run",
            details={"exitCode": code, "tail": output[-2000:]},
        )
        return code == 0, output

    async def _approve_command(self, context: TaskContext, command: str, *, purpose: str) -> bool:
        await self.client.event(
            context.task_id,
            f"Command approval required before {purpose}: {command}",
            event_type="log",
            details={"command": command},
        )
        await self.client.request_approval(
            context.task_id,
            "command",
            f"Approve command execution ({purpose}): {command}",
            f"# Command\n{command}\n\n# Worktree\n{context.worktree}\n",
            [{"path": f"command:{purpose}", "status": "modified", "additions": 0, "deletions": 0}],
        )
        try:
            await self._wait_for_approval(
                context,
                gate="command",
                summary="",
                diff=context.diff,
                step=4,
                already_requested=True,
                wait_timeout=self.config.command_approval_wait_seconds,
            )
        except TaskAborted:
            await self.client.event(
                context.task_id,
                f"Command not approved; skipping {purpose}: {command}",
                event_type="log",
                details={"command": command},
            )
            return False
        return True

    async def _commit(self, context: TaskContext) -> None:
        status = await run_command("git status --porcelain", cwd=context.worktree, check=False)
        if not status[1].strip():
            return
        await run_command("git add -A", cwd=context.worktree)
        await run_command(
            f"git commit -m {shlex.quote('agent: ' + context.task['instruction'][:100])}",
            cwd=context.worktree,
            check=False,
        )
        await self.client.event(
            context.task_id,
            f"Committed the change set on branch {context.branch}",
            event_type="log",
            details={"branch": context.branch},
        )
        if self.config.push_branch:
            pushed = await run_command(f"git push -u origin {shlex.quote(context.branch)}", cwd=context.worktree, check=False)
            await self.client.event(
                context.task_id,
                f"Pushed branch {context.branch}" if pushed[0] == 0 else f"Push failed: {pushed[2][:300]}",
                event_type="log",
            )
            if pushed[0] == 0 and self.config.create_pr and which("gh"):
                title = context.task["instruction"][:80]
                pr = await run_command(
                    f"gh pr create --fill --title {shlex.quote(title)} --body "
                    f"{shlex.quote('Automated DevAgent task ' + context.task_id)}",
                    cwd=context.worktree,
                    check=False,
                )
                await self.client.event(
                    context.task_id,
                    f"Pull request: {pr[1].strip()}" if pr[0] == 0 else f"PR creation failed: {pr[2][:300]}",
                    event_type="log",
                )


def _extract_diff(content: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\n(.*?)```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    start = content.find("diff --git")
    if start == -1:
        start = content.find("--- ")
    if start == -1:
        return ""
    diff = content[start:]
    if diff.startswith("--- ") and "diff --git" not in diff:
        diff = diff  # plain unified diff without git headers is still valid
    return diff.strip() + "\n"


# ----------------------------------------------------------------------
# entrypoint
# ----------------------------------------------------------------------
def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def default_env_path() -> Path:
    return Path(os.environ.get("DEVAGENT_ENV_FILE", Path.home() / ".devagent" / "agent.env"))


def _ask(prompt: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    question = f"{prompt}{suffix}: "
    if secret and sys.stdin.isatty():
        raw = getpass.getpass(question)
    else:
        # piped input (tests, provisioning scripts) and Windows consoles
        raw = input(question)
    value = raw.strip()
    return value or default


def _ask_bool(prompt: str, default: bool = False) -> bool:
    raw = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true", "da"}


def _http_probe(ws_url: str) -> str:
    """One plain HTTPS request to the same host, to see *who* answered."""
    parsed = urllib.parse.urlparse(ws_url)
    scheme = "https" if parsed.scheme in {"wss", "https"} else "http"
    target = urllib.parse.urlunparse((scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))
    headers = {
        "User-Agent": f"DevAgent-Agent/{CLIENT_VERSION} ({platform.system()} {platform.release()})",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    request = urllib.request.Request(target, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - known URL
            return f"HTTP {response.status} from server={response.headers.get('server', '?')}"
    except urllib.error.HTTPError as exc:
        bits = [f"HTTP {exc.code}", f"server={exc.headers.get('server', '?')}"]
        for header in ("cf-ray", "cf-mitigated", "cf-cache-status"):
            value = exc.headers.get(header)
            if value:
                bits.append(f"{header}={value}")
        body = ""
        with contextlib.suppress(Exception):
            body = exc.read(240).decode("utf-8", "replace").replace("\n", " ").strip()
        if body:
            bits.append(f"body={body[:180]}")
        return " · ".join(bits)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return f"probe failed: {type(exc).__name__}: {exc}"


async def _validate_connection(env: dict[str, str]) -> tuple[bool, str]:
    """Handshake check used by the wizard (browser-like headers included)."""
    config = Config.from_env(env)
    runner = TaskRunner(config)
    client = GatewayClient(config, runner)
    try:
        headers = client.handshake_headers()
        kwargs: dict[str, Any] = {"open_timeout": 15, "ping_interval": None}
        if headers:
            kwargs["additional_headers"] = headers
        try:
            websocket = await websockets.connect(client.url, **kwargs)
        except TypeError:
            kwargs["extra_headers"] = kwargs.pop("additional_headers", {})
            websocket = await websockets.connect(client.url, **kwargs)
        async with websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "hostname": socket.gethostname(),
                        "os": f"{platform.system()} {platform.release()}",
                        "version": CLIENT_VERSION,
                        "workspace": config.workspaces[0] if config.workspaces else "~",
                        "capabilities": client.capabilities(),
                    }
                )
            )
            raw = await asyncio.wait_for(websocket.recv(), timeout=15)
            message = json.loads(raw)
            if message.get("type") == "welcome":
                return True, f"enrolled as {message.get('agentId')}"
            if message.get("type") == "error":
                detail = message.get("message") or message.get("code") or raw[:160]
                return False, str(detail)
            return False, f"unexpected reply: {raw[:160]}"
    except ConnectionClosed as exc:  # server closed right after an error frame
        return False, f"connection closed (code {exc.code}): {exc.reason or 'no reason given'}"
    except Exception as exc:  # noqa: BLE001 - the wizard only reports it
        detail = f"{type(exc).__name__}: {exc}"
        if "403" in detail and client.requested_transport in {"auto", "http"}:
            ok, fallback_detail = await _validate_http(client)
            if ok:
                return True, fallback_detail
            return False, f"{detail} — {fallback_detail}"
        return False, detail


async def _validate_http(client: "GatewayClient") -> tuple[bool, str]:
    """Enrol over HTTPS long-poll (used when a proxy refuses the WebSocket)."""
    try:
        data = await client._http_call(
            "send", {"agentId": client.config.agent_id, "message": client.hello_payload()}, timeout=20
        )
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        return False, f"HTTPS fallback also failed: {type(exc).__name__}: {exc}"
    for message in data.get("messages") or []:
        if message.get("type") == "welcome":
            return True, f"enrolled as {message.get('agentId')} (HTTPS long-poll; WebSocket refused)"
        if message.get("type") == "error":
            return False, str(message.get("message") or "gateway error over HTTPS")
    return False, "HTTPS fallback: gateway answered without a welcome"


def _step(number: int, total: int, title: str) -> None:
    print(f"\n[{number}/{total}] {title}")
    print("-" * 46)


def _explain(text: str) -> None:
    for line in text.strip("\n").splitlines():
        print(f"   {line}")
    print()


def _default_workspaces() -> str:
    dev = Path.home() / "dev"
    return str(dev if dev.exists() else Path.home())


async def run_setup(env_path: Path) -> int:
    """Guided first-run configuration; writes ``agent.env`` (mode 600)."""
    total = 6
    print("\n=== DevAgent agent setup ===\n")
    print("This machine will become a DevAgent worker: it takes coding tasks from")
    print("the gateway, works in an isolated git worktree and waits for your")
    print("approval before applying anything.\n")
    print("You need three things:")
    print("  • the gateway URL      (the default is correct for the hosted gateway)")
    print("  • an agent API key     (generated in the app, Settings → API keys)")
    print("  • the folders it may edit (absolute paths, comma separated)")
    print("\nPress Enter to accept the default shown in [brackets].")

    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                existing[key.strip()] = value.strip().strip('"')

    # 1 -- gateway ------------------------------------------------------
    _step(1, total, "Gateway")
    _explain(
        """
The agent opens an OUTBOUND WebSocket to the gateway; no port is opened on this
machine. Keep the default if you use the hosted gateway.
Change it only for your own deployment, e.g.
  ws://127.0.0.1:8082/mobile/agent/ws   (gateway on this computer)
        """
    )
    gateway = _ask(
        "Gateway WebSocket URL",
        existing.get("DEVAGENT_GATEWAY_URL", "wss://deepseek-api.cs-hub.xyz/mobile/agent/ws"),
    )

    # 2 -- API key ------------------------------------------------------
    _step(2, total, "Agent API key")
    _explain(
        """
Generate a key for THIS machine in the app:
   Settings → API keys → name: mbp-yourname · kind: agent → Generate
Copy the dvk_… value immediately (it is shown only once) and paste it here.
One key per machine is best: you can revoke a single machine later.
The key used to log in to the app (kind 'pwa') is NOT accepted here.
        """
    )
    token = _ask("Agent API key (dvk_…)", existing.get("DEVAGENT_AGENT_TOKEN", ""), secret=True)
    if not token:
        print("\nA key is required to enrol this machine. Nothing was written.")
        return 2
    if not token.startswith(("dvk_", "dvs_")):
        print("   note: this is not a dvk_… key. It still works if it is the bootstrap agent token,")
        print("         but a per-machine key is safer (Settings → API keys).")

    # 3 -- identity -----------------------------------------------------
    _step(3, total, "Machine identity")
    _explain(
        """
A short unique name for this machine; it is what you see in the app and what
you pick in "Run on" when creating a task. Default: the hostname.
        """
    )
    agent_id = _ask(
        "Agent id (unique per machine)",
        existing.get("DEVAGENT_AGENT_ID", socket.gethostname().split(".")[0]),
    )

    # 4 -- workspaces ---------------------------------------------------
    _step(4, total, "Folders it may edit")
    _explain(
        """
Absolute paths, comma separated. The agent refuses anything outside them and
edits inside an isolated git worktree (your working tree is never touched).
Usually your code folder, e.g. ~/dev or ~/Projects.
        """
    )
    workspaces = _ask(
        "Allowed workspace roots",
        existing.get("DEVAGENT_WORKSPACES", _default_workspaces()),
    )
    while True:
        missing = []
        for entry in [part.strip() for part in workspaces.split(",") if part.strip()]:
            resolved = Path(entry).expanduser()
            if not resolved.exists():
                missing.append(str(resolved))
        if not missing:
            break
        print("   these paths do not exist on this machine:")
        for path in missing:
            print(f"     • {path}")
        if _ask_bool("Save them anyway (the agent will refuse tasks there)?", False):
            break
        workspaces = _ask("Allowed workspace roots", workspaces)

    # 5 -- AI provider --------------------------------------------------
    _step(5, total, "AI provider")
    _explain(
        """
Recommended: press Enter. The gateway attaches the AI provider configured in
the app (Settings → AI providers) to every task, so this machine does not need
any credential and you can switch models without touching the agent.
Fill it in only if this machine must call a specific endpoint on its own
(OpenAI-compatible base URL ending in /v1).
        """
    )
    llm_url = _ask(
        "Local LLM endpoint (Enter = use the provider from the app)",
        existing.get("DEVAGENT_LLM_BASE_URL", ""),
    )
    llm_token = ""
    if llm_url:
        llm_token = _ask("LLM token for that endpoint", existing.get("DEVAGENT_LLM_TOKEN", ""), secret=True)

    # 6 -- workflow -----------------------------------------------------
    _step(6, total, "Execution behaviour")
    _explain(
        """
Test command: run after applying an approved patch. Empty = auto-detect
(npm test, pytest, go test, cargo test, make test).
Push / PR: off by default — the agent commits on a task branch locally.
Dry-run: generate the diff but never apply it (useful for a first try).
        """
    )
    test_command = _ask("Test command (optional)", existing.get("DEVAGENT_TEST_COMMAND", ""))
    push = _ask_bool("Push the branch after committing?", _bool(existing.get("DEVAGENT_PUSH_BRANCH"), False))
    create_pr = push and _ask_bool("Open a pull request with gh?", _bool(existing.get("DEVAGENT_CREATE_PR"), False))
    dry_run = _ask_bool("Start in dry-run mode?", _bool(existing.get("DEVAGENT_DRY_RUN"), False))

    values = {
        "DEVAGENT_GATEWAY_URL": gateway,
        "DEVAGENT_AGENT_TOKEN": token,
        "DEVAGENT_AGENT_ID": agent_id,
        "DEVAGENT_WORKSPACES": workspaces,
        "DEVAGENT_STATE_DIR": existing.get("DEVAGENT_STATE_DIR", str(Path.home() / ".devagent")),
        "DEVAGENT_HEARTBEAT_SECONDS": existing.get("DEVAGENT_HEARTBEAT_SECONDS", "10"),
        "DEVAGENT_BROWSER_HEADERS": "true",
        "DEVAGENT_TRANSPORT": existing.get("DEVAGENT_TRANSPORT", "auto"),
        "DEVAGENT_LLM_BASE_URL": llm_url,
        "DEVAGENT_LLM_TOKEN": llm_token,
        "DEVAGENT_MODEL": existing.get("DEVAGENT_MODEL", "DeepSeek-Coder-V2.5"),
        "DEVAGENT_TEST_COMMAND": test_command,
        "DEVAGENT_PUSH_BRANCH": "true" if push else "false",
        "DEVAGENT_CREATE_PR": "true" if create_pr else "false",
        "DEVAGENT_DRY_RUN": "true" if dry_run else "false",
        "DEVAGENT_APPROVE_COMMANDS": existing.get("DEVAGENT_APPROVE_COMMANDS", "true"),
    }

    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by `devagent-agent --setup` - keep this file private.",
        *[f'{key}="{value}"' for key, value in values.items() if value != ""],
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(env_path, 0o600)

    masked = f"{token[:8]}…{token[-4:]}" if len(token) > 14 else "…"
    print("\n=== Summary ===")
    print(f"  gateway     : {gateway}")
    print(f"  agent id    : {agent_id}")
    print(f"  workspaces  : {workspaces}")
    print(f"  api key     : {masked}")
    print(f"  provider    : {'local ' + llm_url if llm_url else 'from the app (recommended)'}")
    print(f"  test cmd    : {test_command or 'auto-detect'}")
    print(f"  push/PR     : {'yes' if push else 'no'}{' / gh' if create_pr else ''}")
    print(f"  dry-run     : {'yes' if dry_run else 'no'}")
    print(f"  transport   : {values['DEVAGENT_TRANSPORT']} (ws, then HTTPS long-poll)")
    print(f"  saved to    : {env_path} (mode 600)")

    print("\nChecking the gateway handshake…")
    ok, detail = await _validate_connection(values)
    if ok:
        print(f"✅ Connection OK — {detail}")
        launch = _launch_command(env_path)
        print("\nNext steps")
        print("  1) keep it running in the foreground to watch it:")
        print(f"       {launch} --verbose")
        print("     run it at login:")
        print("       macOS/Linux → ./devagent-agent --install-service   (launchd / systemd --user)")
        print("       Windows     → schtasks /Create /SC ONLOGON /TN DevAgent /TR \"<bin> --env-file <env>\"")
        print("  2) open the app → Dashboard: this machine appears in the fleet within ~2s")
        print("  3) New Task → 'Run on' → pick this machine (or leave it on any idle agent)")
        return 0

    print(f"\n⚠️  Could not enrol yet: {detail}")
    if "wrong_token_kind" in detail or "kind '" in detail:
        print("    This key belongs to the app (kind 'pwa'), not to an agent.")
        print("    Create a separate key: Settings → API keys → kind 'agent' → Generate.")
    elif "403" in detail:
        print("    The handshake was refused before reaching the agent handler. Checking who answered…")
        probe = _http_probe(values.get("DEVAGENT_GATEWAY_URL", ""))
        print(f"    {probe}")
        if "server=cloudflare" in probe:
            print("    → Cloudflare (not the gateway) rejected it. In the dashboard for this zone:")
            print("       Security → Bots → Bot Fight Mode: Off   (it cannot be skipped by WAF rules)")
            print("       or Security → Settings → Browser Integrity Check: Off")
            print("       then retry the handshake (the values are already saved).")
        else:
            print("    → The gateway answered: check the key kind/state, then retry.")
    elif "401" in detail:
        print("    401 = the key is wrong, revoked, or of the wrong kind (must be `agent`).")
    elif "getaddrinfo" in detail or "Name or service" in detail:
        print("    DNS: check the gateway URL (typo? offline?).")
    elif "CERTIFICATE_VERIFY_FAILED" in detail or "SSLCertVerification" in detail:
        print("    TLS: no CA bundle was found on this machine.")
        print("      macOS:  SSL_CERT_FILE=/etc/ssl/cert.pem " + f'"{sys.executable}" --setup --env-file "{env_path}"')
        print("      or install the bundle:  brew install ca-certificates")
        print("      (builds from v1.0.2 onward ship their own CA bundle)")
    print("    Your answers were saved, so you can fix one value and re-run:")
    print(f'      "{sys.executable}" --setup --env-file "{env_path}"' if getattr(sys, "frozen", False) else f'      "{sys.executable}" "{Path(__file__).resolve()}" --setup --env-file "{env_path}"')
    return 1



async def amain(args: argparse.Namespace) -> int:
    if args.version:
        print(f"devagent-agent {CLIENT_VERSION} ({platform.system()} {platform.release()} {platform.machine()})")
        return 0

    if args.setup:
        return await run_setup(Path(args.env_file).expanduser())

    if args.install_service:
        env_path = Path(args.env_file).expanduser()
        if not env_path.exists():
            print(f"No configuration at {env_path} — run --setup first.", file=sys.stderr)
            return 2
        return install_service(env_path)

    if args.env_file:
        load_env_file(Path(args.env_file).expanduser())

    # First run: no settings yet and a human at the keyboard -> ask the questions.
    env_path = Path(args.env_file).expanduser()
    if not args.check and not env_path.exists() and sys.stdin.isatty():
        print(f"No configuration found at {env_path}; starting the setup wizard.\n")
        code = await run_setup(env_path)
        if code != 0:
            return code
        print()

    config = Config.from_env()
    if args.check:
        print("DevAgent reference agent")
        print(f"  gateway      : {config.gateway_url}")
        print(f"  agent id     : {config.agent_id}")
        print(f"  workspaces   : {', '.join(config.workspaces)}")
        print(f"  state dir    : {config.state_dir}")
        print(f"  transport    : {config.transport} (auto = ws, then HTTPS long-poll)")
        print(f"  llm endpoint : {config.llm_base_url or '(not configured: manual plan mode)'}")
        print(f"  git          : {which('git') or 'MISSING'}")
        print(f"  gh           : {which('gh') or '(not installed, PRs disabled)'}")
        if config.test_command:
            head = shlex.split(config.test_command)[0] if config.test_command.strip() else ""
            status = "ok" if which(head) else "NOT FOUND — leave empty for auto-detect"
            print(f"  test command : {config.test_command} ({status})")
        else:
            print("  test command : auto-detect")
        print(f"  python       : {sys.version.split()[0]} on {platform.system()} {platform.release()}")
        for workspace in config.workspaces:
            root = Path(workspace).expanduser()
            print(f"  workspace {root}: {'ok' if root.exists() else 'MISSING'}")
        missing_roots = [
            str(Path(workspace).expanduser())
            for workspace in config.workspaces
            if not Path(workspace).expanduser().exists()
        ]
        if missing_roots:
            print("\n  ⚠️  DEVAGENT_WORKSPACES points to missing folders:")
            for path in missing_roots:
                print(f"     • {path}")
            print("     Tasks targeting them fail immediately; fix the path (typos!) and restart.")
        return 0

    runner = TaskRunner(config)
    client = GatewayClient(config, runner)
    runner.client = client

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stopping.set)

    LOG.info("DevAgent agent %s starting (gateway=%s)", config.agent_id, config.gateway_url)
    connection = asyncio.create_task(client.run_forever())
    await stopping.wait()
    LOG.info("shutting down")
    client.stopping.set()
    connection.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await connection
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DevAgent macOS reference agent")
    parser.add_argument("--env-file", default=str(default_env_path()))
    parser.add_argument("--check", action="store_true", help="validate configuration and exit")
    parser.add_argument("--setup", action="store_true", help="interactive first-run configuration")
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="start at login (launchd on macOS, systemd --user on Linux)",
    )
    parser.add_argument("--version", action="store_true", help="print the agent version")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_ca_bundle()
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
