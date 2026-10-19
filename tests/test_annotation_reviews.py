import json

import duckdb
from fastapi.testclient import TestClient

from amigo3.annotation_reviews import index_reviews
from amigo3.api import create_app
from amigo3.catalog import Catalog


def test_indexed_assessments_agree_with_gene_page_and_frozen_details(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        rows = client.post("/api/query", json={"collection": "annotations"}).json()["hits"]
        gene = client.get("/api/genes/HGNC:1").json()
        expected = {r["id"]: r["review_entry_ids"] for r in gene["annotations"]}
        for row in rows:
            assert row["review_entry_ids"] == expected.get(row["id"], [])
        actions = client.post(
            "/api/query",
            json={"collection": "annotations", "facets": {"actions": {"field": "review_actions"}}},
        ).json()["facets"]["actions"]["buckets"]
        assert {b["value"]: b["count"] for b in actions} == {"ACCEPT": 1, "MODIFY": 1}
        accepted = client.post(
            "/api/query",
            json={
                "collection": "annotations",
                "filters": [{"id": "action", "field": "review_actions", "values": ["ACCEPT"]}],
            },
        ).json()
        assert accepted["total"] == 1
        row = accepted["hits"][0]
        assert row["has_annotation_review"] and row["has_review"]
        assert not row["review_multiple"] and row["review_count"] == 1
        detail = client.post("/api/annotation-reviews", json={"annotation_ids": [row["id"]]}).json()
        item = detail["assessments"][row["id"]][0]
        assert item["entry"]["review"]["summary"] == "Kinase activity supported."
        assert item["path"].endswith("GENE1-ai-review.yaml")
        assert len(item["sha256"]) == 64
        assert (
            client.post(
                "/api/annotation-reviews", json={"annotation_ids": [row["id"]], "view": "wrong"}
            ).status_code
            == 422
        )
        assert client.post("/api/annotation-reviews", json={"annotation_ids": ["x"] * 101}).status_code == 422
        config = client.get("/api/collections").json()["collections"]["annotations"]
        assert config["field_labels"]["has_review"] == "Gene review available"
        assert config["facet_groups"][-1]["visible_when"]["display_fields"] == ["assessments"]


def test_multiple_assessments_and_gene_review_without_matching_entry(built_data):
    # Use a private in-memory copy: one source row gets two assessments; another
    # shares a reviewed gene but differs in the tested isoform and must not match.
    catalog = Catalog(built_data)
    with duckdb.connect() as db:
        db.execute(
            "CREATE TABLE annotations AS SELECT * EXCLUDE(has_annotation_review,review_entry_ids,review_actions,review_replacement_ids,review_has_support,review_count,review_multiple) FROM read_parquet(?)",
            [str(catalog.path / "annotations.parquet")],
        )
        original = {"term": {"id": "GO:0004672"}, "evidence_type": "IDA", "original_reference_id": "PMID:123"}
        entries = [
            {**original, "review": {"action": "ACCEPT", "reason": "First assessment"}},
            {
                **original,
                "review": {
                    "action": "MODIFY",
                    "reason": "Second assessment",
                    "proposed_replacement_terms": [{"id": "GO:0016301"}],
                    "supported_by": [{"reference_id": "PMID:123", "supporting_text": "Frozen evidence"}],
                },
            },
            {**original, "review": {"action": "NEW"}},
        ]
        index_reviews(
            db,
            [
                {
                    "gene_id": "HGNC:1",
                    "review_path": "genes/test.yaml",
                    "review_sha256": "a" * 64,
                    "review_document": json.dumps({"id": "P00001", "existing_annotations": entries}),
                }
            ],
        )
        result = db.execute(
            "SELECT has_annotation_review,review_actions,review_count,review_multiple,review_has_support,review_replacement_ids FROM annotations WHERE gene_id='HGNC:1' ORDER BY ontology_class_id"
        ).fetchall()
        assert result == [
            (False, [], 0, False, False, []),
            (True, ["ACCEPT", "MODIFY"], 2, True, True, ["GO:0016301"]),
        ]
        assert db.execute("SELECT count(*) FROM annotations").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM annotation_review_links").fetchone()[0] == 2


def test_empty_review_index(built_data):
    catalog = Catalog(built_data)
    with duckdb.connect() as db:
        db.execute(
            "CREATE TABLE annotations AS SELECT * EXCLUDE(has_annotation_review,review_entry_ids,review_actions,review_replacement_ids,review_has_support,review_count,review_multiple) FROM read_parquet(?)",
            [str(catalog.path / "annotations.parquet")],
        )
        index_reviews(db, [])
        assert db.execute("SELECT count(*) FROM annotations WHERE has_annotation_review").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM annotation_review_entries").fetchone()[0] == 0
