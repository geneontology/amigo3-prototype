"""Snapshot GO-CAMs, recursive modules and cached PANTHER family records."""

from __future__ import annotations

import csv
import hashlib
import io
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import yaml

from amigo3.metrics import gocam_metrics, module_metrics
from amigo3.parse import identifier_variants, normalize_id

COLLECTIONS = ("gocams", "modules", "families")
LISTS = (
    "term_ids",
    "gene_ids",
    "gene_symbols",
    "participant_ids",
    "chemical_ids",
    "family_ids",
    "model_ids",
    "module_ids",
    "taxon_ids",
)
INTEGERS = (
    "member_count",
    "activity_count",
    "node_count",
    "reported_protein_count",
    # Structural metrics (amigo3.metrics); 0 where a collection has no such structure.
    "enabled_by_count",
    "causal_edge_count",
    "component_count",
    "longest_path_length",
    "part_count",
    "variant_set_count",
    "variant_count",
    "realization_count",
    "max_depth",
    "annoton_count",
    "connection_count",
)
STRINGS = ("id", "label", "description", "status", "kind", "scope", "functional_coherence", "search_text")
SCHEMA = pa.schema(
    [(k, pa.string()) for k in STRINGS]
    + [(k, pa.list_(pa.string())) for k in LISTS]
    + [(k, pa.int64()) for k in INTEGERS]
    + [("has_review", pa.bool_())]
)
PATTERNS = (
    "modules/*.yaml",
    "gocams/*/*-src.yaml",
    "gocams/*/*-review.yaml",
    "interpro/panther/*/*-metadata.yaml",
    "interpro/panther/*/*-review.yaml",
    "interpro/panther/*/*-entries.csv",
    "interpro/panther/*/*-paint.tsv",
)


def input_paths(root: Path | None) -> list[Path]:
    return sorted({path for pattern in PATTERNS for path in root.glob(pattern)}) if root else []


def parse_document(path: str, text: str):
    if path.endswith((".csv", ".tsv")):
        return list(csv.DictReader(io.StringIO(text), delimiter="\t" if path.endswith(".tsv") else ","))
    return yaml.load(text, Loader=yaml.CSafeLoader)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def grounding(value):
    """Structured identifiers only; never mine prose for biological assertions."""
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("evidence", "references", "objects", "supported_by", "provenances"):
                continue
            if (
                key
                in (
                    "id",
                    "term",
                    "go_id",
                    "family_id",
                    "subfamily_id",
                    "taxon",
                    "model",
                    "molecule",
                    "chemical",
                )
                and isinstance(child, str)
                and ":" in child
            ):
                result.add(normalize_id(child))
            result.update(grounding(child))
    elif isinstance(value, list):
        for child in value:
            result.update(grounding(child))
    return result


def new_record(identifier, label, kind):
    return {
        **dict.fromkeys(STRINGS, ""),
        **{k: [] for k in LISTS},
        **dict.fromkeys(INTEGERS, 0),
        "has_review": False,
        "id": identifier,
        "label": label or identifier,
        "kind": kind,
    }


def load_knowledge(db, root: Path | None, expected: dict[str, str]) -> dict:
    """Write projections plus byte-exact sources; do not mutate upstream files."""
    db.execute(
        "CREATE TABLE knowledge_sources(collection VARCHAR, record_id VARCHAR, role VARCHAR, path VARCHAR, sha256 VARCHAR, raw_text VARCHAR)"
    )
    groups = defaultdict(dict)
    source_batch = []
    for path in input_paths(root):
        raw = path.read_bytes()
        relative = str(path.relative_to(root))
        checksum = hashlib.sha256(raw).hexdigest()
        if expected.get(relative) != checksum:
            raise ValueError(f"Repository source changed during build: {relative}")
        text = raw.decode("utf-8-sig")
        document = parse_document(relative, text)
        if relative.startswith("modules/"):
            collection, identifier, role = "modules", document["id"], "module"
        elif relative.startswith("gocams/"):
            collection, identifier = "gocams", "gomodel:" + path.parent.name
            role = "model" if path.name.endswith("-src.yaml") else "review"
        else:
            collection, identifier = "families", "PANTHER:" + path.parent.name
            role = path.stem.rsplit("-", 1)[-1]
        groups[collection, identifier][role] = document
        source_batch.append((collection, identifier, role, relative, checksum, raw.decode("utf-8")))
        if len(source_batch) == 200:
            db.executemany("INSERT INTO knowledge_sources VALUES (?, ?, ?, ?, ?, ?)", source_batch)
            source_batch.clear()
    if source_batch:
        db.executemany("INSERT INTO knowledge_sources VALUES (?, ?, ?, ?, ?, ?)", source_batch)
    # Explicit aliases from the integration catalog. Multiple candidates stay multiple.
    aliases, symbols = defaultdict(set), {}
    for identifier, symbol, ids in db.execute("SELECT id, symbol, identifiers FROM genes").fetchall():
        symbols[identifier] = symbol
        for alias in ids:
            aliases[normalize_id(alias)].add(identifier)
    records = {c: [] for c in COLLECTIONS}
    for (collection, identifier), documents in sorted(groups.items()):
        review = documents.get("review") or {}
        refs, extra_search = set(), []
        if collection == "gocams":
            model = documents.get("model")
            if not model:
                raise ValueError(f"Review has no cached GO-CAM: {identifier}")
            if model["id"] != identifier:
                raise ValueError(f"GO-CAM path/ID mismatch: {identifier}")
            record = new_record(identifier, model.get("title"), "GO-CAM")
            record.update(
                status=model.get("status") or "",
                description=review.get("summary") or "",
                **gocam_metrics(model),
            )
            refs = grounding(model)
            # Molecule roles remain separate from enabled_by in the source document.
            refs.update(grounding(model.get("molecules", [])))
            extra_search = [o.get("label", "") for o in model.get("objects") or []]
        elif collection == "modules":
            document = documents["module"]
            module = document["module"]
            record = new_record(
                identifier,
                document.get("title") or module.get("label"),
                module.get("module_type") or "MODULE",
            )
            record.update(
                description=document.get("description") or "",
                status=document.get("status") or "",
                scope=document.get("scope") or "",
                node_count=sum("module_type" in node for node in walk(module)),
                **module_metrics(module),
            )
            refs = grounding(module)
            for node in walk(module):
                for association in node.get("gocam_associations") or []:
                    refs.add("gomodel:" + association["model"].removeprefix("gomodel:"))
                for conformance in node.get("conforms_to") or []:
                    template = (
                        conformance["template"].split("#")[0].removeprefix("modules/").removesuffix(".yaml")
                    )
                    refs.add(template if template.startswith("MODULE:") else "MODULE:" + template)
                extra_search.extend(str(node[k]) for k in ("label", "preferred_term") if node.get(k))
        else:
            metadata = (documents.get("metadata") or {}).get("metadata") or {}
            name = metadata.get("name") or {}
            official_name = name.get("name") if isinstance(name, dict) else name
            if review and review.get("family_id") != identifier:
                raise ValueError(f"Family review path/ID mismatch: {identifier}")
            record = new_record(identifier, official_name or review.get("family_name"), "PANTHER family")
            record.update(
                description=review.get("summary") or "",
                status=review.get("review_status") or "UNREVIEWED",
                functional_coherence=review.get("functional_coherence") or "",
                reported_protein_count=int((metadata.get("counters") or {}).get("proteins") or 0),
            )
            refs = grounding(review) | grounding(metadata)
            members = documents.get("entries") or []
            record["member_count"] = len(members)
            for member in members:
                refs.add(normalize_id(member["id"] if ":" in member["id"] else "UniProtKB:" + member["id"]))
                if member.get("source_tax_id"):
                    refs.add("NCBITaxon:" + member["source_tax_id"])
                extra_search.extend(
                    member.get(k) or ""
                    for k in ("gene", "name", "subfamily", "subfamily_name", "source_tax_name")
                )
            for assertion in documents.get("paint") or []:
                if assertion.get("go_id"):
                    refs.add(assertion["go_id"])
            extra_search.extend(
                str(node.get("label") or node.get("preferred_name") or "") for node in walk(review)
            )
        record["has_review"] = bool(review)
        record["term_ids"] = sorted(x for x in refs if x.startswith("GO:"))
        record["chemical_ids"] = sorted(x for x in refs if x.startswith("CHEBI:"))
        record["taxon_ids"] = sorted(x for x in refs if x.startswith("NCBITaxon:"))
        record["family_ids"] = sorted(x for x in refs if x.startswith("PANTHER:PTHR"))
        record["model_ids"] = sorted(
            x for x in refs if x.startswith("gomodel:") and "/" not in x and x != identifier
        )
        record["module_ids"] = sorted(x for x in refs if x.startswith("MODULE:") and x != identifier)
        gene_ids = set()
        for ref in refs:
            for alias in identifier_variants(ref):
                gene_ids.update(aliases.get(alias, ()))
        record["gene_ids"] = sorted(gene_ids)
        record["gene_symbols"] = sorted({symbols[x] for x in gene_ids})
        record["participant_ids"] = sorted(
            x
            for x in refs
            if x.startswith(
                ("UniProtKB:", "PomBase:", "WB:", "MGI:", "SGD:", "FB:", "ZFIN:", "RGD:", "ComplexPortal:")
            )
        )
        record["search_text"] = " ".join(
            [
                identifier,
                record["label"],
                record["description"],
                *sorted(refs),
                *record["gene_symbols"],
                *extra_search,
            ]
        )
        records[collection].append(record)
    for collection, rows in records.items():
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        db.register("knowledge_batch", table)
        db.execute(f'CREATE TABLE "{collection}" AS SELECT * FROM knowledge_batch')
        db.unregister("knowledge_batch")
    return {
        "sources": sum(len(d) for d in groups.values()),
        "counts": {c: len(rows) for c, rows in records.items()},
        "scope": "Cached repository records across all taxa; gene links resolve only into the loaded gene catalogs. Family inventories are cached subsets; PAINT assertions and module exemplars do not generate gene annotations.",
    }
