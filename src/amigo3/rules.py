"""Source-preserving mapping assertions, propagation rules and their reviews."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import duckdb
import pyarrow as pa
import yaml

STRINGS = (
    "id",
    "label",
    "description",
    "kind",
    "rule_source",
    "source_path",
    "mapping_set_id",
    "subject_id",
    "object_id",
    "predicate_id",
    "predicate_modifier",
    "mapping_justification",
    "review_status",
    "review_action",
    "mapping_scope",
    "antecedent_status",
    "search_text",
)
LISTS = (
    "term_ids",
    "family_ids",
    "gene_ids",
    "model_ids",
    "module_ids",
    "taxon_ids",
    "premise_ids",
    "project_ids",
)
SCHEMA = pa.schema(
    [(k, pa.string()) for k in STRINGS]
    + [(k, pa.list_(pa.string())) for k in LISTS]
    + [("has_review", pa.bool_()), ("condition_set_count", pa.int64())]
)
PATTERNS = (
    "projects/**/*.sssom.yaml",
    "projects/PROTEOSTASIS/mappings/*.yaml",
    "rules/*/*/*-review.yaml",
    "interpro/pfam/*/*-review.yaml",
)
# SSSOM 1.0 set slots which apply to individual mappings. Creator/reviewer
# metadata on the set is deliberately NOT inherited by individual mappings.
PROPAGATABLE = (
    "mapping_date",
    "mapping_provider",
    "mapping_tool",
    "mapping_tool_version",
    "object_match_field",
    "object_preprocessing",
    "object_source",
    "object_source_version",
    "object_type",
    "subject_match_field",
    "subject_preprocessing",
    "subject_source",
    "subject_source_version",
    "subject_type",
)


def input_paths(root):
    return sorted({p for pattern in PATTERNS for p in root.glob(pattern)}) if root else []


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def identity(path, payload):
    return "RULE:" + hashlib.sha256(encoded([path, payload]).encode()).hexdigest()[:24]


def entries(path, source):
    """Yield display projections plus the complete typed source payload.

    Anonymous assertion IDs include the entire source row, not its ordinal:
    reordering is stable, and changed assertions cannot inherit an old review.
    Native rule IDs retain continuity across source revisions.
    """
    if "mappings" in source:
        metadata = {k: v for k, v in source.items() if k != "mappings"}
        for row in source["mappings"]:
            effective = {k: metadata[k] for k in PROPAGATABLE if k in metadata} | row
            yield (
                {
                    "kind": "SSSOM mapping",
                    "mapping_set_id": str(metadata.get("mapping_set_id", "")),
                    "label": f"{row.get('subject_label') or row.get('subject_id', 'Literal subject')} → {row.get('object_label') or row.get('object_id', 'Literal target')}",
                    "description": row.get("comment", ""),
                    **{
                        k: row.get(k, "")
                        for k in (
                            "subject_id",
                            "object_id",
                            "predicate_id",
                            "predicate_modifier",
                            "mapping_justification",
                        )
                    },
                    "antecedent_status": "Mapping assertion",
                },
                {"mapping": row, "effective_mapping": effective, "mapping_set": metadata},
                row,
            )
    elif "subject_curations" in source:
        for row in source["subject_curations"]:
            target = row.get("target_term") or {}
            yield (
                {
                    "kind": "Conditional mapping",
                    "mapping_set_id": source.get("id", ""),
                    "label": row["subject_code"] + " → " + target.get("label", "No target recorded"),
                    "description": row.get("rationale", ""),
                    "subject_id": "",
                    "object_id": target.get("id", ""),
                    "review_status": row.get("curation_status", ""),
                    "mapping_scope": row.get("mapping_scope", ""),
                    "has_review": row.get("curation_status") not in (None, "pending_review"),
                    "antecedent_status": "Source context and exclusions",
                },
                {
                    "curation": row,
                    "mapping_set": {k: v for k, v in source.items() if k != "subject_curations"},
                },
                row,
            )
    elif "pfam_id" in source:
        for row in source.get("proposed_annotations") or []:
            term = row.get("term") or {}
            yield (
                {
                    "kind": "Family annotation proposal",
                    "subject_id": "Pfam:" + source["pfam_id"],
                    "object_id": term.get("id", ""),
                    "predicate_id": (row.get("relation") or {}).get("id", ""),
                    "label": source["pfam_id"] + " → " + term.get("label", term.get("id", "")),
                    "description": row.get("rationale", ""),
                    "review_status": row.get("status", ""),
                    "has_review": True,
                    "antecedent_status": "Family membership",
                },
                {"proposal": row, "entry": {k: v for k, v in source.items() if k != "proposed_annotations"}},
                row,
            )
    elif "rule" in source:
        rule = source["rule"] or {}
        sets = rule.get("condition_sets") or []
        yield (
            {
                "id": "RULE:" + str(source["id"]),
                "kind": "Annotation rule",
                "label": str(source["id"]),
                "description": source.get("description", ""),
                "review_status": source.get("status", ""),
                "review_action": source.get("action", ""),
                "has_review": True,
                "condition_set_count": len(sets),
                "antecedent_status": "OR of AND groups" if sets else "Not captured",
            },
            {"rule": rule, "review": {k: v for k, v in source.items() if k != "rule"}},
            source,
        )


def typed_ids(value):
    """Collect explicit structured references, never identifiers from rationale prose."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("id", "curie", "go_id", "subject_id", "object_id", "accession", "term") and isinstance(
                child, str
            ):
                yield child
            elif isinstance(child, (dict, list)):
                yield from typed_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from typed_ids(child)


def load_rules(db, root, expected):
    db.execute("CREATE TABLE rule_documents(id VARCHAR, document_json VARCHAR)")
    db.execute("CREATE TABLE rule_sources(path VARCHAR, sha256 VARCHAR, raw_text VARCHAR)")
    aliases = {}
    for gene, identifiers in db.execute("SELECT id, identifiers FROM genes").fetchall():
        for alias in identifiers:
            aliases.setdefault(alias, set()).add(gene)
    records, documents, sources, seen = [], [], [], set()
    for path in input_paths(root):
        relative = str(path.relative_to(root))
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if expected.get(relative) != checksum:
            raise ValueError(f"Rule/mapping source changed during build: {path}")
        source = yaml.load(raw, Loader=yaml.CSafeLoader)
        sources.append((relative, checksum, raw.decode("utf-8")))
        duplicates = Counter()
        for projection, document, original in entries(relative, source):
            identifier = projection.get("id") or identity(relative, original)
            duplicates[identifier] += 1
            if duplicates[identifier] > 1:
                identifier += ":" + str(duplicates[identifier])
            if identifier in seen:
                raise ValueError(f"Duplicate rule identifier: {identifier}")
            seen.add(identifier)
            record = {
                **{k: "" for k in STRINGS},
                **{k: [] for k in LISTS},
                "has_review": False,
                "condition_set_count": 0,
                **projection,
            }
            record.update(
                id=identifier,
                source_path=relative,
                rule_source=(
                    source.get("rule_type")
                    or ("Pfam" if "pfam_id" in source else relative.split("/")[1].upper())
                ),
            )
            # References are navigation aids, not asserted consequences or matched genes.
            ids = set(
                typed_ids(
                    document.get("rule")
                    or document.get("mapping")
                    or document.get("curation")
                    or document.get("proposal")
                )
            )
            ids.update([record["subject_id"], record["object_id"]])
            ids = {
                "PANTHER:" + i if i.startswith("PTHR") else i.replace("UniProt:", "UniProtKB:") for i in ids
            }
            record["term_ids"] = sorted(i for i in ids if i.startswith("GO:"))
            record["family_ids"] = sorted(
                {":".join(i.split(":")[:2]) for i in ids if i.startswith("PANTHER:PTHR")}
            )
            record["taxon_ids"] = sorted(i for i in ids if i.startswith("NCBITaxon:"))
            record["gene_ids"] = sorted({g for i in ids for g in aliases.get(i, [])})
            sets = document.get("rule", {}).get("condition_sets") or []
            record["premise_ids"] = sorted(
                {
                    str(c.get("curie") or c.get("value"))
                    for s in sets
                    for c in s.get("conditions", [])
                    if c.get("curie") or c.get("value")
                }
            )
            record["search_text"] = encoded([relative, projection, document])
            record["description"] = str(record["description"] or "")[:600]
            documents.append((identifier, encoded(document)))
            records.append(record)
    db.register("_rules", pa.Table.from_pylist(records, schema=SCHEMA))
    db.execute("CREATE TABLE rules AS SELECT * FROM _rules")
    db.unregister("_rules")
    if documents:
        db.executemany("INSERT INTO rule_documents VALUES (?, ?)", documents)
    if sources:
        db.executemany("INSERT INTO rule_sources VALUES (?, ?, ?)", sources)
    return {
        "records": len(records),
        "sources": len(sources),
        "kinds": dict(Counter(r["kind"] for r in records)),
    }


def rule_detail(path, record):
    from amigo3.details import related

    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        document = json.loads(
            db.execute("SELECT document_json FROM rule_documents WHERE id=?", [record["id"]]).fetchone()[0]
        )
        checksum = db.execute(
            "SELECT sha256 FROM rule_sources WHERE path=?", [record["source_path"]]
        ).fetchone()[0]
        neighbors = related(db, "rule_ids", record["id"])
    return dict(
        record=record,
        documents=document,
        sources=[dict(role="Source", path=record["source_path"], sha256=checksum)],
        related=neighbors,
    )
