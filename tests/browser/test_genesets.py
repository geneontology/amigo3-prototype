"""Example and searched gene sets feed the enrichment form, with provenance."""

import re

import httpx
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.genesets import GenesetClient

LIVE = {
    "_id": "WP_LIVE",
    "name": "Live pathway",
    "source": "wikipathways",
    "taxid": 9606,
    "count": 3,
    "genes": [{"symbol": "STALE", "ncbigene": "1"}, {"symbol": "GENE2"}, {"symbol": "MISSING"}],
    # Only bundled examples carry curated expectations; injected here to test highlighting.
    "expected_go": [{"id": "GO:0004672", "label": "protein kinase activity", "category": "core_function"}],
}


def handler(request):
    if request.url.path == "/v1/query":
        hit = {key: LIVE[key] for key in ("_id", "name", "source", "taxid", "count")}
        return httpx.Response(200, json={"total": 1, "hits": [hit]})
    if request.url.path == "/v1/geneset/WP_LIVE":
        return httpx.Response(200, json=LIVE)
    return httpx.Response(404)


def test_examples_and_search_fill_the_study_list(page, browser_data, serve_app):
    client = GenesetClient("https://mygeneset.test/v1", transport=httpx.MockTransport(handler))
    with serve_app(create_app(browser_data, "duckdb", genesets=client)) as base:
        page.goto(base + "/enrichment")
        example = page.locator("#example-set option").nth(1)
        expect(example).to_contain_text("MSigDB")
        page.locator("#example-set").select_option(index=1)
        loaded = page.locator("#geneset-loaded")
        expect(loaded).to_contain_text("bundled copy retrieved from MyGeneset")
        expect(loaded.locator(".expected-go summary")).to_contain_text("Curated interpretation expects")
        expect(page.locator("#genes")).not_to_have_value("")
        expect(page).to_have_url(re.compile(r"geneset="))

        expect(page.locator("#geneset-collection")).to_have_value("signatures")
        page.locator("#geneset-query").fill("pathway")
        page.locator("#geneset-query").press("Enter")  # Searches; must not submit the enrichment form.
        expect(page.locator("#enrichment-results")).to_be_hidden()
        page.get_by_role("button", name=re.compile("Live pathway")).click()
        expect(page.locator("#genes")).to_have_value("GENE1\nGENE2\nMISSING")
        expect(loaded).to_contain_text("2 of 3 members mapped")
        expect(loaded.get_by_role("link", name=re.compile("WikiPathways WP_LIVE"))).to_be_visible()
        loaded.locator("summary", has_text="unmapped").click()
        expect(loaded).to_contain_text("MISSING")

        page.locator("#aspect").select_option("F")
        page.locator("#min-size").fill("1")
        page.locator("#enrichment-form button[type=submit]").click()
        expect(page.locator("#mapping-body .mapping-group", has_text="Not found")).to_contain_text("MISSING")
        page.locator("#show-all").check()
        expected = page.locator("#term-table tr.expected-row")
        expect(expected).to_have_count(1)
        expect(expected).to_contain_text("curated expectation")
        expect(page.locator("#term-note")).to_contain_text("of 1 curated expected terms")
        expect(page).to_have_url(re.compile(r"geneset=WP_LIVE"))

        page.reload()  # A shared link restores the provenance note without replacing the list.
        expect(loaded).to_contain_text("Live pathway")
        expect(page.locator("#genes")).to_have_value("GENE1\nGENE2\nMISSING")
        page.locator("#genes").fill("GENE1")
        expect(loaded).to_be_hidden()


def test_search_is_hidden_when_disabled(page):
    page.goto("/enrichment")
    expect(page.locator("#example-set option").nth(1)).to_be_attached()
    expect(page.locator("#geneset-search-box")).to_be_hidden()
