"""Local/reference query backend over the published DuckDB build."""

from __future__ import annotations

import duckdb

from amigo3.breakdowns import breakdown_result, histogram_result
from amigo3.query import Query, decode_cursor, encode_cursor
from amigo3.sorting import resolve_sort


class DuckBackend:
    name = "duckdb"

    def __init__(self, catalog):
        self.catalog = catalog

    def _where(self, query, excluded=()):
        clauses, values = [], []
        fields = self.catalog.collections[query.collection]["fields"]
        for token in query.search.split():
            clauses.append("contains(lower(search_text), lower(?))")
            values.append(token)
        for f in query.filters:
            if f.id in excluded:
                continue
            column = '"' + f.field + '"'
            multivalued = fields[f.field]["multivalued"]
            if f.op == "exists":
                if not multivalued and fields[f.field]["type"] == "string":
                    clauses.append(f"NULLIF({column}, '') IS NOT NULL")
                else:
                    clauses.append(f"len({column}) > 0" if multivalued else f"{column} IS NOT NULL")
                continue
            if f.op == "between":
                clauses.append(f"{column} BETWEEN ? AND ?")
                values.extend(f.values)
                continue
            if f.op in ("gte", "lte"):
                clauses.append(f"{column} {'>=' if f.op == 'gte' else '<='} ?")
                values.append(f.values[0])
                continue
            if multivalued:
                expression = "(" + " OR ".join(f"list_contains({column}, ?)" for _ in f.values) + ")"
            else:
                expression = f"{column} IN (" + ",".join("?" for _ in f.values) + ")"
            clauses.append(f"NOT ({expression})" if f.op == "not_in" else expression)
            values.extend(f.values)
        return " AND ".join(clauses) or "TRUE", values

    def query(self, query: Query) -> dict:
        self.catalog.validate(query)
        position = decode_cursor(self.catalog.build_id, query) or 0
        if type(position) is not int or position < 0:
            raise ValueError("Invalid local cursor")
        with duckdb.connect(str(self.catalog.path / "integration.duckdb"), read_only=True) as db:
            where, values = self._where(query)
            total = db.execute(f'SELECT count(*) FROM "{query.collection}" WHERE {where}', values).fetchone()[
                0
            ]
            columns = (
                ", ".join('"' + c + '"' for c in query.select) if query.select else "* EXCLUDE (search_text)"
            )
            ordering, sort_values = [], []
            fields = self.catalog.collections[query.collection]["fields"]
            for clause in resolve_sort(self.catalog.collections[query.collection], query):
                column = f'"{clause.field}"'
                if clause.value is not None:
                    expression = (
                        f"list_contains({column}, ?)"
                        if fields[clause.field]["multivalued"]
                        else f"{column} = ?"
                    )
                    column = f"coalesce({expression}, false)"
                    sort_values.append(clause.value)
                else:
                    # Match Solr's missing scalar values: 0/false for numeric
                    # fields, before strings ascending and after descending.
                    kind = fields[clause.field]["type"]
                    if kind in ("integer", "boolean"):
                        column = f"coalesce({column}, {'0' if kind == 'integer' else 'false'})"
                ordering.append(
                    f"{column} {clause.direction} NULLS {'FIRST' if clause.direction == 'asc' else 'LAST'}"
                )
            cursor = db.execute(
                f'SELECT {columns} FROM "{query.collection}" WHERE {where} ORDER BY {", ".join(ordering)} LIMIT ? OFFSET ?',
                [*values, *sort_values, query.page.size, position],
            )
            names = [c[0] for c in cursor.description]
            hits = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            facets = {}
            for key, facet in query.facets.items():
                fw, fv = self._where(query, facet.exclude_filters)
                field = '"' + facet.field + '"'
                if facet.values is not None:
                    requested = list(dict.fromkeys(facet.values))
                    match = (
                        f"list_contains(a.{field}, v.value)"
                        if self.catalog.collections[query.collection]["fields"][facet.field]["multivalued"]
                        else f"a.{field}=v.value"
                    )
                    rows = db.execute(
                        f'''WITH matches AS (SELECT * FROM "{query.collection}" WHERE {fw})
                        SELECT v.value, count(DISTINCT a.id) FROM unnest(?) v(value)
                        LEFT JOIN matches a ON {match} GROUP BY v.value''',
                        [*fv, requested],
                    ).fetchall()
                    counts = dict(rows)
                    facets[key] = {
                        "field": facet.field,
                        "buckets": [{"value": v, "count": counts[v]} for v in requested],
                        "count_unit": "records",
                        "exact": True,
                        "truncated": False,
                    }
                    continue
                if self.catalog.collections[query.collection]["fields"][facet.field]["multivalued"]:
                    field = f"unnest({field})"
                nonempty = "value IS NOT NULL"
                if self.catalog.collections[query.collection]["fields"][facet.field]["type"] == "string":
                    nonempty += " AND value <> ''"
                rows = db.execute(
                    f'''SELECT value, count(DISTINCT id) AS n FROM
                    (SELECT id, {field} AS value FROM "{query.collection}" WHERE {fw})
                    WHERE {nonempty} GROUP BY value ORDER BY n DESC, value LIMIT ?''',
                    [*fv, facet.limit + 1],
                ).fetchall()
                facets[key] = {
                    "field": facet.field,
                    "buckets": [{"value": v, "count": n} for v, n in rows[: facet.limit]],
                    "count_unit": "records",
                    "exact": True,
                    "truncated": len(rows) > facet.limit,
                }
            breakdowns = {}
            field_types = self.catalog.collections[query.collection]["fields"]

            def expression(field):
                column = f'"{field}"'
                return f"unnest({column})" if field_types[field]["multivalued"] else column

            for key, spec in query.breakdowns.items():

                def categories(field, limit=spec.limit):
                    nonempty = "value IS NOT NULL"
                    if field_types[field]["type"] == "string":
                        nonempty += " AND value <> ''"
                    return db.execute(
                        f'''SELECT value, count(DISTINCT id) AS n FROM
                        (SELECT id, {expression(field)} AS value FROM "{query.collection}" WHERE {where})
                        WHERE {nonempty} GROUP BY value ORDER BY n DESC, value LIMIT ?''',
                        [*values, limit],
                    ).fetchall()

                rows = categories(spec.field)
                columns = categories(spec.split_by) if spec.split_by else []
                cells, covered = {}, 0
                if rows:
                    row_values = [v for v, _ in rows]
                    selected = ",".join("?" for _ in rows)
                    source = f"SELECT id, {expression(spec.field)} AS row_value"
                    if spec.split_by:
                        source += f', "{spec.split_by}" AS column_value'
                    source += f' FROM "{query.collection}" WHERE {where}'
                    covered = db.execute(
                        f"SELECT count(DISTINCT id) FROM ({source}) WHERE row_value IN ({selected})",
                        [*values, *row_values],
                    ).fetchone()[0]
                    if columns:
                        counts = db.execute(
                            f"""SELECT row_value, column_value, count(DISTINCT id) FROM ({source})
                            WHERE row_value IN ({selected})
                            AND column_value IN ({",".join("?" for _ in columns)})
                            GROUP BY row_value, column_value""",
                            [*values, *row_values, *[v for v, _ in columns]],
                        ).fetchall()
                        cells = {(row, column): count for row, column, count in counts}
                breakdowns[key] = breakdown_result(
                    spec,
                    total,
                    rows,
                    columns,
                    cells,
                    covered=covered,
                    multivalued=field_types[spec.field]["multivalued"],
                )
            histograms = {}
            for key, spec in query.histograms.items():
                hw, hv = self._where(query, spec.exclude_filters)
                dimensions = [f'"{spec.field}"'] + ([f'"{spec.y_field}"'] if spec.y_field else [])
                rows = db.execute(
                    f"""SELECT {", ".join(dimensions)}, count(DISTINCT id) FROM "{query.collection}"
                    WHERE {hw} AND {" AND ".join(d + " IS NOT NULL" for d in dimensions)}
                    GROUP BY ALL""",
                    hv,
                ).fetchall()
                scope = total
                if spec.exclude_filters:
                    scope = db.execute(
                        f'SELECT count(*) FROM "{query.collection}" WHERE {hw}', hv
                    ).fetchone()[0]
                histograms[key] = histogram_result(
                    spec,
                    scope,
                    {(row[0], row[1]) if spec.y_field else row[0]: row[-1] for row in rows},
                )
        return {
            "build_id": self.catalog.build_id,
            "collection": query.collection,
            "total": total,
            "total_exact": True,
            "hits": hits,
            "facets": facets,
            "breakdowns": breakdowns,
            "histograms": histograms,
            "next_cursor": encode_cursor(self.catalog.build_id, query, position + len(hits))
            if position + len(hits) < total
            else None,
        }
