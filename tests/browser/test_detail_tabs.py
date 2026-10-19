"""Detail tabs preserve content, deep links and interactive views across record types."""

from urllib.parse import quote

import pytest
from playwright.sync_api import expect


def record_url(page, collection):
    result = page.request.post("/api/query", data={"collection": collection}).json()
    return f"/{collection}/{quote(result['hits'][0]['id'], safe='')}"


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    "collection", ["terms", "gocams", "modules", "families", "rules", "reports", "projects", "references"]
)
def test_detail_tabs_switch_reload_and_keyboard(page, collection, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(record_url(page, collection))
    expect(page.locator("#resource-content")).to_be_visible()
    expect(page.locator("#resource-stats")).to_have_count(0)
    expect(page.get_by_role("tabpanel")).to_have_count(1)
    first_panel = {
        "terms": "panel-overview",
        "gocams": "panel-graph",
        "modules": "panel-structure",
        "families": "panel-members",
        "reports": "report-narrative",
        "projects": "project-narrative",
    }.get(collection)
    if first_panel:
        expect(page.get_by_role("tabpanel")).to_have_attribute("id", first_panel)
    tabs = page.get_by_role("tab")
    assert tabs.count() >= 3
    for tab in tabs.all():
        tab.click()
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("tabpanel")).to_have_count(1)
        expect(page.get_by_role("tabpanel")).to_have_attribute("aria-labelledby", tab.get_attribute("id"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), collection
    selected = tabs.last.inner_text()
    page.reload()
    expect(page.get_by_role("tab", name=selected, exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name=selected, exact=True).focus()
    page.keyboard.press("Home")
    expect(tabs.first).to_be_focused()
    expect(tabs.first).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowLeft")
    expect(tabs.last).to_be_focused()
    expect(tabs.last).to_have_attribute("aria-selected", "true")


def test_term_counts_and_filters_survive_tab_switches(page):
    page.goto("/terms/GO:0016301")
    tab = page.get_by_role("tab", name="Annotations (33)", exact=True)
    tab.click()
    page.locator("#term-scope").select_option("ontology_class_id")
    expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
    page.get_by_role("tab", name="Ontology (").click()
    tab.click()
    expect(page.locator("#term-scope")).to_have_value("ontology_class_id")
    expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
    page.reload()
    expect(tab).to_have_attribute("aria-selected", "true")


@pytest.mark.parametrize("collection", ["terms", "modules", "families"])
def test_description_in_header_is_expandable_plain_text(page, collection):
    description = "A source description <script>window.badDescription=true</script>. " * 12
    url = record_url(page, collection)

    def describe(route):
        response = route.fetch()
        data = response.json()
        if collection == "terms":
            data["record"]["definition"] = description
        elif collection == "modules":
            data["documents"]["module"]["description"] = description
        else:
            data["documents"]["review"]["summary"] = description
        route.fulfill(response=response, json=data)

    page.route(f"**/api/{'terms' if collection == 'terms' else 'resources/' + collection}/*", describe)
    page.goto(url)
    header = page.locator(".gene-intro #resource-description")
    expect(header).to_be_visible()
    preview = header.locator("span").inner_text()
    assert len(preview) <= 321 and preview.endswith("…")
    header.get_by_role("button", name="[more…]", exact=True).click()
    expect(header.locator("span")).to_have_text(description.strip())
    assert not page.evaluate("Boolean(window.badDescription)")
    header.get_by_role("button", name="[less]", exact=True).click()
    expect(header.locator("span")).to_have_text(preview)


def test_graph_evidence_links_open_activities_and_keep_viewer_state(page):
    page.goto("/gocams/gomodel:test")
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    page.locator("#graph-activity").select_option("gomodel:test/a")
    page.locator(".graph-evidence-link").click()
    expect(page.get_by_role("tabpanel", name="Activities (2)")).to_be_visible()
    expect(page.locator('[id="activity-gomodel:test/a"]')).to_be_in_viewport()
    page.go_back()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#graph-activity")).to_have_value("gomodel:test/a")
    page.goto("/gocams/gomodel:test#molecule-gomodel%3Atest%2Finput")
    expect(page.get_by_role("tabpanel", name="Activities (2)")).to_be_visible()
    expect(page.locator('[id="molecule-gomodel:test/input"]')).to_be_in_viewport()
    page.get_by_role("tab", name="Pathway", exact=True).click()
    expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()


@pytest.mark.parametrize("collection,prefix", [("reports", "report"), ("projects", "project")])
def test_narrative_contents_and_bookmarks_open_the_reading_panel(page, collection, prefix):
    url = record_url(page, collection)
    page.goto(url)
    contents = page.locator(".report-contents a").first
    expect(contents).to_be_visible()
    fragment = contents.get_attribute("href")
    contents.click()
    expect(page.locator(f"#{prefix}-narrative")).to_be_visible()
    page.get_by_role("tab", name="Provenance").click()
    expect(page.locator(".report-contents")).to_be_hidden()
    page.go_back()
    expect(page.locator(f"#{prefix}-narrative")).to_be_visible()
    page.goto(url + fragment)
    expect(page.locator(f"#{prefix}-narrative")).to_be_visible()
    expect(page.locator(fragment)).to_be_in_viewport()


@pytest.mark.parametrize("collection", ["reports", "projects"])
def test_narrative_summary_uses_full_rendered_source(page, collection):
    url = record_url(page, collection)
    summary = "A source-grounded description with complete supporting context. " * 12

    def describe(route):
        response = route.fetch()
        data = response.json()
        data["record"]["description"] = "**Metadata:** a truncated search excerpt"
        data["html"] = "<p>Metadata first.</p><h2>Summary</h2><p><strong>" + summary + "</strong></p>"
        route.fulfill(response=response, json=data)

    page.route(f"**/api/{collection}/*", describe)
    page.goto(url)
    header = page.locator("#resource-description")
    expect(header).not_to_contain_text("Metadata")
    header.get_by_role("button", name="[more…]", exact=True).click()
    expect(header.locator("span")).to_have_text(summary.strip())


def test_empty_optional_family_sections_are_omitted(page):
    def empty_family(route):
        response = route.fetch()
        data = response.json()
        data["documents"].update(entries=[], paint=[], review=None)
        data["record"]["member_count"] = 0
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/families/*", empty_family)
    page.goto("/families/PANTHER:PTHR00001#subfamilies")
    expect(page.get_by_role("tab", name="Subfamilies")).to_have_count(0)
    expect(page.get_by_role("tab", name="Ancestral annotations")).to_have_count(0)
    expect(page.get_by_role("tab", name="Review", exact=True)).to_have_count(0)
    expect(page.get_by_role("tabpanel", name="Members (0)")).to_be_visible()


def test_family_review_grouping_without_subfamily_id_renders(page):
    """Issue #32: real reviews (e.g. PTHR45633) group unplaced members without a subfamily_id."""
    errors = []
    page.on("pageerror", lambda error: errors.append(error.message))

    def unplaced_group(route):
        response = route.fetch()
        data = response.json()
        data["documents"]["review"]["subfamilies"] = [
            {
                "label": "Benchmark members without resolved subfamily placement",
                "description": "Protein-folding assistance is the conserved core.",
                "representative_members": [{"id": "UniProtKB:P00001", "gene_symbol": "GENE1"}],
            }
        ]
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/families/*", unplaced_group)
    search = '?collection=families&filters=[{"id":"taxon_ids","field":"taxon_ids","op":"in","values":["NCBITaxon:9606"]}]'
    page.goto("/families/PANTHER%3APTHR00001?from=" + quote(search, safe=""))
    expect(page.locator("#resource-content")).to_be_visible()
    expect(page.locator("#back-results")).to_have_attribute("href", "/" + search)
    page.get_by_role("tab", name="Subfamilies (3)").click()
    expect(page.locator("#subfamilies")).to_contain_text("without resolved subfamily placement")
    # Only identified subfamilies are offered as member filters.
    expect(page.locator(".subfamily-link")).to_have_count(2)
    expect(page.locator("#member-subfamily option")).to_have_count(3)
    assert not errors


def test_related_counts_separate_pathways_from_evolution_and_keep_old_bookmarks(page):
    def related(route):
        response = route.fetch()
        data = response.json()
        data["related"] = {
            "gocams": {"total": 1, "items": [{"id": "gomodel:test", "label": "Test pathway"}]},
            "modules": {"total": 1, "items": [{"id": "MODULE:test", "label": "Test module"}]},
            "families": {"total": 1, "items": [{"id": "PANTHER:PTHR00001", "label": "Test family"}]},
            "reports": {"total": 0, "items": []},
        }
        route.fulfill(response=response, json=data)

    page.route("**/api/terms/*", related)
    page.goto("/terms/GO:0016301#related")
    pathways = page.get_by_role("tabpanel", name="Pathways (2)")
    expect(pathways).to_be_visible()
    expect(pathways.get_by_role("link")).to_have_count(2)
    expect(pathways).not_to_contain_text("Test family")
    expect(page.get_by_role("tab", name="Reports")).to_have_count(0)
    page.get_by_role("tab", name="Evolution (1)").click()
    evolution = page.get_by_role("tabpanel", name="Evolution (1)")
    expect(evolution.get_by_role("link", name="Test family")).to_be_visible()
    expect(evolution).not_to_contain_text("Test pathway")
    page.reload()
    expect(evolution).to_be_visible()
    page.go_back()
    expect(pathways).to_be_visible()


def test_module_without_description_or_evidence_defaults_to_structure(page):
    def empty_module(route):
        response = route.fetch()
        data = response.json()
        data["documents"]["module"].update(description="", evidence=[], references=[], knowledge_gaps=[])
        data["related"] = {}
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/modules/*", empty_module)
    page.goto("/modules/MODULE:test#evidence")
    expect(page.get_by_role("tabpanel", name="Structure (")).to_be_visible()
    expect(page.get_by_role("tab", name="Evidence", exact=True)).to_have_count(0)
    expect(page.locator("#resource-description")).to_be_hidden()
    page.goto("/modules/MODULE:test#related")
    expect(page.get_by_role("tabpanel", name="Structure (")).to_be_visible()
