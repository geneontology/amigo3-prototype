"""Preloaded comparison navigation, filtering, evidence and downloads."""

import csv
import io
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from test_predictions import prediction_data  # noqa: F401

from amigo3.api import create_app


@pytest.fixture
def base_url(prediction_data, serve_app):  # noqa: F811 -- imported pytest fixture
    with serve_app(create_app(prediction_data, "duckdb")) as url:
        yield url


def test_preloaded_comparison(page):
    page.goto("/predictions")
    expect(page.locator("#row-count")).to_have_text("1–15 of 15 source annotation rows")
    expect(page.locator("#rows tr")).to_have_count(15)
    expect(page.locator("#rows script")).to_have_count(0)
    expect(page.locator("#rows")).to_contain_text("<script>symbol</script>")
    page.locator('#prediction-facets input[data-field="status"][value="covered"]').check()
    expect(page.locator("#rows tr")).to_have_count(2)
    assert "status=covered" in page.url and "view=" in page.url
    page.locator("#rows details").filter(
        has=page.locator("summary", has_text="Baseline matches")
    ).first.click()
    expect(
        page.locator("#rows details").filter(has=page.locator("summary", has_text="Baseline matches")).first
    ).to_contain_text("protein kinase activity")
    with page.expect_download() as download:
        page.get_by_role("link", name="Download filtered TSV").click()
    exported = download.value.path().read_text()
    assert exported.count("\tcovered\t") == 2
    assert "raw_record" in exported and '"status": "covered"' in exported
    page.get_by_text("Sources, dates and mapping coverage", exact=True).click()
    expect(page.locator("#provenance")).to_contain_text("2026-01-01")
    with page.expect_download() as download:
        page.get_by_role("link", name="Download gaf", exact=True).click()
    assert download.value.path().read_text().startswith("!gaf-version: 2.2")
    with page.expect_download() as download:
        page.get_by_role("link", name="Download summary JSON", exact=True).click()
    assert '"input_rows":15' in download.value.path().read_text()
    page.reload()
    expect(page.locator("#rows tr")).to_have_count(2)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_stale_prediction_link(page):
    page.goto("/predictions?view=old")
    expect(page.get_by_role("alert")).to_contain_text("different build")
    expect(page.locator("#workspace")).to_be_hidden()


def test_multiple_facets_and_metric_drilldown(page):
    page.goto("/predictions")
    expect(page.locator("#rows tr")).to_have_count(15)
    page.locator('#metrics button[data-status="exact"]').click()
    expect(page.locator("#rows tr")).to_have_count(3)
    page.locator('#prediction-facets input[data-field="status"][value="covered"]').check()
    expect(page.locator("#rows tr")).to_have_count(5)
    page.locator('#prediction-facets input[data-field="model_type"][value="HMM"]').check()
    expect(page.locator("#rows tr")).to_have_count(5)
    assert "model_type=HMM" in page.url
    page.reload()
    expect(page.locator("#rows tr")).to_have_count(5)
    expect(page.locator('#prediction-facets input[value="exact"]')).to_be_checked()
    page.get_by_role("button", name="Clear filters", exact=True).click()
    expect(page.locator("#rows tr")).to_have_count(15)


def test_gene_predictions_tab(page):
    page.goto("/genes/HGNC:1#predictions")
    expect(page.get_by_role("tab", name="Predictions (10)", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.locator("#gene-prediction-rows tr")).to_have_count(10)
    page.locator("#gene-prediction-identifiers summary").click()
    expect(page.locator("#gene-prediction-identifiers")).to_contain_text("Different taxon")
    expect(
        page.locator('#gene-prediction-identifiers a[href="https://www.ncbi.nlm.nih.gov/gene/999"]')
    ).to_be_visible()
    page.locator('#gene-prediction-categories button[data-status="exact"]').click()
    expect(page.locator("#gene-prediction-rows tr")).to_have_count(3)
    page.get_by_role("link", name="Explore Test external set with facets →", exact=True).click()
    expect(page.locator("#rows tr")).to_have_count(3)
    expect(page.locator("#gene-scope")).to_contain_text("HGNC:1")
    page.get_by_role("button", name="Clear filters", exact=True).click()
    expect(page.locator("#rows tr")).to_have_count(15)
    expect(page.locator("#gene-scope")).to_be_hidden()
    assert not parse_qs(urlsplit(page.url).query).get("gene")
    with page.expect_download() as download:
        page.get_by_role("link", name="Download filtered TSV").click()
    lines = [line for line in download.value.path().read_text().splitlines() if not line.startswith("#")]
    assert len(list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))) == 15
    page.reload()
    expect(page.locator("#rows tr")).to_have_count(15)
    expect(page.locator("#gene-scope")).to_be_hidden()
