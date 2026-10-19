"""Complete, typed ancestor subgraphs from the pinned ontology snapshot."""

HIERARCHY_PREDICATES = ("is_a", "part_of", "BFO:0000050")


def ontology_graph(db, record):
    identifiers = sorted(set(record["isa_partof_closure"]) | {record["id"]})
    nodes = (
        db.execute(
            "SELECT id, label, definition, obsolete FROM terms WHERE id IN (SELECT unnest(?)) ORDER BY id",
            [identifiers],
        )
        .to_arrow_table()
        .to_pylist()
    )
    known = {node["id"] for node in nodes}
    # Keep references to missing parents visible rather than inventing a root.
    nodes.extend(
        {
            "id": identifier,
            "label": identifier,
            "definition": "Term missing from this snapshot.",
            "missing": True,
        }
        for identifier in identifiers
        if identifier not in known
    )
    edges = (
        db.execute(
            """SELECT DISTINCT subject, predicate, object FROM ontology_edges
        WHERE subject IN (SELECT unnest(?)) AND object IN (SELECT unnest(?))
        AND predicate IN (SELECT unnest(?)) ORDER BY subject, predicate, object""",
            [identifiers, identifiers, HIERARCHY_PREDICATES],
        )
        .to_arrow_table()
        .to_pylist()
    )
    children = (
        db.execute(
            """SELECT DISTINCT e.subject AS id, coalesce(t.label, e.subject) AS label, e.predicate,
        EXISTS (SELECT 1 FROM ontology_edges child WHERE child.object=e.subject
                AND child.predicate IN (SELECT unnest(?))) AS has_children
        FROM ontology_edges e LEFT JOIN terms t ON t.id=e.subject
        WHERE e.object=? AND e.predicate IN (SELECT unnest(?)) ORDER BY label, id, predicate""",
            [HIERARCHY_PREDICATES, record["id"], HIERARCHY_PREDICATES],
        )
        .to_arrow_table()
        .to_pylist()
    )
    return {"focus": record["id"], "nodes": nodes, "edges": edges, "children": children}
