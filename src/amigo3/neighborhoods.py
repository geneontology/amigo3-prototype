"""Overlapping, study-independent local neighborhoods on the reference graph."""

import hashlib
import json
from collections import Counter, defaultdict, deque
from functools import lru_cache
from itertools import combinations

import networkx as nx

POLICY = "gocam-local-pagerank-v1"
RESTART = 0.15
EPSILON = 1e-5
MAX_SIZE = 50
WHOLE_COMPONENT_THRESHOLD = 0.5


def personalized_pagerank(adjacency, seed, restart=RESTART, epsilon=EPSILON):
    """ACL residual push for p = restart*seed + (1-restart)*p*(I+D^-1 A)/2.

    Sorted adjacency and a FIFO queue fix floating-point/tie order. On exit,
    every residual/degree is at most epsilon. No study genes enter this step.
    """
    scores = defaultdict(float)
    residual = defaultdict(float, {seed: 1.0})
    queue, queued = deque([seed]), {seed}
    while queue:
        gene = queue.popleft()
        queued.remove(gene)
        mass = residual[gene]
        scores[gene] += restart * mass
        residual[gene] = (1 - restart) * mass / 2
        share = (1 - restart) * mass / (2 * len(adjacency[gene]))
        for neighbor in adjacency[gene]:
            residual[neighbor] += share
            if residual[neighbor] > epsilon * len(adjacency[neighbor]) and neighbor not in queued:
                queue.append(neighbor)
                queued.add(neighbor)
        if residual[gene] > epsilon * len(adjacency[gene]) and gene not in queued:
            queue.append(gene)
            queued.add(gene)
    return dict(scores)


def local_neighborhood(
    graph, adjacency, seed, component_volume, *, restart=RESTART, epsilon=EPSILON, max_size=MAX_SIZE
):
    """Minimum-conductance connected seed-containing sweep prefix, 2..50 genes.

    Conductance uses the seed's component, so unrelated components cannot alter
    its boundary. Whole components are a fallback only when no proper prefix
    has conductance below 0.5, avoiding the trivial zero-cut solution. Equal minima
    favor the smaller prefix. This is a bounded heuristic, not PageRank-Nibble's
    full theoretical procedure. Whole components are still subject to the cap.
    """
    scores = personalized_pagerank(adjacency, seed, restart, epsilon)
    order = sorted(scores, key=lambda g: (-scores[g] / len(adjacency[g]), g))
    members, volume, boundary = set(), 0, 0
    best, best_score = None, float("inf")
    for gene in order[:max_size]:
        boundary += len(adjacency[gene]) - 2 * sum(n in members for n in adjacency[gene])
        volume += len(adjacency[gene])
        members.add(gene)
        denominator = min(volume, component_volume - volume)
        if not denominator and best_score < WHOLE_COMPONENT_THRESHOLD:
            continue
        conductance = boundary / denominator if denominator else 0.0
        if len(members) < 2 or seed not in members or conductance >= best_score - 1e-12:
            continue
        if nx.is_connected(graph.subgraph(members)):
            best, best_score = tuple(sorted(members)), conductance
    # Very high degree graphs can leave only the seed in the approximate support.
    # A deterministic adjacent pair preserves coverage, with its true cut score.
    fallback = best is None
    if fallback:
        best = tuple(sorted((seed, adjacency[seed][0])))
        volume = sum(len(adjacency[g]) for g in best)
        boundary = volume - 2
        denominator = min(volume, component_volume - volume)
        best_score = boundary / denominator if denominator else 0.0
    return best, best_score, fallback


def communities(taxon, edges):
    """Legacy entry point; returns overlapping sets, not a disjoint partition."""
    pairs = tuple(sorted({tuple(sorted((a, b))) for a, b in edges if a != b}))
    return _communities(taxon, pairs)


@lru_cache(maxsize=8)
def _communities(taxon, pairs):
    graph = nx.Graph()
    graph.add_nodes_from(sorted({g for pair in pairs for g in pair}))
    graph.add_edges_from(pairs)
    adjacency = {g: tuple(sorted(graph[g])) for g in graph}
    volumes = {}
    for component in nx.connected_components(graph):
        volume = sum(len(adjacency[g]) for g in component)
        volumes.update(dict.fromkeys(component, volume))
    definitions = {}
    for seed in adjacency:
        members, conductance, fallback = local_neighborhood(graph, adjacency, seed, volumes[seed])
        definition = definitions.setdefault(
            members, {"seed_ids": [], "conductance": conductance, "fallback_seed_ids": []}
        )
        definition["seed_ids"].append(seed)
        if fallback:
            definition["fallback_seed_ids"].append(seed)
    groups = sorted(definitions, key=lambda gs: (-len(gs), gs))
    memberships = tuple(
        ("GN:" + hashlib.sha256(json.dumps([taxon, gs]).encode()).hexdigest()[:16], gs) for gs in groups
    )
    collection_id = hashlib.sha256(
        json.dumps([POLICY, RESTART, EPSILON, MAX_SIZE, WHOLE_COMPONENT_THRESHOLD, taxon, pairs]).encode()
    ).hexdigest()
    metadata = {
        "policy": POLICY,
        "algorithm": "Personalized PageRank with a bounded conductance sweep",
        "implementation": "networkx " + nx.__version__,
        "overlapping": True,
        "restart_probability": RESTART,
        "residual_tolerance": EPSILON,
        "max_neighborhood_size": MAX_SIZE,
        "min_neighborhood_size": 2,
        "walk": "lazy: half stay, half move to a uniformly chosen neighbor",
        "boundary_policy": "minimum conductance connected seed-containing prefix; component volume; smaller prefix breaks ties",
        "whole_component_policy": "only if no proper eligible prefix has conductance below threshold; still size capped",
        "whole_component_threshold": WHOLE_COMPONENT_THRESHOLD,
        "deduplication": "identical reference memberships only",
        "fallback_policy": "seed plus first neighbor if no eligible sweep prefix",
        "weighted": False,
        "collection_id": collection_id,
        "partition_id": collection_id,  # Compatibility alias; no longer a partition.
        "reference_nodes": graph.number_of_nodes(),
        "reference_edges": len(pairs),
        "communities": len(groups),
        "seed_count": len(adjacency),
        "fallback_seed_count": sum(len(d["fallback_seed_ids"]) for d in definitions.values()),
        "definitions": {ident: definitions[gs] for ident, gs in memberships},
        "scope": "Full nonisolated catalog-mapped taxon graph, before custom background and size filters",
    }
    return memberships, metadata


def describe(memberships, adjacency, labels, go_sets, go_labels, metadata=None):
    """Descriptive GO overlap only: never affects partitioning, tests or ranking."""
    descriptions = {}
    annotations = defaultdict(list)
    for term, genes in go_sets.items():
        for gene in genes:
            annotations[gene].append(term)
    for index, (ident, genes) in enumerate(memberships.items(), 1):
        hubs = sorted(genes, key=lambda g: (-len(adjacency[g] & genes), g))[:3]
        context = []
        overlaps = Counter(term for gene in genes for term in annotations[gene])
        for term, overlap in overlaps.items():
            if overlap < 2:
                continue
            annotated = go_sets[term]
            context.append(
                {
                    "id": term,
                    "label": go_labels.get(term) or term,
                    "members": overlap,
                    "network_annotated": len(annotated),
                    "jaccard": overlap / (len(genes) + len(annotated) - overlap),
                }
            )
        context.sort(key=lambda t: (-t["jaccard"], -t["members"], t["id"]))
        descriptions[ident] = {
            "label": f"Neighborhood {index:03d}",
            "representatives": [{"id": g, "symbol": labels[g] or g} for g in hubs],
            "reference_size": len(genes),
            "go_context": context[:3],
        }
        if metadata:
            definition = metadata["definitions"][ident]
            descriptions[ident].update(
                seeds=[{"id": g, "symbol": labels.get(g) or g} for g in definition["seed_ids"]],
                conductance=definition["conductance"],
            )
    return descriptions


def overview(ctx, terms, returned):
    by_gene = defaultdict(set)
    for ident, genes in ctx["communities"].items():
        for gene in genes & ctx["universe"]:
            by_gene[gene].add(ident)
    internal, shared = Counter(), Counter()
    for a, b, _ in ctx["edges"]:
        internal.update(by_gene[a] & by_gene[b])
    for neighborhoods in by_gene.values():
        shared.update(combinations(sorted(neighborhoods), 2))
    scores = {t["id"]: t for t in terms}
    nodes = []
    for ident, reference in ctx["communities"].items():
        members = reference & ctx["universe"]
        if not members:
            continue
        score = scores.get(ident)
        nodes.append(
            {
                "id": ident,
                **ctx["descriptions"][ident],
                "size": len(members),
                "study_count": len(members & ctx["used"]),
                "internal_edges": internal[ident],
                "tested": score is not None,
                "returned": ident in returned,
                **{k: score[k] if score else None for k in ("connections", "expected", "p_value", "by_fdr")},
            }
        )
    return {
        **{k: v for k, v in ctx["partition"].items() if k != "definitions"},
        "nodes": nodes,
        "edge_kind": "shared_genes",
        "edges": [
            {"source": a, "target": b, "shared_genes": count} for (a, b), count in sorted(shared.items())
        ],
        "go_context_policy": "Descriptive BP overlap (all evidence), ranked by Jaccard on reference graph; no GO significance test",
    }
