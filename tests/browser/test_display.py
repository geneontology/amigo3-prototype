"""Field controls after a cached UI upgrade."""

import json
import re
from urllib.parse import urlencode

import pytest
from fastapi.responses import HTMLResponse, Response
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.genesets import GenesetClient


def test_customize_every_collection_with_a_warm_browser_cache(biology_data, page, serve_app):
    # Exercise actual result fields for every collection, including optional
    # Rhea/CL inputs absent from the ordinary browser fixture.
    app = create_app(biology_data, "duckdb", genesets=GenesetClient(""))
    legacy_requests = []
    upgrade = False

    async def deployment(scope, receive, send):
        # Reproduce a browser holding the old unversioned presentation.js while
        # a deployment delivers new HTML/app.js. Do not use page.route: it
        # disables the browser cache and would mask precisely this regression.
        path = scope.get("path")
        if path == "/cache-seed":
            response = HTMLResponse('<script src="/presentation.js"></script>')
        elif not upgrade and path == "/presentation.js":
            legacy_requests.append(path)
            response = Response(
                "window.legacyPresentation = true;",
                media_type="text/javascript",
                headers={"Cache-Control": "public, max-age=3600"},
            )
        else:
            await app(scope, receive, send)
            return
        await response(scope, receive, send)

    with serve_app(deployment) as base:
        for _ in range(2):
            page.goto(base + "/cache-seed", wait_until="networkidle")
            assert page.evaluate("window.legacyPresentation")
        assert len(legacy_requests) == 1  # The second visit really used the HTTP cache.
        upgrade = True
        page.goto(base, wait_until="networkidle")
        expect(page.locator("#rows .row").first).to_be_visible()
        assert not page.evaluate("Boolean(window.legacyPresentation)")
        assert page.evaluate("typeof normalizeDisplay") == "function"
        for name, config in app.state.catalog.collections.items():
            assert config["count"] > 0, f"Seed {name} before testing result field customization"
            # A row from the previous collection can still be visible while
            # this query is pending; wait for the requested collection first.
            with page.expect_response(
                lambda response, name=name: (
                    response.url.endswith("/api/query")
                    and response.request.post_data_json["collection"] == name
                )
            ) as query:
                page.locator(f'[data-collection="{name}"]').click()
            assert query.value.ok
            expect(page.locator("#display-tools")).to_be_visible()
            expect(page.locator("#rows .row").first).to_be_visible()
            page.locator("#customize-fields summary").click()
            field = next(f for f in config["display_fields"] if f != "assessments")
            page.locator(f'[data-display-field="{field}"]').check()
            extra = page.locator(f'#rows .extra-field[data-field="{field}"]').first
            expect(extra).to_be_visible()
            expect(extra.locator("strong")).to_have_text(config["display_fields"][field])
            assert "display=custom" in page.url
            page.reload(wait_until="networkidle")
            expect(extra).to_be_visible()
            # Clearing the checkbox actually removes the added field.
            page.locator("#customize-fields summary").click()
            page.locator(f'[data-display-field="{field}"]').uncheck()
            expect(extra).to_have_count(0)


@pytest.mark.parametrize(
    "collection", ["reactions", "chemicals", "reaction_participants", "cell_types", "differentiations"]
)
def test_customize_empty_collection_preserves_preferences(page, collection):
    page.goto("/?collection=" + collection)
    expect(page.locator("#rows .empty")).to_contain_text("No matches in this snapshot")
    expect(page.locator("#rows .row")).to_have_count(0)
    page.locator("#customize-fields summary").click()
    checkbox = page.locator("[data-display-field]").first
    field = checkbox.get_attribute("data-display-field")
    checkbox.check()
    expect(page).to_have_url(re.compile(r"display=custom"))
    page.reload(wait_until="networkidle")
    expect(page.locator("#rows .empty")).to_contain_text("No matches in this snapshot")
    expect(page.locator("#rows .extra-field")).to_have_count(0)
    page.locator("#customize-fields summary").click()
    expect(page.locator(f'[data-display-field="{field}"]')).to_be_checked()


def test_presets_tab_choices_and_invalid_fields(page):
    page.goto("/?collection=genes")
    expect(page.locator("#rows .row").first).to_be_visible()
    page.locator('[data-display="details"]').click()
    expect(page.locator('[data-field="identifiers"]').first).to_be_visible()
    page.locator('[data-display="compact"]').click()
    expect(page.locator(".extra-field")).to_have_count(0)
    page.locator("#customize-fields summary").click()
    page.locator('[data-display-field="identifiers"]').check()
    page.locator('[data-collection="reports"]').click()
    expect(page.locator('[data-display="compact"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".extra-field")).to_have_count(0)
    page.locator('[data-display="details"]').click()
    expect(page.locator('[data-field="model"]').first).to_be_visible()
    page.locator('[data-collection="genes"]').click()
    expect(page.locator('[data-field="identifiers"]').first).to_be_visible()
    page.locator('[data-collection="reports"]').click()
    expect(page.locator('[data-display="details"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#expand-reviews")).to_be_hidden()
    page.goto(
        "/?collection=genes&display=custom&fields=search_text,toString,assessments,identifiers,identifiers"
    )
    expect(page.locator("#rows .row").first.locator(".extra-field")).to_have_count(1)
    assert page.url.endswith("fields=identifiers")
    page.goto("/?collection=genes&display=review")
    expect(page.locator('[data-display="compact"]')).to_have_attribute("aria-pressed", "true")


def test_gene_review_facets_follow_review_focus_and_keep_active_filters(page):
    page.goto("/?collection=genes")
    expect(page.locator("#rows .row")).to_have_count(4)
    review = page.locator('[data-group="review"]')
    expect(review).to_have_count(0)
    expect(page.locator("#review-count-note")).to_be_hidden()
    page.locator('[data-group="coverage"]').get_by_text("Yes", exact=True).click()
    expect(review).to_be_visible()
    expect(page.locator("#review-count-note")).to_contain_text("count genes/products")
    review.get_by_text("MODIFY", exact=True).click()
    expect(page.locator("#rows .row")).to_have_count(1)
    page.locator("#chips button").filter(has_text="Gene review available").click()
    # The action remains discoverable/removable without the coverage filter.
    expect(review.get_by_text("MODIFY", exact=True)).to_be_visible()
    page.reload()
    expect(review.get_by_text("MODIFY", exact=True)).to_be_visible()
    review.get_by_text("MODIFY", exact=True).click()
    expect(page.locator("#rows .row")).to_have_count(4)
    expect(review).to_have_count(0)
    page.locator("#customize-fields summary").click()
    page.locator('[data-display-field="review_actions"]').check()
    expect(review).to_be_visible()
    page.locator('[data-display="compact"]').click()
    expect(review).to_have_count(0)
    # A deliberate Add filter choice is still available in the broad catalog.
    page.locator("#add-filter summary").click()
    page.locator("#facet-picker").select_option("review_actions")
    expect(review).to_be_visible()


def test_gene_review_action_from_species_evidence_and_custom_fields(page, multispecies_url):
    filters = [
        {"id": field, "field": field, "op": "in", "values": [value]}
        for field, value in [("species", "PSEPK"), ("has_review", True), ("evidence_codes", "IDA")]
    ]
    query = urlencode(
        {
            "collection": "genes",
            "filters": json.dumps(filters),
            "display": "custom",
            "fields": "review_actions",
        }
    )
    page.goto(multispecies_url + "/?" + query)
    review = page.locator('[data-group="review"]')
    expect(review.get_by_text("ACCEPT", exact=True)).to_be_visible()
    review.get_by_text("ACCEPT", exact=True).click()
    expect(page.locator("#rows .row-title")).to_have_text("katE")
    expect(page.locator("#chips")).to_contain_text("Review action: ACCEPT")
    expect(page.locator("#review-count-note")).to_contain_text("different annotations on the same gene")
