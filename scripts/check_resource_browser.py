"""Exercise term, GO-CAM, module and family browsing against real cached data."""

import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.getenv("AMIGO3_BROWSER_URL", "http://127.0.0.1:8030")


def main():
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        examples = {
            "terms": "GO:0016301",
            "gocams": "gomodel:6246724f00000549",
            "modules": "MODULE:bcl2_family_momp_control",
            "families": "PANTHER:PTHR10102",
        }
        for collection, identifier in examples.items():
            page.goto(
                BASE
                + "/?collection="
                + collection
                + "&filters="
                + quote(json.dumps([{"id": "record", "field": "id", "values": [identifier]}])),
                wait_until="networkidle",
            )
            assert page.get_by_role("tab").count() == 9
            page.locator("#rows .row-title").first.click()
            page.wait_for_url("**/" + collection + "/**")
            page.locator("#resource-content").wait_for(state="visible")
            assert page.locator("dialog").count() == 0
            assert page.locator("#resource-id").inner_text() == identifier
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / f"{collection}-page.png"))
            page.reload(wait_until="networkidle")
            page.locator("#resource-content").wait_for(state="visible")
            if collection == "terms":
                page.get_by_role("tab", name="Annotations (").click()
                page.locator("#term-annotations tbody tr").first.wait_for()
                first = page.locator(".source-row").first.get_attribute("data-source")
                page.locator("#term-next").click()
                page.wait_for_function("document.querySelector('#term-page').textContent === 'Page 2'")
                assert page.locator(".source-row").first.get_attribute("data-source") != first
                page.locator("#term-scope").select_option("ontology_class_id")
                page.wait_for_function("document.querySelector('#term-page').textContent === 'Page 1'")
                page.locator(".source-row summary").first.click()
                page.locator(".source-row pre").first.wait_for()
                assert "GO:0016301" in page.locator(".source-row pre").first.inner_text()
            elif collection == "gocams":
                page.locator("#pathway-canvas[data-ready=true]").wait_for()
                assert page.locator("#pathway-canvas canvas").count() > 0
                assert (
                    page.locator(".activity-card").count()
                    == page.request.get(BASE + "/api/resources/gocams/" + identifier).json()["record"][
                        "activity_count"
                    ]
                )
                assert page.locator("#molecules").get_by_text("AGT Hsap", exact=False).count() > 0
            elif collection == "modules":
                assert page.locator(".module-node").count() > 3
                assert page.locator(".annoton-card").count() > 3
            else:
                page.locator("#member-search").fill("O00411")
                assert page.locator("#family-members tbody tr").count() == 1
                assert "/genes/" in page.locator("#family-members tbody a").first.get_attribute("href")
                assert page.locator(".subfamily-link").count() > 0
                subfamily = page.locator(".subfamily-link").first.get_attribute("data-subfamily")
                page.get_by_role("tab", name="Subfamilies (").click()
                page.locator(".subfamily-link").first.click()
                assert page.locator("#member-subfamily").input_value() == subfamily
                for row in page.locator("#family-members tbody tr").all():
                    assert row.locator("td").nth(3).inner_text().removeprefix("PANTHER:") == subfamily
        page.goto(BASE + "/modules/MODULE:generic_gluconeogenesis", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator(".module-variants").count() >= 2
        page.goto(BASE + "/families/PANTHER:PTHR12778", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator("#paint tbody tr").count() > 0
        page.get_by_role("tab", name="Ancestral annotations (").click()
        assert "IBD" in page.locator("#paint").inner_text()
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        for collection, identifier in examples.items():
            mobile.goto(BASE + "/" + collection + "/" + identifier, wait_until="networkidle")
            mobile.locator("#resource-content").wait_for(state="visible")
            assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"), collection
            mobile.screenshot(path=str(output / f"{collection}-mobile.png"))
        assert not errors, errors
        browser.close()
    (output / "resources-result.json").write_text(
        json.dumps({"status": "passed", "errors": errors}, indent=2)
    )
    print("Term, GO-CAM, module and family browser checks passed.")


if __name__ == "__main__":
    main()
