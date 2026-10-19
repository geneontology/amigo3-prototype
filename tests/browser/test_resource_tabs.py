"""Counted record tabs preserve filters, deep links, graph state, and keyboard access."""

import re

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [1440, 390])
def test_term_tabs_counts_filters_history_and_keyboard(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("/terms/GO:0016301")
    expect(page.get_by_role("tabpanel", name="Overview")).to_be_visible()
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    annotations = page.get_by_role("tab", name="Annotations (33)")
    annotations.click()
    if width == 390:
        assert page.locator("#term-scope").bounding_box()["width"] >= 180
    page.locator("#term-scope").select_option("ontology_class_id")
    expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
    page.get_by_role("tab", name="Ontology (").click()
    expect(page.locator("#ontology")).to_be_visible()
    page.go_back()
    expect(annotations).to_have_attribute("aria-selected", "true")
    expect(page.locator("#term-scope")).to_have_value("ontology_class_id")
    page.go_forward()
    expect(page.locator("#ontology")).to_be_visible()
    page.reload()
    expect(page.get_by_role("tab", name="Ontology (")).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name="Ontology (").focus()
    page.keyboard.press("ArrowRight")
    expect(annotations).to_be_focused()
    page.keyboard.press("End")
    expect(page.get_by_role("tabpanel", name="Sources (1)")).to_be_visible()
    page.keyboard.press("Home")
    expect(page.get_by_role("tabpanel", name="Overview")).to_be_visible()
    page.keyboard.press("ArrowLeft")
    expect(page.get_by_role("tab", name="Sources (1)")).to_be_focused()
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto("/terms/GO:0016301#%broken")
    expect(page.get_by_role("tabpanel", name="Overview")).to_be_visible()


@pytest.mark.parametrize(
    "path,tab,section",
    [
        ("/gocams/gomodel:test#connections", "Activities (2)", "#connections"),
        ("/families/PANTHER:PTHR00001#subfamilies", "Subfamilies (2)", "#subfamilies"),
        ("/rules/RULE:ARBA1#rule-record", "Sources (1)", "#rule-record"),
        ("/rules/RULE:ARBA2#review", "Review", "#review"),
    ],
)
def test_record_section_bookmarks(page, path, tab, section):
    page.goto(path)
    expect(page.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.locator(section)).to_be_visible()
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    page.get_by_role("tab", name="Overview", exact=True).click()
    expect(page.locator(section)).to_be_hidden()
    page.go_back()
    expect(page.locator(section)).to_be_visible()


def test_activity_deep_links_and_graph_survive_tab_switches(page):
    page.goto("/gocams/gomodel:test#activity-gomodel%3Atest%2Fa")
    expect(page.get_by_role("tabpanel", name="Activities (2)")).to_be_visible()
    expect(page.locator('[id="activity-gomodel:test/a"]')).to_be_in_viewport()
    # Do not initialize a canvas inside a hidden panel.
    expect(page.locator("#pathway-canvas[data-ready]")).to_have_count(0)
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    page.locator("#graph-activity").select_option("gomodel:test/b")
    page.locator(".graph-evidence-link").click()
    expect(page.get_by_role("tabpanel", name="Activities (2)")).to_be_visible()
    expect(page.locator('[id="activity-gomodel:test/b"]')).to_be_in_viewport()
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.locator("#graph-activity")).to_have_value("gomodel:test/b")
    assert page.evaluate("document.getElementById('pathway-canvas')._cy.nodes().length") == 2
    assert page.evaluate("document.getElementById('pathway-canvas')._cy.width()") > 0


def test_slow_graph_load_after_leaving_tab_and_empty_model(page):
    pending = []
    page.route("**/vendor/cytoscape.min.js", lambda route: pending.append(route))
    page.goto("/gocams/gomodel:test#graph")
    page.get_by_role("tab", name="Overview", exact=True).click()
    assert len(pending) == 1
    pending.pop().continue_()
    expect(page.locator("#pathway-canvas")).to_have_attribute("data-ready", "true")
    page.get_by_role("tab", name="Pathway", exact=True).click()
    page.wait_for_function("document.getElementById('pathway-canvas')._cy.zoom() > 0.1")
    assert page.evaluate("document.getElementById('pathway-canvas')._cy.width()") > 100

    def empty_model(route):
        response = route.fetch()
        data = response.json()
        data["documents"]["model"]["activities"] = []
        data["record"]["activity_count"] = 0
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/gocams/*", empty_model)
    page.reload()
    expect(page.get_by_role("tabpanel", name="Pathway", exact=True)).to_contain_text("No activities recorded")
    page.get_by_role("tab", name="Overview", exact=True).click()
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.get_by_role("tabpanel", name="Pathway", exact=True)).to_be_visible()


@pytest.mark.parametrize("width", [1440, 390])
def test_term_metadata_follows_related_tabs_and_legacy_bookmark(page, width):
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto("/terms/GO:0004672")
    expect(page.locator("#ontology-canvas[data-ready=true]")).to_be_visible()
    expect(page.get_by_role("tabpanel", name="Overview", exact=True)).not_to_contain_text("Synonyms")
    tabs = page.locator("#resource-nav [role=tab]").all_text_contents()
    assert tabs[-2:] == ["Metadata", "Sources (1)"]
    page.get_by_role("tab", name="Metadata", exact=True).click()
    expect(page.get_by_role("tabpanel", name="Metadata", exact=True)).to_contain_text("Synonyms")
    page.reload()
    expect(page.get_by_role("tabpanel", name="Metadata", exact=True)).to_be_visible()
    page.goto("/terms/GO:0004672#definition")
    expect(page.get_by_role("tabpanel", name="Metadata", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_related_mappings_show_predicates_and_negation(page):
    page.goto("/terms/GO:0004672#related-rules")
    panel = page.get_by_role("tabpanel", name="Rules & mappings (")
    expect(panel).to_be_visible()
    predicate = panel.locator(".mapping-table .mapping-predicate")
    expect(predicate).to_contain_text("Exact match")
    expect(predicate).to_contain_text("skos:exactMatch")
    expect(predicate.locator(".mapping-modifier")).to_have_text("NOT")
    expect(panel.locator(".mapping-table")).to_contain_text("InterPro:IPR1")
    expect(panel.locator(".mapping-table")).to_contain_text("GO:0004672")
    panel.get_by_role("link", name="Mapping details →").click()
    expect(page.locator("#mapping .mapping-predicate")).to_contain_text("skos:exactMatch")
    expect(page.locator("#mapping .mapping-modifier")).to_have_text("NOT")


def test_term_metadata_subset_links_and_empty_state(page):
    page.goto("/terms/GO:0016301#metadata")
    panel = page.get_by_role("tabpanel", name="Metadata", exact=True)
    expect(panel).to_contain_text("GO subsets / slims")
    panel.get_by_role("link", name="goslim_generic", exact=True).click()
    expect(page.locator("#chips")).to_contain_text("goslim_generic")
    expect(page.locator("#rows .row").first).to_be_visible()
    page.goto("/terms/GO:0004672#metadata")
    expect(page.get_by_role("tabpanel", name="Metadata", exact=True)).to_contain_text("None recorded")


def test_gocam_review_tab_and_activity_findings(page):
    page.goto("/gocams/gomodel:test")
    review_tab = page.get_by_role("tab", name="Review (2)", exact=True)
    review_tab.click()
    panel = page.get_by_role("tabpanel", name="Review (2)")
    expect(panel).to_contain_text("A one-step kinase model.")
    expect(panel).to_contain_text("Model-level reviewer note.")
    finding = panel.locator('[id="activity-review-gomodel:test/a"]')
    expect(finding.locator(".action.reconsider")).to_have_text("UNCERTAIN")
    expect(finding).to_contain_text("CONSISTENT")
    expect(finding).to_contain_text("DIRECT VS INDIRECT CAUSAL")
    expect(finding).to_contain_text("the edge is likely indirect")
    finding.get_by_text("Evidence & supporting text").click()
    expect(finding.locator("blockquote")).to_contain_text("Synthetic phosphorylation evidence.")
    # A finding for an activity no longer in the cached model is kept, not linked.
    retired = panel.locator('[id="activity-review-gomodel:test/retired"]')
    expect(retired.locator(".action.reject")).to_have_text("WRONG")
    expect(retired.locator('a[href^="#activity-"]')).to_have_count(0)
    # Findings also annotate the activity cards and the flagged activity's causal edge.
    finding.get_by_role("link", name="Activity a").click()
    activities = page.get_by_role("tabpanel", name="Activities (2)")
    card = activities.locator('[id="activity-gomodel:test/a"]')
    expect(card.locator(".activity-review")).to_contain_text("MISSING LOCATION")
    expect(activities.locator('[id="activity-gomodel:test/b"] .activity-review')).to_have_count(0)
    edge = activities.locator("#connections tbody tr").first
    expect(edge).to_contain_text("DIRECT VS INDIRECT CAUSAL")
    expect(edge).not_to_contain_text("MISSING LOCATION")
    card.get_by_role("link", name="Review finding →").click()
    expect(finding).to_be_in_viewport()
    page.goto("/gocams/gomodel:test#overview")
    page.get_by_role("link", name="Read the repository review →").click()
    expect(review_tab).to_have_attribute("aria-selected", "true")


@pytest.mark.parametrize("width", [1440, 390])
def test_project_hero_shows_main_artifact_and_metadata(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("/projects/PROJECT:TEST")
    hero = page.locator("#project-summary")
    expect(hero.locator("#project-primary")).to_contain_text("Main artifact")
    slides = hero.locator("#project-primary").get_by_role("link", name="Slides", exact=True)
    expect(slides).to_have_attribute("href", re.compile(r"^/api/report-assets/"))
    for text in (
        "Status",
        "IN PROGRESS",
        "Created",
        "2025-01-15",
        "Ada Lovelace, Grace Hopper",
        "BIOLOGY_DOMAIN",
    ):
        expect(hero).to_contain_text(text)
    page.get_by_role("tab", name="Resources (").click()
    # Main artifacts lead the list; bare filenames are disambiguated by their project path.
    items = page.locator("#project-resources li")
    expect(items.first).to_contain_text("Main artifact")
    expect(items.first.get_by_role("link", name="Slides", exact=True)).to_be_visible()
    run = items.filter(has_text="results/run1/index.html")
    expect(run.get_by_role("link", name="index.html", exact=True)).to_be_visible()
    expect(run.locator(".resource-path")).to_have_text("results/run1/index.html")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
