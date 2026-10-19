"""Exercise the actual browser against the running API, on desktop and mobile."""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("AMIGO3_BROWSER_URL", "http://127.0.0.1:8030")


def main():
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until="networkidle")
        page.locator(".row-title").first.wait_for()
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.get_by_role("button", name="PomBase:SPAC1002.01", exact=True).click()
        page.get_by_role("link", name="mrx11", exact=True).click()
        page.wait_for_url("**/genes/**")
        page.get_by_role("heading", name="mrx11", exact=True).wait_for()
        assert page.locator("dialog").count() == 0
        page.get_by_text("UniProtKB:Q9US57", exact=True).wait_for()
        gene = page.request.get(BASE + "/api/genes/PomBase:SPAC1002.01").json()
        assert page.locator("#annotation-list .gene-annotation").count() == gene["annotation_count"]
        page.screenshot(path=str(output / "gene.png"))
        page.get_by_role("tab", name="Annotations (").click()
        page.locator(".annotation-provenance summary").first.click()
        page.locator(".raw-source pre").first.wait_for()
        assert "PomBase" in page.locator(".raw-source pre").first.inner_text()
        page.locator(".annotation-provenance").first.screenshot(path=str(output / "provenance.png"))
        page.reload(wait_until="networkidle")
        page.get_by_role("heading", name="mrx11", exact=True).wait_for()
        page.get_by_role("link", name="Search results").click()
        page.locator(".row-title").first.wait_for()
        assert page.get_by_role("searchbox").input_value() == "PomBase:SPAC1002.01"
        page.get_by_role("button", name="Reset", exact=True).click()
        page.get_by_role("tab", name="GO terms").click()
        page.get_by_role("searchbox").fill("kinase")
        page.get_by_role("button", name="Search", exact=False).first.click()
        page.wait_for_function("document.querySelector('#status').textContent.includes('kinase')")
        page.locator(".row-title").first.wait_for()
        page.screenshot(path=str(output / "terms.png"), full_page=True)
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        mobile.on("pageerror", lambda error: errors.append(str(error)))
        mobile.goto(BASE + "/?q=TP53", wait_until="networkidle")
        mobile.locator(".row-title").first.wait_for()
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "mobile.png"), full_page=True)
        mobile.get_by_role("link", name="TP53", exact=True).click()
        mobile.get_by_role("heading", name="TP53", exact=True, level=1).wait_for()
        tp53 = mobile.request.get(BASE + "/api/genes/HGNC:11998").json()
        assert tp53["annotation_count"] > 100
        assert mobile.locator("#annotation-list .gene-annotation").count() == tp53["annotation_count"]
        assert mobile.locator("#annotation-list .assessment").count() > 0
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "gene-mobile.png"))
        mobile.get_by_role("tab", name="Annotations (").click()
        mobile.locator("#review-filter").select_option("REMOVE")
        shown = mobile.locator("#annotation-list .gene-annotation").count()
        assert 0 < shown < tp53["annotation_count"]
        assert mobile.locator("#annotation-list .action").first.inner_text() == "REMOVE"
        mobile.locator("#annotations").scroll_into_view_if_needed()
        mobile.screenshot(path=str(output / "gene-review-mobile.png"))
        mobile.get_by_role("button", name="Reset filters", exact=True).click()
        assert mobile.locator("#annotation-list .gene-annotation").count() == tp53["annotation_count"]
        assert not errors, errors
        browser.close()
    (output / "result.json").write_text(json.dumps({"status": "passed", "browser_errors": errors}, indent=2))
    print(f"Browser checks passed; screenshots in {output}")


if __name__ == "__main__":
    main()
