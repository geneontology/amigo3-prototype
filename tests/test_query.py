import os

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Facet, Filter, Histogram, Page, Query
from amigo3.solr import SolrBackend, publish


def test_facets_exclude_only_named_filter(built_data):
    backend = DuckBackend(Catalog(built_data))
    query = Query(
        collection="annotations",
        filters=[Filter(id="species", field="species", values=["human"])],
        facets={
            "species": Facet(field="species", exclude_filters=["species"]),
            "evidence": Facet(field="evidence_code"),
        },
    )
    response = backend.query(query)
    assert response["total"] == 3
    assert {b["value"]: b["count"] for b in response["facets"]["species"]["buckets"]} == {
        "human": 3,
        "pombe": 1,
    }
    assert response["facets"]["evidence"]["buckets"] == [{"value": "IDA", "count": 3}]


def test_method_facet_distinguishes_iea_pipelines(built_data):
    catalog = Catalog(built_data)
    assert catalog.collections["annotations"]["fields"]["method"]["facetable"]
    backend = DuckBackend(catalog)
    response = backend.query(
        Query(
            collection="annotations",
            filters=[Filter(id="method", field="method", values=["InterPro2GO"])],
            facets={"method": Facet(field="method", exclude_filters=["method"])},
        )
    )
    assert response["total"] == 1
    assert response["hits"][0]["evidence_code"] == "IEA"
    assert {b["value"]: b["count"] for b in response["facets"]["method"]["buckets"]} == {
        "Literature curation": 3,
        "InterPro2GO": 1,
    }


def test_cursor_bound_to_query_and_build(built_data):
    backend = DuckBackend(Catalog(built_data))
    query = Query(collection="annotations", page=Page(size=1))
    first = backend.query(query)
    query.page.cursor = first["next_cursor"]
    second = backend.query(query)
    assert first["hits"][0]["id"] != second["hits"][0]["id"]
    query.search = "different"
    with pytest.raises(ValueError, match="different query or build"):
        backend.query(query)


def test_annotation_redundancy_facet_and_sort_orders(built_data):
    backend = DuckBackend(Catalog(built_data))
    res = backend.query(
        Query(
            collection="annotations",
            facets={"is_redundant": Facet(field="is_redundant")},
            sort="recommended",
        )
    )
    buckets = {b["value"]: b["count"] for b in res["facets"]["is_redundant"]["buckets"]}
    # GO:0003674 on GENE1 is on isoform UniProtKB:P00001-2 with extensions, so it is not redundant with canonical GO:0004672
    assert buckets == {False: 4}
    # Experimental (IDA) annotations sort ahead of IEA under recommended sort
    assert res["hits"][0]["is_redundant"] is False
    assert res["hits"][0]["evidence_code"] == "IDA"
    assert res["hits"][-1]["evidence_code"] == "IEA"

    # Agreement sort puts ACCEPT ahead of MODIFY and unreviewed
    by_agreement = backend.query(Query(collection="annotations", sort="agreement"))
    assert by_agreement["hits"][0]["review_actions"] == ["ACCEPT"]

    # Term sort orders alphabetically by term_label
    by_term = backend.query(Query(collection="annotations", sort="term"))
    labels = [h["term_label"] for h in by_term["hits"]]
    assert labels == sorted(labels)


def test_api_resolution_provenance_and_validation(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        a = client.get("/api/resolve", params={"identifier": "UniProtKB:Q00001"}).json()
        b = client.get("/api/resolve", params={"identifier": "PomBase:SPTEST.1"}).json()
        assert a["hits"][0]["id"] == b["hits"][0]["id"]
        result = client.post("/api/query", json={"collection": "annotations"}).json()
        row = result["hits"][0]
        provenance = client.get("/api/provenance/" + row["id"])
        assert provenance.status_code == 200
        assert len(provenance.json()["raw_record"].split("\t")) == 17
        assert (
            client.post(
                "/api/query",
                json={"filters": [{"id": "bad", "field": "id; DROP TABLE genes", "values": ["x"]}]},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/query", json={"filters": [{"id": "bad", "field": "has_review", "values": ["false"]}]}
            ).status_code
            == 422
        )
        assert client.post("/api/query", json={"view": "wrong"}).status_code == 422
        assert client.get("/api/records/genes/missing").status_code == 404
        assert client.get("/").status_code == 200


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Set AMIGO3_TEST_SOLR to a local Solr URL")
def test_real_solr_parquet_roundtrip_and_facet_parity(built_data):
    catalog = Catalog(built_data)
    url = os.environ["AMIGO3_TEST_SOLR"]
    publish(catalog, url, batch_size=2)
    solr = SolrBackend(catalog, url)
    duck = DuckBackend(catalog)
    requests = [
        Query(
            collection="annotations",
            filters=[Filter(id="species", field="species", values=["human"])],
            facets={
                "species": Facet(field="species", exclude_filters=["species"]),
                "evidence": Facet(field="evidence_code"),
                "method": Facet(field="method"),
            },
        ),
        Query(
            collection="genes",
            filters=[Filter(id="identifier", field="identifiers", values=["UniProtKB:Q00001"])],
        ),
        Query(collection="annotations", filters=[Filter(id="negative", field="negation", values=[True])]),
        Query(
            collection="terms",
            filters=[Filter(id="ancestors", field="isa_partof_closure", values=["GO:0016301"])],
        ),
    ]
    requests.append(
        Query(
            collection="annotations",
            filters=[
                Filter(id="review", field="has_annotation_review", values=[True]),
                Filter(id="action", field="review_actions", values=["ACCEPT"]),
            ],
            facets={
                "actions": Facet(field="review_actions", exclude_filters=["action"]),
                "support": Facet(field="review_has_support"),
            },
        )
    )
    requests.append(
        Query(
            collection="reports",
            search="distinctiveflavorkinase",
            facets={"provider": Facet(field="provider")},
        )
    )
    requests.append(Query(collection="reports", facets={"organism": Facet(field="organism")}))
    requests.append(Query(collection="reports", filters=[Filter(id="model", field="model", op="exists")]))
    requests.append(
        Query(
            collection="projects",
            facets={"maturity": Facet(field="maturity"), "species": Facet(field="project_species")},
        )
    )
    requests.append(
        Query(collection="projects", filters=[Filter(id="tag", field="tags", values=["BIOLOGY_DOMAIN"])])
    )
    requests.append(
        Query(
            collection="rules",
            facets={
                "kind": Facet(field="kind"),
                "review": Facet(field="has_review"),
                "modifier": Facet(field="predicate_modifier"),
            },
        )
    )
    requests.append(
        Query(collection="rules", filters=[Filter(id="modifier", field="predicate_modifier", values=["Not"])])
    )
    requests.extend(
        Query(collection=c, facets={"terms": Facet(field="term_ids")})
        for c in ("gocams", "modules", "families")
    )
    requests.append(
        Query(
            collection="modules",
            filters=[Filter(id="parts", field="part_count", op="between", values=[0, 5])],
            histograms={
                "parts": Histogram(field="part_count", exclude_filters=["parts"]),
                "joint": Histogram(field="variant_count", y_field="realization_count", y_scale="log"),
            },
        )
    )
    for query in requests:
        a, b = solr.query(query), duck.query(query)
        assert a["total"] == b["total"]
        assert a["hits"] == b["hits"]
        assert a["facets"] == b["facets"]
        assert a["histograms"] == b["histograms"]
    query = Query(collection="annotations", page=Page(size=1))
    first = solr.query(query)
    query.page.cursor = first["next_cursor"]
    assert solr.query(query)["hits"][0]["id"] != first["hits"][0]["id"]
