"""Chemical links lead to local pages and return to exact reaction/model contexts."""

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app


@pytest.mark.parametrize("width", [1440, 390])
def test_chemical_navigation_and_reverse_links(browser, serve_app, chemical_data, width):
    with serve_app(create_app(chemical_data, "duckdb")) as base:
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base + "/terms/GO:0047034")
            page.locator(".reaction-card").get_by_role("link", name="NAD(+)", exact=True).click()
            expect(page.locator("#resource-title")).to_have_text("NAD(+)")
            expect(page.get_by_role("link", name="View in ChEBI")).to_be_visible()
            page.get_by_role("tab", name="Pathways (2)", exact=True).click()
            expect(page.locator('#related-pathways a[href="/gocams/gomodel%3Atest"]')).to_be_visible()
            expect(page.locator('#related-pathways a[href="/modules/MODULE%3Atest"]')).to_be_visible()
            page.get_by_role("tab", name="Reactions (3)", exact=True).click()
            expect(page.locator("#reactions tbody tr")).to_have_count(3)
            page.locator('#reactions a[href*="GO%3A0047034#"]').click()
            expect(page.locator(".reaction-card")).to_be_visible()
            page.go_back()
            expect(page.locator("#reactions")).to_be_visible()
            page.goto(base + "/chemicals/CHEBI:999999")
            expect(page.locator("#resource-title")).to_have_text("Model-only chemical")
            page.get_by_role("tab", name="Reactions (0)", exact=True).click()
            expect(page.locator("#reactions")).to_contain_text("None recorded")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
        finally:
            context.close()
