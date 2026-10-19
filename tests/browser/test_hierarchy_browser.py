"""Tree selections use the actual API, including multiple parent paths."""

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app


@pytest.mark.parametrize("width", [1440, 390])
def test_tree_selection_counts_search_and_history(page, serve_app, hierarchy_data, width):
    page.set_viewport_size({"width": width, "height": 1000})
    with serve_app(create_app(hierarchy_data, "duckdb")) as url:
        page.goto(url + "/?collection=annotations")
        expect(page.locator("#status strong")).to_have_text("6")
        go = page.locator('[data-hierarchy="go_slim"]')
        species = page.locator('[data-hierarchy="taxon_groups"]')
        if width < 750:
            # The old mobile facet strip clipped everything below the tree toolbar.
            assert species.locator(".hierarchy-body").evaluate("el => el.clientHeight") >= 80
            assert page.locator("#facets").evaluate("el => el.clientHeight") > 250
        for branch in ["branch one", "branch two"]:
            go.get_by_role("button", name="Expand " + branch, exact=True).click()
        kinases = go.get_by_role("checkbox", name="GO · generic slim: kinase activity", exact=True)
        expect(kinases).to_have_count(2)
        kinases.first.check()
        expect(page.locator("#status strong")).to_have_text("5")
        for checkbox in kinases.all():
            expect(checkbox).to_be_checked()
        counts = go.locator('.hierarchy-row:has([data-hierarchy-value="GO:0016301"]) small')
        expect(counts).to_have_text(["5", "5"])
        expect(page.locator("#chips")).to_contain_text("kinase activity")
        species.get_by_role("button", name="Expand Opisthokonta", exact=True).click()
        species.get_by_role("checkbox", name="Species: Metazoa", exact=True).check()
        expect(page.locator("#status strong")).to_have_text("3")
        expect(counts).to_have_text(["3", "3"])
        # Siblings retain counts with this facet's own selections excluded.
        expect(
            species.locator('.hierarchy-row:has([data-hierarchy-value="NCBITaxon:4751"]) small')
        ).to_have_text("1")
        selected_url = page.url
        page.reload()
        expect(page.locator("#status strong")).to_have_text("3")
        expect(species.get_by_role("checkbox", name="Species: Metazoa", exact=True)).to_be_checked()
        expect(kinases).to_have_count(2)
        for checkbox in kinases.all():
            expect(checkbox).to_be_checked()
        page.go_back()
        expect(page.locator("#status strong")).to_have_text("5")
        page.go_forward()
        expect(page.locator("#status strong")).to_have_text("3")
        assert page.url == selected_url
        # Category search reveals paths without changing the query or results.
        search = go.get_by_role("searchbox")
        search.fill("kinase")
        expect(search).to_be_focused()
        expect(kinases).to_have_count(2)
        expect(page.locator("#status strong")).to_have_text("3")
        go.get_by_role("button", name="Most used", exact=True).click()
        expect(kinases).to_have_count(1)
        expect(kinases).to_be_checked()
        kinases.uncheck()
        expect(page.locator("#status strong")).to_have_text("4")
        page.locator("#search").fill("no-such-gene")
        page.locator("#search-form button").click()
        expect(page.locator("#status strong")).to_have_text("0")
        expect(species.get_by_role("checkbox", name="Species: Metazoa", exact=True)).to_be_checked()
        expect(go.locator(".hierarchy-row small")).to_have_text(["0"])
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#clear").click()
        expect(page.locator("#status strong")).to_have_text("6")
        page.get_by_role("tab", name="Genes & products").click()
        expect(go).to_have_count(0)
        expect(species).to_be_visible()
