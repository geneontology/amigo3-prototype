"""User journeys through the actual fixture-backed app; no mocked search results."""

import hashlib
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect


def open_results(page, collection, search=""):
    page.goto(f"/?collection={collection}&q={search}")
    expect(page.locator("#rows .row").first).to_be_visible()


def open_detail(page, collection, search=""):
    open_results(page, collection, search)
    result_url = page.url
    page.locator("#rows .row-title").first.click()
    expect(page.locator("#resource-content")).to_be_visible()
    expect(page.locator("dialog[open]")).to_have_count(0)
    return result_url


def test_search_gene_reviews_provenance_and_return(page):
    open_results(page, "genes")
    page.get_by_role("searchbox", name="Search genes, identifiers, or biology").fill("GENE1")
    page.locator("#search-form").get_by_role("button").click()
    expect(page.locator("#status")).to_contain_text("matching “GENE1”")
    expect(page.locator("#rows .row")).to_have_count(1)
    page.locator("#rows .row-title").click()
    expect(page.locator("#gene-symbol")).to_have_text("GENE1")
    expect(page.locator("#gene-identifiers")).to_contain_text("HGNC:1")
    expect(page.locator("#gene-identifiers")).to_contain_text("UniProtKB:P00001")
    # Isoform-specific GO:0003674 is not redundant with canonical GO:0004672, so all 32 annotations remain visible
    expect(page.locator("#annotation-list .gene-annotation")).to_have_count(32)
    page.get_by_role("tab", name="Annotations (").click()
    page.locator('[data-facet="is_redundant"]').first.click()
    expect(page.locator("#chips")).to_contain_text("Redundant (broader term): No")
    # Toggle Summary boxes rolled-up view and back to Detailed rows
    page.locator('[data-annotation-view="summary"]').click()
    expect(page.locator(".term-summary-box")).to_have_count(2)
    expect(page.locator(".term-summary-box").first).to_contain_text("molecular function")
    expect(page.locator(".term-summary-box").last).to_contain_text("protein kinase activity")
    page.locator('[data-annotation-view="rows"]').click()
    page.locator("#review-filter").select_option("MODIFY")
    expect(page.locator("#annotation-list .gene-annotation")).to_have_count(1)
    page.locator(".review-reason summary").first.click()
    expect(page.locator("#annotation-list blockquote")).to_have_text("Synthetic kinase evidence.")
    expect(page.locator("#annotation-list")).to_contain_text("Proposed replacement")
    page.locator(".annotation-provenance summary").first.click()
    expect(page.locator(".raw-source pre")).to_contain_text("UniProtKB")
    expect(page.locator(".raw-source")).to_contain_text("SHA-256")
    page.locator("#reset-annotations").click()
    expect(page.locator("#annotation-list .gene-annotation")).to_have_count(32)
    page.locator("#review-coverage").get_by_role("link", name="Proposed additions (1)").click()
    expect(page.locator("#proposed-annotations")).to_contain_text("A proposal stays separate.")
    expect(page.locator("#unmatched-reviews")).to_contain_text("A different reference.")
    page.reload()
    expect(page.locator("#gene-content")).to_be_visible()
    page.locator("#back-results").click()
    expect(page.locator("#search")).to_have_value("GENE1")
    expect(page.locator("#rows .row")).to_have_count(1)
    page.locator("#search").fill("no-such-fixture-record")
    page.locator("#search-form").get_by_role("button").click()
    expect(page.locator("#rows .empty")).to_contain_text("No matches")
    page.locator("#clear").click()
    expect(page.locator("#rows .row")).to_have_count(4)


def test_annotation_review_facets_evidence_and_pagination(page):
    open_results(page, "annotations")
    expect(page.locator('[data-group="review"]')).to_have_count(0)
    first = page.locator("[data-record]").first.get_attribute("data-record")
    page.locator("#next").click()
    expect(page.locator("#page-label")).to_contain_text("Page 2")
    assert page.locator("[data-record]").first.get_attribute("data-record") != first
    page.locator("#previous").click()
    expect(page.locator("#page-label")).to_contain_text("Page 1")
    page.locator('[data-display="review"]').click()
    expect(page.locator('[data-group="review"]')).to_be_visible()
    page.locator('[data-group="review"]').get_by_text("MODIFY", exact=True).click()
    expect(page.locator("#status strong")).to_have_text("1")
    expect(page.locator(".assessment-preview")).to_contain_text("More specific term needed.")
    page.locator(".card-review summary").click()
    expect(page.locator(".review-support blockquote")).to_have_text("Synthetic kinase evidence.")
    expect(page.locator(".assessment-full")).to_contain_text("SHA-256")
    page.locator("#expand-reviews").click()
    expect(page.locator(".card-review:not([open])")).to_have_count(0)
    page.locator('[data-group-more="review"]').click()
    expect(page.locator('[data-facet="review_has_support"]').first).to_be_visible()
    # An active review filter must survive switching back to minimal cards.
    page.locator('[data-display="compact"]').click()
    expect(page.locator("#chips")).to_contain_text("MODIFY")
    expect(page.locator('[data-group="review"]')).to_be_visible()
    page.locator("#customize-fields summary").click()
    page.locator('[data-display-field="assessments"]').check()
    page.locator('[data-display-field="db_object_id"]').check()
    expect(page.locator('[data-field="db_object_id"]')).to_contain_text("UniProtKB:P00001-2")
    url = page.url
    page.reload()
    expect(page.locator(".assessment-preview")).to_contain_text("MODIFY")
    page.locator("#rows .row-title").first.click()
    expect(page.locator("#gene-content")).to_be_visible()
    page.locator("#back-results").click()
    expect(page).to_have_url(url)
    expect(page.locator(".assessment-preview")).to_contain_text("MODIFY")


def test_annotation_method_facet_follows_evidence_and_labels_rows(page):
    open_results(page, "annotations")
    fields = page.locator('[data-group="annotation"] [data-facet-field]').evaluate_all(
        "nodes => nodes.map(n => n.dataset.facetField)"
    )
    assert fields.index("method") == fields.index("evidence_code") + 1
    method = page.locator('[data-facet-field="method"]')
    expect(method.locator("h3")).to_have_text("Method")
    method.locator("label").filter(has_text="InterPro2GO").locator("input").check()
    expect(page.locator("#chips")).to_contain_text("Method: InterPro2GO")
    expect(page.locator("#rows .row")).to_have_count(1)
    expect(page.locator("#rows .row-tag")).to_contain_text("IEA · InterPro2GO")


def test_term_descendants_exact_scope_paging_and_not(page):
    page.goto("/terms/GO:0016301")
    page.get_by_role("tab", name="Annotations (").click()
    expect(page.locator("#resource-content")).to_be_visible()
    expect(page.locator("#term-annotation-status")).to_have_text("33 matching annotations")
    first = page.locator(".source-row").first.get_attribute("data-source")
    page.locator("#term-next").click()
    expect(page.locator("#term-page")).to_have_text("Page 2")
    assert page.locator(".source-row").first.get_attribute("data-source") != first
    page.locator("#include-not").uncheck()
    expect(page.locator("#term-page")).to_have_text("Page 1")
    expect(page.locator("#term-annotation-status")).to_have_text("32 matching annotations")
    page.locator("#term-scope").select_option("ontology_class_id")
    expect(page.locator("#term-annotation-status")).to_have_text("1 matching annotations")
    expect(page.locator("#term-next")).to_be_disabled()
    page.locator(".source-row summary").click()
    expect(page.locator(".source-row pre")).to_contain_text("PomBase")
    page.locator("#term-species").select_option("human")
    expect(page.locator("#term-annotation-status")).to_have_text("0 matching annotations")
    page.goto("/terms/GO:9916301")
    expect(page.locator("#resource-id")).to_have_text("GO:0016301")


def test_module_variants_family_members_and_paint(page):
    result = open_detail(page, "modules", "test")
    page.get_by_role("tab", name="Structure (").click()
    expect(page.locator(".module-variants")).to_have_count(1)
    expect(page.locator(".module-node")).to_have_count(3)
    expect(page.locator(".annoton-card")).to_have_count(1)
    expect(page.locator("#structure")).to_contain_text("Route A")
    page.locator("#back-results").click()
    expect(page).to_have_url(result)
    open_detail(page, "families")
    page.get_by_role("tab", name="Members (").click()
    expect(page.locator("#family-members tbody tr")).to_have_count(2)
    page.locator("#member-search").fill("P00001")
    expect(page.locator("#family-members tbody tr")).to_have_count(1)
    assert "/genes/" in page.locator("#family-members tbody a").first.get_attribute("href")
    page.locator("#member-search").fill("")
    page.get_by_role("tab", name="Subfamilies (").click()
    page.locator('[data-subfamily="PTHR00001:SF2"]').click()
    expect(page.locator("#member-subfamily")).to_have_value("PTHR00001:SF2")
    expect(page.locator("#family-members tbody tr")).to_have_count(1)
    expect(page.locator("#family-members")).to_contain_text("Other protein")
    page.get_by_role("tab", name="Ancestral annotations (").click()
    expect(page.locator("#paint")).to_contain_text("IBD")
    expect(page.locator("#paint")).to_contain_text("NOT")


def test_reports_downloads_projects_and_safe_markdown(page):
    result = open_detail(page, "reports", "distinctiveflavorkinase")
    expect(page.locator("#report-text table")).to_have_count(1)
    expect(page.locator("#report-text script, #report-text img")).to_have_count(0)
    expect(page.locator('#report-text a[href^="javascript:"]')).to_have_count(0)
    expect(page.locator("#report-text")).not_to_contain_text("Prompt-only-token")
    page.get_by_role("tab", name="Provenance", exact=True).click()
    page.locator("#report-source summary").filter(has_text="Original question").click()
    expect(page.locator("#report-source")).to_contain_text("Prompt-only-token")
    page.get_by_role("tab", name="Files & citations (").click()
    link = page.locator("#report-files a").filter(has_text="evidence.csv")
    with page.expect_download() as download:
        link.click()
    assert download.value.path().read_bytes() == b"gene,result\r\nGENE1,yes\r\n"
    expect(page.locator("#report-files")).to_contain_text("not in source snapshot")
    page.reload()
    expect(page.locator("#resource-content")).to_be_visible()
    page.locator("#back-results").click()
    expect(page).to_have_url(result)
    result = open_detail(page, "projects", "searchablephrase")
    expect(page.locator("#project-text table")).to_have_count(1)
    expect(page.locator("#project-text script")).to_have_count(0)
    page.get_by_role("tab", name="Provenance", exact=True).click()
    source = page.locator("#project-source a").get_attribute("href")
    response = page.request.get(source)
    expect(page.locator("#project-source")).to_contain_text(hashlib.sha256(response.body()).hexdigest())
    page.get_by_role("tab", name="Related records (").click()
    page.locator('#project-related a[href^="/reports/"]').first.click()
    expect(page.locator("#resource-content")).to_be_visible()
    page.locator('.report-context a[href^="/projects/"]').first.click()
    expect(page.locator("#resource-id")).to_have_text("PROJECT:TEST")
    page.get_by_role("tab", name="Related records (").click()
    page.locator('#project-related a[href^="/genes/"]').first.click()
    expect(page.locator("#gene-content")).to_be_visible()


def test_rule_mapping_semantics_sources_and_missing_conditions(page):
    open_results(page, "rules", "IPR1")
    # Select the negated assertion, independently of the chosen result ordering.
    page.locator("#rows .row").filter(has_text="Not skos:exactMatch").locator(".row-title").click()
    expect(page.locator("#resource-content")).to_be_visible()
    page.get_by_role("tab", name="Mapping", exact=True).click()
    expect(page.locator("#mapping .mapping-modifier")).to_have_text("NOT")
    expect(page.locator("#mapping script")).to_have_count(0)
    source = page.locator("#sources a").first.get_attribute("href")
    assert hashlib.sha256(page.request.get(source).body()).hexdigest() == source.split("/")[-1]
    page.locator("#back-results").click()
    expect(page.locator("#search")).to_have_value("IPR1")
    page.goto("/rules/RULE:ARBA1")
    page.get_by_role("tab", name="Conditions & conclusions").click()
    expect(page.locator("#conditions .activity-card")).to_have_count(2)
    expect(page.locator("#conditions .logic-or")).to_have_count(1)
    expect(page.locator("#conditions")).to_contain_text("NOT")
    page.get_by_role("tab", name="Review", exact=True).click()
    expect(page.locator("#review")).to_contain_text("narrow scope")
    page.goto("/rules/RULE:ARBA2#conditions")
    expect(page.locator("#conditions")).to_contain_text("Antecedent not captured")


@pytest.mark.parametrize(
    "collection",
    ["genes", "annotations", "terms", "gocams", "modules", "families", "reports", "projects", "rules"],
)
def test_mobile_collection_and_detail(page, collection):
    page.set_viewport_size({"width": 390, "height": 844})
    open_results(page, collection)
    page.locator("#customize-fields summary").click()
    expect(page.locator("#display-fields")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#rows .row-title").first.click()
    content = "#gene-content" if collection in {"genes", "annotations"} else "#resource-content"
    expect(page.locator(content)).to_be_visible()
    if collection == "gocams":
        page.get_by_role("tab", name="Pathway", exact=True).click()
        expect(page.locator("#pathway-canvas[data-ready=true]")).to_be_visible()
        expect(page.locator("#graph-direction")).to_have_value("TB")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), urlparse(page.url).path


def test_api_failure_and_missing_record_are_readable(page):
    page.route(
        "**/api/query",
        lambda route: route.fulfill(status=503, json={"detail": "Search backend unavailable."}),
    )
    page.goto("/")
    expect(page.locator("#error")).to_have_text("Search backend unavailable.")
    expect(page.locator("#next")).to_be_disabled()
    page.goto("/genes/missing")
    expect(page.locator("#gene-error")).to_have_text("Record not found in this build")


def test_enrichment_from_shared_link_maps_inputs_and_links_terms(page):
    page.goto("/")
    page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Enrichment").click()
    expect(page.locator("#species option")).to_have_count(2)
    page.goto("/enrichment?genes=GENE1,GENE2,nope,GENE1&species=human&aspect=F&min=1")
    expect(page.locator("#enrichment-results")).to_be_visible()
    expect(page.locator("#genes")).to_have_value("GENE1\nGENE2\nnope\nGENE1")
    mapping = page.locator("#mapping-body")
    expect(mapping.locator(".mapping-group", has_text="Analysed (1)")).to_contain_text("GENE1")
    expect(mapping.locator(".mapping-group", has_text="No qualifying annotation")).to_contain_text("GENE2")
    expect(mapping.locator(".mapping-group", has_text="Not found (1)")).to_contain_text("nope")
    expect(mapping.locator(".mapping-group", has_text="Duplicates ignored (1)")).to_be_visible()
    # A single-gene background has nothing to enrich; all terms remain inspectable.
    expect(page.locator("#term-table tbody")).to_contain_text("No terms pass FDR")
    page.locator("#show-all").check()
    expect(page.locator("#term-table tbody tr").first.locator("a").first).to_be_visible()
    page.locator("#aspect").select_option("P")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"aspect=P"))
    page.locator("#aspect").select_option("F")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#term-table tbody tr").first.locator("a").first).to_be_visible()
    page.locator("#term-table tbody tr").first.locator("a").first.click()
    expect(page.locator("#resource-content")).to_be_visible()
    page.goto("/enrichment?genes=GENE1,GENE2&species=human&target=gocam&min=1")
    expect(page.locator("#terms-heading")).to_have_text("Enriched GO-CAM models")
    expect(page.locator("#aspect")).to_be_hidden()
    page.locator("#show-all").check()
    page.locator("#term-table tbody").get_by_role("link", name="Test causal model").click()
    expect(page).to_have_url(re.compile(r"/gocams/gomodel"))
    expect(page.locator("#resource-content")).to_be_visible()
    page.goto("/enrichment")
    page.locator("#target").select_option("gocam")
    expect(page.locator("#species option[value=pombe]")).to_have_attribute("disabled", "")
    expect(page.locator("#evidence")).to_be_hidden()
    page.goto("/enrichment?genes=GENE1&species=human&min=5&max=2")
    expect(page.locator("#enrichment-error")).to_contain_text("min_term_size")


def test_enrichment_mgsa_reports_posteriors(page):
    page.goto("/enrichment?genes=GENE1&species=human&aspect=F&min=1&method=mgsa")
    expect(page.locator("#enrichment-results")).to_be_visible()
    expect(page.locator("#analysis-method")).to_have_value("mgsa")
    expect(page.locator("#threshold-label")).to_have_text("Show results with posterior ≥")
    expect(page.locator("#threshold")).to_have_value("0.5")
    expect(page.locator("#score-column")).to_have_text("Posterior")
    expect(page.locator("#method")).to_contain_text("Model-based (MGSA")
    page.locator("#show-all").check()
    expect(page.locator("#term-table tbody tr").first.locator("td").nth(6)).to_have_text(
        re.compile(r"^[01]\.\d{3}$")
    )
    # Switching back restores the FDR threshold and drops the method from the link.
    page.locator("#analysis-method").select_option("hypergeometric")
    expect(page.locator("#threshold")).to_have_value("0.05")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#score-column")).to_have_text("FDR")
    expect(page).not_to_have_url(re.compile(r"method="))


def test_enrichment_mgsa_links_merged_gocam_models_to_model_pages(page):
    # The fixture has a single model, so add a merged equivalent to the real response.
    def add_equivalent(route):
        data = route.fetch().json()
        data["terms"][0]["equivalents"] = [{"id": "gomodel:twin", "label": "Twin model"}]
        route.fulfill(json=data)

    page.route(
        "**/api/enrichment",
        lambda route: add_equivalent(route) if route.request.method == "POST" else route.continue_(),
    )
    page.goto("/enrichment?genes=GENE1,GENE2&species=human&target=gocam&min=1&method=mgsa")
    page.locator("#show-all").check()
    twin = page.locator("#term-table tbody").get_by_role("link", name="Twin model")
    expect(twin).to_have_attribute("href", "/gocams/gomodel%3Atwin")
