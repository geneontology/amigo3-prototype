"""Collection sort presets, resolved to the same clauses for both query engines."""

from amigo3.query import Sort


def configure_sorts(config):
    title = config["title_field"]
    options = {
        "recommended": {"label": "Recommended", **config.pop("recommended_sort", {"sort": []})},
        "name_asc": {"label": "Name A–Z", "sort": [{"field": title}]},
        "name_desc": {"label": "Name Z–A", "sort": [{"field": title, "direction": "desc"}]},
        "id": {"label": "ID", "sort": [{"field": "id"}]},
        **config.get("sort_options", {}),
    }
    # Immutable older snapshots can lack newer fields.
    for option in options.values():
        option["sort"] = [s for s in option["sort"] if s["field"] in config["fields"]]
    config["sort_options"] = options


def resolve_sort(config, query):
    if isinstance(query.sort, str):
        if query.sort not in config["sort_options"]:
            raise ValueError(f"Unknown sort option: {query.sort}")
        clauses = [Sort(**s) for s in config["sort_options"][query.sort]["sort"]]
        if query.sort == "recommended" and query.search.strip():
            # An exact name or identifier wins over general browsing preferences.
            clauses = [
                Sort(field=f, direction="desc", value=query.search.strip())
                for f in ("id", config["title_field"])
            ] + clauses
    else:
        clauses = list(query.sort)
    if not any(s.field == "id" and s.value is None for s in clauses):
        clauses.append(Sort(field="id"))
    return clauses
