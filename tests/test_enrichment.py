import math

import duckdb
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.enrichment import EnrichmentRequest, benjamini_hochberg, enrich, hypergeometric_sf, split_genes


def exact_sf(k, population, successes, draws):
    total = math.comb(population, draws)
    return (
        sum(
            math.comb(successes, i) * math.comb(population - successes, draws - i)
            for i in range(k, min(successes, draws) + 1)
        )
        / total
    )


@pytest.mark.parametrize(
    "args", [(0, 10, 3, 4), (1, 10, 3, 4), (3, 10, 3, 4), (4, 10, 3, 4), (5, 50, 8, 12), (20, 20000, 40, 100)]
)
def test_hypergeometric_matches_exact_sum(args):
    assert hypergeometric_sf(*args) == pytest.approx(exact_sf(*args), rel=1e-9, abs=1e-300)


def test_benjamini_hochberg_counts_untested_terms():
    assert benjamini_hochberg([0.01, 0.04, 0.03], 3) == pytest.approx([0.03, 0.04, 0.04])
    # Two more tested terms without study genes (p=1) enlarge the correction.
    assert benjamini_hochberg([0.01], 5) == pytest.approx([0.05])


def test_split_genes():
    assert split_genes("A, B;C\n\tD  ") == ["A", "B", "C", "D"]


@pytest.fixture
def snapshot(tmp_path):
    db = duckdb.connect(str(tmp_path / "integration.duckdb"))
    db.execute("CREATE TABLE terms(id VARCHAR, label VARCHAR, aspect VARCHAR)")
    db.execute(
        """INSERT INTO terms VALUES ('GO:0008150','biological_process','P'),
        ('GO:1','parent process','P'), ('GO:2','child process','P'), ('GO:3','other process','P'),
        ('GO:9','function','F')"""
    )
    db.execute(
        """CREATE TABLE genes(id VARCHAR, symbol VARCHAR, synonyms VARCHAR[], identifiers VARCHAR[],
        species VARCHAR)"""
    )
    db.execute(
        """CREATE TABLE annotations(gene_id VARCHAR, species VARCHAR, aspect VARCHAR, negation BOOLEAN,
        evidence_code VARCHAR, isa_partof_closure VARCHAR[])"""
    )
    child = ["GO:0008150", "GO:1", "GO:2"]
    other = ["GO:0008150", "GO:3"]
    for i in range(1, 21):
        db.execute(
            "INSERT INTO genes VALUES (?, ?, ?, ?, 'human')",
            [f"HGNC:{i}", f"G{i}", ["DUP"] if i in (19, 20) else [], [f"HGNC:{i}", f"UniProtKB:P{i:05d}"]],
        )
        if i <= 5:  # Two annotations to the same closure count once per gene.
            db.execute(
                "INSERT INTO annotations VALUES (?, 'human', 'P', false, 'IDA', ?)", [f"HGNC:{i}", child]
            )
            db.execute(
                "INSERT INTO annotations VALUES (?, 'human', 'P', false, 'IEA', ?)", [f"HGNC:{i}", child]
            )
        elif i <= 18:
            db.execute(
                "INSERT INTO annotations VALUES (?, 'human', 'P', false, 'IEA', ?)", [f"HGNC:{i}", other]
            )
    db.execute("INSERT INTO annotations VALUES ('HGNC:6', 'human', 'P', true, 'IDA', ?)", [child])
    for gene, closure in (("HGNC:1", child), ("HGNC:2", child), ("HGNC:7", other)):  # PAINT rows.
        db.execute("INSERT INTO annotations VALUES (?, 'human', 'P', false, 'IBA', ?)", [gene, closure])
    db.execute("INSERT INTO genes VALUES ('PomBase:X', 'G1', [], ['PomBase:X'], 'pombe')")
    db.execute("CREATE TABLE gocams(id VARCHAR, label VARCHAR, status VARCHAR, gene_ids VARCHAR[])")
    db.execute(
        """INSERT INTO gocams VALUES
        ('gomodel:a', 'Pathway A', 'production', ['HGNC:1', 'HGNC:2', 'HGNC:3', 'HGNC:4']),
        ('gomodel:b', 'Pathway B', 'development', ['HGNC:5', 'HGNC:6', 'HGNC:7', 'HGNC:8', 'PomBase:X']),
        ('gomodel:c', '', 'production', ['HGNC:9', 'HGNC:10', 'HGNC:1']),
        ('gomodel:d', 'Pombe only', 'production', ['PomBase:X'])"""
    )
    db.close()
    return tmp_path


def test_enrichment_counts_genes_once_and_uses_annotated_background(snapshot):
    request = EnrichmentRequest(
        genes=["G1", "g2", "UniProtKB:P00003", "P00004", "G6", "G20", "nope", "G1"], species="human"
    )
    result = enrich(snapshot, request, "build")
    study = result["study"]
    assert [g["id"] for g in study["matched"]] == [
        "HGNC:1",
        "HGNC:2",
        "HGNC:3",
        "HGNC:4",
        "HGNC:6",
        "HGNC:20",
    ]
    assert {g["match"] for g in study["matched"][2:4]} == {"identifier"}
    assert study["unresolved"] == ["nope"] and study["duplicates"] == ["G1"]
    assert study["outside_background"] == ["HGNC:20"]  # No annotation; NOT rows never count.
    counts = result["counts"]
    assert counts["population"] == 18 and counts["study"] == 5
    assert counts["tests"] == 3  # Root excluded; GO:1, GO:2, GO:3 tested.
    terms = {t["id"]: t for t in result["terms"]}
    assert terms["GO:1"]["study_count"] == 4 and terms["GO:1"]["term_size"] == 5
    assert terms["GO:1"]["p_value"] == pytest.approx(exact_sf(4, 18, 5, 5))
    assert terms["GO:1"]["fold_enrichment"] == pytest.approx((4 / 5) / (5 / 18))
    assert [g["symbol"] for g in terms["GO:1"]["genes"]] == ["G1", "G2", "G3", "G4"]
    assert terms["GO:3"]["study_count"] == 1
    assert result["terms"][0]["id"] in ("GO:1", "GO:2")
    assert "GO:0008150" not in terms and "GO:9" not in terms


def test_enrichment_filters_evidence_background_and_ambiguity(snapshot):
    result = enrich(
        snapshot, EnrichmentRequest(genes=["G1", "G7", "DUP"], species="human", evidence="experimental"), "b"
    )
    assert result["counts"]["population"] == 5  # Only IDA-annotated genes remain.
    assert result["study"]["outside_background"] == ["HGNC:7"]
    assert result["study"]["ambiguous"][0]["input"] == "DUP"
    assert len(result["study"]["ambiguous"][0]["candidates"]) == 2
    custom = enrich(
        snapshot,
        EnrichmentRequest(genes=["G1", "G2"], species="human", background=["G1", "G2", "G3", "G7", "G20"]),
        "b",
    )
    assert custom["counts"]["population"] == 4
    assert custom["background"]["without_annotations"] == ["HGNC:20"]
    assert custom["parameters"]["background"] == "custom"
    sized = enrich(snapshot, EnrichmentRequest(genes=["G1"], species="human", max_term_size=5), "b")
    assert {t["id"] for t in sized["terms"]} == {"GO:1", "GO:2"} and sized["counts"]["tests"] == 2


def test_enrichment_iba_only_evidence(snapshot):
    result = enrich(
        snapshot, EnrichmentRequest(genes=["G1", "G2", "G3"], species="human", evidence="iba"), "b"
    )
    assert result["counts"]["population"] == 3  # Only IBA-annotated genes remain.
    assert result["study"]["outside_background"] == ["HGNC:3"]
    terms = {t["id"]: t for t in result["terms"]}
    assert (terms["GO:1"]["study_count"], terms["GO:1"]["term_size"]) == (2, 2)
    assert "GO:3" not in terms  # One IBA gene is below the default minimum term size.
    assert result["parameters"]["evidence"] == "iba"
    assert result["parameters"]["evidence_label"].startswith("IBA only")
    with pytest.raises(ValueError):
        enrich(snapshot, EnrichmentRequest(genes=["G1"], species="mouse"), "b")
    with pytest.raises(ValueError):
        enrich(
            snapshot, EnrichmentRequest(genes=["G1"], species="human", min_term_size=5, max_term_size=2), "b"
        )


def test_enrichment_can_exclude_anti_slim_terms(snapshot):
    request = {"genes": ["G1", "G2", "G7"], "species": "human"}
    with pytest.raises(ValueError, match="no term subsets"):
        enrich(snapshot, EnrichmentRequest(**request, exclude_anti_slim=True), "b")
    db = duckdb.connect(str(snapshot / "integration.duckdb"))
    db.execute("ALTER TABLE terms ADD COLUMN subsets VARCHAR[] DEFAULT []")
    db.execute("UPDATE terms SET subsets = ['gocheck_do_not_annotate', 'goslim_generic'] WHERE id = 'GO:1'")
    db.execute("UPDATE terms SET subsets = ['gocheck_do_not_manually_annotate'] WHERE id = 'GO:3'")
    db.close()
    plain = enrich(snapshot, EnrichmentRequest(**request), "b")
    assert plain["counts"]["tests"] == 3 and plain["parameters"]["exclusions"] == []
    result = enrich(snapshot, EnrichmentRequest(**request, exclude_anti_slim=True), "b")
    # Anti-slim terms are neither reported nor counted as tests; the background is unchanged.
    assert [t["id"] for t in result["terms"]] == ["GO:2"] and result["counts"]["tests"] == 1
    assert result["counts"]["population"] == plain["counts"]["population"]
    assert result["terms"][0]["p_value"] == pytest.approx(
        next(t for t in plain["terms"] if t["id"] == "GO:2")["p_value"]
    )
    assert result["parameters"]["exclusions"] == ["Exclude anti-slim terms"]
    # GO-CAM models have no subsets, so the flag does not apply.
    models = enrich(snapshot, EnrichmentRequest(**request, target="gocam", exclude_anti_slim=True), "b")
    assert models["counts"]["tests"] > 0 and models["parameters"]["exclusions"] == []


def test_enrichment_can_exclude_review_flagged_annotations(snapshot):
    request = {"genes": ["G1", "G2", "G3"], "species": "human", "evidence": "experimental"}
    with pytest.raises(ValueError, match="no annotation reviews"):
        enrich(snapshot, EnrichmentRequest(**request, exclude_review_flagged=True), "b")
    db = duckdb.connect(str(snapshot / "integration.duckdb"))
    db.execute("ALTER TABLE annotations ADD COLUMN review_actions VARCHAR[] DEFAULT []")
    # G1's only experimental annotation is flagged REMOVE. G2's is marked over-annotated by one
    # assessment and accepted by another; any flag excludes it. G3's MODIFY is not a flag.
    for gene, actions in (("HGNC:1", ["REMOVE"]), ("HGNC:2", ["ACCEPT", "MARK_AS_OVER_ANNOTATED"])):
        db.execute(
            "UPDATE annotations SET review_actions = ? WHERE gene_id = ? AND evidence_code = 'IDA'",
            [actions, gene],
        )
    db.execute("UPDATE annotations SET review_actions = ['MODIFY'] WHERE gene_id = 'HGNC:3'")
    db.close()
    plain = enrich(snapshot, EnrichmentRequest(**request), "b")
    assert plain["counts"]["population"] == 5 and plain["counts"]["study"] == 3
    result = enrich(snapshot, EnrichmentRequest(**request, exclude_review_flagged=True), "b")
    assert result["counts"]["population"] == 3
    assert result["study"]["outside_background"] == ["HGNC:1", "HGNC:2"]
    assert result["parameters"]["exclusions"] == ["Exclude annotations flagged by AI gene review"]
    # With all evidence, unflagged IEA annotations still place G1 and G2 on their terms.
    kept = enrich(
        snapshot, EnrichmentRequest(**request | {"evidence": "all"}, exclude_review_flagged=True), "b"
    )
    assert kept["counts"]["population"] == 18 and kept["study"]["outside_background"] == []


def test_gocam_enrichment_uses_model_genes_and_model_background(snapshot):
    result = enrich(
        snapshot, EnrichmentRequest(genes=["G1", "G2", "G3", "G12"], species="human", target="gocam"), "b"
    )
    assert result["counts"]["population"] == 10  # Human genes in any model; pombe member ignored.
    assert result["counts"]["study"] == 3 and result["study"]["outside_background"] == ["HGNC:12"]
    assert result["counts"]["tests"] == 3  # The pombe-only model has no human members.
    models = {m["id"]: m for m in result["terms"]}
    assert set(models) == {"gomodel:a", "gomodel:c"}
    a = models["gomodel:a"]
    assert (a["label"], a["status"], a["study_count"], a["term_size"]) == ("Pathway A", "production", 3, 4)
    assert a["p_value"] == pytest.approx(exact_sf(3, 10, 4, 3))
    assert models["gomodel:c"]["label"] == "gomodel:c"  # Untitled models fall back to their ID.
    assert result["terms"][0]["id"] == "gomodel:a"
    assert result["parameters"]["background"] == "genes in any GO-CAM model in species"
    # Aspect and evidence presets do not change GO-CAM membership.
    same = enrich(
        snapshot,
        EnrichmentRequest(
            genes=["G1", "G2", "G3"], species="human", target="gocam", aspect="C", evidence="experimental"
        ),
        "b",
    )
    assert same["terms"] == result["terms"]


def test_mgsa_merges_identical_sets_onto_the_most_specific_term(snapshot):
    request = EnrichmentRequest(genes=["G1", "G2", "G3", "G4", "G7"], species="human", method="mgsa")
    # The fixture has no term closures, so ties fall back to the lowest ID.
    plain = enrich(snapshot, request, "b")
    assert {t["id"] for t in plain["terms"]} == {"GO:1", "GO:3"}
    db = duckdb.connect(str(snapshot / "integration.duckdb"))
    db.execute("ALTER TABLE terms ADD COLUMN isa_partof_closure VARCHAR[]")
    db.execute("UPDATE terms SET isa_partof_closure = ['GO:2', 'GO:1', 'GO:0008150'] WHERE id = 'GO:2'")
    db.execute("UPDATE terms SET isa_partof_closure = [id, 'GO:0008150'] WHERE id <> 'GO:2'")
    db.close()
    result = enrich(snapshot, request, "b")
    terms = {t["id"]: t for t in result["terms"]}
    # GO:1 and GO:2 hold the same genes, so they are one variable shown on the child.
    assert set(terms) == {"GO:2", "GO:3"}
    assert terms["GO:2"]["equivalents"] == [{"id": "GO:1", "label": "parent process"}]
    assert terms["GO:2"]["study_count"] == 4 and "fdr" in terms["GO:2"]
    assert all(0 <= t["posterior"] <= 1 for t in result["terms"])
    posteriors = [t["posterior"] for t in result["terms"]]
    assert posteriors == sorted(posteriors, reverse=True)
    assert result["mgsa"]["modeled_terms"] == 2
    assert result["parameters"]["method_label"].startswith("Model-based")
    assert enrich(snapshot, request, "b") == result  # Seeded, so reproducible.


def test_mgsa_scores_gocam_models(snapshot):
    result = enrich(
        snapshot,
        EnrichmentRequest(genes=["G1", "G2", "G3"], species="human", target="gocam", method="mgsa"),
        "b",
    )
    assert result["terms"][0]["id"] == "gomodel:a"
    assert all(0 <= t["posterior"] <= 1 for t in result["terms"])


def test_gocam_enrichment_requires_models(tmp_path):
    db = duckdb.connect(str(tmp_path / "integration.duckdb"))
    db.execute(
        "CREATE TABLE genes(id VARCHAR, symbol VARCHAR, synonyms VARCHAR[], identifiers VARCHAR[], species VARCHAR)"
    )
    db.execute("INSERT INTO genes VALUES ('HGNC:1', 'G1', [], [], 'human')")
    db.close()
    with pytest.raises(ValueError, match="no GO-CAM models"):
        enrich(tmp_path, EnrichmentRequest(genes=["G1"], species="human", target="gocam"))


def test_enrichment_api_and_page(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        options = client.get("/api/enrichment").json()
        assert {s["value"] for s in options["species"]} == {"human", "pombe"}
        assert set(options["aspects"]) == {"P", "F", "C"}
        assert set(options["targets"]) == {"go", "gocam", "neighborhood"}
        assert set(options["methods"]) == {"hypergeometric", "mgsa", "network", "neighborhood"}
        assert set(options["evidence"]) == {"all", "no_iea", "experimental", "iba"}
        assert {s["value"]: s["gocam_genes"] for s in options["species"]} == {"human": 2, "pombe": 0}
        assert set(options["exclusions"]) == {"exclude_anti_slim", "exclude_review_flagged"}
        assert all(e["label"] and e["description"] for e in options["exclusions"].values())
        models = client.post(
            "/api/enrichment",
            json={"genes": ["GENE1", "GENE3"], "species": "human", "target": "gocam", "min_term_size": 1},
        ).json()
        # GENE2 is a model participant (input), so it is in the GO-CAM background.
        assert models["counts"]["population"] == 2 and models["counts"]["study"] == 1
        assert models["terms"][0]["id"] == "gomodel:test"
        assert models["terms"][0]["label"] == "Test causal model"
        assert models["study"]["outside_background"] == ["HGNC:3"]
        response = client.post(
            "/api/enrichment",
            json={
                "genes": ["GENE1", "GENE2", "GENE3"],
                "species": "human",
                "aspect": "F",
                "min_term_size": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["build_id"] == options["build_id"]
        # GENE2 has only a NOT annotation and GENE3 none: neither is in the background.
        assert data["counts"]["population"] == data["counts"]["study"] == 1
        assert sorted(data["study"]["outside_background"]) == ["HGNC:2", "HGNC:3"]
        assert all(t["p_value"] == 1 for t in data["terms"])
        iba = client.post("/api/enrichment", json={"genes": ["GENE1"], "species": "human", "evidence": "iba"})
        assert iba.status_code == 200 and iba.json()["parameters"]["evidence"] == "iba"
        bogus = client.post("/api/enrichment", json={"genes": ["GENE1"], "species": "human", "evidence": "x"})
        assert bogus.status_code == 422
        both = {"exclude_anti_slim": True, "exclude_review_flagged": True}
        flagged = client.post(
            "/api/enrichment",
            json={"genes": ["GENE1"], "species": "human", "aspect": "F", "min_term_size": 1, **both},
        )
        assert flagged.status_code == 200
        assert len(flagged.json()["parameters"]["exclusions"]) == 2
        bad = client.post("/api/enrichment", json={"genes": ["GENE1"], "species": "human", "view": "other"})
        assert bad.status_code == 422
        assert client.post("/api/enrichment", json={"genes": [], "species": "human"}).status_code == 422
        page = client.get("/enrichment")
        assert page.status_code == 200 and "/enrichment.js?v=" in page.text
