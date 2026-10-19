"""Chemical pages over shared identities and exact, source-recorded references."""

from __future__ import annotations

import duckdb

from amigo3.structures import structure_details


def chemical_detail(path, record):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        total, distinct = db.execute(
            """SELECT count(*), count(DISTINCT rhea_id) FROM reactions
            WHERE list_contains(chemical_ids, ?)""",
            [record["id"]],
        ).fetchone()
        reactions = (
            db.execute(
                """SELECT r.id, r.go_term_id, t.label AS go_term_label, r.rhea_id,
            r.label, r.direction, list_contains(r.left_chemical_ids, ?) AS on_left,
            list_contains(r.right_chemical_ids, ?) AS on_right
            FROM reactions r JOIN terms t ON t.id=r.go_term_id
            WHERE list_contains(r.chemical_ids, ?) ORDER BY r.rhea_id, r.go_term_id LIMIT 12""",
                [record["id"]] * 3,
            )
            .to_arrow_table()
            .to_pylist()
        )
        structure = structure_details(db, [record["id"]]).get(record["id"])
        related = {}
        available = True
        for collection in ("gocams", "modules"):
            fields = {
                row[0]
                for row in db.execute(
                    """SELECT column_name FROM information_schema.columns
                WHERE table_name=?""",
                    [collection],
                ).fetchall()
            }
            if "chemical_ids" not in fields:
                available = False
                continue
            count = db.execute(
                f"SELECT count(*) FROM {collection} WHERE list_contains(chemical_ids, ?)", [record["id"]]
            ).fetchone()[0]
            items = (
                db.execute(
                    f"""SELECT id, label, kind FROM {collection}
                WHERE list_contains(chemical_ids, ?) ORDER BY label, id LIMIT 12""",
                    [record["id"]],
                )
                .to_arrow_table()
                .to_pylist()
            )
            related[collection] = {
                "total": count,
                "items": items,
                "field": "chemical_ids",
                "value": record["id"],
            }
    return {
        "record": record,
        "structure": structure,
        "reactions": {"total": total, "distinct_rhea_count": distinct, "items": reactions},
        "related": related,
        "knowledge_links_available": available,
    }
