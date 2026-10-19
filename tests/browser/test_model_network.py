import json
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app

CY = "document.getElementById('network-canvas')._cy"


@pytest.fixture
def network_url(megamodel_data, serve_app):
    with serve_app(create_app(megamodel_data, "duckdb")) as url:
        yield url


def ready(page):
    expect(page.locator("#network-canvas[data-ready=true]")).to_be_visible()


def test_every_model_by_default_and_connection_inspection(page, network_url):
    page.goto(network_url + "/megamodels")
    ready(page)
    expect(page.locator("#network-status")).to_contain_text("4 models")
    assert page.evaluate(CY + ".nodes().length") == 4
    assert page.evaluate(CY + ".getElementById('gomodel:worm-test').degree()") == 0
    page.locator("#network-node").select_option("gomodel:mega-a")
    expect(page.locator("#network-inspector")).to_contain_text("Seed pathway")
    page.locator("#network-inspector [data-edge]").filter(has_text="Neighbor pathway").click()
    expect(page.locator("#network-inspector")).to_contain_text("CHEBI:123")
    expect(page.locator("#network-inspector")).to_contain_text("Full activity context")
    with page.expect_download() as download:
        page.locator("#network-download").click()
    doc = json.loads(download.value.path().read_text())
    assert len(doc["models"]) == 4 and doc["edge_count"] == len(doc["edges"])
    page.locator("#network-layout").select_option("concentric")
    ready(page)
    assert page.evaluate(CY + ".nodes().every(n=>Number.isFinite(n.position().x))")
    page.locator("#network-labels").check()
    assert page.evaluate(CY + ".nodes('.labels').length") == 4
    page.locator("#network-deselect").click()
    assert page.evaluate(CY + ".elements('.dim').length") == 0
    page.locator("#network-node").select_option("gomodel:mega-a")
    page.get_by_role("link", name="Open model & explore neighbors").click()
    expect(page.locator("#mega-canvas[data-ready=true]")).to_be_visible()
    assert "/gocams/" in page.url and page.url.endswith("#neighbors")
    page.locator("#mega-node").select_option("gomodel:mega-a")
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    page.go_back()
    expect(page.locator("#mega-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#mega-node")).to_have_value("gomodel:mega-a")


def test_list_network_filter_round_trip_and_history(page, network_url):
    filters = [{"id": "taxon_ids", "field": "taxon_ids", "values": ["NCBITaxon:9606"]}]
    page.goto(network_url + "/?" + urlencode({"collection": "gocams", "filters": json.dumps(filters)}))
    expect(page.locator("#rows .row")).to_have_count(3)
    page.locator("#gocam-network").click()
    ready(page)
    assert page.evaluate(CY + ".nodes().length") == 3
    expect(page.locator("#network-facets input[data-value='\"NCBITaxon:9606\"']")).to_be_checked()
    page.locator("#network-query").fill("Neighbor")
    page.locator("#network-search button").click()
    expect(page.locator("#network-status")).to_contain_text("1 models")
    ready(page)
    assert page.evaluate(CY + ".edges().length") == 0
    page.locator("#network-list").click()
    expect(page.locator("#rows .row")).to_have_count(1)
    expect(page.locator("#search")).to_have_value("Neighbor")
    page.go_back()
    ready(page)
    expect(page.locator("#network-query")).to_have_value("Neighbor")
    page.locator("#network-clear").click()
    expect(page.locator("#network-status")).to_contain_text("4 models")
    ready(page)
    page.go_back()
    ready(page)
    expect(page.locator("#network-status")).to_contain_text("1 models")


def test_rules_change_edges_without_removing_models_and_empty_search(page, network_url):
    page.goto(network_url + "/megamodels?layout=circle")
    ready(page)
    for criterion in ["gene_mf", "full_activity_signature", "terminal_to_initial", "chemical_flow"]:
        page.locator(f'#network-criteria input[value="{criterion}"]').uncheck()
    expect(page.locator("#network-status")).to_contain_text("0 connections")
    ready(page)
    assert page.evaluate(CY + ".nodes().length") == 4
    assert page.evaluate(CY + ".edges().length") == 0
    page.locator('#network-criteria input[value="chemical_flow"]').check()
    expect(page.locator("#network-status")).to_contain_text("1 connections")
    ready(page)
    page.reload()
    ready(page)
    expect(page.locator('#network-criteria input[value="chemical_flow"]')).to_be_checked()
    expect(page.locator('#network-criteria input[value="gene_mf"]')).not_to_be_checked()
    page.locator("#network-query").fill("no such model")
    page.locator("#network-search button").click()
    expect(page.locator("#network-status")).to_contain_text("0 models")
    ready(page)
    assert page.evaluate(CY + ".nodes().length") == 0


def test_stale_build_and_failed_request_clear_network(page, network_url):
    page.goto(network_url + "/megamodels?build=old")
    expect(page.locator("#network-error")).to_contain_text("pinned build")
    page.goto(network_url + "/megamodels")
    ready(page)
    page.route(
        "**/api/megamodels/network",
        lambda route: route.fulfill(status=503, json={"detail": "Test unavailable"}),
    )
    page.locator("#network-query").fill("anything")
    page.locator("#network-search button").click()
    expect(page.locator("#network-error")).to_have_text("Test unavailable")
    assert page.evaluate(CY) is None
    expect(page.locator("#network-download")).to_be_disabled()
