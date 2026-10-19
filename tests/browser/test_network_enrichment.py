import re

from playwright.sync_api import expect


def test_network_gene_set_workflow_edges_export_and_share(page, tmp_path):
    page.goto(
        "/enrichment?genes=GENE2,GENE3,missing&species=human&aspect=F&min=1&method=network&network_seed=17&network_permutations=199&run=0"
    )
    expect(page.locator("#network-options")).to_be_visible()
    expect(page.locator("#network-taxon")).to_have_value("NCBITaxon:9606")
    expect(page.locator("#network-seed")).to_have_value("17")
    expect(page.locator("#network-permutations")).to_have_value("199")
    expect(page.locator("#target option[value=gocam]")).to_have_attribute("disabled", "")
    with page.expect_response(
        lambda r: r.url.endswith("/api/enrichment") and r.request.method == "POST"
    ) as response:
        page.locator("#enrichment-form button[type=submit]").click()
    data = response.value.json()
    expect(page.locator("#network-summary")).to_be_visible()
    expect(page.locator("#network-summary")).to_contain_text(
        "2 connected background genes and 1 distinct pairs"
    )
    expect(page.locator("#network-summary")).to_contain_text("Exhaustive comparison of 2")
    expect(page.locator("#mapping")).to_contain_text("Absent from the mapped network")
    expect(page.locator("#mapping")).to_contain_text("GENE3")
    expect(page.locator("#mapping")).to_contain_text("Not found")
    expect(page.locator("#study-column")).to_have_text("Connections")
    expect(page.locator("#score-column")).to_have_text("BY FDR")
    page.locator("#show-all").check()
    row = page.locator("#term-table tbody tr").filter(has=page.locator('a[href="/terms/GO%3A0004672"]'))
    expect(row).to_contain_text("Direct overlap: none")
    expect(row).to_contain_text("Term neighbors: GENE1")
    with page.expect_response("**/api/enrichment/network/edges") as detail:
        row.locator(".network-evidence > summary").click()
    assert len(detail.value.json()["edges"]) == 1
    expect(row.locator(".network-evidence")).to_contain_text("RO:0002630")
    expect(row.locator(".network-evidence")).to_contain_text("Evidence and context")
    row.get_by_text("Evidence and context", exact=True).click()
    expect(row.locator("pre")).to_contain_text("PMID:123")
    expect(row.locator("pre")).to_contain_text("sha256")
    assert row.locator('.network-evidence a[href*="#activity-"]').count() == 2
    with page.expect_download() as downloaded:
        page.locator("#download").click()
    destination = tmp_path / "network.tsv"
    downloaded.value.save_as(destination)
    text = destination.read_text()
    assert "# method: network" in text and "# network_seed: 17" in text
    assert "connections\tby_fdr\tneighbor_ids\toverlap_ids" in text
    assert "# network_null:" in text and "# network_policy:" in text
    assert "# counts:" in text and "# truncated: false" in text
    expect(page).to_have_url(re.compile(r"network_taxon=NCBITaxon%3A9606"))
    expect(page).to_have_url(re.compile(r"view="))
    # Follow a real source link, then return via the exact shared URL.
    shared_url = page.url
    row.locator('.network-evidence a[href*="#activity-"]').first.click()
    expect(page.locator('[id="activity-gomodel:test/a"]')).to_be_in_viewport()
    with page.expect_response(
        lambda r: r.url.endswith("/api/enrichment") and r.request.method == "POST"
    ) as replay:
        page.goto(shared_url)
    assert replay.value.json()["terms"] == data["terms"]
    expect(page.locator("#network-seed")).to_have_value("17")


def test_depletion_custom_background_and_switching_methods(page):
    page.goto("/enrichment?genes=GENE1&species=human&aspect=F&min=1&method=network&network_alternative=less")
    expect(page.locator("#terms-heading")).to_have_text("GO terms with depleted connectivity")
    expect(page.locator("#network-alternative")).to_have_value("less")
    page.locator("#show-all").check()
    expect(page.locator("#term-table")).to_contain_text("GO:0004672")
    page.locator("#background-panel summary").click()
    page.locator("#background").fill("GENE1")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#mapping")).to_contain_text("No connections in the background graph")
    expect(page.locator("#network-summary")).to_contain_text("No test was run")
    page.locator("#analysis-method").select_option("hypergeometric")
    expect(page.locator("#network-options")).to_be_hidden()
    expect(page.locator("#target option[value=gocam]")).to_be_enabled()
    page.locator("#background").fill("")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#network-summary")).to_be_hidden()
    expect(page.locator("#study-column")).to_have_text("Study")
    expect(page.locator("#score-column")).to_have_text("FDR")


def test_network_mobile_and_unavailable_species(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network")
    expect(page.locator("#network-summary")).to_be_visible()
    page.locator("#show-all").check()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#species").select_option("pombe")
    expect(page.locator("#network-options-status")).to_contain_text("No catalog-mapped GO-CAM network")


def test_network_source_text_is_escaped(page):
    def inject(route):
        data = route.fetch().json()
        for m in data["models"]:
            m["label"] = '<img src=x onerror="window.bad=true">'
        route.fulfill(json=data)

    page.route("**/api/enrichment/network/edges", inject)
    page.goto("/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network")
    expect(page.locator("#network-summary")).to_be_visible()
    page.locator("#show-all").check()
    page.locator(".network-evidence > summary").first.click()
    expect(page.locator(".network-evidence-body").first).to_contain_text("<img src=x")
    assert page.locator(".network-evidence-body img").count() == 0
    assert page.evaluate("window.bad === undefined")


def test_invalid_shared_network_taxon_is_not_silently_replaced(page):
    page.goto("/enrichment?genes=GENE2&species=human&method=network&network_taxon=NCBITaxon:999")
    expect(page.locator("#network-taxon")).to_have_value("NCBITaxon:999")
    expect(page.locator("#enrichment-error")).to_contain_text("selected network taxon is not available")
    page.locator("#network-taxon").select_option("NCBITaxon:9606")
    page.locator("#aspect").select_option("F")
    page.locator("#min-size").fill("1")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#network-summary")).to_be_visible()
    expect(page.locator("#enrichment-error")).to_be_hidden()


NETWORK_CY = "document.getElementById('enrichment-network-canvas')._cy"


def graph_ready(page):
    page.goto(
        "/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network&show_all=1&result_view=graphs"
    )
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()


def test_network_graph_counts_roles_evidence_export_and_shared_view(page):
    graph_ready(page)
    expect(page.locator("#graphs-tab")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#table-panel")).to_be_hidden()
    expect(page.locator("#graph-term-score")).to_contain_text("1 connections")
    expect(page.locator("#graph-term-score")).to_contain_text("does not pass 0.05")
    assert page.evaluate(NETWORK_CY + ".nodes().length") == 2
    assert page.evaluate(NETWORK_CY + ".edges().length") == 1
    assert page.evaluate(NETWORK_CY + ".nodes('.study').length") == 1
    assert page.evaluate(NETWORK_CY + ".nodes('.neighbor').length") == 1
    assert page.evaluate(NETWORK_CY + ".nodes('.both').length") == 0
    assert page.evaluate(NETWORK_CY + ".edges().every(e=>e.style('target-arrow-shape') === 'none')")
    expect(page.locator(".connection-chart-row").first).to_contain_text("1 / 0.50")
    page.locator("#graph-pair").select_option("0")
    expect(page.locator("#network-graph-inspector")).to_contain_text("RO:0002630")
    page.locator("#network-graph-inspector summary").first.click()
    expect(page.locator("#network-graph-inspector pre")).to_contain_text("PMID:123")
    expect(page.locator("#network-graph-inspector pre")).to_contain_text("sha256")
    page.evaluate(NETWORK_CY + ".nodes('.study').first().emit('tap')")
    expect(page.locator("#network-graph-inspector")).to_contain_text("Study gene")
    with page.expect_download() as download:
        page.locator("#network-png").click()
    assert download.value.path().read_bytes().startswith(b"\x89PNG")
    page.locator("#network-fit").click()
    page.locator("#graph-term").select_option("GO:0004672")
    expect(page).to_have_url(re.compile(r"graph_term=GO%3A0004672"))
    shared_url = page.url
    page.reload()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#graph-term")).to_have_value("GO:0004672")
    expect(page).to_have_url(shared_url)
    # Accessible tabs support keyboard navigation and retain the selected graph.
    page.locator("#graphs-tab").focus()
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#table-tab")).to_be_focused()
    expect(page.locator("#table-panel")).to_be_visible()
    page.keyboard.press("End")
    expect(page.locator("#graphs-tab")).to_be_focused()
    expect(page.locator("#graphs-panel")).to_be_visible()


def test_network_graph_filter_empty_and_mobile(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network")
    page.locator("#graphs-tab").click()
    expect(page.locator("#graph-filter-note")).to_contain_text("No terms under")
    expect(page.locator("#graph-content")).to_be_hidden()
    page.locator("#show-all").check()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#show-all").uncheck()
    expect(page.locator("#graph-content")).to_be_hidden()
    assert page.evaluate(NETWORK_CY + " === undefined")
    page.locator("#analysis-method").select_option("hypergeometric")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#result-tabs")).to_be_hidden()
    expect(page.locator("#table-panel")).to_be_visible()


def test_network_graph_overlap_and_rerun_uses_new_pairs(page):
    graph_ready(page)
    page.locator("#genes").fill("GENE1,GENE2")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#network-summary")).to_contain_text("Exhaustive comparison")
    page.wait_for_function(NETWORK_CY + "?.nodes('.both').length === 1")
    assert page.evaluate(NETWORK_CY + ".nodes('.study').length") == 1
    # No gene is in both sets merely because its neighbor is annotated.
    assert page.evaluate(NETWORK_CY + ".nodes('.neighbor').length") == 0
    page.locator("#background-panel summary").click()
    page.locator("#background").fill("GENE1")
    page.locator("#enrichment-form button[type=submit]").click()
    expect(page.locator("#graph-content")).to_be_hidden()
    assert page.evaluate(NETWORK_CY + " === undefined")


def test_network_graph_error_retry_and_escaped_source(page):
    attempts = 0

    def edges(route):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            route.fulfill(status=503, json={"detail": "Temporary source error"})
        else:
            data = route.fetch().json()
            data["models"][0]["label"] = '<img src=x onerror="window.graphBad=true">'
            route.fulfill(json=data)

    page.route("**/api/enrichment/network/edges", edges)
    page.goto(
        "/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network&show_all=1&result_view=graphs"
    )
    expect(page.locator("#network-graph-status")).to_contain_text("Temporary source error")
    page.locator("#network-retry").click()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    page.locator("#graph-pair").select_option("0")
    expect(page.locator("#network-graph-inspector")).to_contain_text("<img src=x")
    assert page.locator("#network-graph-inspector img").count() == 0
    assert page.evaluate("window.graphBad === undefined")


def test_network_graph_library_failure_keeps_pair_evidence(page):
    page.route("**/vendor/cytoscape.min.js", lambda route: route.abort())
    page.goto(
        "/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network&show_all=1&result_view=graphs"
    )
    expect(page.locator("#network-graph-status")).to_contain_text("Pair evidence remains available")
    page.locator("#graph-pair").select_option("0")
    expect(page.locator("#network-graph-inspector")).to_contain_text("RO:0002630")
    page.unroute("**/vendor/cytoscape.min.js")
    page.locator("#network-retry").click()
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()


def test_network_graph_zero_connections_have_no_canvas(page):
    page.goto(
        "/enrichment?genes=GENE1&species=human&aspect=F&min=1&method=network"
        "&network_alternative=less&show_all=1&result_view=graphs&graph_term=GO:0004672"
    )
    expect(page.locator("#network-graph-status")).to_contain_text("No contributing pairs")
    expect(page.locator("#graph-term-score")).to_contain_text("0 connections")
    expect(page.locator("#network-png")).to_be_disabled()
    assert page.evaluate(NETWORK_CY + " === undefined")


def test_network_graph_late_term_response_cannot_replace_selection(page):
    pending = []

    def hold_first(route):
        if not pending:
            pending.append((route, route.fetch().json()))
        else:
            route.continue_()

    page.route("**/api/enrichment/network/edges", hold_first)
    page.goto(
        "/enrichment?genes=GENE2&species=human&aspect=F&min=1&method=network"
        "&show_all=1&result_view=graphs&graph_term=GO:0004672"
    )
    expect(page.locator("#network-graph-status")).to_contain_text("Loading contributing pairs")
    page.wait_for_timeout(100)  # Allow the intercepted local source response to arrive.
    page.locator("#graph-term").select_option("GO:0016301")
    expect(page.locator("#enrichment-network-canvas[data-ready=true]")).to_be_visible()
    expect(page.locator("#graph-term-score")).to_contain_text("GO:0016301")
    assert len(pending) == 1
    route, data = pending[0]
    data["genes"][0]["symbol"] = "STALE_SOURCE"
    route.fulfill(json=data)
    # A later UI turn ensures the delayed fetch callback had an opportunity to run.
    page.locator("#table-tab").click()
    page.locator("#graphs-tab").click()
    expect(page.locator("#graph-term")).to_have_value("GO:0016301")
    assert "STALE_SOURCE" not in page.evaluate(NETWORK_CY + ".nodes().map(n=>n.data('label'))")
