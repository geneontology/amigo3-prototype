"""Gene panels, review summaries, and bookmarks work together on all screen sizes."""

import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [1440, 390])
def test_gene_tabs_navigation_and_sources(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("/genes/HGNC:1")
    expect(page.get_by_role("tab")).to_have_text(
        [
            "Annotations (32)",
            "Predictions (0)",
            "Pathways (2)",
            "Evolution (1)",
            re.compile(r"Reports \(\d+\)"),
        ]
    )
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    expect(page.get_by_role("tabpanel", name="Annotations (32)")).to_be_visible()
    expect(page.get_by_role("tab", name="Core Functions")).to_have_count(0)
    expect(page.locator(".gene-intro #review-overview")).to_have_text(
        "Frozen review summary.GENE1 · review source"
    )
    expect(page.locator("#gene-stats")).to_have_count(0)

    page.get_by_role("tab", name="Annotations (").click()
    page.locator("#review-filter").select_option("MODIFY")
    expect(page.locator("#annotation-list .gene-annotation")).to_have_count(1)
    expect(page.get_by_role("tab", name="Annotations (32)")).to_be_visible()
    page.get_by_role("tab", name="Pathways").click()
    expect(page.locator('#gene-related a[href^="/gocams/"]').first).to_be_visible()
    expect(page.locator('#gene-related a[href^="/modules/"]').first).to_be_visible()
    expect(page.locator('#gene-related a[href^="/families/"]')).to_have_count(0)
    expect(page.locator('#gene-related a[href^="/reports/"]')).to_have_count(0)
    page.get_by_role("tab", name="Reports").click()
    expect(page.locator('#gene-reports a[href^="/reports/"]').first).to_be_visible()
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    page.go_back()
    expect(page.get_by_role("tabpanel", name="Pathways")).to_be_visible()
    page.go_back()
    expect(page.locator("#review-filter")).to_have_value("MODIFY")
    page.reload()
    expect(page.get_by_role("tabpanel", name="Annotations (")).to_be_visible()

    tab = page.get_by_role("tab", name="Annotations (")
    tab.focus()
    tab.press("ArrowRight")
    expect(page.get_by_role("tab", name="Predictions (0)")).to_be_focused()
    expect(page.locator("#gene-prediction-count")).to_contain_text("No preloaded predictions")
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Pathways")).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Evolution (1)")).to_be_focused()
    expect(page.locator('#gene-evolution a[href^="/families/"]')).to_be_visible()
    expect(
        page.locator('#gene-evolution a[href^="/gocams/"], #gene-evolution a[href^="/modules/"]')
    ).to_have_count(0)
    page.keyboard.press("End")
    expect(page.get_by_role("tabpanel", name="Reports")).to_be_visible()
    page.keyboard.press("Home")
    expect(page.get_by_role("tabpanel", name="Annotations (32)")).to_be_visible()

    page.locator("#review-overview .source-ref").click()
    expect(page.get_by_role("tabpanel", name="Reports")).to_be_visible()
    expect(page.locator(".review-source")).to_be_in_viewport()
    page.reload()
    expect(page.locator(".review-source")).to_be_in_viewport()
    page.goto("/genes/HGNC:1#related")
    expect(page.get_by_role("tabpanel", name="Pathways")).to_be_visible()
    page.get_by_role("tab", name="Evolution").click()
    page.reload()
    expect(page.get_by_role("tabpanel", name="Evolution (1)")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 390])
def test_review_description_expands_in_header(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    description = "A reviewed gene description with supporting biological context. " * 15

    def long_review(route):
        response = route.fetch()
        data = response.json()
        data["reviews"][0]["document"]["description"] = description
        data["reviews"][0]["document"]["core_functions"] = [{"description": "Reviewed kinase activity."}]
        route.fulfill(response=response, json=data)

    page.route("**/api/genes/HGNC%3A1", long_review)
    page.goto("/genes/HGNC:1")
    summary = page.locator(".gene-intro #review-description-0")
    expect(summary).to_be_visible()
    expect(page.get_by_role("tab", name="Core Functions (1)")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#core-functions")).to_contain_text("Reviewed kinase activity.")
    preview = summary.inner_text()
    assert len(preview) <= 321 and preview.endswith("…")
    button = page.get_by_role("button", name="[more…]", exact=True)
    button.click()
    expect(summary).to_have_text(description.strip())
    expect(page.get_by_role("button", name="[less]", exact=True)).to_have_attribute("aria-expanded", "true")
    page.get_by_role("button", name="[less]", exact=True).click()
    expect(summary).to_have_text(preview)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_gene_without_review_has_useful_empty_panels(page):
    page.goto("/genes/HGNC:3#overview")
    expect(page.locator("#gene-content")).to_be_visible()
    expect(page.locator("#review-overview")).to_be_hidden()
    expect(page.get_by_role("button", name="[more…]", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab")).to_have_text(
        ["Annotations (0)", "Predictions (0)", "Pathways (0)", "Evolution (0)", "Reports (0)"]
    )
    expect(page.locator("#overview")).to_be_hidden()
    expect(page.get_by_role("tabpanel", name="Annotations (")).to_be_visible()
    expect(page.locator("#annotation-list")).to_contain_text("No annotations")
    page.get_by_role("tab", name="Pathways").click()
    expect(page.locator("#gene-related")).to_contain_text("No linked pathways")
    page.get_by_role("tab", name="Evolution").click()
    expect(page.locator("#gene-evolution")).to_contain_text("No linked families")
    page.get_by_role("tab", name="Reports").click()
    expect(page.locator("#review-provenance")).to_contain_text("No review documents")
