"""Full frozen reviews remain readable, linked, and safe on the gene page."""

import json
import shutil

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.build import build


@pytest.fixture(scope="module")
def rich_review_data(browser_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("rich-review") / "data"
    shutil.copytree(browser_data, data)
    repo = data / "review_repo"
    path = repo / "genes/human/GENE1/GENE1-ai-review.yaml"
    doc = json.loads(path.read_text())
    doc.update(
        status="COMPLETE",
        product_type="PROTEIN",
        references=[
            {
                "id": "PMID:123",
                "title": "Experimental kinase evidence",
                "full_text_unavailable": False,
                "reference_review": {
                    "relevance": "HIGH",
                    "correctness": "VERIFIED",
                    "review_notes": "Checked the experimental evidence.",
                    "replacement": {"reference_id": "PMID:999", "reason": "Canonical record."},
                },
                "findings": [
                    {
                        "statement": "Measured kinase activity.",
                        "supporting_text": "Verbatim experimental observation.",
                        "finding_review": {"finding_status": "DISPUTED", "superseded_by": ["PMID:999"]},
                    }
                ],
            },
            {"id": "PMID:999", "title": "A later kinase study", "findings": []},
        ],
        core_functions=[
            {
                "description": "Core catalytic function.",
                "molecular_function": {"id": "GO:0004672", "label": "protein kinase activity"},
                "locations": [{"id": "GO:0005829", "label": "cytosol"}],
                "in_complex": {"id": "GO:0005839", "label": "proteasome core complex"},
                "substrates": [{"id": "CHEBI:15422", "label": "ATP"}],
                "anatomical_locations": [{"id": "UBERON:0002107", "label": "liver"}],
                "supported_by": [{"reference_id": "PMID:123", "supporting_text": "Core evidence quote."}],
            }
        ],
        functional_isoforms=[{"name": "Active splice class", "description": "Retains catalytic activity."}],
        alternative_products=[{"id": "P00001-2", "name": "2", "sequence_note": "Alternative sequence."}],
        knowledge_gaps=[{"description": "The physiological substrate is unknown.", "gap_kind": ["BIOLOGY"]}],
        suggested_questions=[{"question": "Which substrate is used in vivo?", "experts": ["Expert curator"]}],
        suggested_experiments=[{"description": "Measure substrate turnover in cells."}],
        proposed_new_terms=[
            {"proposed_name": "Specific kinase role", "proposed_definition": "A proposed activity."}
        ],
        future_review_field={"note": "Preserve new schema content.", "count": 0, "verified": False},
    )
    doc["existing_annotations"][0]["review"].update(
        reason="Direct experimental support. " * 35 + " Final rationale sentence. PMID:123",
        propagation_review={
            "root_cause": "TERM_SCOPING_PROBLEM",
            "failure_modes": ["FUNCTIONAL_DIVERGENCE"],
            "source_entities": [
                {"source_id": "PANTHER:PTN001147669", "comment": "Checked the ancestral node."}
            ],
            "residue_claims": [{"claim_type": "LOSS", "target": {"position": 42, "residue": "A"}}],
        },
        additional_reference_ids=["PMID:999"],
        knowledge_gaps=[{"description": "A mechanistic uncertainty."}],
    )
    doc["existing_annotations"][0]["retired"] = False
    doc["existing_annotations"][0]["supporting_entities"] = ["UniProtKB:P00002"]
    path.write_text(json.dumps(doc))
    build(data, repository=repo)
    return data


@pytest.fixture
def rich_review_url(rich_review_data, serve_app):
    with serve_app(create_app(rich_review_data, "duckdb")) as url:
        yield url


@pytest.mark.parametrize("width", [1440, 390])
def test_complete_review_and_citation_navigation(page, rich_review_url, width):
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(rich_review_url + "/genes/HGNC:1#annotations")
    expect(page.locator("#review-coverage")).to_contain_text("4 review entries")
    expect(page.locator("#review-coverage")).to_contain_text("2 matched to 32 of 32")
    first = (
        page.locator("#annotation-list .gene-annotation").filter(has_text="Direct experimental support").first
    )
    expect(first.locator(".review-rationale")).to_contain_text("Direct experimental support")
    expect(first.locator(".review-rationale")).not_to_contain_text("Final rationale sentence")
    first.get_by_role("button", name="[more…]", exact=True).click()
    expect(first.locator(".review-rationale")).to_contain_text("Final rationale sentence")
    expect(first.locator(".propagation-review")).to_contain_text("TERM_SCOPING_PROBLEM")
    expect(first.locator(".propagation-review")).to_contain_text("FUNCTIONAL_DIVERGENCE")
    expect(first.locator(".propagation-review")).to_contain_text("Checked the ancestral node.")
    expect(first.get_by_role("link", name="PANTHER:PTN001147669", exact=True)).to_have_attribute(
        "href", "/?collection=families&q=PANTHER%3APTN001147669"
    )
    first.locator(".review-reason > summary").click()
    expect(first.locator(".review-reason")).to_contain_text("A mechanistic uncertainty.")
    expect(first.locator(".review-reason")).to_contain_text("false")
    first.locator(".review-rationale").get_by_role("link", name="PMID:123", exact=True).click()
    expect(page.get_by_role("tabpanel", name="References (2)")).to_be_visible()
    card = page.locator(".reference-card").filter(has_text="Experimental kinase evidence")
    expect(card).to_be_in_viewport()
    expect(card).to_contain_text("VERIFIED")
    expect(card).to_contain_text("Measured kinase activity.")
    expect(card).to_contain_text("DISPUTED")
    expect(card).to_contain_text("false")
    page.reload()
    expect(card).to_be_in_viewport()
    card.locator(".citation-contexts summary").click()
    card.get_by_role("link", name="Core function 1", exact=True).click()
    expect(page.get_by_role("tabpanel", name="Core Functions (1)")).to_be_visible()
    expect(page.locator(".core-card")).to_contain_text("cytosol")
    expect(page.locator(".core-card")).to_contain_text("proteasome core complex")
    expect(page.locator(".core-card")).to_contain_text("ATP")
    expect(page.locator(".core-card")).to_contain_text("liver")
    for content in [
        "Active splice class",
        "UniProtKB:P00001-2",
        "Specific kinase role",
        "The physiological substrate is unknown.",
        "Preserve new schema content.",
    ]:
        expect(page.locator("#review-biology")).to_contain_text(content)
    expect(page.locator("#review-biology")).to_contain_text("false")
    expect(page.locator("#review-biology")).not_to_contain_text("Which substrate is used in vivo?")
    expect(page.locator("#review-biology")).not_to_contain_text("Measure substrate turnover in cells.")
    page.get_by_role("tab", name="Questions & experiments (2)").click()
    questions = page.get_by_role("tabpanel", name="Questions & experiments (2)")
    expect(questions).to_be_visible()
    expect(questions.locator(".review-section h3")).to_have_text(
        ["Suggested questions", "Suggested experiments"]
    )
    expect(questions).to_contain_text("Which substrate is used in vivo?")
    expect(questions).to_contain_text("Expert curator")
    expect(questions).to_contain_text("Measure substrate turnover in cells.")
    page.reload()
    expect(questions).to_be_visible()
    page.get_by_role("tab", name="Annotations (").click()
    page.locator("#review-coverage").get_by_role("link", name="Unmatched reviews (1)").click()
    expect(page.locator("#unmatched-reviews")).to_be_in_viewport()
    expect(page.locator("#unmatched-reviews")).to_contain_text("A different reference.")
    expect(page.locator("#unmatched-reviews")).not_to_contain_text("A proposal stays separate.")
    page.locator("#review-coverage").get_by_role("link", name="Proposed additions (1)").click()
    expect(page.locator("#proposed-annotations")).to_contain_text("A proposal stays separate.")
    page.reload()
    expect(page.locator("#proposed-annotations")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_reference_search_and_backlink_restore_filtered_annotations(page, rich_review_url):
    page.goto(rich_review_url + "/genes/HGNC:1#references")
    page.locator("#reference-search").fill("DISPUTED")
    expect(page.locator(".reference-card")).to_have_count(1)
    page.locator("#reference-search").fill("no such reference")
    expect(page.locator("#reference-list")).to_contain_text("No references match")
    page.get_by_role("tab", name="Annotations (").click()
    page.locator("#review-filter").select_option("MODIFY")
    page.locator(".review-reason > summary").first.click()
    page.locator("#annotation-list .support .review-citation").click()
    expect(page.locator("#references")).to_be_visible()
    expect(page.locator("#reference-search")).to_have_value("")
    expect(page.locator(".reference-card")).to_have_count(2)
    card = page.locator(".reference-card").filter(has_text="Experimental kinase evidence")
    card.locator(".citation-contexts summary").click()
    backlink = card.locator('.citation-contexts a[href^="#annotation-"]').first.get_attribute("href")
    page.get_by_role("tab", name="Annotations (").click()
    page.locator("#review-filter").select_option("REMOVE")
    page.evaluate("hash => { location.hash = hash; }", backlink)
    expect(page.locator("#review-filter")).to_have_value("")
    expect(page.locator("#annotation-list .gene-annotation")).to_have_count(32)


def test_review_fallback_escapes_untrusted_content(page, rich_review_url):
    payload = '<img src=x onerror="window.reviewInjected=true">'

    def inject(route):
        response = route.fetch()
        data = response.json()
        doc = data["reviews"][0]["document"]
        doc["future_review_field"] = {"note": payload, "url": "javascript:alert(1)"}
        doc["references"][0]["title"] = payload
        doc["references"][0]["findings"] = [{"statement": payload}]
        data["review_entries"][0]["entry"]["review"]["propagation_review"] = {"root_cause": payload}
        route.fulfill(response=response, json=data)

    page.route("**/api/genes/HGNC%3A1", inject)
    page.goto(rich_review_url + "/genes/HGNC:1")
    expect(page.locator("#review-biology")).to_contain_text(payload)
    expect(
        page.locator('#gene-content img, #gene-content script, #gene-content a[href^="javascript:"]')
    ).to_have_count(0)
    assert page.evaluate("window.reviewInjected") is None


def test_distinct_review_sources_keep_reference_assessments_separate(page, rich_review_url):
    def add_review(route):
        response = route.fetch()
        data = response.json()
        extra = json.loads(json.dumps(data["reviews"][0]))
        extra["id"] = "second-review"
        extra["document"]["references"] = [{"id": "PMID:123", "title": "Independent source assessment"}]
        data["reviews"].append(extra)
        route.fulfill(response=response, json=data)

    page.route("**/api/genes/HGNC%3A1", add_review)
    page.goto(rich_review_url + "/genes/HGNC:1#overview")
    core = page.locator(".core-card").nth(1)
    core.locator("summary").click()
    core.get_by_role("link", name="PMID:123", exact=True).click()
    expect(page.locator(".reference-card:target")).to_contain_text("Independent source assessment")
    expect(page.locator(".reference-card:target")).not_to_contain_text("VERIFIED")


def test_questions_tab_is_deep_linkable(page, rich_review_url):
    page.goto(rich_review_url + "/genes/HGNC:1#questions")
    expect(page.get_by_role("tabpanel", name="Questions & experiments (2)")).to_be_visible()
    expect(page.get_by_role("tab", name="Questions & experiments (2)")).to_have_attribute(
        "aria-selected", "true"
    )
    anchor = page.locator('#questions article[id^="suggested_experiments-"]').get_attribute("id")
    page.goto(rich_review_url + "/genes/HGNC:1#overview")
    expect(page.locator("#questions")).to_be_hidden()
    page.goto(rich_review_url + "/genes/HGNC:1#" + anchor)
    expect(page.get_by_role("tabpanel", name="Questions & experiments (2)")).to_be_visible()
    expect(page.locator("#" + anchor)).to_be_in_viewport()


def test_questions_tab_hidden_without_suggestions(page, rich_review_url):
    def strip(route):
        response = route.fetch()
        data = response.json()
        data["reviews"][0]["document"].update(suggested_questions=[], suggested_experiments=[])
        route.fulfill(response=response, json=data)

    page.route("**/api/genes/HGNC%3A1", strip)
    page.goto(rich_review_url + "/genes/HGNC:1#questions")
    expect(page.get_by_role("tab", name="Questions & experiments")).to_have_count(0)
    expect(page.locator("#questions")).to_be_hidden()
    expect(page.get_by_role("tabpanel", name="Core Functions (1)")).to_be_visible()
