from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.hierarchies import load_species
from amigo3.satisfiability import evaluate

TAXONOMY = Path(__file__).parent / "fixtures/uniprot-taxonomy.json"
SPECIES = {
    "worm": {"label": "C. elegans", "taxon": "NCBITaxon:6239"},
    "PSEPK": {"label": "P. putida KT2440", "taxon": "NCBITaxon:160488"},
}

# A small MF tree: transferase > acyltransferase > {acetyltransferase X, acetyltransferase Y}.
TERMS = {
    "GO:0016740": ("transferase activity", []),
    "GO:0016746": ("acyltransferase activity", ["GO:0016740"]),
    "GO:0000001": ("X acetyltransferase activity", ["GO:0016746"]),
    "GO:0000002": ("Y acetyltransferase activity", ["GO:0016746"]),
    "GO:0000003": ("kinase Z activity", []),
    "GO:0000004": ("lyase W activity", []),
    "GO:0000005": ("synthase V activity", []),
    "GO:0000100": ("test biosynthetic process", []),
}


def closure(term):
    result = {term}
    for parent in TERMS[term][1]:
        result.update(closure(parent))
    return sorted(result)


def annoton(identifier, function=None, members=(), family=None):
    participant = {"selector_type": "FAMILY"}
    if family or members:
        participant["family"] = {
            "term": {"id": family} if family else None,
            "representative_members": [{"term": {"id": m}} for m in members],
        }
    result = {"id": identifier, "label": identifier, "participant": participant}
    if function:
        result["function"] = {"term": {"id": function, "label": TERMS[function][0]}}
    return result


MODULE = {
    "id": "MODULE:toy",
    "module": {
        "id": "toy",
        "label": "Toy pathway",
        "concepts": [{"term": {"id": "GO:0000100"}}],
        "parts": [
            {"order": 1, "node": {"id": "s1", "label": "Step 1", "annotons": [annoton("x", "GO:0000001")]}},
            {
                "order": 2,
                "node": {
                    "id": "s2",
                    "label": "Step 2",
                    "variant_sets": [
                        {
                            "id": "routes",
                            "selection": "ONE_OR_MORE",
                            "variants": [
                                {
                                    "id": "r1",
                                    "label": "Kinase route",
                                    "annotons": [annoton("z", "GO:0000003")],
                                },
                                {
                                    "id": "r2",
                                    "label": "Curated route",
                                    "annotons": [annoton("named", members=["UniProtKB:QNAMED"])],
                                },
                            ],
                        }
                    ],
                },
            },
            {
                "order": 3,
                "node": {
                    "id": "s3",
                    "label": "Step 3",
                    "annotons": [annoton("w", "GO:0000004", family="PANTHER:PTHR1:SF2")],
                },
            },
            {
                "order": 4,
                "optional": True,
                "node": {"id": "s4", "label": "Optional", "annotons": [annoton("v", "GO:0000005")]},
            },
            {"order": 5, "node": {"id": "s5", "label": "Unscorable", "annotons": [annoton("u")]}},
        ],
    },
}

GENES = [
    # id, symbol, species, extra identifiers
    ("UniProtKB:QX", "geneX", "toy", []),
    ("UniProtKB:QBROAD", "acyl", "toy", []),
    ("UniProtKB:QSIB", "sibY", "toy", []),
    ("UniProtKB:QNAMED", "named", "toy", []),
    ("UniProtKB:QFAM", "famW", "toy", []),
    ("UniProtKB:QPATH", "pathOnly", "toy", []),
    ("UniProtKB:QBOTH", "sibBroad", "toy", []),
    ("UniProtKB:QNOT", "notW", "toy", []),
    ("UniProtKB:QOTHER", "otherX", "other", []),
]
ANNOTATIONS = [
    # gene, term, evidence, aspect, negation
    ("UniProtKB:QX", "GO:0000001", "IEA", "F", False),
    ("UniProtKB:QBROAD", "GO:0016746", "IBA", "F", False),
    ("UniProtKB:QSIB", "GO:0000002", "IDA", "F", False),
    ("UniProtKB:QPATH", "GO:0000100", "IMP", "P", False),
    ("UniProtKB:QBOTH", "GO:0000002", "IDA", "F", False),
    ("UniProtKB:QBOTH", "GO:0016746", "IEA", "F", False),
    ("UniProtKB:QNOT", "GO:0000100", "IMP", "P", False),
    ("UniProtKB:QNOT", "GO:0000004", "IDA", "F", True),
    ("UniProtKB:QOTHER", "GO:0000001", "IDA", "F", False),
]
ENTRIES = (
    "id,name,source_tax_id,source_tax_name,gene,subfamily,subfamily_name\n"
    "QFAM,W family member,1,Toy,famW,PTHR1:SF2,W\n"
    "QNOT,W family member,1,Toy,notW,PTHR1:SF2,W\n"
    "QELSE,Other species member,2,Other,famW,PTHR1:SF2,W\n"
)


@pytest.fixture
def db():
    con = duckdb.connect()
    load_species(con, TAXONOMY, SPECIES)
    con.execute(
        "CREATE TABLE terms(id VARCHAR, label VARCHAR, is_a_closure VARCHAR[], isa_partof_closure VARCHAR[])"
    )
    con.executemany(
        "INSERT INTO terms VALUES (?, ?, ?, ?)", [(t, v[0], closure(t), closure(t)) for t, v in TERMS.items()]
    )
    con.execute("CREATE TABLE ontology_edges(subject VARCHAR, predicate VARCHAR, object VARCHAR)")
    con.executemany(
        "INSERT INTO ontology_edges VALUES (?, 'is_a', ?)", [(t, p) for t, v in TERMS.items() for p in v[1]]
    )
    con.execute(
        "CREATE TABLE genes(id VARCHAR, symbol VARCHAR, species VARCHAR, taxon_id VARCHAR, identifiers VARCHAR[])"
    )
    con.executemany(
        "INSERT INTO genes VALUES (?, ?, ?, ?, ?)",
        [(g, s, sp, "NCBITaxon:1" if sp == "toy" else "NCBITaxon:2", [g, *x]) for g, s, sp, x in GENES],
    )
    con.execute(
        """CREATE TABLE annotations(id VARCHAR, gene_id VARCHAR, gene_symbol VARCHAR, ontology_class_id VARCHAR,
        term_label VARCHAR, evidence_code VARCHAR, aspect VARCHAR, negation BOOLEAN, species VARCHAR,
        isa_partof_closure VARCHAR[])"""
    )
    symbols = {g: (s, sp) for g, s, sp, _ in GENES}
    con.executemany(
        "INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (f"a{i}", g, symbols[g][0], t, TERMS[t][0], e, a, n, symbols[g][1], closure(t))
            for i, (g, t, e, a, n) in enumerate(ANNOTATIONS)
        ],
    )
    con.execute(
        "CREATE TABLE knowledge_sources(collection VARCHAR, record_id VARCHAR, role VARCHAR, path VARCHAR, sha256 VARCHAR, raw_text VARCHAR)"
    )
    con.execute(
        "INSERT INTO knowledge_sources VALUES ('families', 'PANTHER:PTHR1', 'entries', 'x', 'y', ?)",
        [ENTRIES],
    )
    yield con
    con.close()


def steps(result):
    found = {}

    def visit(node):
        for a in node["annotons"]:
            found[a["id"]] = a
        for part in node["parts"]:
            visit(part)
        for variant_set in node["variant_sets"]:
            for variant in variant_set["variants"]:
                visit(variant)

    visit(result["tree"])
    return found


def test_direct_annotation_and_named_participants_satisfy_steps(db):
    result = evaluate(db, MODULE, "toy")
    found = steps(result)
    assert found["x"]["status"] == "satisfied"
    assert [g["symbol"] for g in found["x"]["genes"]] == ["geneX"]
    assert found["x"]["genes"][0]["tier_label"] == "electronic (IEA)"
    # The curated representative member resolves into this species.
    assert found["named"]["status"] == "satisfied"
    assert found["named"]["genes"][0]["named_as"] == ["UniProtKB:QNAMED"]
    # A variant set needs one realized variant, not all of them.
    s2 = result["tree"]["parts"][1]
    assert s2["variant_sets"][0]["variants"][0]["status"] == "gap"
    assert s2["status"] == "satisfied"
    # Annotations from another species never satisfy a step.
    assert all(g["symbol"] != "otherX" for g in found["x"]["genes"])


def test_evidence_filter_turns_electronic_steps_into_gaps(db):
    found = steps(evaluate(db, MODULE, "toy", "no_iea"))
    assert found["x"]["status"] == "gap"
    by_symbol = {c["symbol"]: c for c in found["x"]["candidates"]}
    # The IBA acyltransferase annotation is a less specific statement of the same activity.
    assert by_symbol["acyl"]["basis"][0]["kind"] == "broader_term"
    assert by_symbol["acyl"]["basis"][0]["detail"]["term"]["id"] == "GO:0016746"
    # The sibling-activity paralog is weaker; strong candidates exist, so it is held back.
    assert "sibY" not in by_symbol
    # A broad annotation restating a different, known activity is not broader-term support.
    assert "sibBroad" not in by_symbol
    assert by_symbol["acyl"]["score"] == 3


def test_iba_only_evidence_keeps_phylogenetic_annotations(db):
    result = evaluate(db, MODULE, "toy", "iba")
    assert result["evidence_label"].startswith("IBA only")
    found = steps(result)
    # Only the IBA row survives: the IEA and IDA annotations no longer satisfy steps.
    assert found["x"]["status"] == "gap"
    assert "acyl" in {c["symbol"] for c in found["x"]["candidates"]}


def test_gap_filling_uses_family_and_pathway_context_and_respects_not(db):
    result = evaluate(db, MODULE, "toy")
    found = steps(result)
    w = found["w"]
    assert w["status"] == "gap"
    by_symbol = {c["symbol"]: c for c in w["candidates"]}
    assert {b["kind"] for b in by_symbol["famW"]["basis"]} == {"subfamily_member"}
    assert {b["kind"] for b in by_symbol["pathOnly"]["basis"]} == {"pathway_context"}
    # NOT enables lyase W removes the gene even though family and pathway support it.
    assert "notW" not in by_symbol
    # A proposal is reported for each candidate, but not applied anywhere.
    proposals = {(p["annoton_id"], p["symbol"]) for p in result["proposals"]}
    assert ("w", "famW") in proposals
    assert all(p["relation"] == "enables" and p["term"]["id"] for p in result["proposals"])
    # Step 3 blocks the module; the kinase route is an alternative to a realized variant.
    assert found["w"]["needed"] and not found["z"]["needed"]
    assert result["proposals"][0]["needed"]


def test_rollup_ignores_optional_and_unscorable_parts(db):
    result = evaluate(db, MODULE, "toy")
    parts = result["tree"]["parts"]
    assert parts[3]["optional"] and parts[3]["status"] in ("missing", "gap")
    assert parts[4]["status"] == "unscorable"
    # Step 3 is a fillable gap; nothing is missing outright.
    assert result["status"] == "gap"
    assert result["counts"]["satisfied"] == 2


def test_other_species_sees_module_differently(db):
    result = evaluate(db, MODULE, "other")
    found = steps(result)
    assert found["x"]["status"] == "satisfied"
    assert found["w"]["status"] == "missing"
    assert result["status"] == "missing"


def test_satisfiability_api(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        options = client.get("/api/satisfiability", params={"module": "MODULE:test"}).json()
        assert options["default_species"] == "human"
        assert set(options["species"]) == {"human", "pombe"}
        result = client.post("/api/satisfiability", json={"module": "MODULE:test", "species": "human"}).json()
        route_a = result["tree"]["variant_sets"][0]["variants"][0]
        assert route_a["status"] == "satisfied"
        assert route_a["annotons"][0]["genes"][0]["symbol"] == "GENE1"
        assert result["status"] == "satisfied"
        pombe = client.post("/api/satisfiability", json={"module": "MODULE:test", "species": "pombe"}).json()
        # Route A is a taxon alternative whose curated member and cached family are human-only;
        # empty Route B cannot be scored either way.
        route_a = pombe["tree"]["variant_sets"][0]["variants"][0]
        assert route_a["status"] == "not_expected"
        assert route_a["annotons"][0]["scope"]["species"] == ["human"]
        assert pombe["status"] == "unscorable"
        bad = client.post("/api/satisfiability", json={"module": "MODULE:test", "species": "mars"})
        assert bad.status_code == 422
        assert (
            client.post("/api/satisfiability", json={"module": "MODULE:nope", "species": "human"}).status_code
            == 404
        )


def only(db, annotations):
    db.execute("DELETE FROM annotations")
    for i, (gene, term, evidence, negation) in enumerate(annotations):
        db.execute(
            "INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?, 'F', ?, 'toy', ?)",
            [str(i), gene, gene.split(":")[1], term, TERMS[term][0], evidence, negation, closure(term)],
        )


@pytest.mark.parametrize(
    "positive,negative,required,expected",
    [
        # NOT on an ancestor rules out the required, more specific function.
        ("GO:0000001", "GO:0016746", "GO:0000001", False),
        # NOT on one specific activity leaves the broader function open.
        ("GO:0016746", "GO:0000001", "GO:0016746", True),
        # NOT on the required term itself.
        ("GO:0000001", "GO:0000001", "GO:0000001", False),
        # NOT on a sibling is irrelevant.
        ("GO:0000001", "GO:0000002", "GO:0000001", True),
    ],
)
def test_negation_direction(db, positive, negative, required, expected):
    only(db, [("UniProtKB:QX", positive, "IDA", False), ("UniProtKB:QX", negative, "IDA", True)])
    document = {"module": {"id": "root", "annotons": [annoton("step", required)]}}
    result = evaluate(db, document, "toy")
    assert bool(result["tree"]["annotons"][0]["genes"]) == expected


def test_negation_direction_for_gap_candidates(db):
    only(
        db,
        [
            # Both are broader-term candidates for X acetyltransferase.
            ("UniProtKB:QBROAD", "GO:0016746", "IBA", False),
            ("UniProtKB:QSIB", "GO:0016746", "IBA", False),
            # A more distant broader-term candidate, two levels up.
            ("UniProtKB:QX", "GO:0016740", "IBA", False),
            # NOT X itself rules QBROAD out; NOT sibling Y does not affect QSIB.
            ("UniProtKB:QBROAD", "GO:0000001", "IDA", True),
            ("UniProtKB:QSIB", "GO:0000002", "IDA", True),
            # NOT on acyltransferase, an ancestor of X, rules QX out.
            ("UniProtKB:QX", "GO:0016746", "IDA", True),
        ],
    )
    document = {"module": {"id": "root", "annotons": [annoton("step", "GO:0000001")]}}
    step = evaluate(db, document, "toy")["tree"]["annotons"][0]
    assert step["status"] == "gap"
    assert [c["symbol"] for c in step["candidates"]] == ["QSIB"]


@pytest.mark.parametrize("sibling_step,expected", [(False, ["QSIB"]), (True, [])])
def test_sibling_activity_of_gene_filling_another_step_is_not_proposed(db, sibling_step, expected):
    only(db, [("UniProtKB:QSIB", "GO:0000002", "IDA", False)])
    annotons = [annoton("x", "GO:0000001")] + ([annoton("y", "GO:0000002")] if sibling_step else [])
    found = steps(evaluate(db, {"module": {"id": "root", "annotons": annotons}}, "toy"))
    # Y acetyltransferase is only a sibling of X under acyltransferase. Once it fills
    # step y, it scores 0 for step x and is dropped rather than listed as a candidate.
    assert [c["symbol"] for c in found["x"]["candidates"]] == expected
    assert found["x"]["status"] == ("missing" if sibling_step else "gap")


def taxon(identifier, label):
    return {"preferred_term": label, "term": {"id": identifier, "label": label}}


@pytest.fixture
def putida(db):
    """The toy species as P. putida KT2440 and the other as C. elegans, so lineages are known."""
    db.execute(
        "UPDATE genes SET taxon_id = CASE species WHEN 'toy' THEN 'NCBITaxon:160488' ELSE 'NCBITaxon:6239' END"
    )
    db.execute("UPDATE knowledge_sources SET raw_text = replace(raw_text, ',1,Toy,', ',160488,Toy,')")
    return db


@pytest.mark.parametrize(
    "scope,expected",
    [
        ([taxon("NCBITaxon:6072", "Eumetazoa")], "not_expected"),
        ([taxon("NCBITaxon:33208", "Metazoa"), taxon("NCBITaxon:4751", "Fungi")], "not_expected"),
        # A clade that contains KT2440 keeps gap filling.
        ([taxon("NCBITaxon:2", "Bacteria")], "gap"),
        ([taxon("NCBITaxon:6072", "Eumetazoa"), taxon("NCBITaxon:1236", "Gammaproteobacteria")], "gap"),
        # A single organism is where the module was curated, not a limit on where it applies.
        ([taxon("NCBITaxon:83333", "Escherichia coli K-12")], "gap"),
        ([], "gap"),
    ],
)
def test_clade_scope_outside_species_is_not_expected(putida, scope, expected):
    document = {
        "module": {
            "id": "root",
            "label": "Root",
            "context": {"taxa": scope},
            "annotons": [annoton("x", "GO:0000001"), annoton("w", "GO:0000004", family="PANTHER:PTHR1:SF2")],
        }
    }
    result = evaluate(putida, document, "toy")
    found = steps(result)
    # Direct evidence wins over scope.
    assert found["x"]["status"] == "satisfied"
    assert found["w"]["status"] == expected
    if expected == "not_expected":
        assert found["w"]["candidates"] == [] and not found["w"]["needed"]
        assert found["w"]["scope"] == {"kind": "taxon", "node": "Root", "taxa": [t["term"] for t in scope]}
        assert not any(p["annoton_id"] == "w" for p in result["proposals"])
        assert result["counts"]["not_expected"] == 1
        # Not expected steps do not count against the module.
        assert result["status"] == "satisfied"
    else:
        assert found["w"]["candidates"]


def test_module_wholly_outside_scope_is_not_expected(putida):
    document = {
        "module": {
            "id": "root",
            "context": {"taxa": [taxon("NCBITaxon:6072", "Eumetazoa")]},
            "annotons": [annoton("w", "GO:0000004"), annoton("u")],
        }
    }
    assert evaluate(putida, document, "toy")["status"] == "not_expected"
    # C. elegans is a eumetazoan, so the same step is an ordinary unfilled step there.
    assert steps(evaluate(putida, document, "other"))["w"]["status"] == "missing"


def lineage_module(axis):
    """A Notch-like coactivator set: family alternatives and a nematode gene-product alternative."""
    nematode = annoton("lag3", "GO:0000001")
    nematode["participant"] = {
        "selector_type": "GENE_PRODUCT",
        "gene_product": {"term": {"id": "UniProtKB:QOTHER"}},
    }
    variants = [
        ("fam", "Family", annoton("fam", "GO:0000004", family="PANTHER:PTHR1:SF2")),
        ("elsewhere", "Absent family", annoton("abs", "GO:0000005", family="PANTHER:PTHR9")),
        ("worm", "Nematode LAG-3", nematode),
    ]
    return {
        "module": {
            "id": "root",
            "variant_sets": [
                {
                    "id": "coactivator",
                    "axis": axis,
                    "selection": "ONE",
                    "variants": [{"id": i, "label": label, "annotons": [a]} for i, label, a in variants],
                }
            ],
        }
    }


def test_taxon_axis_alternative_of_other_species_is_not_expected(db):
    db.execute(
        "INSERT INTO knowledge_sources VALUES ('families', 'PANTHER:PTHR9', 'entries', 'x', 'y', ?)",
        ["id,name,source_tax_id,source_tax_name,gene,subfamily,subfamily_name\nQELSE,Other,2,Other,e,,\n"],
    )
    # Without IEA the toy species has no X acetyltransferase, so LAG-3 would otherwise be a gap.
    result = evaluate(db, lineage_module("taxon / sequence lineage"), "toy", "no_iea")
    found = steps(result)
    assert found["lag3"]["status"] == "not_expected"
    assert found["lag3"]["candidates"] == []
    assert found["lag3"]["scope"]["kind"] == "lineage_variant"
    assert found["lag3"]["scope"]["species"] == ["other"]
    # The cached family has no member in this species.
    assert found["abs"]["status"] == "not_expected"
    assert found["abs"]["scope"]["families"] == ["PANTHER:PTHR9"]
    # The family alternative has a member here and remains the route to fill.
    assert found["fam"]["status"] == "gap"
    assert result["tree"]["variant_sets"][0]["status"] == "gap"
    assert {p["annoton_id"] for p in result["proposals"]} == {"fam"}
    # The nematode species realizes its own alternative.
    other = steps(evaluate(db, lineage_module("taxon / sequence lineage"), "other"))
    assert other["lag3"]["status"] == "satisfied"


def test_non_taxon_axis_alternatives_keep_gap_filling(db):
    found = steps(evaluate(db, lineage_module("enzyme family"), "toy", "no_iea"))
    assert found["lag3"]["status"] == "gap"
    assert "scope" not in found["lag3"]
