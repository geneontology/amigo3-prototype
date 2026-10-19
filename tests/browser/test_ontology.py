"""Term ancestry, multiple inheritance, count filters and accessible navigation."""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app

CY = "document.getElementById('ontology-canvas')._cy"


@pytest.mark.parametrize("width", [1440, 390])
def test_ancestry_drilldown_counts_and_filter_links(page, serve_app, ontology_data, width):
    page.set_viewport_size({"width": width, "height": 1000})
    with serve_app(create_app(ontology_data, "duckdb")) as url:
        page.goto(url + "/terms/GO:0016301")
        expect(page.locator("#ontology-canvas[data-ready=true]")).to_be_visible()
        assert page.evaluate(CY + ".nodes().length") == 4
        assert page.evaluate(CY + ".edges().length") == 4
        assert page.evaluate(CY + ".edges().every(e=>e.source().position('y') > e.target().position('y'))")
        assert page.evaluate(CY + ".edges('.partof').every(e=>e.style('line-style')==='dashed')")
        expect(page.locator("#ontology-count-status")).to_contain_text("5 annotations")
        protein = page.locator('.ontology-child[data-term="GO:0004672"]')
        empty = page.locator('.ontology-child[data-term="GO:9000003"]')
        expect(protein.locator(".ontology-count > a").first).to_have_text("4")
        expect(empty.locator(".ontology-count > a").first).to_have_text("0")
        page.locator("#ontology-node").select_option("GO:9000002")
        assert page.evaluate(CY + ".elements('.faded').length") > 0
        expect(page.locator("#ontology-open")).to_have_attribute("href", "/terms/GO%3A9000002")
        page.get_by_role("button", name="Add protein kinase activity paths to graph").click()
        page.wait_for_function(CY + ".nodes().length === 5")
        assert page.evaluate(CY + ".edges().length") == 5
        expect(page.locator("#ontology-node")).to_have_value("GO:0004672")
        page.get_by_role("button", name="Enlarge", exact=True).click()
        expect(page.locator("#ontology-enlarge")).to_have_attribute("aria-expanded", "true")
        page.get_by_role("button", name="Compact view", exact=True).click()
        page.get_by_role("button", name="Reset graph", exact=True).click()
        assert page.evaluate(CY + ".nodes().length") == 4
        expect(page.locator("#ontology-node")).to_have_value("GO:0016301")
        page.get_by_role("button", name="Add protein kinase activity paths to graph").click()
        page.wait_for_function(CY + ".nodes().length === 5")
        page.locator("#ontology-species").select_option("human")
        expect(page.locator("#ontology-count-status")).to_contain_text("2 annotations")
        page.locator("#ontology-not").uncheck()
        expect(page.locator("#ontology-count-status")).to_contain_text("1 annotation")
        expect(protein.locator(".ontology-direct")).to_have_text("1 direct")
        page.locator("#ontology-evidence").select_option("IDA")
        page.get_by_role("checkbox", name="Select protein kinase activity branch").check()
        link = page.locator("#ontology-browse").get_attribute("href")
        filters = json.loads(parse_qs(urlparse(link).query)["filters"][0])
        assert filters == [
            {"id": "term", "field": "isa_partof_closure", "values": ["GO:0004672"]},
            {"id": "species", "field": "species", "values": ["human"]},
            {"id": "evidence", "field": "evidence_code", "values": ["IDA"]},
            {"id": "negation", "field": "negation", "values": [False]},
        ]
        page.locator("#ontology-hide-empty").check()
        expect(empty).to_have_count(0)
        page.get_by_role("tab", name="Annotations (").click()
        expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
        expect(page.locator("#term-evidence")).to_have_value("IDA")
        page.locator("#term-evidence").select_option("IEA")
        expect(page.locator("#term-annotation-status")).to_have_text("0 matching annotations")
        page.get_by_role("tab", name="Overview", exact=True).click()
        expect(page.locator("#ontology-evidence")).to_have_value("IEA")
        # Graph expansion and inspection survive tab changes.
        assert page.evaluate(CY + ".nodes().length") == 5
        page.locator("#ontology-evidence").select_option("IDA")
        page.reload()
        expect(page.locator("#ontology-count-status")).to_contain_text("1 annotation")
        expect(page.get_by_role("checkbox", name="Select protein kinase activity branch")).to_be_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f"/tmp/amigo-ontology-{width}.png", full_page=True)
        page.set_viewport_size({"width": 390 if width == 1440 else 1440, "height": 1000})
        page.wait_for_function(
            CY
            + ".nodes().every(n=>{const b=n.renderedBoundingBox();return b.x1>=0 && b.x2<="
            + CY
            + ".width() && b.y1>=0 && b.y2<="
            + CY
            + ".height();})"
        )


def test_lazy_diamond_navigation_synchronizes_selection(page, serve_app, ontology_data):
    with serve_app(create_app(ontology_data, "duckdb")) as url:
        page.goto(url + "/terms/GO:0003674")
        for label in ["branch one", "branch two"]:
            page.get_by_role("button", name="Expand " + label, exact=True).click()
        kinases = page.get_by_role("checkbox", name="Select kinase activity branch", exact=True)
        expect(kinases).to_have_count(2)
        kinases.first.check()
        for checkbox in kinases.all():
            expect(checkbox).to_be_checked()
        expect(kinases.first).to_be_focused()
        expect(
            page.locator('.ontology-child[data-term="GO:0016301"] .ontology-count > a:first-child')
        ).to_have_text(["5", "5"])
        page.get_by_role("button", name="Expand kinase activity", exact=True).first.click()
        expect(page.get_by_role("checkbox", name="Select protein kinase activity branch")).to_have_count(2)
        assert "branches=GO%3A0016301" in page.url
        page.reload()
        expect(kinases).to_have_count(2)
        for checkbox in kinases.all():
            expect(checkbox).to_be_checked()
        page.locator("#ontology-browse").click()
        expect(page.locator("#status strong")).to_have_text("5")


def test_graph_failure_keeps_text_and_counts(page):
    page.route("**/vendor/cytoscape.min.js", lambda route: route.abort())
    page.goto("/terms/GO:0004672")
    expect(page.locator("#ontology-status")).to_contain_text("Graph unavailable")
    page.get_by_text("Graph terms and relationships as text", exact=True).click()
    expect(page.locator("#ontology-text")).to_contain_text("protein kinase activity is a kinase activity")
    expect(page.locator("#ontology-count-status")).to_contain_text("32 annotations")


@pytest.mark.parametrize("topology", ["wide", "deep", "root"])
def test_large_graph_controls_and_node_geometry(page, topology):
    detail = page.request.get("/api/terms/GO:0016301").json()
    focus = detail["record"]["id"]
    nodes = [{"id": focus, "label": "kinase activity", "definition": ""}]
    edges = []
    if topology != "root":
        for i in range(18):
            identifier = f"TEST:{i}"
            nodes.append(
                {
                    "id": identifier,
                    "label": "An unusually long ancestor label with enough words to wrap across several lines",
                    "definition": "Synthetic layout test",
                }
            )
            edges.append(
                {
                    "subject": focus if topology == "wide" or i == 0 else f"TEST:{i - 1}",
                    "predicate": "part_of" if i % 2 else "is_a",
                    "object": identifier,
                }
            )
    detail["ontology"] = {"focus": focus, "nodes": nodes, "edges": edges, "children": []}
    page.route("**/api/terms/*", lambda route: route.fulfill(json=detail))
    page.goto("/terms/GO:0016301")
    expect(page.locator("#ontology-canvas[data-ready=true]")).to_be_visible()
    assert page.evaluate(CY + ".nodes().length") == len(nodes)
    assert page.evaluate(
        CY + ".nodes().every(n=>Number.isFinite(n.position('x')) && Number.isFinite(n.position('y')))"
    )
    assert page.evaluate(CY + ".nodes().every(n=>n.height() >= n.data('label').split('\\n').length*18+20)")
    # Layered boxes may not overlap, even when labels become tall.
    assert page.evaluate("""() => {
      const boxes = document.getElementById('ontology-canvas')._cy.nodes().map(n=>n.boundingBox());
      return boxes.every((a,i)=>boxes.slice(i+1).every(b=>a.x2<=b.x1 || b.x2<=a.x1 || a.y2<=b.y1 || b.y2<=a.y1));
    }""")
    zoom = page.evaluate(CY + ".zoom()")
    page.get_by_role("button", name="Zoom in", exact=True).click()
    assert page.evaluate(CY + ".zoom()") > zoom
    page.get_by_role("button", name="Fit graph", exact=True).click()
    assert page.evaluate(CY + ".elements().renderedBoundingBox().w <= " + CY + ".width()")
    page.get_by_role("tab", name="Ontology (").click()
    page.get_by_role("tab", name="Overview", exact=True).click()
    assert page.evaluate(CY + ".width()") > 0


def test_filter_history_and_lazy_graph_initialization(page):
    page.goto("/terms/GO:0016301#annotations")
    expect(page.get_by_role("tabpanel", name="Annotations (")).to_be_visible()
    expect(page.locator("#ontology-canvas[data-ready]")).to_have_count(0)
    page.locator("#term-species").select_option("human")
    page.get_by_role("tab", name="Overview", exact=True).click()
    expect(page.locator("#ontology-canvas[data-ready=true]")).to_be_visible()
    page.locator("#ontology-not").uncheck()
    expect(page.locator("#ontology-count-status")).to_contain_text("31 annotations")
    page.go_back()
    expect(page.get_by_role("tabpanel", name="Annotations (")).to_be_visible()
    expect(page.locator("#include-not")).to_be_checked()
    expect(page.locator("#term-annotation-status")).to_have_text("32 matching annotations")
    page.go_forward()
    expect(page.locator("#ontology-not")).not_to_be_checked()
    expect(page.locator("#ontology-count-status")).to_contain_text("31 annotations")
