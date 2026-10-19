"""Frozen external GAF sets and protein-level comparisons against the build baseline."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa
import yaml

from amigo3.parse import gaf_rows, normalize_id, source_header
from amigo3.sources import digest

STATUSES = {
    "exact": "Exact term match",
    "covered": "Covered by a more specific baseline term",
    "refinement": "Candidate refinement",
    "unmatched": "Other unmatched term",
    "negative": "Negative assertion",
    "obsolete": "Obsolete GO term",
    "unknown_term": "Unknown GO term",
    "unmapped": "Unmapped protein",
    "ambiguous": "Ambiguous protein mapping",
    "out_of_scope": "Outside the subject catalog",
    "baseline_unavailable": "Baseline unavailable",
}
FILES = ("gaf", "mapping", "subjects", "provenance", "gene_xrefs")

FACETS = {
    "status": "Comparison category",
    "aspect": "GO aspect",
    "mapping_status": "Protein mapping",
    "model_type": "Model identifier type",
    "evidence_code": "Evidence code",
}


def model_type(value: str) -> str:
    prefixes = {
        "BlastRule"
        if re.fullmatch(r"NBR\d+", part)
        else part.split(":", 1)[0]
        if ":" in part
        else "Unclassified"
        for part in re.split(r"[|,]", value.strip())
        if part
    }
    return next(iter(prefixes)) if len(prefixes) == 1 else "Multiple" if prefixes else "None"


def input_sets(config: Path | None) -> list[dict]:
    """Validate operator-configured local artifacts and capture their hashes."""
    if config is None:
        return []
    document = yaml.safe_load(config.read_text())
    if not isinstance(document, dict) or not isinstance(document.get("sets"), list):
        raise ValueError("External configuration must contain a sets list")
    result, seen = [], set()
    for entry in document["sets"]:
        if not isinstance(entry, dict):
            raise ValueError("Each external set must be an object")
        identifier = entry.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identifier) or identifier in seen:
            raise ValueError(f"Invalid or duplicate external set ID: {identifier}")
        seen.add(identifier)
        for key in ("label", "taxon", "species", "baseline_sources", "gaf", "subjects"):
            if not entry.get(key):
                raise ValueError(f"External set {identifier} requires {key}")
        if not isinstance(entry["baseline_sources"], list) or not all(
            isinstance(x, str) and x for x in entry["baseline_sources"]
        ):
            raise ValueError("baseline_sources must be a nonempty list of source IDs")
        metadata = {k: v for k, v in entry.items() if k not in FILES}
        metadata["taxon"] = normalize_id(metadata["taxon"])
        if not re.fullmatch(r"NCBITaxon:\d+", metadata["taxon"]):
            raise ValueError(f"Invalid taxon for {identifier}")
        files = {}
        for kind in FILES:
            if entry.get(kind):
                path = (config.parent / entry[kind]).resolve()
                files[kind] = {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
        result.append({"metadata": metadata, "files": files})
    return result


def signature(sets: list[dict]) -> list[dict]:
    return [
        {
            "metadata": s["metadata"],
            "files": {k: {f: v[f] for f in ("sha256", "bytes")} for k, v in s["files"].items()},
        }
        for s in sets
    ]


def tsv(path: Path, required: set[str]) -> list[dict]:
    with path.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name}: expected columns {sorted(required)}")
        return list(reader)


def resolve_subject(subject: str, mappings: dict, subjects: set[str]) -> tuple[str, str, list]:
    """Explicit mappings are authoritative; never fall back to symbols or gene IDs."""
    candidates = sorted(mappings.get(subject, {}))
    if not candidates and subject in subjects:
        return subject, "identity", [{"target_id": subject, "method": "same_identifier"}]
    evidence = [{"target_id": t, "methods": sorted(mappings[subject][t])} for t in candidates]
    if len(candidates) > 1:
        return "", "ambiguous", evidence
    if not candidates:
        return "", "unmapped", evidence
    target = candidates[0]
    return (target, "mapped", evidence) if target in subjects else (target, "out_of_scope", evidence)


def compare_claim(row: dict, baseline: list[dict], terms: dict, aliases: dict) -> dict:
    """Term specificity is is_a-only; full assertion equality is reported separately."""
    term = aliases.get(row["ontology_class_id"], row["ontology_class_id"])
    node = terms.get(term)
    eligible = [b for b in baseline if b["term"] in terms and not terms[b["term"]]["obsolete"]]
    positive = [b for b in eligible if not b["negation"]]
    exact = sorted({b["term"] for b in positive if b["term"] == term})
    descendants = sorted(
        {b["term"] for b in positive if b["term"] != term and term in terms[b["term"]]["is_a_closure"]}
    )
    ancestors = sorted(
        {b["term"] for b in positive if node and b["term"] != term and b["term"] in node["is_a_closure"]}
    )
    compatible = [
        b
        for b in eligible
        if b["term"] == term
        and b["relation"] == row["relation"]
        and b["annotation_extensions"] == row["annotation_extensions"]
        and b["interacting_taxon_id"] == row["interacting_taxon_id"]
    ]
    assertion_match = any(b["negation"] == row["negation"] for b in compatible)
    opposite = [b["id"] for b in compatible if b["negation"] != row["negation"]]
    status = (
        "unknown_term"
        if not node
        else "obsolete"
        if node["obsolete"]
        else "negative"
        if row["negation"]
        else "exact"
        if exact
        else "covered"
        if descendants
        else "refinement"
        if ancestors
        else "unmatched"
    )
    matched_terms = set(exact + descendants + ancestors)
    support = [b for b in eligible if (not b["negation"] and b["term"] in matched_terms) or b in compatible]
    return {
        "term_id": term,
        "term_label": node["label"] if node else term,
        "aspect": node["aspect"] if node else row["aspect"],
        "status": status,
        "exact_terms": exact,
        "more_specific_terms": descendants,
        "broader_terms": ancestors,
        "assertion_match": assertion_match,
        "opposite_assertion_ids": opposite,
        "baseline_matches": support,
    }


def load_sets(
    db, sets: list[dict], work: Path, manifest: dict, terms: list[dict], ontology_version: str
) -> dict:
    """Store external sets in separate tables without modifying baseline annotations."""
    db.execute("CREATE TABLE external_sets (id VARCHAR PRIMARY KEY, payload VARCHAR)")
    db.execute("""CREATE TABLE external_claims (id VARCHAR, set_id VARCHAR, status VARCHAR,
        aspect VARCHAR, subject VARCHAR, term VARCHAR, search_text VARCHAR, payload VARCHAR)""")
    term_map = {t["id"]: t for t in terms}
    aliases = {a: t["id"] for t in terms for a in [t["id"], *t["alt_ids"]]}
    gene_ids = defaultdict(set)
    if sets:
        for gene, identifiers in db.execute("SELECT id,identifiers FROM genes").fetchall():
            for identifier in identifiers:
                gene_ids[identifier].add(gene)
    summaries = []
    for spec in sets:
        meta, files = dict(spec["metadata"]), spec["files"]
        identifier, taxon = meta["id"], meta["taxon"]
        frozen = work / "external" / identifier
        frozen.mkdir(parents=True)
        paths, frozen_files = {}, {}
        for kind, info in files.items():
            source = Path(info["path"])
            name = kind + (
                ".gaf.gz"
                if kind == "gaf" and source.suffix == ".gz"
                else ".gaf"
                if kind == "gaf"
                else ".json"
                if kind == "provenance"
                else ".tsv"
            )
            target = frozen / name
            shutil.copyfile(source, target)
            if digest(target) != info["sha256"]:
                raise ValueError(f"External input changed during build: {identifier}/{kind}")
            paths[kind] = target
            frozen_files[kind] = {
                "path": target.relative_to(work).as_posix(),
                "sha256": info["sha256"],
                "bytes": info["bytes"],
            }
        headers = source_header(paths["gaf"])
        if "!gaf-version: 2.2" not in headers:
            raise ValueError(f"External set {identifier}: expected !gaf-version: 2.2")
        subjects = set()
        for row in tsv(paths["subjects"], {"subject_id", "taxon_id"}):
            if normalize_id(row["taxon_id"]) != taxon:
                raise ValueError(f"Subject catalog taxon mismatch in {identifier}")
            subject = normalize_id(row["subject_id"])
            if not subject or ":" not in subject:
                raise ValueError(f"Subject catalog requires prefixed identifiers in {identifier}")
            subjects.add(subject)
        gene_xrefs = defaultdict(list)
        if "gene_xrefs" in paths:
            for row in tsv(
                paths["gene_xrefs"],
                {"subject_id", "taxon_id", "gene_id", "gene_taxon_id", "gene_label", "organism", "source"},
            ):
                subject = normalize_id(row["subject_id"])
                if normalize_id(row["taxon_id"]) != taxon or subject not in subjects:
                    raise ValueError(f"Gene cross-reference outside subject scope in {identifier}")
                if not re.fullmatch(r"NCBIGene:\d+", row["gene_id"]):
                    raise ValueError(f"Invalid NCBI Gene identifier in {identifier}")
                gene_taxon = normalize_id(row["gene_taxon_id"])
                gene_xrefs[subject].append(
                    {
                        "id": row["gene_id"],
                        "taxon_id": gene_taxon,
                        "label": row["gene_label"],
                        "organism": row["organism"],
                        "source": row["source"],
                        "taxon_status": "same_taxon"
                        if gene_taxon == taxon
                        else "other_taxon"
                        if gene_taxon
                        else "unverified",
                    }
                )
        mappings = defaultdict(lambda: defaultdict(set))
        if "mapping" in paths:
            for row in tsv(paths["mapping"], {"source_id", "target_id", "taxon_id", "method"}):
                if normalize_id(row["taxon_id"]) != taxon:
                    raise ValueError(f"Mapping taxon mismatch in {identifier}")
                source, target = normalize_id(row["source_id"]), normalize_id(row["target_id"])
                if ":" not in source or ":" not in target or not row["method"]:
                    raise ValueError(f"Mapping requires prefixed IDs and a method in {identifier}")
                mappings[source][target].add(row["method"])
        baseline_sources = meta["baseline_sources"]
        available = all(s in manifest and manifest[s]["kind"] == "gaf" for s in baseline_sources)
        for source_id in baseline_sources:
            source = manifest.get(source_id, {})
            allowed = source.get("allowed_taxa") or ([source["taxon"]] if source.get("taxon") else [])
            if allowed and taxon not in {normalize_id(t) for t in allowed}:
                available = False
        baseline = defaultdict(list)
        if available:
            columns = "id,db_object_id,ontology_class_id,relation,negation,annotation_extensions,interacting_taxon_id,evidence_code,references,with_or_from,source_id"
            projection = ",".join(f'"{column}"' for column in columns.split(","))
            for b in (
                db.execute(
                    f"SELECT {projection} FROM annotations WHERE source_id IN (SELECT unnest(?)) AND taxon_id=?",
                    [baseline_sources, taxon],
                )
                .to_arrow_table()
                .to_pylist()
            ):
                b["term"] = aliases.get(b["ontology_class_id"], b["ontology_class_id"])
                b["label"] = term_map.get(b["term"], {}).get("label", b["term"])
                baseline[b["db_object_id"]].append(b)
        descriptor = {
            "id": identifier,
            "sha256": files["gaf"]["sha256"],
            "species": meta["species"],
            "taxon": taxon,
        }
        claims = []
        resolutions = {}
        for row in gaf_rows(paths["gaf"], descriptor, {}):
            source_subject = row["db_object_id"]
            if source_subject not in resolutions:
                resolutions[source_subject] = resolve_subject(source_subject, mappings, subjects)
            target, mapping_status, mapping_evidence = resolutions[source_subject]
            comparable = available and mapping_status in ("mapped", "identity")
            result = compare_claim(row, baseline.get(target, []) if comparable else [], term_map, aliases)
            if mapping_status in ("unmapped", "ambiguous", "out_of_scope"):
                result["status"] = mapping_status
            elif not available:
                result["status"] = "baseline_unavailable"
            genes = gene_ids.get(target, set())
            claims.append(
                {
                    **row,
                    **result,
                    "set_id": identifier,
                    "source_subject_id": source_subject,
                    "target_subject_id": target,
                    "mapping_status": mapping_status,
                    "mapping_evidence": mapping_evidence,
                    "gene_id": next(iter(genes)) if len(genes) == 1 else "",
                    "ncbi_gene_xrefs": gene_xrefs.get(target, [])
                    if mapping_status in ("mapped", "identity")
                    else [],
                    "model_type": model_type(row["with_or_from"]),
                    "term_available": result["term_id"] in term_map,
                }
            )
        compared = [c for c in claims if c["status"] in {"exact", "covered", "refinement", "unmatched"}]
        pairs = {(c["target_subject_id"], c["term_id"]) for c in compared}
        targets = {c["target_subject_id"] for c in compared}
        baseline_pairs = {
            (t, b["term"])
            for t in targets
            for b in baseline[t]
            if not b["negation"] and b["term"] in term_map and not term_map[b["term"]]["obsolete"]
        }
        new_subjects = targets - {p[0] for p in baseline_pairs}
        summary = {
            "input_rows": len(claims),
            "input_subjects": len(resolutions),
            "subject_catalog_size": len(subjects),
            "mapping_subject_counts": dict(Counter(r[1] for r in resolutions.values())),
            "status_row_counts": dict(Counter(c["status"] for c in claims)),
            "status_pair_counts": {
                status: len(
                    {(c["target_subject_id"], c["term_id"]) for c in compared if c["status"] == status}
                )
                for status in ("exact", "covered", "refinement", "unmatched")
            },
            "comparable_proteins": len(targets),
            "comparable_pairs": len(pairs),
            "shared_pairs": len(pairs & baseline_pairs),
            "external_only_pairs": len(pairs - baseline_pairs),
            "baseline_only_pairs": len(baseline_pairs - pairs),
            "baseline_pairs_on_comparable_proteins": len(baseline_pairs),
            "proteins_without_baseline_annotations": len(new_subjects),
            "assertion_match_rows": sum(c["assertion_match"] for c in claims),
            "opposite_assertion_rows": sum(bool(c["opposite_assertion_ids"]) for c in claims),
            "missing_symbol_rows": sum(not c["symbol"] for c in claims),
        }
        payload = {
            **meta,
            "summary": summary,
            "files": frozen_files,
            "headers": headers,
            "baseline_available": available,
            "ontology_version": ontology_version,
            "baseline_sources": {
                s: {**manifest[s], "headers": source_header(work.parent.parent / manifest[s]["path"])}
                for s in baseline_sources
                if s in manifest
            },
            "provenance": json.loads(paths["provenance"].read_text()) if "provenance" in paths else {},
            "policy": {
                "subject_unit": "Exact mapped protein/product; no gene-level pooling",
                "baseline": "Positive active GO terms from the selected baseline sources and taxon; no review actions applied",
                "ancestry": "is_a only; term coverage is separate from assertion equality",
                "denominator": "Proteins with mapped, active positive external claims; baseline-only pairs use this same cohort",
                "assertion_equality": "Mapped product, canonical GO term, relation, polarity, interacting taxa and literal extension; evidence is retained separately",
            },
        }
        db.execute("INSERT INTO external_sets VALUES (?,?)", [identifier, json.dumps(payload)])
        if claims:
            table = pa.Table.from_pylist(
                [
                    {
                        "id": c["id"],
                        "set_id": identifier,
                        "status": c["status"],
                        "aspect": c["aspect"],
                        "subject": c["source_subject_id"],
                        "term": c["term_id"],
                        "search_text": " ".join(
                            str(c.get(k, ""))
                            for k in (
                                "source_subject_id",
                                "target_subject_id",
                                "symbol",
                                "name",
                                "term_id",
                                "term_label",
                                "with_or_from",
                            )
                        ),
                        "payload": json.dumps(c),
                    }
                    for c in claims
                ]
            )
            db.register("external_batch", table)
            db.execute("INSERT INTO external_claims SELECT * FROM external_batch")
            db.unregister("external_batch")
        summaries.append({"id": identifier, **summary})
    for name in ("external_sets", "external_claims"):
        destination = str((work / f"{name}.parquet").resolve()).replace("'", "''")
        db.execute(
            f"COPY (SELECT * FROM {name} ORDER BY id) TO '{destination}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    return {"sets": summaries}


def list_sets(path: Path) -> list[dict]:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        if not db.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='external_sets'"
        ).fetchone()[0]:
            return []
        return [
            json.loads(r[0]) for r in db.execute("SELECT payload FROM external_sets ORDER BY id").fetchall()
        ]


def claim_rows(
    path: Path,
    identifier: str | None,
    status: str = "",
    aspect: str = "",
    q: str = "",
    offset: int = 0,
    size: int | None = 50,
    *,
    mapping_status: str = "",
    model_type: str = "",
    evidence_code: str = "",
    gene: str = "",
    include_facets: bool = True,
) -> dict:
    """OR within each facet, AND across facets; counts exclude their own filter."""
    selected = {
        k: sorted(set(v.split(",")) - {""})
        for k, v in {
            "status": status,
            "aspect": aspect,
            "mapping_status": mapping_status,
            "model_type": model_type,
            "evidence_code": evidence_code,
        }.items()
    }
    if any(v not in STATUSES for v in selected["status"]):
        raise ValueError("Unknown comparison status")
    if any(v not in ("F", "P", "C") for v in selected["aspect"]):
        raise ValueError("Unknown GO aspect")
    expressions = {
        k: k if k in ("status", "aspect") else f"json_extract_string(payload, '$.{k}')" for k in FACETS
    }
    expressions["model_type"] = (
        "coalesce(json_extract_string(payload, '$.model_type'), CASE "
        "WHEN regexp_full_match(json_extract_string(payload, '$.with_or_from'), 'NBR[0-9]+') THEN 'BlastRule' "
        "WHEN coalesce(json_extract_string(payload, '$.with_or_from'),'')='' THEN 'None' "
        "ELSE coalesce(nullif(regexp_extract(json_extract_string(payload, '$.with_or_from'), '^([^:]+):', 1), ''), 'Unclassified') END)"
    )

    def where(exclude=""):
        clauses, params = ["TRUE"], []
        if identifier is not None:
            clauses.append("set_id=?")
            params.append(identifier)
        if gene:
            clauses.append("json_extract_string(payload, '$.gene_id')=?")
            params.append(gene)
        if q:
            clauses.append(
                "contains(lower(search_text || coalesce(json_extract_string(payload, '$.ncbi_gene_xrefs'), '')),lower(?))"
            )
            params.append(q)
        for field, values in selected.items():
            if values and field != exclude:
                clauses.append(f"{expressions[field]} IN (SELECT unnest(?))")
                params.append(values)
        return " AND ".join(clauses), params

    facets = {}
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        if not db.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='external_claims'"
        ).fetchone()[0]:
            return {"total": 0, "offset": offset, "rows": [], "facets": {}}
        predicate, params = where()
        total = db.execute(f"SELECT count(*) FROM external_claims WHERE {predicate}", params).fetchone()[0]
        sql = f"SELECT payload FROM external_claims WHERE {predicate} ORDER BY subject,term,id"
        paging = [size, offset] if size is not None else []
        rows = db.execute(sql + (" LIMIT ? OFFSET ?" if size is not None else ""), params + paging).fetchall()
        if include_facets:
            for field in FACETS:
                predicate, params = where(field)
                counts = dict(
                    db.execute(
                        f"SELECT {expressions[field]} AS value,count(*) FROM external_claims WHERE {predicate} GROUP BY value",
                        params,
                    ).fetchall()
                )
                values = (
                    list(STATUSES)
                    if field == "status"
                    else sorted((set(counts) | set(selected[field])) - {None})
                )
                facets[field] = [
                    {"value": v, "count": counts.get(v, 0), "selected": v in selected[field]} for v in values
                ]
    return {"total": total, "offset": offset, "rows": [json.loads(r[0]) for r in rows], "facets": facets}


def export_tsv(rows: list[dict], build_id: str, identifier: str, filters: dict | None = None) -> str:
    output = io.StringIO()
    output.write(f"# build_id: {build_id}\n# set_id: {identifier}\n# ancestry: is_a\n")
    output.write(f"# filters: {json.dumps(filters or {}, ensure_ascii=False)}\n")
    fields = [
        "id",
        "status",
        "source_subject_id",
        "target_subject_id",
        "gene_id",
        "ncbi_gene_xrefs",
        "model_type",
        "symbol",
        "ontology_class_id",
        "term_id",
        "term_label",
        "aspect",
        "relation",
        "negation",
        "evidence_code",
        "references",
        "with_or_from",
        "annotation_extensions",
        "gene_product_form_id",
        "mapping_status",
        "mapping_evidence",
        "exact_terms",
        "more_specific_terms",
        "broader_terms",
        "assertion_match",
        "opposite_assertion_ids",
        "baseline_matches",
        "source_line",
        "raw_record",
    ]
    writer = csv.writer(output, delimiter="\t")
    writer.writerow(fields)
    for row in rows:
        writer.writerow(
            [
                json.dumps(row.get(f, ""), ensure_ascii=False)
                if isinstance(row.get(f), (dict, list))
                else row.get(f, "")
                for f in fields
            ]
        )
    return output.getvalue()
