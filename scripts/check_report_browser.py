"""Exercise report filtering, full pages, snapshots and responsive reading."""

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
    filters = [{"id": "provider", "field": "provider", "values": ["openscientist"]}]
    start = "/?collection=reports&q=pol5&filters=" + quote(json.dumps(filters))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + start, wait_until="networkidle")
        page.locator("#rows .row-title").first.wait_for()
        assert page.get_by_role("tab", name="Reports").get_attribute("aria-selected") == "true"
        page.screenshot(path=str(output / "reports.png"))
        page.locator("#rows .row-title").filter(has_text="Direct Pol I").first.click()
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator("dialog").count() == 0
        assert page.locator("#report-text table").count() > 0
        assert "openscientist" in page.locator("#resource-kind").inner_text()
        assert page.locator('.report-context a[href^="/genes/"]').count() > 0
        page.screenshot(path=str(output / "report-page.png"))
        identifier = page.url.split("/reports/")[1].split("?")[0]
        detail = page.request.get(BASE + "/api/reports/" + identifier).json()
        pdf = next(f for f in detail["files"] if f["filename"].endswith(".pdf"))
        downloaded = page.request.get(BASE + pdf["url"])
        assert hashlib.sha256(downloaded.body()).hexdigest() == pdf["sha256"]
        page.reload(wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        page.locator("#back-results").click()
        page.locator("#rows .row-title").first.wait_for()
        assert page.locator("#search").input_value() == "pol5"
        assert "openscientist" in page.url
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        mobile.goto(BASE + "/reports/" + identifier, wait_until="networkidle")
        mobile.locator("#resource-content").wait_for(state="visible")
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
        mobile.screenshot(path=str(output / "report-mobile.png"))
        assert not errors, errors
        browser.close()
    print("Report browser checks passed, including PDF checksum and mobile layout.")


if __name__ == "__main__":
    main()
