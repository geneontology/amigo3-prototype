"""Range sliders and distributions over structural metrics of modules and GO-CAMs."""

import json
import re
import shutil

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.build import build


def module(index):
    """Module i has i ordered parts, and i-1 variant sets of two routes each."""
    parts = [{"order": step, "node": {"id": f"s{step}", "module_type": "STEP"}} for step in range(index)]
    variant_sets = [
        {
            "id": f"v{n}",
            "selection": "EXACTLY_ONE",
            "variants": [{"id": f"v{n}a", "module_type": "STEP"}, {"id": f"v{n}b", "module_type": "STEP"}],
        }
        for n in range(index - 1)
    ]
    return {
        "id": f"MODULE:shape{index}",
        "title": f"Shape module {index}",
        "status": "DRAFT",
        "module": {"id": "root", "module_type": "PATHWAY", "parts": parts, "variant_sets": variant_sets},
    }


@pytest.fixture(scope="module")
def shape_data(built_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("shapes") / "data"
    shutil.copytree(built_data, data)
    repository = data / "review_repo"
    for index in range(1, 7):
        (repository / f"modules/shape{index}.yaml").write_text(json.dumps(module(index)))
    build(data, repository=repository)
    return data


@pytest.fixture
def shape_url(shape_data, serve_app):
    with serve_app(create_app(shape_data, "duckdb")) as url:
        yield url


@pytest.fixture
def shape_page(browser, shape_url):
    context = browser.new_context(base_url=shape_url, viewport={"width": 1440, "height": 1050})
    errors = []
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert not errors, errors


def test_range_slider_filters_and_round_trips(shape_page):
    page = shape_page
    page.goto("/?collection=modules")
    expect(page.locator("#status strong")).to_have_text("7")
    facet = page.locator('[data-range-field="part_count"]')
    expect(facet.locator("h3")).to_have_text("AND parts")
    # Unit bins for 0..6 parts: the shared fixture module has none, shapes 1-6 have 1..6.
    expect(facet.locator(".range-histogram span")).to_have_count(7)
    low = facet.locator('[data-range-input="low"]')
    expect(low).to_have_value("0")
    low.fill("4")
    low.dispatch_event("change")
    expect(page.locator("#status strong")).to_have_text("3")
    expect(page.locator("#chips button")).to_have_text("AND parts: 4–6 ×")
    # The range is not also offered as a checkbox facet of its endpoint values.
    expect(page.locator('[data-facet-field="part_count"]')).to_have_count(0)
    # The slider keeps its full domain while its own filter is active.
    expect(facet.locator(".range-histogram span.in-range")).to_have_count(3)
    expect(facet.locator('[data-range-thumb="low"]')).to_have_value("4")
    page.reload()
    expect(page.locator("#status strong")).to_have_text("3")
    # Other range facets narrow to the current selection.
    expect(page.locator('[data-range-field="variant_set_count"] [data-range-input="low"]')).to_have_value("3")
    page.locator('[data-range-field="part_count"] [data-range-clear]').click()
    expect(page.locator("#status strong")).to_have_text("7")
    expect(page.locator("#chips button")).to_have_count(0)


def test_dashboard_distributions_and_joint_heatmap(shape_page):
    page = shape_page
    page.goto("/?collection=modules&view=dashboard")
    expect(page.locator("#dashboard-total")).to_have_text("7")
    expect(page.locator(".distribution-card")).to_have_count(6)
    card = page.locator(".distribution-card").filter(has_text="Realizations")
    expect(card.locator(".chart-heading span")).to_contain_text("log-spaced bins")
    joint = page.locator(".joint-heatmap")
    expect(joint.locator("caption")).to_contain_text("AND parts (rows) × OR branch points (columns)")
    page.locator("#dashboard-joint-y").select_option("max_depth")
    expect(page).to_have_url(re.compile("jy=max_depth"))
    expect(joint.locator("caption")).to_contain_text("Nesting depth")
    # A histogram column is a range filter on that bin.
    page.locator(".distribution-card").filter(has_text="AND parts").locator(".histogram-column").last.click()
    expect(page.locator("#dashboard-total")).to_have_text("1")
    expect(page.locator("#chips button")).to_have_text("AND parts: 6 ×")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_gocam_structure_facets(shape_page):
    page = shape_page
    page.goto("/?collection=gocams")
    labels = page.locator(".range-group .facet h3")
    expect(labels).to_have_text(
        [
            "Activities",
            "Enabling genes / complexes",
            "Causal edges",
            "Longest causal path",
            "Disconnected subgraphs",
        ]
    )
    page.locator("#result-sort").select_option("longest_path")
    expect(page.locator("#rows .row")).to_have_count(1)
