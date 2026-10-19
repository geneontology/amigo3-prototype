"""Check real indexed data against the integration database and identifier mappings."""

import json
from pathlib import Path

import duckdb

from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Facet, Filter, Query
from amigo3.solr import SolrBackend
from amigo3.sources import write_json


def main():
    data = Path("data")
    published = json.loads((data / "published.json").read_text())
    catalog = Catalog(data, published["build_id"])
    solr, duck = SolrBackend(catalog, published["url"]), DuckBackend(catalog)
    checks = []
    for collection in catalog.collections:
        result = solr.query(Query(collection=collection, select=["id"]))
        expected = catalog.manifest["counts"][collection]
        assert result["total"] == expected, (collection, result["total"], expected)
        checks.append({"check": f"{collection} count", "count": expected})
    for filters in (
        [],
        *[
            [Filter(id="species", field="species", values=[species])]
            for species in sorted(
                {s["species"] for s in catalog.manifest["sources"].values() if s["kind"] == "gaf"}
            )
        ],
        [Filter(id="negative", field="negation", values=[True])],
        [Filter(id="ancestry", field="isa_partof_closure", values=["GO:0016301"])],
    ):
        exclusions = [f.id for f in filters if f.id == "species"]
        query = Query(
            collection="annotations",
            filters=filters,
            select=["id"],
            facets={
                "species": Facet(field="species", exclude_filters=exclusions),
                "evidence": Facet(field="evidence_code", limit=100),
                "negation": Facet(field="negation"),
            },
        )
        a, b = solr.query(query), duck.query(query)
        assert a["total"] == b["total"]
        for key in a["facets"]:
            assert {x["value"]: x["count"] for x in a["facets"][key]["buckets"]} == {
                x["value"]: x["count"] for x in b["facets"][key]["buckets"]
            }, key
        checks.append(
            {"check": "facet parity", "filters": [f.model_dump() for f in filters], "total": a["total"]}
        )
    if "has_annotation_review" in catalog.collections["annotations"]["fields"]:
        for filters in (
            [Filter(id="coverage", field="has_review", values=[True])],
            [Filter(id="coverage", field="has_annotation_review", values=[True])],
            [Filter(id="action", field="review_actions", values=["ACCEPT"])],
        ):
            query = Query(
                collection="annotations",
                filters=filters,
                select=["id"],
                facets={
                    "actions": Facet(
                        field="review_actions", exclude_filters=[f.id for f in filters if f.id == "action"]
                    ),
                    "multiple": Facet(field="review_multiple"),
                    "support": Facet(field="review_has_support"),
                },
            )
            a, b = solr.query(query), duck.query(query)
            assert a["total"] == b["total"]
            for key in a["facets"]:
                assert {x["value"]: x["count"] for x in a["facets"][key]["buckets"]} == {
                    x["value"]: x["count"] for x in b["facets"][key]["buckets"]
                }, key
            checks.append(
                {
                    "check": "review facet parity",
                    "filters": [f.model_dump() for f in filters],
                    "total": a["total"],
                }
            )
    if "rules" in catalog.collections:
        for filters in (
            [],
            [Filter(id="modifier", field="predicate_modifier", values=["Not"])],
            [Filter(id="project", field="project_ids", values=["PROJECT:PROTEOSTASIS"])],
            [Filter(id="action", field="review_action", values=["ACCEPT"])],
        ):
            query = Query(
                collection="rules",
                filters=filters,
                facets={
                    name: Facet(field=name, limit=100)
                    for name in (
                        "kind",
                        "rule_source",
                        "has_review",
                        "review_status",
                        "review_action",
                        "mapping_scope",
                        "predicate_id",
                        "predicate_modifier",
                        "antecedent_status",
                    )
                },
            )
            a, b = solr.query(query), duck.query(query)
            assert a["total"] == b["total"]
            assert a["hits"] == b["hits"]
            assert a["facets"] == b["facets"]
            checks.append(
                {
                    "check": "rule/mapping parity",
                    "filters": [f.model_dump() for f in filters],
                    "total": a["total"],
                }
            )
    identifiers = [
        "UniProtKB:Q9US57",
        "PomBase:SPAC1002.01",
        "UniProtKB:P04637",
        "HGNC:11998",
        "ENSEMBL:ENSG00000141510",
    ]
    resolved = {}
    for identifier in identifiers:
        query = Query(
            collection="genes", filters=[Filter(id="identifier", field="identifiers", values=[identifier])]
        )
        result = solr.query(query)
        resolved[identifier] = [row["id"] for row in result["hits"]]
        assert resolved[identifier], identifier
    assert resolved[identifiers[0]] == resolved[identifiers[1]]
    assert resolved[identifiers[2]] == resolved[identifiers[3]] == resolved[identifiers[4]]
    with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
        assert (
            db.execute("SELECT count(*) FROM source_annotations").fetchone()[0]
            == catalog.manifest["counts"]["annotations"]
        )
    result = {
        "build_id": catalog.build_id,
        "checks": checks,
        "identifier_resolution": resolved,
        "status": "passed",
    }
    write_json(catalog.path / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
