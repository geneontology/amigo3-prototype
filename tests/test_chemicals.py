"""Local chemical pages join exact identifiers, not lexical or activity-type guesses."""

from fastapi.testclient import TestClient

from amigo3.api import create_app


def test_local_chemical_reverse_links_and_shared_label(chemical_data):
    with TestClient(create_app(chemical_data, "duckdb")) as client:
        response = client.get("/api/chemicals/CHEBI:57540")
        assert response.status_code == 200
        detail = response.json()
        assert detail["record"]["label"] == "NAD(+)"  # Rhea label remains authoritative.
        assert detail["reactions"]["total"] == 3
        assert detail["reactions"]["distinct_rhea_count"] == 2
        assert all(item["on_left"] and not item["on_right"] for item in detail["reactions"]["items"])
        assert detail["knowledge_links_available"]
        for collection, identifier in [("gocams", "gomodel:test"), ("modules", "MODULE:test")]:
            group = detail["related"][collection]
            assert group["total"] == 1
            assert group["items"][0]["id"] == identifier
            query = client.post(
                "/api/query",
                json={
                    "collection": collection,
                    "filters": [{"id": "chemical", "field": "chemical_ids", "values": ["CHEBI:57540"]}],
                },
            ).json()
            assert query["total"] == group["total"]
        assert client.get("/chemicals/CHEBI%3A57540").status_code == 200


def test_model_only_chemical_and_unused_vocabulary(chemical_data):
    with TestClient(create_app(chemical_data, "duckdb")) as client:
        detail = client.get("/api/chemicals/CHEBI:999999").json()
        assert detail["record"]["label"] == "Model-only chemical"
        assert detail["reactions"]["total"] == 0
        assert detail["related"]["gocams"]["total"] == 1
        assert detail["record"]["source_id"] == "knowledge:gocams"
        assert client.get("/api/snapshots/" + detail["record"]["source_sha256"]).status_code == 200
        assert client.get("/api/chemicals/CHEBI:888888").status_code == 404
        # H+ is used by the same Rhea reaction, but the model does not mention it.
        # A mapped molecular function is not direct evidence of participation.
        proton = client.get("/api/chemicals/CHEBI:15378").json()
        assert proton["related"]["gocams"]["total"] == 0
