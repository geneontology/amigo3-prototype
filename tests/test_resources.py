import hashlib

from fastapi.testclient import TestClient

from amigo3.api import create_app


def test_term_page_preserves_typed_edges_and_counts(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        detail = client.get("/api/terms/GO:9916301").json()
        assert detail["record"]["id"] == "GO:0016301"
        assert detail["counts"]["annotation_count"] == 3
        assert detail["counts"]["direct_annotation_count"] == 1
        assert detail["counts"]["negated_annotation_count"] == 1
        assert {e["id"] for e in detail["children"]} == {"GO:0004672"}
        regulation = client.get("/api/terms/GO:9999999").json()
        assert regulation["counts"]["annotation_count"] == 0
        assert regulation["children"][0]["predicate"] == "regulates"
        assert client.get("/api/terms/GO:missing").status_code == 404
        assert client.get("/terms/GO:0016301").status_code == 200


def test_context_collections_preserve_scope_and_snapshots(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        catalog = client.get("/api/collections").json()["collections"]
        assert {c: catalog[c]["count"] for c in ("gocams", "modules", "families")} == {
            "gocams": 1,
            "modules": 1,
            "families": 1,
        }
        model = client.get("/api/resources/gocams/gomodel:test").json()
        # Input molecules and enabled_by remain distinguishable, with links to both genes.
        assert model["documents"]["model"]["activities"][0]["enabled_by"]["term"] == "UniProtKB:P00001"
        assert model["documents"]["model"]["molecules"][0]["term"] == "UniProtKB:P00002"
        assert model["record"]["gene_ids"] == ["HGNC:1", "HGNC:2"]
        assert model["related"]["modules"]["items"][0]["id"] == "MODULE:test"
        # The GO-CAM review travels as a separate, attributed source document.
        assert model["record"]["has_review"] and model["record"]["description"] == "A one-step kinase model."
        findings = model["documents"]["review"]["activity_reviews"]
        assert findings[0]["activity_id"] == "gomodel:test/a"
        assert findings[0]["qc_flags"] == ["DIRECT_VS_INDIRECT_CAUSAL", "MISSING_LOCATION"]
        assert {s["role"] for s in model["sources"]} == {"model", "review"}
        module = client.get("/api/resources/modules/MODULE:test").json()
        variants = module["documents"]["module"]["module"]["variant_sets"][0]
        assert variants["selection"] == "ONE" and len(variants["variants"]) == 2
        family = client.get("/api/resources/families/PANTHER:PTHR00001").json()
        assert family["record"]["member_count"] == 2
        assert family["record"]["reported_protein_count"] == 1000
        assert family["record"]["gene_ids"] == ["HGNC:1"]
        assert family["documents"]["paint"][0]["negated"] == "true"
        assert family["documents"]["paint"][0]["evidence"] == "IBD"
        assert family["related"]["modules"]["total"] == 1  # subfamily reference
        source = next(s for s in family["sources"] if s["role"] == "entries")
        snapshot = client.get("/api/snapshots/" + source["sha256"])
        assert hashlib.sha256(snapshot.content).hexdigest() == source["sha256"]
        (built_data / "review_repo" / source["path"]).write_text("later edit")
        assert client.get("/api/snapshots/" + source["sha256"]).content == snapshot.content
        assert client.get("/api/genes/HGNC:1").json()["related"]["families"]["total"] == 1
        # Importing module/PAINT context has not manufactured any GAF annotations.
        assert client.post("/api/query", json={"collection": "annotations"}).json()["total"] == 4
        assert client.get("/api/resources/families/PANTHER:missing").status_code == 404
        for route in ("gocams/gomodel:test", "modules/MODULE:test", "families/PANTHER:PTHR00001"):
            assert client.get("/" + route).status_code == 200


def test_related_mapping_retains_endpoints_predicate_and_modifier(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        detail = client.get("/api/terms/GO:0004672").json()
        mapping = next(
            item for item in detail["related"]["rules"]["items"] if item["kind"] == "SSSOM mapping"
        )
        assert mapping["subject_id"] == "InterPro:IPR1"
        assert mapping["subject_label"] == "Domain"
        assert mapping["object_id"] == "GO:0004672"
        assert mapping["predicate_id"] == "skos:exactMatch"
        assert mapping["predicate_modifier"] == "Not"
