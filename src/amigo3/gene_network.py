"""A provenance-preserving gene projection of explicit GO-CAM causal assertions.

The simple undirected graph is a functional network, not a physical interactome.
Direction and context remain on the supporting activity-level assertions.
"""

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import duckdb

from amigo3.megamodels import ModelIndex, model_index
from amigo3.parse import normalize_id

# Explicit, versioned policy rather than accepting every predicate found in a
# causal_associations field (some source models put part_of/has_part there).
# https://geneontology.github.io/gocam-py/CausalPredicateEnum/
# https://github.com/geneontology/noctua/issues/1132
CAUSAL_PREDICATES = {
    "RO:0002211",
    "RO:0002212",
    "RO:0002213",  # regulation
    "RO:0002304",
    "RO:0002305",  # causal positive/negative effects
    "RO:0002407",
    "RO:0002409",  # indirect regulation
    "RO:0002411",
    "RO:0002412",
    "RO:0002413",
    "RO:0002418",
    "RO:0002578",
    "RO:0002629",
    "RO:0002630",  # direct regulation
    "RO:0004046",
    "RO:0004047",
    "RO:0012009",
    "RO:0012010",
    "RO:0019004",
}
# Only used for explicitly typed gene-product enablers absent from the catalog.
# No identifier/orthology inference or prefix-based mapping between providers.
GENE_PRODUCT_PREFIXES = {
    "UniProtKB",
    "HGNC",
    "NCBIGene",
    "ENSEMBL",
    "PomBase",
    "WB",
    "SGD",
    "MGI",
    "FB",
    "ZFIN",
    "RGD",
    "Xenbase",
    "RNAcentral",
    "AGI_LocusCode",
    "EcoCyc",
}
POLICY = {
    "version": "gocam-gene-projection-v1",
    "description": "Species-specific functional connectivity across recorded contexts; not physical PPI evidence.",
    "edges": "One undirected edge per distinct gene pair and exact model taxon; no self-loops.",
    "sources": "All loaded model statuses; explicit causal assertions only, no co-membership or inferred stitching.",
    "identity": "Unique same-taxon catalog identifier mapping, otherwise an explicitly typed source gene-product ID.",
    "complexes": "Complex-enabled and ambiguous activities are omitted, not expanded into gene pairs.",
    "predicates": sorted(CAUSAL_PREDICATES),
    "source_identifier_prefixes": sorted(GENE_PRODUCT_PREFIXES),
}


class GeneResolver:
    def __init__(self, genes):
        self.genes = {g["id"]: g for g in genes}
        self.aliases = defaultdict(set)
        for gene in self.genes.values():
            for alias in [gene["id"], *(gene.get("identifiers") or [])]:
                self.aliases[normalize_id(alias)].add(gene["id"])

    def resolve(self, activity):
        """Return (gene, omission reason); an uncertain mapping never fans out."""
        if not activity["taxon"]:
            return None, "missing_taxon"
        if activity["duplicate_id"]:
            return None, "duplicate_activity_id"
        enabler = activity["record"].get("enabled_by") or {}
        identifier = activity["gene"]
        if (
            enabler.get("type") == "EnabledByProteinComplexAssociation"
            or enabler.get("has_part")
            or identifier.startswith("ComplexPortal:")
        ):
            return None, "complex_enabler"
        if not identifier:
            return None, "missing_enabler"
        if identifier.startswith(("GO:", "CHEBI:")):
            return None, "non_gene_enabler"
        candidates = [self.genes[g] for g in sorted(self.aliases.get(identifier, ()))]
        matching = [g for g in candidates if normalize_id(g["taxon_id"]) == activity["taxon"]]
        if candidates and not matching:
            return None, "taxon_mismatch"
        if len(matching) > 1:
            return None, "ambiguous_gene_mapping"
        if matching:
            gene = matching[0]
            if gene.get("record_kind") == "complex" or "GO:0032991" in (
                gene.get("gene_product_type_ids") or []
            ):
                return None, "complex_enabler"
            return {
                "gene_id": gene["id"],
                "label": gene["symbol"],
                "resolution": "catalog",
                "has_go_annotations": bool(gene.get("annotation_count", 0)),
            }, None
        if (
            enabler.get("type") == "EnabledByGeneProductAssociation"
            and identifier.partition(":")[0] in GENE_PRODUCT_PREFIXES
            and identifier.partition(":")[2]
        ):
            return {
                "gene_id": identifier,
                "label": activity["label"],
                "resolution": "source_identifier",
                "has_go_annotations": False,
            }, None
        return None, "unresolved_enabler"


def component_sizes(adjacency):
    remaining = set(adjacency)
    sizes = []
    while remaining:
        seed = remaining.pop()
        pending, size = [seed], 0
        while pending:
            node = pending.pop()
            size += 1
            new = adjacency[node] & remaining
            remaining.difference_update(new)
            pending.extend(new)
        sizes.append(size)
    return sorted(sizes, reverse=True)


class GeneNetwork:
    def __init__(self, index: ModelIndex, genes):
        resolver = GeneResolver(genes)
        self.models = index.models
        self.activities, self.nodes, self.edges = {}, {}, {}
        self.omitted_assertions = []
        self.coverage = {}
        for taxon in sorted({m["taxon"] for m in index.models.values()}):
            models = [m for m in index.models.values() if m["taxon"] == taxon]
            self.coverage[taxon] = {
                "taxon": taxon,
                "taxon_label": models[0]["taxon_label"],
                "models": len(models),
                "model_statuses": dict(sorted(Counter(m["status"] for m in models).items())),
                "activities": 0,
                "projectable_activities": 0,
                "activity_omissions": Counter(),
                "enabler_resolutions": Counter(),
                "causal_assertions": 0,
                "projected_assertions": 0,
                "assertion_omissions": Counter(),
                "source_predicates": Counter(),
            }
        for key, activity in sorted(index.activities.items()):
            stats = self.coverage[activity["taxon"]]
            stats["activities"] += 1
            gene, reason = resolver.resolve(activity)
            self.activities[key] = {**activity, "projection": gene, "omission_reason": reason}
            if reason:
                stats["activity_omissions"][reason] += 1
                continue
            stats["projectable_activities"] += 1
            stats["enabler_resolutions"][gene["resolution"]] += 1
            node_id = activity["taxon"] + "|" + gene["gene_id"]
            self.activities[key]["node_id"] = node_id
            if node_id not in self.nodes:
                self.nodes[node_id] = {
                    "id": node_id,
                    "taxon": activity["taxon"],
                    **gene,
                    "activity_ids": [],
                    "degree": 0,
                }
            self.nodes[node_id]["activity_ids"].append(key)
        for model_id in sorted(index.models):
            for assertion in index.causal[model_id]:
                stats = self.coverage[index.models[model_id]["taxon"]]
                stats["causal_assertions"] += 1
                predicate = normalize_id(assertion["predicate"] or "")
                stats["source_predicates"][predicate] += 1
                source = self.activities[assertion["source"]]
                target = self.activities.get(assertion["target"])
                reason = (
                    "unsupported_predicate"
                    if predicate not in CAUSAL_PREDICATES
                    else "unresolved_target"
                    if assertion["external"] or target is None
                    else "unprojectable_endpoint"
                    if not source["projection"] or not target["projection"]
                    else "self_loop"
                    if source["node_id"] == target["node_id"]
                    else None
                )
                if reason:
                    stats["assertion_omissions"][reason] += 1
                    self.omitted_assertions.append({**assertion, "omission_reason": reason})
                    continue
                stats["projected_assertions"] += 1
                pair = tuple(sorted((source["node_id"], target["node_id"])))
                if pair not in self.edges:
                    self.edges[pair] = {
                        "source": pair[0],
                        "target": pair[1],
                        "taxon": source["taxon"],
                        "assertions": [],
                    }
                    self.nodes[pair[0]]["degree"] += 1
                    self.nodes[pair[1]]["degree"] += 1
                self.edges[pair]["assertions"].append(
                    {
                        **assertion,
                        "source_node": source["node_id"],
                        "target_node": target["node_id"],
                    }
                )
        for taxon, stats in self.coverage.items():
            nodes = [n for n in self.nodes.values() if n["taxon"] == taxon]
            adjacency = {n["id"]: set() for n in nodes}
            for edge in self.edges.values():
                if edge["taxon"] == taxon:
                    adjacency[edge["source"]].add(edge["target"])
                    adjacency[edge["target"]].add(edge["source"])
            sizes = component_sizes(adjacency)
            stats.update(
                {
                    "projectable_fraction": stats["projectable_activities"] / stats["activities"]
                    if stats["activities"]
                    else None,
                    "genes": len(nodes),
                    "edges": sum(n["degree"] for n in nodes) // 2,
                    "connected_genes": sum(n["degree"] > 0 for n in nodes),
                    "isolated_genes": sum(n["degree"] == 0 for n in nodes),
                    "catalog_genes": sum(n["resolution"] == "catalog" for n in nodes),
                    "source_identifier_genes": sum(n["resolution"] == "source_identifier" for n in nodes),
                    "annotated_genes": sum(n["has_go_annotations"] for n in nodes),
                    "components": len(sizes),
                    "component_sizes": sizes,
                    "largest_component": sizes[0] if sizes else 0,
                }
            )
            for key, value in list(stats.items()):
                if isinstance(value, Counter):
                    stats[key] = dict(sorted(value.items()))

    def result(self, taxon: str | None = None, summary: bool = False):
        if taxon is not None:
            taxon = normalize_id(taxon)
            if taxon not in self.coverage:
                raise ValueError("Taxon is not present in the loaded GO-CAM sources")
        coverage = [s for t, s in self.coverage.items() if taxon is None or t == taxon]
        result = {
            "policy": POLICY,
            "coverage": coverage,
            "totals": {
                key: sum(s[key] for s in coverage)
                for key in (
                    "models",
                    "activities",
                    "projectable_activities",
                    "causal_assertions",
                    "projected_assertions",
                    "genes",
                    "edges",
                    "connected_genes",
                    "isolated_genes",
                    "catalog_genes",
                    "source_identifier_genes",
                    "annotated_genes",
                    "components",
                )
            },
        }
        if not summary:
            selected = {m for m, v in self.models.items() if taxon is None or v["taxon"] == taxon}
            result.update(
                {
                    "models": [self.models[m] for m in sorted(selected)],
                    "activities": [a for a in self.activities.values() if a["model_id"] in selected],
                    "nodes": [
                        n for _, n in sorted(self.nodes.items()) if taxon is None or n["taxon"] == taxon
                    ],
                    "edges": [
                        e for _, e in sorted(self.edges.items()) if taxon is None or e["taxon"] == taxon
                    ],
                    "omitted_assertions": [a for a in self.omitted_assertions if a["model_id"] in selected],
                }
            )
        return result


@lru_cache(maxsize=2)
def gene_network(path: Path):
    """Read only the pinned integration snapshot, including its frozen sources."""
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if "knowledge_sources" not in tables:
            raise ValueError("This build has no GO-CAM sources; rebuild with --repository")
        columns = {r[0] for r in db.execute("DESCRIBE genes").fetchall()}
        fields = ["id", "symbol", "taxon_id", "identifiers", "record_kind", "annotation_count"]
        if "gene_product_type_ids" in columns:
            fields.append("gene_product_type_ids")
        genes = [
            dict(zip(fields, row, strict=True))
            for row in db.execute("SELECT " + ", ".join(fields) + " FROM genes").fetchall()
        ]
    return GeneNetwork(model_index(path), genes)
