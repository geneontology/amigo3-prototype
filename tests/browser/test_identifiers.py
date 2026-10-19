"""Provider identifiers stay visible at the top and link using full CURIE labels."""

import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("identifier", ["PomBase:SPTEST.1", "HGNC:1"])
def test_gene_identifiers_are_visible_linked_curies(page, context, width, identifier):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("/genes/" + identifier)
    expect(page.locator("#gene-content")).to_be_visible()
    identifiers = page.locator(".gene-intro #gene-identifiers")
    expect(identifiers).to_be_visible()
    labels = identifiers.locator("a").all_text_contents()
    assert labels[0] == identifier
    assert len(labels) == len(set(labels))
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*:[^\s]+", label) for label in labels)
    assert not any(label.startswith("HGNC:HGNC:") or label.startswith("Ensembl:") for label in labels)
    box = identifiers.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] < 844
    assert box["y"] < page.get_by_role("tabpanel").bounding_box()["y"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if identifier.startswith("PomBase:"):
        link = identifiers.get_by_role("link", name="PomBase:SPTEST.1", exact=True)
        destination = "https://www.pombase.org/gene/SPTEST.1"
        expect(link).to_have_attribute("href", destination)
        expect(identifiers.get_by_role("link", name="UniProtKB:Q00001", exact=True)).to_have_attribute(
            "href", "https://www.uniprot.org/uniprotkb/Q00001/entry"
        )
        # Exercise the external link without making CI depend on a biological service.
        context.route(destination, lambda route: route.fulfill(body="Provider record"))
        with page.expect_popup() as opened:
            link.click()
        expect(opened.value).to_have_url(destination)
        opened.value.close()
        expect(page.locator("#gene-content")).to_be_visible()
    # Existing deep links now lead to the identifiers in the summary.
    page.goto("/genes/" + identifier + "#identifiers")
    expect(identifiers).to_be_visible()
    assert identifiers.bounding_box()["y"] < 844


def test_gene_header_shows_primary_identifiers_and_discloses_the_rest(page):
    page.goto("/genes/HGNC:1")
    identifiers = page.locator(".gene-intro #gene-identifiers")
    expect(identifiers).to_be_visible()
    visible = identifiers.locator(":scope > a, :scope > span").all_text_contents()
    # Own ID first; then UniProt accessions, unversioned Ensembl gene and NCBIGene.
    assert visible[0] == "HGNC:1"
    assert {"UniProtKB:P00001", "ENSEMBL:ENSG00000000001"} <= set(visible)
    assert not any(label.startswith("RefSeq:") or "-PRO_" in label for label in visible)
    assert "ENSEMBL:ENSG00000000001.3" not in visible
    # Nothing is lost: the remaining cross-references sit behind a collapsed disclosure.
    more = identifiers.locator("details.more-identifiers")
    hidden = more.locator(".id-list > a, .id-list > span")
    expect(hidden.first).to_be_hidden()
    summary = more.locator("summary")
    expect(summary).to_have_text(re.compile(r"^\d+ more identifiers?$"))
    assert summary.text_content().startswith(str(hidden.count()) + " ")
    summary.click()
    for label in ["RefSeq:NP_000001.1", "UniProtKB:P00001-PRO_0000000001", "ENSEMBL:ENSG00000000001.3"]:
        expect(more.get_by_text(label, exact=True)).to_be_visible()
    assert set(visible).isdisjoint(hidden.all_text_contents())
