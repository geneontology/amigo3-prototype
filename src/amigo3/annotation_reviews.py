"""Index conservative annotation/review links without applying review decisions."""

import json
from collections import defaultdict

import pyarrow as pa

from amigo3.gene import matches
from amigo3.parse import review_subject_id


def index_reviews(db, reviews):
    entries, candidates = {}, defaultdict(list)
    for source in reviews:
        document = json.loads(source["review_document"])
        for index, entry in enumerate(document.get("existing_annotations") or []):
            if (entry.get("review") or {}).get("action") == "NEW":
                continue
            identifier = f"{source['review_sha256'][:16]}:{index}"
            item = {
                "id": identifier,
                "path": source["review_path"],
                "sha256": source["review_sha256"],
                "subject_id": review_subject_id(document["id"]),
                "entry_json": json.dumps(entry),
            }
            entries[identifier] = item
            key = source["gene_id"], (entry.get("term") or {}).get("id"), entry.get("evidence_type")
            candidates[key].append((item, entry))
    schema = pa.schema([(key, pa.string()) for key in ("id", "path", "sha256", "subject_id", "entry_json")])
    db.register("review_entry_batch", pa.Table.from_pylist(list(entries.values()), schema=schema))
    db.execute("CREATE TABLE annotation_review_entries AS SELECT * FROM review_entry_batch")
    db.unregister("review_entry_batch")
    links = set()
    # Read only annotations whose gene has a review. Candidate keys avoid a
    # quadratic scan, and the shared matcher retains reference/isoform/NOT rules.
    rows = (
        db.execute("""SELECT id, gene_id, ontology_class_id, evidence_code, "references",
        negation, gene_product_form_id, canonical_subject_id, gene_identifiers
        FROM annotations WHERE has_review""")
        .to_arrow_table()
        .to_pylist()
    )
    for annotation in rows:
        key = annotation["gene_id"], annotation["ontology_class_id"], annotation["evidence_code"]
        for item, entry in candidates.get(key, ()):
            if matches(annotation, entry, item["subject_id"], annotation["gene_identifiers"]):
                links.add((annotation["id"], item["id"]))
    link_schema = pa.schema([("annotation_id", pa.string()), ("review_entry_id", pa.string())])
    db.register(
        "review_link_batch",
        pa.Table.from_pylist(
            [{"annotation_id": a, "review_entry_id": r} for a, r in sorted(links)], schema=link_schema
        ),
    )
    db.execute("CREATE TABLE annotation_review_links AS SELECT * FROM review_link_batch")
    db.unregister("review_link_batch")
    summaries = defaultdict(list)
    for annotation_id, review_id in sorted(links):
        summaries[annotation_id].append(entries[review_id])
    projections = []
    for annotation_id, items in summaries.items():
        assessments = [json.loads(item["entry_json"]).get("review") or {} for item in items]
        projections.append(
            {
                "annotation_id": annotation_id,
                "review_entry_ids": [item["id"] for item in items],
                "review_actions": sorted({r.get("action") or "PENDING" for r in assessments}),
                "review_replacement_ids": sorted(
                    {
                        t["id"]
                        for r in assessments
                        for t in r.get("proposed_replacement_terms") or []
                        if t.get("id")
                    }
                ),
                "review_has_support": any(r.get("supported_by") for r in assessments),
                "review_text": " ".join(
                    str(r.get(k) or "") for r in assessments for k in ("summary", "reason")
                ),
            }
        )
    projection_schema = pa.schema(
        [
            ("annotation_id", pa.string()),
            ("review_entry_ids", pa.list_(pa.string())),
            ("review_actions", pa.list_(pa.string())),
            ("review_replacement_ids", pa.list_(pa.string())),
            ("review_has_support", pa.bool_()),
            ("review_text", pa.string()),
        ]
    )
    db.register("review_projection", pa.Table.from_pylist(projections, schema=projection_schema))
    db.execute("""CREATE TABLE reviewed_annotations AS SELECT a.* EXCLUDE(search_text),
        r.annotation_id IS NOT NULL AS has_annotation_review,
        coalesce(r.review_entry_ids, []::VARCHAR[]) AS review_entry_ids,
        coalesce(r.review_actions, []::VARCHAR[]) AS review_actions,
        coalesce(r.review_replacement_ids, []::VARCHAR[]) AS review_replacement_ids,
        coalesce(r.review_has_support, false) AS review_has_support,
        coalesce(len(r.review_entry_ids), 0)::BIGINT AS review_count,
        coalesce(len(r.review_entry_ids)>1, false) AS review_multiple,
        concat_ws(' ', a.search_text, r.review_text) AS search_text
        FROM annotations a LEFT JOIN review_projection r ON a.id=r.annotation_id""")
    db.unregister("review_projection")
    db.execute("DROP TABLE annotations")
    db.execute("ALTER TABLE reviewed_annotations RENAME TO annotations")
