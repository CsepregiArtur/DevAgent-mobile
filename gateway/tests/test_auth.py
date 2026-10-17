"""Authentication and API-key lifecycle tests (provider-like token management)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.server import create_app

from test_gateway import AUTH, TOKEN, create_task, make_settings


@pytest.fixture()
def client(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path))) as test_client:
        yield test_client


@pytest.fixture()
def password_client(tmp_path: Path):
    settings = make_settings(tmp_path, admin_password_hash=hash_password("corect-cal-puternic"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# ----------------------------------------------------------------------
# public config + login
# ----------------------------------------------------------------------
def test_auth_config_is_public(client: TestClient) -> None:
    response = client.get("/mobile/api/auth/config")
    assert response.status_code == 200
    body = response.json()
    assert body["tokenLogin"] is True
    assert body["passwordLogin"] is False


def test_login_with_token_and_me(client: TestClient) -> None:
    response = client.post("/mobile/api/auth/login", json={"token": TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["issued"] is False
    assert "admin" in body["auth"]["scopes"]

    me = client.get("/mobile/api/auth/me", headers=AUTH).json()
    assert me["canManageTokens"] is True
    assert me["canUseTasks"] is True


def test_login_rejects_bad_credentials(client: TestClient) -> None:
    assert client.post("/mobile/api/auth/login", json={"token": "nope"}).status_code == 401
    assert client.post("/mobile/api/auth/login", json={}).status_code == 400
    # password login is disabled unless a hash is configured
    assert client.post("/mobile/api/auth/login", json={"password": "x"}).status_code == 401


def test_password_login_issues_session_that_can_be_revoked(password_client: TestClient) -> None:
    response = password_client.post(
        "/mobile/api/auth/login", json={"password": "corect-cal-puternic"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["issued"] is True
    session = body["token"]
    assert session.startswith("dvs_")

    headers = {"Authorization": f"Bearer {session}"}
    assert password_client.get("/mobile/api/tasks", headers=headers).status_code == 200

    assert password_client.post("/mobile/api/auth/logout", headers=headers).json()["revoked"] is True
    assert password_client.get("/mobile/api/tasks", headers=headers).status_code == 401


def test_password_login_wrong_password(password_client: TestClient) -> None:
    assert password_client.post("/mobile/api/auth/login", json={"password": "gresit"}).status_code == 401


# ----------------------------------------------------------------------
# API keys
# ----------------------------------------------------------------------
def test_token_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/mobile/api/tokens", json={"name": "iPhone-Artur", "kind": "pwa"}, headers=AUTH
    )
    assert created.status_code == 201
    secret = created.json()["token"]
    meta = created.json()["meta"]
    assert secret.startswith("dvk_")
    assert meta["status"] == "active"
    assert "warning" in created.json()

    pwa_headers = {"Authorization": f"Bearer {secret}"}

    # the new key works for tasks ...

    assert client.get("/mobile/api/tasks", headers=pwa_headers).status_code == 200
    assert client.get("/mobile/api/auth/me", headers=pwa_headers).json()["canManageTokens"] is False

    # ... but cannot manage keys
    assert client.get("/mobile/api/tokens", headers=pwa_headers).status_code == 403
    assert (
        client.post("/mobile/api/tokens", json={"name": "x"}, headers=pwa_headers).status_code
        == 403
    )

    # the list never leaks the plaintext, only the prefix
    listed = client.get("/mobile/api/tokens", headers=AUTH).json()
    entry = next(row for row in listed["tokens"] if row["id"] == meta["id"])
    assert entry["prefix"] == meta["prefix"]
    assert secret not in str(listed)

    # revoke -> immediate 401
    assert client.post(f"/mobile/api/tokens/{meta['id']}/revoke", headers=AUTH).status_code == 200
    assert client.get("/mobile/api/tasks", headers=pwa_headers).status_code == 401

    # delete -> gone
    assert client.delete(f"/mobile/api/tokens/{meta['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/mobile/api/tokens/{meta['id']}", headers=AUTH).status_code == 404


def test_agent_key_opens_the_websocket_but_not_the_admin_api(client: TestClient) -> None:
    created = client.post(
        "/mobile/api/tokens", json={"name": "macbook-agent", "kind": "agent"}, headers=AUTH
    )
    secret = created.json()["token"]

    with client.websocket_connect(
        f"/mobile/agent/ws?token={secret}&agentId=macbook-1"
    ) as websocket:
        websocket.send_json(
            {"type": "hello", "hostname": "mbp", "workspace": "~/dev/x", "capabilities": ["git"]}
        )
        assert websocket.receive_json()["type"] == "welcome"

    agent_headers = {"Authorization": f"Bearer {secret}"}
    assert client.get("/mobile/api/tokens", headers=agent_headers).status_code == 403
    assert client.get("/mobile/api/tasks", headers=agent_headers).status_code == 403
    # agent channel endpoints accept an agent-scoped token
    assert client.get("/mobile/api/agent/status", headers=agent_headers).status_code == 200
    heartbeat = client.post(
        "/mobile/api/agent/heartbeat",
        json={"agentId": "macbook-1", "hostname": "mbp", "workspace": "~/dev/x"},
        headers=agent_headers,
    )
    assert heartbeat.status_code == 200


def test_bootstrap_tokens_cannot_be_revoked_from_the_api(client: TestClient) -> None:
    response = client.post("/mobile/api/tokens/bootstrap/revoke", headers=AUTH)
    assert response.status_code == 409
    assert "environment" in response.json()["detail"]["error"]


def test_created_key_can_do_the_full_task_flow(client: TestClient) -> None:
    created = client.post(
        "/mobile/api/tokens", json={"name": "web", "kind": "pwa"}, headers=AUTH
    )
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    task = client.post(
        "/mobile/api/tasks",
        json={"instruction": "test", "mode": "supervised"},
        headers=headers,
    )
    assert task.status_code == 201
    assert task.json()["task"]["status"] == "queued"
    assert client.get(f"/mobile/api/tasks/{task.json()['task']['id']}", headers=headers).status_code == 200
