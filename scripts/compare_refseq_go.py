#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.4,<2", "pyarrow>=19"]
# ///
"""Download a KT2440 pilot and compare RefSeq/UniProt GO on identical proteins.

Run: uv run scripts/compare_refseq_go.py
Inputs/results stay under data/refseq-pilot; this does not change the app build.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from amigo3.ontology import load_ontology  # noqa: E402

BASE = "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/007/565/GCF_000007565.2_ASM756v2/"
PREFIX = "GCF_000007565.2_ASM756v2"
SOURCES = {
    "ncbi.gaf.gz": BASE + PREFIX + "_gene_ontology.gaf.gz",
    "ncbi.faa.gz": BASE + PREFIX + "_protein.faa.gz",
    "assembly_report.txt": BASE + PREFIX + "_assembly_report.txt",
    "uniprot.gaf": "https://ftp.ebi.ac.uk/pub/databases/GO/goa/proteomes/109.P_putida_KT2440.goa",
    "uniprot.tsv": "https://rest.uniprot.org/uniprotkb/stream?"
    + urllib.parse.urlencode(
        {
            "query": "organism_id:160488",
            "format": "tsv",
            "fields": "accession,xref_refseq,sequence,gene_primary,protein_name",
        }
    ),
    "uniprot-geneids.tsv": "https://rest.uniprot.org/uniprotkb/stream?"
    + urllib.parse.urlencode(
        {"query": "organism_id:160488", "format": "tsv", "fields": "accession,xref_geneid"}
    ),
    "go-basic.obo": "https://current.geneontology.org/ontology/go-basic.obo",
}
COLUMNS = [
    "db",
    "accession",
    "symbol",
    "qualifier",
    "go_id",
    "reference",
    "evidence",
    "with_from",
    "aspect",
    "name",
    "synonyms",
    "object_type",
    "taxon",
    "annotation_date",
    "assigned_by",
    "extension",
    "form_id",
]


def fetch(folder: Path, refresh: bool) -> dict:
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    queue = list(SOURCES.items())
    for filename, url in queue:
        path = folder / filename
        old = manifest.get(filename, {})
        valid = (
            path.exists()
            and old.get("url") == url
            and old.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
        )
        if refresh or not valid:
            if filename.startswith("ncbi-gene-summary-"):
                time.sleep(0.4)  # NCBI E-utilities: at most three requests per second.
            print(f"Downloading {filename}", flush=True)
            request = urllib.request.Request(url, headers={"User-Agent": "amigo3-refseq-go-pilot/0.1"})
            with urllib.request.urlopen(request, timeout=180) as response:
                content = response.read()
                entry = {
                    "url": url,
                    "resolved_url": response.url,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "last_modified": response.headers.get("Last-Modified"),
                    "uniprot_release": response.headers.get("X-UniProt-Release"),
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
            manifest[filename] = entry
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        if filename == "uniprot-geneids.tsv":
            with path.open() as handle:
                ids = sorted(
                    {
                        i.strip()
                        for row in csv.DictReader(handle, delimiter="\t")
                        for i in row["GeneID"].split(";")
                        if i.strip()
                    }
                )
            for start in range(0, len(ids), 200):
                url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
                    {"db": "gene", "id": ",".join(ids[start : start + 200]), "retmode": "json"}
                )
                queue.append((f"ncbi-gene-summary-{start // 200:03d}.json", url))
    return manifest


def read_gaf(path: Path, source: str) -> tuple[list, list]:
    rows, headers = [], []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        for number, line in enumerate(handle, 1):
            if line.startswith("!"):
                headers.append(line.rstrip())
                continue
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) != 17 or fields[12] != "taxon:160488":
                raise ValueError(f"Unexpected GAF shape/taxon: {path}:{number}")
            rows.append([source, number, *fields])
    if not rows:
        raise ValueError(f"Empty GAF: {path}")
    return rows, headers


def read_fasta(path: Path) -> list:
    records = []
    accession, name, sequence = None, "", []
    with gzip.open(path, "rt") as handle:
        for line in handle:
            if line.startswith(">"):
                if accession:
                    records.append(["ncbi", accession, "".join(sequence), "", name, ""])
                accession, _, name = line[1:].strip().partition(" ")
                sequence = []
            else:
                sequence.append(line.strip())
    if accession:
        records.append(["ncbi", accession, "".join(sequence), "", name, ""])
    return records


def dictionaries(con, sql: str) -> list[dict]:
    cursor = con.execute(sql)
    columns = [x[0] for x in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def insert_rows(con, table: str, rows: list) -> None:
    """Bulk insert column lists; table names are internal constants."""
    if rows:
        columns = list(zip(*rows, strict=True))
        incoming = pa.table({f"c{i}": list(column) for i, column in enumerate(columns)})
        con.register("incoming", incoming)
        con.execute(f"INSERT INTO {table} SELECT * FROM incoming")
        con.unregister("incoming")


def build(folder: Path, manifest: dict) -> dict:
    # Rebuild only this experiment's derived database; keep verified inputs.
    db_path = folder / f"comparison-{uuid4().hex}.tmp.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("BEGIN TRANSACTION")
    con.execute(
        "CREATE TABLE annotations (source VARCHAR, source_line INTEGER, "
        + ", ".join(f'"{column}" VARCHAR' for column in COLUMNS)
        + ")"
    )
    headers = {}
    for source, filename in [("ncbi", "ncbi.gaf.gz"), ("uniprot", "uniprot.gaf")]:
        rows, headers[source] = read_gaf(folder / filename, source)
        insert_rows(con, "annotations", rows)
    con.execute(
        "CREATE TABLE proteins (source VARCHAR, accession VARCHAR, sequence VARCHAR, symbol VARCHAR, name VARCHAR, refseq_xrefs VARCHAR)"
    )
    proteins = read_fasta(folder / "ncbi.faa.gz")
    with (folder / "uniprot.tsv").open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            proteins.append(
                [
                    "uniprot",
                    row["Entry"],
                    row["Sequence"],
                    row["Gene Names (primary)"],
                    row["Protein names"],
                    row["RefSeq"],
                ]
            )
    insert_rows(con, "proteins", proteins)
    con.execute("""
        CREATE TABLE sequence_candidates AS
        SELECT n.accession refseq, u.accession uniprot,
            count(*) OVER (PARTITION BY n.accession) uniprot_count,
            count(*) OVER (PARTITION BY u.accession) refseq_count,
            list_contains(string_split(u.refseq_xrefs, ';'), n.accession) explicit_xref
        FROM proteins n JOIN proteins u ON n.sequence=u.sequence AND length(n.sequence)>0
        WHERE n.source='ncbi' AND u.source='uniprot';
        CREATE TABLE protein_map AS SELECT * FROM sequence_candidates
        WHERE uniprot_count=1 AND refseq_count=1;
    """)
    nodes, _, release = load_ontology(folder / "go-basic.obo")
    con.execute(
        "CREATE TABLE terms (go_id VARCHAR PRIMARY KEY, label VARCHAR, aspect VARCHAR, obsolete BOOLEAN)"
    )
    insert_rows(
        con,
        "terms",
        [(n["id"], n["label"], n["aspect"], n["obsolete"]) for n in nodes],
    )
    con.execute("CREATE TABLE term_ids (original_id VARCHAR PRIMARY KEY, canonical_id VARCHAR)")
    insert_rows(con, "term_ids", [(i, n["id"]) for n in nodes for i in [n["id"], *n["alt_ids"]]])
    con.execute("CREATE TABLE ancestors (go_id VARCHAR, ancestor VARCHAR)")
    ancestry = [(n["id"], a) for n in nodes if not n["obsolete"] for a in n["isa_partof_closure"]]
    insert_rows(con, "ancestors", ancestry)
    con.execute("""
        CREATE VIEW normalized AS SELECT a.*, coalesce(i.canonical_id,a.go_id) term,
            contains('|' || qualifier || '|', '|NOT|') negated,
            t.go_id IS NULL unknown_term, coalesce(t.obsolete,false) obsolete
        FROM annotations a LEFT JOIN term_ids i ON a.go_id=i.original_id
        LEFT JOIN terms t ON i.canonical_id=t.go_id;
        CREATE VIEW matched AS
        SELECT a.*, m.uniprot protein FROM normalized a JOIN protein_map m ON a.accession=m.refseq WHERE source='ncbi'
        UNION ALL
        SELECT a.*, m.uniprot protein FROM normalized a JOIN protein_map m ON a.accession=m.uniprot WHERE source='uniprot';
        CREATE TABLE pairs AS SELECT DISTINCT source, protein, term, aspect FROM matched
        WHERE NOT negated AND NOT unknown_term AND NOT obsolete;
        CREATE VIEW differences AS
        SELECT coalesce(n.protein,u.protein) protein, coalesce(n.term,u.term) term,
            coalesce(n.aspect,u.aspect) aspect,
            CASE WHEN n.protein IS NULL THEN 'uniprot_only' WHEN u.protein IS NULL THEN 'ncbi_only' ELSE 'shared' END status
        FROM (SELECT * FROM pairs WHERE source='ncbi') n
        FULL OUTER JOIN (SELECT * FROM pairs WHERE source='uniprot') u USING (protein,term,aspect);
        CREATE VIEW relation_differences AS
        WITH p AS (SELECT DISTINCT source,protein,term,qualifier,extension,form_id FROM matched
            WHERE NOT negated AND NOT unknown_term AND NOT obsolete)
        SELECT coalesce(n.protein,u.protein) protein, coalesce(n.term,u.term) term,
            CASE WHEN n.protein IS NULL THEN 'uniprot_only' WHEN u.protein IS NULL THEN 'ncbi_only' ELSE 'shared' END status
        FROM (SELECT * FROM p WHERE source='ncbi') n FULL OUTER JOIN (SELECT * FROM p WHERE source='uniprot') u
        USING (protein,term,qualifier,extension,form_id);
        CREATE VIEW ncbi_candidates AS
        SELECT d.*, t.label, p.symbol, p.name,
            EXISTS (SELECT 1 FROM pairs u JOIN ancestors a ON u.term=a.go_id
                    WHERE u.source='uniprot' AND u.protein=d.protein AND a.ancestor=d.term) covered_by_uniprot_descendant,
            EXISTS (SELECT 1 FROM pairs u JOIN ancestors a ON u.term=a.ancestor
                    WHERE u.source='uniprot' AND u.protein=d.protein AND a.go_id=d.term) has_uniprot_ancestor
        FROM differences d JOIN terms t ON d.term=t.go_id
        JOIN proteins p ON d.protein=p.accession AND p.source='uniprot' WHERE d.status='ncbi_only';
    """)
    queries = {
        "source_counts": "SELECT source,count(*) annotation_rows,count(DISTINCT accession) proteins,count(DISTINCT go_id) terms FROM annotations GROUP BY source ORDER BY source",
        "catalog_counts": "SELECT source,count(*) proteins FROM proteins GROUP BY source ORDER BY source",
        "mapping": "SELECT count(*) unambiguous_identical_pairs,count(*) FILTER (WHERE explicit_xref) also_explicit_xref FROM protein_map",
        "ambiguous_mapping": "SELECT count(DISTINCT refseq) refseq,count(DISTINCT uniprot) uniprot FROM sequence_candidates WHERE refseq_count>1 OR uniprot_count>1",
        "annotation_mapping": "SELECT source,count(*) annotation_rows,count(DISTINCT accession) proteins FROM matched GROUP BY source ORDER BY source",
        "excluded": "SELECT source,count(*) FILTER(WHERE negated) negated_rows,count(*) FILTER(WHERE obsolete) obsolete_rows,count(*) FILTER(WHERE unknown_term) unknown_rows,count(*) FILTER(WHERE symbol='') missing_symbol_rows FROM normalized GROUP BY source ORDER BY source",
        "pair_overlap": "SELECT status,count(*) pairs,count(DISTINCT protein) proteins FROM differences GROUP BY status ORDER BY status",
        "aspect_overlap": "SELECT aspect,status,count(*) pairs FROM differences GROUP BY aspect,status ORDER BY aspect,status",
        "relation_overlap": "SELECT status,count(*) assertions FROM relation_differences GROUP BY status ORDER BY status",
        "ncbi_novelty": "SELECT covered_by_uniprot_descendant,has_uniprot_ancestor,count(*) pairs,count(DISTINCT protein) proteins FROM ncbi_candidates GROUP BY ALL ORDER BY ALL",
        "methods": "SELECT source,evidence,assigned_by,split_part(with_from,':',1) with_prefix,count(*) annotation_rows FROM annotations GROUP BY ALL ORDER BY source,annotation_rows DESC",
        "novel_methods": "SELECT split_part(a.with_from,':',1) with_prefix,count(DISTINCT (d.protein,d.term)) pairs FROM ncbi_candidates d JOIN matched a ON a.source='ncbi' AND a.protein=d.protein AND a.term=d.term WHERE NOT d.covered_by_uniprot_descendant GROUP BY ALL ORDER BY pairs DESC",
        "newly_annotated": "SELECT count(DISTINCT n.protein) proteins FROM pairs n WHERE n.source='ncbi' AND NOT EXISTS (SELECT 1 FROM pairs u WHERE u.source='uniprot' AND u.protein=n.protein)",
    }
    report = {
        "assembly": PREFIX,
        "taxon": 160488,
        "ontology_release": release,
        "headers": headers,
        "sources": manifest,
        "metrics": {key: dictionaries(con, sql) for key, sql in queries.items()},
    }
    for name, sql in {
        "ncbi_candidates": "SELECT * FROM ncbi_candidates ORDER BY covered_by_uniprot_descendant,protein,term",
        "differences": "SELECT * FROM differences ORDER BY status,protein,term",
        "candidate_evidence": "SELECT d.protein,d.term,d.label,d.covered_by_uniprot_descendant,d.has_uniprot_ancestor,a.accession,a.qualifier,a.evidence,a.reference,a.with_from,a.source_line FROM ncbi_candidates d JOIN matched a ON a.protein=d.protein AND a.term=d.term AND a.source='ncbi' ORDER BY d.protein,d.term",
    }.items():
        rows = dictionaries(con, sql)
        if rows:
            with (folder / f"{name}.tsv").open("w") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
    (folder / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    con.execute("COMMIT")
    con.close()
    db_path.replace(folder / "comparison.duckdb")
    return report


def write_preload(folder: Path, report: dict) -> None:
    """Export a portable GAF/mapping/catalog bundle for AmiGO's build adapter."""
    output = folder / "preload"
    output.mkdir(exist_ok=True)
    shutil.copyfile(folder / "ncbi.gaf.gz", output / "ncbi.gaf.gz")
    with duckdb.connect(str(folder / "comparison.duckdb"), read_only=True) as con:
        with (output / "mapping.tsv").open("w") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["source_id", "target_id", "taxon_id", "method"])
            for refseq, uniprot, explicit in con.execute(
                "SELECT refseq,uniprot,explicit_xref FROM sequence_candidates ORDER BY refseq,uniprot"
            ).fetchall():
                writer.writerow(
                    [
                        f"RefSeq:{refseq}",
                        f"UniProtKB:{uniprot}",
                        "NCBITaxon:160488",
                        "exact_sequence+explicit_xref" if explicit else "exact_sequence",
                    ]
                )
        with (output / "subjects.tsv").open("w") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["subject_id", "taxon_id"])
            for (accession,) in con.execute(
                "SELECT accession FROM proteins WHERE source='uniprot' ORDER BY accession"
            ).fetchall():
                writer.writerow([f"UniProtKB:{accession}", "NCBITaxon:160488"])
    gene_metadata = {}
    for filename in report["sources"]:
        if filename.startswith("ncbi-gene-summary-"):
            payload = json.loads((folder / filename).read_text())
            if "result" not in payload:
                raise ValueError(f"NCBI Gene summary failed: {filename}")
            gene_metadata.update({k: v for k, v in payload["result"].items() if k != "uids"})
    with (output / "gene_xrefs.tsv").open("w") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            ["subject_id", "taxon_id", "gene_id", "gene_taxon_id", "gene_label", "organism", "source"]
        )
        with (folder / "uniprot-geneids.tsv").open() as source:
            for row in csv.DictReader(source, delimiter="\t"):
                for identifier in row["GeneID"].split(";"):
                    identifier = identifier.strip()
                    if not identifier:
                        continue
                    gene = gene_metadata.get(identifier, {})
                    organism = gene.get("organism", {})
                    writer.writerow(
                        [
                            f"UniProtKB:{row['Entry']}",
                            "NCBITaxon:160488",
                            f"NCBIGene:{identifier}",
                            f"NCBITaxon:{organism['taxid']}" if organism.get("taxid") else "",
                            gene.get("name", ""),
                            organism.get("scientificname", ""),
                            "UniProt GeneID cross-reference; NCBI Gene ESummary",
                        ]
                    )
    provenance = {
        "assembly": report["assembly"],
        "taxon": report["taxon"],
        "inputs": report["sources"],
        "mapping_policy": "Exact full protein sequence equality within taxon 160488; all candidates retained so ambiguity is explicit",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/refseq-pilot"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = build(args.output, fetch(args.output, args.refresh))
    write_preload(args.output, report)
    print(json.dumps(report["metrics"], indent=2))
    print(f"Saved {args.output / 'comparison.duckdb'} and summary.json")


if __name__ == "__main__":
    main()
