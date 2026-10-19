"""Inspectable cross-model candidates over a pinned GO-CAM corpus.

Inspired by gocam-mega-editor's six link criteria. Activities are never merged;
heuristic links are separate from the source model's causal assertions.
"""

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field

from amigo3.knowledge import parse_document
from amigo3.parse import normalize_id
from amigo3.query import Page, Query

CRITERIA = {
    "shared_gene": {
        "label": "Same enabler",
        "description": "Exact enabled_by CURIE; function and context may differ.",
    },
    "gene_mf": {
        "label": "Enabler + function",
        "description": "Same enabled_by and molecular function; context may differ.",
    },
    "full_activity_signature": {
        "label": "Full activity context",
        "description": "Same enabler, function, process and location; all four must be recorded.",
    },
    "terminal_to_initial": {
        "label": "Terminal → initial overlap",
        "description": "Full context match at the terminal and initial ends of recorded causal chains. A stitching candidate, not a new causal assertion.",
    },
    "shared_chemical": {
        "label": "Shared chemical",
        "description": "Same exact ChEBI term used by activities, irrespective of participant role.",
    },
    "chemical_flow": {
        "label": "Output → input",
        "description": "An output ChEBI term in one model matches an input in another. Candidate flow, not a curated connection.",
    },
}
Criterion = Literal[
    "shared_gene",
    "gene_mf",
    "full_activity_signature",
    "terminal_to_initial",
    "shared_chemical",
    "chemical_flow",
]
DEFAULT_CRITERIA = ["gene_mf", "full_activity_signature", "terminal_to_initial", "chemical_flow"]
# Deliberately explicit and inspectable; exact IDs, not an ontology-wide exclusion.
CURRENCY = {
    "CHEBI:15377",
    "CHEBI:15378",
    "CHEBI:15379",
    "CHEBI:16526",
    "CHEBI:18367",
    "CHEBI:24636",
    "CHEBI:29101",
    "CHEBI:29103",
    "CHEBI:29108",
    "CHEBI:30616",
    "CHEBI:456216",
    "CHEBI:456215",
    "CHEBI:57540",
    "CHEBI:57945",
    "CHEBI:58349",
    "CHEBI:57783",
}
MAX_ACTIVITIES = 500
MAX_LINKS = 2000


class LinkOptions(BaseModel):
    criteria: list[Criterion] = Field(default_factory=lambda: DEFAULT_CRITERIA.copy(), max_length=6)
    chemical_location: Literal["same", "compatible", "any"] = "same"
    include_currency: bool = False
    view: str | None = None


class MegamodelRequest(LinkOptions):
    model_ids: list[str] = Field(min_length=1, max_length=12)
    neighbor_limit: int = Field(default=40, ge=1, le=100)


class NetworkRequest(LinkOptions):
    query: Query = Field(default_factory=lambda: Query(collection="gocams"))


class ConnectionRequest(LinkOptions):
    model_ids: list[str] = Field(min_length=2, max_length=2)


def term(value):
    return normalize_id((value or {}).get("term") or "")


class ModelIndex:
    def __init__(self, sources):
        self._networks = {}
        self.models, self.activities = {}, {}
        self.features = defaultdict(lambda: defaultdict(set))
        self.keys = defaultdict(lambda: defaultdict(set))
        self.causal = defaultdict(list)
        for document, provenance in sources:
            model_id = document["id"]
            labels = {o["id"]: o.get("label") or o["id"] for o in document.get("objects") or []}
            taxon = normalize_id(document.get("taxon") or "")
            activities = document.get("activities") or []
            self.models[model_id] = {
                "id": model_id,
                "label": document.get("title") or model_id,
                "taxon": taxon,
                "taxon_label": labels.get(taxon, taxon or "Taxon not recorded"),
                "activity_count": len(activities),
                "status": document.get("status") or "",
                "source": provenance,
            }
            incoming, outgoing, unresolved = set(), set(), set()
            occurrences = Counter(a["id"] for a in activities)
            node_ids = [
                self.node_id(model_id, a["id"]) + (f"|row:{i}" if occurrences[a["id"]] > 1 else "")
                for i, a in enumerate(activities)
            ]
            targets = {a["id"]: node_ids[i] for i, a in enumerate(activities) if occurrences[a["id"]] == 1}
            self.models[model_id]["duplicate_activity_ids"] = sum(n > 1 for n in occurrences.values())
            for row, a in enumerate(activities):
                for n, edge in enumerate(a.get("causal_associations") or []):
                    target = edge.get("downstream_activity")
                    if target in targets:
                        incoming.add(targets[target])
                        outgoing.add(node_ids[row])
                    else:
                        unresolved.add(node_ids[row])
                    self.causal[model_id].append(
                        {
                            "id": f"causal:{node_ids[row]}:{n}",
                            "model_id": model_id,
                            "source": node_ids[row],
                            "target": targets.get(target, self.node_id(model_id, target)),
                            "predicate": edge.get("predicate"),
                            "assertion": edge,
                            "external": target not in targets,
                            "unresolved_reason": (
                                "ambiguous activity ID" if occurrences[target] > 1 else "outside model"
                            )
                            if target not in targets
                            else None,
                        }
                    )
            molecules = {m["id"]: m for m in document.get("molecules") or []}
            for row, a in enumerate(activities):
                key = node_ids[row]
                gene, mf, bp, cc = (
                    term(a.get(f)) for f in ("enabled_by", "molecular_function", "part_of", "occurs_in")
                )
                role = (
                    "unresolved"
                    if key in unresolved or occurrences[a["id"]] > 1
                    else "internal"
                    if key in incoming and key in outgoing
                    else "terminal"
                    if key in incoming
                    else "initial"
                    if key in outgoing
                    else "isolated"
                )
                ref = {
                    "id": key,
                    "activity_id": a["id"],
                    "source_row": row,
                    "duplicate_id": occurrences[a["id"]] > 1,
                    "model_id": model_id,
                    "taxon": taxon,
                    "gene": gene,
                    "function": mf,
                    "process": bp,
                    "location": cc,
                    "role": role,
                    "label": labels.get(gene, gene or a["id"]),
                    "function_label": labels.get(mf, mf),
                    "process_label": labels.get(bp, bp),
                    "location_label": labels.get(cc, cc),
                    "record": a,
                    "chemicals": [],
                }
                self.activities[key] = ref
                if not taxon:
                    continue  # Missing taxa do not establish cross-model compatibility.
                for kind, values in [
                    ("shared_gene", (gene,)),
                    ("gene_mf", (gene, mf)),
                    ("full_activity_signature", (gene, mf, bp, cc)),
                ]:
                    if all(values):
                        self.add(kind, (taxon, *values), key)
                for n, association in enumerate(a.get("molecular_associations") or []):
                    raw = association.get("molecule") or ""
                    molecule = molecules.get(raw, {})
                    chemical = normalize_id(raw) if raw.startswith("CHEBI:") else term(molecule)
                    if not chemical.startswith("CHEBI:"):
                        continue
                    # Prefer explicit molecule location, otherwise the activity's compartment.
                    location = term(molecule.get("located_in")) or term(molecule.get("occurs_in")) or cc
                    participant = {
                        "term": chemical,
                        "label": labels.get(chemical, chemical),
                        "location": location,
                        "predicate": association.get("predicate"),
                        "molecule_id": raw,
                        "association_index": n,
                    }
                    ref["chemicals"].append(participant)
                    self.add("shared_chemical", (taxon, chemical), key)
                    if association.get("predicate") == "RO:0002234":
                        self.add("output", (taxon, chemical), key)
                    elif association.get("predicate") == "RO:0002233":
                        self.add("input", (taxon, chemical), key)

    @staticmethod
    def node_id(model, activity):
        # A source activity can itself be reused in another model. Keep both instances.
        return model + "|" + str(activity)

    def add(self, kind, key, activity):
        self.features[kind][key].add(activity)
        self.keys[activity][kind].add(key)

    def options(self):
        return {
            "criteria": CRITERIA,
            "defaults": DEFAULT_CRITERIA,
            "currency_terms": sorted(CURRENCY),
            "max_activities": MAX_ACTIVITIES,
            "models": sorted(self.models.values(), key=lambda m: (m["label"].lower(), m["id"])),
        }

    def chemical_matches(self, left, right, chemical, kind, location_policy):
        outputs = [
            c
            for c in left["chemicals"]
            if c["term"] == chemical and (kind != "chemical_flow" or c["predicate"] == "RO:0002234")
        ]
        inputs = [
            c
            for c in right["chemicals"]
            if c["term"] == chemical and (kind != "chemical_flow" or c["predicate"] == "RO:0002233")
        ]
        matches = []
        for a in outputs:
            for b in inputs:
                x, y = a["location"], b["location"]
                context = "same" if x and x == y else "different" if x and y else "unknown"
                if location_policy == "same" and context != "same":
                    continue
                if location_policy == "compatible" and context == "different":
                    continue
                matches.append({"source": a, "target": b, "location_match": context})
        return matches

    def candidates(self, selected, request):
        seen = set()
        for left_id in sorted(self.activities):
            left = self.activities[left_id]
            if left["model_id"] not in selected:
                continue
            for kind in sorted(set(request.criteria)):
                lookups = [(kind, kind, False)]
                if kind == "terminal_to_initial":
                    lookups = [("full_activity_signature", "full_activity_signature", False)]
                elif kind == "chemical_flow":
                    lookups = [("output", "input", False), ("input", "output", True)]
                for own, other, reverse in lookups:
                    for key in sorted(self.keys[left_id][own]):
                        if "chemical" in kind and not request.include_currency and key[-1] in CURRENCY:
                            continue
                        for right_id in sorted(self.features[other][key]):
                            right = self.activities[right_id]
                            if left["model_id"] == right["model_id"]:
                                continue
                            a, b = (right, left) if reverse else (left, right)
                            directional = kind in ("chemical_flow", "terminal_to_initial")
                            if kind == "terminal_to_initial":
                                if a["role"] == "initial" and b["role"] == "terminal":
                                    a, b = b, a
                                if (a["role"], b["role"]) != ("terminal", "initial"):
                                    continue
                            if not directional and a["id"] > b["id"]:
                                a, b = b, a
                            identity = (kind, a["id"], b["id"], key)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            participants = []
                            if "chemical" in kind:
                                participants = self.chemical_matches(
                                    a, b, key[-1], kind, request.chemical_location
                                )
                                if not participants:
                                    continue
                            yield {
                                "source": a["id"],
                                "target": b["id"],
                                "source_model": a["model_id"],
                                "target_model": b["model_id"],
                                "source_activity": a["activity_id"],
                                "target_activity": b["activity_id"],
                                "source_label": a["label"],
                                "target_label": b["label"],
                                "criterion": kind,
                                "match": list(key[1:]),
                                "directed": directional,
                                "participants": participants,
                                "candidate": True,
                            }

    def explore(self, request):
        selected = list(dict.fromkeys(request.model_ids))
        missing = set(selected) - self.models.keys()
        if missing:
            raise ValueError("Models not in this snapshot: " + ", ".join(sorted(missing)))
        activities = [a for a in self.activities.values() if a["model_id"] in selected]
        if len(activities) > MAX_ACTIVITIES:
            raise ValueError(
                f"Selection has {len(activities)} activities; choose at most {MAX_ACTIVITIES} for this view."
            )
        neighbors, links, total_links = {}, [], 0
        for candidate in self.candidates(set(selected), request):
            other = (
                candidate["target_model"]
                if candidate["source_model"] in selected
                else candidate["source_model"]
            )
            if other in selected:
                total_links += 1
                if len(links) < MAX_LINKS:
                    links.append(candidate)
            else:
                n = neighbors.setdefault(
                    other, {"model": self.models[other], "criteria": Counter(), "anchors": []}
                )
                n["criteria"][candidate["criterion"]] += 1
                if len(n["anchors"]) < 5:
                    n["anchors"].append(candidate)
        neighbors = sorted(
            neighbors.values(),
            key=lambda n: (-len(n["criteria"]), -sum(n["criteria"].values()), n["model"]["id"]),
        )
        causal = [e for m in selected for e in self.causal[m]]
        return {
            "models": [self.models[m] for m in selected],
            "activities": activities,
            "causal_edges": [e for e in causal if not e["external"]],
            "external_causal_edges": [e for e in causal if e["external"]],
            "links": links,
            "link_count": total_links,
            "links_truncated": total_links > len(links),
            "neighbors": neighbors[: request.neighbor_limit],
            "neighbor_count": len(neighbors),
            "parameters": request.model_dump(exclude={"view"}),
            "policy": "Exact CURIE matches within the same recorded taxon. Candidate links do not assert causality or activity identity. Original activities and evidence remain distinct.",
        }

    def network_edges(self, criteria, chemical_location, include_currency):
        """Aggregate every match, with no activity or edge cap on the collection view."""
        cache_key = (criteria, chemical_location, include_currency)
        if cache_key in self._networks:
            return self._networks[cache_key]
        request = LinkOptions(
            criteria=list(criteria), chemical_location=chemical_location, include_currency=include_currency
        )
        edges = {}
        for match in self.candidates(set(self.models), request):
            pair = tuple(sorted((match["source_model"], match["target_model"])))
            edge = edges.setdefault(
                pair,
                {
                    "id": "network:" + "|".join(pair),
                    "source": pair[0],
                    "target": pair[1],
                    "criteria": Counter(),
                    "match_count": 0,
                },
            )
            edge["criteria"][match["criterion"]] += 1
            edge["match_count"] += 1
        if len(self._networks) >= 3:
            self._networks.pop(next(iter(self._networks)))
        self._networks[cache_key] = list(edges.values())
        return self._networks[cache_key]

    def connection(self, request):
        selected = set(request.model_ids)
        if len(selected) != 2 or not selected <= self.models.keys():
            raise ValueError("Choose two distinct models present in this snapshot")
        anchors, counts = [], Counter()
        for match in self.candidates(selected, request):
            if match["source_model"] in selected and match["target_model"] in selected:
                counts[match["criterion"]] += 1
                if len(anchors) < 100:
                    anchors.append(match)
        return {
            "models": [self.models[m] for m in request.model_ids],
            "anchors": anchors,
            "criteria": counts,
            "match_count": sum(counts.values()),
            "truncated": sum(counts.values()) > len(anchors),
        }


def network(index, engine, request):
    """Use the list's own backend/search semantics, consuming every result page."""
    if request.query.collection != "gocams":
        raise ValueError("The model network requires the gocams collection")
    query = request.query.model_copy(
        update={
            "select": ["id", "label", "taxon_ids", "status", "has_review", "activity_count"],
            "page": Page(size=100),
        }
    )
    rows, facets, total = [], {}, 0
    while True:
        result = engine.query(query)
        if not rows:
            facets, total = result["facets"], result["total"]
        rows.extend(result["hits"])
        if not result["next_cursor"]:
            break
        query = query.model_copy(update={"page": Page(size=100, cursor=result["next_cursor"])})
    selected = {row["id"] for row in rows}
    for row in rows:
        row["primary_taxon"] = index.models.get(row["id"], {}).get("taxon", "")
    edges = [
        e
        for e in index.network_edges(
            tuple(sorted(set(request.criteria))), request.chemical_location, request.include_currency
        )
        if e["source"] in selected and e["target"] in selected
    ]
    connected = {m for e in edges for m in (e["source"], e["target"])}
    return {
        "models": rows,
        "edges": edges,
        "total": total,
        "facets": facets,
        "isolated_count": len(selected - connected),
        "edge_count": len(edges),
        "match_count": sum(e["match_count"] for e in edges),
        "missing_sources": sorted(selected - index.models.keys()),
        "parameters": request.model_dump(exclude={"view"}),
    }


@lru_cache(maxsize=2)
def model_index(path: Path):
    """Cache only immutable build paths; never read the mutable source repository."""
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        rows = db.execute(
            "SELECT path, sha256, raw_text FROM knowledge_sources WHERE collection='gocams' AND role='model' ORDER BY record_id"
        ).fetchall()
    return ModelIndex((parse_document(p, raw), {"path": p, "sha256": sha}) for p, sha, raw in rows)
