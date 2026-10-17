"""Small operational CLI.

Usage:
    python -m app.cli hash-password            # interactive, prints the env line
    python -m app.cli hash-password --stdin    # pipe the password in (no echo)
    python -m app.cli token --name iPhone --kind pwa   # mint a key from the shell
    python -m app.cli tokens                   # list keys
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from .auth import create_api_token, hash_password, token_to_dict
from .config import Settings
from .db import Database


def _cmd_hash_password(args: argparse.Namespace) -> int:
    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Admin password: ")
        if password != getpass.getpass("Repeat: "):
            print("Passwords do not match", file=sys.stderr)
            return 2
    if len(password) < 10:
        print("Use at least 10 characters", file=sys.stderr)
        return 2
    print("\nAdd this line to /etc/deepseek-devagent.env (chmod 600):\n")
    print(f'DEVAGENT_ADMIN_PASSWORD_HASH="{hash_password(password)}"')
    return 0


async def _with_db(settings: Settings):
    db = Database(settings.database_path)
    await db.connect()
    return db


def _cmd_token(args: argparse.Namespace) -> int:
    settings = Settings.load()

    async def run() -> int:
        db = await _with_db(settings)
        try:
            raw, row = await create_api_token(
                db,
                name=args.name,
                kind=args.kind,
                created_by="cli",
                expires_in_days=args.days,
            )
            print(raw)
            print(f"# id={row['id']} kind={row['kind']} prefix={row['prefix']}", file=sys.stderr)
            return 0
        finally:
            await db.close()

    return asyncio.run(run())


def _cmd_tokens(args: argparse.Namespace) -> int:
    settings = Settings.load()

    async def run() -> int:
        db = await _with_db(settings)
        try:
            rows = await db.list_tokens()
            if not rows:
                print("(no API keys yet)")
            for row in rows:
                meta = token_to_dict(row)
                print(
                    f"{meta['id']:<16} {meta['prefix']:<14} {meta['kind']:<8} "
                    f"{meta['status']:<8} {meta['name']}"
                )
            return 0
        finally:
            await db.close()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="DevAgent gateway maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    hash_parser = sub.add_parser("hash-password", help="hash an admin password for the env file")
    hash_parser.add_argument("--stdin", action="store_true")
    hash_parser.set_defaults(func=_cmd_hash_password)

    token_parser = sub.add_parser("token", help="create an API key")
    token_parser.add_argument("--name", required=True)
    token_parser.add_argument("--kind", default="pwa", choices=["admin", "pwa", "agent"])
    token_parser.add_argument("--days", type=int, default=None, help="optional expiry")
    token_parser.set_defaults(func=_cmd_token)

    list_parser = sub.add_parser("tokens", help="list API keys")
    list_parser.set_defaults(func=_cmd_tokens)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
