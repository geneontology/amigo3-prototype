"""Optional GitHub sign-in with bounded, process-local sessions (no database)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = "amigo3_session"
FLOW_COOKIE = "amigo3_oauth"
FLOW_TTL = 600
SESSION_TTL = 8 * 60 * 60
MAX_ENTRIES = 1000
ALLOWLIST_POLL_SECONDS = 5


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    origin: str = "http://127.0.0.1:8030"
    site_access: str = "public"
    allowlist_file: Path | None = None

    def __post_init__(self):
        if self.site_access not in {"public", "private"}:
            raise ValueError("AMIGO3_SITE_ACCESS must be public or private")
        if self.site_access == "private" and self.allowlist_file is None:
            raise ValueError("Private sites require AMIGO3_GITHUB_ALLOWLIST_FILE")
        if self.allowlist_file is not None:
            read_allowlist(self.allowlist_file)  # Refuse invalid initial configuration.
        url = urlsplit(self.origin)
        if (
            not self.client_id
            or not self.client_secret
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError(
                "GitHub auth requires credentials and an HTTPS origin (HTTP allowed on loopback)"
            )

    @classmethod
    def from_env(cls):
        names = ("AMIGO3_GITHUB_CLIENT_ID", "AMIGO3_GITHUB_CLIENT_SECRET", "AMIGO3_AUTH_ORIGIN")
        values = [os.environ.get(name, "") for name in names]
        access = os.environ.get("AMIGO3_SITE_ACCESS", "public")
        allowlist = os.environ.get("AMIGO3_GITHUB_ALLOWLIST_FILE", "")
        if not any(values):
            if access != "public" or allowlist:
                raise ValueError("Site access restrictions require GitHub login credentials")
            return None
        if not all(values):
            raise ValueError("Set all of " + ", ".join(names) + " to enable GitHub login")
        return cls(*values, site_access=access, allowlist_file=Path(allowlist) if allowlist else None)

    @property
    def callback(self):
        return self.origin + "/auth/github/callback"


@dataclass
class LoginSession:
    user: dict
    expires: float
    # Separate from the bearer cookie; safe to use to namespace browser drafts.
    owner: str = field(default_factory=lambda: secrets.token_hex(16))
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    timer: asyncio.TimerHandle | None = None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def read_allowlist(path):
    """Numeric GitHub IDs survive handle changes. Empty lists deny everyone."""
    value = json.loads(Path(path).read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"github_ids"}
        or not isinstance(value["github_ids"], list)
        or any(type(i) is not int or i <= 0 for i in value["github_ids"])
    ):
        raise ValueError('Allowlist must be an object containing "github_ids": [positive integers]')
    return set(value["github_ids"])


def return_path(value):
    # Only root-relative navigation; reject browser URL normalization tricks.
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        return "/"
    if value.split("?", 1)[0].startswith("/auth/"):
        return "/"
    return value


class Auth:
    def __init__(self, config):
        self.config = config
        self.flows = {}
        self.sessions = {}
        self.on_end = None
        self.transport = None  # Injectable HTTP transport for offline integration tests.

    def allowed(self, user):
        if not self.config or self.config.allowlist_file is None:
            return True
        try:
            return user["id"] in read_allowlist(self.config.allowlist_file)
        except (OSError, ValueError):
            # Missing or malformed updates must never open access.
            return False

    async def revoke_disallowed(self):
        for key, session in list(self.sessions.items()):
            if not self.allowed(session.user):
                await self.end(key)

    async def watch_allowlist(self):
        while True:
            await asyncio.sleep(ALLOWLIST_POLL_SECONDS)
            await self.revoke_disallowed()

    def session(self, connection):
        token = connection.cookies.get(SESSION_COOKIE, "")
        session = self.sessions.get(digest(token)) if token else None
        return (
            session
            if session
            and session.expires > time.monotonic()
            and not session.ended.is_set()
            and self.allowed(session.user)
            else None
        )

    def require(self, connection):
        session = self.session(connection)
        if self.config and session is None:
            raise HTTPException(401, "Sign in with GitHub to use chat")
        return session

    def owner(self, connection):
        session = self.require(connection)
        return session.owner if session else None

    async def end(self, key):
        session = self.sessions.pop(key, None)
        if session:
            session.ended.set()
            if session.timer:
                session.timer.cancel()
            if self.on_end:
                await self.on_end(session.owner)

    def check_origin(self, request, mutation=False):
        if not self.config:
            raise HTTPException(404, "GitHub login is not configured")
        if request.headers.get("host") != urlsplit(self.config.origin).netloc:
            raise HTTPException(400, "Use the configured login origin")
        if mutation and (
            request.headers.get("origin") != self.config.origin
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, "A same-origin request is required")

    def cookie(self, response, name, value, ttl, path="/"):
        response.set_cookie(
            name,
            value,
            max_age=ttl,
            httponly=True,
            secure=self.config.origin.startswith("https:"),
            samesite="lax",
            path=path,
        )


def attach_auth(app, config):
    auth = Auth(config)
    app.state.auth = auth

    @app.middleware("http")
    async def private_responses(request, call_next):
        await auth.revoke_disallowed()
        private = bool(config and config.site_access == "private")
        path = request.url.path
        public_route = path in {"/auth/github", "/auth/github/callback", "/auth/logout", "/api/me"}
        if private and not public_route and auth.session(request) is None:
            if request.method in {"GET", "HEAD"} and "text/html" in request.headers.get("accept", ""):
                target = return_path(path + ("?" + request.url.query if request.url.query else ""))
                response = RedirectResponse("/auth/github?" + urlencode({"return_to": target}), 303)
            else:
                response = JSONResponse(
                    {"detail": "Sign in with an approved GitHub account"}, status_code=401
                )
        else:
            response = await call_next(request)
        if private or path.startswith(("/auth/", "/api/chat/")) or path == "/api/me":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/me")
    async def me(request: Request):
        session = auth.session(request)
        return {
            "login_enabled": bool(config),
            "user": session.user if session else None,
            "session_key": session.owner if session else None,
        }

    @app.get("/auth/github")
    async def login(request: Request, return_to: str = "/"):
        auth.check_origin(request)
        now = time.monotonic()
        auth.flows = {k: v for k, v in auth.flows.items() if v["expires"] > now}
        if len(auth.flows) >= MAX_ENTRIES:
            raise HTTPException(503, "Too many pending logins; try again later")
        state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        # Starting another flow in this browser invalidates its previous attempt.
        old_binding = request.cookies.get(FLOW_COOKIE)
        if old_binding:
            auth.flows = {k: v for k, v in auth.flows.items() if v["binding"] != digest(old_binding)}
        auth.flows[state] = {
            "binding": digest(binding),
            "verifier": verifier,
            "return_to": return_path(return_to),
            "expires": now + FLOW_TTL,
        }
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        response = RedirectResponse(
            "https://github.com/login/oauth/authorize?"
            + urlencode(
                {
                    "client_id": config.client_id,
                    "redirect_uri": config.callback,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            ),
            status_code=302,
        )
        auth.cookie(response, FLOW_COOKIE, binding, FLOW_TTL, "/auth")
        return response

    @app.get("/auth/github/callback")
    async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        auth.check_origin(request)
        flow = auth.flows.get(state)
        if (
            not flow
            or flow["expires"] <= time.monotonic()
            or not secrets.compare_digest(flow["binding"], digest(request.cookies.get(FLOW_COOKIE, "")))
        ):
            raise HTTPException(400, "Login expired or invalid. Start sign-in again.")
        del auth.flows[state]  # Consume once, including cancellation and exchange failures.
        if error or not code:
            # Do not redirect a private site back into an automatic login loop.
            response = (
                JSONResponse({"detail": "GitHub sign-in cancelled"}, status_code=401)
                if config.site_access == "private"
                else RedirectResponse("/?login_error=cancelled", status_code=303)
            )
        else:
            try:
                async with httpx.AsyncClient(timeout=10, transport=auth.transport) as client:
                    token_response = await client.post(
                        "https://github.com/login/oauth/access_token",
                        headers={"Accept": "application/json"},
                        data={
                            "client_id": config.client_id,
                            "client_secret": config.client_secret,
                            "code": code,
                            "redirect_uri": config.callback,
                            "code_verifier": flow["verifier"],
                        },
                    )
                    token_response.raise_for_status()
                    token = token_response.json().get("access_token")
                    if not isinstance(token, str) or not token:
                        raise ValueError("Token exchange failed")
                    profile_response = await client.get(
                        "https://api.github.com/user",
                        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
                    )
                    profile_response.raise_for_status()
                    profile = profile_response.json()
                    if type(profile.get("id")) is not int or not isinstance(profile.get("login"), str):
                        raise ValueError("Invalid GitHub identity")
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                response = JSONResponse(
                    {"detail": "GitHub sign-in failed. Please start sign-in again."}, status_code=502
                )
            else:
                # Tokens are used only for identification, never persisted or sent to the browser.
                old = request.cookies.get(SESSION_COOKIE)
                if old:
                    await auth.end(digest(old))
                if not auth.allowed(profile):
                    response = JSONResponse(
                        {"detail": "This GitHub account is not approved"}, status_code=403
                    )
                    response.delete_cookie(SESSION_COOKIE)
                    response.delete_cookie(FLOW_COOKIE, path="/auth")
                    return response
                if len(auth.sessions) >= MAX_ENTRIES:
                    raise HTTPException(503, "Too many active logins; try again later")
                token = secrets.token_urlsafe(32)
                key = digest(token)
                session = LoginSession(
                    {"id": profile["id"], "login": profile["login"]}, time.monotonic() + SESSION_TTL
                )
                auth.sessions[key] = session
                session.timer = asyncio.get_running_loop().call_later(
                    SESSION_TTL,
                    lambda: asyncio.create_task(auth.end(key)),
                )
                response = RedirectResponse(flow["return_to"], status_code=303)
                auth.cookie(response, SESSION_COOKIE, token, SESSION_TTL)
        response.delete_cookie(FLOW_COOKIE, path="/auth")
        return response

    @app.post("/auth/logout")
    async def logout(request: Request):
        auth.check_origin(request, mutation=True)
        await auth.end(digest(request.cookies.get(SESSION_COOKIE, "")))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with previous_lifespan(app) as state:
            watcher = (
                asyncio.create_task(auth.watch_allowlist()) if config and config.allowlist_file else None
            )
            try:
                yield state
            finally:
                if watcher:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher
                for key in list(auth.sessions):
                    await auth.end(key)
                auth.flows.clear()

    app.router.lifespan_context = lifespan
    return auth
