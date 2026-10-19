import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.cli import main
from amigo3.gene_network import GeneNetwork, gene_network
from amigo3.megamodels import ModelIndex

HUMAN = "NCBITaxon:9606"


def gene(identifier, aliases=(), taxon=HUMAN, **kwargs):
    return {
        "id": identifier,
        "symbol": identifier,
        "identifiers": list(aliases),
        "taxon_id": taxon,
        "record_kind": "gene",
        "annotation_count": 1,
        **kwargs,
    }


def activity(identifier, enabler, downstream=None, predicate="RO:0002629", **kwargs):
    return {
        "id": identifier,
        "enabled_by": {"type": "EnabledByGeneProductAssociation", "term": enabler},
        "causal_associations": [
            {"predicate": predicate, "downstream_activity": downstream, "evidence": [{"reference": "PMID:1"}]}
        ]
        if downstream
        else [],
        **kwargs,
    }


def model(identifier, activities, taxon=HUMAN):
    return {"id": identifier, "taxon": taxon, "status": "production", "activities": activities}


def project(models, genes=()):
    index = ModelIndex((m, {"path": m["id"] + ".yaml", "sha256": "test-checksum"}) for m in models)
    return GeneNetwork(index, genes).result()


def test_union_deduplicates_pairs_without_losing_direction_context_or_isolates():
    models = [
        model(
            "m1",
            [
                activity("a", "UniProtKB:P1", "b", occurs_in={"term": "GO:0005829"}),
                activity("b", "UniProtKB:P2"),
                activity("c", "UniProtKB:P3"),
            ],
        ),
        model("m2", [activity("a", "UniProtKB:P2", "b", predicate="RO:0002630"), activity("b", "HGNC:1")]),
    ]
    result = project(models, [gene("HGNC:1", ["UniProtKB:P1"]), gene("HGNC:2", ["UniProtKB:P2"])])
    assert len(result["nodes"]) == 3
    assert len(result["edges"]) == 1  # Neither repeated models nor co-membership add edges.
    edge = result["edges"][0]
    assert len(edge["assertions"]) == 2
    first, second = edge["assertions"]
    assert first["source_node"] == second["target_node"]
    assert first["target_node"] == second["source_node"]
    assert first["assertion"]["evidence"] == [{"reference": "PMID:1"}]
    assert {a["predicate"] for a in edge["assertions"]} == {"RO:0002629", "RO:0002630"}
    assert result["activities"][0]["record"]["occurs_in"]["term"] == "GO:0005829"
    assert all(m["source"]["sha256"] == "test-checksum" for m in result["models"])
    stats = result["coverage"][0]
    assert stats["component_sizes"] == [2, 1]
    assert stats["isolated_genes"] == 1
    assert stats["catalog_genes"] == 2 and stats["source_identifier_genes"] == 1
    assert stats["projectable_fraction"] == 1
    assert sorted(n["degree"] for n in result["nodes"]) == [0, 1, 1]


def test_same_genes_with_different_pairs_produce_different_topology():
    chain = model(
        "m",
        [activity("a", "UniProtKB:A", "b"), activity("b", "UniProtKB:B", "c"), activity("c", "UniProtKB:C")],
    )
    star = deepcopy(chain)
    star["activities"][1]["causal_associations"] = []
    star["activities"][0]["causal_associations"].append(
        {"predicate": "RO:0002629", "downstream_activity": "c"}
    )
    one, two = project([chain]), project([star])
    assert {n["gene_id"] for n in one["nodes"]} == {n["gene_id"] for n in two["nodes"]}
    assert {(e["source"], e["target"]) for e in one["edges"]} != {
        (e["source"], e["target"]) for e in two["edges"]
    }


def test_taxa_never_merge_and_catalog_mismatch_is_not_rescued_by_raw_identifier():
    activities = [activity("a", "UniProtKB:A", "b"), activity("b", "UniProtKB:B")]
    result = project([model("human", activities), model("mouse", activities, "NCBITaxon:10090")])
    assert len(result["edges"]) == 2
    assert len({n["id"] for n in result["nodes"]}) == 4
    result = project([model("wrong", activities, "NCBITaxon:10090")], [gene("UniProtKB:A")])
    assert result["coverage"][0]["activity_omissions"] == {"taxon_mismatch": 1}
    assert not result["edges"]
    result = project([model("unknown", activities, "")])
    assert not result["nodes"] and not result["edges"]
    assert result["coverage"][0]["activity_omissions"] == {"missing_taxon": 2}


@pytest.mark.parametrize(
    "enabler,genes,reason",
    [
        ({"type": "EnabledByProteinComplexAssociation", "term": "SGD:C1"}, [], "complex_enabler"),
        ({"term": "UniProtKB:P1", "has_part": [{"term": "HGNC:1"}]}, [], "complex_enabler"),
        ({"term": "ComplexPortal:C1"}, [], "complex_enabler"),
        (
            {"term": "UniProtKB:C1"},
            [gene("UniProtKB:C1", gene_product_type_ids=["GO:0032991"])],
            "complex_enabler",
        ),
        ({"type": "EnabledByGeneProductAssociation", "term": "CHEBI:1"}, [], "non_gene_enabler"),
        ({"term": "GO:123"}, [], "non_gene_enabler"),
        ({}, [], "missing_enabler"),
        ({"term": "UniProtKB:P1"}, [], "unresolved_enabler"),  # Untyped IDs require catalog grounding.
        ({"type": "EnabledByGeneProductAssociation", "term": "PR:123"}, [], "unresolved_enabler"),
        (
            {"term": "UniProtKB:P1"},
            [gene("HGNC:1", ["UniProtKB:P1"]), gene("HGNC:2", ["UniProtKB:P1"])],
            "ambiguous_gene_mapping",
        ),
    ],
)
def test_ambiguous_complex_and_non_gene_enablers_are_reported(enabler, genes, reason):
    result = project([model("m", [activity("a", "", enabled_by=enabler)])], genes)
    assert not result["nodes"]
    assert result["coverage"][0]["activity_omissions"] == {reason: 1}


def test_bad_targets_repeated_ids_self_loops_and_noncausal_relations():
    result = project(
        [
            model(
                "m",
                [
                    activity("duplicate", "UniProtKB:A", "unique"),
                    activity("duplicate", "UniProtKB:B"),
                    activity("unique", "UniProtKB:C", "duplicate"),
                    activity("external", "UniProtKB:D", "other-model/activity"),
                    activity("self", "UniProtKB:C", "unique"),
                    activity("noncausal", "UniProtKB:E", "unique", predicate="BFO:0000050"),
                    activity("unrecognized", "UniProtKB:F", "unique", predicate="RO:9999999"),
                ],
            )
        ]
    )
    assert not result["edges"]
    stats = result["coverage"][0]
    assert stats["activity_omissions"] == {"duplicate_activity_id": 2}
    assert stats["assertion_omissions"] == {
        "unprojectable_endpoint": 1,
        "unresolved_target": 2,
        "self_loop": 1,
        "unsupported_predicate": 2,
    }
    assert len(result["omitted_assertions"]) == 6
    assert stats["activities"] == stats["projectable_activities"] + sum(stats["activity_omissions"].values())
    assert stats["causal_assertions"] == stats["projected_assertions"] + sum(
        stats["assertion_omissions"].values()
    )


def test_empty_corpus_and_empty_model():
    assert project([])["totals"]["genes"] == 0
    result = project([model("empty", [])])
    assert result["coverage"][0]["projectable_fraction"] is None
    assert result["coverage"][0]["component_sizes"] == []


def test_pinned_api_cli_and_filtering(megamodel_data, tmp_path, monkeypatch, capsys):
    app = create_app(megamodel_data, "duckdb")
    client = TestClient(app)
    full = client.get("/api/gene-network").json()
    assert full["build_id"] == app.state.catalog.build_id
    assert full["totals"]["edges"] == 1
    assert len(full["edges"][0]["assertions"]) == 2
    summary = client.get("/api/gene-network", params={"taxon": HUMAN, "summary": True}).json()
    assert "edges" not in summary
    assert len(summary["coverage"]) == 1
    assert summary["totals"]["genes"] == 2
    assert client.get("/api/gene-network?view=stale").status_code == 422
    assert client.get("/api/gene-network?taxon=NCBITaxon:999").status_code == 422
    assert client.get("/api/gene-network?taxon=taxon:9606&summary=true").json() == summary
    assert gene_network(app.state.catalog.path) is gene_network(app.state.catalog.path)
    # CLI and API share exactly the same projection, metadata and taxon policy.
    output = tmp_path / "export" / "network.json"
    argv = [
        "amigo3",
        "--data",
        str(megamodel_data),
        "gene-network",
        "--taxon",
        HUMAN,
        "--summary",
        "--build-id",
        full["build_id"],
    ]
    monkeypatch.setattr("sys.argv", [*argv, "--output", str(output)])
    main()
    assert json.loads(output.read_text()) == summary
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", argv)
    main()
    assert json.loads(capsys.readouterr().out) == summary
