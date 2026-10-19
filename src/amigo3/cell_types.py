"""GO-owned differentiation descriptions, independent of annotation propagation."""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

import duckdb
import pyarrow as pa

OBO = "http://purl.obolibrary.org/obo/"
TARGET_RELATION = "RO:0002315"


def curie(identifier):
    return identifier.removeprefix(OBO).replace("_", ":", 1) if identifier.startswith(OBO) else identifier


def schema(strings, lists=(), booleans=()):
    return pa.schema(
        [(k, pa.string()) for k in strings.split()]
        + [(k, pa.list_(pa.string())) for k in lists]
        + [(k, pa.bool_()) for k in booleans]
    )


CELL_SCHEMA = schema(
    "id label definition source_id source_sha256 search_text",
    ("synonyms", "parent_ids", "is_a_closure"),
    ("obsolete",),
)
DIFFERENTIATION_SCHEMA = schema(
    "id go_term_id label target_cell_type_id predicate source_id source_sha256 search_text",
    ("target_cell_type_closure", "axiom_kinds"),
)


def load_cell_types(db, data, manifest):
    sources = {}
    for kind in ("go_plus_json", "cl_json"):
        matches = [s for s in manifest.values() if s["kind"] == kind]
        if len(matches) > 1:
            raise ValueError(f"Use exactly one {kind} snapshot")
        sources[kind] = matches[0] if matches else None
    if bool(sources["go_plus_json"]) != bool(sources["cl_json"]):
        raise ValueError("Differentiation import requires both go_plus_json and cl_json sources")
    cells, descriptions, issues = {}, [], []
    if sources["cl_json"]:
        source = sources["cl_json"]
        graphs = json.loads((data / source["path"]).read_text())["graphs"]
        for graph in graphs:
            for node in graph.get("nodes", []):
                identifier = curie(node["id"])
                if not identifier.startswith("CL:") or node.get("type") != "CLASS":
                    continue
                meta = node.get("meta", {})
                cells[identifier] = {
                    "id": identifier,
                    "label": node.get("lbl", identifier),
                    "definition": meta.get("definition", {}).get("val", ""),
                    "synonyms": sorted({s["val"] for s in meta.get("synonyms", [])}),
                    "obsolete": bool(meta.get("deprecated", False)),
                    "parent_ids": [],
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                }
        for graph in graphs:
            for edge in graph.get("edges", []):
                child, parent = curie(edge["sub"]), curie(edge["obj"])
                if edge["pred"] == "is_a" and child in cells and parent in cells:
                    cells[child]["parent_ids"].append(parent)
        visiting = set()

        @lru_cache(maxsize=None)
        def ancestors(identifier):
            if identifier in visiting:
                raise ValueError(f"Cycle in Cell Ontology is_a hierarchy: {identifier}")
            visiting.add(identifier)
            result = {identifier}
            for parent in cells[identifier]["parent_ids"]:
                result.update(ancestors(parent))
            visiting.remove(identifier)
            return frozenset(result)

        for identifier, cell in cells.items():
            cell["parent_ids"] = sorted(set(cell["parent_ids"]))
            cell["is_a_closure"] = sorted(ancestors(identifier))
            cell["search_text"] = " ".join([identifier, cell["label"], cell["definition"], *cell["synonyms"]])
        source = sources["go_plus_json"]
        graphs = json.loads((data / source["path"]).read_text())["graphs"]
        # Use only GO-basic's is_a closure to identify differentiation processes.
        # No GO-plus edges are written to ontology_edges or annotation closures.
        owners = dict(
            db.execute("""SELECT id, label FROM terms WHERE NOT obsolete
            AND list_contains(is_a_closure, 'GO:0030154')""").fetchall()
        )
        assertions = defaultdict(set)
        for graph in graphs:
            for axiom in graph.get("logicalDefinitionAxioms", []):
                for restriction in axiom.get("restrictions", []):
                    if curie(restriction["propertyId"]) == TARGET_RELATION:
                        assertions[(curie(axiom["definedClassId"]), curie(restriction["fillerId"]))].add(
                            "logical_definition"
                        )
            for edge in graph.get("edges", []):
                if curie(edge["pred"]) == TARGET_RELATION:
                    assertions[(curie(edge["sub"]), curie(edge["obj"]))].add("ontology_edge")
        for (owner, target), kinds in sorted(assertions.items()):
            if not target.startswith("CL:") or owner not in owners:
                continue
            if target not in cells or cells[target]["obsolete"]:
                issues.append(
                    {"kind": "missing_or_obsolete_target_cell", "go_term_id": owner, "target": target}
                )
                continue
            descriptions.append(
                {
                    "id": f"{owner}/differentiation/{target}",
                    "go_term_id": owner,
                    "label": owners[owner],
                    "target_cell_type_id": target,
                    "target_cell_type_closure": cells[target]["is_a_closure"],
                    "predicate": TARGET_RELATION,
                    "axiom_kinds": sorted(kinds),
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "search_text": " ".join([owner, owners[owner], target, cells[target]["label"]]),
                }
            )
    for name, rows, arrow_schema in (
        ("cell_types", list(cells.values()), CELL_SCHEMA),
        ("differentiations", descriptions, DIFFERENTIATION_SCHEMA),
    ):
        db.register("cell_batch", pa.Table.from_pylist(rows, schema=arrow_schema))
        db.execute(f'CREATE TABLE "{name}" AS SELECT * FROM cell_batch')
        db.execute(f'ALTER TABLE "{name}" ADD PRIMARY KEY (id)')
        db.unregister("cell_batch")
    db.execute("CREATE INDEX differentiation_owner ON differentiations(go_term_id)")
    db.execute("CREATE INDEX differentiation_target ON differentiations(target_cell_type_id)")
    return {"cell_types": len(cells), "differentiations": len(descriptions), "issues": issues}


def differentiation_details(db, owner):
    if "differentiations" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}:
        return []
    rows = (
        db.execute(
            """SELECT d.* EXCLUDE(search_text), c.label AS target_label FROM differentiations d
        JOIN cell_types c ON c.id=d.target_cell_type_id WHERE go_term_id=? ORDER BY d.id""",
            [owner],
        )
        .to_arrow_table()
        .to_pylist()
    )
    for row in rows:
        row["target_cell_type"] = {"id": row["target_cell_type_id"], "label": row.pop("target_label")}
    return rows


def cell_type_detail(path, record):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        parents = (
            db.execute(
                "SELECT id, label FROM cell_types WHERE id IN (SELECT unnest(?)) ORDER BY label, id",
                [record["parent_ids"]],
            )
            .to_arrow_table()
            .to_pylist()
        )
        children = (
            db.execute(
                "SELECT id, label FROM cell_types WHERE list_contains(parent_ids, ?) ORDER BY label, id",
                [record["id"]],
            )
            .to_arrow_table()
            .to_pylist()
        )
        groups = {}
        for key, field, where in (
            ("exact", "target_cell_type_id", "target_cell_type_id=?"),
            ("subtypes", "target_cell_type_closure", "list_contains(target_cell_type_closure, ?)"),
        ):
            count = db.execute(
                f"SELECT count(*) FROM differentiations WHERE {where}", [record["id"]]
            ).fetchone()[0]
            rows = (
                db.execute(
                    f"SELECT id, go_term_id, label, target_cell_type_id FROM differentiations WHERE {where} ORDER BY label, id LIMIT 12",
                    [record["id"]],
                )
                .to_arrow_table()
                .to_pylist()
            )
            groups[key] = {"total": count, "items": rows, "field": field, "value": record["id"]}
    return {"record": record, "parents": parents, "children": children, "differentiations": groups}
