import pytest
from playwright.sync_api import expect

from amigo3.api import create_app


@pytest.mark.parametrize("width", [1440, 390])
def test_product_type_filter_and_gene_page(page, product_types_data, serve_app, width):
    page.set_viewport_size({"width": width, "height": 950})
    with serve_app(create_app(product_types_data, "duckdb")) as url:
        page.goto(url)
        facet = page.locator(".facet").filter(
            has=page.get_by_role("heading", name="Gene product type", exact=True)
        )
        expect(facet).to_be_visible()
        expect(page.get_by_role("heading", name="Record kind", exact=True)).to_have_count(0)
        facet.get_by_role("button", name="Expand ncRNA", exact=True).click()
        facet.get_by_role("checkbox", name="Gene product type: tRNA", exact=True).check()
        expect(page.locator("#status")).to_contain_text("1 genes & products")
        expect(page.locator("#chips")).to_contain_text("Gene product type: tRNA")
        expect(page.locator(".row-tag")).to_contain_text("tRNA")
        page.reload()
        expect(facet.get_by_role("checkbox", name="Gene product type: tRNA", exact=True)).to_be_checked()
        page.get_by_role("link", name="trna1", exact=True).click()
        expect(page.locator("#gene-species")).to_have_text("FISSION YEAST / tRNA")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 390])
def test_ncrna_parent_counts_nested_types_and_shared_urls(page, product_hierarchy_data, serve_app, width):
    page.set_viewport_size({"width": width, "height": 1000})
    with serve_app(create_app(product_hierarchy_data, "duckdb")) as url:
        page.goto(url)
        tree = page.locator('[data-hierarchy="gene_product_type_groups"]')
        parent = tree.get_by_role("checkbox", name="Gene product type: ncRNA", exact=True)
        expect(parent).to_be_visible()
        expect(tree.get_by_role("checkbox", name="Gene product type: tRNA", exact=True)).to_have_count(0)
        expect(tree.locator('.hierarchy-row:has([data-hierarchy-value="SO:0000655"]) small')).to_have_text(
            "4"
        )
        parent.check()
        expect(page.locator("#status strong")).to_have_text("4")
        tree.get_by_role("button", name="Expand ncRNA", exact=True).click()
        trna = tree.get_by_role("checkbox", name="Gene product type: tRNA", exact=True)
        trna.check()
        expect(page.locator("#status strong")).to_have_text("4")  # Parent + child is a union.
        parent.uncheck()
        expect(page.locator("#status strong")).to_have_text("1")
        page.reload()
        expect(trna).to_be_checked()  # Ancestors of a URL-selected subtype open automatically.
        expect(page.locator("#chips")).to_contain_text("Gene product type: tRNA")
        trna.uncheck()
        tree.get_by_role("button", name="Expand lncRNA", exact=True).click()
        long = tree.get_by_role("checkbox", name="Gene product type: lncRNA", exact=True)
        long.check()
        expect(page.locator("#status strong")).to_have_text("2")
        expect(
            tree.get_by_role("checkbox", name="Gene product type: Antisense lncRNA", exact=True)
        ).to_be_visible()
        page.reload()
        expect(long).to_be_checked()
        tree.get_by_role("searchbox").fill("antisense")
        expect(
            tree.get_by_role("checkbox", name="Gene product type: Antisense lncRNA", exact=True)
        ).to_be_visible()
        expect(page.locator("#status strong")).to_have_text("2")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
