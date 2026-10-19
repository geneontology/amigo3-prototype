"""Build small facet hierarchies; membership arrays use ordinary backend facets.

The GO projection follows ai-gene-review's build_browser_hierarchies: retain
nearest displayed ancestors and all parent paths, and map detailed terms using
the full ontology. Taxonomy is deliberately a curated subset for loaded species.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

from amigo3.product_types import product_type_hierarchy

GO_ROOTS = {"GO:0003674", "GO:0008150", "GO:0005575"}
UNMAPPED_GO = "unmapped-go"

# NCBI Taxonomy IDs, with intermediate ranks omitted for navigation.
CLADES = {
    "NCBITaxon:2": ("Bacteria", []),
    "NCBITaxon:2157": ("Archaea", []),
    "NCBITaxon:2759": ("Eukaryota", []),
    "NCBITaxon:33154": ("Opisthokonta", ["NCBITaxon:2759"]),
    "NCBITaxon:33208": ("Metazoa", ["NCBITaxon:33154"]),
    "NCBITaxon:4751": ("Fungi", ["NCBITaxon:33154"]),
    "NCBITaxon:33090": ("Viridiplantae", ["NCBITaxon:2759"]),
    "NCBITaxon:554915": ("Amoebozoa", ["NCBITaxon:2759"]),
}


def load_species(db, taxonomy: Path, species: dict[str, dict]) -> None:
    """Configured species and the UniProt lineages of their taxa, as build tables.

    A species taxon takes its configured label; other taxa (strains named by
    allowed_taxa) keep the UniProt scientific name. Lineages are root first.
    """
    labels = {s["taxon"]: s["label"] for s in species.values()}
    db.execute("CREATE TABLE species(key VARCHAR, label VARCHAR, taxon_id VARCHAR, aliases VARCHAR[])")
    db.executemany(
        "INSERT INTO species VALUES (?, ?, ?, ?)",
        [(key, s["label"], s["taxon"], s.get("aliases", [])) for key, s in species.items()],
    )
    db.execute("CREATE TABLE taxa(taxon_id VARCHAR, label VARCHAR, lineage VARCHAR[])")
    rows = []
    for record in json.loads(taxonomy.read_text())["results"]:
        taxon = f"NCBITaxon:{record['taxonId']}"
        lineage = [f"NCBITaxon:{ancestor['taxonId']}" for ancestor in record["lineage"]]
        rows.append((taxon, labels.get(taxon, record["scientificName"]), lineage))
    db.executemany("INSERT INTO taxa VALUES (?, ?, ?)", rows)
    missing = labels.keys() - {row[0] for row in rows}
    if missing:
        raise ValueError(f"UniProt taxonomy lacks configured taxa: {sorted(missing)}")


def lineage(db, taxon: str) -> set[str] | None:
    """The taxon and its NCBI ancestors, or None when the taxon is not a loaded one."""
    row = db.execute("SELECT lineage FROM taxa WHERE taxon_id = ?", [taxon]).fetchone()
    return {taxon, *row[0]} if row else None


def go_hierarchy(terms: list[dict], observed: set[str]) -> tuple[dict, dict]:
    active = {term["id"]: term for term in terms if not term["obsolete"]}
    selected = {key for key, term in active.items() if "goslim_generic" in term["subsets"]}
    if not selected:
        raise ValueError("GO source contains no active goslim_generic terms")
    selected |= GO_ROOTS & active.keys()
    aliases = {alias: key for key, term in active.items() for alias in term["alt_ids"]}

    def ancestors(key):
        return set(active.get(key, {}).get("isa_partof_closure", []))

    memberships = {
        key: sorted(ancestors(aliases.get(key, key)) & selected) or [UNMAPPED_GO] for key in sorted(observed)
    }
    used = {value for values in memberships.values() for value in values}
    nodes = {}
    for key in sorted(used - {UNMAPPED_GO}):
        candidates = (ancestors(key) & used) - {key}
        distant = {ancestor for parent in candidates for ancestor in ancestors(parent) - {parent}}
        nodes[key] = {
            "label": active[key]["label"].replace("_", " "),
            "parents": sorted(candidates - distant),
        }
    if UNMAPPED_GO in used:
        nodes[UNMAPPED_GO] = {"label": "Unmapped / obsolete GO terms", "parents": []}
    return {
        "field": "go_slim",
        "label": "GO · generic slim",
        "nodes": nodes,
        "description": "Includes detailed terms beneath each category. Counts annotations, including NOT unless filtered out.",
        "subset": "goslim_generic",
        "predicates": ["is_a", "part_of"],
    }, memberships


def species_hierarchy(observed: dict[str, str], taxa: dict[str, tuple[str, list[str]]]) -> tuple[dict, dict]:
    """Observed taxa beneath curated clades; each attaches to its nearest displayed ancestor.

    ``taxa`` maps a taxon to its label and root-first lineage. An observed taxon
    without one stays a selectable root under its fallback label.
    """
    nodes, paths = {}, {}

    def place(key: str) -> set[str]:
        if key not in paths:
            if key in CLADES:
                label, parents = CLADES[key]
            else:
                label, ancestors = taxa.get(key, (observed[key] or key, []))
                parents = [a for a in reversed(ancestors) if a in CLADES or a in observed][:1]
            nodes[key] = {"label": label, "parents": parents}
            paths[key] = {key}.union(*(place(parent) for parent in parents))
        return paths[key]

    memberships = {key: sorted(place(key)) for key in sorted(observed)}
    return {
        "field": "taxon_groups",
        "label": "Species",
        "replaces": "species",
        "nodes": nodes,
        "description": "Selecting a group includes its loaded species. Counts reflect other active filters.",
        "source": "Curated NCBI Taxonomy subset; unlisted taxa remain selectable roots.",
    }, memberships


def index_hierarchies(db, terms: list[dict]) -> dict:
    go, go_memberships = go_hierarchy(
        terms, {row[0] for row in db.execute("SELECT DISTINCT ontology_class_id FROM annotations").fetchall()}
    )
    species, taxon_memberships = species_hierarchy(
        dict(
            db.execute(
                "SELECT taxon_id, min(species) FROM (SELECT taxon_id, species FROM genes "
                "UNION ALL SELECT taxon_id, species FROM annotations) GROUP BY taxon_id"
            ).fetchall()
        ),
        {taxon: (label, lineage) for taxon, label, lineage in db.execute("SELECT * FROM taxa").fetchall()},
    )
    product_types, product_memberships = product_type_hierarchy(
        {row[0] for row in db.execute("SELECT DISTINCT unnest(gene_product_types) FROM genes").fetchall()}
    )
    for field, memberships, collections, source, multivalued in (
        ("go_slim", go_memberships, ["annotations"], "ontology_class_id", False),
        ("taxon_groups", taxon_memberships, ["genes", "annotations"], "taxon_id", False),
        ("gene_product_type_groups", product_memberships, ["genes"], "gene_product_types", True),
    ):
        table = pa.table(
            {"source": list(memberships), "memberships": list(memberships.values())},
            schema=pa.schema(
                [
                    ("source", pa.string()),
                    ("memberships", pa.list_(pa.string())),
                ]
            ),
        )
        db.register("hierarchy_memberships", table)
        try:
            for collection in collections:
                db.execute(f'ALTER TABLE "{collection}" ADD COLUMN "{field}" VARCHAR[]')
                if multivalued:
                    db.execute(f'''UPDATE "{collection}" SET "{field}"=m.memberships FROM (
                        SELECT id, list_sort(list_distinct(flatten(list(h.memberships)))) AS memberships
                        FROM (SELECT id, unnest("{source}") AS value FROM "{collection}") s
                        JOIN hierarchy_memberships h ON s.value=h.source GROUP BY id
                    ) m WHERE "{collection}".id=m.id''')
                else:
                    db.execute(
                        f'UPDATE "{collection}" SET "{field}"=m.memberships '
                        f'FROM hierarchy_memberships m WHERE "{source}"=m.source'
                    )
        finally:
            db.unregister("hierarchy_memberships")
    for hierarchy in (go, species, product_types):
        if len(hierarchy["nodes"]) > 1000:
            raise ValueError(f"Hierarchy exceeds facet bucket limit: {hierarchy['field']}")
    return {"genes": [species, product_types], "annotations": [species, go]}
