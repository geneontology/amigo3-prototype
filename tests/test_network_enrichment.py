import itertools
import math
import random

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.enrichment import benjamini_hochberg
from amigo3.network_enrichment import ConnectivityTest


def graph(edges, nodes=()):
    adjacency = {g: set() for g in nodes}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    return adjacency


def naive_score(adjacency, a, b):
    return sum(
        (u in a and v in b) or (v in a and u in b)
        for u, neighbors in adjacency.items()
        for v in neighbors
        if u < v
    )


def reference_sets(adjacency, study):
    # Independent, brute-force reference over all same-sized subsets.
    degrees = sorted(len(adjacency[g]) for g in study)
    return [
        set(gs)
        for gs in itertools.combinations(adjacency, len(study))
        if sorted(len(adjacency[g]) for g in gs) == degrees
    ]


@pytest.mark.parametrize("alternative", ["greater", "less"])
def test_exact_statistics_and_expectation_against_brute_force_with_overlap(alternative):
    adjacency = graph([("a", "b"), ("b", "c"), ("c", "d"), ("d", "a"), ("a", "c"), ("d", "e")])
    terms = {"overlap": {"a", "b", "c"}, "disjoint": {"e"}, "whole": set(adjacency)}
    for study in (set("ab"), set("bc"), set("ace"), set(adjacency)):
        test = ConnectivityTest(adjacency, study, terms)
        obs, p, null = test.run(999, 0, alternative)
        samples = reference_sets(adjacency, study)
        assert null["sampling"] == "exact" and null["samples"] == len(samples)
        for i, members in enumerate(terms.values()):
            scores = [naive_score(adjacency, sample, members) for sample in samples]
            expected = sum(scores) / len(scores)
            observed = naive_score(adjacency, study, members)
            assert obs[i] == observed
            assert test.expectation(members) == pytest.approx(expected, abs=1e-12)
            extreme = sum(v >= observed if alternative == "greater" else v <= observed for v in scores)
            assert p[i] == extreme / len(samples)


def test_overlap_does_not_count_edges_that_only_touch_a_shared_endpoint():
    adjacency = graph([("a", "b"), ("b", "c")])
    test = ConnectivityTest(adjacency, {"a"}, {"self": {"a"}, "neighbor": {"b"}})
    assert test.scores({"a"}) == [0, 1]
    test = ConnectivityTest(adjacency, {"a", "b"}, {"overlap": {"a", "b"}})
    assert test.scores({"a", "b"}) == [1]


def test_exact_null_calibration_and_multiple_testing_for_overlapping_terms():
    adjacency = graph([(str(i), str((i + 1) % 8)) for i in range(8)])
    terms = {"one": {"0", "1"}, "two": {"0", "1", "2"}, "three": {"3", "4", "5"}}
    all_sets = list(itertools.combinations(adjacency, 3))
    for alternative in ("greater", "less"):
        p_values, corrected = [], []
        for study in all_sets:
            _, p, _ = ConnectivityTest(adjacency, set(study), terms).run(999, 0, alternative)
            p_values.append(p)
            bh = benjamini_hochberg(p, len(terms))
            corrected.append([min(1, v * sum(1 / i for i in range(1, len(terms) + 1))) for v in bh])
        for alpha in (0.01, 0.05, 0.1, 0.25, 0.5):
            for i in range(len(terms)):
                assert sum(p[i] <= alpha for p in p_values) / len(all_sets) <= alpha + 1e-12
            assert sum(any(v <= alpha for v in p) for p in corrected) / len(all_sets) <= alpha + 1e-12


def test_monte_carlo_is_reproducible_nonzero_and_preserves_degrees():
    adjacency = graph([(str(i), str((i + 1) % 30)) for i in range(30)])
    study = {str(i) for i in range(10)}
    terms = {"local": {str(i) for i in range(10)}, "remote": {"20", "21"}}
    test = ConnectivityTest(adjacency, study, terms)
    result = test.run(199, 42, "greater")
    assert result == test.run(199, 42, "greater")
    observed, p, null = result
    assert null["sampling"] == "monte_carlo"
    assert null["possible_sets"] == str(math.comb(30, 10))
    assert all(1 / 200 <= value <= 1 for value in p)
    rng = random.Random(42)
    values = [
        naive_score(adjacency, set(rng.sample(sorted(adjacency), 10)), terms["local"]) for _ in range(199)
    ]
    assert p[0] == (1 + sum(v >= observed[0] for v in values)) / 200


def test_unique_hub_is_fixed_and_cannot_be_significant():
    adjacency = graph([("hub", str(i)) for i in range(10)])
    test = ConnectivityTest(adjacency, {"hub"}, {"leaves": {str(i) for i in range(10)}})
    obs, p, null = test.run(1999, 0, "greater")
    assert obs == [10] and p == [1]
    assert test.expectation(set(adjacency) - {"hub"}) == 10
    assert null["possible_sets"] == "1" and null["fixed_study_genes"] == ["hub"]


def test_different_pairings_with_identical_genes_and_degrees_change_enrichment():
    connected = graph([("a", "c"), ("b", "d"), ("e", "f"), ("g", "h")])
    rewired = graph([("a", "b"), ("c", "d"), ("e", "f"), ("g", "h")])
    study, terms = {"a", "b"}, {"process": {"c", "d"}}
    one = ConnectivityTest(connected, study, terms).run(1999, 0, "greater")
    two = ConnectivityTest(rewired, study, terms).run(1999, 0, "greater")
    assert set(connected) == set(rewired)
    assert {g: len(ns) for g, ns in connected.items()} == {g: len(ns) for g, ns in rewired.items()}
    assert one[0] == [2] and one[1] == [1 / 28]  # Detectable connectivity with zero gene overlap.
    assert two[0] == [0] and two[1] == [1]


@pytest.fixture
def client(megamodel_data):
    return TestClient(create_app(megamodel_data, "duckdb"))


def payload(**kwargs):
    return {
        "genes": ["GENE2"],
        "species": "human",
        "method": "network",
        "aspect": "F",
        "min_term_size": 1,
        **kwargs,
    }


def test_api_finds_connections_without_gene_overlap_and_preserves_evidence(client):
    options = client.get("/api/enrichment/network").json()
    assert any(t["taxon"] == "NCBITaxon:9606" for t in options["taxa"])
    request = payload(genes=["GENE2", "HGNC:2", "missing", "GENE3"], network_seed=7)
    response = client.post("/api/enrichment", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["counts"]["population"] == 2 and result["counts"]["study"] == 1
    assert result["study"]["duplicates"] == ["HGNC:2"]
    assert result["study"]["not_in_network"] == ["HGNC:3"]
    assert result["study"]["unresolved"] == ["missing"]
    term = next(t for t in result["terms"] if t["id"] == "GO:0004672")
    assert term["connections"] == 1 and term["study_count"] == 0
    assert term["expected"] == 0.5 and term["p_value"] == 0.5
    assert term["overlap_genes"] == []
    assert term["neighbors"] == [{"id": "HGNC:1", "symbol": "GENE1"}]
    assert term["genes"] == [{"id": "HGNC:2", "symbol": "GENE2"}]
    assert result["network"]["null"]["sampling"] == "exact"
    assert all(t["by_fdr"] >= t["fdr"] for t in result["terms"])
    edges = client.post(
        "/api/enrichment/network/edges", json={**request, "term_id": term["id"], "view": result["build_id"]}
    )
    assert edges.status_code == 200
    detail = edges.json()
    assert len(detail["edges"]) == term["connections"]
    assert len(detail["edges"][0]["assertions"]) == 2
    assert detail["edges"][0]["assertions"][0]["assertion"]["evidence"] == [{"reference": "PMID:123"}]
    assert len(detail["activities"]) == 4
    assert all(m["source"]["sha256"] for m in detail["models"])


def test_filters_change_membership_not_topology_and_custom_background_induces_graph(client):
    all_result = client.post("/api/enrichment", json=payload()).json()
    iba = client.post("/api/enrichment", json=payload(evidence="iba")).json()
    assert iba["counts"]["population"] == all_result["counts"]["population"]
    assert iba["network"]["coverage"] == all_result["network"]["coverage"]
    assert len(iba["terms"]) <= len(all_result["terms"])
    custom = client.post("/api/enrichment", json=payload(background=["GENE2"])).json()
    assert custom["counts"]["study"] == 0 and custom["terms"] == []
    assert custom["study"]["isolated_in_background"] == ["HGNC:2"]
    assert custom["background"]["isolated_in_background"] == ["HGNC:2"]
    assert custom["network"]["coverage"]["background_network_edges"] == 0
    empty = client.post("/api/enrichment", json=payload(background=[])).json()
    assert empty["counts"]["population"] == 0
    assert empty["study"]["outside_custom_background"] == ["HGNC:2"]
    depleted = client.post(
        "/api/enrichment", json=payload(genes=["GENE1"], network_alternative="less")
    ).json()
    term = next(t for t in depleted["terms"] if t["id"] == "GO:0004672")
    assert term["connections"] == 0 and term["p_value"] == 0.5
    assert depleted["counts"]["tests"] == all_result["counts"]["tests"]
    truncated = client.post("/api/enrichment", json=payload(max_results=1)).json()
    assert truncated["counts"]["tests"] == all_result["counts"]["tests"]
    assert truncated["terms"][0]["by_fdr"] == all_result["terms"][0]["by_fdr"]


@pytest.mark.parametrize(
    "change",
    [
        {"target": "gocam"},
        {"species": "missing"},
        {"network_taxon": "NCBITaxon:10090"},
        {"min_term_size": 10, "max_term_size": 2},
        {"network_permutations": 0},
        {"network_permutations": 20000},
        {"network_seed": -1},
        {"network_alternative": "bad"},
        {"view": "stale"},
    ],
)
def test_network_validation(client, change):
    assert client.post("/api/enrichment", json=payload(**change)).status_code == 422


def test_edge_inspection_validation(client):
    for change in ({"view": "stale"}, {"method": "hypergeometric"}, {"term_id": "GO:0008150"}):
        assert (
            client.post(
                "/api/enrichment/network/edges", json={**payload(), "term_id": "GO:0004672", **change}
            ).status_code
            == 422
        )
