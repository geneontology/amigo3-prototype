from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.details import model_processes


def test_model_processes_are_distinct_ranked_and_include_larger_processes():
    model = {
        "activities": [
            {"id": "a", "part_of": {"term": "GO:0000002"}},
            {"id": "b", "part_of": {"term": "GO:0000001", "part_of": {"term": "GO:0000003"}}},
            {"id": "c", "part_of": {"term": "GO:0000001"}},
            # One activity contributes once per process, however it is reached.
            {"id": "d", "part_of": [{"term": "GO:0000001"}, {"term": "GO:0000001"}]},
            {"id": "e", "part_of": {"term": "GO:0000004", "part_of": "GO:0000003"}},
            {"id": "f", "molecular_function": {"term": "GO:0004672"}},
            {"id": "g", "part_of": {"term": "CHEBI:1"}},
        ]
    }
    processes = model_processes(model, {"GO:0000001": "signaling"})
    assert [(p["id"], p["activity_count"]) for p in processes] == [
        ("GO:0000001", 3),
        ("GO:0000003", 2),
        ("GO:0000002", 1),
        ("GO:0000004", 1),
    ]
    assert processes[0]["label"] == "signaling"
    assert processes[1]["label"] == "GO:0000003"  # unlabeled terms fall back to the identifier
    assert model_processes(None, {}) == []
    assert model_processes({"activities": [{"id": "x"}]}, {}) == []


def test_gocam_api_exposes_processes(megamodel_data, built_data):
    with TestClient(create_app(megamodel_data, "duckdb")) as client:
        processes = client.get("/api/resources/gocams/gomodel:mega-a").json()["processes"]
        assert [(p["id"], p["activity_count"]) for p in processes] == [("GO:0008150", 2)]
        assert processes[0]["label"]
        assert "processes" not in client.get("/api/resources/modules/MODULE:test").json()
    with TestClient(create_app(built_data, "duckdb")) as client:
        # A model without part_of associations has an empty list, not a missing field.
        assert client.get("/api/resources/gocams/gomodel:test").json()["processes"] == []
