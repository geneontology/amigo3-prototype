"""Collection field choices, shareable URLs, tab state and safe inline values."""

import os
from pathlib import Path

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
        configs = page.request.get(BASE + "/api/collections").json()["collections"]
        for collection, config in configs.items():
            if collection == "annotations":
                continue  # Rich review behavior has its own regression script.
            page.goto(BASE + "/?collection=" + collection, wait_until="networkidle")
            expect(page.locator("#display-tools")).to_be_visible()
            expect(page.locator(".extra-field")).to_have_count(0)
            total = page.locator("#status strong").inner_text()
            page.locator('[data-display="details"]').click()
            expect(page.locator(".extra-field").first).to_be_visible()
            expect(page.locator("#status strong")).to_have_text(total)
            page.locator('[data-display="compact"]').click()
            expect(page.locator(".extra-field")).to_have_count(0)
            page.locator("#customize-fields summary").click()
            field = next(iter(config["display_fields"]))
            checkbox = page.locator(f'[data-display-field="{field}"]')
            checkbox.check()
            extra = page.locator(f'.extra-field[data-field="{field}"]').first
            expect(extra).to_contain_text(config["display_fields"][field])
            assert "display=custom" in page.url
            page.reload(wait_until="networkidle")
            expect(extra).to_be_visible()
            # Detail navigation carries the exact custom results URL back.
            url = page.url
            page.locator("#rows .row-title").first.click()
            page.wait_for_url("**/" + collection + "/**")
            page.locator("#back-results").click()
            expect(extra).to_be_visible()
            assert page.url == url
            # Independent defaults and remembered choices when switching tabs.
            page.locator('[data-collection="annotations"]').click()
            expect(page.locator('[data-display="compact"]')).to_have_attribute("aria-pressed", "true")
            page.locator('[data-display="review"]').click()
            page.locator(f'[data-collection="{collection}"]').click()
            expect(extra).to_be_visible()
            expect(page.locator("#expand-reviews")).to_be_hidden()
            expect(page.locator("#review-count-note")).to_be_hidden()
            page.locator('[data-collection="annotations"]').click()
            expect(page.locator('[data-display="review"]')).to_have_attribute("aria-pressed", "true")
            page.locator(f'[data-collection="{collection}"]').click()
            # All available fields must render at mobile width without overflow.
            fields = ",".join(config["display_fields"])
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(
                BASE + f"/?collection={collection}&display=custom&fields={fields}", wait_until="networkidle"
            )
            expect(page.locator(".extra-field").first).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), collection
            page.set_viewport_size({"width": 1440, "height": 1100})
        # Invalid/stale fields cannot expose internals or inherited object properties.
        page.goto(
            BASE
            + "/?collection=genes&display=custom&fields=search_text,toString,assessments,identifiers,identifiers",
            wait_until="networkidle",
        )
        assert page.url.endswith("fields=identifiers")
        expect(page.locator("#rows .row").first.locator(".extra-field")).to_have_count(1)
        page.goto(BASE + "/?collection=genes&display=review", wait_until="networkidle")
        expect(page.locator('[data-display="compact"]')).to_have_attribute("aria-pressed", "true")
        assert "display=" not in page.url

        # Inject edge-case values through the normal query response path.
        def edge_cases(route):
            response = route.fetch()
            body = response.json()
            row = body["hits"][0]
            row.update(
                has_review=False,
                reported_protein_count=0,
                taxon_ids=[],
                description='<img src=x onerror="window.fieldXSS=true">' + "x" * 800,
            )
            route.fulfill(response=response, json=body)

        page.route("**/api/query", edge_cases)
        page.goto(
            BASE
            + "/?collection=families&display=custom&fields=has_review,reported_protein_count,taxon_ids,description",
            wait_until="networkidle",
        )
        row = page.locator("#rows .row").first
        expect(row.locator('[data-field="has_review"] .field-value')).to_have_text("No")
        expect(row.locator('[data-field="reported_protein_count"] .field-value')).to_have_text("0")
        expect(row.locator('[data-field="taxon_ids"] .field-value')).to_have_text("—")
        disclosure = row.locator('[data-field="description"] details')
        expect(disclosure).not_to_have_attribute("open", "")
        disclosure.locator("summary").click()
        expect(disclosure.locator(":scope > span")).to_contain_text("<img src=x")
        assert not page.evaluate("Boolean(window.fieldXSS)")
        expect(row.locator("img")).to_have_count(0)
        page.unroute("**/api/query")
        page.goto(BASE + "/?collection=genes&q=TP53&display=details", wait_until="networkidle")
        page.locator("#customize-fields summary").click()
        page.screenshot(path=str(output / "custom-fields.png"))
        assert not errors, errors
        browser.close()
    print("All collection displays, URL/tab state, inline values and mobile checks passed.")


if __name__ == "__main__":
    main()
