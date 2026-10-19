"""Species defaults must not depend on API ordering or override shared links."""

import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("human_available", [True, False])
def test_enrichment_species_default_and_explicit_choice(page, human_available):
    def reorder(route):
        data = route.fetch().json()
        data["species"] = sorted(data["species"], key=lambda s: s["value"], reverse=True)
        if not human_available:
            data["species"] = [s for s in data["species"] if s["value"] != "human"]
        route.fulfill(json=data)

    page.route("**/api/enrichment", reorder)
    page.goto("/enrichment")
    expect(page.locator("#species")).to_have_value("human" if human_available else "pombe")
    expect(page.locator("#species option").first).to_have_attribute("value", "pombe")
    page.goto("/enrichment?species=pombe")
    expect(page.locator("#species")).to_have_value("pombe")
    if human_available:
        page.locator("#target").select_option("gocam")
        expect(page.locator("#species")).to_have_value("human")  # pombe has no models
        page.locator("#target").select_option("go")
        expect(page.locator("#species")).to_have_value("human")


def test_enrichment_iba_evidence_option_runs_and_round_trips(page):
    page.goto("/enrichment")
    expect(page.locator("#evidence option[value=iba]")).to_have_text("IBA only (phylogenetic, PAINT)")
    page.goto("/enrichment?genes=GENE1&species=human&aspect=F&evidence=iba&min=1")
    expect(page.locator("#evidence")).to_have_value("iba")
    expect(page.locator("#method")).to_contain_text("IBA only (phylogenetic, PAINT)")
    expect(page).to_have_url(re.compile(r"evidence=iba"))
