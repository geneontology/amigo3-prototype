"""Local ACP WebSocket ↔ stdio bridge. Agent configuration belongs to the server."""

from __future__ import annotations

import asyncio
import contextlib
import html
import importlib.util
import json
import logging
import os
import re
import signal
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import anyio
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from amigo3.chat_workspace import list_files, read_file

LOG = logging.getLogger(__name__)
MAX_MESSAGE = 2_000_000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_METHODS = {"initialize", "authenticate", "session/new", "session/prompt", "session/cancel"}


class MarkdownRequest(BaseModel):
    text: str = Field(max_length=200_000)


class GlobalPermissions(BaseModel):
    mode: Literal["ask", "allow"]


class SessionPermissions(BaseModel):
    mode: Literal["inherit", "ask", "allow"]


def local_socket(socket: WebSocket) -> bool:
    """Reject cross-origin websites and remote access to this local subprocess service."""
    host = urlsplit("http://" + socket.headers.get("host", "")).hostname
    origin = urlsplit(socket.headers.get("origin", ""))
    return bool(
        socket.client
        and socket.client.host in LOCAL_HOSTS
        and host in LOCAL_HOSTS
        and origin.scheme in {"http", "https"}
        and origin.netloc == socket.headers.get("host")
    )


SESSION_GRACE_SECONDS = 30 * 60
MAX_HISTORY_BYTES = 16_000_000
MAX_SESSIONS = 8


class AgentSession:
    """An agent outlives its browser socket; one attached page receives its event log."""

    def __init__(self, command, catalog, resumable=False, global_permissions=None):
        self.owner = None
        self.command = command
        self.catalog = catalog
        self.resumable = resumable
        self.workspace = tempfile.TemporaryDirectory(prefix="amigo3-chat-")
        self.workspace_fd = os.open(self.workspace.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.global_permissions = global_permissions if global_permissions is not None else {"mode": "ask"}
        self.permission_mode = "inherit"
        self.permission_lock = asyncio.Lock()
        self.resolved_permissions = set()
        self.cancelling = False
        self.process = None
        self.tasks = []
        self.history = []
        self.history_bytes = 0
        self.subscriber = None
        self.expiry = None
        self.closed = False
        self.lifecycle_lock = asyncio.Lock()
        self.cleaned_up = False
        self.init_result = None
        self.session_result = None
        self.requests = {}
        self.handshakes = {}
        self.permissions = {}
        self.prompt_id = None
        self.next_id = 0
        self.title = "New conversation"
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at
        self.detached_at = None
        self.message_count = 0

    @property
    def effective_permission_mode(self):
        return self.global_permissions["mode"] if self.permission_mode == "inherit" else self.permission_mode

    async def resolve_permission(self, identifier, outcome, automatic=False):
        async with self.permission_lock:
            if automatic and (self.closed or self.effective_permission_mode != "allow"):
                return
            message = self.permissions.get(identifier)
            if message is None:
                if identifier in self.resolved_permissions:
                    return  # A manual response can race with a policy change.
                raise ValueError("Unknown permission request")
            if outcome.get("outcome") == "selected":
                options = message["params"]["options"]
                if not any(option["optionId"] == outcome.get("optionId") for option in options):
                    raise ValueError("Permission choice was not offered by the agent")
            elif outcome.get("outcome") != "cancelled":
                raise ValueError("Invalid permission outcome")
            if self.cancelling:
                outcome = {"outcome": "cancelled"}
                automatic = False
            scope = "global" if self.permission_mode == "inherit" else "session"
            decision = next(
                (o["name"] for o in message["params"]["options"] if o["optionId"] == outcome.get("optionId")),
                "Cancelled",
            )
            await self.write({"jsonrpc": "2.0", "id": identifier, "result": {"outcome": outcome}})
            self.permissions.pop(identifier, None)
            self.resolved_permissions.add(identifier)
            if self.resumable:
                self.publish(
                    {
                        "jsonrpc": "2.0",
                        "method": "amigo/permission_resolved",
                        "params": {
                            "id": identifier,
                            "outcome": outcome,
                            "automatic": automatic,
                            "decision": decision,
                            "scope": scope,
                            "title": message["params"].get("toolCall", {}).get("title", "Agent tool"),
                        },
                    }
                )

    async def apply_permission_policy(self):
        if self.closed or self.effective_permission_mode != "allow":
            return
        for identifier, message in list(self.permissions.items()):
            # Select an actual allow option, preferring a one-time grant so revocation stays effective.
            options = message["params"]["options"]
            option = next(
                (o for kind in ("allow_once", "allow_always") for o in options if o["kind"] == kind), None
            )
            if option:
                await self.resolve_permission(
                    identifier, {"outcome": "selected", "optionId": option["optionId"]}, automatic=True
                )

    def summary(self, key):
        status = (
            "ended"
            if self.closed
            else "permission"
            if self.permissions
            else "working"
            if self.prompt_id is not None
            else "ready"
            if self.session_result
            else "starting"
        )
        return {
            "key": key,
            "title": self.title,
            "status": status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": self.message_count,
            "pending_approvals": len(self.permissions),
            "pending_titles": [
                m["params"].get("toolCall", {}).get("title", "Agent tool") for m in self.permissions.values()
            ],
            "permission_mode": self.permission_mode,
            "effective_permission_mode": self.effective_permission_mode,
            "attached": self.subscriber is not None,
            "expires_at": (self.detached_at + timedelta(seconds=SESSION_GRACE_SECONDS)).isoformat()
            if self.detached_at
            else None,
        }

    async def start(self):
        async with self.lifecycle_lock:
            if self.cleaned_up:
                raise OSError("Session ended before agent startup")
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.workspace.name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_MESSAGE,
                start_new_session=True,
            )
            self.tasks = [asyncio.create_task(self.read_agent()), asyncio.create_task(self.drain_stderr())]

    def publish(self, message):
        if message.get("method") in {"amigo/user_prompt", "session/update", "session/request_permission"}:
            self.updated_at = datetime.now(UTC)
        size = len(json.dumps(message).encode())
        if self.history_bytes + size > MAX_HISTORY_BYTES:
            raise ValueError("Session transcript limit reached; download it and start a new session")
        self.history_bytes += size
        self.history.append(message)
        if self.subscriber:
            self.subscriber.put_nowait(message)

    def snapshot(self):
        return {
            "jsonrpc": "2.0",
            "method": "amigo/restore",
            "params": {
                "history": list(self.history),
                "permissions": list(self.permissions.values()),
                "busy": self.prompt_id is not None,
                "sessionId": (self.session_result or {}).get("sessionId"),
                "nextId": self.next_id,
                "closed": self.closed,
            },
        }

    async def write(self, message):
        if self.closed:
            raise ValueError("Agent session has ended; download the transcript and start a new session")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def client_message(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("Expected a JSON-RPC 2.0 object")
        method = message.get("method")
        identifier = message.get("id")
        if type(identifier) is int:
            self.next_id = max(self.next_id, identifier)
        if method:
            if method not in ALLOWED_METHODS:
                if identifier is not None:
                    self.publish(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "error": {"code": -32601, "message": "Method not supported by this bridge"},
                        }
                    )
                return
            params = message.setdefault("params", {})
            if not isinstance(params, dict):
                raise ValueError("Expected object parameters")
            if method in {"initialize", "session/new"}:
                # A reconnect negotiates with this bridge, not a second agent process.
                # If navigation interrupted negotiation, await the original reply first.
                if method in self.handshakes:
                    await asyncio.shield(self.handshakes[method])
                cached = self.init_result if method == "initialize" else self.session_result
                if cached:
                    self.publish({"jsonrpc": "2.0", "id": identifier, "result": cached})
                    return
                self.handshakes[method] = asyncio.get_running_loop().create_future()
            if method == "initialize":
                params["clientCapabilities"] = {}
            if method == "session/new":
                if not self.init_result:
                    raise ValueError("Initialize the connection before creating a session")
                message["params"] = {
                    "cwd": str(Path(self.workspace.name).resolve()),
                    "mcpServers": [
                        {
                            "name": "amigo3-duckdb",
                            "command": sys.executable,
                            "args": ["-m", "amigo3.chat_tools", "--build", str(self.catalog.path.resolve())],
                            "env": [],
                        }
                    ],
                }
            if method in {"session/prompt", "session/cancel"}:
                if not self.session_result or params.get("sessionId") != self.session_result["sessionId"]:
                    raise ValueError("Unknown session")
            if method == "session/prompt":
                if self.prompt_id is not None:
                    raise ValueError("A prompt is already running")
                self.prompt_id = identifier
                self.cancelling = False
                if not self.message_count:
                    texts = [
                        block.get("text", "")
                        for block in params.get("prompt", [])
                        if block.get("type") == "text"
                    ]
                    self.title = (
                        " ".join((texts[-1] if texts else "New conversation").split())[:80]
                        or "New conversation"
                    )
                self.message_count += 1
                if self.resumable:
                    self.publish(
                        {
                            "jsonrpc": "2.0",
                            "method": "amigo/user_prompt",
                            "params": {
                                "id": identifier,
                                "prompt": params.get("prompt", []),
                            },
                        }
                    )
            if identifier is not None:
                self.requests[identifier] = method
            if method == "session/cancel":
                self.cancelling = True
                for request_id in list(self.permissions):
                    await self.resolve_permission(request_id, {"outcome": "cancelled"})
        else:
            await self.resolve_permission(identifier, message.get("result", {}).get("outcome", {}))
            return
        await self.write(message)

    async def read_agent(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Expected an ACP object")
                if "method" not in message and "id" in message:
                    method = self.requests.pop(message["id"], None)
                    if method == "initialize":
                        self.init_result = message.get("result")
                    elif method == "session/new":
                        self.session_result = message.get("result")
                    elif method == "session/prompt":
                        self.prompt_id = None
                        self.resolved_permissions.update(self.permissions)
                        self.permissions.clear()
                        if "error" in message:
                            self.publish(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "amigo/turn_error",
                                    "params": {
                                        "message": message["error"].get("message", "Agent turn failed")
                                    },
                                }
                            )
                    future = self.handshakes.pop(method, None)
                    if future and not future.done():
                        future.set_result(None)
                elif message.get("method") == "session/request_permission":
                    self.resolved_permissions.discard(message["id"])
                    self.permissions[message["id"]] = message
                elif "method" in message and "id" in message:
                    await self.write(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Client capability not supported"},
                        }
                    )
                    continue
                self.publish(message)
                if message.get("method") == "session/request_permission":
                    if self.cancelling:
                        await self.resolve_permission(message["id"], {"outcome": "cancelled"})
                    else:
                        await self.apply_permission_policy()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            LOG.warning("ACP session ended: %s", exc)
        finally:
            self.closed = True
            self.prompt_id = None
            self.permissions.clear()
            for future in self.handshakes.values():
                if not future.done():
                    future.set_result(None)
            self.handshakes.clear()
            if self.subscriber:
                self.subscriber.put_nowait({"jsonrpc": "2.0", "method": "amigo/ended", "params": {}})

    async def drain_stderr(self):
        while await self.process.stderr.read(8192):
            pass

    async def close(self):
        with anyio.CancelScope(shield=True):
            async with self.lifecycle_lock:
                if self.cleaned_up:
                    return
                if self.expiry:
                    self.expiry.cancel()
                    self.expiry = None
                self.closed = True
                for task in self.tasks:
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
                if self.process:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(self.process.wait(), 3)
                    except TimeoutError:
                        pass
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                    await self.process.wait()
                self.workspace.cleanup()
                if self.workspace_fd is not None:
                    os.close(self.workspace_fd)
                    self.workspace_fd = None
                self.cleaned_up = True


def attach_chat(app, catalog, command: list[str] | None, browser_files):
    auth = app.state.auth
    available = bool(command) and all(importlib.util.find_spec(name) for name in ("mcp", "websockets"))

    @app.get("/chat", include_in_schema=False)
    def chat_page():
        return browser_files.page(Path(__file__).parent / "web" / "chat.html")

    @app.get("/api/chat/config")
    def chat_config(request: Request):
        login_required = bool(auth.config) and auth.session(request) is None
        return {
            "enabled": available and not login_required,
            "login_required": login_required,
            "build_id": catalog.build_id,
            "agent": Path(command[0]).name if command else None,
            "message": "Sign in with GitHub to use chat."
            if login_required
            else "Ready to start a local agent session."
            if available
            else "Chat is not enabled. Install the chat extra and start serve with --chat-command (see README).",
        }

    @app.post("/api/chat/render")
    def render_chat(body: MarkdownRequest, request: Request):
        auth.require(request)
        md = MarkdownIt("js-default", {"html": False})
        # Agent-provided images must not make automatic requests to external hosts.
        md.add_render_rule("image", lambda self, tokens, idx, options, env: html.escape(tokens[idx].content))
        return {"html": md.render(body.text)}

    sessions = {}
    global_permissions = {}

    def permissions_for(owner):
        return global_permissions.setdefault(owner, {"mode": "ask"})

    async def end_login(owner):
        for key, session in list(sessions.items()):
            if session.owner == owner:
                sessions.pop(key, None)
                await session.close()
        global_permissions.pop(owner, None)

    auth.on_end = end_login
    app.state.chat_sessions = sessions

    def require_local(request: Request, mutation=False):
        owner = auth.owner(request)
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if (
            not request.client
            or request.client.host not in LOCAL_HOSTS
            or urlsplit("http://" + host).hostname not in LOCAL_HOSTS
            or request.headers.get("sec-fetch-site") == "cross-site"
            or (
                origin
                and (urlsplit(origin).scheme not in {"http", "https"} or urlsplit(origin).netloc != host)
            )
            or (mutation and not origin)
        ):
            raise HTTPException(403, "Session management requires a local, same-origin browser")
        return owner

    @app.get("/api/chat/sessions")
    async def list_sessions(request: Request):
        owner = require_local(request)
        items = [session.summary(key) for key, session in sessions.items() if session.owner == owner]
        return {
            "sessions": sorted(
                items, key=lambda item: (item["pending_approvals"] > 0, item["updated_at"]), reverse=True
            ),
            "limit": MAX_SESSIONS,
            "global_permission_mode": permissions_for(owner)["mode"],
        }

    def get_session(key, request):
        if key not in sessions or sessions[key].owner != auth.owner(request):
            raise HTTPException(404, "Session no longer available")
        return sessions[key]

    @app.put("/api/chat/permissions")
    async def set_global_permissions(body: GlobalPermissions, request: Request):
        owner = require_local(request, mutation=True)
        permissions_for(owner)["mode"] = body.mode
        for session in list(sessions.values()):
            if session.owner == owner:
                await session.apply_permission_policy()
        return {"mode": body.mode}

    @app.put("/api/chat/sessions/{key}/permissions")
    async def set_session_permissions(key: str, body: SessionPermissions, request: Request):
        require_local(request, mutation=True)
        session = get_session(key, request)
        session.permission_mode = body.mode
        await session.apply_permission_policy()
        return {"mode": body.mode, "effective_mode": session.effective_permission_mode}

    @app.get("/api/chat/sessions/{key}/files")
    async def workspace_listing(key: str, request: Request, path: str = "."):
        require_local(request)
        session = get_session(key, request)
        if session.workspace_fd is None:
            raise HTTPException(404, "Workspace is no longer available")
        result = await anyio.to_thread.run_sync(list_files, session.workspace_fd, path)
        return {**result, "root": str(Path(session.workspace.name).resolve())}

    @app.get("/api/chat/sessions/{key}/file")
    async def workspace_preview(key: str, request: Request, path: str, download: bool = False):
        require_local(request)
        session = get_session(key, request)
        if session.workspace_fd is None:
            raise HTTPException(404, "Workspace is no longer available")
        result = await anyio.to_thread.run_sync(read_file, session.workspace_fd, path, download)
        if download:
            return Response(
                result,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(path).name, safe=""),
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "sandbox",
                    "Cache-Control": "no-store",
                },
            )
        return result

    @app.delete("/api/chat/sessions/{key}")
    async def delete_session(key: str, request: Request):
        require_local(request, mutation=True)
        session = get_session(key, request)
        sessions.pop(key)
        if session.subscriber:
            session.subscriber.put_nowait({"jsonrpc": "2.0", "method": "amigo/deleted", "params": {}})
        await session.close()
        return {"deleted": key}

    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with previous_lifespan(app) as state:
            try:
                yield state
            finally:
                await asyncio.gather(*(session.close() for session in sessions.values()))
                sessions.clear()

    app.router.lifespan_context = lifespan

    async def expire(key, session):
        if sessions.get(key) is session and session.subscriber is None:
            sessions.pop(key)
            session.expiry = None
            await session.close()

    @app.websocket("/api/chat/acp")
    async def chat_socket(socket: WebSocket):
        login = auth.session(socket)
        if auth.config and login is None:
            await socket.close(code=4401, reason="Sign in with GitHub to use chat")
            return
        owner = login.owner if login else None
        key = socket.query_params.get("connection")
        if not available or not local_socket(socket) or (key and not re.fullmatch(r"[a-f0-9]{32}", key)):
            await socket.close(code=1008)
            return
        # URLs without a reconnect key preserve the original ephemeral ACP bridge contract.
        session = sessions.get(key) if key else None
        if session and session.owner != owner:
            await socket.close(code=1008, reason="Session no longer available")
            return
        if session and session.subscriber is not None:
            await socket.close(code=1008, reason="Session already open in another page")
            return
        if not session and len(sessions) >= MAX_SESSIONS:
            await socket.close(code=1013, reason="Too many sessions; close an existing session first")
            return
        await socket.accept()
        if not session:
            session = AgentSession(
                command, catalog, resumable=bool(key), global_permissions=permissions_for(owner)
            )
            session.owner = owner
            session.subscriber = asyncio.Queue()
            if key:
                sessions[key] = session
            try:
                await session.start()
            except OSError:
                if key:
                    sessions.pop(key, None)
                await session.close()
                await socket.close(code=1011, reason="Unable to start agent")
                return
        if session.expiry:
            session.expiry.cancel()
            session.expiry = None
        session.detached_at = None
        queue = asyncio.Queue()
        session.subscriber = queue
        tasks = []
        explicitly_closed = False
        try:
            if key:
                await socket.send_json(session.snapshot())

            async def to_browser():
                while True:
                    await socket.send_json(await queue.get())

            async def from_browser():
                nonlocal explicitly_closed
                while True:
                    raw = await socket.receive_text()
                    if auth.config and auth.session(socket) is None:
                        explicitly_closed = True
                        await socket.close(code=4401, reason="Sign-in expired")
                        return
                    if len(raw.encode()) > MAX_MESSAGE:
                        raise ValueError("ACP message is too large")
                    message = json.loads(raw)
                    if isinstance(message, dict) and message.get("method") == "amigo/close":
                        explicitly_closed = True
                        return
                    await session.client_message(message)

            async def login_ended():
                nonlocal explicitly_closed
                await login.ended.wait()
                explicitly_closed = True
                await socket.close(code=4401, reason="Sign-in expired or signed out")

            tasks = [asyncio.create_task(to_browser()), asyncio.create_task(from_browser())]
            if login:
                tasks.append(asyncio.create_task(login_ended()))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            LOG.warning("ACP client disconnected: %s", exc)
            with contextlib.suppress(Exception):
                await socket.send_json(
                    {"jsonrpc": "2.0", "method": "amigo/error", "params": {"message": str(exc)}}
                )
        finally:
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                session.subscriber = None
                if not key or explicitly_closed:
                    if key:
                        sessions.pop(key, None)
                    await session.close()
                elif sessions.get(key) is session:
                    session.detached_at = datetime.now(UTC)
                    session.expiry = asyncio.get_running_loop().call_later(
                        SESSION_GRACE_SECONDS,
                        lambda: asyncio.create_task(expire(key, session)),
                    )
                with contextlib.suppress(Exception):
                    await socket.close(code=1000)
