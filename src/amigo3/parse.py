"""Source adapters. GAF is a transport format, not the browser's data model."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Iterator

import yaml
from go_standard_annotation_schema.io import GpiReader, RowError

METHODS = yaml.safe_load((Path(__file__).parent / "annotation_methods.yaml").read_text())


def normalize_id(value: str) -> str:
    value = value.strip()
    if value.startswith("HGNC:HGNC:"):
        value = value[5:]
    prefix, sep, local = value.partition(":")
    aliases = {
        "taxon": "NCBITaxon",
        "ensembl": "ENSEMBL",
        "uniprotkb": "UniProtKB",
        "pombase": "PomBase",
        "hgnc": "HGNC",
        "wb": "WB",
        "wormbase": "WB",
    }
    return f"{aliases.get(prefix.lower(), prefix)}:{local}" if sep else value


def review_subject_id(value: str) -> str:
    """A review subject as a CURIE; reviews give bare UniProt or RNAcentral accessions.

    >>> review_subject_id("P04637")
    'UniProtKB:P04637'
    >>> review_subject_id("URS0000759CF4_9606")
    'RNAcentral:URS0000759CF4_9606'
    >>> review_subject_id("uniprotkb:P04637")
    'UniProtKB:P04637'
    """
    if ":" in value:
        return normalize_id(value)
    return ("RNAcentral:" if re.fullmatch(r"URS[0-9A-F]{10}_\d+", value) else "UniProtKB:") + value


def identifier_variants(value: str) -> list[str]:
    normalized = normalize_id(value)
    variants = {value, normalized}
    if normalized.startswith("ENSEMBL:"):
        variants.add(re.sub(r"\.\d+$", "", normalized))
    return sorted(variants - {""})


def split_values(value: str) -> list[str]:
    return [v.strip() for v in value.split("|") if v.strip()]


def text_rows(path: Path, expected: int, header: str | None = None) -> Iterator[tuple[int, list[str], str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    seen_version = header is None
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("!"):
                if header and line.startswith(header):
                    seen_version = True
                continue
            if not seen_version:
                raise ValueError(f"{path}:{line_number}: expected {header}")
            fields = line.split("\t")
            if len(fields) != expected:
                raise ValueError(f"{path}:{line_number}: expected {expected} columns, got {len(fields)}")
            yield line_number, fields, line


def source_header(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    result = []
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.startswith("!"):
                break
            result.append(line.rstrip())
    return result


def gpi_rows(path: Path, source: dict, issues: list[dict]) -> Iterator[dict]:
    for number, fields, raw in text_rows(path, 11, "!gpi-version: 2.0"):
        # The current GO files use an underscore here; the GPI specification and
        # SAB parser use a hyphen. Normalize only this property name, retain raw.
        fields[10] = re.sub(r"(^|\|)db_subset=", r"\1db-subset=", fields[10])
        try:
            entity = GpiReader.parse_line("\t".join(fields), line_number=number, source=source["id"])
            doc = entity.model_dump(mode="json")
        except RowError as error:
            # A read-only browser must not lose published entities because a
            # stricter authoring schema rejects a legacy symbol or property.
            # Preserve the row and report the mismatch, without claiming validity.
            issues.append(
                {
                    "kind": "entity_schema_warning",
                    "source_id": source["id"],
                    "source_line": number,
                    "subject": fields[0],
                    "message": str(error.__cause__ or error),
                }
            )
            doc = dict(
                zip(
                    (
                        "db_object_id",
                        "db_object_symbol",
                        "db_object_name",
                        "db_object_synonyms",
                        "db_object_type",
                        "db_object_taxon_id",
                        "encoded_by",
                        "canonical_object_id",
                        "protein_containing_complex_members",
                        "db_xrefs",
                        "gene_product_properties",
                    ),
                    fields,
                    strict=True,
                )
            )
            for key in ("db_object_synonyms", "encoded_by", "db_xrefs"):
                doc[key] = split_values(doc[key])
        yield {
            "id": normalize_id(doc["db_object_id"]),
            "symbol": doc["db_object_symbol"],
            "name": doc.get("db_object_name") or "",
            "synonyms": doc.get("db_object_synonyms") or [],
            "object_type": doc["db_object_type"],
            "taxon_id": normalize_id(doc["db_object_taxon_id"]),
            # Providers may omit the optional canonical ID (e.g. FlyBase).
            # An absent mapping must retain this subject, never merge blank IDs.
            "canonical_id": normalize_id(doc.get("canonical_object_id") or doc["db_object_id"]),
            "encoded_by": [normalize_id(x) for x in doc.get("encoded_by") or []],
            "xrefs": doc.get("db_xrefs") or [],
            "species": source["species"],
            "source_id": source["id"],
            "source_line": number,
            "raw_record": raw,
        }


def hgnc_rows(path: Path) -> Iterator[dict]:
    with path.open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row.get("status") != "Approved":
                continue
            ids = [row["hgnc_id"]]
            ids.extend(f"UniProtKB:{x}" for x in split_values(row.get("uniprot_ids", "")))
            ids.extend(f"ENSEMBL:{x}" for x in split_values(row.get("ensembl_gene_id", "")))
            if row.get("entrez_id"):
                ids.append(f"NCBIGene:{row['entrez_id']}")
            yield {
                "id": row["hgnc_id"],
                "symbol": row["symbol"],
                "name": row["name"],
                "synonyms": sorted(
                    set(split_values(row.get("alias_symbol", "")) + split_values(row.get("prev_symbol", "")))
                ),
                "identifiers": ids,
                "taxon_id": "NCBITaxon:9606",
                "species": "human",
            }


def eco_mapping(path: Path) -> dict[tuple[str, str], str]:
    result = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3:
            result[(fields[0], fields[1])] = fields[2]
    return result


def expression_groups(value: str) -> list[list[str]]:
    """Preserve OR (pipe) and AND (comma) rather than flattening either."""
    return [[v.strip() for v in group.split(",") if v.strip()] for group in value.split("|") if group]


def annotation_method(references: list[str]) -> str:
    """Name the pipeline or curation route behind a row; see annotation_methods.yaml."""
    go_refs = [r for r in references if r.startswith("GO_REF:")]
    for ref in go_refs:
        if ref in METHODS["go_refs"]:
            return METHODS["go_refs"][ref]
    if go_refs:
        return go_refs[0]
    for ref in references:
        if (method := METHODS["prefixes"].get(ref.partition(":")[0])) is not None:
            return method
    return METHODS["default"]


def gaf_rows(path: Path, source: dict, eco: dict) -> Iterator[dict]:
    for number, fields, raw in text_rows(path, 17, "!gaf-version: 2.2"):
        base = normalize_id(f"{fields[0]}:{fields[1]}")
        subject = normalize_id(fields[16]) if fields[16] else base
        qualifiers = split_values(fields[3])
        relations = [q for q in qualifiers if q != "NOT"]
        if len(relations) != 1:
            raise ValueError(f"{source['id']}:{number}: expected one GAF 2.2 relation")
        taxa = [normalize_id(x) for x in split_values(fields[12])]
        if not taxa:
            raise ValueError(f"{source['id']}:{number}: missing taxon")
        allowed_taxa = source.get("allowed_taxa", [source["taxon"]] if source.get("taxon") else [])
        if allowed_taxa and taxa[0] not in {normalize_id(t) for t in allowed_taxa}:
            raise ValueError(f"{source['id']}:{number}: unexpected taxon {taxa[0]}")
        references = split_values(fields[5])
        eco_ids = {eco.get((fields[6], ref), eco.get((fields[6], "Default"), "")) for ref in references}
        eco_ids.discard("")
        yield {
            "id": f"{source['id']}:{source['sha256'][:16]}:{number}",
            "record_fingerprint": hashlib.sha256(raw.encode()).hexdigest(),
            "db_object_id": subject,
            "canonical_subject_id": base,
            "gene_product_form_id": normalize_id(fields[16]) if fields[16] else "",
            "symbol": fields[2],
            "name": fields[9],
            "synonyms": split_values(fields[10]),
            "relation": relations[0],
            "negation": "NOT" in qualifiers,
            "ontology_class_id": fields[4],
            "references": references,
            "evidence_code": fields[6],
            "evidence_type": sorted(eco_ids),
            "method": annotation_method(references),
            "with_or_from": fields[7],
            "with_or_from_groups": json.dumps(expression_groups(fields[7])),
            "aspect": fields[8],
            "object_type": fields[11],
            "taxon_id": taxa[0],
            "interacting_taxon_id": taxa[1:],
            "annotation_date": fields[13],
            "assigned_by": fields[14],
            "annotation_extensions": fields[15],
            "extension_groups": json.dumps(expression_groups(fields[15])),
            "species": source["species"],
            "source_id": source["id"],
            "source_line": number,
            "raw_record": raw,
        }
