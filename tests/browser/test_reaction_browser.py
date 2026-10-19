"""The structured equation is visible on term overviews and query hits return to it."""

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app


@pytest.mark.parametrize("width", [1440, 390])
def test_reaction_overview_and_browsing(browser, serve_app, reaction_data, width):
    with serve_app(create_app(reaction_data, "duckdb")) as base:
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base + "/terms/GO:0047034#overview")
            card = page.locator(".reaction-card")
            expect(card).to_be_visible()
            expect(card.locator(".reaction-direction")).to_have_text("Direction unspecified")
            expect(card.locator(".reaction-participant")).to_have_count(5)
            expect(card.get_by_role("link", name="NAD(+)", exact=True)).to_have_attribute(
                "href", "/chemicals/CHEBI%3A57540"
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            card.get_by_text("Reaction source & mappings").click()
            expect(card.get_by_text("rdfs:seeAlso · rhea_go")).to_be_visible()
            card.get_by_role("link", name="Browse this term’s reactions").click()
            expect(page.locator("#rows .row")).to_have_count(1)
            page.locator("#rows .row-title").click()
            expect(page.locator(".reaction-card")).to_be_visible()
            expect(page.get_by_role("tab", name="Overview", exact=True)).to_have_attribute(
                "aria-selected", "true"
            )
            page.get_by_role("tab", name="Metadata", exact=True).click()
            expect(page.locator(".reaction-card")).to_be_hidden()
            page.go_back()
            expect(page.locator(".reaction-card")).to_be_visible()
            assert not errors
        finally:
            context.close()
