"""Isolated browser sessions, a reproducible app, and diagnostics on failure."""

import hashlib
import json
import os
import re
import shutil
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from amigo3.api import create_app
from amigo3.build import build
from amigo3.genesets import GenesetClient


@contextmanager
def serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "Test server failed to start"
                time.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)


@pytest.fixture
def serve_app():
    return serve


@pytest.fixture(scope="session", autouse=True)
def browser_enabled():
    if not os.getenv("AMIGO3_TEST_BROWSER"):
        pytest.skip("Set AMIGO3_TEST_BROWSER=1 and install Playwright Chromium")


@pytest.fixture(scope="session")
def browser_data(built_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("browser-data") / "data"
    shutil.copytree(built_data, data)
    # Extend a private copy: other tests retain their small, exact-count fixture.
    gaf = data / "human_gaf.txt"
    row = gaf.read_text().splitlines()[1].split("\t")
    with gaf.open("a") as stream:
        for i in range(30):
            row[7] = f"UniProtKB:SUPPORT{i}"
            stream.write("\t".join(row) + "\n")
    manifest = json.loads((data / "sources.json").read_text())
    manifest["human_gaf"]["sha256"] = hashlib.sha256(gaf.read_bytes()).hexdigest()
    (data / "sources.json").write_text(json.dumps(manifest))
    repository = data / "review_repo"
    review = repository / "genes/human/GENE1/GENE1-ai-review.yaml"
    doc = json.loads(review.read_text())
    doc["existing_annotations"][2]["review"].update(
        proposed_replacement_terms=[{"id": "GO:0004672", "label": "protein kinase activity"}],
        supported_by=[{"reference_id": "PMID:123", "supporting_text": "Synthetic kinase evidence."}],
    )
    review.write_text(json.dumps(doc))
    model = repository / "gocams/test/test-src.yaml"
    doc = json.loads(model.read_text())
    doc["activities"][0]["causal_associations"] = [
        {
            "predicate": "RO:0002630",
            "downstream_activity": "gomodel:test/b",
            "evidence": [{"reference": "PMID:123"}],
        }
    ]
    doc["activities"].append(
        {
            "id": "gomodel:test/b",
            "enabled_by": {"term": "UniProtKB:P00002"},
            "molecular_function": {"term": "GO:0016301"},
        }
    )
    doc["objects"].append({"id": "UniProtKB:P00002", "label": "GENE2"})
    model.write_text(json.dumps(doc))
    build(data, repository / "genes", repository)
    return data


@pytest.fixture(scope="session")
def app(browser_data):
    # Hermetic: no live MyGeneset calls; bundled example gene sets still load.
    return create_app(browser_data, "duckdb", genesets=GenesetClient(""))


@pytest.fixture(scope="session")
def base_url(app):
    with serve(app) as url:
        yield url


@pytest.fixture(scope="session")
def multispecies_url(multispecies_data):
    with serve(create_app(multispecies_data, "duckdb")) as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("AMIGO3_BROWSER_CHANNEL") or None)
        yield browser
        browser.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, "report_" + call.when, outcome.get_result())


@pytest.fixture
def context(browser, base_url, request):
    context = browser.new_context(base_url=base_url, viewport={"width": 1440, "height": 1050})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    errors, events = [], []

    def track(page):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: events.append(f"{message.type}: {message.text}"))
        page.on("requestfailed", lambda req: events.append(f"request failed: {req.url}: {req.failure}"))

    context.on("page", track)
    yield context
    failed = getattr(request.node, "report_call", None)
    failed = failed is None or failed.failed or bool(errors)
    try:
        if failed:
            name = re.sub(r"[^a-zA-Z0-9_.-]", "_", request.node.nodeid)
            output = Path(os.getenv("AMIGO3_BROWSER_ARTIFACTS", "data/browser-failures")) / name
            output.mkdir(parents=True, exist_ok=True)
            for i, page in enumerate(context.pages):
                if not page.is_closed():
                    events.append(f"page {i}: {page.url}")
                    try:
                        page.screenshot(path=str(output / f"page-{i}.png"), timeout=5000)
                        page.screenshot(path=str(output / f"page-{i}-full.png"), full_page=True, timeout=5000)
                    except Exception as error:
                        events.append(f"Screenshot failed: {error}")
            (output / "browser.log").write_text("\n".join([*events, *errors]))
            context.tracing.stop(path=str(output / "trace.zip"))
        else:
            context.tracing.stop()
    finally:
        context.close()
    assert not errors, "Browser JavaScript errors: " + "; ".join(errors)


@pytest.fixture
def page(context):
    return context.new_page()
