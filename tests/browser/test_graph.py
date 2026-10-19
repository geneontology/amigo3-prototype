"""GO-CAM layout, inspection, export, topology and graceful degradation."""

import pytest
from playwright.sync_api import expect

CY = "document.getElementById('pathway-canvas')._cy"


def ready(page):
    page.goto("/gocams/gomodel:test#graph")
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()


def test_graph_layout_inspection_export_and_back_navigation(page):
    ready(page)
    assert page.evaluate(CY + ".nodes().length") == 2
    assert page.evaluate(CY + ".edges().length") == 1
    assert page.evaluate(CY + ".edges('.negative').every(e=>e.style('target-arrow-shape')==='tee')")
    assert page.evaluate(CY + ".edges().every(e=>e.source().position('x') < e.target().position('x'))")
    page.locator("#graph-activity").select_option("gomodel:test/a")
    # Selection recenters over 200 ms; finish it before testing later zoom controls.
    page.wait_for_function(CY + ".animated() === false")
    expect(page.locator("#graph-inspector h3")).to_contain_text("GENE1")
    expect(page.locator("#graph-inspector h3 a")).to_have_attribute("href", "/genes/HGNC%3A1")
    page.locator("#graph-inspector .graph-edge-link").click()
    expect(page.locator("#graph-inspector")).to_contain_text("negatively regulates")
    expect(page.locator("#graph-inspector")).to_contain_text("PMID:123")
    page.locator("#graph-clear").click()
    assert page.evaluate(CY + ".elements('.faded').length") == 0
    page.locator("#graph-direction").select_option("TB")
    assert page.evaluate(CY + ".edges().every(e=>e.source().position('y') < e.target().position('y'))")
    zoom = page.evaluate(CY + ".zoom()")
    page.locator("#graph-zoom-in").click()
    assert page.evaluate(CY + ".zoom()") > zoom
    page.locator("#graph-fit").click()
    with page.expect_download() as download:
        page.locator("#graph-export").click()
    assert download.value.path().read_bytes().startswith(b"\x89PNG")
    page.goto("/?collection=gocams")
    expect(page.locator("#rows .row")).to_have_count(1)
    page.go_back()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    assert not page.evaluate(CY + ".destroyed()")


@pytest.mark.parametrize("topology", ["complex", "isolated", "large"])
def test_graph_topologies(page, topology):
    data = page.request.get("/api/resources/gocams/gomodel:test").json()
    count = 132 if topology == "large" else 4
    activities = [
        {
            "id": f"gomodel:test/{i}",
            "enabled_by": {"term": f"TEST:{i}"},
            "molecular_function": {"term": "GO:0004672"},
        }
        for i in range(count)
    ]
    if topology == "large":
        for i in range(count - 1):
            activities[i]["causal_associations"] = [
                {"predicate": "RO:0002629", "downstream_activity": f"gomodel:test/{i + 1}"}
            ]
    if topology == "complex":
        activities[0]["causal_associations"] = [
            {"predicate": "RO:0002629", "downstream_activity": "gomodel:test/1"},
            {"predicate": "RO:0002630", "downstream_activity": "gomodel:test/1"},
            {"predicate": "RO:unknown", "downstream_activity": "gomodel:test/0"},
            {"predicate": "RO:0002629", "downstream_activity": "gomodel:test/missing"},
        ]
        activities[1]["causal_associations"] = [
            {"predicate": "RO:0002629", "downstream_activity": "gomodel:test/0"}
        ]
        activities[2]["enabled_by"]["term"] = "<script>window.graphXSS=true</script>"
    data["documents"]["model"]["activities"] = activities
    data["documents"]["model"]["molecules"] = []
    page.route("**/api/resources/gocams/*", lambda route: route.fulfill(json=data))
    ready(page)
    assert page.evaluate(CY + ".nodes().length") == count
    edges = {"complex": 4, "isolated": 0, "large": 131}[topology]
    assert page.evaluate(CY + ".edges().length") == edges
    assert page.evaluate(
        CY + ".nodes().every(n=>Number.isFinite(n.position().x) && Number.isFinite(n.position().y))"
    )
    assert page.evaluate(
        CY
        + ".edges().every(e=>Number.isFinite(e.targetEndpoint().x) && Number.isFinite(e.targetEndpoint().y))"
    )
    if topology == "complex":
        expect(page.locator("#graph-status")).to_contain_text("1 connections refer")
        assert page.evaluate(CY + ".nodes('.isolated').length") == 2
        assert not page.evaluate("Boolean(window.graphXSS)")
    if topology == "isolated":
        assert page.evaluate(CY + ".nodes('.isolated').length") == 4


def test_graph_dependency_failure_retains_source_tables(page):
    page.route("**/vendor/cytoscape.min.js", lambda route: route.abort())
    page.goto("/gocams/gomodel:test#graph")
    expect(page.locator("#pathway-canvas[data-error=true]")).to_be_visible()
    expect(page.locator("#graph-status")).to_contain_text("tables remain available")
    expect(page.locator("#activities .activity-card")).to_have_count(2)
    page.get_by_role("tab", name="Activities (").click()
    expect(page.locator("#activities")).to_be_visible()
    page.get_by_role("tab", name="Sources (").click()
    expect(page.locator("#sources a").first).to_be_visible()
