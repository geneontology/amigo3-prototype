"""Structural metrics of cached GO-CAMs and modules, derived only from explicit structure.

GO-CAM graph metrics follow gocam-py's query index (the official GO-CAM browser):
nodes are activities with an enabled_by; edges are explicit causal associations plus
molecule-mediated chains (one activity's output is another's input or regulator).
"""

from __future__ import annotations

from collections import defaultdict, deque

# gocam-py IMPLICIT_CAUSAL_ASSOCIATION_CHAINS: output -> input/regulator predicates.
OUTPUTS = {"RO:0002234", "RO:0004008"}
CONSUMERS = {"RO:0002233", "RO:0004009", "RO:0012000", "RO:0012001", "RO:0012002"}
# Module connection types that relate parts in sequence; PART_OF is containment.
NON_FLOW = {"PART_OF"}
# Realization counts are combinatorial; saturate well inside int64.
REALIZATION_CAP = 10**12


def longest_path(edges: dict[str, set[str]], nodes) -> int:
    """Longest of the shortest directed paths, in edges (gocam-py's definition; cycle-safe)."""
    longest = 0
    for start in nodes:
        distance = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for target in edges.get(node, ()):
                if target not in distance:
                    distance[target] = distance[node] + 1
                    longest = max(longest, distance[target])
                    queue.append(target)
    return longest


def components(edges: dict[str, set[str]], nodes) -> int:
    """Weakly connected components: distinct causal subgraphs, including singletons."""
    neighbours = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            neighbours[source].add(target)
            neighbours[target].add(source)
    seen, count = set(), 0
    for start in nodes:
        if start in seen:
            continue
        count += 1
        stack = [start]
        seen.add(start)
        while stack:
            for other in neighbours[stack.pop()]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
    return count


def gocam_metrics(model: dict) -> dict[str, int]:
    activities = model.get("activities") or []
    enabled = [a for a in activities if a.get("enabled_by")]
    ids = [a["id"] for a in enabled]
    known = set(ids)
    consumers = defaultdict(set)
    for activity in enabled:
        for association in activity.get("molecular_associations") or []:
            if association.get("molecule") and association.get("predicate") in CONSUMERS:
                consumers[association["molecule"]].add(activity["id"])
    edges = defaultdict(set)
    for activity in enabled:
        for causal in activity.get("causal_associations") or []:
            if causal.get("downstream_activity") in known:
                edges[activity["id"]].add(causal["downstream_activity"])
        for association in activity.get("molecular_associations") or []:
            if association.get("predicate") in OUTPUTS:
                edges[activity["id"]].update(
                    consumers.get(association.get("molecule"), set()) - {activity["id"]}
                )
    enabled_by = {a["enabled_by"].get("term") for a in enabled if a["enabled_by"].get("term")}
    edge_count = sum(len(targets) for targets in edges.values())
    return {
        "activity_count": len(activities),
        "enabled_by_count": len(enabled_by),
        "causal_edge_count": edge_count,
        "longest_path_length": longest_path(edges, ids),
        "component_count": components(edges, ids),
    }


def module_metrics(module: dict) -> dict[str, int]:
    """AND/OR structure of a recursive module.

    Parts are conjunctive; variant sets are disjunctive branch points. The path graph
    combines explicit connections (except PART_OF containment) with implicit
    succession between consecutively ordered sibling parts.
    """
    totals = defaultdict(int)
    edges = defaultdict(set)
    nodes = set()

    def visit(node: dict, depth: int) -> int:
        totals["depth"] = max(totals["depth"], depth)
        totals["annoton_count"] += len(node.get("annotons") or [])
        for connection in node.get("connections") or []:
            totals["connection_count"] += 1
            source, target = connection.get("source"), connection.get("target")
            if source and target and connection.get("connection_type") not in NON_FLOW:
                edges[source].add(target)
                nodes.update((source, target))
        realizations = 1
        orders = defaultdict(list)
        for part in node.get("parts") or []:
            totals["part_count"] += 1
            child = part.get("node") or {}
            count = visit(child, depth + 1) if child else 1
            realizations *= count + 1 if part.get("optional") else count
            if isinstance(part.get("order"), int) and child.get("id"):
                orders[part["order"]].append(child["id"])
        ranked = [orders[k] for k in sorted(orders)]
        for before, after in zip(ranked, ranked[1:], strict=False):
            for source in before:
                edges[source].update(after)
                nodes.update([source, *after])
        for variant_set in node.get("variant_sets") or []:
            totals["variant_set_count"] += 1
            counts = []
            for variant in variant_set.get("variants") or []:
                totals["variant_count"] += 1
                counts.append(visit(variant, depth + 1))
            # Absent or unrecognised selections are counted as EXACTLY_ONE, the smallest reading.
            selection = variant_set.get("selection")
            if selection not in ("ONE_OR_MORE", "ZERO_OR_MORE"):
                choices = sum(counts)
            else:
                choices = 1
                for count in counts:
                    # One above the cap, so subtracting the empty choice still saturates.
                    choices = min(choices * (count + 1), REALIZATION_CAP + 1)
                if selection == "ONE_OR_MORE":
                    choices -= 1
                choices = min(choices, REALIZATION_CAP)
            realizations *= max(choices, 1)
            realizations = min(realizations, REALIZATION_CAP)
        return min(realizations, REALIZATION_CAP)

    realizations = visit(module, 0)
    return {
        "part_count": totals["part_count"],
        "variant_set_count": totals["variant_set_count"],
        "variant_count": totals["variant_count"],
        "realization_count": realizations,
        "max_depth": totals["depth"],
        "annoton_count": totals["annoton_count"],
        "connection_count": totals["connection_count"],
        "longest_path_length": longest_path(edges, sorted(nodes)),
    }
