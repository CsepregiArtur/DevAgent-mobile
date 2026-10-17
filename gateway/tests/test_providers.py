"""AI provider management + provider selection on tasks.

Requirement: providers can be added/edited/removed and selected automatically or
manually, **without** affecting the agent <-> gateway connection. The provider is
attached to the task and delivered inside ``task_assign``.

Removed in this iteration: the built-in simulator (there is no simulation mode).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import create_app

from test_gateway import AUTH, TOKEN, agent_url, create_task, make_settings, say_hello


@pytest.fixture()
def client(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path))) as test_client:
        yield test_client


def fake_provider(**overrides) -> dict:
    body = {
        "name": "DeepSeek (proxy)",
        "kind": "openai",
        "baseUrl": "https://deepseek-api.cs-hub.xyz/v1",
        "apiKey": "proxy-secret-key-1234",
        "models": ["DeepSeek-Coder-V2.5", "DeepSeek-V3"],
        "defaultModel": "DeepSeek-Coder-V2.5",
    }
    body.update(overrides)
    return body


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------
def test_provider_crud_and_default(client: TestClient) -> None:
    created = client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH)
    assert created.status_code == 201, created.text
    provider = created.json()["provider"]
    assert provider["name"] == "DeepSeek (proxy)"
    assert provider["models"] == ["DeepSeek-Coder-V2.5", "DeepSeek-V3"]
    assert provider["hasApiKey"] is True
    # the secret is never returned, only a hint
    assert "proxy-secret-key-1234" not in str(created.json())
    assert provider["apiKeyHint"].endswith("1234")

    second = client.post(
        "/mobile/api/providers", json=fake_provider(name="Local vLLM", baseUrl="http://10.0.0.5:8000/v1"), headers=AUTH
    ).json()["provider"]

    # make the second one the single default
    defaulted = client.post(
        f"/mobile/api/providers/{second['id']}/default", headers=AUTH
    ).json()["provider"]
    assert defaulted["isDefault"] is True

    listed = client.get("/mobile/api/providers", headers=AUTH).json()
    defaults = [row for row in listed["providers"] if row["isDefault"]]
    assert len(defaults) == 1 and defaults[0]["id"] == second["id"]
    assert listed["canManage"] is True

    updated = client.patch(
        f"/mobile/api/providers/{provider['id']}",
        json={"name": "DeepSeek private", "enabled": False},
        headers=AUTH,
    )
    assert updated.status_code == 200
    assert updated.json()["provider"]["name"] == "DeepSeek private"
    assert updated.json()["provider"]["enabled"] is False

    assert client.delete(f"/mobile/api/providers/{provider['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/mobile/api/providers/{provider['id']}", headers=AUTH).status_code == 404


def test_provider_validation(client: TestClient) -> None:
    assert client.post("/mobile/api/providers", json={"name": ""}, headers=AUTH).status_code == 400
    assert (
        client.post(
            "/mobile/api/providers", json=fake_provider(baseUrl="ftp://x"), headers=AUTH
        ).status_code
        == 400
    )
    client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH)
    duplicate = client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH)
    assert duplicate.status_code == 409


def test_provider_management_requires_admin(client: TestClient) -> None:
    key = client.post(
        "/mobile/api/tokens", json={"name": "phone", "kind": "pwa"}, headers=AUTH
    ).json()["token"]
    headers = {"Authorization": f"Bearer {key}"}

    # reading is allowed (the app shows the list) ...
    assert client.get("/mobile/api/providers", headers=headers).status_code == 200
    assert client.get("/mobile/api/providers", headers=headers).json()["canManage"] is False

    # ... writing is not
    assert client.post("/mobile/api/providers", json=fake_provider(), headers=headers).status_code == 403
    assert client.delete("/mobile/api/providers/x", headers=headers).status_code == 403


# ----------------------------------------------------------------------
# selection: manual / automatic
# ----------------------------------------------------------------------
def test_task_can_select_a_provider_manually_and_agent_receives_it(client: TestClient) -> None:
    primary = client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH).json()["provider"]
    secondary = client.post(
        "/mobile/api/providers",
        json=fake_provider(name="Backup", baseUrl="https://backup.example/v1", defaultModel="DeepSeek-V3"),
        headers=AUTH,
    ).json()["provider"]
    client.post(f"/mobile/api/providers/{primary['id']}/default", headers=AUTH)

    with client.websocket_connect(agent_url("agent-a")) as ws:
        say_hello(ws, capabilities=["git"])
        task = create_task(client, providerId=secondary["id"])
        assignment = ws.receive_json()
        assert assignment["type"] == "task_assign"
        assert assignment["task"]["id"] == task["id"]
        # the provider travels with the task, including its credential
        assert assignment["provider"]["id"] == secondary["id"]
        assert assignment["provider"]["baseUrl"] == "https://backup.example/v1"
        assert assignment["provider"]["apiKey"] == "proxy-secret-key-1234"
        assert assignment["model"] == "DeepSeek-V3"


def test_task_without_choice_uses_the_default_provider(client: TestClient) -> None:
    primary = client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH).json()["provider"]
    client.post(f"/mobile/api/providers/{primary['id']}/default", headers=AUTH)

    with client.websocket_connect(agent_url("agent-a")) as ws:
        say_hello(ws)
        create_task(client)
        assignment = ws.receive_json()
        assert assignment["provider"]["id"] == primary["id"]
        assert assignment["model"] == "DeepSeek-Coder-V2.5"


def test_disabled_provider_cannot_be_selected(client: TestClient) -> None:
    provider = client.post(
        "/mobile/api/providers", json=fake_provider(enabled=False), headers=AUTH
    ).json()["provider"]
    response = client.post(
        "/mobile/api/tasks",
        json={"instruction": "x", "providerId": provider["id"]},
        headers=AUTH,
    )
    assert response.status_code == 409


def test_unknown_provider_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/mobile/api/tasks", json={"instruction": "x", "providerId": "prov-nope"}, headers=AUTH
    )
    assert response.status_code == 404


def test_diagnostics_report_providers(client: TestClient) -> None:
    provider = client.post("/mobile/api/providers", json=fake_provider(), headers=AUTH).json()["provider"]
    client.post(f"/mobile/api/providers/{provider['id']}/default", headers=AUTH)

    diagnostics = client.get("/mobile/api/diagnostics", headers=AUTH).json()
    ai = diagnostics["aiProvider"]
    assert ai["provider"] == "DeepSeek (proxy)"
    assert ai["defaultModel"] == "DeepSeek-Coder-V2.5"
    assert ai["status"] == "ready"
    assert [row["id"] for row in ai["providers"]] == [provider["id"]]


def test_simulator_is_gone(client: TestClient) -> None:
    assert client.post("/mobile/api/agent/simulate-toggle", json={"enabled": True}, headers=AUTH).status_code in {404, 405}
    status = client.get("/mobile/api/agent/status", headers=AUTH).json()
    assert status["isSimulated"] is False
    assert "simulatorActive" not in status
