import json
import os

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.build import build
from amigo3.catalog import Catalog
from amigo3.parse import gaf_rows
from amigo3.solr import publish


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_new_species_identifiers_reviews_and_facets(multispecies_data, backend):
    solr = os.environ.get("AMIGO3_TEST_SOLR")
    if backend == "solr":
        if not solr:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(Catalog(multispecies_data), solr)
    with TestClient(create_app(multispecies_data, backend, solr_url=solr or "")) as client:
        for species, identifier, kind in [
            ("worm", "WB:WBGene00000001", "gene"),
            ("PSEPK", "UniProtKB:Q88RL4", "gene_product"),
        ]:
            result = client.post(
                "/api/query",
                json={
                    "collection": "annotations",
                    "filters": [{"id": "species", "field": "species", "values": [species]}],
                    "facets": {"species": {"field": "species", "exclude_filters": ["species"]}},
                },
            ).json()
            assert result["total"] == 1
            assert result["hits"][0]["has_annotation_review"]
            assert {b["value"] for b in result["facets"]["species"]["buckets"]} == {
                "human",
                "pombe",
                "worm",
                "PSEPK",
            }
            detail = client.get("/api/genes/" + identifier).json()
            assert detail["record"]["record_kind"] == kind
            assert detail["record"]["has_review"]
            assert detail["reviews"][0]["document"]["description"].startswith("Review for ")
            assert detail["review_entries"][0]["annotation_ids"]
            assert detail["annotation_count"] == 1
        for identifier in [
            "WB:WBGene00000001",
            "WormBase:WBGene00000001",
            "WBGene00000001",
            "UniProtKB:G5EDP9",
            "UniProtKB:QWORM2",
        ]:
            result = client.get("/api/resolve", params={"identifier": identifier}).json()
            assert result["hits"][0]["id"] == "WB:WBGene00000001"


def test_review_from_added_species_changes_build_identity(multispecies_data, tmp_path):
    import shutil

    # Work on a private copy so session fixtures remain immutable.
    data = tmp_path / "data"
    shutil.copytree(multispecies_data, data)
    old = Catalog(data).build_id
    path = data / "repo/genes/PSEPK/katE/katE-ai-review.yaml"
    doc = json.loads(path.read_text())
    doc["description"] = "Updated bacterial review"
    path.write_text(json.dumps(doc))
    assert build(data, reviews=data / "repo/genes")["build_id"] != old


def test_source_taxon_mismatch_fails(multispecies_data):
    source = json.loads((multispecies_data / "sources.json").read_text())["putida_gaf"]
    source["taxon"] = "NCBITaxon:6239"
    with pytest.raises(ValueError, match="unexpected taxon NCBITaxon:160488"):
        list(gaf_rows(multispecies_data / source["path"], source, {}))

    source["allowed_taxa"] = ["NCBITaxon:6239", "NCBITaxon:160488"]
    assert len(list(gaf_rows(multispecies_data / source["path"], source, {}))) == 1


def test_species_config_drives_labels_and_hierarchy(multispecies_data):
    """Labels, aliases and taxonomy placement come from the species config, not code."""
    catalog = Catalog(multispecies_data)
    assert catalog.manifest["species"]["pombe"]["aliases"] == ["SCHPO"]
    with TestClient(create_app(multispecies_data, "duckdb")) as client:
        script = client.get("/species.js")
        assert script.headers["content-type"].startswith("text/javascript")
        labels = json.loads(script.text.split("\n", 1)[0].removeprefix("const SPECIES_LABELS = ").rstrip(";"))
        assert labels["worm"] == "C. elegans"
        assert labels["SCHPO"] == labels["pombe"] == "Fission yeast"
        assert "AmigoSpecies" in script.text
    nodes = catalog.manifest["hierarchies"]["genes"][0]["nodes"]
    assert nodes["NCBITaxon:6239"] == {"label": "C. elegans", "parents": ["NCBITaxon:33208"]}
    assert nodes["NCBITaxon:160488"] == {"label": "P. putida KT2440", "parents": ["NCBITaxon:2"]}
