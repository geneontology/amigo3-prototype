"""The network is the primary result; annotation names never define the targets."""

import re
from copy import deepcopy

from playwright.sync_api import expect

GENES = "document.getElementById('enrichment-network-canvas')._cy"
OVERVIEW = "document.getElementById('neighborhood-overview-canvas')._cy"


def ready(page):
    page.goto("/enrichment?genes=GENE2&species=human&method=neighborhood&overview_min_study=1")
    expect(page.locator("#neighborhood-overview-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()


def test_neighborhood_network_is_primary_and_sources_export_share(page, tmp_path):
    ready(page)
    expect(page.locator("#target")).to_have_value("neighborhood")
    expect(page.locator("#aspect")).to_be_hidden()
    expect(page.locator("#graphs-tab")).to_have_text("Network")
    expect(page).to_have_url(re.compile(r"result_view=graphs"))
    expect(page.locator("#graphs-tab")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#network-summary")).to_contain_text("topology-derived neighborhoods")
    expect(page.locator("#graph-term-score")).to_contain_text("Neighborhood 001")
    expect(page.locator("#graph-term-score")).to_contain_text("does not pass 0.05")
    expect(page.locator("#graph-term-score")).not_to_contain_text("GO:")
    expect(page.locator("#neighborhood-context")).to_contain_text("not enrichment targets")
    expect(page.locator("#count-chart-section")).not_to_have_attribute("open", "")
    assert page.evaluate(OVERVIEW + ".nodes().length") == 1
    assert page.evaluate(OVERVIEW + ".nodes('.significant').length") == 0
    assert page.evaluate(GENES + ".nodes().length") == 2
    assert page.evaluate(GENES + ".nodes('.both').length") == 1
    assert page.evaluate(GENES + ".edges('.counted').length") == 1
    page.locator("#graph-pair").select_option("0")
    expect(page.locator("#network-graph-inspector")).to_contain_text("Counted connection")
    page.locator("#network-graph-inspector summary").click()
    expect(page.locator("#network-graph-inspector pre")).to_contain_text("PMID:123")
    # Clicking the overview selects a topology identity and updates the share URL.
    page.evaluate(OVERVIEW + ".nodes().first().emit('tap')")
    expect(page).to_have_url(re.compile(r"graph_term=GN%3A"))
    shared = page.url
    page.reload()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page).to_have_url(shared)
    with page.expect_download() as png:
        page.locator("#overview-png").click()
    assert png.value.path().read_bytes().startswith(b"\x89PNG")
    with page.expect_download() as tsv:
        page.locator("#download").click()
    dest = tmp_path / "neighborhood.tsv"
    tsv.value.save_as(dest)
    text = dest.read_text()
    assert "# method: neighborhood" in text
    assert "# target: neighborhood" in text
    assert "# neighborhood_partition:" in text
    assert "reference_size\tgo_context" in text
    assert "GN:" in text
    page.locator("#table-tab").click()
    page.locator("#show-all").check()
    expect(page.locator("#term-table")).to_contain_text("Neighborhood 001")
    assert page.locator('#term-table a[href*="/terms/GN"]').count() == 0
    expect(page).to_have_url(re.compile(r"result_view=table"))
    page.reload()
    expect(page.locator("#table-panel")).to_be_visible()
    expect(page.locator("#graphs-panel")).to_be_hidden()


def test_neighborhood_mobile_empty_family_and_switch_to_go_connectivity(page):
    page.set_viewport_size({"width": 390, "height": 844})
    ready(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # No significant scores should not hide the network or its genes.
    expect(page.locator("#show-all")).not_to_be_checked()
    expect(page.locator("#neighborhood-overview")).to_be_visible()
    page.locator("#min-size").fill("3")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#graph-content")).to_be_hidden()
    expect(page.locator("#neighborhood-overview-canvas[data-ready=true]")).to_be_visible()
    assert page.evaluate(OVERVIEW + ".nodes().length") == 0
    assert page.evaluate(GENES + " === undefined")
    page.locator("#analysis-method").select_option("network")
    expect(page.locator("#target")).to_have_value("go")
    expect(page.locator("#aspect")).to_be_visible()
    page.locator("#aspect").select_option("F")
    page.locator("#min-size").fill("1")
    page.locator("#show-all").check()
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#neighborhood-overview")).to_be_hidden()
    expect(page.locator("#graphs-tab")).to_have_text("Graphs")
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#graph-term-score")).to_contain_text("GO:")


def test_neighborhood_stale_view_rejected(page):
    page.goto("/enrichment?genes=GENE2&species=human&method=neighborhood&view=stale")
    expect(page.locator("#enrichment-error")).to_contain_text("pinned build")
    expect(page.locator("#enrichment-results")).to_be_hidden()


def test_switch_from_neighborhood_to_membership_restores_go_controls(page):
    ready(page)
    expect(page.locator("#max-size-label")).to_have_text("Max genes per neighborhood")
    expect(page.locator("#neighborhood-size-help")).to_be_visible()
    page.locator("#analysis-method").select_option("hypergeometric")
    expect(page.locator("#max-size-label")).to_have_text("Max set size")
    expect(page.locator("#neighborhood-size-help")).to_be_hidden()
    expect(page.locator("#target")).to_have_value("go")
    expect(page.locator("#aspect")).to_be_visible()
    expect(page.locator("#evidence")).to_be_visible()
    expect(page.locator("#network-options")).to_be_hidden()
    page.locator("#aspect").select_option("F")
    page.locator("#min-size").fill("1")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#result-tabs")).to_be_hidden()
    expect(page.locator("#table-panel")).to_be_visible()


def test_max_genes_and_show_all_do_not_restore_untested_overview_nodes(page):
    def collection(route):
        if route.request.method != "POST":
            route.continue_()
            return
        payload = route.fetch().json()
        nodes = payload["network"]["neighborhoods"]["nodes"]
        # The API retains the full reference inventory even under max=10.
        nodes.append(
            {
                **deepcopy(nodes[0]),
                "id": "GN:large",
                "size": 44,
                "study_count": 7,
                "tested": False,
                "returned": False,
                "p_value": None,
                "by_fdr": None,
            }
        )
        nodes.append({**deepcopy(nodes[0]), "id": "GN:single", "study_count": 1})
        route.fulfill(json=payload)

    page.route("**/api/enrichment", collection)
    page.goto("/enrichment?genes=GENE1,GENE2&species=human&method=neighborhood&min=2&max=10&show_all=1")
    expect(page.locator("#neighborhood-overview-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#show-all")).to_be_checked()
    expect(page.locator("#max-size")).to_have_value("10")
    expect(page.locator("#overview-status")).to_contain_text("1 of 1 matching")
    assert page.evaluate(OVERVIEW + ".nodes().length") == 1
    assert page.evaluate(OVERVIEW + ".getElementById('GN:large').length") == 0
    assert page.evaluate(OVERVIEW + ".getElementById('GN:single').length") == 0


def test_context_expansion_keeps_score_and_survives_shared_link(page):
    def edges(route):
        payload = route.fetch().json()
        payload["member_ids"].append("HGNC:3")
        if route.request.post_data_json.get("include_context"):
            payload["genes"].append({"id": "HGNC:3", "symbol": "GENE3"})
            extra = deepcopy(payload["edges"][0])
            extra.update(source_gene="HGNC:1", target_gene="HGNC:3", counted=False)
            payload["edges"].append(extra)
        route.fulfill(json=payload)

    page.route("**/api/enrichment/network/edges", edges)
    ready(page)
    score = page.locator("#graph-term-score").inner_text()
    expect(page.locator("#network-full-context")).not_to_be_checked()
    assert page.evaluate(GENES + ".edges('.context').length") == 0
    page.locator("#network-full-context").check()
    expect(page.locator("#network-graph-status")).to_contain_text("1 counted; 1 context only")
    assert page.evaluate(GENES + ".nodes().length") == 3
    assert page.evaluate(GENES + ".edges('.context').length") == 1
    expect(page.locator("#graph-term-score")).to_have_text(score)
    page.locator("#graph-pair").select_option("1")
    expect(page.locator("#network-graph-inspector")).to_contain_text("Internal context only")
    expect(page).to_have_url(re.compile(r"network_context=full"))
    page.reload()
    expect(page.locator("#network-full-context")).to_be_checked()
    expect(page.locator("#network-graph-status")).to_contain_text("1 counted; 1 context only")
    page.locator("#network-full-context").uncheck()
    expect(page.locator("#network-graph-status")).to_contain_text("1 counted; 0 context only")
    expect(page).not_to_have_url(re.compile(r"network_context=full"))
    expect(page.locator("#graph-term-score")).to_have_text(score)


def test_overview_filters_labels_ranking_and_sharing(page):
    posts = []
    page.on(
        "request",
        lambda r: posts.append(r) if r.method == "POST" and r.url.endswith("/api/enrichment") else None,
    )

    def collection(route):
        if route.request.method != "POST":
            route.continue_()
            return
        payload = route.fetch().json()
        prototype = payload["terms"][0]
        node = payload["network"]["neighborhoods"]["nodes"][0]
        payload["terms"] = [
            {**deepcopy(prototype), "id": f"GN:{i:03}", "label": f"Neighborhood {i:03}"} for i in range(1, 66)
        ]
        data = payload["network"]["neighborhoods"]
        data["nodes"] = [
            {
                **deepcopy(node),
                "id": t["id"],
                "label": t["label"],
                "study_count": 7 if i == 1 else 1 if i == 2 else 3,
                "size": 6 if i == 3 else 44,
                "by_fdr": 0.6 if i == 1 else 0.9,
                "p_value": 0.001 if i == 1 else 0.02,
                "go_context": [{"label": "regulation of mitotic cell cycle phase transition"}],
            }
            for i, t in enumerate(payload["terms"], 1)
        ]
        data["edges"] = [{"source": "GN:001", "target": "GN:003", "shared_genes": 2}]
        for term, n in zip(payload["terms"], data["nodes"], strict=True):
            term.update(by_fdr=n["by_fdr"], p_value=n["p_value"], study_count=n["study_count"])
        route.fulfill(json=payload)

    def edges(route):
        request = route.request.post_data_json
        result = page.request.post(
            "/api/enrichment", data={"genes": ["GENE2"], "species": "human", "method": "neighborhood"}
        ).json()
        request["term_id"] = result["terms"][0]["id"]
        route.fulfill(response=route.fetch(post_data=request))

    page.route("**/api/enrichment", collection)
    page.route("**/api/enrichment/network/edges", edges)
    page.goto("/enrichment?genes=GENE2&species=human&method=neighborhood")
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#neighborhood-overview-canvas[data-ready=true]")).to_be_visible()
    assert page.evaluate(OVERVIEW + ".nodes().length") == 20
    assert page.locator("#graph-term option").count() == 65
    assert page.evaluate(OVERVIEW + ".getElementById('GN:002').length") == 0
    assert "mitotic cell cycle" in page.evaluate(OVERVIEW + ".nodes().first().data('label')")
    assert page.evaluate(
        OVERVIEW + ".getElementById('GN:001').width() > " + OVERVIEW + ".getElementById('GN:003').width()"
    )
    expect(page.locator("#overview-status")).to_contain_text("exploratory; BY FDR filter off")
    page.evaluate(OVERVIEW + ".edges().first().emit('tap')")
    expect(page.locator("#overview-status")).to_contain_text("2 shared genes")
    # A selected detail cannot evade the filters or replace a top-N result.
    page.locator("#graph-term").select_option("GN:002")
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    assert page.evaluate(OVERVIEW + ".getElementById('GN:002').length") == 0
    expect(page.locator("#overview-selection-note")).to_contain_text("outside the overview filters")
    score = page.locator("#graph-term-score").inner_text()
    page.locator("#overview-use-fdr").check()
    expect(page.locator("#overview-status")).to_contain_text("No neighborhoods match")
    assert page.evaluate(OVERVIEW + ".nodes().length") == 0
    expect(page.locator("#overview-png")).to_be_disabled()
    page.locator("#threshold").fill("0.7")
    expect(page.locator("#overview-status")).to_contain_text("1 of 1 matching")
    expect(page.locator("#overview-fdr-legend")).to_have_text("BY FDR ≤ 0.7")
    expect(page.locator("#enrichment-stats > div").last).to_contain_text("BY FDR ≤ 0.7")
    expect(page.locator("#enrichment-stats > div").last.locator("strong")).to_have_text("1")
    page.locator("#overview-use-fdr").uncheck()
    page.locator("#threshold").fill("1")
    expect(page.locator("#overview-use-fdr")).to_be_checked()
    expect(page.locator("#overview-status")).to_contain_text("20 of 64 matching")
    page.locator("#overview-rank").select_option("fraction")
    expect(page.locator("#overview-status")).to_contain_text("highest study fraction")
    assert page.evaluate(OVERVIEW + ".nodes().first().id()") == "GN:003"
    page.locator("#overview-limit").select_option("10")
    expect(page.locator("#overview-status")).to_contain_text("10 of 64 matching")
    page.locator("#overview-min-study").fill("4")
    page.locator("#overview-min-study").press("Tab")
    expect(page.locator("#overview-status")).to_contain_text("1 of 1 matching")
    expect(page.locator("#graph-term-score")).to_have_text(score)
    assert len(posts) == 1  # Display changes never rerun enrichment.
    page.reload()
    expect(page.locator("#overview-status")).to_contain_text("1 of 1 matching")
    expect(page.locator("#overview-limit")).to_have_value("10")
    expect(page.locator("#overview-min-study")).to_have_value("4")
    expect(page.locator("#overview-rank")).to_have_value("fraction")
    expect(page.locator("#overview-use-fdr")).to_be_checked()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
