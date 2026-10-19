"""Shared chart response semantics for the local and published query engines."""


def configure_dashboard(config):
    """Keep chart choices compatible with older immutable snapshots."""
    dashboard = config["dashboard"]
    fields = config["fields"]
    dashboard["fields"] = [
        field for field in dashboard["fields"] if field in fields and fields[field]["facetable"]
    ]
    dashboard["cards"] = [field for field in dashboard["cards"] if field in dashboard["fields"]]
    if dashboard["group"] not in dashboard["fields"]:
        dashboard["group"] = dashboard["fields"][0]
    if dashboard["split"] not in dashboard["fields"] or dashboard["split"] == dashboard["group"]:
        dashboard["split"] = ""


def breakdown_result(spec, total, rows, columns=(), cells=None, *, covered=None, multivalued=False):
    """Rows/columns are independently ranked across the full filtered result set.

    Groups may overlap; covered counts distinct records in their union. Scalar
    splits remain additive within each row. Missing/outside-top-N values stay
    in the remainder and in the denominators.
    """
    cells = cells or {}
    buckets = []
    for value, count in rows:
        bucket = {"value": value, "count": count}
        if spec.split_by:
            bucket["segments"] = [
                {"value": column, "count": cells.get((value, column), 0)} for column, _ in columns
            ]
            bucket["remainder"] = count - sum(item["count"] for item in bucket["segments"])
        buckets.append(bucket)
    return {
        "field": spec.field,
        "split_by": spec.split_by,
        "buckets": buckets,
        "columns": [{"value": value, "count": count} for value, count in columns],
        "remainder": total - (covered if covered is not None else sum(count for _, count in rows)),
        "group_multivalued": multivalued,
        "total": total,
        "count_unit": "records",
        "exact": True,
    }


def configure_ranges(config):
    """Keep numeric facets to integer, single-valued fields present in this snapshot."""
    fields = config["fields"]

    def numeric(field):
        return field in fields and fields[field]["type"] == "integer" and not fields[field]["multivalued"]

    config["range_facets"] = {k: v for k, v in (config.get("range_facets") or {}).items() if numeric(k)}
    dashboard = config["dashboard"]
    dashboard["distributions"] = [f for f in dashboard.get("distributions", []) if numeric(f)]
    joint = [f for f in dashboard.get("joint", []) if numeric(f)]
    dashboard["joint"] = joint if len(joint) == 2 and joint[0] != joint[1] else []


def bin_edges(low: int, high: int, bins: int, scale: str = "linear") -> list[tuple[int, int]]:
    """Inclusive integer intervals covering [low, high]; at most `bins` of them."""
    if high - low + 1 <= bins:
        return [(value, value) for value in range(low, high + 1)]
    if bins == 1:
        return [(low, high)]
    if scale == "log" and high > 0:
        intervals, start = [], low
        if low <= 0:
            intervals.append((low, 0))
            start, bins = 1, bins - 1
        ratio = (high / start) ** (1 / bins)
        bounds = sorted({start, *(round(start * ratio**i) for i in range(1, bins)), high + 1})
        return intervals + [(a, b - 1) for a, b in zip(bounds, bounds[1:], strict=False)]
    width = -(-(high - low + 1) // bins)
    return [(start, min(start + width - 1, high)) for start in range(low, high + 1, width)]


def _locate(edges, value):
    for index, (start, end) in enumerate(edges):
        if start <= value <= end:
            return index
    raise ValueError(value)


def histogram_result(spec, total: int, counts: dict) -> dict:
    """Bin an exact distribution: {x: count}, or {(x, y): count} for a joint histogram."""
    joint = spec.y_field is not None
    xs = [key[0] if joint else key for key in counts]
    result = {
        "field": spec.field,
        "y_field": spec.y_field,
        "scale": spec.scale,
        "total": total,
        "missing": total - sum(counts.values()),
        "min": min(xs, default=None),
        "max": max(xs, default=None),
        "buckets": [],
        "count_unit": "records",
        "exact": True,
    }
    if not counts:
        return result
    edges = bin_edges(result["min"], result["max"], spec.bins, spec.scale)
    totals = [0] * len(edges)
    for key, count in counts.items():
        totals[_locate(edges, key[0] if joint else key)] += count
    result["buckets"] = [{"start": a, "end": b, "count": n} for (a, b), n in zip(edges, totals, strict=True)]
    if joint:
        ys = [y for _, y in counts]
        y_edges = bin_edges(min(ys), max(ys), spec.y_bins, spec.y_scale)
        cells = [[0] * len(y_edges) for _ in edges]
        for (x, y), count in counts.items():
            cells[_locate(edges, x)][_locate(y_edges, y)] += count
        result.update(
            y_scale=spec.y_scale,
            y_min=min(ys),
            y_max=max(ys),
            y_buckets=[
                {"start": a, "end": b, "count": sum(row[i] for row in cells)}
                for i, (a, b) in enumerate(y_edges)
            ],
            cells=cells,
        )
    return result
