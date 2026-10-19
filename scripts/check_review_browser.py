"""Review display, progressive facets and shareable state against real data."""

import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv("AMIGO3_BROWSER_URL", "http://127.0.0.1:8030")


def main():
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/?collection=annotations", wait_until="networkidle")
        expect(page.locator("#rows .row").first).to_be_visible()
        assert page.locator('[data-group="review"]').count() == 0
        assert page.locator(".review-preview").count() == 0
        total = page.locator("#status strong").inner_text()
        page.locator('[data-display="review"]').click()
        expect(page.locator('[data-group="review"]')).to_be_visible()
        expect(page.locator("#status strong")).to_have_text(total)
        page.locator('[data-display="compact"]').click()
        expect(page.locator('[data-group="review"]')).to_have_count(0)
        # The user's existing bookmarked gene-review filter remains valid.
        filters = [{"id": "has_review", "field": "has_review", "op": "in", "values": [True]}]
        page.goto(
            BASE + "/?collection=annotations&filters=" + quote(json.dumps(filters)), wait_until="networkidle"
        )
        expect(page.locator('[data-group="review"]')).to_be_visible()
        coverage_total = page.locator("#status strong").inner_text()
        page.locator('[data-display="review"]').click()
        expect(page.locator(".assessment-preview").first).to_be_visible()
        expect(page.locator("#status strong")).to_have_text(coverage_total)
        page.locator('[data-group="review"]').get_by_text("ACCEPT", exact=True).click()
        expect(page.locator("#chips")).to_contain_text("ACCEPT")
        expect(page.locator(".assessment-preview").first).to_be_visible()
        page.locator(".card-review summary").first.click()
        expect(page.locator(".assessment-full").first).to_be_visible()
        expect(page.locator(".assessment-full").first).to_contain_text("SHA-256")
        page.locator("#expand-reviews").click()
        expect(page.locator(".card-review:not([open])")).to_have_count(0)
        assert page.locator("dialog[open]").count() == 0
        page.screenshot(path=str(output / "review-expanded.png"))
        page.locator("#expand-reviews").click()
        page.locator('[data-group-more="review"]').click()
        expect(page.locator('[data-facet="review_has_support"]').first).to_be_visible()
        # Removing the activating filter and returning to Compact cannot hide an
        # active action filter. Display choices do not silently change filters.
        page.locator("#chips button").filter(has_text="Gene review available").click()
        page.locator('[data-display="compact"]').click()
        expect(page.locator('[data-group="review"]')).to_be_visible()
        expect(page.locator("#chips")).to_contain_text("ACCEPT")
        page.locator("#customize-fields summary").click()
        page.locator('[data-display-field="db_object_id"]').check()
        expect(page.locator(".extra-field").first).to_contain_text("Annotated subject")
        page.locator('[data-display-field="assessments"]').check()
        expect(page.locator(".assessment-preview").first).to_be_visible()
        url = page.url
        assert "display=custom" in url and "fields=" in url
        page.reload(wait_until="networkidle")
        expect(page.locator(".extra-field").first).to_contain_text("Annotated subject")
        expect(page.locator(".assessment-preview").first).to_be_visible()
        page.screenshot(path=str(output / "review-cards.png"))
        page.locator("#rows .row-title").first.click()
        page.wait_for_url("**/genes/**")
        page.locator("#back-results").click()
        expect(page.locator(".assessment-preview").first).to_be_visible()
        assert "display=custom" in page.url
        # More fields are always discoverable, even without review context.
        page.goto(BASE + "/?collection=annotations", wait_until="networkidle")
        page.locator("#add-filter summary").click()
        page.locator("#facet-picker").select_option("review_actions")
        expect(page.locator('[data-group="review"]')).to_be_visible()
        evidence_filters = [
            {"id": "action", "field": "review_actions", "values": ["MODIFY"]},
            {"id": "support", "field": "review_has_support", "values": [True]},
            {"id": "replacement", "field": "review_replacement_ids", "op": "exists"},
        ]
        page.goto(
            BASE + "/?collection=annotations&display=review&filters=" + quote(json.dumps(evidence_filters)),
            wait_until="networkidle",
        )
        expect(page.locator(".assessment-preview").first).to_contain_text("MODIFY")
        expect(page.locator(".assessment-preview").first).to_contain_text("Proposed replacement")
        page.locator(".card-review summary").first.click()
        expect(page.locator(".review-support blockquote").first).to_be_visible()
        page.screenshot(path=str(output / "review-evidence.png"))
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        mobile.goto(url, wait_until="networkidle")
        expect(mobile.locator(".assessment-preview").first).to_be_visible()
        mobile.locator("#rows").scroll_into_view_if_needed()
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "review-mobile.png"))
        assert not errors, errors
        browser.close()
    print("Review display, progressive facet and mobile checks passed.")


if __name__ == "__main__":
    main()
