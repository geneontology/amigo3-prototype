import json

import duckdb
import pytest

from amigo3.build import integrate_entities, read_reviews
from amigo3.catalog import Catalog
from amigo3.parse import annotation_method, gaf_rows, gpi_rows, identifier_variants


def test_identifiers_and_context_survive(built_data):
    catalog = Catalog(built_data)
    with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
        assert db.execute("select count(*) from annotations").fetchone()[0] == 4
        gene = db.execute("select identifiers, annotation_count from genes where id='HGNC:1'").fetchone()
        assert set(["UniProtKB:P00001", "UniProtKB:P00001-2", "HGNC:1", "ENSEMBL:ENSG00000000001"]) <= set(
            gene[0]
        )
        assert gene[1] == 2
        assert (
            db.execute(
                "select id from genes where list_contains(identifiers, 'UniProtKB:Q00001')"
            ).fetchone()[0]
            == "PomBase:SPTEST.1"
        )
        isoform = db.execute(
            "select db_object_id, gene_id, extension_groups, with_or_from_groups from annotations where gene_product_form_id <> ''"
        ).fetchone()
        assert isoform[:2] == ("UniProtKB:P00001-2", "HGNC:1")
        assert len(json.loads(isoform[2])) == 2
        assert json.loads(isoform[3]) == [["UniProtKB:A"], ["UniProtKB:B", "UniProtKB:C"]]
        assert db.execute("select term_ids from genes where id='HGNC:2'").fetchone()[0] == []
        assert db.execute("select annotation_count from genes where id='HGNC:3'").fetchone()[0] == 0
        assert (
            db.execute("select count(*) from entities where id='RNAcentral:UNANNOTATED_9606'").fetchone()[0]
            == 1
        )
        assert (
            db.execute("select count(*) from genes where id='RNAcentral:UNANNOTATED_9606'").fetchone()[0] == 0
        )
        closure = db.execute("select isa_partof_closure from terms where id='GO:0004672'").fetchone()[0]
        assert set(closure) == {"GO:0004672", "GO:0016301", "GO:0003674"}


def test_ambiguous_mapping_is_not_arbitrarily_merged():
    genes = [
        {
            "id": f"HGNC:{i}",
            "symbol": f"G{i}",
            "name": "",
            "identifiers": [f"HGNC:{i}", "UniProtKB:SHARED"],
            "synonyms": [],
            "species": "human",
            "taxon_id": "NCBITaxon:9606",
        }
        for i in (1, 2)
    ]
    entity = {
        "id": "UniProtKB:SHARED",
        "canonical_id": "UniProtKB:SHARED",
        "object_type": "PR:000000001",
        "xrefs": [],
        "encoded_by": [],
        "species": "human",
        "symbol": "same",
        "name": "",
        "synonyms": [],
        "taxon_id": "NCBITaxon:9606",
        "source_id": "test",
    }
    issues = []
    _, mapping, _ = integrate_entities([entity], genes, issues)
    assert mapping[0]["gene_id"] == "UniProtKB:SHARED"
    assert mapping[0]["mapping_status"] == "ambiguous"
    assert issues[0]["candidates"] == ["HGNC:1", "HGNC:2"]


def test_identifier_aliases_do_not_strip_uniprot_isoforms():
    assert "ENSEMBL:ENSG00001" in identifier_variants("Ensembl:ENSG00001.5")
    assert identifier_variants("UniProtKB:P00001-2") == ["UniProtKB:P00001-2"]
    assert "HGNC:12" in identifier_variants("HGNC:HGNC:12")


def test_gpi_without_canonical_ids_keeps_distinct_flybase_genes(tmp_path):
    path = tmp_path / "fly.gpi"
    raw = [
        "\t".join([identifier, "gene", "", "", "PR:000000001", "NCBITaxon:7227", "", "", "", xref, ""])
        for identifier, xref in [
            ("FB:FBgn0000008", "UniProtKB:P10000"),
            ("FB:FBgn0000009", "UniProtKB:P10001"),
        ]
    ]
    path.write_text("!gpi-version: 2.0\n" + "\n".join(raw) + "\n")
    issues = []
    entities = list(gpi_rows(path, {"id": "fly_gpi", "species": "fly"}, issues))
    genes, mappings, _ = integrate_entities(entities, [], issues, {e["id"] for e in entities})
    assert {g["id"] for g in genes} == {"FB:FBgn0000008", "FB:FBgn0000009"}
    assert all(m["subject_id"] == m["gene_id"] for m in mappings)
    assert [e["raw_record"] for e in entities] == raw
    assert next(g for g in genes if g["id"] == "FB:FBgn0000008")["identifiers"] == [
        "FB:FBgn0000008",
        "UniProtKB:P10000",
    ]


def test_malformed_gaf_does_not_silently_drop_rows(tmp_path):
    path = tmp_path / "bad.gaf"
    path.write_text("!gaf-version: 2.2\nwrong\tcolumns\n")
    with pytest.raises(ValueError, match="expected 17 columns"):
        list(gaf_rows(path, {"id": "test", "sha256": "abc", "species": "human"}, {}))


def test_annotation_method_from_references(tmp_path):
    assert annotation_method(["GO_REF:0000002"]) == "InterPro2GO"
    assert annotation_method(["GO_REF:0000033", "PMID:21873635"]) == "PAINT phylogenetic inference"
    # A mapped GO_REF names the method even when listed after a publication.
    assert annotation_method(["PMID:1", "GO_REF:0000120"]) == "Combined automated annotation"
    assert annotation_method(["GO_REF:9999999"]) == "GO_REF:9999999"
    assert annotation_method(["PMID:123"]) == "Literature curation"
    assert annotation_method(["Reactome:R-HSA-1"]) == "Reactome"
    assert annotation_method(["UNKNOWN:1"]) == "Other"
    assert annotation_method([]) == "Other"

    fields = ["UniProtKB", "P1", "S", "enables", "GO:1", "GO_REF:0000117", "IEA", "", "F"]
    fields += ["", "", "protein", "taxon:9606", "20260101", "UniProt", "", ""]
    path = tmp_path / "iea.gaf"
    path.write_text("!gaf-version: 2.2\n" + "\t".join(fields) + "\n")
    (row,) = gaf_rows(path, {"id": "test", "sha256": "abc", "species": "human"}, {})
    assert row["method"] == "ARBA"


@pytest.mark.parametrize(
    "review_id, gene",
    [
        ("URS0000759CF4_9606", {"id": "HGNC:6295", "identifiers": ["RNAcentral:URS0000759CF4_9606"]}),
        ("P04637", {"id": "HGNC:11998", "identifiers": ["UniProtKB:P04637"]}),
    ],
)
def test_reviews_attach_by_rnacentral_or_uniprot_accession(tmp_path, review_id, gene):
    path = tmp_path / "genes" / "human" / "X" / "X-ai-review.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(f"id: {review_id}\ndescription: A review.\n")
    assert [r["gene_id"] for r in read_reviews(tmp_path / "genes", [gene])] == [gene["id"]]
