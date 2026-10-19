"""A dashboard is another view of the same query, not a summary of one page."""

import json
import re

import pytest
from playwright.sync_api import expect


def test_dashboard_drilldown_list_history_and_reload(page):
    page.goto("/?collection=annotations")
    expect(page.locator("#rows .row")).to_have_count(25)
    expect(page.get_by_role("button", name="List", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#dashboard")).to_be_hidden()
    page.get_by_role("button", name="Dashboard", exact=True).click()
    expect(page.locator("#dashboard-total")).to_have_text("34")
    expect(page.locator("#rows")).to_be_hidden()
    expect(page.locator(".pagination")).to_be_hidden()
    expect(page.locator(".stack-row")).to_have_count(2)
    expect(page.locator(".stack-total").first).to_have_text("33")
    page.locator("#dashboard-chart").select_option("heatmap")
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    page.locator("#dashboard-scale").select_option("percent")
    expect(page.locator(".dashboard-heatmap tbody tr").first.locator("td").first).to_have_text("100%")
    # A cell adds both dimensions in a single interaction.
    page.locator(".dashboard-heatmap td button").filter(has_text="100%").first.click()
    expect(page.locator("#dashboard-total")).to_have_text("33")
    expect(page.locator("#chips button")).to_have_count(2)
    # The evidence selection also constrains the sidebar species domain.
    expect(page.locator('[data-facet="species"]')).to_have_count(1)
    page.get_by_role("button", name="List", exact=True).click()
    expect(page.locator("#rows .row")).to_have_count(25)
    expect(page.locator("#dashboard")).to_be_hidden()
    expect(page.locator("#chips button")).to_have_count(2)
    page.go_back()
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    expect(page.locator("#dashboard-total")).to_have_text("33")
    page.reload()
    expect(page.locator("#dashboard-scale")).to_have_value("percent")
    expect(page.locator("#dashboard-total")).to_have_text("33")
    page.locator("#chips button").filter(has_text="Evidence").click()
    expect(page.locator("#chips button")).to_have_count(1)
    expect(page.locator('[data-facet="species"]')).to_have_count(2)
    expect(page.locator(".dashboard-heatmap tbody tr")).to_have_count(1)
    page.locator("#clear").click()
    expect(page.locator("#dashboard-total")).to_have_text("34")


def test_dashboard_controls_empty_results_and_mobile(page):
    page.goto("/?collection=annotations&view=dashboard")
    expect(page.locator("#dashboard-total")).to_have_text("34")
    page.locator("#dashboard-group").select_option("evidence_code")
    expect(page.locator("#dashboard-split")).to_have_value("")
    page.locator("#dashboard-split").select_option("species")
    page.locator("#dashboard-limit").select_option("5")
    page.locator("#dashboard-chart").select_option("heatmap")
    expect(page.locator(".comparison-card h3")).to_have_text("Evidence code × Species")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#search").fill("no-such-annotation")
    page.locator("#search-form").evaluate("(form) => form.requestSubmit()")
    expect(page.locator("#dashboard-total")).to_have_text("0")
    expect(page.locator("#dashboard-charts")).to_contain_text("No matching annotations")
    expect(page.locator(".dashboard-heatmap")).to_have_count(0)
    page.locator("#clear").click()
    expect(page.locator("#dashboard-total")).to_have_text("34")
    page.locator('[data-collection="genes"]').click()
    expect(page.locator("#dashboard")).to_be_visible()
    expect(page.locator("#result-views")).to_be_visible()
    expect(page.locator("#dashboard-total-label")).to_have_text("matching genes & products")
    expect(page.locator("#dashboard-split")).to_have_value("species")
    page.locator('[data-collection="annotations"]').click()
    expect(page.locator(".dashboard-heatmap")).to_be_visible()


def test_invalid_dashboard_url_uses_safe_defaults(page):
    page.goto("/?collection=annotations&view=dashboard&group=toString&split=species&chart=bogus&top=1000")
    expect(page.locator("#dashboard-total")).to_have_text("34")
    expect(page.locator("#dashboard-group")).to_have_value("species")
    expect(page.locator("#dashboard-split")).to_have_value("")
    expect(page.locator("#dashboard-limit")).to_have_value("10")
    expect(page.locator("#error")).to_be_hidden()


def test_dashboard_single_dimension_percent_and_failed_refresh(page):
    page.goto("/?collection=annotations&view=dashboard&split=&scale=percent")
    expect(page.locator("#dashboard-total")).to_have_text("34")
    expect(page.locator(".comparison-card .chart-heading")).to_contain_text("Percent of selection")
    expect(page.locator(".comparison-card .bar-value").first).to_contain_text("97.1%")
    page.route(
        "**/api/query",
        lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"detail":"Temporarily unavailable"}'
        ),
    )
    page.locator("#dashboard-limit").select_option("5")
    expect(page.locator("#error")).to_have_text("Temporarily unavailable")
    expect(page.locator("#dashboard-total")).to_have_text("—")
    expect(page.locator(".comparison-card")).to_have_count(0)
    expect(page.locator("#dashboard-charts")).to_have_attribute("aria-busy", "false")
    page.unroute("**/api/query")
    page.locator("#clear").click()
    expect(page.locator("#dashboard-total")).to_have_text("34")


@pytest.mark.parametrize(
    "collection",
    ["genes", "annotations", "terms", "gocams", "modules", "families", "reports", "projects", "rules"],
)
def test_dashboard_each_collection_drilldown_and_shared_settings(page, collection):
    page.goto(f"/?collection={collection}&view=dashboard")
    expect(page.locator(".comparison-card")).to_be_visible()
    expect(page.locator("#error")).to_be_hidden()
    expect(page.locator("#sort-controls")).to_be_hidden()
    page.locator("#dashboard-chart").select_option("heatmap")
    page.locator("#dashboard-scale").select_option("percent")
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    cell = page.locator(".dashboard-heatmap td button:enabled").first
    pairs = json.loads(cell.get_attribute("data-dashboard-filter"))
    with page.expect_response(lambda r: r.url.endswith("/api/query")) as response:
        cell.click()
    filters = response.value.request.post_data_json["filters"]
    assert all(any(f["field"] == field and value in f["values"] for f in filters) for field, value in pairs)
    assert response.value.status == 200
    total = response.value.json()["total"]
    expect(page.locator("#dashboard-total")).to_have_text(f"{total:,}")
    page.reload()
    expect(page.locator("#dashboard-scale")).to_have_value("percent")
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    expect(page.locator("#dashboard-total")).to_have_text(f"{total:,}")
    page.get_by_role("button", name="List", exact=True).click()
    expect(page.locator("#rows .row")).to_have_count(min(25, total))
    expect(page.locator("#dashboard")).to_be_hidden()


def test_subsets_group_overlap_copy_and_mobile(page):
    page.goto("/?collection=terms&view=dashboard&group=subsets&split=aspect&chart=heatmap")
    expect(page.locator(".comparison-card")).to_contain_text("Groups can overlap")
    expect(page.locator("#dashboard-split option[value=subsets]")).to_have_count(0)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".dashboard-heatmap")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name=re.compile("goslim_generic")).first.click()
    expect(page.locator("#chips")).to_contain_text("goslim_generic")
    expect(page.locator("#error")).to_be_hidden()
