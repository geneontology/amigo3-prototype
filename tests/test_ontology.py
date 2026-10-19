"""Ancestor topology and exact, bounded descendant facets share the pinned data."""

import os

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Facet, Filter, Query
from amigo3.solr import SolrBackend, publish


def test_complete_mixed_ancestry_and_children(ontology_data):
    with TestClient(create_app(ontology_data, "duckdb")) as client:
        graph = client.get("/api/ontology/GO:9916301").json()
        assert graph["focus"] == "GO:0016301"
        assert {n["id"] for n in graph["nodes"]} == {"GO:0016301", "GO:9000001", "GO:9000002", "GO:0003674"}
        assert len(graph["edges"]) == 4
        assert {e["predicate"] for e in graph["edges"]} == {"is_a", "BFO:0000050"}
        assert {n["id"] for n in graph["children"]} == {"GO:0004672", "GO:9000003"}
        assert not any(n["has_children"] for n in graph["children"])
        detail = client.get("/api/terms/GO:0004672").json()
        assert len(detail["ontology"]["edges"]) == 5
        assert len(detail["ontology"]["nodes"]) == 5
        assert "regulates" in {e["predicate"] for e in detail["parents"]}
        assert "GO:9999999" not in {n["id"] for n in detail["ontology"]["nodes"]}
        root = client.get("/api/ontology/GO:0003674").json()
        assert len(root["nodes"]) == 1 and root["edges"] == []
        assert all(n["has_children"] for n in root["children"])
        assert client.get("/api/ontology/GO:missing").status_code == 404
        assert client.get("/api/ontology/GO:0003674?view=stale").status_code == 422


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_requested_buckets_count_diamond_once_and_keep_zeroes(ontology_data, backend):
    catalog = Catalog(ontology_data)
    if backend == "solr":
        url = os.environ.get("AMIGO3_TEST_SOLR")
        if not url:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(catalog, url)
        engine = SolrBackend(catalog, url)
    else:
        engine = DuckBackend(catalog)
    values = ["GO:0003674", "GO:9000001", "GO:9000002", "GO:9000003", "GO:missing", "GO:9000001"]
    query = Query(
        collection="annotations",
        filters=[Filter(id="go", field="isa_partof_closure", values=["GO:9000003"])],
        facets={
            "branches": Facet(field="isa_partof_closure", values=values, exclude_filters=["go"], limit=1),
            "direct": Facet(
                field="ontology_class_id", values=["GO:0016301", "GO:0004672"], exclude_filters=["go"]
            ),
        },
    )
    result = engine.query(query)
    assert result["total"] == 0
    facet = result["facets"]["branches"]
    assert [b["value"] for b in facet["buckets"]] == values[:-1]
    assert [b["count"] for b in facet["buckets"]] == [6, 5, 5, 0, 0]
    assert not facet["truncated"] and facet["exact"]
    assert [b["count"] for b in result["facets"]["direct"]["buckets"]] == [1, 4]
    query.filters += [
        Filter(id="species", field="species", values=["human"]),
        Filter(id="negative", field="negation", values=[False]),
        Filter(id="evidence", field="evidence_code", values=["IDA"]),
    ]
    result = engine.query(query)
    assert [b["count"] for b in result["facets"]["branches"]["buckets"]] == [2, 1, 1, 0, 0]
    query.search = "no_such_annotation"
    assert all(b["count"] == 0 for b in engine.query(query)["facets"]["branches"]["buckets"])


def test_requested_facet_validation_and_scalar_types(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        for field, values in [
            ("bad", ["x"]),
            ("negation", ["false"]),
            ("species", [False]),
            ("species", []),
            ("species", ["x"] * 201),
        ]:
            response = client.post(
                "/api/query",
                json={"collection": "annotations", "facets": {"v": {"field": field, "values": values}}},
            )
            assert response.status_code == 422
        response = client.post(
            "/api/query",
            json={
                "collection": "annotations",
                "facets": {"v": {"field": "negation", "values": [False, True]}},
            },
        )
        assert response.json()["facets"]["v"]["buckets"] == [
            {"value": False, "count": 3},
            {"value": True, "count": 1},
        ]
