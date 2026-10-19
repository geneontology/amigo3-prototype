"""GO-CAM layout, semantic edge styling, evidence, export and failure fallback."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("AMIGO3_BROWSER_URL", "http://127.0.0.1:8030")
MODEL = "gomodel:568b0f9600000284"
CY = "document.getElementById('pathway-canvas')._cy"


def main():
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1150))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "warning" else None)
        page.goto(BASE + "/gocams/" + MODEL + "#graph", wait_until="networkidle")
        page.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert page.evaluate(CY + ".nodes().length") == 10
        assert page.evaluate(CY + ".edges().length") == 9
        assert page.evaluate(CY + ".edges('.negative').length") == 3
        assert page.evaluate(CY + ".edges('.negative').every(e=>e.style('target-arrow-shape')==='tee')")
        assert "10 activities have no" not in page.locator("#graph-status").inner_text()
        # Every edge in this acyclic model flows to a later column, independent of source order.
        assert page.evaluate(CY + ".edges().every(e=>e.source().position('x') < e.target().position('x'))")
        page.locator("#graph").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "gocam-layout.png"))
        node = page.evaluate(CY + ".nodes().filter(n=>n.data('name')==='pmk-1 Cele').first().id()")
        page.locator("#graph-activity").select_option(node)
        assert page.locator("#graph-inspector h3").inner_text() == "pmk-1 Cele"
        assert page.locator("#graph-inspector .graph-edge-link").count() == 4
        assert page.evaluate(CY + ".elements('.faded').length") > 0
        page.locator("#graph-inspector .graph-edge-link").filter(has_text="vhp-1").click()
        assert "directly negatively regulates" in page.locator("#graph-inspector").inner_text()
        assert "PMID:" in page.locator("#graph-inspector").inner_text()
        page.locator("#graph-clear").click()
        assert page.evaluate(CY + ".elements('.faded').length") == 0
        page.locator("#graph-direction").select_option("TB")
        assert page.evaluate(CY + ".edges().every(e=>e.source().position('y') < e.target().position('y'))")
        page.locator("#graph-direction").select_option("LR")
        z = page.evaluate(CY + ".zoom()")
        page.locator("#graph-zoom-in").click()
        assert page.evaluate(CY + ".zoom()") > z
        page.locator("#graph-fit").click()
        with page.expect_download() as download:
            page.locator("#graph-export").click()
        export = download.value
        export.save_as(str(output / "gocam-export.png"))
        assert Path(export.path()).read_bytes().startswith(b"\x89PNG")
        assert Path(export.path()).stat().st_size > 10000
        # Largest loaded model: no former 80-activity cut-off.
        page.goto(BASE + "/gocams/gomodel:MGI_MGI_109482", wait_until="networkidle")
        page.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert page.evaluate(CY + ".nodes().length") == 132
        mobile = browser.new_page(viewport=dict(width=390, height=844))
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        mobile.goto(BASE + "/gocams/" + MODEL + "#graph", wait_until="networkidle")
        mobile.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert mobile.locator("#graph-direction").input_value() == "TB"
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.locator("#graph-activity").select_option(node)
        mobile.locator("#graph-inspector").scroll_into_view_if_needed()
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "gocam-graph-mobile.png"))
        # Synthetic topology: parallel edges, feedback, self-loop, isolated node, missing target.
        data = page.request.get(BASE + "/api/resources/gocams/" + MODEL).json()
        activities = []
        for i in range(4):
            activities.append(
                dict(
                    id=f"gomodel:test/{i}",
                    enabled_by=dict(term=f"TEST:{i}"),
                    molecular_function=dict(term="GO:0004674"),
                )
            )
        activities[0]["causal_associations"] = [
            dict(predicate="RO:0002629", downstream_activity="gomodel:test/1"),
            dict(predicate="RO:0002630", downstream_activity="gomodel:test/1"),
            dict(predicate="RO:unknown", downstream_activity="gomodel:test/0"),
            dict(predicate="RO:0002629", downstream_activity="gomodel:test/missing"),
        ]
        activities[1]["causal_associations"] = [
            dict(predicate="RO:0002629", downstream_activity="gomodel:test/0")
        ]
        activities[2]["enabled_by"]["term"] = "<script>bad()</script>"
        data["documents"]["model"]["activities"] = activities
        data["documents"]["model"]["molecules"] = []
        page.route("**/api/resources/gocams/*", lambda route: route.fulfill(json=data))
        page.goto(BASE + "/gocams/gomodel:test", wait_until="networkidle")
        page.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert page.evaluate(CY + ".nodes().length") == 4
        assert page.evaluate(CY + ".edges().length") == 4
        assert page.evaluate(CY + ".nodes('.isolated').length") == 2
        assert "2 activities have no" in page.locator("#graph-status").inner_text()
        assert "1 connections refer" in page.locator("#graph-status").inner_text()
        assert page.evaluate(
            CY + ".nodes().every(n=>Number.isFinite(n.position().x) && Number.isFinite(n.position().y))"
        )
        assert page.locator("script").count() == 4  # graph, resource, two vendored libraries only
        assert page.evaluate(
            CY
            + ".edges().every(e=>Number.isFinite(e.targetEndpoint().x) && Number.isFinite(e.targetEndpoint().y))"
        )
        for a in activities:
            a.pop("causal_associations", None)
        page.reload(wait_until="networkidle")
        page.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert page.evaluate(CY + ".edges().length") == 0
        assert page.evaluate(CY + ".nodes('.isolated').length") == 4
        page.goto(BASE + "/?collection=gocams", wait_until="networkidle")
        page.go_back(wait_until="networkidle")
        page.locator("#pathway-canvas[data-ready=true]").wait_for()
        assert not page.evaluate(CY + ".destroyed()")
        # Dependency failure must leave source tables readable and report a useful error.
        failed = browser.new_page()
        failed.route("**/vendor/cytoscape.min.js", lambda route: route.abort())
        failed.goto(BASE + "/gocams/" + MODEL, wait_until="networkidle")
        failed.locator("#pathway-canvas[data-error=true]").wait_for()
        assert failed.locator("#activities .activity-card").count() == 10
        assert "tables remain available" in failed.locator("#graph-status").inner_text()
        assert not errors, errors
        browser.close()
    print("GO-CAM layout, interactions, export, large model, topology, fallback and mobile checks passed")


if __name__ == "__main__":
    main()
