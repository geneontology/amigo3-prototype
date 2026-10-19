"""Small GO-basic adapter with separately computed is-a and is-a/part-of closures."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


def load_ontology(path: Path) -> tuple[list[dict], list[dict], str]:
    nodes, edges, stanza, release = {}, [], None, ""
    current: dict = {}

    def finish():
        if stanza == "Term" and current.get("id", "").startswith("GO:"):
            nodes[current["id"]] = dict(current)

    for line in path.read_text().splitlines():
        if line.startswith("data-version: "):
            release = line.split(": ", 1)[1]
        if line.startswith("["):
            finish()
            stanza = line.strip("[]")
            current = {
                "synonyms": [],
                "alt_ids": [],
                "subsets": [],
                "parents": [],
                "obsolete": False,
                "definition": "",
                "aspect": "",
            }
        elif stanza == "Term" and ": " in line:
            key, value = line.split(": ", 1)
            if key in ("id", "name"):
                current[key] = value
            elif key == "namespace":
                current["aspect"] = {
                    "molecular_function": "F",
                    "biological_process": "P",
                    "cellular_component": "C",
                }.get(value, value)
            elif key == "is_obsolete":
                current["obsolete"] = value == "true"
            elif key == "alt_id":
                current["alt_ids"].append(value)
            elif key == "subset":
                current["subsets"].append(value.split()[0])
            elif key in ("synonym", "def"):
                match = re.match(r'"((?:\\.|[^"\\])*)"', value)
                if match:
                    text = match[1].replace('\\"', '"')
                    if key == "synonym":
                        current["synonyms"].append(text)
                    else:
                        current["definition"] = text
            elif key == "is_a":
                current["parents"].append(("is_a", value.split()[0]))
            elif key == "relationship":
                relation, target, *_ = value.split()
                current["parents"].append((relation, target))
    finish()

    visiting = set()

    @lru_cache(maxsize=None)
    def ancestors(term: str, part_of: bool) -> frozenset[str]:
        key = (term, part_of)
        if key in visiting:
            raise ValueError(f"Cycle in GO closure at {term}")
        visiting.add(key)
        result = {term}
        for predicate, parent in nodes.get(term, {}).get("parents", []):
            if predicate == "is_a" or (part_of and predicate in ("part_of", "BFO:0000050")):
                result.update(ancestors(parent, part_of))
        visiting.remove(key)
        return frozenset(result)

    documents = []
    for term, node in sorted(nodes.items()):
        for predicate, parent in node["parents"]:
            edges.append({"subject": term, "predicate": predicate, "object": parent})
        documents.append(
            {
                "id": term,
                "label": node.get("name", term),
                "definition": node["definition"],
                "aspect": node["aspect"],
                "synonyms": node["synonyms"],
                "alt_ids": node["alt_ids"],
                "subsets": node["subsets"],
                "obsolete": node["obsolete"],
                "parent_ids": sorted({p for _, p in node["parents"]}),
                "is_a_closure": sorted(ancestors(term, False)),
                "isa_partof_closure": sorted(ancestors(term, True)),
            }
        )
    return documents, edges, release
