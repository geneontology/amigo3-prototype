"""Independent gene sets from MyGeneset, mapped onto the pinned build.

These are study sets for GO enrichment, so they must not be derived from GO: GO
term sets (MyGeneset's own and MSigDB C5 GO subcollections) are never searched or
loaded. The default search collection is expression and perturbation signatures.

Bundled examples are frozen MSigDB signatures with the GO terms their curated
interpretation expects (see scripts/fetch_example_genesets.py), so the page has
examples without network access. Live search is an optional proxy to the
MyGeneset API. Either way, membership is MyGeneset's as published; this module only
maps members onto the loaded gene catalog, preferring stable identifiers (NCBI
Gene, UniProt, Ensembl/WormBase/PomBase) and using symbols last.
"""

from __future__ import annotations

import functools
from pathlib import Path

import duckdb
import httpx
import yaml

from amigo3.enrichment import resolve_genes

DEFAULT_URL = "https://mygeneset.info/v1"
EXAMPLES = Path(__file__).parent / "example_genesets.yaml"
# MyGeneset files fission yeast under both the species and the reference strain.
RELATED_TAXA = {"4896": ["284812"], "284812": ["4896"]}
SUMMARY_FIELDS = "name,source,count,taxid,description,msigdb.category.code,msigdb.subcategory.code,msigdb.url"
NOT_GO = "NOT source:go AND NOT msigdb.subcategory.code:go*"
COLLECTIONS = {
    "signatures": (
        "Expression & perturbation signatures (MSigDB H, C2:CGP, C4, C6, C7, C8)",
        "(msigdb.category.code:(h OR c4 OR c6 OR c7 OR c8) OR msigdb.subcategory.code:cgp)",
    ),
    "regulatory": (
        "Regulatory targets (MSigDB C3: transcription factor and miRNA targets)",
        "msigdb.category.code:c3",
    ),
    "chemicals": ("Chemical-gene interactions (CTD)", "source:ctd"),
    "disease": (
        "Disease and phenotype (Disease Ontology, MSigDB HPO)",
        "(source:do OR msigdb.subcategory.code:hpo)",
    ),
    "pathways": (
        "Pathways (Reactome, WikiPathways, SMPDB, MSigDB C2:CP)",
        "(source:(reactome OR wikipathways OR smpdb) OR msigdb.subcategory.code:cp*)",
    ),
    "all": ("All non-GO sources", "*"),
}
SOURCES = {"msigdb": "MSigDB", "reactome": "Reactome", "wikipathways": "WikiPathways", "ctd": "CTD"}
SOURCES.update(do="Disease Ontology", smpdb="SMPDB")


def go_derived(doc: dict) -> bool:
    subcategory = (((doc.get("msigdb") or {}).get("subcategory") or {}).get("code") or "").lower()
    return doc.get("source") == "go" or subcategory.startswith("go")


def collection_label(doc: dict) -> str:
    msigdb = doc.get("msigdb") or {}
    category = ((msigdb.get("category") or {}).get("code") or "").upper()
    subcategory = ((msigdb.get("subcategory") or {}).get("code") or "").upper()
    if doc.get("source") == "msigdb" and category:
        return "MSigDB " + category + (":" + subcategory if subcategory else "")
    return SOURCES.get(doc.get("source"), doc.get("source") or "")


def as_list(value) -> list:
    if value is None or value == "":
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def member_candidates(member: dict) -> list[str]:
    candidates = [f"NCBIGene:{x}" for x in as_list(member.get("ncbigene"))]
    candidates += [f"UniProtKB:{x}" for x in as_list(member.get("uniprot"))]
    for gene in as_list(member.get("ensemblgene")):
        if gene.startswith("WBGene"):
            candidates.append("WB:" + gene)
        elif gene.startswith("ENS"):
            candidates.append("ENSEMBL:" + gene)
        else:  # PomBase systematic IDs are published in this field.
            candidates.append("PomBase:" + gene)
    candidates += as_list(member.get("symbol"))
    candidates += [x for x in as_list(member.get("source_id")) if not x.isdigit()]
    return list(dict.fromkeys(candidates))


def source_url(doc: dict) -> str:
    source = doc.get("source")
    if source == "wikipathways":
        return (doc.get("wikipathways") or {}).get(
            "url"
        ) or f"https://www.wikipathways.org/pathways/{doc['_id']}"
    if source == "reactome":
        return f"https://reactome.org/content/detail/{doc['_id']}"
    if source == "msigdb":
        return f"https://www.gsea-msigdb.org/gsea/msigdb/human/geneset/{doc['_id']}.html"
    return ""


def summary(doc: dict, origin: str) -> dict:
    source = doc.get("source") or ""
    return {
        "id": doc["_id"],
        # MSigDB names are lower-cased systematic IDs; the ID is the familiar form.
        "name": doc["_id"] if source == "msigdb" else doc.get("name") or doc["_id"],
        "description": doc.get("description") or "",
        "source": source,
        "collection": collection_label(doc),
        "taxid": str(doc.get("taxid") or ""),
        "count": doc.get("count") or 0,
        "url": source_url(doc),
        "origin": origin,
        "expected_go": doc.get("expected_go") or [],
    }


@functools.cache
def examples() -> dict:
    return yaml.safe_load(EXAMPLES.read_text())


def species_taxa(db, species: str) -> list[str]:
    taxa = [
        r[0].removeprefix("NCBITaxon:")
        for r in db.execute(
            "SELECT DISTINCT taxon_id FROM genes WHERE species=? AND taxon_id LIKE 'NCBITaxon:%'", [species]
        ).fetchall()
    ]
    return sorted({t for taxon in taxa for t in (taxon, *RELATED_TAXA.get(taxon, ()))})


def map_members(db, species: str, members: list[dict]) -> dict:
    """Map each member by its first candidate that resolves to exactly one gene."""
    candidates = [member_candidates(m) for m in members]
    resolved = resolve_genes(db, species, [c for cs in candidates for c in cs])
    by_input = {m["input"].upper(): m for m in resolved["matched"]}
    rows = []
    for member, options in zip(members, candidates, strict=True):
        match = next(((c, by_input[c.upper()]) for c in options if c.upper() in by_input), None)
        label = as_list(member.get("symbol"))[:1] or [c for c in options if ":" not in c][:1] or options[:1]
        rows.append(
            {
                "label": label[0] if label else "",
                "gene": {"id": match[1]["id"], "symbol": match[1]["symbol"]} if match else None,
                "via": match[0] if match else "",
            }
        )
    # Prefer readable symbols in the study list, unless a symbol would resolve elsewhere.
    mapped = {r["gene"]["id"]: r["gene"]["symbol"] for r in rows if r["gene"]}
    check = resolve_genes(db, species, [s for s in mapped.values() if s])
    back = {m["input"].upper(): m["id"] for m in check["matched"]}
    genes, seen = [], set()
    for row in rows:
        token = row["label"]
        if row["gene"]:
            gene_id, symbol = row["gene"]["id"], row["gene"]["symbol"]
            token = symbol if symbol and back.get(symbol.upper()) == gene_id else gene_id
        if token and token.upper() not in seen:
            seen.add(token.upper())
            genes.append(token)
    return {
        "members": rows,
        "genes": genes,
        "mapped": len(mapped),
        "unmapped": sum(1 for r in rows if not r["gene"]),
    }


class GenesetClient:
    def __init__(self, base_url: str | None = DEFAULT_URL, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            raise LookupError("Live gene set search is disabled on this server")
        with httpx.Client(base_url=self.base_url, timeout=15, transport=self.transport) as client:
            response = client.get(path, params=params)
            if response.status_code == 404:
                raise KeyError(path)
            return response.raise_for_status().json()

    def search(self, query: str, taxa: list[str], collection: str = "signatures", size: int = 20) -> dict:
        # GO-derived sets would make GO enrichment circular; empty sets are useless.
        scope = COLLECTIONS[collection][1]
        params = {
            "q": f"({query}) AND {scope} AND {NOT_GO} AND count:>0",
            "size": size,
            "fields": SUMMARY_FIELDS,
        }
        if taxa:
            params["species"] = ",".join(taxa)
        return self._get("/query", params)

    def get(self, identifier: str) -> dict:
        return self._get(f"/geneset/{identifier}")


def options(path: Path, species: str, client: GenesetClient) -> dict:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        taxa = species_taxa(db, species)
    data = examples()
    return {
        "species": species,
        "taxa": taxa,
        "examples": [summary(doc, "bundled") for doc in data["genesets"] if str(doc.get("taxid")) in taxa],
        "examples_provenance": data["provenance"],
        "search": {"enabled": client.enabled, "service": client.base_url or None},
        "collections": {key: label for key, (label, _) in COLLECTIONS.items()},
    }


def search(
    path: Path,
    species: str,
    query: str,
    client: GenesetClient,
    collection: str = "signatures",
    size: int = 20,
) -> dict:
    if collection not in COLLECTIONS:
        raise ValueError(f"Unknown collection {collection!r}")
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        taxa = species_taxa(db, species)
    if not taxa:
        return {"total": 0, "hits": []}
    found = client.search(query, taxa, collection, size)
    return {
        "total": found.get("total", 0),
        "hits": [summary(hit, "mygeneset") for hit in found.get("hits", []) if not go_derived(hit)],
        "service": client.base_url,
    }


def geneset(path: Path, species: str, identifier: str, client: GenesetClient) -> dict:
    bundled = {doc["_id"]: doc for doc in examples()["genesets"]}
    if identifier in bundled:
        doc, origin = bundled[identifier], "bundled"
    else:
        doc, origin = client.get(identifier), "mygeneset"
    if go_derived(doc):
        raise ValueError(
            "GO-derived gene sets are not offered: GO enrichment of a GO term's own genes is circular"
        )
    members = doc.get("genes") or []
    members = members if isinstance(members, list) else [members]
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        taxa = species_taxa(db, species)
        mapping = map_members(db, species, members)
    result = {**summary(doc, origin), **mapping, "member_count": len(members)}
    result["species_match"] = result["taxid"] in taxa
    if origin == "bundled":
        result["provenance"] = examples()["provenance"]
    return result
