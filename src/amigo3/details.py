"""Detail-page read models over the immutable integration snapshot."""

from __future__ import annotations

import json

import duckdb

from amigo3.cell_types import differentiation_details
from amigo3.knowledge import COLLECTIONS, parse_document
from amigo3.ontology_graph import ontology_graph
from amigo3.reactions import reaction_details


def related(db, field: str, value: str, exclude: str = "") -> dict:
    if field not in (
        "gene_ids",
        "term_ids",
        "family_ids",
        "model_ids",
        "module_ids",
        "report_ids",
        "project_ids",
        "rule_ids",
    ):
        raise ValueError("Unsupported relationship")
    tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
    result = {}
    for collection in (*COLLECTIONS, "reports", "projects", "rules"):
        if collection not in tables:
            continue
        if field in ("report_ids", "project_ids", "rule_ids") and collection != "projects":
            continue
        where = f'list_contains("{field}", ?) AND id <> ?'
        count = db.execute(f'SELECT count(*) FROM "{collection}" WHERE {where}', [value, exclude]).fetchone()[
            0
        ]
        if count:
            columns = "id, label, kind"
            if collection == "rules":
                columns += ", subject_id, object_id, predicate_id, predicate_modifier, rule_source"
            rows = (
                db.execute(
                    f'SELECT {columns} FROM "{collection}" WHERE {where} ORDER BY label, id LIMIT 12',
                    [value, exclude],
                )
                .to_arrow_table()
                .to_pylist()
            )
            if collection == "rules" and "rule_documents" in tables:
                documents = {
                    identifier: json.loads(document).get("mapping", {})
                    for identifier, document in db.execute(
                        "SELECT id, document_json FROM rule_documents WHERE id IN (SELECT unnest(?))",
                        [[row["id"] for row in rows]],
                    ).fetchall()
                }
                for row in rows:
                    mapping = documents.get(row["id"], {})
                    row.update(
                        {
                            key: mapping.get(key, "")
                            for key in ("subject_label", "object_label", "predicate_label")
                        }
                    )
            result[collection] = {"total": count, "items": rows, "field": field, "value": value}
    return result


def gene_related(path, identifier):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        return related(db, "gene_ids", identifier)


def term_detail(path, record):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:

        def edges(incoming):
            endpoint = "e.subject" if incoming else "e.object"
            condition = "e.object" if incoming else "e.subject"
            return (
                db.execute(
                    f"SELECT {endpoint} AS id, coalesce(t.label, {endpoint}) AS label, e.predicate FROM ontology_edges e LEFT JOIN terms t ON t.id={endpoint} WHERE {condition}=? ORDER BY e.predicate, label, id",
                    [record["id"]],
                )
                .to_arrow_table()
                .to_pylist()
            )

        counts = (
            db.execute(
                """SELECT count(*) AS annotation_count,
            count(*) FILTER (WHERE ontology_class_id=?) AS direct_annotation_count,
            count(DISTINCT gene_id) AS gene_count,
            count(*) FILTER (WHERE negation) AS negated_annotation_count
            FROM annotations WHERE list_contains(isa_partof_closure, ?)""",
                [record["id"], record["id"]],
            )
            .to_arrow_table()
            .to_pylist()[0]
        )
        return {
            "record": record,
            "reactions": reaction_details(db, go_term_id=record["id"]),
            "differentiations": differentiation_details(db, record["id"]),
            "ontology": ontology_graph(db, record),
            "parents": edges(False),
            "children": edges(True),
            "counts": counts,
            "related": related(db, "term_ids", record["id"]),
        }


def model_processes(model, labels) -> list[dict]:
    """Distinct biological processes a GO-CAM's activities are part of.

    Uses each activity's structured part_of association (and any larger process
    that association is itself part of); ordered by how many activities fall in
    each process, then by first appearance in the model.
    """
    counts = {}
    for activity in (model or {}).get("activities") or []:
        seen = set()
        associations = activity.get("part_of") or []
        stack = list(associations if isinstance(associations, list) else [associations])
        while stack:
            association = stack.pop(0)
            if isinstance(association, str):
                association = {"term": association}
            if not isinstance(association, dict):
                continue
            term = association.get("term")
            if isinstance(term, str) and term.startswith("GO:") and term not in seen:
                seen.add(term)
                counts[term] = counts.get(term, 0) + 1
            parent = association.get("part_of")
            stack.extend(parent if isinstance(parent, list) else [parent] if parent else [])
    # Stable sort keeps first-appearance order among equally common processes.
    ranked = sorted(counts, key=lambda term: -counts[term])
    return [
        {"id": term, "label": labels.get(term) or term, "activity_count": counts[term]} for term in ranked
    ]


def resource_detail(path, collection, record):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        sources = db.execute(
            "SELECT role, path, sha256, raw_text FROM knowledge_sources WHERE collection=? AND record_id=? ORDER BY path",
            [collection, record["id"]],
        ).fetchall()
        documents = {role: parse_document(source, raw) for role, source, checksum, raw in sources}
        genes = (
            db.execute(
                "SELECT id, symbol, identifiers FROM genes WHERE id IN (SELECT unnest(?::VARCHAR[])) ORDER BY symbol, id",
                [record["gene_ids"]],
            )
            .to_arrow_table()
            .to_pylist()
        )
        links = {}
        for gene in genes:
            for alias in gene["identifiers"]:
                links.setdefault(alias, []).append({"id": gene["id"], "symbol": gene["symbol"]})
        label_rows = db.execute(
            "SELECT id, label FROM terms WHERE id IN (SELECT unnest(?::VARCHAR[]))", [record["term_ids"]]
        ).fetchall()
        labels = dict(label_rows)
        for obj in (documents.get("model") or {}).get("objects") or []:
            labels.setdefault(obj["id"], obj.get("label") or obj["id"])
        related_field = {"gocams": "model_ids", "modules": "module_ids", "families": "family_ids"}[collection]
        neighbors = related(db, related_field, record["id"], record["id"])
        # A subfamily reference also points back to its parent family, without
        # asserting that a module uses all members of that parent family.
        if collection == "families":
            for other in ("modules",):
                rows = (
                    db.execute(
                        f"SELECT id, label, kind FROM {other} WHERE list_contains(family_ids, ?) OR EXISTS (SELECT 1 FROM unnest(family_ids) AS f(id) WHERE starts_with(f.id, ?)) ORDER BY label",
                        [record["id"], record["id"] + ":"],
                    )
                    .to_arrow_table()
                    .to_pylist()
                )
                if rows:
                    existing = neighbors.setdefault(
                        other, {"total": 0, "items": [], "field": related_field, "value": record["id"]}
                    )
                    combined = {r["id"]: r for r in rows}
                    existing.update(
                        total=len(combined), items=list(combined.values()), subfamilies_included=True
                    )
        return {
            "record": record,
            "documents": documents,
            "sources": [
                {"role": role, "path": source, "sha256": checksum} for role, source, checksum, _ in sources
            ],
            "gene_links": links,
            "genes": [{"id": g["id"], "symbol": g["symbol"]} for g in genes],
            "labels": labels,
            "related": neighbors,
            **(
                {"processes": model_processes(documents.get("model"), labels)}
                if collection == "gocams"
                else {}
            ),
        }
