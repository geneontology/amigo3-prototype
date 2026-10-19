"""Chat protocol and SQL boundaries, without a live model or credentials."""

import asyncio
import json
import os
import sys
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.chat_tools import DuckTools

AGENT = [sys.executable, str(Path(__file__).parent / "fixtures" / "acp_agent.py")]
HEADERS = {"origin": "http://localhost"}


def local_client(app):
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))


@pytest.fixture
def tools(built_data):
    return DuckTools(Catalog(built_data).path)


def test_sql(tools):
    result = tools.query("WITH counts AS (SELECT count(*) n FROM annotations) SELECT * FROM counts")
    assert result["rows"] == [[4]]
    assert not result["truncated"]
    assert result["build_id"] == tools.path.name
    assert tools.query("SELECT * FROM annotations", limit=2)["truncated"]
    assert tools.schema("annotations")["rows"]
    assert "terms" in {row[0] for row in tools.schema()["rows"]}
    assert all(row[0] == "annotations" for row in tools.schema("annotations")["rows"])
    assert tools.schema("annotations' OR true --")["rows"] == []
    assert tools.manifest()["build_id"] == tools.path.name
    assert tools.query("SELECT repeat('x', 110000)")["truncated"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM annotations",
        "CREATE TABLE evil AS SELECT 1",
        "SELECT 1; SELECT 2",
        "COPY annotations TO '/tmp/amigo-chat-forbidden.csv'",
        "INSTALL httpfs",
        "SET enable_external_access=true",
        "ATTACH '/tmp/evil.db' AS evil",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM read_csv_auto('https://example.org/data.csv')",
    ],
)
def test_sql_rejects_writes_and_external_access(tools, sql):
    with pytest.raises((ValueError, duckdb.Error)):
        tools.query(sql)


def test_sql_timeout(tools):
    tools.timeout = 0.05
    with pytest.raises(duckdb.InterruptException):
        tools.query("SELECT sum(sin(i)) FROM range(10000000000) t(i)")
    assert tools.query("SELECT 1")["rows"] == [[1]]


def test_chat_disabled_and_markdown(built_data):
    with local_client(create_app(built_data, "duckdb")) as client:
        assert client.get("/chat").status_code == 200
        assert not client.get("/api/chat/config").json()["enabled"]
        result = client.post(
            "/api/chat/render",
            json={
                "text": "<script>alert(1)</script> ![alt](https://evil/image) [bad](javascript:alert(1)) **Good**"
            },
        ).json()["html"]
        assert "<script>" not in result and "<img" not in result and 'href="javascript:' not in result
        assert "<strong>Good</strong>" in result
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("ws://localhost/api/chat/acp", headers=HEADERS),
        ):
            pass


def initialize(ws):
    ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
    assert ws.receive_json()["result"]["protocolVersion"] == 1
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": "/not/allowed",
                "mcpServers": [{"command": "malicious"}],
            },
        }
    )
    assert ws.receive_json()["result"]["sessionId"] == "test-session"


def prompt(ws, request_id):
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": "test-session",
                "prompt": [{"type": "text", "text": "Count annotations"}],
            },
        }
    )
    assert ws.receive_json()["params"]["update"]["sessionUpdate"] == "plan"
    assert ws.receive_json()["params"]["update"]["rawInput"]["sql"].startswith("SELECT")
    permission = ws.receive_json()
    assert permission["method"] == "session/request_permission"
    return permission["id"]


def test_acp_stream_permission_followup_and_cancel(built_data):
    pytest.importorskip("mcp")
    with local_client(create_app(built_data, "duckdb", chat_command=AGENT)) as client:
        assert client.get("/api/chat/config").json()["enabled"]
        with client.websocket_connect("ws://localhost/api/chat/acp", headers=HEADERS) as ws:
            initialize(ws)
            permission_id = prompt(ws, 3)
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": permission_id,
                    "result": {"outcome": {"outcome": "selected", "optionId": "allow"}},
                }
            )
            assert ws.receive_json()["params"]["update"]["status"] == "completed"
            assert ws.receive_json()["params"]["update"]["content"]["text"] == "There are **4 annotations**. "
            assert "kinase" in ws.receive_json()["params"]["update"]["content"]["text"]
            assert ws.receive_json() == {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}
            permission_id = prompt(ws, 4)
            ws.send_json(
                {"jsonrpc": "2.0", "id": permission_id, "result": {"outcome": {"outcome": "cancelled"}}}
            )
            ws.send_json(
                {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "test-session"}}
            )
            assert ws.receive_json()["result"]["stopReason"] == "cancelled"
        # Each connection gets a fresh process/session, with cleanup on disconnect.
        with client.websocket_connect("ws://localhost/api/chat/acp", headers=HEADERS) as ws:
            initialize(ws)


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:9999", "null", ""])
def test_cross_origin_rejected(built_data, origin):
    with local_client(create_app(built_data, "duckdb", chat_command=AGENT)) as client:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("ws://localhost/api/chat/acp", headers={"origin": origin}),
        ):
            pass


def test_authentication_retry_and_process_cleanup(built_data, tmp_path):
    pytest.importorskip("mcp")
    state_path = tmp_path / "agent-state.json"
    command = [*AGENT, "--auth", "--state", str(state_path)]
    with local_client(create_app(built_data, "duckdb", chat_command=command)) as client:
        with client.websocket_connect("ws://localhost/api/chat/acp", headers=HEADERS) as ws:
            ws.send_json(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
            )
            assert ws.receive_json()["result"]["authMethods"]
            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}})
            assert "error" in ws.receive_json()
            ws.send_json(
                {"jsonrpc": "2.0", "id": 3, "method": "authenticate", "params": {"methodId": "test-auth"}}
            )
            assert ws.receive_json()["result"] == {}
            ws.send_json({"jsonrpc": "2.0", "id": 4, "method": "session/new", "params": {}})
            assert ws.receive_json()["result"]["sessionId"] == "test-session"
            state = json.loads(state_path.read_text())
            assert Path(state["cwd"]).is_dir()
        assert not Path(state["cwd"]).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(state["pid"], 0)


def test_mcp_stdio(built_data):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "amigo3.chat_tools",
                "--build",
                str(Catalog(built_data).path),
            ],
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {"database_schema", "query_sql", "build_manifest", "ui_skill"}
            skill = await session.call_tool("ui_skill", {})
            assert not skill.isError
            assert "/enrichment?genes=" in skill.content[0].text
            assert "run=0" in skill.content[0].text
            assert (await session.call_tool("ui_skill", {"name": "../chat.py"})).isError
            result = await session.call_tool("query_sql", {"sql": "SELECT count(*) FROM annotations"})
            assert not result.isError
            assert json.loads(result.content[0].text)["rows"] == [[4]]
            result = await session.call_tool("query_sql", {"sql": "DELETE FROM annotations"})
            assert result.isError

    asyncio.run(run())


def test_reconnect_restores_pending_turn_and_same_process(built_data, tmp_path):
    pytest.importorskip("mcp")
    state_path = tmp_path / "agent-state.json"
    app = create_app(built_data, "duckdb", chat_command=[*AGENT, "--state", str(state_path)])
    key = "a" * 32
    url = f"ws://localhost/api/chat/acp?connection={key}"
    with local_client(app) as client:
        with client.websocket_connect(url, headers=HEADERS) as ws:
            assert ws.receive_json()["params"]["history"] == []
            initialize(ws)
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "test-session",
                        "prompt": [{"type": "text", "text": "Count annotations"}],
                    },
                }
            )
            assert ws.receive_json()["method"] == "amigo/user_prompt"
            ws.receive_json()  # plan
            ws.receive_json()  # tool call
            assert ws.receive_json()["method"] == "session/request_permission"
            state = json.loads(state_path.read_text())
        assert Path(state["cwd"]).exists()
        os.kill(state["pid"], 0)
        with client.websocket_connect(url, headers=HEADERS) as ws:
            restored = ws.receive_json()["params"]
            assert restored["busy"] and restored["sessionId"] == "test-session"
            assert len(restored["permissions"]) == 1
            assert sum(m.get("method") == "amigo/user_prompt" for m in restored["history"]) == 1
            initialize(ws)  # Served from cache; the process and its context survive.
            assert json.loads(state_path.read_text()) == state
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "permission-1",
                    "result": {
                        "outcome": {"outcome": "selected", "optionId": "allow"},
                    },
                }
            )
            assert ws.receive_json()["method"] == "amigo/permission_resolved"
            ws.receive_json()  # tool completed
            ws.receive_json()  # answer chunk 1
            ws.receive_json()  # answer chunk 2
            assert ws.receive_json()["result"]["stopReason"] == "end_turn"
        with client.websocket_connect(url, headers=HEADERS) as ws:
            restored = ws.receive_json()["params"]
            assert not restored["busy"] and restored["permissions"] == []
            assert sum(m.get("method") == "amigo/user_prompt" for m in restored["history"]) == 1
            assert any(m.get("result", {}).get("stopReason") == "end_turn" for m in restored["history"])
            ws.send_json({"jsonrpc": "2.0", "method": "amigo/close", "params": {}})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert key not in app.state.chat_sessions
        assert not Path(state["cwd"]).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(state["pid"], 0)


def test_detached_session_expires_and_server_shutdown_cleans_up(built_data, monkeypatch):
    pytest.importorskip("mcp")
    import time

    import amigo3.chat as chat

    monkeypatch.setattr(chat, "SESSION_GRACE_SECONDS", 0.05)
    app = create_app(built_data, "duckdb", chat_command=AGENT)
    with local_client(app) as client:
        for key in ["b" * 32, "c" * 32]:
            with client.websocket_connect(
                f"ws://localhost/api/chat/acp?connection={key}", headers=HEADERS
            ) as ws:
                ws.receive_json()
                initialize(ws)
                session = app.state.chat_sessions[key]
            if key.startswith("b"):
                deadline = time.monotonic() + 3
                while key in app.state.chat_sessions and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert key not in app.state.chat_sessions
                # close() is async: wait for process cleanup as well as registry removal.
                while Path(session.workspace.name).exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not Path(session.workspace.name).exists()
            else:
                monkeypatch.setattr(chat, "SESSION_GRACE_SECONDS", 1800)
    assert not app.state.chat_sessions
    assert not Path(session.workspace.name).exists()


def test_session_list_titles_status_and_delete(built_data):
    pytest.importorskip("mcp")
    app = create_app(built_data, "duckdb", chat_command=AGENT)
    key = "d" * 32
    with local_client(app) as client:
        assert client.get("/api/chat/sessions").json()["sessions"] == []
        with client.websocket_connect(f"ws://localhost/api/chat/acp?connection={key}", headers=HEADERS) as ws:
            ws.receive_json()
            initialize(ws)
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "test-session",
                        "prompt": [{"type": "text", "text": "  Compare\nkinase evidence  "}],
                    },
                }
            )
            for _ in range(4):
                ws.receive_json()
            result = client.get("/api/chat/sessions").json()
            assert result["limit"] == 8
            item = result["sessions"][0]
            assert item["key"] == key and item["title"] == "Compare kinase evidence"
            assert item["message_count"] == 1 and item["status"] == "permission"
            assert item["attached"] and item["expires_at"] is None
            assert item["created_at"] <= item["updated_at"]
        item = client.get("/api/chat/sessions").json()["sessions"][0]
        assert not item["attached"] and item["expires_at"]
        session = app.state.chat_sessions[key]
        pid = session.process.pid
        assert client.delete(f"/api/chat/sessions/{key}", headers=HEADERS).json() == {"deleted": key}
        assert client.get("/api/chat/sessions").json()["sessions"] == []
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert client.delete(f"/api/chat/sessions/{key}", headers=HEADERS).status_code == 404


def test_delete_attached_session_notifies_client(built_data):
    pytest.importorskip("mcp")
    app = create_app(built_data, "duckdb", chat_command=AGENT)
    key = "e" * 32
    with local_client(app) as client:
        with client.websocket_connect(f"ws://localhost/api/chat/acp?connection={key}", headers=HEADERS) as ws:
            ws.receive_json()
            initialize(ws)
            assert client.delete(f"/api/chat/sessions/{key}", headers=HEADERS).status_code == 200
            assert ws.receive_json()["method"] == "amigo/deleted"
        assert not app.state.chat_sessions


def test_session_management_requires_local_origin(built_data):
    app = create_app(built_data, "duckdb", chat_command=AGENT)
    with local_client(app) as client:
        assert client.get("/api/chat/sessions", headers={"origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/chat/sessions", headers={"host": "evil.example"}).status_code == 403
        assert client.get("/api/chat/sessions", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert client.delete("/api/chat/sessions/unknown").status_code == 403
        assert (
            client.delete(
                "/api/chat/sessions/unknown", headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )


def start_retained(client, key):
    return client.websocket_connect(f"ws://localhost/api/chat/acp?connection={key}", headers=HEADERS)


def send_prompt(ws, identifier):
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "session/prompt",
            "params": {
                "sessionId": "test-session",
                "prompt": [{"type": "text", "text": "Count annotations"}],
            },
        }
    )


def receive_turn(ws, identifier):
    events = []
    while True:
        message = ws.receive_json()
        events.append(message)
        if message.get("id") == identifier and "method" not in message:
            assert message["result"]["stopReason"] == "end_turn"
            return events


def test_permission_policies_pending_background_overrides_and_revoke(built_data):
    pytest.importorskip("mcp")
    app = create_app(built_data, "duckdb", chat_command=AGENT)
    key = "f" * 32
    with local_client(app) as client:
        with start_retained(client, key) as ws:
            ws.receive_json()
            initialize(ws)
            send_prompt(ws, 3)
            for _ in range(4):
                ws.receive_json()
            assert client.get("/api/chat/sessions").json()["sessions"][0]["pending_approvals"] == 1
        # A session policy can unblock a session while its browser is detached.
        response = client.put(
            f"/api/chat/sessions/{key}/permissions", headers=HEADERS, json={"mode": "allow"}
        )
        assert response.json()["effective_mode"] == "allow"
        with start_retained(client, key) as ws:
            restored = ws.receive_json()["params"]
            assert restored["permissions"] == []
            # If the agent has not finished yet, consume the live remainder.
            if restored["busy"]:
                receive_turn(ws, 3)
            session = app.state.chat_sessions[key]
            decisions = [m for m in session.history if m.get("method") == "amigo/permission_resolved"]
            assert len(decisions) == 1 and decisions[0]["params"]["automatic"]
            assert decisions[0]["params"]["outcome"]["optionId"] == "allow"
            assert decisions[0]["params"]["scope"] == "session"
            client.put("/api/chat/permissions", headers=HEADERS, json={"mode": "allow"})
            client.put(f"/api/chat/sessions/{key}/permissions", headers=HEADERS, json={"mode": "ask"})
            send_prompt(ws, 4)
            for _ in range(4):
                ws.receive_json()
            assert len(session.permissions) == 1  # explicit ask overrides global allow
            client.put(f"/api/chat/sessions/{key}/permissions", headers=HEADERS, json={"mode": "inherit"})
            events = receive_turn(ws, 4)
            assert any(m.get("params", {}).get("scope") == "global" for m in events)
            client.put("/api/chat/permissions", headers=HEADERS, json={"mode": "ask"})
            send_prompt(ws, 5)
            for _ in range(4):
                ws.receive_json()
            assert len(session.permissions) == 1  # changing back to ask is effective
            client.put("/api/chat/permissions", headers=HEADERS, json={"mode": "allow"})
            receive_turn(ws, 5)
        # The global default also applies to future sessions.
        with start_retained(client, "0" * 32) as ws:
            ws.receive_json()
            initialize(ws)
            send_prompt(ws, 3)
            events = receive_turn(ws, 3)
            assert any(m.get("params", {}).get("scope") == "global" for m in events)
        assert (
            client.put("/api/chat/permissions", headers=HEADERS, json={"mode": "inherit"}).status_code == 422
        )
        assert client.put("/api/chat/permissions", json={"mode": "allow"}).status_code == 403


def test_no_allow_option_is_not_fabricated(built_data):
    from amigo3.chat import AgentSession

    async def run():
        session = AgentSession(AGENT, Catalog(built_data))
        try:
            session.permission_mode = "allow"
            session.permissions[1] = {
                "params": {"options": [{"optionId": "no", "kind": "reject_once", "name": "Reject"}]}
            }
            await session.apply_permission_policy()
            assert 1 in session.permissions
            with pytest.raises(ValueError, match="not offered"):
                await session.resolve_permission(1, {"outcome": "selected", "optionId": "invented"})
        finally:
            await session.close()

    asyncio.run(run())


def test_approval_preference_cancellation_and_duplicate_decisions(built_data):
    from unittest.mock import AsyncMock

    from amigo3.chat import AgentSession

    async def run():
        session = AgentSession(AGENT, Catalog(built_data), resumable=True)
        session.write = AsyncMock()
        options = [
            {"optionId": "remember", "kind": "allow_always", "name": "Always allow"},
            {"optionId": "once", "kind": "allow_once", "name": "Allow once"},
        ]
        try:
            session.permission_mode = "allow"
            session.permissions[1] = {"params": {"options": options}}
            await session.apply_permission_policy()
            assert session.write.call_args.args[0]["result"]["outcome"]["optionId"] == "once"
            # A stale manual click after an automatic decision cannot send another reply.
            await session.resolve_permission(1, {"outcome": "cancelled"})
            assert session.write.call_count == 1
            session.permissions[2] = {"params": {"options": options[:1]}}
            await session.apply_permission_policy()
            assert session.write.call_args.args[0]["result"]["outcome"]["optionId"] == "remember"
            session.permissions[3] = {"params": {"options": options}}
            session.cancelling = True
            await session.apply_permission_policy()
            assert session.write.call_args.args[0]["result"]["outcome"] == {"outcome": "cancelled"}
            assert not session.history[-1]["params"]["automatic"]
        finally:
            await session.close()

    asyncio.run(run())


def test_workspace_listing_preview_download_and_boundaries(built_data, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import amigo3.chat_workspace as files

    app = create_app(built_data, "duckdb", chat_command=AGENT)
    key = "1" * 32
    with local_client(app) as client, start_retained(client, key) as ws:
        ws.receive_json()
        initialize(ws)
        session = app.state.chat_sessions[key]
        root = Path(session.workspace.name)
        listing = f"/api/chat/sessions/{key}/files"
        preview = f"/api/chat/sessions/{key}/file"
        assert client.get(listing).json()["entries"] == []
        (root / "results").mkdir()
        (root / "results/counts.csv").write_bytes(b"gene,count\nTP53,4\n")
        (root / "notes.html").write_text("<script>alert(1)</script>")
        (root / "binary.dat").write_bytes(b"\x00\xff")
        (root / "long.txt").write_text("a" + "é" * 100010)
        outside = tmp_path / "outside.txt"
        outside.write_text("private")
        (root / "link").symlink_to(outside)
        (root / "external-dir").symlink_to(tmp_path, target_is_directory=True)
        os.mkfifo(root / "pipe")
        entries = client.get(listing).json()["entries"]
        assert next(e for e in entries if e["name"] == "link")["kind"] == "unavailable"
        assert next(e for e in entries if e["name"] == "results")["kind"] == "directory"
        assert (
            client.get(listing, params={"path": "results"}).json()["entries"][0]["path"]
            == "results/counts.csv"
        )
        assert (
            client.get(preview, params={"path": "notes.html"}).json()["text"] == "<script>alert(1)</script>"
        )
        assert client.get(preview, params={"path": "binary.dat"}).json()["binary"]
        long = client.get(preview, params={"path": "long.txt"}).json()
        assert long["truncated"] and not long["binary"] and "�" not in long["text"]
        response = client.get(preview, params={"path": "results/counts.csv", "download": True})
        assert response.content == b"gene,count\nTP53,4\n"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        for path in ["../outside.txt", str(outside), "link", "external-dir/outside.txt", "pipe", "results"]:
            assert client.get(preview, params={"path": path}).status_code in (400, 404)
        assert client.get(listing, params={"path": "external-dir"}).status_code == 404
        assert (
            client.get(
                preview, params={"path": "notes.html"}, headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        monkeypatch.setattr(files, "MAX_DOWNLOAD", 2)
        assert client.get(preview, params={"path": "notes.html", "download": True}).status_code == 413
        monkeypatch.setattr(files, "MAX_ENTRIES", 2)
        assert client.get(listing).json()["truncated"]
