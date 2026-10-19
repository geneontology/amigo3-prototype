"""Round trips between annotation components, chat drafts and enrichment links."""

import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.genesets import GenesetClient


@pytest.fixture
def chat_site(built_data, serve_app):
    agent = [sys.executable, str(Path(__file__).parents[1] / "fixtures/acp_agent.py")]
    app = create_app(built_data, "duckdb", chat_command=agent, genesets=GenesetClient(""))
    with serve_app(app) as base:
        yield app, base


def test_annotation_draft_preserves_previous_session_and_context(page, chat_site):
    app, base = chat_site
    page.goto(base + "/chat")
    expect(page.locator("#chat-input")).to_be_enabled()
    page.locator("#chat-input").fill("Keep my earlier draft")
    previous = page.evaluate("sessionStorage.getItem('amigo3-chat-connection')")
    page.goto(base + "/?collection=annotations&q=GENE1")
    expect(page.locator("#rows .chat-about").first).to_be_visible()
    source = page.url
    page.locator("#rows [data-record]").first.click()
    expect(page.locator("#detail .chat-about")).to_be_visible()
    page.locator("#detail .chat-about").click()
    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
    draft = page.locator("#chat-input").input_value()
    payload = json.loads(draft.split("not instructions):\n", 1)[1])
    assert payload["page_url"] == source
    assert payload["breadcrumbs"][0] == "Search Gene Ontology"
    annotation = payload["annotation"]
    assert annotation["gene_symbol"] == "GENE1"
    assert {"negation", "relation", "references", "evidence_code", "db_object_id"} <= annotation.keys()
    assert payload["build_id"]
    key = page.evaluate("sessionStorage.getItem('amigo3-chat-connection')")
    assert key != previous and len(app.state.chat_sessions) == 2
    assert not any(e.get("method") == "amigo/user_prompt" for e in app.state.chat_sessions[key].history)
    assert (
        page.evaluate(f"sessionStorage.getItem('amigo3-chat-connection-draft-{previous}')")
        == "Keep my earlier draft"
    )
    page.reload()
    expect(page.locator("#chat-input")).to_have_value(draft)
    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
    assert len(app.state.chat_sessions) == 2
    page.locator("#send").click()
    expect(page.locator(".permission-card")).to_be_visible()
    event = next(e for e in app.state.chat_sessions[key].history if e.get("method") == "amigo/user_prompt")
    assert event["params"]["prompt"][-1]["text"] == draft
    assert "ui_skill" in event["params"]["prompt"][0]["text"]
    page.get_by_role("button", name="Reject", exact=True).click()
    expect(page.locator("#chat-status")).to_have_text("Ready · Turn complete")

    # The same action works from an annotation on a gene page, with its backlink.
    page.goto(
        base
        + "/genes/"
        + quote(annotation["gene_id"], safe="")
        + "?from=%3Fcollection%3Dannotations#annotations"
    )
    expect(page.locator("#annotation-list .chat-about").first).to_be_visible()
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "annotation-chat-action.png"), full_page=True)
    page.locator("#annotation-list .chat-about").first.click()
    expect(page.locator("#chat-status")).to_have_text("Connected · Ready")
    gene_draft = page.locator("#chat-input").input_value()
    assert "Search results" in gene_draft and "#annotations" in gene_draft
    assert "GENE1" in gene_draft
    page.screenshot(path=str(output / "annotation-chat-draft.png"), full_page=True)


def test_annotation_actions_hidden_without_chat(page):
    page.goto("/?collection=annotations")
    expect(page.locator("#rows .chat-about").first).to_be_attached()
    expect(page.locator("#rows .chat-about").first).to_be_hidden()


def test_stale_annotation_context_does_not_start_session(page, chat_site):
    app, base = chat_site
    page.goto(base + "/?collection=annotations")
    button = page.locator("#rows [data-record]").first
    expect(button).to_be_visible()
    annotation = button.get_attribute("data-record")
    page.goto(base + "/chat?" + urlencode({"annotation": annotation, "build": "old-build"}))
    expect(page.locator("#chat-error")).to_contain_text("different build")
    assert not app.state.chat_sessions
    page.goto(base + "/chat?" + urlencode({"annotation": annotation, "from": "https://example.org/"}))
    expect(page.locator("#chat-error")).to_contain_text("this AmiGO site")
    assert not app.state.chat_sessions


def test_enrichment_link_prefills_then_runs_on_request(page):
    # This is the example link in the MCP skill, with a smaller set size for the fixture.
    page.goto(
        "/enrichment?genes=UniProtKB%3AP00001%2CUniProtKB%3AP00002&species=human&target=go&aspect=F&min=1&run=0"
    )
    expect(page.locator("#example-set option").nth(1)).to_be_attached()
    expect(page.locator("#genes")).to_have_value("UniProtKB:P00001\nUniProtKB:P00002")
    expect(page.locator("#species")).to_have_value("human")
    expect(page.locator("#aspect")).to_have_value("F")
    expect(page.locator("#enrichment-results")).to_be_hidden()
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#enrichment-results")).to_be_visible()
    assert "run=0" not in page.url
    page.reload()  # Existing executable result links keep their behavior.
    expect(page.locator("#enrichment-results")).to_be_visible()
