"""Real browser cookies, redirects and chat gating, with GitHub's API mocked."""

import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect
from test_auth import AGENT, github

from amigo3.api import create_app
from amigo3.auth import AuthConfig


@pytest.fixture
def auth_server(built_data):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        app = create_app(
            built_data, "duckdb", chat_command=AGENT, auth_config=AuthConfig("id", "secret", base)
        )
        app.state.auth.transport = httpx.MockTransport(github)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                time.sleep(0.01)
            yield app, base
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def test_github_login_unlocks_chat_and_logout_closes_other_tabs(page, context, auth_server):
    app, base = auth_server

    def authorize(route):
        # Intercept the initial app request: Playwright does not route redirect hops.
        response = route.fetch(max_redirects=0)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        route.fulfill(
            status=302,
            headers={
                "Set-Cookie": response.headers["set-cookie"],
                "Location": base
                + "/auth/github/callback?"
                + urlencode(
                    {
                        "state": query["state"][0],
                        "code": "alice",
                    }
                ),
            },
        )

    context.route(base + "/auth/github?*", authorize)
    page.goto(base + "/?collection=annotations")
    expect(page.locator("#rows .chat-about").first).to_be_attached()
    expect(page.locator("#rows .chat-about").first).to_be_hidden()
    target = base + "/chat?keep=1#conversation"
    page.goto(target)
    expect(page.locator("#github-login")).to_be_visible()
    expect(page.locator("#chat-input")).to_be_disabled()
    assert not app.state.chat_sessions
    page.locator("#github-login a").click()
    expect(page).to_have_url(target)
    expect(page.locator(".account-controls")).to_contain_text("@alice")
    expect(page.locator("#github-login")).to_be_hidden()
    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
    expect(page.locator("#chat-input")).to_be_enabled()
    assert len(app.state.chat_sessions) == 1
    page.locator("#chat-input").fill("Private unsent question")
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(output / "authenticated-chat.png"))

    other = context.new_page()
    other.goto(base + "/?collection=annotations")
    expect(other.locator("#rows .chat-about").first).to_be_visible()
    other.get_by_role("button", name="Sign out", exact=True).click()
    expect(other.get_by_role("link", name="Sign in with GitHub")).to_be_visible()
    expect(other.locator("#rows .chat-about").first).to_be_hidden()
    expect(page.locator("#github-login")).to_be_visible()
    expect(page.locator("#chat-input")).to_be_disabled()
    expect(page.locator("#chat-input")).to_have_value("")
    assert not app.state.chat_sessions
    # Browser Back must re-check identity instead of restoring a live private UI.
    other.goto(base + "/chat")
    other.go_back()
    expect(other.get_by_role("link", name="Sign in with GitHub")).to_be_visible()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(output / "signed-out-chat.png"))
