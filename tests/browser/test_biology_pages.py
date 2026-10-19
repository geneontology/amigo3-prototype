"""Structure expansion and cell-target navigation at desktop and phone widths."""

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app


@pytest.mark.parametrize("width", [1440, 390])
def test_structures_and_cell_targets(browser, serve_app, biology_data, width):
    with serve_app(create_app(biology_data, "duckdb")) as base:
        context = browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base + "/terms/GO:0047034")
            image = page.get_by_role("img", name="2D structure of NAD(+)", exact=True)
            expect(image).to_be_visible()
            assert image.evaluate("img => img.complete && img.naturalWidth > 0")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="Expand structure of NAD(+)", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            expect(page.get_by_role("link", name="Open SVG")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).not_to_be_visible()
            page.goto(base + "/chemicals/CHEBI:57540")
            expect(page.get_by_role("button", name="Expand structure of NAD(+)", exact=True)).to_be_visible()
            page.get_by_role("tab", name="Sources", exact=False).click()
            expect(page.locator("#structure-source")).to_contain_text("RDKit")
            page.goto(base + "/chemicals/CHEBI:999999")
            expect(page.locator(".structure-unavailable")).to_have_text("No structure in this snapshot")
            page.goto(base + "/terms/GO:0030217")
            expect(page.locator(".differentiation-card")).to_contain_text(
                "Results in acquisition of features of"
            )
            page.locator(".differentiation-card").get_by_role("link", name="T cell", exact=True).click()
            expect(page.locator("#resource-title")).to_have_text("T cell")
            page.get_by_role("tab", name="Differentiation (1)", exact=True).click()
            expect(page.locator("#differentiation")).to_contain_text("This cell type and its subtypes (2)")
            page.get_by_role("link", name="Browse targets including subtypes").click()
            expect(page.locator(".row")).to_have_count(2)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
        finally:
            context.close()
