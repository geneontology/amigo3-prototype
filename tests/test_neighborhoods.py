"""Topology-derived targets stay independent of queries and annotation labels."""

import itertools
import json
import shutil

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.build import build
from amigo3.neighborhoods import EPSILON, MAX_SIZE, RESTART, communities, overview, personalized_pagerank
from amigo3.network_enrichment import ConnectivityTest


def test_partition_reproducibility_connected_membership_and_taxon():
    # Two dense blocks joined by one bridge should remain separate communities.
    edges = list(itertools.combinations("abcde", 2)) + list(itertools.combinations("vwxyz", 2))
    edges += [("e", "v")]
    groups, metadata = communities("NCBITaxon:9606", edges)
    assert {frozenset(gs) for _, gs in groups} == {frozenset("abcde"), frozenset("vwxyz")}
    assert (groups, metadata) == communities("NCBITaxon:9606", [(b, a) for a, b in reversed(edges)])
    assert all(ident.startswith("GN:") for ident, _ in groups)
    assert metadata["reference_edges"] == 21
    assert metadata["reference_nodes"] == 10
    assert metadata["overlapping"] is True
    assert metadata["restart_probability"] == RESTART
    graph = nx.Graph(edges)
    assert all(nx.is_connected(graph.subgraph(gs)) for _, gs in groups)
    other, other_meta = communities("NCBITaxon:10090", edges)
    assert {g for _, gs in groups for g in gs} == {g for _, gs in other for g in gs}
    assert {ident for ident, _ in groups}.isdisjoint(ident for ident, _ in other)
    assert metadata["partition_id"] != other_meta["partition_id"]


@pytest.mark.parametrize("alternative", ["greater", "less"])
def test_neighborhood_score_against_independent_exact_enumeration(alternative):
    edges = list(itertools.combinations("abcd", 2)) + list(itertools.combinations("wxyz", 2))
    groups, _ = communities("taxon", edges)
    memberships = {ident: set(gs) for ident, gs in groups}
    memberships["overlapping-target"] = set("abwx")
    adjacency = {g: set() for g in "abcdwxyz"}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    study = set("abc")
    test = ConnectivityTest(adjacency, study, memberships)
    observed, p_values, null = test.run(1999, 0, alternative)
    samples = [set(gs) for gs in itertools.combinations(adjacency, 3)]
    assert null["samples"] == 56
    for i, members in enumerate(memberships.values()):

        def score(genes, members=members):
            return sum((a in genes and b in members) or (b in genes and a in members) for a, b in edges)

        values = [score(sample) for sample in samples]
        assert observed[i] == score(study)
        assert test.expectation(members) == pytest.approx(sum(values) / len(values))
        extreme = sum(v >= observed[i] if alternative == "greater" else v <= observed[i] for v in values)
        assert p_values[i] == extreme / len(values)


@pytest.fixture
def neighborhood_data(built_data, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(built_data, data)
    repository = data / "review_repo"
    path = repository / "gocams/test/test-src.yaml"
    doc = json.loads(path.read_text())
    doc["activities"] = [
        {
            "id": f"gomodel:test/{i}",
            "enabled_by": {"term": f"HGNC:{i}"},
            "molecular_function": {"term": "GO:0004672"},
            "causal_associations": [
                {
                    "predicate": "RO:0002630",
                    "downstream_activity": f"gomodel:test/{i + 1}",
                    "evidence": [{"reference": "PMID:123"}],
                }
            ]
            if i < 3
            else [],
        }
        for i in range(1, 4)
    ]
    doc["objects"] = [{"id": f"HGNC:{i}", "label": f"GENE{i}"} for i in range(1, 4)]
    path.write_text(json.dumps(doc))
    build(data, repository / "genes", repository)
    return data


@pytest.fixture
def neighborhood_client(neighborhood_data):
    return TestClient(create_app(neighborhood_data, "duckdb"))


def body(**overrides):
    return {"genes": ["GENE1"], "species": "human", "method": "neighborhood", **overrides}


def test_api_neighborhood_identity_membership_context_and_counted_pairs(neighborhood_client):
    c = neighborhood_client
    response = c.post("/api/enrichment", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["parameters"]["target"] == "neighborhood"
    assert result["network"]["kind"] == "neighborhood"
    assert result["counts"]["tests"] == 1
    term = result["terms"][0]
    assert term["id"].startswith("GN:")
    assert term["term_size"] == term["reference_size"] == 3
    assert term["connections"] == 1
    assert term["study_count"] == 1
    assert term["by_fdr"] == 1
    assert result["network"]["neighborhoods"]["nodes"][0]["internal_edges"] == 2
    detail = c.post(
        "/api/enrichment/network/edges",
        json={**body(), "term_id": term["id"], "include_context": True, "view": result["build_id"]},
    ).json()
    assert len(detail["genes"]) == 3
    assert len(detail["edges"]) == 2
    assert detail["counted_connections"] == sum(e["counted"] for e in detail["edges"]) == 1
    assert detail["member_ids"] == ["HGNC:1", "HGNC:2", "HGNC:3"]
    assert all(e["assertions"] for e in detail["edges"])
    # The table inspector returns only scored pairs; the canvas adds context.
    scored = c.post("/api/enrichment/network/edges", json={**body(), "term_id": term["id"]}).json()
    assert len(scored["edges"]) == 1 and scored["edges"][0]["counted"]


def test_partition_and_scores_ignore_annotation_controls_and_partition_ignores_background(
    neighborhood_client,
):
    c = neighborhood_client
    a = c.post("/api/enrichment", json=body()).json()
    b = c.post(
        "/api/enrichment",
        json=body(aspect="F", evidence="experimental", exclude_anti_slim=True, exclude_review_flagged=True),
    ).json()
    assert a["terms"] == b["terms"]
    assert a["network"]["neighborhoods"]["partition_id"] == b["network"]["neighborhoods"]["partition_id"]
    smaller = c.post("/api/enrichment", json=body(background=["GENE1", "GENE2"])).json()
    assert smaller["terms"][0]["id"] == a["terms"][0]["id"]
    assert smaller["terms"][0]["term_size"] == 2
    assert smaller["terms"][0]["reference_size"] == 3
    assert (
        smaller["network"]["neighborhoods"]["partition_id"] == a["network"]["neighborhoods"]["partition_id"]
    )
    other_query = c.post("/api/enrichment", json=body(genes=["GENE2"])).json()
    assert other_query["terms"][0]["id"] == a["terms"][0]["id"]
    assert other_query["terms"][0]["connections"] == 2


def test_annotation_free_genes_still_define_tested_neighborhoods(neighborhood_data, monkeypatch):
    def no_annotations(db, request):
        db.execute(
            "CREATE OR REPLACE TEMP TABLE gene_terms AS SELECT ''::VARCHAR AS gene_id, ''::VARCHAR AS term WHERE false"
        )

    monkeypatch.setattr("amigo3.network_enrichment.gene_sets", no_annotations)
    with TestClient(create_app(neighborhood_data, "duckdb")) as c:
        result = c.post("/api/enrichment", json=body()).json()
    assert result["terms"][0]["term_size"] == 3
    assert result["terms"][0]["go_context"] == []
    assert result["terms"][0]["connections"] == 1


def test_neighborhood_api_validation_and_empty_filters(neighborhood_client):
    c = neighborhood_client
    assert c.post("/api/enrichment", json=body(target="gocam")).status_code == 422
    assert (
        c.post("/api/enrichment", json=body(target="neighborhood", method="hypergeometric")).status_code
        == 422
    )
    empty = c.post("/api/enrichment", json=body(min_term_size=4)).json()
    assert empty["terms"] == []
    assert empty["network"]["neighborhoods"]["communities"] == 1
    assert not empty["network"]["neighborhoods"]["nodes"][0]["tested"]
    assert (
        c.post("/api/enrichment/network/edges", json={**body(), "term_id": "GN:missing"}).status_code == 422
    )
    assert c.post("/api/enrichment", json=body(view="stale")).status_code == 422


def test_empty_and_isolated_nodes_do_not_create_communities():
    groups, metadata = communities("taxon", [("isolated", "isolated")])
    assert groups == ()
    assert metadata["communities"] == 0
    assert metadata["seed_count"] == 0
    assert metadata["definitions"] == {}


def test_pagerank_push_against_independent_power_iteration():
    graph = nx.barbell_graph(4, 2)
    adjacency = {g: tuple(sorted(graph[g])) for g in graph}
    approximate = personalized_pagerank(adjacency, 0)
    exact = {g: float(g == 0) for g in graph}
    for _ in range(1000):
        updated = {
            g: RESTART * (g == 0)
            + (1 - RESTART) / 2 * (exact[g] + sum(exact[n] / len(adjacency[n]) for n in adjacency[g]))
            for g in graph
        }
        delta = sum(abs(updated[g] - exact[g]) for g in graph)
        exact = updated
        if delta < 1e-14:
            break
    assert delta < 1e-14
    assert 0 < sum(approximate.values()) <= 1
    for g in graph:
        error = (exact[g] - approximate.get(g, 0)) / len(adjacency[g])
        assert -1e-12 <= error <= EPSILON + 1e-12


def test_overlapping_neighborhoods_preserve_seeds_and_deduplicate_memberships():
    edges = list(nx.path_graph(list("abcdefg")).edges)
    groups, metadata = communities("taxon", edges)
    members = [set(gs) for _, gs in groups]
    assert any(a & b and a != b for a, b in itertools.combinations(members, 2))
    assert len({tuple(gs) for _, gs in groups}) == len(groups)
    seeds = []
    for ident, genes in groups:
        definition = metadata["definitions"][ident]
        assert set(definition["seed_ids"]) <= set(genes)
        seeds.extend(definition["seed_ids"])
    assert sorted(seeds) == list("abcdefg")
    # Adding a disconnected component must not change the existing boundaries.
    bigger, _ = communities("taxon", edges + [("x", "y")])
    assert set(groups) <= set(bigger)


def test_locality_cap_and_conductance_of_reference_boundaries():
    graph = nx.path_graph([f"g{i:03}" for i in range(80)])
    groups, metadata = communities("taxon", graph.edges)
    assert len(groups) > 1
    assert {g for _, gs in groups for g in gs} == set(graph)
    assert all(2 <= len(gs) <= MAX_SIZE for _, gs in groups)
    for ident, genes in groups:
        assert nx.is_connected(graph.subgraph(genes))
        assert metadata["definitions"][ident]["conductance"] == pytest.approx(nx.conductance(graph, genes))


def test_small_checkpoint_branch_is_not_absorbed_into_large_signaling_region():
    # Synthetic G2M analogue: a checkpoint branch reaches a large signaling
    # region through a multifunctional hub. No annotation labels enter.
    signaling = [f"signal{i:02}" for i in range(55)]
    edges = list(itertools.combinations(signaling, 2))
    edges += [("CDK1", g) for g in signaling]
    edges += [("CDC25A", g) for g in ["CDK1", "CHEK1", "WEE1"]]
    groups, metadata = communities("taxon", edges)
    selected = [set(gs) for ident, gs in groups if "CDC25A" in metadata["definitions"][ident]["seed_ids"]]
    assert selected == [{"CDC25A", "CHEK1", "WEE1"}]
    study = {"CDK1", "CDC25A", "CHEK1"}
    counted = [
        (a, b) for a, b in edges if (a in study and b in selected[0]) or (b in study and a in selected[0])
    ]
    assert len(counted) == 3
    assert {g for pair in counted for g in pair} == {"CDK1", "CDC25A", "CHEK1", "WEE1"}


def test_overview_counts_shared_genes_and_internal_edges_for_every_membership():
    ctx = {
        "communities": {"one": set("abc"), "two": set("bcd"), "three": set("ef")},
        "universe": set("abcef"),  # d disappears with the custom background.
        "used": {"b"},
        "edges": [("a", "b", {}), ("b", "c", {}), ("e", "f", {})],
        "partition": {},
        "descriptions": {k: {"label": k} for k in ["one", "two", "three"]},
    }
    result = overview(ctx, [], set())
    assert result["edge_kind"] == "shared_genes"
    assert result["edges"] == [{"source": "one", "target": "two", "shared_genes": 2}]
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["one"]["internal_edges"] == 2
    assert nodes["two"]["internal_edges"] == 1
    assert nodes["two"]["size"] == 2


def test_correction_family_and_overview_do_not_depend_on_result_truncation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from amigo3.enrichment import EnrichmentRequest
    from amigo3.network_enrichment import enrich_network

    pairs = list(itertools.combinations("abcd", 2)) + list(itertools.combinations("wxyz", 2))
    groups, partition = communities("taxon", pairs)
    memberships = {ident: set(gs) for ident, gs in groups}
    adjacency = {g: set() for g in "abcdwxyz"}
    for a, b in pairs:
        adjacency[a].add(b)
        adjacency[b].add(a)
    ctx = {
        "used": set("abc"),
        "universe": set(adjacency),
        "adjacency": adjacency,
        "memberships": memberships,
        "communities": memberships,
        "partition": partition,
        "term_labels": {ident: ident for ident in memberships},
        "labels": {g: g for g in adjacency},
        "edges": [(a, b, {}) for a, b in pairs],
        "taxon": "taxon",
        "study": {},
        "background": None,
        "coverage": {},
        "descriptions": {
            ident: {"label": ident, "reference_size": 4, "representatives": [], "go_context": []}
            for ident in memberships
        },
        "graph": SimpleNamespace(result=lambda summary: {"policy": {}}),
    }
    monkeypatch.setattr("amigo3.network_enrichment.prepare", lambda path, request: ctx)
    one = enrich_network(tmp_path, EnrichmentRequest(**body(max_results=1)), "test")
    all_results = enrich_network(tmp_path, EnrichmentRequest(**body(max_results=2)), "test")
    assert one["counts"]["tests"] == all_results["counts"]["tests"] == 2
    assert one["terms"] == all_results["terms"][:1]
    assert one["truncated"] and not all_results["truncated"]
    nodes = one["network"]["neighborhoods"]["nodes"]
    assert len(nodes) == 2 and sum(n["returned"] for n in nodes) == 1
    assert all(n["tested"] and n["by_fdr"] is not None for n in nodes)
