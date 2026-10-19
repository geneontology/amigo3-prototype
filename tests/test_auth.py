"""Offline OAuth flow and enforcement at the chat HTTP/WebSocket boundaries."""

import base64
import hashlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from amigo3.api import create_app
from amigo3.auth import FLOW_COOKIE, SESSION_COOKIE, AuthConfig, digest

ORIGIN = "http://localhost"
HEADERS = {"origin": ORIGIN}
AGENT = [sys.executable, str(Path(__file__).parent / "fixtures/acp_agent.py")]


def github(request):
    if request.url.path == "/login/oauth/access_token":
        params = parse_qs(request.content.decode())
        if params["code"] == ["bad"]:
            return httpx.Response(200, json={"error": "bad_verification_code"})
        return httpx.Response(200, json={"access_token": "private-" + params["code"][0]})
    assert request.url == "https://api.github.com/user"
    login = request.headers["authorization"].removeprefix("Bearer private-")
    return httpx.Response(200, json={"id": 1 if login == "alice" else 2, "login": login})


@pytest.fixture
def app(built_data):
    app = create_app(
        built_data, "duckdb", chat_command=AGENT, auth_config=AuthConfig("client", "secret", ORIGIN)
    )
    app.state.auth.transport = httpx.MockTransport(github)
    return app


def client_for(app):
    return TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000), follow_redirects=False)


def begin(client, return_to="/chat"):
    response = client.get("/auth/github", params={"return_to": return_to})
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)


def sign_in(client, login="alice", return_to="/chat"):
    params = begin(client, return_to)
    return client.get("/auth/github/callback", params={"code": login, "state": params["state"][0]})


def test_oauth_pkce_cookie_identity_and_logout(app):
    calls = []

    def record(request):
        calls.append(request)
        return github(request)

    app.state.auth.transport = httpx.MockTransport(record)
    with client_for(app) as client:
        target = "/?collection=genes&q=kinase#results"
        params = begin(client, target)
        assert params["redirect_uri"] == [ORIGIN + "/auth/github/callback"]
        assert params["code_challenge_method"] == ["S256"]
        assert "scope" not in params
        state = params["state"][0]
        response = client.get("/auth/github/callback", params={"state": state, "code": "alice"})
        assert response.status_code == 303 and response.headers["location"] == target
        assert (
            "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
        )
        exchange = parse_qs(calls[0].content.decode())
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"][0].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert params["code_challenge"] == [challenge]
        me = client.get("/api/me")
        assert me.headers["cache-control"] == "no-store"
        assert me.json()["user"] == {"id": 1, "login": "alice"}
        assert "private-alice" not in me.text
        cookie = client.cookies.get(SESSION_COOKIE)
        assert cookie not in app.state.auth.sessions
        assert digest(cookie) in app.state.auth.sessions
        assert (
            client.get("/auth/github/callback", params={"state": state, "code": "alice"}).status_code == 400
        )
        assert client.post("/auth/logout").status_code == 403
        assert client.post("/auth/logout", headers={"origin": "https://evil.example"}).status_code == 403
        assert client.post("/auth/logout", headers=HEADERS).status_code == 200
        assert client.get("/api/me").json()["user"] is None
        client.cookies.set(SESSION_COOKIE, cookie)
        assert client.get("/api/chat/sessions").status_code == 401


@pytest.mark.parametrize(
    "target",
    ["https://evil.example", "//evil.example", "/\\evil.example", "/\n/evil.example", "/auth/github"],
)
def test_return_url_restrictions(app, target):
    with client_for(app) as client:
        assert sign_in(client, return_to=target).headers["location"] == "/"


def test_state_is_browser_bound_expires_and_consumed_on_cancellation(app):
    with client_for(app) as client:
        params = begin(client)
        state = params["state"][0]
        assert (
            client.get("/auth/github/callback", params={"state": "wrong", "code": "alice"}).status_code == 400
        )
        binding = client.cookies.get(FLOW_COOKIE)
        client.cookies.clear()
        assert (
            client.get("/auth/github/callback", params={"state": state, "code": "alice"}).status_code == 400
        )
        client.cookies.set(FLOW_COOKIE, binding, domain="localhost.local", path="/auth")
        response = client.get("/auth/github/callback", params={"state": state, "error": "access_denied"})
        assert response.status_code == 303 and "cancelled" in response.headers["location"]
        assert not app.state.auth.sessions and state not in app.state.auth.flows
        state = begin(client)["state"][0]
        app.state.auth.flows[state]["expires"] = 0
        assert (
            client.get("/auth/github/callback", params={"state": state, "code": "alice"}).status_code == 400
        )
        assert sign_in(client, "bad").status_code == 502
        assert not app.state.auth.sessions


def test_anonymous_chat_is_denied_but_browsing_is_public(app):
    with client_for(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/collections").status_code == 200
        assert client.get("/chat").status_code == 200
        config = client.get("/api/chat/config").json()
        assert config["login_required"] and not config["enabled"]
        for method, path, body in [
            ("GET", "/api/chat/sessions", None),
            ("GET", "/api/chat/sessions/key/files", None),
            ("GET", "/api/chat/sessions/key/file?path=x", None),
            ("DELETE", "/api/chat/sessions/key", None),
            ("PUT", "/api/chat/permissions", {"mode": "allow"}),
            ("PUT", "/api/chat/sessions/key/permissions", {"mode": "allow"}),
            ("POST", "/api/chat/render", {"text": "test"}),
        ]:
            assert client.request(method, path, json=body, headers=HEADERS).status_code == 401
        with (
            pytest.raises(WebSocketDisconnect) as error,
            client.websocket_connect("ws://localhost/api/chat/acp", headers=HEADERS),
        ):
            pass
        assert error.value.code == 4401
        assert app.state.chat_sessions == {}


def test_chat_ownership_policies_files_and_logout(app):
    # One app/lifespan, two cookie jars; all requests share the same event loop.
    with client_for(app) as client:
        sign_in(client, "alice")
        alice_cookie = client.cookies.get(SESSION_COOKIE)
        key = "a" * 32
        with client.websocket_connect(
            "ws://localhost/api/chat/acp?connection=" + key, headers=HEADERS
        ) as socket:
            assert socket.receive_json()["method"] == "amigo/restore"
            agent = app.state.chat_sessions[key]
            assert client.get("/api/chat/config").json()["enabled"]
            assert len(client.get("/api/chat/sessions").json()["sessions"]) == 1
            client.put("/api/chat/permissions", json={"mode": "allow"}, headers=HEADERS)
            client.cookies.clear()
            sign_in(client, "bob")
            assert client.get("/api/chat/sessions").json()["sessions"] == []
            assert client.get("/api/chat/sessions").json()["global_permission_mode"] == "ask"
            for path in [f"/api/chat/sessions/{key}/files", f"/api/chat/sessions/{key}/file?path=x"]:
                assert client.get(path).status_code == 404
            assert client.delete(f"/api/chat/sessions/{key}", headers=HEADERS).status_code == 404
            assert (
                client.put(
                    f"/api/chat/sessions/{key}/permissions", json={"mode": "ask"}, headers=HEADERS
                ).status_code
                == 404
            )
            client.put("/api/chat/permissions", json={"mode": "ask"}, headers=HEADERS)
            assert agent.effective_permission_mode == "allow"
            with (
                pytest.raises(WebSocketDisconnect),
                client.websocket_connect("ws://localhost/api/chat/acp?connection=" + key, headers=HEADERS),
            ):
                pass
            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE, alice_cookie, domain="localhost.local", path="/")
            assert client.post("/auth/logout", headers=HEADERS).status_code == 200
            with pytest.raises(WebSocketDisconnect) as error:
                while True:
                    socket.receive_json()
            assert error.value.code == 4401
            assert key not in app.state.chat_sessions and agent.process.returncode is not None


def test_session_expiry_closes_connected_agent(app, monkeypatch):
    monkeypatch.setattr("amigo3.auth.SESSION_TTL", 1)
    with client_for(app) as client:
        sign_in(client)
        with client.websocket_connect(
            "ws://localhost/api/chat/acp?connection=" + "b" * 32, headers=HEADERS
        ) as socket:
            assert socket.receive_json()["method"] == "amigo/restore"
            with pytest.raises(WebSocketDisconnect) as error:
                while True:
                    socket.receive_json()
            assert error.value.code == 4401
        assert client.get("/api/me").json()["user"] is None
        assert not app.state.chat_sessions


def test_no_configuration_and_partial_configuration(built_data, monkeypatch):
    for name in ["AMIGO3_GITHUB_CLIENT_ID", "AMIGO3_GITHUB_CLIENT_SECRET", "AMIGO3_AUTH_ORIGIN"]:
        monkeypatch.delenv(name, raising=False)
    with client_for(create_app(built_data, "duckdb")) as client:
        assert not client.get("/api/me").json()["login_enabled"]
        assert client.get("/auth/github").status_code == 404
    monkeypatch.setenv("AMIGO3_GITHUB_CLIENT_ID", "client")
    with pytest.raises(ValueError, match="Set all"):
        create_app(built_data, "duckdb")
    with pytest.raises(ValueError):
        AuthConfig("id", "secret", "http://public.example")


def test_https_cookies_and_configured_host(built_data):
    app = create_app(built_data, "duckdb", auth_config=AuthConfig("id", "secret", "https://amigo.example"))
    app.state.auth.transport = httpx.MockTransport(github)
    with TestClient(app, base_url="https://amigo.example", follow_redirects=False) as client:
        response = sign_in(client)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/me").json()["user"]
        assert client.get("/auth/github", headers={"host": "evil.example"}).status_code == 400
