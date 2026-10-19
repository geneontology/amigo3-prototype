"""Competitive gene-set connectivity on a fixed, species-specific GO-CAM graph.

The null is uniform sampling of study sets within exact degree strata, NOT
independent edges or the NEAT hypergeometric approximation. Small null spaces
are enumerated; otherwise use seeded Monte Carlo with the observed set added
to the reference distribution. GO memberships and graph topology stay fixed.
"""

import itertools
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from amigo3.enrichment import (
    ASPECTS,
    EVIDENCE,
    EXCLUSIONS,
    METHODS,
    EnrichmentRequest,
    benjamini_hochberg,
    excluded_sets,
    gene_sets,
    resolve_genes,
)
from amigo3.gene_network import gene_network
from amigo3.neighborhoods import communities, describe, overview
from amigo3.parse import normalize_id


class NetworkEdgesRequest(EnrichmentRequest):
    term_id: str
    include_context: bool = False


def options(path: Path):
    graph = gene_network(path)
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        species = db.execute("SELECT DISTINCT species, taxon_id FROM genes ORDER BY 1, 2").fetchall()
    return {
        "taxa": [
            {"species": s, **graph.coverage[t]}
            for s, t in species
            if t in graph.coverage and graph.coverage[t]["catalog_genes"]
        ],
        "default_permutations": 1999,
        "max_permutations": 19999,
    }


def unique_matches(mapping):
    """Aliases of the same gene are a single study/background member."""
    seen, matched = set(), []
    for gene in mapping["matched"]:
        if gene["id"] in seen:
            mapping["duplicates"].append(gene["input"])
        else:
            seen.add(gene["id"])
            matched.append(gene)
    mapping["matched"] = matched
    return mapping


def prepare(path: Path, request: EnrichmentRequest):
    neighborhood = request.method == "neighborhood"
    if request.target != ("neighborhood" if neighborhood else "go"):
        raise ValueError("Network connectivity currently tests GO terms; choose target='go'")
    if request.min_term_size > request.max_term_size:
        raise ValueError("min_term_size must not exceed max_term_size")
    graph = gene_network(path)
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        rows = db.execute(
            "SELECT id, symbol, taxon_id FROM genes WHERE species=?", [request.species]
        ).fetchall()
        if not rows:
            raise ValueError("Species is not loaded")
        candidates = sorted(
            {t for _, _, t in rows if t in graph.coverage and graph.coverage[t]["catalog_genes"]}
        )
        taxon = normalize_id(request.network_taxon) if request.network_taxon else None
        if taxon is None:
            if len(candidates) != 1:
                raise ValueError(
                    "Choose an exact network_taxon for this species: "
                    + (", ".join(candidates) or "no GO-CAM gene network available")
                )
            taxon = candidates[0]
        if taxon not in candidates:
            raise ValueError("The selected network taxon is not available for this species")
        labels = {g: label for g, label, t in rows if t == taxon}
        study = unique_matches(resolve_genes(db, request.species, request.genes))
        background = (
            unique_matches(resolve_genes(db, request.species, request.background))
            if request.background is not None
            else None
        )
        study_ids = {g["id"] for g in study["matched"]}
        allowed = {g["id"] for g in background["matched"]} if background is not None else set(labels)
        nodes = {
            n["id"]: n["gene_id"]
            for n in graph.nodes.values()
            if n["taxon"] == taxon and n["resolution"] == "catalog" and n["gene_id"] in labels
        }
        catalog_nodes = set(nodes.values())
        retained, reference_edges = [], []
        reference_adjacency = {g: set() for g in catalog_nodes}
        adjacency = {g: set() for g in catalog_nodes & allowed}
        for edge in graph.edges.values():
            if edge["source"] not in nodes or edge["target"] not in nodes:
                continue
            a, b = nodes[edge["source"]], nodes[edge["target"]]
            reference_edges.append((a, b))
            reference_adjacency[a].add(b)
            reference_adjacency[b].add(a)
            if a in adjacency and b in adjacency:
                adjacency[a].add(b)
                adjacency[b].add(a)
                retained.append((a, b, edge))
        isolated = {g for g, neighbors in adjacency.items() if not neighbors}
        universe = set(adjacency) - isolated
        adjacency = {g: adjacency[g] for g in sorted(universe)}
        used = study_ids & universe
        study.update(
            {
                "outside_background": sorted(study_ids - universe),
                "outside_custom_background": sorted(study_ids - allowed) if background is not None else [],
                "not_in_network": sorted(
                    (study_ids & allowed if background is not None else study_ids) - catalog_nodes
                ),
                "isolated_in_background": sorted(study_ids & isolated),
            }
        )
        if background is not None:
            background.update(
                {
                    "not_in_network": sorted(allowed - catalog_nodes),
                    "isolated_in_background": sorted(isolated),
                }
            )
        # GO is descriptive only in neighborhood mode, always on the reference
        # graph and independent of the study/background and annotation controls.
        annotation_request = (
            request.model_copy(
                update={
                    "target": "go",
                    "method": "network",
                    "aspect": "P",
                    "evidence": "all",
                    "exclude_anti_slim": False,
                    "exclude_review_flagged": False,
                }
            )
            if neighborhood
            else request
        )
        gene_sets(db, annotation_request)
        excluded = excluded_sets(db, annotation_request)
        annotated_universe = {g for g, ns in reference_adjacency.items() if ns} if neighborhood else universe
        go_sets = defaultdict(set)
        for g, term in db.execute(
            "SELECT gene_id, term FROM gene_terms WHERE gene_id IN (SELECT unnest(?::VARCHAR[])) AND term NOT IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(annotated_universe), excluded],
        ).fetchall():
            go_sets[term].add(g)
        term_labels = dict(db.execute("SELECT id, label FROM terms").fetchall())
        partition, descriptions, groups = None, {}, {}
        if neighborhood:
            fixed, partition = communities(taxon, reference_edges)
            groups = {ident: set(gs) for ident, gs in fixed}
            descriptions = describe(groups, reference_adjacency, labels, go_sets, term_labels, partition)
            memberships = {ident: gs & universe for ident, gs in groups.items()}
            term_labels = {ident: d["label"] for ident, d in descriptions.items()}
        else:
            memberships = go_sets
        memberships = {
            t: gs
            for t, gs in sorted(memberships.items())
            if request.min_term_size <= len(gs) <= request.max_term_size
        }
    return {
        "graph": graph,
        "communities": groups,
        "partition": partition,
        "descriptions": descriptions,
        "taxon": taxon,
        "labels": labels,
        "study": study,
        "background": background,
        "universe": universe,
        "used": used,
        "adjacency": adjacency,
        "edges": retained,
        "memberships": memberships,
        "term_labels": term_labels,
        "coverage": {
            "projection": graph.coverage[taxon],
            "catalog_nodes_in_taxon": len(catalog_nodes),
            "background_network_nodes": len(universe),
            "background_network_edges": len(retained),
            "isolated_background_nodes": len(isolated),
        },
    }


class ConnectivityTest:
    """Count unique connecting pairs using two orientation bits per graph edge."""

    def __init__(self, adjacency, study, memberships):
        self.adjacency, self.study, self.memberships = adjacency, study, memberships
        self.pools = defaultdict(list)
        for gene, neighbors in sorted(adjacency.items()):
            self.pools[len(neighbors)].append(gene)
        self.draws = Counter(len(adjacency[g]) for g in study)
        self.strata = [(degree, self.pools[degree], k) for degree, k in sorted(self.draws.items())]
        self.null_size = math.prod(math.comb(len(pool), k) for _, pool, k in self.strata)
        self.outgoing = {g: 0 for g in adjacency}
        self.incoming = {g: 0 for g in adjacency}
        self.even_mask = 0
        i = 0
        for a, neighbors in sorted(adjacency.items()):
            for b in sorted(neighbors):
                if a >= b:
                    continue
                forward, reverse = 1 << (2 * i), 1 << (2 * i + 1)
                self.outgoing[a] |= forward
                self.incoming[b] |= forward
                self.outgoing[b] |= reverse
                self.incoming[a] |= reverse
                self.even_mask |= forward
                i += 1
        self.term_masks = [self.mask(gs, self.incoming) for gs in memberships.values()]

    @staticmethod
    def mask(genes, masks):
        result = 0
        for g in genes:
            result |= masks[g]
        return result

    def scores(self, genes):
        mask = self.mask(genes, self.outgoing)
        # If both orientations hit (overlapping sets), count the edge only once.
        hits = [mask & term for term in self.term_masks]
        return [((h | (h >> 1)) & self.even_mask).bit_count() for h in hits]

    def expectation(self, members):
        """Exact expectation under within-degree sampling, including overlap."""
        probability = {d: self.draws[d] / len(pool) for d, pool in self.pools.items()}
        total = 0.0
        for a in sorted(members):
            da = len(self.adjacency[a])
            for b in sorted(self.adjacency[a]):
                db = len(self.adjacency[b])
                total += probability[db]
                if b in members and a < b:
                    if da == db:
                        n, k = len(self.pools[da]), self.draws[da]
                        both = k * (k - 1) / (n * (n - 1))  # Distinct endpoints guarantee n >= 2.
                    else:
                        both = probability[da] * probability[db]
                    total -= both
        return max(0.0, total)

    def run(self, permutations, seed, alternative):
        observed = self.scores(self.study)
        exact = self.null_size <= permutations
        if exact:
            combinations = (itertools.combinations(pool, k) for _, pool, k in self.strata)
            samples = (
                set(itertools.chain.from_iterable(parts)) for parts in itertools.product(*combinations)
            )
            count = self.null_size
        else:
            rng = random.Random(seed)
            samples = (
                set(itertools.chain.from_iterable(rng.sample(pool, k) for _, pool, k in self.strata))
                for _ in range(permutations)
            )
            count = permutations
        extreme = [0] * len(observed)
        for sample in samples:
            for i, (value, obs) in enumerate(zip(self.scores(sample), observed, strict=True)):
                extreme[i] += value >= obs if alternative == "greater" else value <= obs
        p_values = [n / count if exact else (n + 1) / (count + 1) for n in extreme]
        return (
            observed,
            p_values,
            {
                "sampling": "exact" if exact else "monte_carlo",
                "samples": count,
                "possible_sets": str(self.null_size),
                "seed": seed,
                "minimum_p_value": 1 / count if exact else 1 / (count + 1),
                "degree_strata": [
                    {"degree": d, "population": len(pool), "study": k} for d, pool, k in self.strata
                ],
                "fixed_study_genes": sorted(g for _, pool, k in self.strata if len(pool) == k for g in pool),
            },
        )


def connecting_edges(edges, study, members):
    return [
        (a, b, edge) for a, b, edge in edges if (a in study and b in members) or (b in study and a in members)
    ]


def enrich_network(path: Path, request: EnrichmentRequest, build_id: str | None):
    ctx = prepare(path, request)
    used, memberships = ctx["used"], ctx["memberships"]
    terms, null = [], None
    if used and memberships:
        test = ConnectivityTest(ctx["adjacency"], used, memberships)
        observed, p_values, null = test.run(
            request.network_permutations, request.network_seed, request.network_alternative
        )
        adjusted = benjamini_hochberg(p_values, len(memberships))
        harmonic = sum(1 / i for i in range(1, len(memberships) + 1))
        for i, (term, members) in enumerate(memberships.items()):
            expected = test.expectation(members)
            terms.append(
                {
                    "id": term,
                    "label": ctx["term_labels"].get(term) or term,
                    "status": "",
                    "study_count": len(used & members),
                    "term_size": len(members),
                    "connections": observed[i],
                    "expected": expected,
                    "fold_enrichment": observed[i] / expected if expected > 0 else None,
                    "p_value": p_values[i],
                    "fdr": adjusted[i],
                    "by_fdr": min(1.0, adjusted[i] * harmonic),
                    "bonferroni": min(1.0, p_values[i] * len(memberships)),
                }
            )
        terms.sort(key=lambda t: (t["p_value"], t["id"]))
    total = len(terms)
    all_terms = terms
    terms = terms[: request.max_results]

    def label(g):
        return {"id": g, "symbol": ctx["labels"].get(g) or g}

    for term in terms:
        members = memberships[term["id"]]
        hits = connecting_edges(ctx["edges"], used, members)
        endpoints = {g for a, b, _ in hits for g in (a, b)}
        term["genes"] = [label(g) for g in sorted(endpoints & used)]
        term["neighbors"] = [label(g) for g in sorted(endpoints & members)]
        term["overlap_genes"] = [label(g) for g in sorted(used & members)]
        if ctx["partition"]:
            term.update(ctx["descriptions"][term["id"]])
    result = {
        "build_id": build_id,
        "parameters": {
            **request.model_dump(exclude={"genes", "background", "view", "mgsa_samples_per_term"}),
            "network_taxon": ctx["taxon"],
            "method_label": METHODS["network"],
            "target_label": "GO terms",
            "aspect_label": ASPECTS[request.aspect],
            "evidence_label": EVIDENCE[request.evidence],
            "background": "connected catalog genes in the GO-CAM graph"
            + (", induced by the custom background" if ctx["background"] is not None else ""),
            "test": "one-sided "
            + ("enriched" if request.network_alternative == "greater" else "depleted")
            + " connectivity against uniformly sampled gene sets with the same exact degree profile",
            "sets": "reflexive is_a/part_of; NOT annotations and roots excluded; each connecting pair counted once",
            "correction": "Benjamini–Yekutieli FDR (displayed), Benjamini–Hochberg FDR and Bonferroni over every set within size bounds",
            "exclusions": [EXCLUSIONS[k]["label"] for k in EXCLUSIONS if getattr(request, k)],
        },
        "counts": {
            "input": len(request.genes),
            "study": len(used),
            "population": len(ctx["universe"]),
            "tests": total,
            "terms_with_study_genes": sum(bool(used & gs) for gs in memberships.values()),
        },
        "study": ctx["study"],
        "background": ctx["background"],
        "terms": terms,
        "truncated": total > len(terms),
        "network": {
            "coverage": ctx["coverage"],
            "null": null,
            "policy": ctx["graph"].result(summary=True)["policy"],
        },
        "mgsa": None,
    }
    result["network"]["kind"] = "neighborhood" if ctx["partition"] else "go"
    if ctx["partition"]:
        result["network"]["neighborhoods"] = overview(ctx, all_terms, {t["id"] for t in terms})
        result["parameters"].update(
            method_label=METHODS["neighborhood"],
            target_label="Network neighborhoods",
            sets="fixed overlapping local neighborhoods, intersected with background; each connecting pair counted once",
            exclusions=[],
            annotation_context="Descriptive GO biological process overlap; does not define or score neighborhoods",
        )
    return result


def edge_details(path: Path, request: NetworkEdgesRequest, build_id: str):
    if request.method not in {"network", "neighborhood"}:
        raise ValueError("Edge inspection requires method='network' or method='neighborhood'")
    ctx = prepare(path, request)
    if request.term_id not in ctx["memberships"]:
        raise ValueError("Term is not among the sets tested under these filters")
    hits = connecting_edges(ctx["edges"], ctx["used"], ctx["memberships"][request.term_id])
    counted = {(a, b) for a, b, _ in hits}
    members = ctx["memberships"][request.term_id]
    displayed_genes = {g for a, b, _ in hits for g in (a, b)}
    if request.include_context and ctx["partition"]:
        displayed_genes |= members
        hits = [
            (a, b, edge)
            for a, b, edge in ctx["edges"]
            if (a, b) in counted or (a in members and b in members)
        ]
    # Full, untruncated evidence. No stochastic test is rerun for inspection.
    edges = [
        {**edge, "source_gene": a, "target_gene": b, "counted": (a, b) in counted} for a, b, edge in hits
    ]
    activity_ids = {a[k] for e in edges for a in e["assertions"] for k in ("source", "target")}
    activities = [ctx["graph"].activities[k] for k in sorted(activity_ids)]
    model_ids = sorted({a["model_id"] for a in activities})
    return {
        "build_id": build_id,
        "taxon": ctx["taxon"],
        "term_id": request.term_id,
        "counted_connections": len(counted),
        "neighborhood": ctx["descriptions"].get(request.term_id),
        "member_ids": sorted(members) if ctx["partition"] else [],
        "study_ids": sorted(ctx["used"]),
        "edges": edges,
        "activities": activities,
        "models": [ctx["graph"].models[k] for k in model_ids],
        "genes": [{"id": g, "symbol": ctx["labels"][g]} for g in sorted(displayed_genes)],
    }
