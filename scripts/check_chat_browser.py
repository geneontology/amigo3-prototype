"""Exercise chat with a fixture dataset and ACP agent, without provider credentials."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from amigo3.api import create_app

ROOT = Path(__file__).resolve().parents[1]


def fixture_data(path):
    sys.path.insert(0, str(ROOT / "tests"))
    spec = importlib.util.spec_from_file_location("amigo_test_fixtures", ROOT / "tests/conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Factory:
        def mktemp(self, name):
            result = path / name
            result.mkdir()
            return result

    return module.built_data.__wrapped__(Factory())


def main():
    output = ROOT / "data/browser-check"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amigo-chat-test-") as temp:
        data = fixture_data(Path(temp))
        app = create_app(
            data, "duckdb", chat_command=[sys.executable, str(ROOT / "tests/fixtures/acp_agent.py")]
        )
        with contextlib.closing(socket.socket()) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                assert server.started
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        channel=os.getenv("AMIGO3_BROWSER_CHANNEL", "chrome"), headless=True
                    )
                    page = browser.new_page(viewport={"width": 1440, "height": 1050})
                    page.clock.install()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(base + "/chat")
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    page.screenshot(path=str(output / "chat-ready.png"))
                    page.locator("#chat-input").fill("How many annotations are available?")
                    page.locator("#send").click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    expect(page.locator(".session-item.current .approval-badge")).to_have_text(
                        "Waiting for approval · 1"
                    )
                    expect(page.locator("#chat-status")).to_have_text("Waiting for approval")
                    page.screenshot(path=str(output / "chat-permission.png"))
                    page.get_by_role("button", name="Allow once", exact=True).click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")
                    expect(page.locator(".message-body strong")).to_have_text("4 annotations")
                    expect(page.locator(".tool-details")).to_have_attribute("data-status", "completed")
                    page.locator(".tool-details summary").click()
                    expect(page.locator(".tool-details pre")).to_contain_text("SELECT count(*)")
                    page.screenshot(path=str(output / "chat-answer.png"))
                    with page.expect_download() as download:
                        page.locator("#download-chat").click()
                    payload = json.loads(Path(download.value.path()).read_text())
                    assert payload["build_id"] and payload["transcript"]
                    # Reproduce the reported bug: follow an answer link, then use Back.
                    original_key = page.evaluate("sessionStorage.getItem('amigo3-chat-connection')")
                    original_session = app.state.chat_sessions[original_key]
                    original_pid = original_session.process.pid
                    page.locator("#chat-input").fill("My unsent follow-up")
                    page.get_by_role("link", name="kinase activity", exact=True).click()
                    expect(page).to_have_url(base + "/terms/GO:0016301")
                    page.go_back()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".message-body strong")).to_have_text("4 annotations")
                    expect(page.locator(".chat-message.user")).to_have_count(1)
                    expect(page.locator("#chat-input")).to_have_value("My unsent follow-up")
                    assert app.state.chat_sessions[original_key].process.pid == original_pid
                    page.reload()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".message-body strong")).to_have_text("4 annotations")
                    expect(page.locator(".permission-card")).to_have_count(0)
                    assert app.state.chat_sessions[original_key].process.pid == original_pid
                    # Follow-up, cancellation while awaiting approval, then a rejected tool call.
                    page.locator("#chat-input").fill("Compare the species.")
                    page.locator("#chat-input").press("Enter")
                    expect(page.locator(".permission-card")).to_be_visible()
                    # Navigate away while the agent is waiting for permission, then return.
                    page.get_by_role("link", name="Search", exact=True).click()
                    page.go_back()
                    expect(page.locator(".permission-card")).to_be_visible()
                    expect(page.locator("#chat-status")).to_have_text("Waiting for approval")
                    expect(page.locator(".chat-message.user")).to_have_count(2)
                    assert app.state.chat_sessions[original_key].process.pid == original_pid
                    page.locator("#stop").click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · cancelled")
                    expect(page.locator(".permission-card")).to_have_count(0)
                    page.locator("#chat-input").fill("Try again.")
                    page.locator("#send").click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    # A previous cancellation deadline must not kill this new turn.
                    page.clock.fast_forward(11000)
                    expect(page.locator(".permission-card")).to_be_visible()
                    page.get_by_role("button", name="Reject", exact=True).click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")
                    expect(page.locator(".chat-message.assistant").last).to_contain_text("Query declined")
                    # Markdown is sanitized; raw HTML and remote images never become live elements.
                    safe = httpx.post(
                        base + "/api/chat/render",
                        json={"text": "<script>bad()</script> ![x](https://evil/image)"},
                    ).json()
                    assert "<script>" not in safe["html"] and "<img" not in safe["html"]
                    page.locator("#chat-input").fill("Draft for the first conversation")
                    page.locator("#new-session").click()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".chat-message")).to_have_count(0)
                    assert not original_session.closed
                    assert original_key in app.state.chat_sessions
                    expect(page.locator(".session-item")).to_have_count(2)
                    second_key = page.evaluate("sessionStorage.getItem('amigo3-chat-connection')")
                    page.locator("#chat-input").fill("Draft for the second conversation")
                    page.get_by_role(
                        "button", name="Open session: How many annotations are available?", exact=True
                    ).click()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".chat-message.user")).to_have_count(3)
                    expect(page.locator("#chat-input")).to_have_value("Draft for the first conversation")
                    assert app.state.chat_sessions[original_key].process.pid == original_pid
                    page.get_by_role("button", name="Open session: New conversation", exact=True).click()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator("#chat-input")).to_have_value("Draft for the second conversation")
                    # Keep a pending turn in this session while working in the other.
                    page.locator("#chat-input").fill("Second conversation question")
                    page.locator("#send").click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    page.get_by_role(
                        "button", name="Open session: How many annotations are available?", exact=True
                    ).click()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".permission-card")).to_have_count(0)
                    page.get_by_role(
                        "button", name="Open session: Second conversation question", exact=True
                    ).click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    expect(page.locator("#chat-status")).to_have_text("Waiting for approval")
                    page.locator("#stop").click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · cancelled")
                    page.reload()
                    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(page.locator(".session-item")).to_have_count(2)
                    # Policies approve both waiting and future requests and remain revocable.
                    page.locator("#chat-input").fill("Test automatic approvals")
                    page.locator("#send").click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    page.get_by_role("button", name="Allow all in this session", exact=True).click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")
                    expect(page.locator(".permission-decision").last).to_contain_text("session policy")
                    expect(page.locator("#session-permissions")).to_have_value("allow")
                    page.locator("#chat-input").fill("Automatically approve another query")
                    page.locator("#send").click()
                    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")
                    expect(page.locator(".permission-card")).to_have_count(0)
                    page.locator("#session-permissions").select_option("ask")
                    expect(page.locator("#session-permissions")).to_be_enabled()
                    page.locator("#global-permissions").select_option("allow")
                    expect(page.locator("#global-permissions")).to_be_enabled()
                    page.locator("#chat-input").fill("Session asks even when global allows")
                    page.locator("#send").click()
                    expect(page.locator(".permission-card")).to_be_visible()
                    page.locator("#session-permissions").select_option("inherit")
                    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")
                    expect(page.locator(".permission-decision").last).to_contain_text("global policy")
                    page.locator("#global-permissions").select_option("ask")
                    expect(page.locator("#global-permissions")).to_be_enabled()
                    # Browse genuine files written by the fixture agent; HTML remains text.
                    page.locator("#refresh-files").click()
                    page.get_by_role("button", name="Preview file: notes.md", exact=True).click()
                    expect(page.locator("#preview-content")).to_contain_text(
                        "<script>notExecutable()</script>"
                    )
                    assert page.locator("#preview-content script").count() == 0
                    page.get_by_role("button", name="Open folder: results", exact=True).click()
                    page.get_by_role("button", name="Preview file: annotations.csv", exact=True).click()
                    expect(page.locator("#preview-content")).to_contain_text("annotations,4")
                    with page.expect_download() as artifact:
                        page.locator("#download-file").click()
                    assert Path(artifact.value.path()).read_text() == "collection,count\nannotations,4\n"
                    page.locator("#parent-folder").click()
                    expect(
                        page.get_by_role("button", name="Preview file: notes.md", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name="Preview file: notes.md", exact=True).click()
                    expect(page.locator("#preview-content")).to_contain_text("<script>")
                    page.screenshot(path=str(output / "chat-workspace.png"), full_page=True)
                    page.screenshot(path=str(output / "chat-sessions.png"))
                    page.on("dialog", lambda dialog: dialog.accept())
                    page.get_by_role(
                        "button", name="Delete session: How many annotations are available?", exact=True
                    ).click()
                    expect(page.locator(".session-item")).to_have_count(1)
                    assert original_key not in app.state.chat_sessions
                    assert original_session.closed
                    page.get_by_role(
                        "button", name="Delete session: Second conversation question", exact=True
                    ).click()
                    expect(page.locator(".session-item")).to_have_count(0)
                    expect(page.locator("#chat-status")).to_have_text(
                        "Session deleted · Choose a session or start a new one"
                    )
                    assert second_key not in app.state.chat_sessions
                    mobile = browser.new_page(viewport={"width": 390, "height": 844})
                    mobile.on("pageerror", lambda e: errors.append(str(e)))
                    mobile.goto(base + "/chat")
                    expect(mobile.locator("#chat-status")).to_have_text("Connected · Ready")
                    expect(mobile.locator(".session-item")).to_have_count(1)
                    expect(mobile.locator("#refresh-files")).to_be_enabled()
                    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    mobile.screenshot(path=str(output / "chat-mobile.png"), full_page=True)
                    assert not errors, errors
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
    print(
        "Chat browser checks passed: streaming, tools, session/global approvals, cancellation, reconnect, sessions, workspace preview/download, export, mobile."
    )


if __name__ == "__main__":
    main()
