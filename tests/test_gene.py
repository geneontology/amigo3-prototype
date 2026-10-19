from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.gene import matches


def test_gene_page_all_annotations_and_frozen_review_context(built_data):
    # Changes to mutable source YAML cannot change an already-published snapshot.
    source = built_data / "review_repo/genes/human/GENE1/GENE1-ai-review.yaml"
    original = source.read_text()
    source.write_text('{"description": "Later edit"}')
    try:
        with TestClient(create_app(built_data, "duckdb")) as client:
            response = client.get("/api/genes/HGNC:1")
            assert response.status_code == 200
            gene = response.json()
            assert gene["annotation_count"] == gene["record"]["annotation_count"] == 2
            assert len(gene["annotations"]) == 2
            assert gene["reviews"][0]["document"]["description"] == "Frozen review summary."
            entries = {entry["entry"]["review"]["action"]: entry for entry in gene["review_entries"]}
            assert len(entries["ACCEPT"]["annotation_ids"]) == 1
            assert len(entries["MODIFY"]["annotation_ids"]) == 1
            assert entries["REMOVE"]["annotation_ids"] == []
            assert entries["NEW"]["annotation_ids"] == []
            assert all(len(a["review_entry_ids"]) == 1 for a in gene["annotations"])
            assert client.get("/genes/HGNC:1").status_code == 200
            assert client.get("/api/genes/missing").status_code == 404
            empty = client.get("/api/genes/HGNC:3").json()
            assert empty["annotations"] == empty["reviews"] == []
    finally:
        source.write_text(original)


@pytest.mark.parametrize(
    "override",
    [
        {"original_reference_id": "PMID:wrong"},
        {"evidence_type": "IEA"},
        {"term": {"id": "GO:wrong"}},
        {"negated": True},
        {"isoform": "P00001-2"},
        {"review": {"action": "NEW"}},
    ],
)
def test_review_context_does_not_cross_assertion_keys(override):
    annotation = {
        "references": ["PMID:123"],
        "ontology_class_id": "GO:1",
        "evidence_code": "IDA",
        "negation": False,
        "gene_product_form_id": "",
        "canonical_subject_id": "UniProtKB:P00001",
    }
    entry = {
        "term": {"id": "GO:1"},
        "evidence_type": "IDA",
        "original_reference_id": "PMID:123",
        "review": {"action": "ACCEPT"},
    }
    assert matches(annotation, entry, "UniProtKB:P00001", [])
    changed = deepcopy(entry)
    changed.update(override)
    assert not matches(annotation, changed, "UniProtKB:P00001", [])
    assert not matches(annotation, entry, "UniProtKB:P00002", ["UniProtKB:P00001", "UniProtKB:P00002"])
    annotation["canonical_subject_id"] = "PomBase:SPTEST.1"
    assert matches(annotation, entry, "UniProtKB:P00001", ["UniProtKB:P00001"])
    assert not matches(annotation, entry, "UniProtKB:P00001", [])


def test_repeated_base_accession_in_form_column_is_not_an_isoform():
    annotation = {
        "references": ["PMID:123"],
        "ontology_class_id": "GO:1",
        "evidence_code": "IDA",
        "negation": False,
        "gene_product_form_id": "UniProtKB:P00001",
        "canonical_subject_id": "UniProtKB:P00001",
    }
    entry = {"term": {"id": "GO:1"}, "evidence_type": "IDA", "original_reference_id": "PMID:123"}
    assert matches(annotation, entry, "UniProtKB:P00001", [])
    annotation["gene_product_form_id"] = "UniProtKB:P00001-1"
    assert not matches(annotation, entry, "UniProtKB:P00001", [])


def test_gene_redundancy_iba_priority_and_standin_core_function(built_data, tmp_path_factory):
    import hashlib
    import json
    import shutil

    from amigo3.build import build
    from amigo3.sources import write_json

    data = tmp_path_factory.mktemp("iba-standin") / "data"
    shutil.copytree(built_data, data)
    # Add an IBA annotation and broader canonical/context-varied annotations on GENE1, plus IBA/IEA on GENE2.
    with (data / "human_gaf.txt").open("a") as stream:
        # IBA on GENE1 for GO:0004672 (exact match to core_function if added)
        stream.write(
            "UniProtKB\tP00001\tGENE1\tenables\tGO:0004672\tGO_REF:0000033\tIBA\tPANTHER:PTN0001\tF\tGene one\t\tprotein\ttaxon:9606\t20260101\tGO_Central\t\t\n"
        )
        # Broader GO:0016301 on canonical P00001 with same relation (enables) and no extensions -> redundant
        stream.write(
            "UniProtKB\tP00001\tGENE1\tenables\tGO:0016301\tGO_REF:0000002\tIEA\tInterPro:IPR0001\tF\tGene one\t\tprotein\ttaxon:9606\t20260101\tUniProt\t\t\n"
        )
        # Broader GO:0016301 on canonical P00001 with different relation (contributes_to) -> NOT redundant
        stream.write(
            "UniProtKB\tP00001\tGENE1\tcontributes_to\tGO:0016301\tPMID:999\tIDA\t\tF\tGene one\t\tprotein\ttaxon:9606\t20260101\tUniProt\t\t\n"
        )
        # Positive IBA and broader IEA on unreviewed GENE2
        stream.write(
            "UniProtKB\tP00002\tGENE2\tenables\tGO:0004672\tGO_REF:0000033\tIBA\tPANTHER:PTN0002\tF\tGene two\t\tprotein\ttaxon:9606\t20260101\tGO_Central\t\t\n"
        )
        stream.write(
            "UniProtKB\tP00002\tGENE2\tenables\tGO:0016301\tGO_REF:0000002\tIEA\tInterPro:IPR0001\tF\tGene two\t\tprotein\ttaxon:9606\t20260101\tUniProt\t\t\n"
        )
    review_path = data / "review_repo/genes/human/GENE1/GENE1-ai-review.yaml"
    review_doc = {
        "id": "P00001",
        "gene_symbol": "GENE1",
        "description": "Review with core function.",
        "core_functions": [
            {
                "description": "Catalyzes protein phosphorylation",
                "molecular_function": {"id": "GO:0004672", "label": "protein kinase activity"},
            }
        ],
        "existing_annotations": [
            {
                "term": {"id": "GO:0004672"},
                "evidence_type": "IDA",
                "original_reference_id": "PMID:123",
                "review": {"action": "ACCEPT", "summary": "Kinase activity supported."},
            }
        ],
    }
    review_path.write_text(json.dumps(review_doc))
    manifest = json.loads((data / "sources.json").read_text())
    for source in manifest.values():
        source["sha256"] = hashlib.sha256((data / source["path"]).read_bytes()).hexdigest()
    write_json(data / "sources.json", manifest)
    build(data, repository=data / "review_repo")

    with TestClient(create_app(data, "duckdb")) as client:
        g1 = client.get("/api/genes/HGNC:1").json()
        # IBA annotation is ordered first by default on the gene page
        assert g1["annotations"][0]["evidence_code"] == "IBA"
        assert g1["annotations"][0]["evidence_group"] == "iba"
        assert g1["annotations"][0]["core_agreement"] == "exact"
        # Redundancy respects isoform, relation, and annotation_extensions context:
        by_key = {
            (a["ontology_class_id"], a["relation"], a["gene_product_form_id"]): a for a in g1["annotations"]
        }
        # GO:0003674 on isoform UniProtKB:P00001-2 with extensions is NOT redundant with canonical GO:0004672
        assert by_key[("GO:0003674", "enables", "UniProtKB:P00001-2")]["is_redundant"] is False
        # GO:0016301 on canonical P00001 with relation enables and no extensions IS redundant with GO:0004672
        assert by_key[("GO:0016301", "enables", "")]["is_redundant"] is True
        # GO:0016301 on canonical P00001 with different relation contributes_to is NOT redundant
        assert by_key[("GO:0016301", "contributes_to", "")]["is_redundant"] is False
        assert by_key[("GO:0004672", "enables", "")]["is_redundant"] is False
        # Reviewed gene has core_iba_agreements and no stand-in iba_core_function
        assert g1["core_iba_agreements"][0]["status"] == "exact"
        assert g1["iba_core_function"] is None

        # Unreviewed GENE2 has stand-in iba_core_function synthesized from its IBA annotation
        g2 = client.get("/api/genes/HGNC:2").json()
        assert g2["iba_core_function"] is not None
        assert g2["iba_core_function"]["source"] == "PAINT / IBA"
        assert g2["iba_core_function"]["molecular_functions"][0]["id"] == "GO:0004672"
        # And GO:0016301 is marked redundant with GO:0004672 on GENE2
        g2_pos = {a["ontology_class_id"]: a for a in g2["annotations"] if not a["negation"]}
        assert g2_pos["GO:0016301"]["is_redundant"] is True
        assert g2_pos["GO:0004672"]["is_redundant"] is False
