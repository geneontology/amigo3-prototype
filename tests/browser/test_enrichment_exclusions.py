"""Optional GO exclusions: off by default, sent to the API, shared in links."""

import re

from playwright.sync_api import expect


def test_enrichment_exclusion_checkboxes(page):
    page.goto("/enrichment")
    anti_slim, flagged = page.locator("#exclude-anti-slim"), page.locator("#exclude-review-flagged")
    expect(anti_slim).not_to_be_checked()
    expect(flagged).not_to_be_checked()
    expect(page.locator("#exclusions")).to_contain_text("gocheck_do_not_annotate")
    expect(page.locator("#exclusions")).to_contain_text("MARK_AS_OVER_ANNOTATED")
    page.locator("#target").select_option("gocam")
    expect(page.locator("#exclusions")).to_be_hidden()
    page.locator("#target").select_option("go")
    expect(anti_slim).to_be_visible()

    page.locator("#genes").fill("GENE1")
    page.locator("#aspect").select_option("F")
    page.locator("#min-size").fill("1")
    flagged.check()
    with page.expect_request(lambda r: r.url.endswith("/api/enrichment") and r.method == "POST") as sent:
        page.locator("#enrichment-form button[type=submit]").click()
    body = sent.value.post_data_json
    assert body["exclude_review_flagged"] is True and body["exclude_anti_slim"] is False
    expect(page).to_have_url(re.compile(r"exclude_review_flagged=1"))
    expect(page).not_to_have_url(re.compile(r"exclude_anti_slim"))
    expect(page.locator("#method")).to_contain_text("Exclude annotations flagged by AI gene review")

    page.goto("/enrichment?genes=GENE1&species=human&aspect=F&min=1&exclude_anti_slim=1")
    expect(page.locator("#exclude-anti-slim")).to_be_checked()
    # Shared links run after the example gene sets load, which can be slow.
    expect(page.locator("#enrichment-results")).to_be_visible(timeout=20000)
    expect(page.locator("#exclude-review-flagged")).not_to_be_checked()
    expect(page.locator("#method")).to_contain_text("Exclude anti-slim terms")
