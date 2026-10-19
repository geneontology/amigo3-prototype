"""Whole-site protection and fail-closed, live allowlist revocation."""

import json

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect
from test_auth import AGENT, HEADERS, ORIGIN, begin, client_for, github, sign_in

from amigo3.api import create_app
from amigo3.auth import AuthConfig


@pytest.fixture
def private_app(built_data, tmp_path):
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({"github_ids": [1]}))
    app = create_app(
        built_data,
        "duckdb",
        chat_command=AGENT,
        auth_config=AuthConfig("id", "secret", ORIGIN, "private", allowlist),
    )
    app.state.auth.transport = httpx.MockTransport(github)
    return app


def test_private_site_gates_pages_data_docs_and_assets(private_app):
    with client_for(private_app) as client:
        response = client.get("/genes/example?tab=references", headers={"accept": "text/html"})
        assert response.status_code == 303
        assert response.headers["location"].startswith("/auth/github?return_to=")
        for path in [
            "/",
            "/api/collections",
            "/api/health",
            "/docs",
            "/openapi.json",
            "/auth.js",
            "/api/report-assets/example",
            "/api/snapshots/example",
        ]:
            result = client.get(path)
            assert result.status_code == 401, path
            assert result.headers["cache-control"] == "no-store"
        assert client.post("/api/query", json={"collection": "genes"}).status_code == 401
        assert client.get("/api/me").json()["user"] is None
        assert sign_in(client, "bob").status_code == 403
        assert client.get("/api/collections").status_code == 401
        assert sign_in(client, "alice", "/").status_code == 303
        for path in ["/", "/api/collections", "/auth.js", "/docs"]:
            result = client.get(path)
            assert result.status_code == 200, path
            assert result.headers["cache-control"] == "no-store"
        assert client.post("/auth/logout", headers=HEADERS).status_code == 200
        assert client.get("/").status_code == 401


@pytest.mark.parametrize("replacement", ['{"github_ids": []}', '{"github_ids": [true]}', "malformed", None])
def test_allowlist_change_revokes_existing_cookie(private_app, replacement):
    with client_for(private_app) as client:
        sign_in(client)
        path = private_app.state.auth.config.allowlist_file
        if replacement is None:
            path.unlink()
        else:
            path.write_text(replacement)
        assert client.get("/api/collections").status_code == 401
        assert not private_app.state.auth.sessions
        assert sign_in(client).status_code == 403


def test_revocation_terminates_connected_chat_without_http_request(private_app, monkeypatch):
    monkeypatch.setattr("amigo3.auth.ALLOWLIST_POLL_SECONDS", 0.05)
    with client_for(private_app) as client:
        sign_in(client)
        with client.websocket_connect(
            "ws://localhost/api/chat/acp?connection=" + "c" * 32, headers=HEADERS
        ) as socket:
            assert socket.receive_json()["method"] == "amigo/restore"
            private_app.state.auth.config.allowlist_file.write_text('{"github_ids": []}')
            with pytest.raises(WebSocketDisconnect) as error:
                while True:
                    socket.receive_json()
            assert error.value.code == 4401
        assert not private_app.state.chat_sessions


def test_private_cancel_does_not_redirect_back_into_login(private_app):
    with client_for(private_app) as client:
        state = begin(client)["state"][0]
        result = client.get("/auth/github/callback", params={"state": state, "error": "access_denied"})
        assert result.status_code == 401
        assert "location" not in result.headers


def test_private_configuration_requires_credentials_and_allowlist(monkeypatch):
    for name in [
        "AMIGO3_GITHUB_CLIENT_ID",
        "AMIGO3_GITHUB_CLIENT_SECRET",
        "AMIGO3_AUTH_ORIGIN",
        "AMIGO3_GITHUB_ALLOWLIST_FILE",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AMIGO3_SITE_ACCESS", "private")
    with pytest.raises(ValueError, match="require GitHub"):
        AuthConfig.from_env()
    with pytest.raises(ValueError, match="require AMIGO3_GITHUB_ALLOWLIST_FILE"):
        AuthConfig("id", "secret", ORIGIN, "private")


def test_public_browsing_can_still_require_allowlisted_logins(built_data, tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text('{"github_ids": [1]}')
    app = create_app(built_data, "duckdb", auth_config=AuthConfig("id", "secret", ORIGIN, "public", path))
    app.state.auth.transport = httpx.MockTransport(github)
    with client_for(app) as client:
        assert client.get("/").status_code == 200
        assert sign_in(client, "bob").status_code == 403
        assert sign_in(client, "alice").status_code == 303
