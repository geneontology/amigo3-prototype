"""GO-CAM hero header surfaces the model's biological processes as linked chips."""

import pytest
from playwright.sync_api import expect


def with_processes(count):
    def handler(route):
        response = route.fetch()
        data = response.json()
        data["processes"] = [
            {"id": f"GO:{i:07d}", "label": f"process {i}", "activity_count": count - i} for i in range(count)
        ]
        route.fulfill(response=response, json=data)

    return handler


def test_model_without_processes_has_no_chips(page):
    page.goto("/gocams/gomodel:test")
    expect(page.locator("#resource-title")).to_have_text("Test causal model")
    expect(page.locator("#resource-processes")).to_be_hidden()


@pytest.mark.parametrize("width", [1440, 390])
def test_process_chips_link_to_terms_and_cap_with_more_toggle(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.route("**/api/resources/gocams/*", with_processes(9))
    page.goto("/gocams/gomodel:test")
    hero = page.locator("#resource-processes")
    expect(hero).to_be_visible()
    expect(hero).to_contain_text("Biological processes")
    chips = hero.locator(".process-chip:visible")
    expect(chips).to_have_count(6)
    expect(chips.first).to_have_text("process 0")
    expect(chips.first).to_have_attribute("href", "/terms/GO%3A0000000")
    toggle = hero.get_by_role("button", name="+3 more")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(chips).to_have_count(9)
    expect(hero.get_by_role("button", name="Show fewer")).to_have_attribute("aria-expanded", "true")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    hero.get_by_role("button", name="Show fewer").click()
    expect(chips).to_have_count(6)
    chips.first.click()
    page.wait_for_url("**/terms/GO%3A0000000")


def test_single_process_has_no_toggle(page):
    page.route("**/api/resources/gocams/*", with_processes(1))
    page.goto("/gocams/gomodel:test")
    hero = page.locator("#resource-processes")
    expect(hero).to_contain_text("Biological process")
    expect(hero).not_to_contain_text("processes")
    expect(hero.locator(".process-chip")).to_have_count(1)
    expect(hero.locator("button")).to_have_count(0)
