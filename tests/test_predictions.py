"""Preload integrity, baseline isolation, mapping and relation-aware comparisons."""

import csv
import hashlib
import io
import json
import shutil

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.build import build
from amigo3.predictions import compare_claim, input_sets, list_sets, model_type


def gaf(subject, term="GO:0004672", relation="enables", extension="", reference="PMID:1"):
    return (
        "\t".join(
            [
                "RefSeq",
                subject,
                "<script>symbol</script>",
                relation,
                term,
                reference,
                "IEA",
                "HMM:test",
                "F",
                "test protein",
                "",
                "protein",
                "taxon:9606",
                "20260101",
                "RefSeq",
                extension,
                "",
            ]
        )
        + "\n"
    )


@pytest.fixture(scope="session")
def prediction_data(built_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("preloaded") / "data"
    shutil.copytree(built_data, data)
    baseline_id = json.loads((data / "current.json").read_text())["build_id"]
    with duckdb.connect(str(data / "builds" / baseline_id / "integration.duckdb"), read_only=True) as db:
        baseline_count = db.execute("SELECT count(*) FROM annotations").fetchone()[0]
    manifest = json.loads((data / "sources.json").read_text())
    go = data / manifest["go"]["path"]
    text = go.read_text().replace("id: GO:0016301\n", "id: GO:0016301\nalt_id: GO:9916301\n")
    go.write_text(
        text
        + "\n[Term]\nid: GO:7777777\nname: obsolete term\nnamespace: molecular_function\nis_obsolete: true\n"
    )
    manifest["go"]["sha256"] = hashlib.sha256(go.read_bytes()).hexdigest()
    (data / "sources.json").write_text(json.dumps(manifest))
    external = data / "external-input"
    external.mkdir()
    rows = [
        gaf("R1"),
        gaf("R1", reference="PMID:2"),
        gaf("R1", "GO:0016301"),
        gaf("R1", "GO:9916301"),
        gaf("R1", "GO:6666666"),
        gaf("R1", "GO:7777777"),
        gaf("R1", relation="NOT|enables"),
        gaf("R1", extension="occurs_in(CL:1)"),
        gaf("R1", "GO:9999999"),
        gaf("R2"),
        gaf("RNEW"),
        gaf("RISO"),
        gaf("RAMB"),
        gaf("RMISSING"),
        gaf("ROUT"),
    ]
    (external / "test.gaf").write_text("!gaf-version: 2.2\n!date-generated: 2026-01-01\n" + "".join(rows))
    (external / "mapping.tsv").write_text(
        "source_id\ttarget_id\ttaxon_id\tmethod\n"
        + "".join(
            f"RefSeq:{s}\tUniProtKB:{t}\tNCBITaxon:9606\texact_sequence\n"
            for s, t in [
                ("R1", "P00001"),
                ("R2", "P00002"),
                ("RNEW", "PNEW"),
                ("RISO", "P00001-2"),
                ("RAMB", "P00001"),
                ("RAMB", "P00002"),
                ("ROUT", "OUTSIDE"),
            ]
        )
    )
    (external / "subjects.tsv").write_text(
        "subject_id\ttaxon_id\n"
        + "".join(f"UniProtKB:{s}\tNCBITaxon:9606\n" for s in ["P00001", "P00002", "PNEW", "P00001-2"])
    )
    (external / "gene_xrefs.tsv").write_text(
        "subject_id\ttaxon_id\tgene_id\tgene_taxon_id\tgene_label\torganism\tsource\n"
        "UniProtKB:P00001\tNCBITaxon:9606\tNCBIGene:1\tNCBITaxon:9606\tGENE1\tHomo sapiens\tTest xref\n"
        "UniProtKB:P00001\tNCBITaxon:9606\tNCBIGene:999\tNCBITaxon:10090\tGene1\tMus musculus\tTest xref\n"
    )
    config = data / "external.json"
    config.write_text(
        json.dumps(
            {
                "sets": [
                    {
                        "id": "test",
                        "label": "Test external set",
                        "species": "human",
                        "taxon": "NCBITaxon:9606",
                        "baseline_sources": ["human_gaf"],
                        "gaf": "external-input/test.gaf",
                        "mapping": "external-input/mapping.tsv",
                        "subjects": "external-input/subjects.tsv",
                        "gene_xrefs": "external-input/gene_xrefs.tsv",
                    }
                ]
            }
        )
    )
    built = build(data, external_sets=config)
    assert built["counts"]["annotations"] == baseline_count
    return data


def test_classification_and_evidence(prediction_data):
    with TestClient(create_app(prediction_data, "duckdb")) as client:
        index = client.get("/api/predictions").json()
        source = index["sets"][0]
        assert source["summary"]["input_rows"] == 15
        counts = source["summary"]["status_row_counts"]
        assert counts == {
            "exact": 3,
            "covered": 2,
            "unknown_term": 1,
            "obsolete": 1,
            "negative": 1,
            "unmatched": 3,
            "refinement": 1,
            "ambiguous": 1,
            "unmapped": 1,
            "out_of_scope": 1,
        }
        assert source["summary"]["status_pair_counts"]["exact"] == 1  # multiple evidence rows stay separate
        assert source["summary"]["proteins_without_baseline_annotations"] == 2  # P2 is NOT-only
        rows = client.get("/api/predictions/test/claims?size=200").json()["rows"]
        isoform = next(r for r in rows if r["source_subject_id"] == "RefSeq:RISO")
        assert isoform["status"] == "refinement"  # canonical protein's child term cannot leak to isoform
        assert isoform["broader_terms"] == ["GO:0003674"]
        extension = next(r for r in rows if r["annotation_extensions"])
        assert extension["status"] == "exact" and not extension["assertion_match"]
        negated = next(r for r in rows if r["negation"])
        assert negated["status"] == "negative" and negated["opposite_assertion_ids"]
        alternative = next(r for r in rows if r["ontology_class_id"] == "GO:9916301")
        assert alternative["term_id"] == "GO:0016301"
        assert next(r for r in rows if r["term_id"] == "GO:9999999")["status"] == "unmatched"
        assert next(r for r in rows if r["source_subject_id"] == "RefSeq:RNEW")["gene_id"] == ""
        assert len(next(r for r in rows if r["status"] == "ambiguous")["mapping_evidence"]) == 2


def test_api_pinning_filtering_exports_and_frozen_inputs(prediction_data):
    with TestClient(create_app(prediction_data, "duckdb")) as client:
        index = client.get("/api/predictions").json()
        source = index["sets"][0]
        assert client.get("/api/predictions/test/claims?view=old").status_code == 422
        assert client.get("/api/predictions/unknown/claims").status_code == 404
        assert client.get("/api/predictions/test/claims?status=bad").status_code == 422
        assert client.get("/api/predictions/test/claims?offset=-1").status_code == 422
        assert client.get("/api/predictions/test/claims?aspect=bad").status_code == 422
        assert client.get("/api/predictions/test/claims?q=%27%20OR%201%3D1").json()["total"] == 0
        result = client.get("/api/predictions/test/claims?status=covered&size=1").json()
        assert result["total"] == 2 and len(result["rows"]) == 1
        exported = client.get("/api/predictions/test/export?status=covered")
        lines = [line for line in exported.text.splitlines() if not line.startswith("#")]
        records = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))
        assert len(records) == 2 and all(r["status"] == "covered" for r in records)
        assert "baseline_matches" in records[0] and "raw_record" in records[0]
        original = client.get("/api/predictions/test/sources/gaf")
        assert hashlib.sha256(original.content).hexdigest() == source["files"]["gaf"]["sha256"]
        assert client.get("/api/predictions/test/sources/missing").status_code == 404
        assert client.get("/api/predictions/test/export?format=json").json()["build_id"] == index["build_id"]
        page = client.get("/predictions")
        assert page.status_code == 200 and "/predictions.js?v=" in page.text
        assert 'href="/predictions"' in client.get("/").text
        assert "<script>symbol</script>" not in page.text


def test_set_named_gene_keeps_all_routes_accessible(prediction_data, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(prediction_data, data)
    config = data / "external.json"
    settings = json.loads(config.read_text())
    settings["sets"][0]["id"] = "gene"
    config.write_text(json.dumps(settings))
    build(data, external_sets=config)
    with TestClient(create_app(data, "duckdb")) as client:
        claims = client.get("/api/predictions/gene/claims")
        assert claims.status_code == 200
        assert claims.json()["set_id"] == "gene"
        assert claims.json()["total"] == 15
        exported = client.get("/api/predictions/gene/export")
        assert exported.status_code == 200
        lines = [line for line in exported.text.splitlines() if not line.startswith("#")]
        assert len(list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))) == 15
        source = client.get("/api/predictions/gene/sources/gaf")
        assert source.status_code == 200
        assert source.content == (data / "external-input/test.gaf").read_bytes()
        scoped = client.get("/api/predictions/gene/HGNC:1")
        assert scoped.status_code == 200
        assert scoped.json()["gene_id"] == "HGNC:1"
        assert scoped.json()["total"] == 10


def test_input_change_new_build_old_snapshot_stable(prediction_data, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(prediction_data, data)
    before = json.loads((data / "current.json").read_text())["build_id"]
    source = data / "external-input/test.gaf"
    source.write_text(source.read_text() + gaf("RNEW", "GO:0016301"))
    result = build(data, external_sets=data / "external.json")
    assert result["build_id"] != before
    assert list_sets(data / "builds" / before)[0]["summary"]["input_rows"] == 15
    assert list_sets(data / "builds" / result["build_id"])[0]["summary"]["input_rows"] == 16
    source.unlink()
    assert list_sets(data / "builds" / result["build_id"])[0]["summary"]["input_rows"] == 16


def test_older_build_and_no_sets(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        assert client.get("/api/predictions").json()["sets"] == []
        assert client.get("/predictions").status_code == 200


def test_invalid_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('sets: [{id: "../../escape"}]')
    with pytest.raises(ValueError, match="Invalid"):
        input_sets(path)


def test_model_identifier_categories():
    assert model_type("NBR004157") == "BlastRule"
    assert model_type("HMM:NF1|HMM:TIGR2") == "HMM"
    assert model_type("ArchId:123") == "ArchId"
    assert model_type("cluster:456") == "cluster"
    assert model_type("HMM:NF1|NBR004157") == "Multiple"
    assert model_type("unrecognized") == "Unclassified"
    assert model_type("") == "None"


def test_facets_gene_scope_and_gene_cross_references(prediction_data):
    with TestClient(create_app(prediction_data, "duckdb")) as client:
        result = client.get("/api/predictions/test/claims?status=exact,covered&model_type=HMM").json()
        assert result["total"] == 5
        categories = {b["value"]: b for b in result["facets"]["status"]}
        assert categories["unmatched"]["count"] == 3  # Exclude own facet, retain HMM filter.
        assert categories["exact"]["selected"] and categories["covered"]["selected"]
        assert result["facets"]["aspect"] == [{"value": "F", "count": 5, "selected": False}]
        assert (
            client.get("/api/predictions/test/claims?status=exact,covered&model_type=ArchId").json()["total"]
            == 0
        )
        exact = client.get("/api/predictions/test/claims?status=exact&gene=HGNC:1").json()
        assert exact["total"] == 3
        refs = exact["rows"][0]["ncbi_gene_xrefs"]
        assert [r["taxon_status"] for r in refs] == ["same_taxon", "other_taxon"]
        assert client.get("/api/predictions/test/claims?q=NCBIGene:999").json()["total"] == 9
        gene = client.get("/api/predictions/gene/HGNC:1").json()
        assert gene["total"] == 10  # Nine canonical rows and one isoform row.
        isoform = next(r for r in gene["rows"] if r["source_subject_id"] == "RefSeq:RISO")
        assert isoform["status"] == "refinement" and isoform["ncbi_gene_xrefs"] == []
        assert client.get("/api/predictions/gene/HGNC:1?view=old").status_code == 422
        assert client.get("/api/predictions/gene/HGNC:999").status_code == 404
        assert client.get("/api/predictions/test/claims?gene=HGNC:1%27%20OR%201=1").json()["total"] == 0
        exported = client.get("/api/predictions/test/export?status=exact,covered&gene=HGNC:1")
        records = list(
            csv.DictReader(
                io.StringIO(
                    "\n".join(line for line in exported.text.splitlines() if not line.startswith("#"))
                ),
                delimiter="\t",
            )
        )
        assert len(records) == 5 and all(r["gene_id"] == "HGNC:1" for r in records)
        assert json.loads(records[0]["ncbi_gene_xrefs"])[1]["taxon_status"] == "other_taxon"


def test_negative_assertion_evidence_and_taxon_context():
    term = "GO:0004672"
    terms = {term: {"label": "kinase", "obsolete": False, "aspect": "F", "is_a_closure": [term]}}
    row = {
        "ontology_class_id": term,
        "relation": "enables",
        "negation": True,
        "annotation_extensions": "",
        "interacting_taxon_id": ["NCBITaxon:1"],
        "aspect": "F",
    }
    baseline = [{**row, "id": "baseline:1", "term": term}]
    result = compare_claim(row, baseline, terms, {})
    assert result["assertion_match"] and result["baseline_matches"] == baseline
    row = {**row, "interacting_taxon_id": ["NCBITaxon:2"]}
    result = compare_claim(row, baseline, terms, {})
    assert not result["assertion_match"] and not result["opposite_assertion_ids"]


def test_missing_baseline_is_not_novelty(prediction_data, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(prediction_data, data)
    config = data / "external.json"
    spec = json.loads(config.read_text())
    spec["sets"][0]["baseline_sources"] = ["missing_gaf"]
    config.write_text(json.dumps(spec))
    build(data, external_sets=config)
    with TestClient(create_app(data, "duckdb")) as client:
        source = client.get("/api/predictions").json()["sets"][0]
        assert not source["baseline_available"]
        assert source["summary"]["comparable_pairs"] == 0
        assert source["summary"]["proteins_without_baseline_annotations"] == 0
        assert source["summary"]["status_row_counts"]["baseline_unavailable"] == 12


def test_empty_gaf_and_wrong_taxon(prediction_data, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(prediction_data, data)
    source = data / "external-input/test.gaf"
    source.write_text("!gaf-version: 2.2\n")
    result = build(data, external_sets=data / "external.json")
    assert list_sets(data / "builds" / result["build_id"])[0]["summary"]["input_rows"] == 0
    source.write_text("")
    with pytest.raises(ValueError, match="expected !gaf-version"):
        build(data, external_sets=data / "external.json")
    source.write_text("!gaf-version: 2.2\n" + gaf("R1").replace("taxon:9606", "taxon:10090"))
    with pytest.raises(ValueError, match="unexpected taxon"):
        build(data, external_sets=data / "external.json")
