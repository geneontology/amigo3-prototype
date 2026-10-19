"""Module pages evaluate species satisfiability on demand and keep the choice bookmarkable."""

from playwright.sync_api import expect


def test_module_satisfiability_tab(page):
    page.goto("/modules/MODULE%3Atest#satisfiability")
    expect(page.get_by_role("tabpanel")).to_have_attribute("id", "panel-satisfiability")
    expect(page.locator("#sat-species")).to_have_value("human")
    expect(page.locator(".sat-summary .sat-badge").first).to_have_text("Satisfied")
    page.locator(".sat-node summary", has_text="Route A").click()
    expect(page.locator(".sat-genes a", has_text="GENE1")).to_be_visible()
    assert "species=human" in page.url
    page.select_option("#sat-species", "pombe")
    page.get_by_role("button", name="Evaluate").click()
    expect(page.locator(".sat-summary")).to_contain_text("Fission yeast")
    # Route A is a human-only taxon alternative: not expected, collapsed, and never gap-filled.
    route_a = page.locator(".sat-node", has_text="Route A").last
    expect(route_a).not_to_have_attribute("open", "")
    route_a.locator("summary").click()
    expect(route_a.locator(".sat-badge.sat-not_expected").first).to_have_text("Not expected")
    expect(route_a.locator(".sat-scope")).to_contain_text("curated participants are from Human")
    expect(page.locator(".sat-candidates")).to_have_count(0)
    page.reload()
    expect(page.locator("#sat-species")).to_have_value("pombe")
