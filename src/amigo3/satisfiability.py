"""Module satisfiability: can a species' annotations realize a module, and what fills its gaps?

A module is a recursive and/or tree. A node needs all of its annotons and all of its
non-optional parts; each variant set needs at least one satisfiable variant
(`ZERO_OR_MORE` sets are optional). Leaf annotons are scored against one species:

* **Satisfied** when a gene of that species has a positive annotation to the
  annoton's required molecular function (or an is_a/part_of descendant), or when a
  curated participant/representative member named by the module resolves to a gene
  of that species. The best evidence tier is reported, so a step held up only by
  IEA rows is visibly weaker than one with experimental support.
* **Gap** otherwise. Gap filling then proposes candidates, ranked by the kind of
  support, never applied as annotations:

  - *broader term*: the gene is annotated to an ancestor of the required function
    (a less specific statement of the same activity);
  - *family member*: the gene is a cached member of the annoton's PANTHER family,
    with a subfamily match ranked above a family-only match;
  - *related activity*: the gene is annotated to a different, more specific
    function under the same close ancestor (often a paralog with another substrate);
  - *pathway context*: the gene is annotated to the module's process concepts but
    to none of the module's satisfied steps.

A NOT annotation to the required function, or to one of its is_a ancestors, removes
a gene from that step: it neither satisfies the step nor is proposed for it. A NOT
annotation to a more specific function does not, since the gene may have another
activity of the same kind. part_of is not followed for negation. Annotons with no molecular function and no concrete participant cannot
be scored and do not count against their parent.

A gene already filling another step whose only support is a related activity scores 0
and is not proposed: a sibling under a shared parent says nothing about this step.

**Not expected** replaces gap filling for an unsatisfied step outside its taxon scope:

* a node's context taxa (or the participant's taxon) name only clades that do not
  contain the species. A species or strain name (a binomial) is read as where the
  step was curated, not as a limit, so it never excludes;
* or the step lies in an alternative of a variant set whose axis is taxon, and that
  alternative's curated participants resolve only to other loaded species, or its
  cached PANTHER families have no member in this species.

Not expected steps do not count against their parent; a node whose every scorable
requirement is not expected is not expected itself. Direct evidence still wins: a
satisfied step stays satisfied whatever its scope.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from pathlib import Path

import duckdb
import yaml
from pydantic import BaseModel

from amigo3.enrichment import EVIDENCE, EXPERIMENTAL, EvidencePreset, evidence_clause
from amigo3.hierarchies import lineage
from amigo3.parse import identifier_variants, normalize_id

ROOTS = {"GO:0003674", "GO:0008150", "GO:0005575", "GO:0003824", "GO:0005488"}
TIERS = {4: "experimental", 3: "curated participant", 2: "curated computational", 1: "electronic (IEA)"}
STATUS_RANK = {"not_expected": -1, "missing": 0, "unscorable": 1, "gap": 2, "satisfied": 3}
IGNORED = {"unscorable", "not_expected"}
BASIS_WEIGHT = {
    "broader_term": 3,
    "subfamily_member": 3,
    "family_member": 2,
    "related_activity": 1,
    "pathway_context": 2,
}
MAX_ANCESTOR_DEPTH = 3
MAX_ANCESTOR_GENES = 40
MAX_CANDIDATES = 8
TAXON_AXIS = re.compile(r"\btaxon", re.IGNORECASE)
# NCBI names at species rank and below are binomials: "Escherichia coli K-12", not "Bacteria".
BINOMIAL = re.compile(r"^[A-Z][a-z]+ [a-z]")


class SatisfiabilityRequest(BaseModel):
    module: str
    species: str
    evidence: EvidencePreset = "all"
    view: str | None = None


def fetch(cursor) -> list[tuple]:
    """All rows via Arrow; DuckDB's fetchall() probes for pandas on every row."""
    table = cursor.to_arrow_table()
    return list(zip(*(column.to_pylist() for column in table.columns), strict=True)) if table.num_rows else []


def tier(evidence_code: str) -> int:
    if evidence_code in EXPERIMENTAL:
        return 4
    return 1 if evidence_code == "IEA" else 2


def descriptor_id(value) -> str:
    term = (value or {}).get("term") if isinstance(value, dict) else None
    return normalize_id(term["id"]) if isinstance(term, dict) and term.get("id") else ""


def required_function(annoton: dict) -> tuple[str, str]:
    participant = annoton.get("participant") or {}
    for holder in (participant.get("required_function"), annoton.get("function")):
        identifier = descriptor_id(holder)
        if identifier.startswith("GO:"):
            label = (holder.get("term") or {}).get("label") or holder.get("preferred_term") or identifier
            return identifier, label
    return "", ""


def named_participants(
    annoton: dict, keys=("gene", "gene_product", "protein_complex", "ortholog_of")
) -> list[str]:
    """Concrete identifiers the curator attached to this role (not family-wide)."""
    participant = annoton.get("participant") or {}
    ids = []
    for key in keys:
        identifier = descriptor_id(participant.get(key))
        if identifier:
            ids.append(identifier)
    for key in ("family", "domain"):
        for member in (participant.get(key) or {}).get("representative_members") or []:
            identifier = descriptor_id(member)
            if identifier:
                ids.append(identifier)
    return ids


def family_ids(annoton: dict) -> list[str]:
    identifier = descriptor_id((annoton.get("participant") or {}).get("family"))
    return [identifier] if identifier.startswith("PANTHER:PTHR") else []


def collect(node: dict, path: tuple[str, ...] = (), frames: tuple = (), axis: str = ""):
    """Yield (annoton, path, frames) for every annoton in the module tree.

    frames are the enclosing nodes, outermost first, each with the axis of the
    variant set it is an alternative in ("" for the root and for parts).
    """
    path, frames = (*path, node.get("id", "")), (*frames, (node, axis))
    for annoton in node.get("annotons") or []:
        yield annoton, path, frames
    for part in node.get("parts") or []:
        yield from collect(part.get("node") or {}, path, frames)
    for variant_set in node.get("variant_sets") or []:
        for variant in variant_set.get("variants") or []:
            yield from collect(variant, path, frames, variant_set.get("axis") or "")


def outside_clades(taxa: list[dict], species_lineage: set[str]) -> list[dict]:
    """The scope taxa when they are clades that all exclude the species, else []."""
    scope = []
    for taxon in taxa:
        identifier = descriptor_id(taxon)
        label = (taxon.get("term") or {}).get("label") or taxon.get("preferred_term") or ""
        if not identifier.startswith("NCBITaxon:") or identifier in species_lineage:
            return []
        # Unlabelled taxa and single organisms say where a step was studied, not where it stops.
        if not label or BINOMIAL.match(label):
            return []
        scope.append({"id": identifier, "label": label})
    return scope


def process_concepts(module: dict) -> list[str]:
    ids = set()

    def visit(node):
        for concept in node.get("concepts") or []:
            identifier = descriptor_id(concept)
            if identifier.startswith("GO:"):
                ids.add(identifier)
        for annoton in node.get("annotons") or []:
            for process in annoton.get("processes") or []:
                identifier = descriptor_id(process)
                if identifier.startswith("GO:"):
                    ids.add(identifier)
        for part in node.get("parts") or []:
            visit(part.get("node") or {})
        for variant_set in node.get("variant_sets") or []:
            for variant in variant_set.get("variants") or []:
                visit(variant)

    visit(module)
    return sorted(ids)


class Context:
    """Species-scoped annotation lookups over the pinned integration snapshot."""

    def __init__(self, db, species: str, evidence: str):
        self.db, self.species = db, species
        self.evidence_sql, self.evidence_args = evidence_clause(evidence)

    def rows(self, terms: list[str], aspect: str | None = None) -> list[tuple]:
        """Positive (gene, symbol, annotated term, label, evidence, annotation id, query term) matches.

        Descendants are expanded in SQL: binding long parameter lists is slow in DuckDB.
        """
        if not terms:
            return []
        aspect_sql = "AND a.aspect=?" if aspect else ""
        evidence_sql = self.evidence_sql.replace("evidence_code", "a.evidence_code")
        return fetch(
            self.db.execute(
                f"""WITH q AS (SELECT unnest(?::VARCHAR[]) AS term),
                expanded AS (
                  SELECT t.id, q.term FROM terms t JOIN q ON list_contains(t.isa_partof_closure, q.term)
                  UNION SELECT term, term FROM q)
                SELECT a.gene_id, a.gene_symbol, a.ontology_class_id, a.term_label, a.evidence_code, a.id, e.term
                FROM annotations a JOIN expanded e ON a.ontology_class_id=e.id
                WHERE a.species=? AND NOT a.negation {aspect_sql} {evidence_sql}""",
                [terms, self.species, *([aspect] if aspect else []), *self.evidence_args],
            )
        )

    def hits(self, terms: list[str], aspect: str | None = None) -> dict[str, dict[str, dict]]:
        """term -> gene -> {symbol, tier, evidence, annotated terms} via the reflexive closure."""
        result = defaultdict(dict)
        for gene, symbol, annotated, label, code, annotation_id, term in self.rows(terms, aspect):
            entry = result[term].setdefault(
                gene, {"gene_id": gene, "symbol": symbol, "tier": 0, "evidence": set(), "terms": {}}
            )
            entry["tier"] = max(entry["tier"], tier(code))
            entry["evidence"].add(code)
            entry["terms"].setdefault(
                annotated, {"id": annotated, "label": label, "annotation_id": annotation_id}
            )
        return result

    def negated(self, terms: list[str]) -> dict[str, set[str]]:
        """term -> genes with a NOT annotation to that term or one of its is_a ancestors.

        Negation runs opposite to positive propagation: NOT acyltransferase rules out
        every acetyltransferase, but NOT one acetyltransferase leaves acyltransferase
        open. part_of is not followed: lacking a whole does not rule out having a part.
        All evidence codes count, whatever the positive evidence preset.
        """
        result = defaultdict(set)
        if not terms:
            return result
        for gene, term in fetch(
            self.db.execute(
                """WITH q AS (SELECT unnest(?::VARCHAR[]) AS term),
                ancestors AS (
                  SELECT unnest(t.is_a_closure) AS id, q.term FROM terms t JOIN q ON t.id=q.term
                  UNION SELECT term, term FROM q)
                SELECT DISTINCT a.gene_id, n.term FROM annotations a JOIN ancestors n ON a.ontology_class_id=n.id
                WHERE a.species=? AND a.negation""",
                [terms, self.species],
            )
        ):
            result[term].add(gene)
        return result

    def resolve(self, identifiers: list[str], any_species: bool = False) -> dict[str, list[dict]]:
        """identifier -> genes of this species (or of any loaded species) it names."""
        variants = {v: i for i in identifiers for v in identifier_variants(i)}
        result = defaultdict(list)
        if not variants:
            return result
        species_sql = "" if any_species else "species=? AND"
        rows = fetch(
            self.db.execute(
                f"""SELECT id, symbol, species, list_intersect(identifiers, ?::VARCHAR[]) FROM genes
            WHERE {species_sql} (id IN (SELECT unnest(?::VARCHAR[])) OR list_has_any(identifiers, ?::VARCHAR[]))""",
                [list(variants), *([] if any_species else [self.species]), list(variants), list(variants)],
            )
        )
        for gene, symbol, species, matched in rows:
            for alias in matched or [gene]:
                source = variants.get(alias) or variants.get(gene)
                if source and all(g["gene_id"] != gene for g in result[source]):
                    entry = {"gene_id": gene, "symbol": symbol}
                    result[source].append({**entry, "species": species} if any_species else entry)
        return result

    def taxa(self) -> set[str]:
        rows = fetch(self.db.execute("SELECT DISTINCT taxon_id FROM genes WHERE species=?", [self.species]))
        return {r[0].split(":")[-1] for r in rows if r[0]}

    def ancestors(self, term: str) -> list[tuple[str, str, int]]:
        """Nearest is_a ancestors (id, label, distance), skipping ontology roots."""
        seen, frontier, result = {term}, [term], []
        for distance in range(1, MAX_ANCESTOR_DEPTH + 1):
            if not frontier:
                break
            rows = fetch(
                self.db.execute(
                    """SELECT DISTINCT e.object, coalesce(t.label, e.object) FROM ontology_edges e
                LEFT JOIN terms t ON t.id=e.object
                WHERE e.subject IN (SELECT unnest(?::VARCHAR[])) AND e.predicate='is_a'""",
                    [frontier],
                )
            )
            frontier = []
            for parent, label in rows:
                if parent in seen or parent in ROOTS:
                    continue
                seen.add(parent)
                frontier.append(parent)
                result.append((parent, label, distance))
        return result

    def is_a_closure(self, term: str) -> set[str]:
        row = self.db.execute("SELECT is_a_closure FROM terms WHERE id=?", [term]).fetchone()
        return set(row[0]) if row else {term}


def family_members(db, families: list[str], taxa: set[str]) -> tuple[dict[str, list[dict]], set[str]]:
    """Cached PANTHER inventory members of the species' taxa per family id, and the cached family ids."""
    result, cached = defaultdict(list), set()
    for family in families:
        root = ":".join(family.split(":")[:2])
        row = db.execute(
            "SELECT raw_text FROM knowledge_sources WHERE collection='families' AND record_id=? AND role='entries'",
            [root],
        ).fetchone()
        if not row:
            continue
        cached.add(family)
        for member in csv.DictReader(io.StringIO(row[0])):
            if (member.get("source_tax_id") or "") not in taxa:
                continue
            accession = member["id"] if ":" in member["id"] else "UniProtKB:" + member["id"]
            subfamily = "PANTHER:" + (member.get("subfamily") or "").removeprefix("PANTHER:")
            result[family].append(
                {
                    "id": accession,
                    "subfamily": subfamily if member.get("subfamily") else "",
                    "subfamily_match": family.count(":") == 2 and subfamily == family,
                }
            )
    return result, cached


def short(term: dict) -> dict:
    return {"id": term["id"], "label": term["label"]}


def first_term(gene: dict) -> dict:
    return next(iter(gene["terms"].values()))


class Candidates:
    """Gap-filling candidates for one step; a gene's bases of support accumulate."""

    def __init__(self, blocked: set[str], assigned: set[str]):
        self.blocked, self.assigned, self.items = blocked, assigned, {}

    def add(self, gene: dict, kind: str, detail: dict):
        if gene["gene_id"] in self.blocked:
            return
        candidate = self.items.setdefault(
            gene["gene_id"],
            {
                "gene_id": gene["gene_id"],
                "symbol": gene["symbol"],
                "basis": [],
                "score": 0,
                "tier": 0,
                "assigned_elsewhere": gene["gene_id"] in self.assigned,
            },
        )
        if kind == "broader_term":
            # One term can be reached along several is_a paths; keep its nearest distance.
            same = [
                b for b in candidate["basis"] if b["kind"] == kind and b["detail"]["term"] == detail["term"]
            ]
            if same:
                same[0]["detail"]["distance"] = min(same[0]["detail"]["distance"], detail["distance"])
                return
        if {"kind": kind, "detail": detail} not in candidate["basis"]:
            candidate["basis"].append({"kind": kind, "detail": detail})
        candidate["tier"] = max(candidate["tier"], gene.get("tier", 0))

    def ranked(self) -> tuple[list[dict], int]:
        for candidate in self.items.values():
            kinds = {b["kind"] for b in candidate["basis"]}
            if "related_activity" in kinds:
                # Its broad annotation most likely restates the different activity it already has.
                candidate["basis"] = [b for b in candidate["basis"] if b["kind"] != "broader_term"]
                kinds.discard("broader_term")
            score = sum(BASIS_WEIGHT[k] for k in kinds - {"broader_term"})
            # A nearer ancestor is a more specific statement of the same activity.
            distances = [b["detail"]["distance"] for b in candidate["basis"] if b["kind"] == "broader_term"]
            if distances:
                score += BASIS_WEIGHT["broader_term"] + 1 - min(distances)
            # A gene already filling another step is a weaker guess for this one.
            candidate["score"] = score - candidate["assigned_elsewhere"]
            candidate["tier_label"] = TIERS.get(candidate["tier"], "")
        # A gene filling another step, related only by a sibling activity, is noise: drop it.
        ranked = sorted(
            (c for c in self.items.values() if c["score"] > 0),
            key=lambda c: (-c["score"], -c["tier"], c["symbol"] or c["gene_id"]),
        )
        # A lone related-activity hint is weak; keep such hints only when nothing better exists.
        strong = [c for c in ranked if c["score"] >= 2]
        return (strong or ranked)[:MAX_CANDIDATES], len(ranked)


def plain(gene: dict) -> dict:
    return {
        "gene_id": gene["gene_id"],
        "symbol": gene["symbol"],
        "tier": gene["tier"],
        "tier_label": TIERS.get(gene["tier"], ""),
        "evidence": sorted(gene.get("evidence") or []),
        "terms": list((gene.get("terms") or {}).values())[:5],
    }


def evaluate(db, document: dict, species: str, evidence: str = "all") -> dict:
    module = document["module"]
    context = Context(db, species, evidence)
    entries = list(collect(module))
    required = {a["id"]: required_function(a) for a, _, _ in entries}
    function_terms = sorted({t for t, _ in required.values() if t})
    function_hits = context.hits(function_terms, "F")
    negations = context.negated(function_terms)
    named = {a["id"]: named_participants(a) for a, _, _ in entries}
    resolved = context.resolve(sorted({i for ids in named.values() for i in ids}))
    taxa = context.taxa()
    families, cached_families = family_members(
        db, sorted({f for a, _, _ in entries for f in family_ids(a)}), taxa
    )
    member_genes = context.resolve(sorted({m["id"] for ms in families.values() for m in ms}))
    concepts = process_concepts(module)
    concept_hits = context.hits(concepts, "P")
    concept_labels = dict(
        fetch(db.execute("SELECT id, label FROM terms WHERE id IN (SELECT unnest(?::VARCHAR[]))", [concepts]))
    )

    # First pass: direct satisfaction for every annoton.
    scored = {}
    for annoton, path, _ in entries:
        term, label = required[annoton["id"]]
        genes = {g: dict(v) for g, v in function_hits.get(term, {}).items()} if term else {}
        for identifier in named[annoton["id"]]:
            for gene in resolved.get(identifier, []):
                entry = genes.setdefault(
                    gene["gene_id"], {**gene, "tier": 0, "evidence": set(), "terms": {}, "named_as": []}
                )
                entry["tier"] = max(entry["tier"], 3)
                entry.setdefault("named_as", []).append(identifier)
        for gene in negations.get(term, ()):
            genes.pop(gene, None)
        scorable = bool(term or named[annoton["id"]])
        status = "satisfied" if genes else ("missing" if scorable else "unscorable")
        scored[annoton["id"]] = {
            "id": annoton["id"],
            "label": annoton.get("label") or annoton["id"],
            "path": list(path),
            "function": {"id": term, "label": label} if term else None,
            "named_participants": named[annoton["id"]],
            "families": family_ids(annoton),
            "status": status,
            "tier": max((g["tier"] for g in genes.values()), default=0),
            "genes": sorted(
                ({**plain(g), "named_as": g.get("named_as", [])} for g in genes.values()),
                key=lambda g: (-g["tier"], g["symbol"] or g["gene_id"]),
            ),
            "candidates": [],
        }
    assigned = {g["gene_id"] for s in scored.values() for g in s["genes"]}

    # Taxon scope: an unsatisfied step outside it is not expected here, so it gets no candidates.
    lineages = [lineage(db, f"NCBITaxon:{t}") for t in taxa]
    species_lineage = set().union(*(x for x in lineages if x)) if any(lineages) else set()
    variant_scopes = {}

    def lineage_variant(variant: dict, axis: str):
        """Why an alternative on a taxon axis belongs to other species, or None."""
        key = id(variant)
        if key not in variant_scopes:
            members = [a for a, _, _ in collect(variant)]
            ids = sorted(
                {
                    i
                    for a in members
                    for i in named_participants(a, ("gene", "gene_product", "protein_complex"))
                }
            )
            others = {
                g["species"] for genes in context.resolve(ids, any_species=True).values() for g in genes
            }
            fams = {f for a in members for f in family_ids(a) if f in cached_families}
            absent = sorted(f for f in fams if not families.get(f))
            here = species in others or len(absent) < len(fams)
            variant_scopes[key] = (
                None
                if here or not (others or absent)
                else {
                    "kind": "lineage_variant",
                    "node": variant.get("label") or variant.get("id"),
                    "axis": axis,
                    "species": sorted(others),
                    "families": absent,
                }
            )
        return variant_scopes[key]

    def taxon_scope(annoton: dict, frames: tuple) -> dict | None:
        if species_lineage:
            holders = [
                (n.get("label") or n.get("id"), (n.get("context") or {}).get("taxa")) for n, _ in frames
            ]
            holders.append(
                (annoton.get("label") or annoton["id"], [(annoton.get("participant") or {}).get("taxon")])
            )
            for node_label, node_taxa in holders:
                outside = outside_clades([t for t in node_taxa or [] if t], species_lineage)
                if outside:
                    return {"kind": "taxon", "node": node_label, "taxa": outside}
        for variant, axis in reversed(frames):
            if TAXON_AXIS.search(axis):
                return lineage_variant(variant, axis)
        return None

    for annoton, _, frames in entries:
        result = scored[annoton["id"]]
        if result["status"] != "satisfied":
            scope = taxon_scope(annoton, frames)
            if scope:
                result["status"], result["scope"] = "not_expected", scope

    # Second pass: gap filling for unsatisfied, scorable annotons. One scan covers every gap's ancestors.
    gap_terms = {s["function"]["id"] for s in scored.values() if s["status"] == "missing" and s["function"]}
    gap_ancestors = {t: context.ancestors(t) for t in gap_terms}
    ancestor_hits = context.hits(
        sorted({a for ancestors in gap_ancestors.values() for a, _, _ in ancestors}), "F"
    )
    for annoton, _, _ in entries:
        result = scored[annoton["id"]]
        if result["status"] != "missing":
            continue
        term = result["function"]["id"] if result["function"] else ""
        candidates = Candidates(negations.get(term, set()), assigned)
        if term:
            closure = context.is_a_closure(term)
            for ancestor, label, distance in gap_ancestors[term]:
                hits = ancestor_hits.get(ancestor, {}).values()
                broader = [(g, [t for t in g["terms"].values() if t["id"] in closure]) for g in hits]
                broader = [(g, terms) for g, terms in broader if terms]
                # Very general ancestors annotate too many genes to discriminate.
                if len(broader) <= MAX_ANCESTOR_GENES:
                    for gene, terms in broader:
                        candidates.add(gene, "broader_term", {"term": short(terms[0]), "distance": distance})
                if distance != 1 or len(hits) > MAX_ANCESTOR_GENES:
                    continue
                for gene in hits:
                    if not any(t["id"] in closure for t in gene["terms"].values()):
                        detail = {
                            "ancestor": {"id": ancestor, "label": label},
                            "term": short(first_term(gene)),
                        }
                        candidates.add(gene, "related_activity", detail)
        for family in result["families"]:
            for member in families.get(family, []):
                kind = "subfamily_member" if member["subfamily_match"] else "family_member"
                detail = {"family": family, "member": member["id"], "subfamily": member["subfamily"]}
                for gene in member_genes.get(member["id"], []):
                    candidates.add({**gene, "tier": 0}, kind, detail)
        for concept in concepts:
            for gene in concept_hits.get(concept, {}).values():
                if gene["gene_id"] not in assigned:
                    detail = {
                        "term": {"id": concept, "label": concept_labels.get(concept, concept)},
                        "annotated": short(first_term(gene)),
                    }
                    candidates.add(gene, "pathway_context", detail)
        result["candidates"], result["candidate_total"] = candidates.ranked()
        if result["candidates"]:
            result["status"] = "gap"

    def node_result(node: dict, role: str = "", optional: bool = False) -> dict:
        children, requirement_statuses = [], []
        for annoton in node.get("annotons") or []:
            requirement_statuses.append((scored[annoton["id"]]["status"], scored[annoton["id"]]["tier"]))
        for part in node.get("parts") or []:
            child = node_result(part.get("node") or {}, part.get("role") or "", bool(part.get("optional")))
            children.append(child)
            if not child["optional"]:
                requirement_statuses.append((child["status"], child["tier"]))
        variant_sets = []
        for variant_set in node.get("variant_sets") or []:
            variants = [node_result(v) for v in variant_set.get("variants") or []]
            best = max(variants, key=lambda v: (STATUS_RANK[v["status"]], v["tier"]), default=None)
            optional_set = variant_set.get("selection") == "ZERO_OR_MORE"
            status = best["status"] if best else "unscorable"
            variant_sets.append(
                {
                    "id": variant_set.get("id"),
                    "label": variant_set.get("label") or variant_set.get("id"),
                    "selection": variant_set.get("selection") or "",
                    "status": status,
                    "tier": best["tier"] if best else 0,
                    "optional": optional_set,
                    "variants": variants,
                }
            )
            if not optional_set:
                requirement_statuses.append((status, best["tier"] if best else 0))
        scorable = [s for s in requirement_statuses if s[0] not in IGNORED]
        if not scorable:
            outside = any(s == "not_expected" for s, _ in requirement_statuses)
            status, node_tier = ("not_expected" if outside else "unscorable"), 0
        else:
            status = min((s for s, _ in scorable), key=STATUS_RANK.__getitem__)
            satisfied = [t for s, t in scorable if s == "satisfied"]
            node_tier = min(satisfied) if status == "satisfied" else 0
        return {
            "id": node.get("id"),
            "label": node.get("label") or node.get("id"),
            "module_type": node.get("module_type") or "",
            "role": role,
            "optional": optional,
            "status": status,
            "tier": node_tier,
            "annotons": [scored[a["id"]] for a in node.get("annotons") or []],
            "parts": children,
            "variant_sets": variant_sets,
        }

    def mark_needed(node: dict, needed: bool):
        """A step blocks the module unless it is optional or an alternative to a satisfied variant."""
        for annoton in node["annotons"]:
            annoton["needed"] = needed and annoton["status"] != "not_expected"
        for part in node["parts"]:
            mark_needed(part, needed and not part["optional"])
        for variant_set in node["variant_sets"]:
            covered = variant_set["status"] == "satisfied"
            for variant in variant_set["variants"]:
                mark_needed(variant, needed and not variant_set["optional"] and not covered)

    tree = node_result(module)
    mark_needed(tree, True)
    steps = list(scored.values())
    counts = defaultdict(int)
    for step in steps:
        counts[step["status"]] += 1
    proposals = [
        {
            "annoton_id": step["id"],
            "annoton_label": step["label"],
            "gene_id": c["gene_id"],
            "symbol": c["symbol"],
            "relation": "enables",
            "term": step["function"],
            "score": c["score"],
            "basis": c["basis"],
            "needed": step["needed"],
        }
        for step in steps
        if step["status"] == "gap" and step["function"]
        for c in step["candidates"]
    ]
    proposals.sort(key=lambda p: not p["needed"])
    return {
        "species": species,
        "evidence": evidence,
        "evidence_label": EVIDENCE[evidence],
        "status": tree["status"],
        "tier": tree["tier"],
        "tier_label": TIERS.get(tree["tier"], ""),
        "counts": dict(counts),
        "tree": tree,
        "proposals": proposals,
        "concepts": [{"id": c, "label": concept_labels.get(c, c)} for c in concepts],
        "method": __doc__.strip(),
    }


def default_species(db, record: dict) -> str:
    """The loaded species with the most module-referenced genes, if any."""
    row = db.execute(
        """SELECT species FROM genes WHERE id IN (SELECT unnest(?::VARCHAR[]))
        GROUP BY species ORDER BY count(*) DESC, species LIMIT 1""",
        [record.get("gene_ids") or []],
    ).fetchone()
    return row[0] if row else ""


def module_satisfiability(path: Path, record: dict, request: SatisfiabilityRequest) -> dict:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        species = [r[0] for r in fetch(db.execute("SELECT DISTINCT species FROM genes ORDER BY species"))]
        if request.species not in species:
            raise ValueError(f"Species is not loaded in this build: {request.species}")
        row = db.execute(
            "SELECT raw_text FROM knowledge_sources WHERE collection='modules' AND record_id=? AND role='module'",
            [record["id"]],
        ).fetchone()
        if not row:
            raise ValueError("Module source is not in this build")
        document = yaml.load(row[0], Loader=yaml.CSafeLoader)
        return evaluate(db, document, request.species, request.evidence)


def satisfiability_options(path: Path, record: dict) -> dict:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        species = [
            r[0] for r in fetch(db.execute("SELECT DISTINCT species FROM annotations ORDER BY species"))
        ]
        return {"species": species, "default_species": default_species(db, record), "evidence": EVIDENCE}
