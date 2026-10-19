"""Reference pages connect citations to the annotations, reviews and models that use them."""

from playwright.sync_api import expect


def test_reference_page_links_annotations_reviews_and_models(page):
    page.goto("/references/PMID:123")
    expect(page.locator("#resource-id")).to_have_text("PMID:123")
    expect(page.get_by_role("link", name="PubMed ↗")).to_have_attribute(
        "href", "https://pubmed.ncbi.nlm.nih.gov/123/"
    )
    page.get_by_role("tab", name="Annotations (").click()
    panel = page.locator("#reference-annotations")
    expect(panel.get_by_role("link", name="GENE1", exact=True)).to_have_attribute("href", "/genes/HGNC%3A1")
    panel.get_by_role("link", name="Browse and filter all annotations →").click()
    expect(page).to_have_url(
        "/?collection=annotations&filters="
        + "%5B%7B%22id%22%3A%22reference%22%2C%22field%22%3A%22references%22%2C%22values%22%3A%5B%22PMID%3A123%22%5D%7D%5D",
        ignore_case=True,
    )
    page.goto("/references/PMID:123#reference-reviews")
    reviews = page.locator("#reference-reviews")
    expect(reviews.get_by_text("Synthetic kinase evidence.")).to_be_visible()
    page.get_by_role("tab", name="Models & modules (").click()
    expect(page.locator("#reference-related").get_by_role("link", name="Test causal model")).to_be_visible()


def test_search_results_and_annotation_rows_link_to_reference_pages(page):
    page.goto("/?collection=references")
    expect(page.locator(".row-title").first).to_have_attribute(
        "href", "/references/PMID%3A123?from=%3Fcollection%3Dreferences"
    )
    page.goto("/?collection=annotations")
    page.locator(".row-meta a", has_text="PMID:123").first.click()
    expect(page.locator("#resource-id")).to_have_text("PMID:123")
