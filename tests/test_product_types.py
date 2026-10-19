"""Biological types survive identity integration and facet identically in both engines."""

import os
import shutil

import duckdb
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.product_types import LNCRNA, NCRNA, normalize_product_type, product_type_hierarchy
from amigo3.solr import publish


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_product_type_facets_and_provenance(product_types_data, backend):
    solr = os.getenv("AMIGO3_TEST_SOLR", "")
    catalog = Catalog(product_types_data)
    if backend == "solr":
        if not solr:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(catalog, solr)
    with TestClient(create_app(product_types_data, backend, solr_url=solr)) as client:
        result = client.post(
            "/api/query",
            json={
                "collection": "genes",
                "facets": {"type": {"field": "gene_product_types"}},
            },
        ).json()
        counts = {b["value"]: b["count"] for b in result["facets"]["type"]["buckets"]}
        assert counts == {"Protein": 3, "miRNA": 1, "tRNA": 1, "Complex": 2, "Unspecified": 1}
        rows = {r["id"]: r for r in result["hits"]}
        assert rows["HGNC:1"]["gene_product_types"] == ["Protein", "miRNA"]
        assert rows["HGNC:1"]["gene_product_type_ids"] == ["PR:000000001", "SO:0000276"]
        assert rows["PomBase:TRNA1"]["record_kind"] == "gene"
        assert rows["PomBase:TRNA1"]["gene_product_types"] == ["tRNA"]
        assert rows["TestDB:ASSEMBLY1"]["gene_product_type_ids"] == ["GO:0032991"]
        assert rows["HGNC:3"]["gene_product_types"] == ["Unspecified"]
        assert rows["HGNC:3"]["gene_product_type_ids"] == []
        filtered = client.post(
            "/api/query",
            json={
                "collection": "genes",
                "filters": [{"id": "type", "field": "gene_product_types", "values": ["tRNA"]}],
            },
        ).json()
        assert filtered["total"] == 1
        assert filtered["hits"][0]["id"] == "PomBase:TRNA1"
        # Existing bookmarks keep their identity-based interpretation.
        assert (
            client.post(
                "/api/query",
                json={
                    "collection": "genes",
                    "filters": [{"id": "kind", "field": "record_kind", "values": ["gene"]}],
                },
            ).status_code
            == 200
        )
    with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
        assert db.execute("""SELECT source_type, source_format, source_id
            FROM gene_product_type_sources WHERE gene_id='TestDB:ASSEMBLY1'""").fetchone() == (
            "protein_complex",
            "gaf",
            "human_gaf",
        )


def test_unknown_types_are_preserved_without_inventing_ontology_ids():
    assert normalize_product_type("SO:9999999") == ("SO:9999999", "SO:9999999")
    assert normalize_product_type("novel_product") == (None, "novel_product")
    assert normalize_product_type("gene") == (None, "Unspecified")
    assert normalize_product_type("SO:0000704") == (None, "Unspecified")


def test_old_snapshots_keep_working(built_data, tmp_path):
    data = tmp_path / "old-data"
    shutil.copytree(built_data, data)
    catalog = Catalog(data)
    path = catalog.path / "genes.parquet"
    pq.write_table(
        pq.read_table(path).drop(["gene_product_types", "gene_product_type_ids", "gene_product_type_groups"]),
        path,
    )
    config = Catalog(data).collections["genes"]
    assert "record_kind" in config["facets"]
    assert "gene_product_types" not in config["facets"]
    assert set(config["display_fields"]) <= set(config["fields"])


def test_product_hierarchy_ancestry_and_unrecognized_roots():
    hierarchy, memberships = product_type_hierarchy({"Antisense lncRNA", "tRNA", "Protein", "SO:9999999"})
    assert hierarchy["nodes"]["SO:0001904"]["parents"] == [LNCRNA]
    assert hierarchy["nodes"][LNCRNA]["parents"] == [NCRNA]
    assert set(memberships["Antisense lncRNA"]) == {"SO:0001904", LNCRNA, NCRNA}
    assert set(memberships["tRNA"]) == {"SO:0000253", NCRNA}
    assert memberships["Protein"] == ["PR:000000001"]
    assert memberships["SO:9999999"] == ["SO:9999999"]
    assert hierarchy["nodes"]["SO:9999999"]["parents"] == []


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_product_hierarchy_union_counts_and_filters(product_hierarchy_data, backend):
    solr = os.getenv("AMIGO3_TEST_SOLR", "")
    catalog = Catalog(product_hierarchy_data)
    if backend == "solr":
        if not solr:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(catalog, solr)
    config = catalog.collections["genes"]
    assert "gene_product_type_groups" in config["facets"]
    assert "gene_product_types" not in config["facets"]
    with TestClient(create_app(product_hierarchy_data, backend, solr_url=solr)) as client:
        query = {"collection": "genes", "facets": {"type": {"field": "gene_product_type_groups"}}}
        result = client.post("/api/query", json=query).json()
        counts = {b["value"]: b["count"] for b in result["facets"]["type"]["buckets"]}
        assert counts[NCRNA] == 4  # A record carrying miRNA AND lincRNA counts only once.
        assert counts[LNCRNA] == 2
        assert counts["SO:0000253"] == 1
        query["filters"] = [
            {"id": "type", "field": "gene_product_type_groups", "values": [NCRNA, "SO:0000253"]}
        ]
        query["facets"]["type"]["exclude_filters"] = ["type"]
        assert client.post("/api/query", json=query).json()["total"] == 4
        query["filters"].append({"id": "species", "field": "species", "values": ["pombe"]})
        result = client.post("/api/query", json=query).json()
        assert result["total"] == 3
        counts = {b["value"]: b["count"] for b in result["facets"]["type"]["buckets"]}
        assert counts[NCRNA] == 3
        assert counts[LNCRNA] == 1
        # Existing exact-type bookmarks still mean the supplied type, not its descendants.
        exact = client.post(
            "/api/query",
            json={
                "collection": "genes",
                "filters": [{"id": "exact", "field": "gene_product_types", "values": ["ncRNA"]}],
            },
        ).json()
        assert exact["total"] == 1
        assert exact["hits"][0]["id"] == "PomBase:NCRNA"
