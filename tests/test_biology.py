"""Source-derived structures and differentiation keep identities and semantics separate."""

import json
import os
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.cell_types import load_cell_types
from amigo3.structures import render_structure, sdf_records


def test_shared_structure_and_immutable_assets(biology_data):
    with TestClient(create_app(biology_data, "duckdb")) as client:
        chemical = client.get("/api/chemicals/CHEBI:57540").json()
        structure = chemical["structure"]
        assert chemical["record"]["has_structure"]
        assert structure["source_id"] == "rhea_structures"
        assert structure["source_sha256"] != chemical["record"]["source_sha256"]
        assert "RDKit" in structure["renderer"]
        response = client.get(structure["url"])
        assert response.status_code == 200
        assert "<svg" in response.text and "bond-" in response.text
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "immutable" in response.headers["cache-control"]
        assert client.get("/api/chemical-structures/CHEBI:57540?sha256=wrong").status_code == 404
        assert client.get("/api/chemical-structures/../../sources.json").status_code == 404
        term = client.get("/api/terms/GO:0016301").json()
        for reaction in term["reactions"]:
            nad = next(p["chemical"] for p in reaction["left"] if p["chemical"]["id"] == "CHEBI:57540")
            assert nad["structure"] == structure
        missing = client.get("/api/chemicals/CHEBI:999999").json()
        assert missing["structure"] is None and not missing["record"]["has_structure"]
        assert client.get("/api/chemical-structures/CHEBI:999999").status_code == 404
        query = client.post(
            "/api/query",
            json={
                "collection": "chemicals",
                "filters": [{"id": "depicted", "field": "has_structure", "values": [True]}],
            },
        ).json()
        assert query["total"] == 2


def test_cell_targets_exact_and_subtype_queries(biology_data):
    with TestClient(create_app(biology_data, "duckdb")) as client:
        term = client.get("/api/terms/GO:0030217").json()
        assert len(term["differentiations"]) == 1  # Edge + logical axiom are one description.
        d = term["differentiations"][0]
        assert d["target_cell_type"] == {"id": "CL:0000084", "label": "T cell"}
        assert d["predicate"] == "RO:0002315"
        assert d["axiom_kinds"] == ["logical_definition", "ontology_edge"]
        assert "precursor_cell_type" not in d
        cell = client.get("/api/cell_types/CL:0000084").json()
        assert cell["record"]["label"] == "T cell"
        assert [p["id"] for p in cell["parents"]] == ["CL:0000542"]
        assert "CL:0000827" not in cell["record"]["is_a_closure"]  # develops_from isn't is_a.
        assert cell["differentiations"]["exact"]["total"] == 1
        assert cell["differentiations"]["subtypes"]["total"] == 2
        for group in cell["differentiations"].values():
            query = client.post(
                "/api/query",
                json={
                    "collection": "differentiations",
                    "filters": [{"id": "target", "field": group["field"], "values": [group["value"]]}],
                },
            ).json()
            assert query["total"] == group["total"]
        assert client.get("/cell_types/CL:0000084").status_code == 200
        assert client.get("/api/cell_types/CL:9999999").status_code == 404
        assert client.get("/api/terms/GO:0047034").json()["differentiations"] == []


def test_enrichment_does_not_change_annotation_closure(biology_data, chemical_data):
    def annotations(data):
        with duckdb.connect(str(Catalog(data).path / "integration.duckdb"), read_only=True) as db:
            return db.execute(
                "SELECT id, is_a_closure, isa_partof_closure FROM annotations ORDER BY id"
            ).fetchall()

    assert annotations(biology_data) == annotations(chemical_data)
    with duckdb.connect(str(Catalog(biology_data).path / "integration.duckdb"), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM ontology_edges WHERE object LIKE 'CL:%'").fetchone()[0] == 0


def test_missing_sources_and_non_differentiation_are_not_inferred(tmp_path):
    source = Path(__file__).parent / "fixtures/go-differentiation.json"
    document = json.loads(source.read_text())
    # Even an explicit target-cell relation on a non-differentiation process is not this capability.
    (tmp_path / "go.json").write_text(json.dumps(document))
    (tmp_path / "cl.json").write_text((source.parent / "cell-types.json").read_text())
    manifest = {
        "go": {"id": "go", "kind": "go_plus_json", "path": "go.json", "sha256": "a"},
        "cl": {"id": "cl", "kind": "cl_json", "path": "cl.json", "sha256": "b"},
    }
    with duckdb.connect() as db:
        with pytest.raises(ValueError, match="both"):
            load_cell_types(db, tmp_path, {"go": manifest["go"]})
        db.execute(
            "CREATE TABLE terms AS SELECT 'GO:0030217' id, 'test' AS label, false obsolete, ['GO:0008150'] is_a_closure"
        )
        assert load_cell_types(db, tmp_path, manifest)["differentiations"] == 0


def test_unrenderable_and_truncated_structures(tmp_path):
    with pytest.raises(ValueError, match="Unrenderable"):
        render_structure("invalid molfile")
    path = tmp_path / "truncated.sdf"
    path.write_text("CHEBI:1\ninvalid\n")
    with pytest.raises(ValueError, match="Truncated"):
        list(sdf_records(path))


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Set AMIGO3_TEST_SOLR")
def test_biology_solr_query_parity(biology_data):
    from amigo3.duck import DuckBackend
    from amigo3.query import Facet, Filter, Query
    from amigo3.solr import SolrBackend, publish

    catalog = Catalog(biology_data)
    publish(catalog, os.environ["AMIGO3_TEST_SOLR"])
    solr, duck = SolrBackend(catalog, os.environ["AMIGO3_TEST_SOLR"]), DuckBackend(catalog)
    for collection, field, value in (
        ("differentiations", "target_cell_type_id", "CL:0000084"),
        ("differentiations", "target_cell_type_closure", "CL:0000084"),
        ("cell_types", "is_a_closure", "CL:0000084"),
        ("chemicals", "has_structure", True),
    ):
        query = Query(
            collection=collection,
            filters=[Filter(id="focus", field=field, values=[value])],
            facets={field: Facet(field=field)},
        )
        a, b = solr.query(query), duck.query(query)
        assert a["total"] == b["total"]
        assert a["hits"] == b["hits"]
        assert a["facets"] == b["facets"]
