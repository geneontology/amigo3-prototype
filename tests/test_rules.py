import hashlib

from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.rules import entries, identity


def test_rules_mapping_semantics_reviews_and_provenance(built_data):
    with TestClient(create_app(built_data, "duckdb")) as c:
        results = c.post("/api/query", json=dict(collection="rules", page=dict(size=100))).json()
        assert results["total"] == 6
        rows = results["hits"]
        mapping = next(r for r in rows if r["predicate_modifier"] == "Not")
        assert not mapping["has_review"]  # A predicate is not a structured review action.
        assert mapping["review_action"] == ""
        detail = c.get("/api/resources/rules/" + mapping["id"]).json()
        assert detail["documents"]["mapping"]["custom_extension"] == "keep this"
        assert detail["documents"]["effective_mapping"]["subject_source"] == "InterPro"
        assert "creator_id" not in detail["documents"]["effective_mapping"]
        assert detail["related"]["projects"]["items"][0]["id"] == "PROJECT:TEST"
        assert "rules" in c.get("/api/terms/GO:0004672").json()["related"]
        assert len(c.get("/api/projects/PROJECT:TEST").json()["record"]["rule_ids"]) == 2
        snap = detail["sources"][0]
        response = c.get("/api/snapshots/" + snap["sha256"])
        assert hashlib.sha256(response.content).hexdigest() == snap["sha256"]
        source = built_data / "review_repo" / snap["path"]
        source.write_text("changed after build")
        assert c.get("/api/snapshots/" + snap["sha256"]).content == response.content
        rule = c.get("/api/resources/rules/RULE:ARBA1").json()
        groups = rule["documents"]["rule"]["condition_sets"]
        assert len(groups) == 2 and len(groups[0]["conditions"]) == 2
        assert groups[0]["conditions"][1]["negated"] is True
        assert rule["record"]["family_ids"] == ["PANTHER:PTHR00001"]
        assert (
            c.get("/api/resources/rules/RULE:ARBA2").json()["record"]["antecedent_status"] == "Not captured"
        )
        no_term = next(r for r in rows if r["object_id"] == "sssom:NoTermFound")
        assert no_term["term_ids"] == []
        conditional = next(r for r in rows if r["kind"] == "Conditional mapping")
        assert conditional["review_status"] == "no_mapping"
        doc = c.get("/api/resources/rules/" + conditional["id"]).json()["documents"]["curation"]
        assert doc["excluded_subjects"][0]["condition_code"] == "X"
        proposal = next(r for r in rows if r["kind"] == "Family annotation proposal")
        assert proposal["gene_ids"] == ["HGNC:2"]  # Referenced counterexample, NOT matched member.
        assert c.get("/rules/" + mapping["id"]).status_code == 200
        assert c.get("/api/resources/rules/RULE:missing").status_code == 404


def test_mapping_identity_and_arbitrary_predicate():
    a = dict(subject_id="X:1", predicate_id="X:custom", object_id="Y:1", comment="A")
    b = a | dict(comment="B")
    assert identity("a", a) != identity("a", b)
    assert identity("a", a) != identity("b", a)
    assert identity("a", a) == identity("a", dict(reversed(list(a.items()))))
    source = dict(mappings=[a, b])
    first = [identity("a", row) for _, _, row in entries("a", source)]
    second = [identity("a", row) for _, _, row in entries("a", dict(mappings=[b, a]))]
    assert first == second[::-1]
