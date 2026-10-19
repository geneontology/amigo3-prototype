"""Integrate source tables in DuckDB and publish immutable Parquet projections."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import yaml

from amigo3.annotation_reviews import index_reviews
from amigo3.cell_types import load_cell_types
from amigo3.hierarchies import index_hierarchies, load_species
from amigo3.knowledge import input_paths, load_knowledge
from amigo3.ontology import load_ontology
from amigo3.parse import (
    eco_mapping,
    gaf_rows,
    gpi_rows,
    hgnc_rows,
    identifier_variants,
    normalize_id,
    review_subject_id,
    source_header,
)
from amigo3.predictions import input_sets, load_sets
from amigo3.predictions import signature as external_signature
from amigo3.product_types import index_product_types
from amigo3.projects import input_paths as project_inputs
from amigo3.projects import load_projects
from amigo3.reactions import load_reactions
from amigo3.references import input_paths as reference_inputs
from amigo3.references import load_references
from amigo3.reports import load_reports, report_inputs
from amigo3.rules import input_paths as rule_inputs
from amigo3.rules import load_rules
from amigo3.sources import digest, write_json
from amigo3.structures import RENDERER, load_structures

LIST_FIELDS = {
    "synonyms",
    "encoded_by",
    "xrefs",
    "identifiers",
    "references",
    "evidence_type",
    "interacting_taxon_id",
    "alt_ids",
    "subsets",
    "parent_ids",
    "is_a_closure",
    "isa_partof_closure",
    "review_actions",
    "gene_ids",
}
BOOL_FIELDS = {"negation", "obsolete", "has_review"}
INT_FIELDS = {"source_line"}


def load_rows(db, name: str, rows, columns: list[str] | None = None) -> int:
    """Insert Arrow batches using a fixed schema, including empty list columns."""
    count, batch, schema = 0, [], None

    def flush():
        nonlocal count, schema
        if not batch:
            return
        if schema is None:
            schema = pa.schema(
                [
                    (
                        key,
                        pa.list_(pa.string())
                        if key in LIST_FIELDS
                        else pa.bool_()
                        if key in BOOL_FIELDS
                        else pa.int64()
                        if key in INT_FIELDS
                        else pa.string(),
                    )
                    for key in batch[0]
                ]
            )
        table = pa.Table.from_pylist(batch, schema=schema)
        db.register("incoming_batch", table)
        if count == 0:
            db.execute(f'CREATE TABLE "{name}" AS SELECT * FROM incoming_batch')
        else:
            db.execute(f'INSERT INTO "{name}" SELECT * FROM incoming_batch')
        count += len(batch)
        batch.clear()
        db.unregister("incoming_batch")

    for row in rows:
        batch.append(row)
        if len(batch) == 10000:
            flush()
    flush()
    if not count and columns:
        db.execute(f'CREATE TABLE "{name}" (' + ", ".join(f'"{c}" VARCHAR' for c in columns) + ")")
    return count


def integrate_entities(
    entities: list[dict], human_genes: list[dict], issues: list[dict], active_subjects: set[str] | None = None
):
    """Use explicit provider mappings; never join entities on gene symbols."""
    genes = {g["id"]: dict(g) for g in human_genes}
    uniprot_to_hgnc = defaultdict(set)
    for gene in human_genes:
        for identifier in gene["identifiers"]:
            if identifier.startswith("UniProtKB:"):
                uniprot_to_hgnc[identifier].add(gene["id"])
    links, mapping = [], []
    for entity in entities:
        if active_subjects is not None and not (
            entity["id"] in active_subjects
            or entity["canonical_id"] in active_subjects
            or entity["id"].startswith(("PomBase:", "WB:WBGene"))
            or any(normalize_id(x).startswith("HGNC:") for x in entity["xrefs"] + entity["encoded_by"])
        ):
            continue
        candidates = set()
        # Keep complexes and distinct ungrounded products as their own records.
        if entity["species"] == "human" and entity["object_type"] != "GO:0032991":
            explicit = {
                normalize_id(x)
                for x in entity["xrefs"] + entity["encoded_by"]
                if normalize_id(x).startswith("HGNC:") and normalize_id(x) in genes
            }
            protein = uniprot_to_hgnc[entity["id"]] | uniprot_to_hgnc[entity["canonical_id"]]
            candidates = explicit | protein
        if len(candidates) == 1:
            gene_id, status = next(iter(candidates)), "mapped"
        elif len(candidates) > 1:
            gene_id, status = entity["canonical_id"], "ambiguous"
            issues.append(
                {
                    "kind": "ambiguous_gene_mapping",
                    "subject": entity["id"],
                    "candidates": sorted(candidates),
                    "source_id": entity["source_id"],
                }
            )
        else:
            gene_id = entity["canonical_id"]
            status = "provider_primary" if gene_id.startswith(("PomBase:", "WB:WBGene")) else "unresolved"
        if gene_id not in genes:
            genes[gene_id] = {
                "id": gene_id,
                "symbol": entity["symbol"] or gene_id,
                "name": entity["name"],
                "synonyms": [],
                "identifiers": [gene_id],
                "taxon_id": entity["taxon_id"],
                "species": entity["species"],
            }
        gene = genes[gene_id]
        gene["synonyms"] = sorted(set(gene["synonyms"] + entity["synonyms"]))
        identifiers = [entity["id"], entity["canonical_id"], *entity["xrefs"], *entity["encoded_by"]]
        for identifier in identifiers:
            for variant in identifier_variants(identifier):
                gene["identifiers"].append(variant)
                links.append(
                    {
                        "gene_id": gene_id,
                        "identifier": variant,
                        "raw_identifier": identifier,
                        "subject_id": entity["id"],
                        "source_id": entity["source_id"],
                        "mapping_status": status,
                    }
                )
        mapping.append({"subject_id": entity["id"], "gene_id": gene_id, "mapping_status": status})
    for gene in genes.values():
        gene["identifiers"] = sorted(set(gene["identifiers"]))
    for gene in human_genes:
        for identifier in gene["identifiers"]:
            links.append(
                {
                    "gene_id": gene["id"],
                    "identifier": identifier,
                    "raw_identifier": identifier,
                    "subject_id": gene["id"],
                    "source_id": "hgnc",
                    "mapping_status": "gene_catalog",
                }
            )
    # A provider may repeat a subject in GPI (e.g. WormBase isoforms).
    # Keep every source row and cross-reference, but join annotations only once.
    unique_mapping = {}
    for row in mapping:
        previous = unique_mapping.get(row["subject_id"])
        if previous is not None and previous != row:
            raise ValueError(f"Conflicting GPI mappings for {row['subject_id']}")
        unique_mapping[row["subject_id"]] = row
    return list(genes.values()), list(unique_mapping.values()), links


def read_reviews(root: Path | None, genes: list[dict]) -> list[dict]:
    result = []
    by_id = defaultdict(set)
    for gene in genes:
        for identifier in gene["identifiers"]:
            by_id[normalize_id(identifier)].add(gene["id"])
    if root:
        for path in sorted(root.glob("*/*/*-ai-review.yaml")):
            document = yaml.load(path.read_text(), Loader=yaml.CSafeLoader)
            for gene_id in sorted(by_id[review_subject_id(document.get("id", ""))]):
                result.append(
                    {
                        "gene_id": gene_id,
                        "review_description": document.get("description") or "",
                        "review_actions": sorted(
                            {
                                a.get("review", {}).get("action", "PENDING")
                                for a in document.get("existing_annotations") or []
                            }
                        ),
                        "review_path": str(path.relative_to(root.parent)),
                        "review_sha256": digest(path),
                        "review_document": json.dumps(document, default=str),
                    }
                )
    return result


def build(
    data: Path, reviews: Path | None = None, repository: Path | None = None, external_sets: Path | None = None
) -> dict:
    external = input_sets(external_sets)
    if repository is not None:
        repository = repository.resolve()
        if not repository.is_dir():
            raise ValueError(f"Repository directory does not exist: {repository}")
        reviews = reviews or repository / "genes"
    manifest = json.loads((data / "sources.json").read_text())
    for source in manifest.values():
        if digest(data / source["path"]) != source["sha256"]:
            raise ValueError(f"Source checksum mismatch: {source['id']}; fetch again")
    # Include code and optional review bytes in the build identity.
    code = hashlib.sha256()
    for path in sorted(
        Path(__file__).parent / name
        for name in (
            "build.py",
            "parse.py",
            "product_types.py",
            "ontology.py",
            "hierarchies.py",
            "sources.py",
            "knowledge.py",
            "reports.py",
            "projects.py",
            "rules.py",
            "reactions.py",
            "references.py",
            "structures.py",
            "cell_types.py",
            "annotation_reviews.py",
            "gene.py",
            "predictions.py",
        )
    ):
        code.update(path.name.encode() + path.read_bytes())
    signature = {
        "sources": {
            k: {
                field: v.get(field)
                for field in ("sha256", "kind", "species", "taxon", "allowed_taxa", "loaded_species")
            }
            for k, v in manifest.items()
        },
        "code": code.hexdigest(),
        "chemical_renderer": RENDERER,
        "external_sets": external_signature(external),
    }
    if reviews:
        signature["reviews"] = {
            str(p.relative_to(reviews)): digest(p) for p in sorted(reviews.glob("*/*/*-ai-review.yaml"))
        }
    signature["repository"] = {str(p.relative_to(repository)): digest(p) for p in input_paths(repository)}
    signature["reports"] = {str(p.relative_to(repository)): digest(p) for p in report_inputs(repository)}
    signature["rules"] = {str(p.relative_to(repository)): digest(p) for p in rule_inputs(repository)}
    signature["projects"] = {str(p.relative_to(repository)): digest(p) for p in project_inputs(repository)}
    # Tens of thousands of cached publications: one aggregate digest keeps the manifest small.
    publications = {str(p.relative_to(repository)): digest(p) for p in reference_inputs(repository)}
    signature["publications"] = hashlib.sha256(json.dumps(publications, sort_keys=True).encode()).hexdigest()
    build_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
    destination = data / "builds" / build_id
    if (destination / "manifest.json").exists():
        write_json(data / "current.json", {"build_id": build_id})
        print(f"Using existing build {build_id}")
        return json.loads((destination / "manifest.json").read_text())
    work = data / "builds" / f"{build_id}.building"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    db = duckdb.connect(str(work / "integration.duckdb"))
    db.execute("SET threads=4")
    db.execute("SET memory_limit='3GB'")
    issues = []
    try:
        print("Parsing ontology and GPI catalogs", flush=True)
        terms, edges, ontology_version = load_ontology(data / manifest["go"]["path"])
        entities = [
            row
            for source in manifest.values()
            if source["kind"] == "gpi"
            for row in gpi_rows(data / source["path"], source, issues)
        ]
        human = list(hgnc_rows(data / manifest["hgnc"]["path"]))
        load_rows(db, "entities", entities)
        load_rows(db, "terms", terms)
        load_rows(db, "ontology_edges", edges)
        eco = eco_mapping(data / manifest["eco_mapping"]["path"])
        print("Loading source annotation records", flush=True)
        count = load_rows(
            db,
            "source_annotations",
            (
                row
                for source in manifest.values()
                if source["kind"] == "gaf"
                for row in gaf_rows(data / source["path"], source, eco)
            ),
        )
        active_subjects = {
            r[0]
            for r in db.execute(
                "SELECT db_object_id FROM source_annotations UNION SELECT canonical_subject_id FROM source_annotations"
            ).fetchall()
        }
        genes, mapping, identifiers = integrate_entities(entities, human, issues, active_subjects)
        load_rows(db, "gene_catalog", genes)
        load_rows(db, "subject_gene", mapping)
        load_rows(db, "identifier_links", identifiers)
        print(f"Integrating {count:,} annotation records", flush=True)
        # GAF-only subjects stay visible, without a guessed identifier mapping.
        db.execute("""CREATE TABLE annotation_subjects AS
          SELECT a.*, coalesce(s.gene_id, c.gene_id, a.canonical_subject_id) AS gene_id,
            coalesce(s.mapping_status, c.mapping_status, 'unresolved') AS mapping_status
          FROM source_annotations a
          LEFT JOIN subject_gene s ON a.db_object_id=s.subject_id
          LEFT JOIN subject_gene c ON a.canonical_subject_id=c.subject_id""")
        db.execute("""INSERT INTO gene_catalog
          SELECT gene_id AS id, min(symbol), min(name), list_distinct(flatten(list(synonyms))),
            list_distinct(flatten(list([db_object_id, canonical_subject_id]))), min(taxon_id), min(species)
          FROM annotation_subjects WHERE gene_id NOT IN (SELECT id FROM gene_catalog) GROUP BY gene_id""")
        print("Attaching repository gene reviews", flush=True)
        gene_table = db.execute("SELECT * FROM gene_catalog").to_arrow_table()
        review_rows = read_reviews(reviews, gene_table.to_pylist())
        if review_rows:
            load_rows(db, "reviews", review_rows)
            db.execute("""CREATE TABLE gene_reviews AS SELECT gene_id,
              string_agg(DISTINCT review_description, '\n\n') AS review_description,
              list_distinct(flatten(list(review_actions))) AS review_actions,
              string_agg(DISTINCT review_path, '|') AS review_path
              FROM reviews GROUP BY gene_id""")
        else:
            db.execute("""CREATE TABLE gene_reviews(gene_id VARCHAR, review_description VARCHAR,
                        review_actions VARCHAR[], review_path VARCHAR)""")
        db.execute("""CREATE TEMP TABLE subsumed_gene_terms AS
          SELECT DISTINCT
            a.gene_id,
            a.isoform_context,
            a.relation,
            a.annotation_extensions,
            ancestor_id AS ontology_class_id
          FROM (
            SELECT DISTINCT
              gene_id,
              CASE WHEN gene_product_form_id = canonical_subject_id THEN '' ELSE coalesce(gene_product_form_id, '') END AS isoform_context,
              coalesce(relation, '') AS relation,
              coalesce(annotation_extensions, '') AS annotation_extensions,
              ontology_class_id
            FROM annotation_subjects
            WHERE NOT negation
          ) a
          JOIN terms t ON a.ontology_class_id = t.id,
          UNNEST(coalesce(t.isa_partof_closure, [a.ontology_class_id])) AS u(ancestor_id)
          WHERE ancestor_id <> a.ontology_class_id""")
        db.execute("""CREATE TABLE annotations AS SELECT
          a.* EXCLUDE (symbol, name, synonyms, raw_record),
          g.symbol AS gene_symbol, g.name AS gene_name, g.identifiers AS gene_identifiers,
          coalesce(t.label, a.ontology_class_id) AS term_label,
          coalesce(t.is_a_closure, [a.ontology_class_id]) AS is_a_closure,
          coalesce(t.isa_partof_closure, [a.ontology_class_id]) AS isa_partof_closure,
          r.gene_id IS NOT NULL AS has_review,
          (NOT a.negation AND sgt.ontology_class_id IS NOT NULL) AS is_redundant,
          concat_ws(' ', a.db_object_id, a.gene_id, g.symbol, g.name,
            array_to_string(g.identifiers, ' '), array_to_string(g.synonyms, ' '),
            a.ontology_class_id, t.label, array_to_string(a.references, ' ')) AS search_text
          FROM annotation_subjects a JOIN gene_catalog g ON a.gene_id=g.id
          LEFT JOIN terms t ON a.ontology_class_id=t.id
          LEFT JOIN gene_reviews r ON a.gene_id=r.gene_id
          LEFT JOIN subsumed_gene_terms sgt
            ON a.gene_id = sgt.gene_id
            AND (CASE WHEN a.gene_product_form_id = a.canonical_subject_id THEN '' ELSE coalesce(a.gene_product_form_id, '') END) = sgt.isoform_context
            AND coalesce(a.relation, '') = sgt.relation
            AND coalesce(a.annotation_extensions, '') = sgt.annotation_extensions
            AND a.ontology_class_id = sgt.ontology_class_id""")
        print("Indexing annotation review assessments", flush=True)
        index_reviews(db, review_rows)
        index_product_types(db)
        db.execute("""CREATE TABLE genes AS SELECT g.*,
          CASE WHEN starts_with(g.id, 'HGNC:') OR starts_with(g.id, 'PomBase:')
                    OR starts_with(g.id, 'WB:WBGene') THEN 'gene'
               WHEN starts_with(g.id, 'ComplexPortal:') THEN 'complex' ELSE 'gene_product' END AS record_kind,
          coalesce(p.gene_product_types, ['Unspecified']) AS gene_product_types,
          coalesce(p.gene_product_type_ids, []::VARCHAR[]) AS gene_product_type_ids,
          coalesce(s.annotation_count, 0) AS annotation_count,
          coalesce(s.annotated_subject_ids, []::VARCHAR[]) AS annotated_subject_ids,
          coalesce(s.evidence_codes, []::VARCHAR[]) AS evidence_codes,
          coalesce(s.term_ids, []::VARCHAR[]) AS term_ids,
          coalesce(s.isa_partof_closure, []::VARCHAR[]) AS isa_partof_closure,
          r.gene_id IS NOT NULL AS has_review, coalesce(r.review_description, '') AS review_description,
          coalesce(r.review_actions, []::VARCHAR[]) AS review_actions, coalesce(r.review_path, '') AS review_path,
          concat_ws(' ', g.id, g.symbol, g.name, array_to_string(g.identifiers, ' '),
            array_to_string(g.synonyms, ' '), r.review_description) AS search_text
          FROM gene_catalog g LEFT JOIN (
            SELECT gene_id, count(*) AS annotation_count,
              list_sort(list(DISTINCT db_object_id)) AS annotated_subject_ids,
              list_sort(list(DISTINCT evidence_code)) AS evidence_codes,
              list_sort(list(DISTINCT ontology_class_id) FILTER (WHERE NOT negation)) AS term_ids,
              list_sort(list_distinct(flatten(list(isa_partof_closure) FILTER (WHERE NOT negation)))) AS isa_partof_closure
            FROM annotations GROUP BY gene_id
          ) s ON g.id=s.gene_id LEFT JOIN gene_reviews r ON g.id=r.gene_id
          LEFT JOIN gene_product_types p ON g.id=p.gene_id""")
        db.execute("""ALTER TABLE terms ADD COLUMN search_text VARCHAR""")
        load_species(db, data / manifest["taxonomy"]["path"], manifest["taxonomy"]["loaded_species"])
        hierarchies = index_hierarchies(db, terms)
        db.execute("""UPDATE terms SET search_text=concat_ws(' ', id, label, definition,
                     array_to_string(synonyms, ' '), array_to_string(alt_ids, ' '))""")
        print("Integrating GO-CAM, module and family snapshots", flush=True)
        knowledge_report = load_knowledge(db, repository, signature["repository"])
        print("Integrating research reports and artifacts", flush=True)
        reports_report = load_reports(db, repository, signature["reports"], work)
        print("Integrating rules and mappings", flush=True)
        rules_report = load_rules(db, repository, signature["rules"])
        print("Integrating GO-owned reactions and chemicals", flush=True)
        reactions_report = load_reactions(db, data, manifest)
        print("Rendering chemical structures and loading cell differentiation", flush=True)
        structures_report = load_structures(db, data, manifest, work)
        cells_report = load_cell_types(db, data, manifest)
        print("Integrating projects", flush=True)
        projects_report = load_projects(db, repository, signature["projects"], work)
        print("Indexing references", flush=True)
        references_report = load_references(db, repository, publications, review_rows)
        print("Comparing preloaded external annotations", flush=True)
        predictions_report = load_sets(db, external, work, manifest, terms, ontology_version)
        counts = {}
        for collection in (
            "genes",
            "annotations",
            "terms",
            "gocams",
            "modules",
            "families",
            "reports",
            "projects",
            "rules",
            "reactions",
            "chemicals",
            "reaction_participants",
            "cell_types",
            "differentiations",
            "references",
        ):
            counts[collection] = db.execute(f'SELECT count(*) FROM "{collection}"').fetchone()[0]
            path = str((work / f"{collection}.parquet").resolve()).replace("'", "''")
            db.execute(f"""COPY (SELECT * FROM "{collection}" ORDER BY id) TO '{path}'
                           (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 10000)""")
        report = {
            "knowledge": knowledge_report,
            "external_predictions": predictions_report,
            "research_reports": reports_report,
            "projects": projects_report,
            "references": references_report,
            "rules": rules_report,
            "reactions": reactions_report,
            "chemical_structures": structures_report,
            "cell_differentiation": cells_report,
            "source_issues": issues,
            "mapping_status": dict(
                db.execute("SELECT mapping_status, count(*) FROM annotation_subjects GROUP BY 1").fetchall()
            ),
            "missing_go_terms": db.execute(
                "SELECT DISTINCT ontology_class_id FROM annotations WHERE ontology_class_id NOT IN (SELECT id FROM terms) ORDER BY 1"
            ).fetchall(),
            "reviewed_genes": db.execute("SELECT count(*) FROM genes WHERE has_review").fetchone()[0],
            "gaf_records": count,
            "catalog_policy": "All HGNC genes, PomBase and WormBase primary objects, mapped GPI objects, and annotated subjects; other GPI objects retained in entities.",
            "gene_record_kinds": dict(
                db.execute("SELECT record_kind, count(*) FROM genes GROUP BY 1").fetchall()
            ),
        }
        if counts["annotations"] != count:
            raise ValueError("Identifier joins multiplied or dropped annotation rows")
        if db.execute("SELECT count(*) FROM (SELECT id FROM genes GROUP BY 1 HAVING count(*)>1)").fetchone()[
            0
        ]:
            raise ValueError("Duplicate gene identities")
        headers = {
            k: source_header(data / v["path"])
            for k, v in manifest.items()
            if v["kind"] in ("gaf", "gpi", "obo")
        }
        result = {
            "build_id": build_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "ontology_version": ontology_version,
            "hierarchies": hierarchies,
            "species": manifest["taxonomy"]["loaded_species"],
            "sources": manifest,
            "source_headers": headers,
            "signature": signature,
            "review_policy": "Snapshot review context; upstream annotations are unchanged.",
            "files": {f"{c}.parquet": digest(work / f"{c}.parquet") for c in counts},
        }
        result["external_predictions"] = predictions_report
        for name in ("external_sets.parquet", "external_claims.parquet"):
            result["files"][name] = digest(work / name)
        for path in sorted((work / "external").rglob("*")):
            if path.is_file():
                result["files"][path.relative_to(work).as_posix()] = digest(path)
        write_json(work / "report.json", report)
        write_json(work / "manifest.json", result)
        db.execute("CHECKPOINT")
    finally:
        db.close()
    os.replace(work, destination)
    write_json(data / "current.json", {"build_id": build_id})
    print(
        json.dumps(
            {"build_id": build_id, "counts": counts, "report": str(destination / "report.json")}, indent=2
        )
    )
    return result
