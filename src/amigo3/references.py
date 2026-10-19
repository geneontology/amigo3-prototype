"""Reference records joining GAF citations, review citations and cached publication metadata.

Only bibliographic metadata and the cached abstract section are published; any
cached full text stays in the repository.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa
import yaml

from amigo3.knowledge import parse_document

CITATION_KEYS = ("reference", "reference_id", "original_reference_id")
LISTS = (
    "authors",
    "annotation_species",
    "evidence_codes",
    "assigned_by_groups",
    "aspects",
    "review_gene_ids",
    "model_ids",
    "module_ids",
    "family_ids",
)
INTEGERS = ("annotation_count", "gene_count", "term_count", "review_count")
STRINGS = (
    "id",
    "label",
    "description",
    "kind",
    "journal",
    "year",
    "doi",
    "publication_type",
    "source_path",
    "search_text",
)
SCHEMA = pa.schema(
    [(name, pa.string()) for name in STRINGS]
    + [(name, pa.list_(pa.string())) for name in LISTS]
    + [(name, pa.int64()) for name in INTEGERS]
    + [("has_review", pa.bool_()), ("has_abstract", pa.bool_())]
)


def input_paths(root: Path | None) -> list[Path]:
    if not root:
        return []
    folder = root / "publications"
    return sorted(path for pattern in ("PMID_*.md", "DOI_*.md") for path in folder.glob(pattern))


def is_reference(value) -> bool:
    """A citable CURIE; local `file:` sources are provenance, not references."""
    return (
        isinstance(value, str)
        and ":" in value
        and not value.startswith("file:")
        and not any(c.isspace() for c in value.strip())
    )


def split_publication(text: str) -> tuple[dict, str]:
    """Frontmatter metadata and the cached abstract section, never the full text."""
    metadata, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            metadata = yaml.load(text[4:end], Loader=yaml.CSafeLoader) or {}
            body = text[end + 5 :]
    abstract = ""
    start = body.find("## Abstract\n")
    if start != -1:
        rest = body[start + len("## Abstract\n") :]
        stop = rest.find("\n## ")
        abstract = (rest if stop == -1 else rest[:stop]).strip()
    return metadata, abstract


def publication_id(path: Path, metadata: dict) -> str | None:
    # The file name is the identifier reviews cite; DOI file names are lossy.
    if path.name.startswith("PMID_"):
        return "PMID:" + path.stem[len("PMID_") :]
    doi = str(metadata.get("doi") or "").strip()
    return "DOI:" + doi if doi else None


def review_citations(document: dict):
    """Structured citations in a gene review; prose is never mined for identifiers."""
    for reference in document.get("references") or []:
        if isinstance(reference, dict) and is_reference(reference.get("id")):
            detail = {
                key: reference[key]
                for key in ("title", "reference_review", "findings", "is_invalid", "full_text_unavailable")
                if reference.get(key) not in (None, "", [], {})
            }
            yield reference["id"].strip(), "bibliography", detail
    for index, entry in enumerate(document.get("existing_annotations") or []):
        if isinstance(entry, dict) and is_reference(entry.get("original_reference_id")):
            term = entry.get("term") or {}
            yield (
                entry["original_reference_id"].strip(),
                "annotation",
                {
                    "index": index,
                    "term_id": term.get("id"),
                    "term_label": term.get("label"),
                    "evidence_type": entry.get("evidence_type"),
                    "negated": bool(entry.get("negated")),
                    "action": (entry.get("review") or {}).get("action"),
                },
            )

    def supports(value, section, term):
        if isinstance(value, dict):
            for key in ("term", "molecular_function"):
                if isinstance(value.get(key), dict) and value[key].get("id"):
                    term = value[key]
                    break
            if is_reference(value.get("reference_id")):
                yield (
                    value["reference_id"].strip(),
                    "support",
                    {
                        "section": section,
                        "term_id": term.get("id"),
                        "term_label": term.get("label"),
                        "supporting_text": value.get("supporting_text") or "",
                    },
                )
            for child in value.values():
                yield from supports(child, section, term)
        elif isinstance(value, list):
            for child in value:
                yield from supports(child, section, term)

    for section, value in document.items():
        if section != "references":
            yield from supports(value, section, {})


def knowledge_citations(document) -> set[str]:
    """Citations held in evidence fields of GO-CAM, module and family snapshots."""
    found = set()

    def visit(value, parent=None):
        if isinstance(value, dict):
            for key, child in value.items():
                if (key in CITATION_KEYS or (parent == "references" and key == "id")) and is_reference(child):
                    found.add(child.strip())
                else:
                    visit(child, key)
        elif isinstance(value, list):
            for child in value:
                if parent == "references" and is_reference(child):
                    found.add(child.strip())
                else:
                    visit(child, parent)

    visit(document)
    return found


def load_references(db, root: Path | None, expected: dict[str, str], review_rows: list[dict]) -> dict:
    """Index every cited reference; publication metadata is optional enrichment."""
    publications, issues = {}, []
    for path in input_paths(root):
        raw = path.read_bytes()
        relative = str(path.relative_to(root))
        checksum = hashlib.sha256(raw).hexdigest()
        if expected.get(relative) != checksum:
            raise ValueError(f"Repository source changed during build: {relative}")
        try:
            metadata, abstract = split_publication(raw.decode("utf-8-sig"))
        except yaml.YAMLError:
            issues.append({"kind": "unparseable_publication", "path": relative})
            continue
        identifier = publication_id(path, metadata)
        if identifier is None:
            issues.append({"kind": "publication_without_identifier", "path": relative})
            continue
        authors = metadata.get("authors") or []
        publications[identifier] = {
            "id": identifier,
            "title": str(metadata.get("title") or "").strip(),
            "authors": [str(a) for a in authors] if isinstance(authors, list) else [str(authors)],
            "journal": str(metadata.get("journal") or ""),
            "year": str(metadata.get("year") or ""),
            "doi": str(metadata.get("doi") or ""),
            "publication_type": str(metadata.get("publication_type") or ""),
            "source_path": relative,
            "sha256": checksum,
            "abstract": abstract,
        }
    publication_schema = pa.schema(
        [
            (name, pa.list_(pa.string()) if name == "authors" else pa.string())
            for name in (
                "id",
                "title",
                "authors",
                "journal",
                "year",
                "doi",
                "publication_type",
                "source_path",
                "sha256",
                "abstract",
            )
        ]
    )
    db.register(
        "publication_batch", pa.Table.from_pylist(list(publications.values()), schema=publication_schema)
    )
    db.execute("CREATE TABLE reference_publications AS SELECT * FROM publication_batch")
    db.unregister("publication_batch")

    citations = set()
    for source in review_rows:
        document = json.loads(source["review_document"])
        for identifier, role, detail in review_citations(document):
            citations.add(
                (identifier, "genes", source["gene_id"], source["review_path"], role, json.dumps(detail))
            )
    knowledge = db.execute(
        "SELECT collection, record_id, path, raw_text FROM knowledge_sources ORDER BY path"
    ).fetchall()
    for collection, record_id, path, raw in knowledge:
        if path.endswith((".csv", ".tsv")):
            continue
        for identifier in knowledge_citations(parse_document(path, raw)):
            citations.add((identifier, collection, record_id, path, "evidence", "{}"))
    columns = ("reference_id", "collection", "record_id", "path", "role", "detail_json")
    db.register(
        "citation_batch",
        pa.Table.from_pylist(
            [dict(zip(columns, row, strict=True)) for row in sorted(citations)],
            schema=pa.schema([(c, pa.string()) for c in columns]),
        ),
    )
    db.execute("CREATE TABLE reference_citations AS SELECT * FROM citation_batch")
    db.unregister("citation_batch")

    # Ordered by reference so detail lookups prune row groups.
    db.execute("""CREATE TABLE reference_annotations AS
        SELECT r.reference_id, a.id AS annotation_id, a.gene_id, a.gene_symbol, a.species,
          a.ontology_class_id, a.term_label, a.aspect, a.evidence_code, a.assigned_by, a.negation
        FROM annotations a, UNNEST(a."references") AS r(reference_id)
        WHERE r.reference_id <> ''
        ORDER BY r.reference_id, a.gene_symbol, a.ontology_class_id""")
    annotated = {
        row[0]: row[1:]
        for row in db.execute("""SELECT reference_id, count(DISTINCT annotation_id),
            count(DISTINCT gene_id), count(DISTINCT ontology_class_id),
            list_sort(list(DISTINCT species) FILTER (WHERE species <> '')),
            list_sort(list(DISTINCT evidence_code) FILTER (WHERE evidence_code <> '')),
            list_sort(list(DISTINCT assigned_by) FILTER (WHERE assigned_by <> '')),
            list_sort(list(DISTINCT aspect) FILTER (WHERE aspect <> ''))
            FROM reference_annotations GROUP BY 1""").fetchall()
    }
    cited = defaultdict(lambda: defaultdict(set))
    titles = {}
    for identifier, collection, record_id, _, role, detail in sorted(citations):
        cited[identifier][collection].add(record_id)
        if role == "bibliography":
            titles.setdefault(identifier, str(json.loads(detail).get("title") or "").strip())

    records = []
    for identifier in sorted(set(annotated) | set(cited) | set(publications)):
        publication = publications.get(identifier, {})
        counts = annotated.get(identifier, (0, 0, 0, [], [], [], []))
        authors = publication.get("authors", [])
        byline = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        venue = " ".join(
            x
            for x in (
                publication.get("journal", ""),
                f"({publication['year']})" if publication.get("year") else "",
            )
            if x
        )
        label = publication.get("title") or titles.get(identifier) or identifier
        doi = publication.get("doi") or (identifier[4:] if identifier.startswith("DOI:") else "")
        record = {
            "id": identifier,
            "label": label,
            "description": " · ".join(x for x in (byline, venue) if x),
            "kind": identifier.split(":", 1)[0],
            "journal": publication.get("journal", ""),
            "year": publication.get("year", ""),
            "doi": doi,
            "publication_type": publication.get("publication_type", ""),
            "source_path": publication.get("source_path", ""),
            "authors": authors,
            "annotation_count": counts[0],
            "gene_count": counts[1],
            "term_count": counts[2],
            "annotation_species": counts[3],
            "evidence_codes": counts[4],
            "assigned_by_groups": counts[5],
            "aspects": counts[6],
            "review_gene_ids": sorted(cited[identifier]["genes"]),
            "model_ids": sorted(cited[identifier]["gocams"]),
            "module_ids": sorted(cited[identifier]["modules"]),
            "family_ids": sorted(cited[identifier]["families"]),
            "review_count": len(cited[identifier]["genes"]),
            "has_review": bool(cited[identifier]["genes"]),
            "has_abstract": bool(publication.get("abstract")),
        }
        record["search_text"] = " ".join(
            x for x in (identifier, label, " ".join(authors), record["journal"], record["year"], doi) if x
        )
        records.append(record)
    db.register("reference_batch", pa.Table.from_pylist(records, schema=SCHEMA))
    db.execute('CREATE TABLE "references" AS SELECT * FROM reference_batch')
    db.unregister("reference_batch")
    return {
        "references": len(records),
        "cached_publications": len(publications),
        "review_citations": sum(1 for c in citations if c[1] == "genes"),
        "knowledge_citations": sum(1 for c in citations if c[1] != "genes"),
        "issues": issues,
        "policy": "Cited identifiers from GAF references, gene review citations and GO-CAM, module and family evidence; cached publications add metadata and the abstract only.",
    }


def reference_detail(path: Path, record: dict, limit: int = 50) -> dict:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        identifier = record["id"]
        publication = db.execute(
            "SELECT title, authors, journal, year, doi, publication_type, source_path, sha256, abstract FROM reference_publications WHERE id=?",
            [identifier],
        ).fetchone()

        def rows(sql):
            return db.execute(sql, [identifier]).to_arrow_table().to_pylist()

        genes = rows(f"""SELECT gene_id AS id, min(gene_symbol) AS symbol, min(species) AS species,
            count(DISTINCT annotation_id) AS annotation_count,
            list_sort(list(DISTINCT ontology_class_id)) AS term_ids
            FROM reference_annotations WHERE reference_id=? GROUP BY gene_id
            ORDER BY annotation_count DESC, symbol, id LIMIT {limit}""")
        terms = rows(f"""SELECT ontology_class_id AS id, min(term_label) AS label, min(aspect) AS aspect,
            count(DISTINCT annotation_id) AS annotation_count, count(DISTINCT gene_id) AS gene_count
            FROM reference_annotations WHERE reference_id=? GROUP BY ontology_class_id
            ORDER BY annotation_count DESC, label, id LIMIT {limit}""")
        evidence = rows("""SELECT evidence_code AS value, count(DISTINCT annotation_id) AS count
            FROM reference_annotations WHERE reference_id=? GROUP BY 1 ORDER BY count DESC, value""")
        citations = rows("""SELECT c.collection, c.record_id, c.path, c.role, c.detail_json,
            coalesce(g.symbol, c.record_id) AS symbol
            FROM reference_citations c LEFT JOIN genes g ON c.collection='genes' AND g.id=c.record_id
            WHERE c.reference_id=? ORDER BY c.collection, symbol, c.record_id, c.role, c.detail_json""")
        reviews, resources = {}, defaultdict(dict)
        for citation in citations:
            if citation["collection"] != "genes":
                resources[citation["collection"]][citation["record_id"]] = citation["path"]
                continue
            review = reviews.setdefault(
                (citation["record_id"], citation["path"]),
                {
                    "gene_id": citation["record_id"],
                    "symbol": citation["symbol"],
                    "path": citation["path"],
                    "bibliography": None,
                    "annotations": [],
                    "supports": [],
                },
            )
            detail = json.loads(citation["detail_json"])
            if citation["role"] == "bibliography":
                review["bibliography"] = detail
            else:
                review["annotations" if citation["role"] == "annotation" else "supports"].append(detail)
        related = {}
        for collection, items in resources.items():
            labels = dict(
                db.execute(
                    f'SELECT id, label FROM "{collection}" WHERE id IN (SELECT unnest(?::VARCHAR[]))',
                    [sorted(items)],
                ).fetchall()
            )
            related[collection] = sorted(
                ({"id": key, "label": labels.get(key, key), "path": value} for key, value in items.items()),
                key=lambda item: (item["label"], item["id"]),
            )
    fields = (
        "title",
        "authors",
        "journal",
        "year",
        "doi",
        "publication_type",
        "source_path",
        "sha256",
        "abstract",
    )
    return {
        "record": record,
        "publication": dict(zip(fields, publication, strict=True)) if publication else None,
        "annotations": {"genes": genes, "terms": terms, "evidence": evidence, "limit": limit},
        "reviews": list(reviews.values()),
        "related": related,
    }
