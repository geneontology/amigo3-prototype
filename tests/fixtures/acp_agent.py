"""Deterministic ACP subprocess for protocol/UI tests; never calls a model."""

import json
import os
import sys
from pathlib import Path

if "--state" in sys.argv:
    Path(sys.argv[sys.argv.index("--state") + 1]).write_text(
        json.dumps({"pid": os.getpid(), "cwd": os.getcwd()})
    )


def send(**message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def update(**value):
    send(method="session/update", params={"sessionId": "test-session", "update": value})


prompt_id = None
authenticated = "--auth" not in sys.argv
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        assert message["params"]["clientCapabilities"] == {}
        send(
            id=message["id"],
            result={
                "protocolVersion": 1,
                "agentCapabilities": {},
                "agentInfo": {"name": "Test agent"},
                "authMethods": [{"id": "test-auth", "name": "Test authentication"}]
                if not authenticated
                else [],
            },
        )
    elif method == "session/new":
        if not authenticated:
            send(id=message["id"], error={"code": -32000, "message": "Authentication required"})
            continue
        params = message["params"]
        assert params["cwd"] == os.getcwd()
        assert len(params["mcpServers"]) == 1
        assert params["mcpServers"][0]["name"] == "amigo3-duckdb"
        send(id=message["id"], result={"sessionId": "test-session"})
    elif method == "authenticate":
        authenticated = True
        send(id=message["id"], result={})
    elif method == "session/prompt":
        prompt_id = message["id"]
        update(
            sessionUpdate="plan",
            entries=[{"content": "Inspect annotations", "status": "in_progress", "priority": "high"}],
        )
        update(
            sessionUpdate="tool_call",
            toolCallId="sql-1",
            title="Query annotations",
            kind="read",
            status="pending",
            rawInput={"sql": "SELECT count(*) FROM annotations"},
        )
        send(
            id="permission-1",
            method="session/request_permission",
            params={
                "sessionId": "test-session",
                "toolCall": {"toolCallId": "sql-1", "title": "Query annotations"},
                "options": [
                    {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Reject", "kind": "reject_once"},
                ],
            },
        )
    elif method == "session/cancel":
        send(id=prompt_id, result={"stopReason": "cancelled"})
        prompt_id = None
    elif message.get("id") == "permission-1":
        outcome = message["result"]["outcome"]
        if outcome["outcome"] == "cancelled":
            continue
        allowed = outcome["optionId"] == "allow"
        if allowed:
            Path("results").mkdir(exist_ok=True)
            Path("results/annotations.csv").write_text("collection,count\nannotations,4\n")
            Path("notes.md").write_text("# Query notes\n<script>notExecutable()</script>\n")
        update(
            sessionUpdate="tool_call_update",
            toolCallId="sql-1",
            status="completed" if allowed else "failed",
            rawOutput={"count": 4} if allowed else {"error": "Permission denied"},
        )
        for text in (
            ["There are **4 annotations**. ", "See [kinase activity](/terms/GO:0016301)."]
            if allowed
            else ["Query declined."]
        ):
            update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": text})
        send(id=prompt_id, result={"stopReason": "end_turn"})
        prompt_id = None
    elif method:
        send(id=message.get("id"), error={"code": -32601, "message": "Unsupported method"})
