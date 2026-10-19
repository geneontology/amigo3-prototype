"""Check project discovery, reading, entity navigation and responsive layout."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = os.getenv("AMIGO3_BROWSER_URL", "http://127.0.0.1:8030")


def main():
    output = Path("data/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    filters = [{"id": "maturity", "field": "maturity", "values": ["SCOPING"]}]
    start = "/?collection=projects&q=cytosolic&filters=" + quote(json.dumps(filters))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + start, wait_until="networkidle")
        page.locator("#rows .row-title").first.wait_for()
        assert page.get_by_role("tab").count() == 9
        assert page.get_by_role("tab", name="Projects").get_attribute("aria-selected") == "true"
        page.screenshot(path=str(output / "projects.png"))
        page.locator("#rows .row-title").filter(has_text="cGAS-STING").first.click()
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator("dialog").count() == 0
        assert page.locator("#project-text table").count() > 0
        assert page.locator('#project-related a[href^="/genes/"]').count() == 10
        page.screenshot(path=str(output / "project-page.png"))
        detail = page.request.get(BASE + "/api/projects/PROJECT:CGAS_STING_PATHWAY").json()
        source = page.request.get(BASE + detail["source"]["url"])
        assert hashlib.sha256(source.body()).hexdigest() == detail["source"]["sha256"]
        page.reload(wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        page.locator("#back-results").click()
        page.locator("#rows .row-title").first.wait_for()
        assert page.locator("#search").input_value() == "cytosolic"
        assert "SCOPING" in page.url
        page.goto(BASE + "/projects/PROJECT:FAMILIES", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator('#project-related a[href^="/reports/"]').count() > 0
        page.get_by_role("tab", name="Related records (").click()
        page.locator('#project-related a[href^="/reports/"]').first.click()
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator('.report-context a[href^="/projects/"]').count() > 0
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        mobile.goto(BASE + "/projects/PROJECT:CGAS_STING_PATHWAY", wait_until="networkidle")
        mobile.locator("#resource-content").wait_for(state="visible")
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "project-mobile.png"))
        assert not errors, errors
        browser.close()
    print("Project browser checks passed: facets, source bytes, gene/report links and mobile layout.")


if __name__ == "__main__":
    main()
