"""Dashboard counts must describe the full selection, independent of pages/facets."""

import os
import shutil

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Breakdown, Facet, Filter, Page, Query
from amigo3.solr import SolrBackend, publish


def dashboard_query(**kwargs):
    return Query(
        collection="annotations",
        breakdowns={"comparison": Breakdown(field="species", split_by="evidence_code")},
        **kwargs,
    )


def test_full_selection_and_disjunctive_sidebar_have_different_domains(built_data):
    backend = DuckBackend(Catalog(built_data))
    query = dashboard_query(
        filters=[Filter(id="species", field="species", values=["human"])],
        facets={
            "species": Facet(field="species", exclude_filters=["species"]),
            "requested": Facet(field="species", values=["human", "missing"]),
        },
        page=Page(size=1),
    )
    result = backend.query(query)
    assert len(result["hits"]) == 1
    assert len(result["facets"]["species"]["buckets"]) == 2
    assert result["facets"]["requested"]["buckets"] == [
        {"value": "human", "count": 3},
        {"value": "missing", "count": 0},
    ]
    chart = result["breakdowns"]["comparison"]
    assert chart["total"] == 3
    assert chart["buckets"] == [
        {"value": "human", "count": 3, "segments": [{"value": "IDA", "count": 3}], "remainder": 0}
    ]
    assert chart["columns"] == [{"value": "IDA", "count": 3}]
    query.page.cursor = result["next_cursor"]
    assert backend.query(query)["breakdowns"] == result["breakdowns"]
    query.page = Page()
    query.search = "no-such-annotation"
    empty = backend.query(query)["breakdowns"]["comparison"]
    assert empty["total"] == 0 and empty["buckets"] == [] and empty["columns"] == []


def test_global_column_ranking_zero_cells_and_remainders(built_data, tmp_path):
    # Make the top evidence globally occur only in the second-ranked species.
    data = tmp_path / "data"
    shutil.copytree(built_data, data)
    catalog = Catalog(data)
    with duckdb.connect(str(catalog.path / "integration.duckdb")) as db:
        db.execute("UPDATE annotations SET species='a', evidence_code='IEA'")
        ids = [r[0] for r in db.execute("SELECT id FROM annotations ORDER BY id").fetchall()]
        db.execute("UPDATE annotations SET species='b', evidence_code='IDA' WHERE id IN (?, ?)", ids[:2])
        db.execute("UPDATE annotations SET evidence_code='IMP' WHERE id=?", [ids[2]])
    query = Query(
        collection="annotations",
        breakdowns={
            "cross": Breakdown(field="species", split_by="evidence_code", limit=1),
            "single": Breakdown(field="species", limit=1),
        },
    )
    charts = DuckBackend(catalog).query(query)["breakdowns"]
    cross = charts["cross"]
    assert cross["total"] == 4 and cross["remainder"] == 2
    assert cross["columns"] == [{"value": "IDA", "count": 2}]
    assert cross["buckets"] == [
        {"value": "a", "count": 2, "segments": [{"value": "IDA", "count": 0}], "remainder": 2}
    ]
    assert charts["single"]["buckets"] == [{"value": "a", "count": 2}]
    # Empty scalar values belong in the remainder, not a misleading blank bar.
    with duckdb.connect(str(catalog.path / "integration.duckdb")) as db:
        db.execute("UPDATE annotations SET species='' WHERE species='a'")
    assert DuckBackend(catalog).query(query)["breakdowns"]["single"]["remainder"] == 2


@pytest.mark.parametrize(
    "spec",
    [
        {"field": "species", "limit": 21},
        {"field": "species", "split_by": "species"},
        {"field": "species", "split_by": "references"},
        {"field": "search_text"},
        {"field": 'species"; DROP TABLE annotations; --'},
        {"field": "species", "exclude_filters": []},
    ],
)
def test_breakdown_validation(built_data, spec):
    with TestClient(create_app(built_data, "duckdb")) as client:
        response = client.post(
            "/api/query", json={"collection": "annotations", "breakdowns": {"chart": spec}}
        )
        assert response.status_code == 422


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Set AMIGO3_TEST_SOLR to a local Solr URL")
def test_breakdown_solr_parity(built_data):
    catalog = Catalog(built_data)
    url = os.environ["AMIGO3_TEST_SOLR"]
    publish(catalog, url, batch_size=2)
    local, solr = DuckBackend(catalog), SolrBackend(catalog, url)
    queries = [
        dashboard_query(page=Page(size=1)),
        dashboard_query(filters=[Filter(id="s", field="species", values=["human"])]),
        dashboard_query(
            filters=[Filter(id="s", field="species", values=["human"])],
            facets={
                "requested": Facet(
                    field="species", values=["human", "pombe", "missing"], exclude_filters=["s"]
                )
            },
        ),
        dashboard_query(search="no-such-annotation"),
        dashboard_query(search="GENE1"),
        Query(
            collection="annotations",
            breakdowns={
                "single": Breakdown(field="evidence_code", limit=1),
                "cross": Breakdown(field="species", split_by="negation", limit=1),
                "reverse": Breakdown(field="evidence_code", split_by="species"),
            },
        ),
    ]
    for query in queries:
        published, local_result = solr.query(query), local.query(query)
        assert published["breakdowns"] == local_result["breakdowns"]
        assert published["facets"] == local_result["facets"]


def test_overlapping_groups_count_records_and_union_remainder(built_data, tmp_path):
    data = tmp_path / "overlapping"
    shutil.copytree(built_data, data)
    catalog = Catalog(data)
    with duckdb.connect(str(catalog.path / "integration.duckdb")) as db:
        ids = [r[0] for r in db.execute("SELECT id FROM terms ORDER BY id").fetchall()]
        assert len(ids) >= 3
        db.execute("UPDATE terms SET subsets=NULL, obsolete=false")
        db.execute("UPDATE terms SET subsets=['a','a','b'] WHERE id=?", [ids[0]])
        db.execute("UPDATE terms SET subsets=['a'], obsolete=true WHERE id=?", [ids[1]])
        db.execute("UPDATE terms SET subsets=['c'] WHERE id=?", [ids[2]])
    query = Query(
        collection="terms",
        breakdowns={"chart": Breakdown(field="subsets", split_by="obsolete", limit=2)},
    )
    backend = DuckBackend(catalog)
    chart = backend.query(query)["breakdowns"]["chart"]
    assert chart["group_multivalued"] is True
    assert chart["remainder"] == len(ids) - 2  # a and b cover only two distinct records
    assert chart["buckets"] == [
        {
            "value": "a",
            "count": 2,
            "segments": [{"value": False, "count": 1}, {"value": True, "count": 1}],
            "remainder": 0,
        },
        {
            "value": "b",
            "count": 1,
            "segments": [{"value": False, "count": 1}, {"value": True, "count": 0}],
            "remainder": 0,
        },
    ]
    query.breakdowns["chart"].limit = 1
    chart = backend.query(query)["breakdowns"]["chart"]
    assert chart["buckets"][0]["remainder"] == 1  # omitted True column
    query.filters = [Filter(id="subset", field="subsets", values=["b"])]
    chart = backend.query(query)["breakdowns"]["chart"]
    assert chart["total"] == 1 and chart["remainder"] == 0


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Requires real Solr")
def test_all_dashboard_dimensions_solr_parity(product_types_data):
    catalog = Catalog(product_types_data)
    url = os.environ["AMIGO3_TEST_SOLR"]
    publish(catalog, url)
    local, solr = DuckBackend(catalog), SolrBackend(catalog, url)
    for collection, config in catalog.collections.items():
        dashboard = config.get("dashboard")
        if not dashboard:
            continue
        split = dashboard["split"]
        for field in dashboard["fields"]:
            query = Query(
                collection=collection,
                breakdowns={
                    "chart": Breakdown(
                        field=field, split_by=split if split and field != split else None, limit=1
                    )
                },
                page=Page(size=1),
            )
            a, b = local.query(query), solr.query(query)
            assert a["breakdowns"] == b["breakdowns"], (collection, field)
            chart = a["breakdowns"]["chart"]
            assert 0 <= chart["remainder"] <= a["total"]
