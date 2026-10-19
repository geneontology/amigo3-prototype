"""Species navigation uses the loaded sources and keeps MOD/provider IDs visible."""

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [1440, 390])
def test_new_species_browsing_and_term_filter(page, multispecies_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(multispecies_url + "/")
    expect(page.locator("#species-scope")).to_contain_text("C. elegans")
    expect(page.locator("#species-scope")).to_contain_text("P. putida KT2440")
    # The hero keeps only the title, search box, species line and examples (#33).
    hero = page.locator(".search-panel")
    expect(hero.locator("h1")).to_have_text("Search Gene Ontology")
    expect(hero.locator(".examples")).to_contain_text("Try")
    expect(hero.locator(".subtitle, #snapshot, .examples > span")).to_have_count(0)
    expect(hero).not_to_contain_text("IDs welcome")
    page.goto(multispecies_url + "/genes/WB:WBGene00000001")
    expect(page.locator("#gene-species")).to_have_text("C. ELEGANS / Protein")
    expect(
        page.locator("#gene-identifiers").get_by_role("link", name="WB:WBGene00000001", exact=True)
    ).to_have_attribute("href", "https://wormbase.org/species/c_elegans/gene/WBGene00000001")
    expect(
        page.locator("#gene-identifiers").get_by_role("link", name="UniProtKB:G5EDP9", exact=True)
    ).to_be_visible()
    expect(page.locator("#annotation-list")).to_contain_text("accept")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto(multispecies_url + "/genes/UniProtKB:Q88RL4")
    expect(page.locator("#gene-species")).to_have_text("P. PUTIDA KT2440 / Protein")
    expect(page.locator("#annotation-list")).to_contain_text("accept")
    page.goto(multispecies_url + "/terms/GO:0004672")
    page.get_by_role("tab", name="Annotations (").click()
    species = page.get_by_role("combobox", name="Annotation species")
    expect(species.locator("option")).to_have_text(
        ["All loaded species", "P. putida KT2440", "Human", "Fission yeast", "C. elegans"]
    )
    for value, symbol in [("worm", "aap-1"), ("PSEPK", "katE")]:
        species.select_option(value)
        expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
        expect(page.locator("#term-annotations")).to_contain_text(symbol)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
