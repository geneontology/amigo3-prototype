"""Every collection exposes sorting; term defaults remain explicit and reversible."""

import hashlib
import json
import re
import shutil

import pytest
from playwright.sync_api import expect

from amigo3.api import create_app
from amigo3.build import build


@pytest.mark.parametrize(
    "collection",
    ["genes", "annotations", "terms", "gocams", "modules", "families", "reports", "projects", "rules"],
)
def test_sort_controls_all_collections_and_shareable_urls(page, collection):
    page.goto(f"/?collection={collection}")
    expect(page.locator("#rows .row").first).to_be_visible()
    expect(page.locator("#result-sort")).to_have_value("recommended")
    with page.expect_response(
        lambda r: r.url.endswith("/api/query") and r.request.method == "POST"
    ) as response:
        page.locator("#result-sort").select_option("name_desc")
    assert response.value.request.post_data_json["sort"] == "name_desc"
    assert response.value.status == 200
    expect(page).to_have_url(re.compile("sort=name_desc"))
    page.reload()
    expect(page.locator("#result-sort")).to_have_value("name_desc")
    expect(page.locator("#rows .row").first).to_be_visible()
    page.locator("#result-sort").select_option("name_asc")
    expect(page).to_have_url(re.compile("sort=name_asc"))
    page.go_back()
    expect(page.locator("#result-sort")).to_have_value("name_desc")
    expect(page.locator("#error")).to_be_hidden()


def test_sort_resets_pagination_and_is_remembered_per_tab(page):
    page.goto("/?collection=annotations&sort=id")
    page.locator("#next").click()
    expect(page.locator("#page-label")).to_contain_text("Page 2")
    page.locator("#result-sort").select_option("newest")
    expect(page.locator("#page-label")).to_contain_text("Page 1")
    page.locator('[data-collection="genes"]').click()
    expect(page.locator("#result-sort")).to_have_value("recommended")
    page.locator("#result-sort").select_option("annotations")
    page.locator('[data-collection="annotations"]').click()
    expect(page.locator("#result-sort")).to_have_value("newest")
    page.locator('[data-collection="genes"]').click()
    expect(page.locator("#result-sort")).to_have_value("annotations")


@pytest.fixture(scope="module")
def obsolete_data(browser_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("obsolete") / "data"
    shutil.copytree(browser_data, data)
    manifest = json.loads((data / "sources.json").read_text())
    source = manifest["go"]
    path = data / source["path"]
    path.write_text(
        path.read_text()
        + "\n[Term]\nid: GO:9000999\nname: obsolete kinase example\nnamespace: molecular_function\nis_obsolete: true\n"
    )
    source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (data / "sources.json").write_text(json.dumps(manifest))
    build(data, repository=data / "review_repo")
    return data


def test_slim_ranking_and_reversible_obsolete_filter(page, obsolete_data, serve_app):
    with serve_app(create_app(obsolete_data, "duckdb")) as url:
        page.goto(url + "/?collection=terms")
        expect(page.locator("#rows .row")).to_have_count(4)
        expect(page.locator("#rows .row-title").first).to_have_text("kinase activity")
        expect(page.locator("#chips")).to_contain_text("Obsolete: No")
        page.locator('[data-facet="subsets"]').check()
        expect(page.locator("#rows .row")).to_have_count(1)
        expect(page.locator("#chips")).to_contain_text("goslim_generic")
        page.locator("#clear").click()
        expect(page.locator("#rows .row")).to_have_count(4)
        page.locator("#chips button").filter(has_text="Obsolete").click()
        expect(page.locator("#rows .row")).to_have_count(5)
        page.reload()
        expect(page.locator("#rows .row")).to_have_count(5)
        page.go_back()
        expect(page.locator("#rows .row")).to_have_count(4)
        page.locator("#clear").click()
        expect(page.locator("#chips")).to_contain_text("Obsolete: No")
        # The default search filter must not make an obsolete detail page inaccessible.
        page.goto(url + "/terms/GO%3A9000999")
        expect(page.locator("#resource-content")).to_be_visible()
