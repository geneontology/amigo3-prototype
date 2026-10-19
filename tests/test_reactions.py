"""Reaction structure, normalized identities, snapshot provenance, and querying."""

import json
import os
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.query import Facet, Filter, Query
from amigo3.reactions import RH, load_reactions, reaction_details, reaction_structure, read_rdf
from amigo3.solr import SolrBackend, publish
from amigo3.sources import digest


@pytest.fixture
def graph():
    return read_rdf(Path(__file__).parent / "fixtures/rhea.rdf")


def test_rhea_quartet_and_split_descriptions(graph):
    for number, direction in [
        (23260, "unspecified"),
        (23261, "left_to_right"),
        (23262, "right_to_left"),
        (23263, "bidirectional"),
    ]:
        actual, members = reaction_structure(graph, f"RHEA:{number}")
        assert actual == direction
        assert len(members) == 5
        assert {p["chemical_id"] for p in members if p["side"] == "left"} == {"CHEBI:57540", "CHEBI:57409"}
        assert all(p["stoichiometry"] == 1 for p in members)


def test_coefficients_and_compartments_are_not_lost(graph):
    side = graph[RH + "23260_L"]
    participant = side["contains1"].pop()
    side["contains2"] = [participant]
    graph[participant]["location"] = [RH + "Out"]
    _, members = reaction_structure(graph, "RHEA:23260")
    member = next(p for p in members if p["source_participant"] == participant)
    assert member["stoichiometry"] == 2
    assert member["compartment"] == RH + "Out"
    side["containsN"] = side.pop("contains2")
    _, members = reaction_structure(graph, "RHEA:23260")
    member = next(p for p in members if p["source_participant"] == participant)
    assert member["stoichiometry"] is None
    assert member["stoichiometry_expression"] == "n"
    del side["containsN"]
    with pytest.raises(ValueError, match="Incomplete participants"):
        reaction_structure(graph, "RHEA:23260")


def test_normalization_and_mapping_consistency(reaction_data):
    catalog = Catalog(reaction_data)
    with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM reactions").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM chemicals").fetchone()[0] == 5
        assert db.execute("SELECT count(*) FROM reaction_participants").fetchone()[0] == 15
        assert (
            db.execute("""SELECT count(*) FROM reaction_mappings m JOIN reactions r ON r.id=m.reaction_id
            WHERE m.rhea_id <> r.rhea_id OR m.go_term_id <> r.go_term_id""").fetchone()[0]
            == 0
        )
        assert db.execute("SELECT count(*) FROM annotations").fetchone()[0] == 4
        assert not db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='reaction_participants' AND column_name='label'"
        ).fetchall()
        data = reaction_details(db, go_term_id="GO:0047034")[0]
        assert {p["chemical"]["label"] for p in data["left"]} == {
            "NAD(+)",
            "(15S)-hydroxy-(5Z,8Z,11Z,13E)-eicosatetraenoate",
        }
        assert data["mappings"][0]["predicate"] == "rdfs:seeAlso"
    report = json.loads((catalog.path / "report.json").read_text())
    assert report["reactions"]["issues"] == []


def test_structured_detail_and_correlated_participant_filters(reaction_data):
    with TestClient(create_app(reaction_data, "duckdb")) as client:
        term = client.get("/api/terms/GO:0047034").json()
        reaction = term["reactions"][0]
        assert reaction["rhea_id"] == "RHEA:23260"
        assert len(reaction["right"]) == 3
        assert reaction["source_sha256"] == term["reaction_sources"]["rhea"]["sha256"]
        assert client.get("/api/reactions/" + reaction["id"]).json()["record"] == reaction
        assert client.get("/api/reactions/missing").status_code == 404
        query = {
            "collection": "reaction_participants",
            "filters": [
                {"id": "chemical", "field": "chemical_id", "values": ["CHEBI:57540"]},
                {"id": "side", "field": "side", "values": ["right"]},
                {"id": "coefficient", "field": "stoichiometry", "values": [1]},
            ],
        }
        assert client.post("/api/query", json=query).json()["total"] == 0
        query["filters"][1]["values"] = ["left"]
        assert client.post("/api/query", json=query).json()["total"] == 3
        assert (
            client.post("/api/query", json={"collection": "chemicals", "search": "NAD"}).json()["total"] == 2
        )
        assert len(client.get("/api/terms/GO:9916301").json()["reactions"]) == 2


def test_old_snapshot_and_missing_source_pair(built_data, tmp_path):
    with duckdb.connect() as db:
        assert reaction_details(db, go_term_id="GO:1") == []
        with pytest.raises(ValueError, match="requires both"):
            load_reactions(db, tmp_path, {"rhea": {"kind": "rhea_rdf"}})
    with TestClient(create_app(built_data, "duckdb")) as client:
        assert client.get("/api/terms/GO:0004672").json()["reactions"] == []


def test_generic_identity_mapping_scope_and_strict_import(graph, tmp_path, monkeypatch):
    """Synthetic polymer and mapping variants cannot collapse identity or assert NOT chemistry."""
    monkeypatch.setattr("amigo3.reactions.read_rdf", lambda path: graph)
    compound = RH + "Compound_3484"
    graph[compound]["accession"] = ["POLYMER:3484"]
    graph[compound]["underlyingChebi"] = ["http://purl.obolibrary.org/obo/CHEBI_57540"]
    graph[compound]["polymerizationIndex"] = ["n"]
    side = graph[RH + "23260_L"]
    participant = RH + "Participant_23260_compound_3484"
    side["contains1"].remove(participant)
    side["containsN"] = [participant]
    source = tmp_path / "rhea_go.tsv"
    source.write_text("RHEA_ID\tDIRECTION\tMASTER_ID\tID\n23260\tUN\t23260\tGO:1\n")
    manifest = {
        "rhea": {"id": "rhea", "kind": "rhea_rdf", "path": "unused", "sha256": "fixture"},
        "rhea_go": {"id": "rhea_go", "kind": "rhea_go", "path": source.name, "sha256": digest(source)},
    }
    with duckdb.connect() as db:
        db.execute(
            "CREATE TABLE terms AS SELECT id, id AS label, false AS obsolete FROM unnest(['GO:1','GO:2','GO:3','GO:4']) t(id)"
        )
        db.execute("""CREATE TABLE rules(id VARCHAR, subject_id VARCHAR, object_id VARCHAR,
            predicate_id VARCHAR, predicate_modifier VARCHAR)""")
        db.executemany(
            "INSERT INTO rules VALUES (?,?,?,?,?)",
            [
                ("RULE:exact", "GO:1", "RHEA:23260", "skos:exactMatch", ""),
                ("RULE:negative", "RHEA:23260", "GO:2", "skos:exactMatch", "Not"),
                ("RULE:broad", "RHEA:23260", "GO:3", "skos:broadMatch", ""),
                ("RULE:missing", "RHEA:999999", "GO:4", "skos:exactMatch", ""),
            ],
        )
        report = load_reactions(db, tmp_path, manifest)
        assert report["reactions"] == 1
        assert report["issues"][0]["rule_id"] == "RULE:missing"
        assert db.execute("SELECT count(*) FROM reaction_mappings").fetchone()[0] == 2
        assert db.execute(
            "SELECT id, underlying_chebi_id FROM chemicals WHERE id='POLYMER:3484'"
        ).fetchone() == ("POLYMER:3484", "CHEBI:57540")
        assert db.execute(
            "SELECT stoichiometry, stoichiometry_expression FROM reaction_participants WHERE chemical_id='POLYMER:3484'"
        ).fetchone() == (None, "n")
    del side["containsN"]
    with duckdb.connect() as db:
        db.execute("CREATE TABLE terms AS SELECT 'GO:1' AS id, 'test' AS label, false AS obsolete")
        with pytest.raises(ValueError, match="Incomplete participants"):
            load_reactions(db, tmp_path, manifest)


@pytest.mark.skipif(not os.getenv("AMIGO3_TEST_SOLR"), reason="Set AMIGO3_TEST_SOLR")
def test_reaction_solr_parity(reaction_data):
    catalog = Catalog(reaction_data)
    publish(catalog, os.environ["AMIGO3_TEST_SOLR"])
    solr, duck = SolrBackend(catalog, os.environ["AMIGO3_TEST_SOLR"]), DuckBackend(catalog)
    for query in [
        Query(
            collection="reactions",
            filters=[Filter(id="chemical", field="left_chemical_ids", values=["CHEBI:57540"])],
            facets={"direction": Facet(field="direction")},
        ),
        Query(
            collection="reaction_participants",
            filters=[
                Filter(id="side", field="side", values=["left"]),
                Filter(id="coefficient", field="stoichiometry", values=[1]),
            ],
        ),
        Query(collection="chemicals", search="NADH"),
    ]:
        a, b = solr.query(query), duck.query(query)
        assert a["total"] == b["total"]
        assert a["hits"] == b["hits"]
        assert a["facets"] == b["facets"]
