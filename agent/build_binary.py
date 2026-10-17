#!/usr/bin/env python3
"""Build the standalone agent binary with PyInstaller.

Usage:
    python agent/build_binary.py            # -> dist/devagent-agent[.exe]
    python agent/build_binary.py --clean    # remove build/ dist/ first

The result is a single-file executable: the user downloads it from GitHub
Releases and runs ``devagent-agent --setup``; no Python installation is needed.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "devagent_agent.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the DevAgent agent binary")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--name", default="devagent-agent")
    args = parser.parse_args(argv)

    if args.clean:
        for folder in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(folder, ignore_errors=True)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is missing: python -m pip install pyinstaller", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--name",
        args.name,
        # websockets ships optional submodules; collect them all to be safe.
        "--collect-submodules",
        "websockets",
        "--collect-data",
        "websockets",
        str(ENTRY),
    ]

    # A frozen interpreter has no CA store, so `wss://` fails with
    # CERTIFICATE_VERIFY_FAILED on a clean machine. Ship certifi's bundle.
    try:
        import certifi  # noqa: F401
    except ImportError:
        print(
            "WARNING: certifi is missing — the binary will fall back to the OS bundle\n"
            "         (macOS /etc/ssl/cert.pem). Install it with: python -m pip install certifi",
            file=sys.stderr,
        )
    else:
        command[command.index(str(ENTRY)):command.index(str(ENTRY))] = [
            "--collect-data",
            "certifi",
            "--hidden-import",
            "certifi",
        ]
    print(" ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)

    suffix = ".exe" if platform.system() == "Windows" else ""
    binary = ROOT / "dist" / f"{args.name}{suffix}"
    if not binary.exists():  # pragma: no cover - build failure
        print(f"Build finished but {binary} is missing", file=sys.stderr)
        return 1
    print(f"\nOK: {binary} ({binary.stat().st_size / 1_048_576:.1f} MB)")
    print(f"platform: {platform.system()} {platform.machine()}")
    print(
        "Smoke test: "
        f"{binary} --version  (then: {binary} --setup)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
