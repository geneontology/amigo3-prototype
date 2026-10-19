"""Exercise mapping interpretation, condition logic, source links and mobile pages."""

import hashlib
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
        page.goto(BASE + "/?collection=rules&q=IPR000719", wait_until="networkidle")
        page.locator("#rows .row-title").first.wait_for()
        assert page.get_by_role("tab", name="Rules & mappings").get_attribute("aria-selected") == "true"
        page.screenshot(path=str(output / "rules.png"))
        page.locator("#rows .row-title").first.click()
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator("dialog").count() == 0
        assert "Not" in page.locator("#mapping").inner_text()
        assert "exactMatch rows endorse" in page.locator("#interpretation").inner_text()
        page.screenshot(path=str(output / "mapping-page.png"))
        source = page.locator("#sources a").first.get_attribute("href")
        content = page.request.get(BASE + source).body()
        assert hashlib.sha256(content).hexdigest() == source.split("/")[-1]
        page.locator("#back-results").click()
        page.locator("#rows .row-title").first.wait_for()
        assert page.locator("#search").input_value() == "IPR000719"
        page.goto(BASE + "/rules/RULE:ARBA00026799", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        assert page.locator("#conditions .activity-card").count() == 6
        assert page.locator("#conditions .logic-or").count() == 5
        page.get_by_role("tab", name="Review", exact=True).click()
        assert page.locator("#review").is_visible()
        page.screenshot(path=str(output / "rule-page.png"))
        page.goto(BASE + "/rules/RULE:ARBA00026302", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        assert "Antecedent not captured" in page.locator("#conditions").inner_text()
        page.goto(BASE + "/projects/PROJECT:PROTEOSTASIS", wait_until="networkidle")
        page.locator("#resource-content").wait_for(state="visible")
        page.get_by_role("tab", name="Related records (").click()
        page.locator("#project-related a").filter(has_text="Browse all 2,029").click()
        page.locator("#rows .row-title").first.wait_for()
        assert "project_ids" in page.url
        pn = page.request.post(
            BASE + "/api/query",
            data=dict(
                collection="rules",
                filters=[dict(id="scope", field="mapping_scope", values=["ok_for_propagation_to_go"])],
            ),
        ).json()["hits"][0]
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        for path in ["/?collection=rules", "/rules/" + quote(pn["id"]), "/rules/RULE:ARBA00026799"]:
            mobile.goto(BASE + path, wait_until="networkidle")
            mobile.locator(
                "#rows .row-title" if path.startswith("/?") else "#resource-content"
            ).first.wait_for(state="visible")
            assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
        mobile.screenshot(path=str(output / "rule-mobile.png"))
        assert not errors, errors
        browser.close()
    print("Rule/mapping browser checks passed")


if __name__ == "__main__":
    main()
