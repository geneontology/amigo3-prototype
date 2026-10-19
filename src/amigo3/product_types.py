"""Project supplied GPI/GAF product types onto integrated gene/product records.

Vocabulary: https://geneontology.org/docs/gene-product-information-gpi-format/
SO labels: https://github.com/The-Sequence-Ontology/SO-Ontologies
No type is inferred from an identifier namespace, gene symbol, or GO annotation.
"""

PRODUCT_TYPES = {
    "PR:000000001": ("protein", "Protein"),
    "GO:0032991": ("protein_complex", "Complex"),
    "SO:0000253": ("tRNA", "tRNA"),
    "SO:0000252": ("rRNA", "rRNA"),
    "SO:0000274": ("snRNA", "snRNA"),
    "SO:0000275": ("snoRNA", "snoRNA"),
    "SO:0000276": ("miRNA", "miRNA"),
    "SO:0000655": ("ncRNA", "ncRNA"),
    "SO:0001035": ("piRNA", "piRNA"),
    "SO:0001877": ("lncRNA", "lncRNA"),
    "SO:0001463": ("lincRNA", "lincRNA"),
    "SO:0001904": ("antisense_lncRNA", "Antisense lncRNA"),
    "SO:0000590": ("SRP_RNA", "SRP RNA"),
    "SO:0000372": ("enzymatic_RNA", "Enzymatic RNA"),
}
GAF_TYPES = {name: identifier for identifier, (name, _) in PRODUCT_TYPES.items()}

# Nearest displayed is_a ancestors in SO 2026-08-07. Intermediate classes such
# as sncRNA and small_regulatory_ncRNA are omitted in this navigation subset.
# Source: SO-Ontologies/Ontology_Files/so.obo (see module documentation).
NCRNA = "SO:0000655"
LNCRNA = "SO:0001877"
PRODUCT_TYPE_PARENTS = {
    "SO:0000253": [NCRNA],  # tRNA via sncRNA
    "SO:0000252": [NCRNA],  # rRNA
    "SO:0000274": [NCRNA],  # snRNA via sncRNA
    "SO:0000275": [NCRNA],  # snoRNA via sncRNA
    "SO:0000276": [NCRNA],  # miRNA via small_regulatory_ncRNA and sncRNA
    "SO:0001035": [NCRNA],  # piRNA via small_regulatory_ncRNA and sncRNA
    LNCRNA: [NCRNA],
    "SO:0001463": [LNCRNA],  # lincRNA
    "SO:0001904": [LNCRNA],  # antisense_lncRNA; the antisense_RNA parent is omitted
    "SO:0000590": [NCRNA],  # SRP_RNA
    "SO:0000372": [NCRNA],  # enzymatic_RNA
}
UNSPECIFIED_PRODUCT_TYPE = "unspecified-product-type"


def product_type_hierarchy(observed: set[str]) -> tuple[dict, dict]:
    """Return a small is_a hierarchy and reflexive closure for each supplied type."""
    by_label = {label: identifier for identifier, (_, label) in PRODUCT_TYPES.items()}
    by_label["Unspecified"] = UNSPECIFIED_PRODUCT_TYPE
    nodes, memberships = {}, {}

    def visit(identifier, fallback):
        parents = PRODUCT_TYPE_PARENTS.get(identifier, [])
        label = PRODUCT_TYPES.get(identifier, (None, fallback))[1]
        nodes[identifier] = {"label": label, "parents": parents}
        return {identifier} | {ancestor for parent in parents for ancestor in visit(parent, parent)}

    for label in sorted(observed):
        # Unrecognized types stay selectable roots, without inferred ancestry.
        identifier = by_label.get(label, label)
        memberships[label] = sorted(visit(identifier, label))
    return {
        "field": "gene_product_type_groups",
        "label": "Gene product type",
        "replaces": "gene_product_types",
        "nodes": nodes,
        "description": "Selecting a group includes its subtypes. Counts unique genes/products, including types supplied only at the parent level.",
        "source": "Sequence Ontology 2026-08-07 is_a navigation subset; intermediate classes omitted.",
        "source_url": "https://github.com/The-Sequence-Ontology/SO-Ontologies/blob/master/Ontology_Files/so.obo",
        "predicates": ["is_a"],
    }, memberships


def normalize_product_type(value: str) -> tuple[str | None, str]:
    """Return a vocabulary CURIE and display label; preserve unfamiliar source types."""
    value = value.strip()
    identifier = GAF_TYPES.get(value, value)
    if identifier in PRODUCT_TYPES:
        return identifier, PRODUCT_TYPES[identifier][1]
    # "gene" supplies no product type; it must not turn into a biological category.
    if value in ("", "gene", "gene_product", "SO:0000704"):
        return None, "Unspecified"
    return (identifier if ":" in identifier else None), value


def index_product_types(db):
    """Retain type provenance and aggregate distinct types, counting each record once."""
    rows = db.execute("""SELECT DISTINCT s.gene_id, e.id, e.object_type, e.source_id, 'gpi'
        FROM entities e JOIN subject_gene s ON e.id=s.subject_id
        UNION
        SELECT DISTINCT gene_id, db_object_id, object_type, source_id, 'gaf'
        FROM annotation_subjects""").fetchall()
    db.execute("""CREATE TABLE gene_product_type_sources (
        gene_id VARCHAR, subject_id VARCHAR, source_type VARCHAR, source_id VARCHAR,
        source_format VARCHAR, type_id VARCHAR, product_type VARCHAR)""")
    if rows:
        # Arrow keeps this bulk insert fast on complete upstream catalogs.
        import pyarrow as pa

        columns = [
            "gene_id",
            "subject_id",
            "source_type",
            "source_id",
            "source_format",
            "type_id",
            "product_type",
        ]
        table = pa.Table.from_pylist(
            [dict(zip(columns, (*row, *normalize_product_type(row[2])), strict=True)) for row in rows],
            schema=pa.schema([(name, pa.string()) for name in columns]),
        )
        db.register("product_types_batch", table)
        db.execute("INSERT INTO gene_product_type_sources SELECT * FROM product_types_batch")
        db.unregister("product_types_batch")
    db.execute("""CREATE TABLE gene_product_types AS SELECT gene_id,
        list_sort(list(DISTINCT type_id) FILTER (WHERE type_id IS NOT NULL)) AS gene_product_type_ids,
        list_sort(list(DISTINCT product_type) FILTER (WHERE product_type <> 'Unspecified')) AS gene_product_types
        FROM gene_product_type_sources GROUP BY gene_id""")
