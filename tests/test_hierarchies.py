"""Subset membership, counts, and query semantics across both serving engines."""

import json
import os
from pathlib import Path

import pytest

from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.hierarchies import UNMAPPED_GO, go_hierarchy, species_hierarchy
from amigo3.ontology import load_ontology
from amigo3.query import Facet, Filter, Query
from amigo3.solr import SolrBackend, publish


def test_projection_skips_intermediates_preserves_diamond_and_aliases(tmp_path):
    path = tmp_path / "go.obo"
    path.write_text("""format-version: 1.2
data-version: test

[Term]
id: GO:0008150
name: biological_process

[Term]
id: GO:1
name: branch one
subset: goslim_generic
is_a: GO:0008150

[Term]
id: GO:2
name: branch two
subset: goslim_generic
is_a: GO:0008150

[Term]
id: GO:3
name: hidden intermediate
is_a: GO:1
relationship: part_of GO:2

[Term]
id: GO:4
name: shared slim category
subset: goslim_generic
is_a: GO:3

[Term]
id: GO:5
name: detailed term
alt_id: GO:ALIAS
is_a: GO:4

[Term]
id: GO:6
name: unused category
subset: goslim_generic
is_a: GO:0008150

[Term]
id: GO:7
name: regulator
is_a: GO:0008150
relationship: regulates GO:1
relationship: has_part GO:2

[Term]
id: GO:8
name: obsolete term
is_obsolete: true
is_a: GO:1
""")
    terms, _, _ = load_ontology(path)
    hierarchy, memberships = go_hierarchy(terms, {"GO:5", "GO:ALIAS", "GO:7", "GO:8", "GO:unknown"})
    assert hierarchy["nodes"]["GO:4"]["parents"] == ["GO:1", "GO:2"]
    assert memberships["GO:5"] == memberships["GO:ALIAS"] == ["GO:0008150", "GO:1", "GO:2", "GO:4"]
    assert memberships["GO:7"] == ["GO:0008150"]
    assert memberships["GO:8"] == memberships["GO:unknown"] == [UNMAPPED_GO]
    assert not {"GO:3", "GO:5", "GO:6"} & hierarchy["nodes"].keys()
    for term in terms:
        term["subsets"] = []
    with pytest.raises(ValueError, match="goslim_generic"):
        go_hierarchy(terms, {"GO:5"})


def lineages():
    results = json.loads((Path(__file__).parent / "fixtures/uniprot-taxonomy.json").read_text())["results"]
    return {
        f"NCBITaxon:{r['taxonId']}": (
            r["scientificName"],
            [f"NCBITaxon:{a['taxonId']}" for a in r["lineage"]],
        )
        for r in results
    }


def test_species_subset_keeps_unlisted_taxa():
    hierarchy, memberships = species_hierarchy(
        {
            "NCBITaxon:9606": "human",
            "NCBITaxon:4896": "pombe",
            "NCBITaxon:284812": "pombe",
            "NCBITaxon:123": "Other species",
        },
        lineages(),
    )
    assert hierarchy["nodes"]["NCBITaxon:33208"]["parents"] == ["NCBITaxon:33154"]
    assert "NCBITaxon:33154" in memberships["NCBITaxon:4896"]
    assert "NCBITaxon:33208" not in memberships["NCBITaxon:4896"]
    assert hierarchy["nodes"]["NCBITaxon:284812"]["parents"] == ["NCBITaxon:4896"]
    assert set(memberships["NCBITaxon:284812"]) == {*memberships["NCBITaxon:4896"], "NCBITaxon:284812"}
    assert memberships["NCBITaxon:123"] == ["NCBITaxon:123"]
    assert hierarchy["nodes"]["NCBITaxon:123"] == {"label": "Other species", "parents": []}


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_hierarchy_counts_and_combined_filters(multispecies_data, backend):
    catalog = Catalog(multispecies_data)
    if backend == "solr":
        url = os.environ.get("AMIGO3_TEST_SOLR")
        if not url:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(catalog, url)
        engine = SolrBackend(catalog, url)
    else:
        engine = DuckBackend(catalog)
    config = catalog.collections["annotations"]
    assert set(config["hierarchies"]) == {"taxon_groups", "go_slim"}
    assert "species" not in config["facet_groups"][0]["fields"]
    assert set(catalog.collections["genes"]["hierarchies"]) == {"taxon_groups", "gene_product_type_groups"}
    facets = {field: Facet(field=field, limit=1000) for field in config["hierarchies"]}
    result = engine.query(Query(collection="annotations", facets=facets))
    counts = {
        field: {b["value"]: b["count"] for b in facet["buckets"]} for field, facet in result["facets"].items()
    }
    assert result["total"] == 6
    assert counts["taxon_groups"]["NCBITaxon:33154"] == 5
    assert counts["taxon_groups"]["NCBITaxon:33208"] == 4
    assert counts["go_slim"] == {"GO:0003674": 6, "GO:0016301": 5}
    query = Query(
        collection="annotations",
        filters=[
            Filter(id="taxa", field="taxon_groups", values=["NCBITaxon:33208", "NCBITaxon:9606"]),
            Filter(id="go", field="go_slim", values=["GO:0016301"]),
        ],
        facets={
            "taxa": Facet(field="taxon_groups", exclude_filters=["taxa"], limit=1000),
            "go": Facet(field="go_slim", exclude_filters=["go"], limit=1000),
        },
    )
    result = engine.query(query)
    assert result["total"] == 3  # Parent + child selection is OR, not a duplicated union.
    assert sum(row["negation"] for row in result["hits"]) == 1
    assert {b["value"]: b["count"] for b in result["facets"]["go"]["buckets"]} == {
        "GO:0003674": 4,
        "GO:0016301": 3,
    }
    assert "NCBITaxon:4751" in {b["value"] for b in result["facets"]["taxa"]["buckets"]}
    query.filters.append(Filter(id="positive", field="negation", values=[False]))
    assert engine.query(query)["total"] == 2
    query.filters.append(Filter(id="evidence", field="evidence_code", values=["IEA"]))
    assert engine.query(query)["total"] == 0


def test_catalog_does_not_mutate_shared_presentation(multispecies_data):
    first = Catalog(multispecies_data)
    second = Catalog(multispecies_data)
    assert first.collections == second.collections


@pytest.mark.parametrize("backend", ["duckdb", "solr"])
def test_diamond_counts_once_and_overlapping_selection_is_union(hierarchy_data, backend):
    catalog = Catalog(hierarchy_data)
    if backend == "solr":
        url = os.environ.get("AMIGO3_TEST_SOLR")
        if not url:
            pytest.skip("Set AMIGO3_TEST_SOLR to exercise real Solr")
        publish(catalog, url)
        engine = SolrBackend(catalog, url)
    else:
        engine = DuckBackend(catalog)
    query = Query(
        collection="annotations",
        filters=[
            Filter(id="go", field="go_slim", values=["GO:9000001", "GO:9000002"]),
        ],
        facets={"go": Facet(field="go_slim", limit=1000, exclude_filters=["go"])},
    )
    result = engine.query(query)
    assert result["total"] == 5
    assert {b["value"]: b["count"] for b in result["facets"]["go"]["buckets"]} == {
        "GO:0003674": 6,
        "GO:0016301": 5,
        "GO:9000001": 5,
        "GO:9000002": 5,
    }
