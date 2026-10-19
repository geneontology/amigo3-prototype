"""Gene set over-representation analysis over the pinned integration snapshot.

Two kinds of gene set can be tested. For GO terms, each gene counts once per term: its positive (non-NOT) annotations are propagated
over the reflexive is_a/part_of closure, so a gene annotated to several descendants
of a term is not counted repeatedly. The one-sided hypergeometric test asks whether
the study set holds more annotated genes than a random draw of the same size from
the background. Benjamini-Hochberg and Bonferroni corrections cover every term
whose background size is within the requested bounds, not only those observed in
the study set.

With `method="mgsa"`, terms are instead scored jointly by Bayesian model-based
gene set analysis (MGSA; Bauer, Gagneur & Robinson 2010), run by the genesets
package (Rust, from monarch-initiative/genesets). MGSA explains the study set with
a few active terms, so ancestors and overlapping neighbors of an enriched term are
explained away; each term gets the posterior probability that it is active. The
same sets (size bounds, no roots) are modeled, and sets with identical genes in the
background are merged into one, reported on the most specific term.

For GO-CAM models, a model's set is the loaded genes it references (activity
enablers and other gene-product participants), as resolved by the build through
explicit identifiers. The default background is every gene of the species that
appears in at least one model. Membership says a gene product is part of the model,
not which role it plays there.

With method="neighborhood", targets are topology-derived communities in the
species-specific GO-CAM gene network. Degree-matched connectivity tests reuse the
network null; GO annotation overlaps provide descriptive labels only. The
method="network" option retains connectivity testing against GO-annotated sets.

Two optional GO exclusions are off by default. `exclude_anti_slim` drops terms in the
GO "anti-slim" subsets (gocheck_do_not_annotate, gocheck_do_not_manually_annotate)
from testing, as roots are: they are too general or uninformative to interpret, and
dropping them also shrinks the multiple-testing burden. Annotations to their
descendants still count. `exclude_review_flagged` drops annotations that an AI gene
review (ai-gene-review) marked REMOVE or MARK_AS_OVER_ANNOTATED before propagation, so
a gene only loses a term when no remaining annotation supports it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

import duckdb
import genesets
from pydantic import BaseModel, Field, model_validator

from amigo3.parse import identifier_variants

ASPECTS = {"P": "biological_process", "F": "molecular_function", "C": "cellular_component"}
ROOTS = ("GO:0008150", "GO:0003674", "GO:0005575")
EXPERIMENTAL = ("EXP", "IDA", "IPI", "IMP", "IGI", "IEP", "HTP", "HDA", "HMP", "HGI", "HEP")
EVIDENCE = {
    "all": "All evidence",
    "no_iea": "Exclude IEA (electronic)",
    "experimental": "Experimental only (" + ", ".join(EXPERIMENTAL) + ")",
    "iba": "IBA only (phylogenetic, PAINT)",
}
EvidencePreset = Literal["all", "no_iea", "experimental", "iba"]
TARGETS = {"go": "GO terms", "gocam": "GO-CAM models", "neighborhood": "Network neighborhoods"}
METHODS = {
    "hypergeometric": "Over-representation (hypergeometric)",
    "mgsa": "Model-based (MGSA, Bayesian)",
    "neighborhood": "Network neighborhoods (GO-CAM)",
    "network": "GO-term connectivity (GO-CAM)",
}
ANTI_SLIM_SUBSETS = ("gocheck_do_not_annotate", "gocheck_do_not_manually_annotate")
FLAGGED_REVIEW_ACTIONS = ("REMOVE", "MARK_AS_OVER_ANNOTATED")
EXCLUSIONS = {
    "exclude_anti_slim": {
        "label": "Exclude anti-slim terms",
        "description": "Do not test GO terms in the do-not-annotate subsets ("
        + ", ".join(ANTI_SLIM_SUBSETS)
        + "): very general or uninformative terms. Annotations to their descendants still count.",
    },
    "exclude_review_flagged": {
        "label": "Exclude annotations flagged by AI gene review",
        "description": "Ignore annotations that an AI gene review marked "
        + " or ".join(FLAGGED_REVIEW_ACTIONS)
        + ". Genes without a review are unaffected.",
    },
}
LOCAL_PREFIXES = ("UniProtKB:", "PomBase:", "WB:")
MAX_GENES = 20000


class EnrichmentRequest(BaseModel):
    genes: list[str] = Field(min_length=1, max_length=MAX_GENES)
    species: str
    target: Literal["go", "gocam", "neighborhood"] = "go"
    method: Literal["hypergeometric", "mgsa", "network", "neighborhood"] = "hypergeometric"
    network_taxon: str | None = None
    network_permutations: int = Field(default=1999, ge=99, le=19999)
    network_seed: int = Field(default=0, ge=0, le=2**32 - 1)
    network_alternative: Literal["greater", "less"] = "greater"
    mgsa_samples_per_term: int = Field(default=100, ge=1, le=1000)
    aspect: Literal["P", "F", "C"] = "P"
    evidence: EvidencePreset = "all"
    background: list[str] | None = Field(default=None, max_length=200000)
    min_term_size: int = Field(default=2, ge=1)
    max_term_size: int = Field(default=1000, ge=1)
    max_results: int = Field(default=500, ge=1, le=5000)
    exclude_anti_slim: bool = False
    exclude_review_flagged: bool = False
    view: str | None = None

    @model_validator(mode="after")
    def neighborhood_target(self):
        if self.method == "neighborhood":
            if self.target == "gocam":
                raise ValueError(
                    "Neighborhood analysis tests topology-derived communities, not GO-CAM models"
                )
            self.target = "neighborhood"
            # These controls describe GO membership tests. Neighborhood context
            # always uses BP/all evidence and cannot change the topology test.
            self.aspect = "P"
            self.evidence = "all"
            self.exclude_anti_slim = False
            self.exclude_review_flagged = False
        elif self.target == "neighborhood":
            raise ValueError("Network neighborhoods require method='neighborhood'")
        return self


def split_genes(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,;]+", text) if t]


def hypergeometric_sf(k: int, population: int, successes: int, draws: int) -> float:
    """P(X >= k) for X ~ Hypergeometric(population, successes, draws)."""
    upper = min(successes, draws)
    if k <= max(0, draws - (population - successes)):
        return 1.0
    if k > upper:
        return 0.0

    def log_choose(n, r):
        return math.lgamma(n + 1) - math.lgamma(r + 1) - math.lgamma(n - r + 1)

    denominator = log_choose(population, draws)
    logs = [
        log_choose(successes, i) + log_choose(population - successes, draws - i) - denominator
        for i in range(k, upper + 1)
    ]
    peak = max(logs)
    return min(1.0, math.exp(peak) * sum(math.exp(v - peak) for v in logs))


def benjamini_hochberg(p_values: list[float], tests: int) -> list[float]:
    """Adjusted p-values, treating `tests - len(p_values)` unlisted tests as p=1."""
    order = sorted(range(len(p_values)), key=p_values.__getitem__)
    adjusted = [1.0] * len(p_values)
    running = 1.0
    for rank in range(len(order), 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * tests / rank)
        adjusted[index] = running
    return adjusted


def resolve_genes(db, species: str, tokens: list[str]) -> dict:
    """Map user input to gene records: identifiers first, then symbols, then synonyms.

    Symbols are user-facing labels here, never integration keys. A token matching
    several records at the same level is reported as ambiguous and left out.
    """
    unique, duplicates = [], []
    seen = set()
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if token.upper() in seen:
            duplicates.append(token)
            continue
        seen.add(token.upper())
        unique.append(token)
    candidates = {}
    for token in unique:
        variants = identifier_variants(token)
        if ":" not in token:
            variants += [prefix + token for prefix in LOCAL_PREFIXES]
        candidates[token] = variants
    all_ids = sorted({v for vs in candidates.values() for v in vs})
    upper = sorted({t.upper() for t in unique})
    rows = db.execute(
        """SELECT id, symbol, identifiers, list_transform(synonyms, x -> upper(x)) AS synonyms
        FROM genes WHERE species=? AND (
          id IN (SELECT unnest(?::VARCHAR[])) OR list_has_any(identifiers, ?::VARCHAR[])
          OR upper(symbol) IN (SELECT unnest(?::VARCHAR[]))
          OR list_has_any(list_transform(synonyms, x -> upper(x)), ?::VARCHAR[]))""",
        [species, all_ids, all_ids, upper, upper],
    ).fetchall()
    by_id, by_symbol, by_synonym = {}, {}, {}
    symbols = {}
    for gene_id, symbol, identifiers, synonyms in rows:
        symbols[gene_id] = symbol
        for value in {gene_id, *identifiers}:
            by_id.setdefault(value, set()).add(gene_id)
        by_symbol.setdefault((symbol or "").upper(), set()).add(gene_id)
        for synonym in synonyms:
            by_synonym.setdefault(synonym, set()).add(gene_id)
    matched, unresolved, ambiguous = [], [], []
    for token in unique:
        found, how = set(), ""
        for value in candidates[token]:
            found |= by_id.get(value, set())
        if found:
            how = "identifier"
        elif token.upper() in by_symbol:
            found, how = by_symbol[token.upper()], "symbol"
        elif token.upper() in by_synonym:
            found, how = by_synonym[token.upper()], "synonym"
        if not found:
            unresolved.append(token)
        elif len(found) > 1:
            ambiguous.append(
                {
                    "input": token,
                    "match": how,
                    "candidates": [{"id": g, "symbol": symbols[g]} for g in sorted(found)],
                }
            )
        else:
            gene_id = next(iter(found))
            matched.append({"input": token, "id": gene_id, "symbol": symbols[gene_id], "match": how})
    return {"matched": matched, "unresolved": unresolved, "ambiguous": ambiguous, "duplicates": duplicates}


def evidence_clause(evidence: str) -> tuple[str, list]:
    if evidence == "no_iea":
        return "AND evidence_code <> 'IEA'", []
    if evidence == "experimental":
        return "AND evidence_code IN (SELECT unnest(?::VARCHAR[]))", [list(EXPERIMENTAL)]
    if evidence == "iba":
        return "AND evidence_code = 'IBA'", []
    return "", []


def gene_sets(db, request: EnrichmentRequest) -> str:
    """Create temp table gene_terms(gene_id, term); return a SQL source of id/label/status."""
    if request.target == "gocam":
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if "gocams" not in tables:
            raise ValueError("This build has no GO-CAM models; rebuild with --repository")
        db.execute(
            """CREATE TEMP TABLE gene_terms AS
            SELECT DISTINCT m.gene_id, m.term
            FROM (SELECT id AS term, unnest(gene_ids) AS gene_id FROM gocams) m
            JOIN genes g ON g.id=m.gene_id AND g.species=?""",
            [request.species],
        )
        return "SELECT id, label, status FROM gocams"
    evidence, values = evidence_clause(request.evidence)
    if request.exclude_review_flagged:
        if "review_actions" not in {r[0] for r in db.execute("DESCRIBE annotations").fetchall()}:
            raise ValueError("This build has no annotation reviews; rebuild with --repository")
        evidence += " AND NOT list_has_any(review_actions, ?::VARCHAR[])"
        values.append(list(FLAGGED_REVIEW_ACTIONS))
    # Positive annotations, propagated within the selected aspect; one row per gene/term.
    db.execute(
        f"""CREATE TEMP TABLE gene_terms AS
        SELECT DISTINCT c.gene_id, c.term
        FROM (SELECT gene_id, unnest(isa_partof_closure) AS term FROM annotations
              WHERE species=? AND aspect=? AND NOT negation {evidence}) c
        JOIN terms t ON t.id=c.term AND t.aspect=?""",
        [request.species, request.aspect, *values, request.aspect],
    )
    return "SELECT id, label, '' AS status FROM terms"


def excluded_sets(db, request: EnrichmentRequest) -> list[str]:
    """Set IDs never tested: GO roots, plus anti-slim terms when requested."""
    if request.target != "go" or not request.exclude_anti_slim:
        return list(ROOTS)
    if "subsets" not in {r[0] for r in db.execute("DESCRIBE terms").fetchall()}:
        raise ValueError("This build has no term subsets; rebuild to exclude anti-slim terms")
    anti_slim = db.execute(
        "SELECT id FROM terms WHERE list_has_any(subsets, ?::VARCHAR[])", [list(ANTI_SLIM_SUBSETS)]
    ).fetchall()
    return [*ROOTS, *(r[0] for r in anti_slim)]


def term_closure(db, ids: list[str]) -> list[tuple[str, str]] | None:
    """(term, ancestor) pairs among `ids`, if this build stores term closures."""
    columns = {r[0] for r in db.execute("DESCRIBE terms").fetchall()}
    if "isa_partof_closure" not in columns:
        return None
    return db.execute(
        """SELECT id, ancestor FROM (
          SELECT id, unnest(isa_partof_closure) AS ancestor FROM terms
          WHERE id IN (SELECT unnest(?::VARCHAR[])))
        WHERE ancestor IN (SELECT unnest(?::VARCHAR[]))""",
        [ids, ids],
    ).fetchall()


def mgsa_terms(
    db, request: EnrichmentRequest, terms: list[dict], study: list[str]
) -> tuple[list[dict], dict]:
    """Score `terms` jointly with MGSA; equivalent sets collapse onto one row."""
    ids = [t["id"] for t in terms]
    pairs = db.execute(
        """SELECT g.gene_id, g.term FROM gene_terms g JOIN universe u USING (gene_id)
        WHERE g.term IN (SELECT unnest(?::VARCHAR[]))""",
        [ids],
    ).fetchall()
    universe = [r[0] for r in db.execute("SELECT gene_id FROM universe").fetchall()]
    closure = term_closure(db, ids) if request.target == "go" else None
    fit = genesets.mgsa(
        pairs, study, background=universe, closure=closure, samples_per_term=request.mgsa_samples_per_term
    )
    by_id = {t["id"]: t for t in terms}
    scored = []
    for row in fit["terms"]:
        term = by_id[row["id"]]
        term["posterior"] = row["posterior"]
        term["equivalents"] = [{"id": e, "label": by_id[e]["label"]} for e in row["equivalent_ids"]]
        scored.append(term)
    return scored, fit["stats"]


def enrich(path: Path, request: EnrichmentRequest, build_id: str | None = None) -> dict:
    if request.min_term_size > request.max_term_size:
        raise ValueError("min_term_size must not exceed max_term_size")
    if request.method in {"network", "neighborhood"}:
        from amigo3.network_enrichment import enrich_network

        return enrich_network(path, request, build_id)
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        species = [r[0] for r in db.execute("SELECT DISTINCT species FROM genes ORDER BY 1").fetchall()]
        if request.species not in species:
            raise ValueError(f"Species {request.species!r} is not loaded; choose one of {', '.join(species)}")
        study = resolve_genes(db, request.species, request.genes)
        background = resolve_genes(db, request.species, request.background) if request.background else None
        labels = gene_sets(db, request)
        excluded = excluded_sets(db, request)
        annotated = {r[0] for r in db.execute("SELECT DISTINCT gene_id FROM gene_terms").fetchall()}
        if background is not None:
            supplied = {g["id"] for g in background["matched"]}
            universe = supplied & annotated
            background["without_annotations"] = sorted(supplied - annotated)
        else:
            universe = annotated
        study_ids = {g["id"] for g in study["matched"]}
        outside = sorted(study_ids - universe)
        study_ids &= universe
        population, draws = len(universe), len(study_ids)
        terms = []
        tests = 0
        mgsa_stats = None
        if draws:
            db.execute(
                "CREATE TEMP TABLE universe AS SELECT unnest(?::VARCHAR[]) AS gene_id", [sorted(universe)]
            )
            db.execute(
                "CREATE TEMP TABLE study AS SELECT unnest(?::VARCHAR[]) AS gene_id", [sorted(study_ids)]
            )
            counts = db.execute(
                f"""WITH sized AS (
                  SELECT g.term, count(*) AS term_size,
                    list_sort(list(g.gene_id) FILTER (WHERE s.gene_id IS NOT NULL)) AS genes
                  FROM gene_terms g JOIN universe u USING (gene_id)
                  LEFT JOIN study s USING (gene_id)
                  WHERE g.term NOT IN (SELECT unnest(?::VARCHAR[]))
                  GROUP BY g.term HAVING count(*) BETWEEN ? AND ?)
                SELECT s.term, coalesce(nullif(t.label, ''), s.term), t.status, s.term_size, s.genes,
                  (SELECT count(*) FROM sized) AS tests
                FROM sized s LEFT JOIN ({labels}) t ON t.id=s.term WHERE len(s.genes) > 0""",
                [excluded, request.min_term_size, request.max_term_size],
            ).fetchall()
            tests = counts[0][5] if counts else 0
            symbols = {g["id"]: g["symbol"] for g in study["matched"]}
            for term, label, status, size, genes, _ in counts:
                k = len(genes)
                terms.append(
                    {
                        "id": term,
                        "label": label,
                        "status": status or "",
                        "study_count": k,
                        "term_size": size,
                        "expected": draws * size / population,
                        "fold_enrichment": (k / draws) / (size / population),
                        "p_value": hypergeometric_sf(k, population, size, draws),
                        "genes": [{"id": g, "symbol": symbols.get(g) or g} for g in genes],
                    }
                )
            fdr = benjamini_hochberg([t["p_value"] for t in terms], tests)
            for term, adjusted in zip(terms, fdr, strict=True):
                term["fdr"] = adjusted
                term["bonferroni"] = min(1.0, term["p_value"] * tests)
            terms.sort(key=lambda t: (t["p_value"], -t["fold_enrichment"], t["id"]))
            if request.method == "mgsa" and terms:
                terms, mgsa_stats = mgsa_terms(db, request, terms, sorted(study_ids))
    return {
        "build_id": build_id,
        "parameters": {
            **request.model_dump(
                exclude={
                    "genes",
                    "background",
                    "view",
                    "network_taxon",
                    "network_alternative",
                    "network_permutations",
                    "network_seed",
                }
            ),
            "target_label": TARGETS[request.target],
            "aspect_label": ASPECTS[request.aspect],
            "evidence_label": EVIDENCE[request.evidence],
            "background": "custom"
            if background is not None
            else "annotated genes in species"
            if request.target == "go"
            else "genes in any GO-CAM model in species",
            "method_label": METHODS[request.method],
            "test": "Bayesian model-based gene set analysis (MGSA): posterior probability that each set is "
            "active, with term, false-positive and false-negative rates integrated out under uniform priors"
            if request.method == "mgsa"
            else "one-sided hypergeometric (over-representation)",
            "sets": "reflexive is_a/part_of; NOT annotations excluded; root terms excluded"
            if request.target == "go"
            else "genes referenced by each model (any participant role); aspect and evidence do not apply",
            "exclusions": [
                EXCLUSIONS[k]["label"] for k in EXCLUSIONS if request.target == "go" and getattr(request, k)
            ],
            "correction": "Benjamini-Hochberg and Bonferroni over all sets within the size bounds"
            + (
                "; MGSA posteriors need no correction, FDR is shown for comparison"
                if request.method == "mgsa"
                else ""
            ),
        },
        "counts": {
            "input": len(request.genes),
            "study": draws,
            "population": population,
            "tests": tests,
            "terms_with_study_genes": len(terms),
        },
        "mgsa": mgsa_stats,
        "study": {**study, "outside_background": outside},
        "background": background,
        "terms": terms[: request.max_results],
        "truncated": len(terms) > request.max_results,
    }
