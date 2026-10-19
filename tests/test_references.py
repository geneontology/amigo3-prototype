import json
import shutil

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.build import build
from amigo3.references import knowledge_citations, review_citations, split_publication

PUBLICATION = """---
pmid: '123'
title: A kinase that phosphorylates things.
authors:
- Smith A
- Jones B
- Brown C
- Distinctauthorname D
journal: J Test Biol
year: '2001'
doi: 10.1234/test.123
publication_type: PRIMARY_RESEARCH
full_text_available: true
---

# A kinase that phosphorylates things.

## Abstract

GENE1 phosphorylates substrates in vitro.

## Full Text

Copyrighted-full-text-token must not be published.
"""


@pytest.fixture(scope="module")
def reference_data(built_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("references") / "data"
    shutil.copytree(built_data, data)
    repository = data / "review_repo"
    (repository / "publications").mkdir()
    (repository / "publications/PMID_123.md").write_text(PUBLICATION)
    (repository / "publications/notes.md").write_text("Not a publication.")
    review_path = repository / "genes/human/GENE1/GENE1-ai-review.yaml"
    review = json.loads(review_path.read_text())
    review["references"] = [
        {
            "id": "PMID:123",
            "title": "Review-supplied title",
            "reference_review": {"relevance": "HIGH", "correctness": "VERIFIED"},
            "findings": [{"statement": "Kinase activity shown directly."}],
        },
        {"id": "file:human/GENE1/GENE1-notes.md", "title": "Local notes"},
    ]
    review["existing_annotations"][0]["review"]["supported_by"] = [
        {"reference_id": "PMID:123", "supporting_text": "GENE1 phosphorylates substrates"}
    ]
    review_path.write_text(json.dumps(review))
    model_path = repository / "gocams/test/test-src.yaml"
    model = json.loads(model_path.read_text())
    model["activities"][0]["molecular_function"]["evidence"] = [
        {"term": "ECO:0000314", "reference": "PMID:123"}
    ]
    model_path.write_text(json.dumps(model))
    build(data, repository=repository)
    return data


def test_publication_cache_exposes_metadata_and_abstract_only():
    metadata, abstract = split_publication(PUBLICATION)
    assert metadata["title"] == "A kinase that phosphorylates things."
    assert abstract == "GENE1 phosphorylates substrates in vitro."
    assert split_publication("No frontmatter") == ({}, "")


def test_citations_come_from_structured_fields_only():
    document = {
        "description": "Mentions PMID:555 in prose.",
        "references": [{"id": "PMID:1", "title": "One"}, {"id": "file:x.md"}],
        "existing_annotations": [
            {
                "term": {"id": "GO:1", "label": "one"},
                "evidence_type": "IDA",
                "original_reference_id": "PMID:2",
                "review": {
                    "action": "ACCEPT",
                    "supported_by": [{"reference_id": "PMID:1", "supporting_text": "q"}],
                },
            }
        ],
        "core_functions": [
            {
                "molecular_function": {"id": "GO:3", "label": "three"},
                "supported_by": [{"reference_id": "PMID:3"}],
            }
        ],
    }
    found = [
        (identifier, role, detail.get("term_id")) for identifier, role, detail in review_citations(document)
    ]
    assert found == [
        ("PMID:1", "bibliography", None),
        ("PMID:2", "annotation", "GO:1"),
        ("PMID:1", "support", "GO:1"),
        ("PMID:3", "support", "GO:3"),
    ]
    model = {
        "evidence": [{"reference": "PMID:4"}],
        "references": ["PMID:5", {"id": "PMID:6"}],
        "objects": [{"id": "UniProtKB:P1"}],
        "notes": "PMID:7",
    }
    assert knowledge_citations(model) == {"PMID:4", "PMID:5", "PMID:6"}


def test_reference_records_join_annotations_reviews_and_models(reference_data):
    with TestClient(create_app(reference_data, "duckdb")) as client:
        found = client.post(
            "/api/query",
            json={"collection": "references", "facets": {"kind": {"field": "kind"}}},
        ).json()
        ids = {hit["id"] for hit in found["hits"]}
        assert {"PMID:123", "PMID:999", "GO_REF:0000002"} <= ids
        assert not any(identifier.startswith("file:") for identifier in ids)
        assert {b["value"] for b in found["facets"]["kind"]["buckets"]} == {"PMID", "GO_REF"}
        record = next(hit for hit in found["hits"] if hit["id"] == "PMID:123")
        assert record["label"] == "A kinase that phosphorylates things."
        assert record["description"] == "Smith A, Jones B, Brown C et al. · J Test Biol (2001)"
        assert record["annotation_count"] == 3
        assert record["gene_count"] == 2
        assert record["review_gene_ids"] == ["HGNC:1"]
        assert record["model_ids"] == ["gomodel:test"]
        assert record["has_abstract"] and record["has_review"]
        assert "search_text" not in record
        # Cited only by a review: no annotations, title-less, still navigable.
        review_only = next(hit for hit in found["hits"] if hit["id"] == "PMID:999")
        assert review_only["annotation_count"] == 0 and review_only["has_review"]
        searched = client.post(
            "/api/query", json={"collection": "references", "search": "Distinctauthorname"}
        ).json()
        assert [hit["id"] for hit in searched["hits"]] == ["PMID:123"]

        detail = client.get("/api/references/PMID:123").json()
        assert detail["publication"]["abstract"] == "GENE1 phosphorylates substrates in vitro."
        assert "Copyrighted-full-text-token" not in json.dumps(detail)
        assert {g["symbol"] for g in detail["annotations"]["genes"]} == {"GENE1", "GENE2"}
        assert {t["id"] for t in detail["annotations"]["terms"]} == {"GO:0004672", "GO:0003674"}
        [review] = detail["reviews"]
        assert review["symbol"] == "GENE1"
        assert review["bibliography"]["reference_review"]["relevance"] == "HIGH"
        assert review["supports"] == [
            {
                "section": "existing_annotations",
                "term_id": "GO:0004672",
                "term_label": None,
                "supporting_text": "GENE1 phosphorylates substrates",
            }
        ]
        assert {a["action"] for a in review["annotations"]} == {"ACCEPT", "MODIFY", "NEW"}
        assert detail["related"]["gocams"][0]["id"] == "gomodel:test"

        annotations = client.post(
            "/api/query",
            json={
                "collection": "annotations",
                "filters": [{"id": "reference", "field": "references", "values": ["PMID:123"]}],
            },
        ).json()
        assert annotations["total"] == record["annotation_count"]
        assert client.get("/api/references/PMID:missing").status_code == 404
        assert client.get("/references/PMID:123").status_code == 200
        report = client.get("/api/report").json()["references"]
        assert report["cached_publications"] == 1


def test_build_without_publications_still_indexes_citations(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        detail = client.get("/api/references/GO_REF:0000002").json()
        assert detail["publication"] is None
        assert detail["record"]["annotation_species"] == ["pombe"]
        assert detail["reviews"] == []
