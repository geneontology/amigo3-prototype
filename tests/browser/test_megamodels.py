import json

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app

CY = "document.getElementById('mega-canvas')._cy"


@pytest.fixture
def mega_url(megamodel_data, serve_app):
    with serve_app(create_app(megamodel_data, "duckdb")) as url:
        yield url


def ready(page):
    expect(page.locator("#mega-canvas[data-ready=true]")).to_be_visible()


def test_discover_inspect_layout_export_and_restore(page, mega_url):
    page.goto(mega_url + "/gocams/gomodel:mega-a")
    page.get_by_role("tab", name="Neighboring models", exact=True).click()
    ready(page)
    # Both viewers coexist; source-graph events must not reach the neighborhood graph.
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    page.locator("#graph-activity").select_option("gomodel:mega-a/a")
    page.locator("#graph-inspector .graph-edge-link").click()
    expect(page.locator("#graph-inspector")).to_contain_text("PMID:123")
    page.get_by_role("tab", name="Neighboring models", exact=True).click()
    neighbor = page.locator("#neighbor-models .mega-model").filter(has_text="Neighbor pathway")
    neighbor.get_by_role("button", name="Why linked?").click()
    expect(page.locator("#mega-inspector")).to_contain_text("Example anchors")
    expect(page.locator("#mega-inspector")).to_contain_text("CHEBI:123")
    neighbor.get_by_role("button", name="Add model").click()
    expect(page.locator("#mega-status")).to_contain_text("2 models · 4 activities")
    ready(page)
    page.locator("#view-models").click()
    assert page.evaluate(CY + ".nodes().length") == 2
    assert page.evaluate(CY + ".edges().length") == 1
    page.locator("#mega-connections > summary").click()
    page.locator("#connection-list [data-edge]").first.click()
    expect(page.locator("#mega-inspector")).to_contain_text("Why these models connect")
    expect(page.locator("#mega-inspector")).to_contain_text("Full activity context")
    page.locator("#view-activities").click()
    assert page.evaluate(CY + ".nodes('.activity').length") == 4
    assert page.evaluate(CY + ".nodes(':parent').length") == 2
    assert page.evaluate(CY + ".edges('.causal').length") == 2
    assert page.evaluate(CY + ".edges('.causal').every(e=>e.source().position('x')<e.target().position('x'))")
    assert page.evaluate("""(()=>{const cy=document.getElementById('mega-canvas')._cy;
      const [a,b]=cy.nodes(':parent').map(n=>n.boundingBox());
      return a.x2<b.x1 || b.x2<a.x1 || a.y2<b.y1 || b.y2<a.y1;})()""")
    page.locator("#mega-node").select_option("gomodel:mega-a|gomodel:mega-a/a")
    expect(page.locator("#mega-inspector")).to_contain_text("GENE1")
    expect(page.locator("#mega-inspector")).to_contain_text("PMID:123")
    with page.expect_download() as download:
        page.locator("#mega-download").click()
    snapshot = json.loads(download.value.path().read_text())
    assert snapshot["build_id"] and len(snapshot["activities"]) == 4
    assert snapshot["links"][0]["source_activity"]
    assert snapshot["models"][0]["source"]["sha256"]
    page.reload()
    ready(page)
    expect(page.locator("#view-activities")).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Remove Neighbor pathway").click()
    expect(page.locator("#mega-status")).to_contain_text("1 models")
    page.go_back()
    ready(page)
    expect(page.locator("#selected-count")).to_have_text("2 / 12")


def test_search_criteria_empty_state_and_snapshot_errors(page, mega_url):
    page.goto(mega_url + "/gocams/gomodel:mega-a#neighbors")
    ready(page)
    page.locator("#link-settings summary").first.click()
    for criterion in ["gene_mf", "full_activity_signature", "terminal_to_initial", "chemical_flow"]:
        page.locator(f'#link-criteria input[value="{criterion}"]').uncheck()
    expect(page.locator("#neighbor-models")).to_contain_text("No other models match")
    expect(page.locator("#chemical-options")).to_be_hidden()
    page.locator('#link-criteria input[value="chemical_flow"]').check()
    expect(page.locator("#chemical-options")).to_be_visible()
    expect(page.locator("#neighbor-models")).to_contain_text("Neighbor pathway")
    page.goto(mega_url + "/gocams/gomodel:mega-a?build=obsolete#neighbors")
    expect(page.locator("#mega-error")).to_contain_text("pinned build")
    expect(page.locator("#mega-download")).to_be_disabled()


def test_isolated_grid_is_compact_and_no_causal_links_are_invented(page, mega_url):
    def isolated(route):
        response = route.fetch()
        data = response.json()
        if route.request.method == "POST":
            base = data["activities"][0]
            data["activities"] = [
                {**base, "id": f"isolated:{i}", "activity_id": f"isolated:{i}"} for i in range(12)
            ]
            data["causal_edges"] = []
            data["links"] = []
        route.fulfill(response=response, json=data)

    page.route("**/api/megamodels", isolated)
    page.goto(mega_url + "/gocams/gomodel:mega-a#neighbors")
    ready(page)
    assert page.evaluate(CY + ".nodes('.activity').length") == 12
    assert page.evaluate(CY + ".edges().length") == 0
    assert page.evaluate(CY + ".nodes().boundingBox().h") < 800
    assert page.evaluate(CY + ".nodes('.activity').every(n=>Number.isFinite(n.position().x))")


def test_failed_request_clears_stale_graph(page, mega_url):
    page.goto(mega_url + "/gocams/gomodel:mega-a#neighbors")
    ready(page)
    page.route(
        "**/api/megamodels", lambda route: route.fulfill(status=503, json={"detail": "Test unavailable"})
    )
    page.locator("#link-settings summary").first.click()
    page.locator('#link-criteria input[value="shared_gene"]').check()
    expect(page.locator("#mega-error")).to_have_text("Test unavailable")
    assert page.evaluate(CY) is None
    expect(page.locator("#mega-download")).to_be_disabled()


@pytest.mark.parametrize("width", [1280, 390])
def test_neighbor_list_stays_visible_with_linking_options_open(page, mega_url, width):
    # Issue #41: expanding the linking options must not push the neighbor list out of view.
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(mega_url + "/gocams/gomodel:mega-a#neighbors")
    ready(page)
    neighbor = page.locator("#neighbor-models .mega-model").first
    expect(neighbor).to_contain_text("Neighbor pathway")
    expect(page.locator("#link-settings")).not_to_have_attribute("open", "")
    page.locator("#link-settings summary").first.click()
    expect(page.locator("#link-criteria")).to_be_visible()
    # Without scrolling the sidebar itself, the first neighbor is inside its visible box.
    assert page.evaluate(
        """() => {
          const side = document.querySelector('.model-neighborhood .mega-sidebar');
          side.scrollTop = 0;
          const s = side.getBoundingClientRect();
          const n = document.querySelector('#neighbor-models .mega-model').getBoundingClientRect();
          return n.top >= s.top && n.bottom <= s.bottom;
        }"""
    )
    neighbor.scroll_into_view_if_needed()
    expect(neighbor).to_be_in_viewport()
