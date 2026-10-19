"""Ranking, safe generic sort clauses, and stable cross-engine pagination."""

import os

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Facet, Page, Query, Sort
from amigo3.solr import SolrBackend, publish


def all_ids(engine, query):
    ids = []
    for _ in range(100):
        result = engine.query(query)
        ids.extend(row["id"] for row in result["hits"])
        if not result["next_cursor"]:
            assert len(ids) == len(set(ids)) == result["total"]
            return ids
        query.page.cursor = result["next_cursor"]
    pytest.fail("Cursor did not terminate")


def test_recommended_proteins_and_slim_members(product_types_data):
    engine = DuckBackend(Catalog(product_types_data))
    result = engine.query(Query(collection="genes", sort="recommended"))
    proteins = ["Protein" in r["gene_product_types"] for r in result["hits"]]
    assert proteins == sorted(proteins, reverse=True)
    terms = engine.query(Query(collection="terms", sort="recommended"))["hits"]
    assert terms[0]["id"] == "GO:0016301"
    # A specific non-slim term wins over slim members in a search for its name.
    assert (
        engine.query(Query(collection="terms", sort="recommended", search="protein kinase activity"))["hits"][
            0
        ]["id"]
        == "GO:0004672"
    )
    explicit = engine.query(
        Query(collection="genes", sort=[Sort(field="annotation_count", direction="desc")])
    )
    counts = [r["annotation_count"] for r in explicit["hits"]]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.parametrize(
    "sort",
    [
        "missing",
        [{"field": "search_text"}],
        [{"field": "id; DROP TABLE genes"}],
        [{"field": "identifiers"}],
        [{"field": "has_review", "value": "true"}],
        [{"field": "symbol", "direction": "sideways"}],
        [{"field": "id"}] * 9,
    ],
)
def test_invalid_sorts_are_rejected(built_data, sort):
    with TestClient(create_app(built_data, "duckdb")) as client:
        assert client.post("/api/query", json={"collection": "genes", "sort": sort}).status_code == 422


def test_cursor_rejects_changed_sort(built_data):
    engine = DuckBackend(Catalog(built_data))
    query = Query(collection="genes", sort="name_asc", page=Page(size=1))
    query.page.cursor = engine.query(query)["next_cursor"]
    query.sort = "name_desc"
    with pytest.raises(ValueError, match="different query"):
        engine.query(query)


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Requires real Solr")
def test_sort_presets_membership_boosts_and_cursor_parity(product_types_data):
    catalog = Catalog(product_types_data)
    url = os.environ["AMIGO3_TEST_SOLR"]
    publish(catalog, url)
    engines = [DuckBackend(catalog), SolrBackend(catalog, url)]
    for collection, config in catalog.collections.items():
        for preset in config["sort_options"]:
            orders = [
                all_ids(e, Query(collection=collection, sort=preset, page=Page(size=2))) for e in engines
            ]
            assert orders[0] == orders[1], (collection, preset)
    for query in [
        Query(collection="genes", search="GENE1", sort="recommended"),
        Query(collection="terms", search="kinase", sort="recommended"),
        Query(collection="genes", sort=[Sort(field="gene_product_types", value="Protein", direction="desc")]),
        Query(collection="genes", sort=[Sort(field="has_review", value=False, direction="desc")]),
        Query(collection="genes", sort=[Sort(field="symbol", value='" ) OR *:*', direction="desc")]),
    ]:
        query.facets = {"id": Facet(field="id")}
        a, b = [e.query(query) for e in engines]
        assert a["hits"] == b["hits"]
        assert a["facets"] == b["facets"]
