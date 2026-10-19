from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.megamodels import CRITERIA, MegamodelRequest, ModelIndex, model_index


def index(docs):
    return ModelIndex((d, {"path": d["id"] + ".yaml", "sha256": "example"}) for d in docs)


def explore(docs, **kwargs):
    return index(docs).explore(MegamodelRequest(model_ids=[d["id"] for d in docs], **kwargs))


def test_all_link_types_preserve_anchors_and_direction(megamodel_documents):
    result = explore(megamodel_documents, criteria=list(CRITERIA))
    assert {c["criterion"] for c in result["links"]} == set(CRITERIA)
    assert len(result["activities"]) == 4
    assert len(result["causal_edges"]) == 2
    assert result["causal_edges"][0]["assertion"]["evidence"] == [{"reference": "PMID:123"}]
    terminal = [c for c in result["links"] if c["criterion"] == "terminal_to_initial"]
    assert len(terminal) == 2
    assert all(c["source_activity"].endswith("/b") and c["target_activity"].endswith("/a") for c in terminal)
    flows = [c for c in result["links"] if c["criterion"] == "chemical_flow"]
    assert len(flows) == 2  # Both directions; discovered once even with both models selected.
    assert all(c["source_activity"].endswith("/a") and c["target_activity"].endswith("/b") for c in flows)
    assert all(c["candidate"] and c["participants"][0]["location_match"] == "same" for c in flows)


@pytest.mark.parametrize("taxon", ["NCBITaxon:4896", ""])
def test_taxon_is_required_and_must_match(megamodel_documents, taxon):
    docs = deepcopy(megamodel_documents)
    docs[1]["taxon"] = taxon
    assert not explore(docs, criteria=list(CRITERIA))["links"]
    if not taxon:
        docs[0]["taxon"] = ""
        assert not explore(docs, criteria=list(CRITERIA))["links"]


def test_context_is_required_and_isolated_is_not_a_chain_end(megamodel_documents):
    docs = deepcopy(megamodel_documents)
    for doc in docs:
        for a in doc["activities"]:
            a.pop("causal_associations", None)
            a.pop("part_of")
    result = explore(docs, criteria=["gene_mf", "full_activity_signature", "terminal_to_initial"])
    assert {c["criterion"] for c in result["links"]} == {"gene_mf"}
    for doc in docs:
        for a in doc["activities"]:
            a["part_of"] = {"term": "GO:0008150"}
    assert not explore(docs, criteria=["terminal_to_initial"])["links"]


def test_currency_and_compartment_policies(megamodel_documents):
    docs = deepcopy(megamodel_documents)
    for doc in docs:
        for a in doc["activities"]:
            a["molecular_associations"][0]["molecule"] = "CHEBI:15377"
    assert not explore(docs, criteria=["chemical_flow"])["links"]
    assert len(explore(docs, criteria=["chemical_flow"], include_currency=True)["links"]) == 2
    for a in docs[1]["activities"]:
        a.pop("occurs_in")
    assert not explore(docs, criteria=["chemical_flow"], include_currency=True)["links"]
    result = explore(docs, criteria=["chemical_flow"], include_currency=True, chemical_location="compatible")
    assert len(result["links"]) == 2
    assert result["links"][0]["participants"][0]["location_match"] == "unknown"
    for a in docs[1]["activities"]:
        a["occurs_in"] = {"term": "GO:0005634"}
    assert not explore(
        docs, criteria=["chemical_flow"], include_currency=True, chemical_location="compatible"
    )["links"]
    assert (
        len(
            explore(docs, criteria=["chemical_flow"], include_currency=True, chemical_location="any")["links"]
        )
        == 2
    )


def test_molecule_instances_and_multiple_anchors(megamodel_documents):
    docs = deepcopy(megamodel_documents)
    for doc in docs:
        doc["molecules"] = [{"id": "instance:1", "term": "CHEBI:123"}]
        for a in doc["activities"]:
            a["molecular_associations"][0]["molecule"] = "instance:1"
    duplicate = deepcopy(docs[0]["activities"][0])
    duplicate["id"] += "extra"
    docs[0]["activities"].append(duplicate)
    result = explore(docs, criteria=["chemical_flow"])
    assert len(result["links"]) == 3
    assert result["links"][0]["participants"][0]["source"]["molecule_id"] == "instance:1"


def test_repeated_activity_ids_are_distinct_and_ambiguous_targets_are_not_guessed(megamodel_documents):
    docs = deepcopy(megamodel_documents)
    duplicate = deepcopy(docs[0]["activities"][1])
    duplicate["occurs_in"]["term"] = "GO:0005634"
    docs[0]["activities"].append(duplicate)
    result = explore(docs)
    assert len(result["activities"]) == 5
    assert len({a["id"] for a in result["activities"]}) == 5
    assert result["models"][0]["duplicate_activity_ids"] == 1
    assert len(result["causal_edges"]) == 1
    assert result["external_causal_edges"][0]["unresolved_reason"] == "ambiguous activity ID"


def test_neighbors_and_limits(megamodel_documents, monkeypatch):
    idx = index(megamodel_documents)
    result = idx.explore(MegamodelRequest(model_ids=["gomodel:mega-a"]))
    assert not result["links"]
    assert result["neighbor_count"] == 1
    neighbor = result["neighbors"][0]
    assert neighbor["model"]["id"] == "gomodel:mega-b"
    assert neighbor["anchors"][0]["source_activity"]
    assert neighbor["anchors"][0]["target_label"]
    monkeypatch.setattr("amigo3.megamodels.MAX_LINKS", 1)
    result = explore(megamodel_documents)
    assert result["links_truncated"] and len(result["links"]) == 1 and result["link_count"] > 1
    monkeypatch.setattr("amigo3.megamodels.MAX_ACTIVITIES", 1)
    with pytest.raises(ValueError, match="activities"):
        explore(megamodel_documents)


def test_pinned_api_and_validation(megamodel_data):
    app = create_app(megamodel_data, "duckdb")
    client = TestClient(app)
    options = client.get("/api/megamodels").json()
    assert "gomodel:mega-a" in {m["id"] for m in options["models"]}
    result = client.post("/api/megamodels", json={"model_ids": ["gomodel:mega-a", "gomodel:mega-b"]})
    assert result.status_code == 200
    assert result.json()["build_id"] == options["build_id"]
    assert result.json()["models"][0]["source"]["sha256"]
    assert client.get("/megamodels").status_code == 200
    for payload in [
        {"model_ids": []},
        {"model_ids": ["absent"]},
        {"model_ids": ["gomodel:mega-a"], "view": "stale"},
        {"model_ids": ["gomodel:mega-a"], "criteria": ["guess"]},
        {"model_ids": ["gomodel:mega-a"] * 13},
    ]:
        assert client.post("/api/megamodels", json=payload).status_code == 422
    # Index is loaded from the immutable build, independent of edits to the checkout.
    idx = model_index(app.state.catalog.path)
    assert idx is model_index(app.state.catalog.path)


def test_collection_network_matches_list_filters_and_retains_isolates(megamodel_data):
    client = TestClient(create_app(megamodel_data, "duckdb"))
    result = client.post("/api/megamodels/network", json={}).json()
    assert result["total"] == len(result["models"]) == 4
    assert result["isolated_count"] == 1
    assert "gomodel:worm-test" in {m["id"] for m in result["models"]}
    assert all(e["match_count"] == sum(e["criteria"].values()) for e in result["edges"])
    assert all("gomodel:worm-test" not in (e["source"], e["target"]) for e in result["edges"])
    filters = [{"id": "taxon_ids", "field": "taxon_ids", "values": ["NCBITaxon:9606"]}]
    query = {
        "collection": "gocams",
        "filters": filters,
        "page": {"size": 1},
        "facets": {"taxon_ids": {"field": "taxon_ids", "exclude_filters": ["taxon_ids"]}},
    }
    graph = client.post("/api/megamodels/network", json={"query": query}).json()
    listing = client.post("/api/query", json={**query, "page": {"size": 100}}).json()
    assert {m["id"] for m in graph["models"]} == {m["id"] for m in listing["hits"]}
    assert graph["facets"] == listing["facets"]
    assert graph["total"] == 3  # Caller pagination never silently reduces the graph.
    assert all(
        e["source"] in {m["id"] for m in graph["models"]}
        and e["target"] in {m["id"] for m in graph["models"]}
        for e in graph["edges"]
    )
    query["search"] = "Neighbor"
    graph = client.post("/api/megamodels/network", json={"query": query}).json()
    assert graph["total"] == 1 and not graph["edges"]
    empty = client.post("/api/megamodels/network", json={"criteria": []}).json()
    assert len(empty["models"]) == 4 and not empty["edges"] and empty["isolated_count"] == 4
    for payload in [
        {"query": {"collection": "genes"}},
        {"view": "stale"},
        {"query": {"collection": "gocams", "view": "stale"}},
        {"query": {"collection": "gocams", "filters": [{"id": "bad", "field": "unknown", "values": ["x"]}]}},
    ]:
        assert client.post("/api/megamodels/network", json=payload).status_code == 422


def test_network_connection_counts_match_exact_anchors(megamodel_data):
    client = TestClient(create_app(megamodel_data, "duckdb"))
    graph = client.post("/api/megamodels/network", json={}).json()
    for edge in graph["edges"]:
        detail = client.post(
            "/api/megamodels/connection", json={"model_ids": [edge["source"], edge["target"]]}
        ).json()
        assert detail["criteria"] == edge["criteria"]
        assert detail["match_count"] == edge["match_count"] == len(detail["anchors"])
        assert all(m["source"]["sha256"] for m in detail["models"])
    for ids in [["gomodel:mega-a"] * 2, ["missing", "gomodel:mega-a"]]:
        assert client.post("/api/megamodels/connection", json={"model_ids": ids}).status_code == 422


def test_collection_network_reads_past_first_page(megamodel_data, tmp_path):
    import json
    import shutil

    from amigo3.build import build

    data = tmp_path / "data"
    shutil.copytree(megamodel_data, data)
    repo = data / "review_repo"
    for i in range(101):
        folder = repo / "gocams" / f"page-{i}"
        folder.mkdir()
        (folder / f"page-{i}-src.yaml").write_text(
            json.dumps(
                {
                    "id": f"gomodel:page-{i}",
                    "title": f"Paged model {i}",
                    "taxon": "NCBITaxon:9606",
                    "status": "production",
                    "activities": [],
                }
            )
        )
    build(data, repo / "genes", repo)
    client = TestClient(create_app(data, "duckdb"))
    graph = client.post("/api/megamodels/network", json={"criteria": []}).json()
    assert graph["total"] == len(graph["models"]) == 105
    assert graph["isolated_count"] == 105 and not graph["missing_sources"]
