"""Gene detail projections and conservative links to frozen review context."""

import json
from pathlib import Path

import duckdb

from amigo3.parse import review_subject_id


def matches(annotation: dict, entry: dict, accession: str, identifiers: list[str]) -> bool:
    """Match context keys, not an exact annotation identity or applied decision.

    Review YAML does not usually specify relation, extensions or with/from. A
    match can therefore cover several upstream rows; retain those independently.
    """
    if (entry.get("review") or {}).get("action") == "NEW":
        return False
    reference = entry.get("original_reference_id")
    if not reference or reference not in annotation["references"]:
        return False
    if (entry.get("term") or {}).get("id") != annotation["ontology_class_id"]:
        return False
    if entry.get("evidence_type") != annotation["evidence_code"]:
        return False
    if bool(entry.get("negated")) != annotation["negation"]:
        return False
    isoform = review_subject_id(entry["isoform"]) if entry.get("isoform") else ""
    form = annotation["gene_product_form_id"]
    # Providers may repeat the base accession in GAF column 17. That is not
    # an isoform-specific assertion; preserve distinct -1/-2 and PRO_ forms.
    if form == annotation["canonical_subject_id"]:
        form = ""
    if isoform != form:
        return False
    subject = annotation["canonical_subject_id"]
    if subject.startswith("UniProtKB:"):
        return subject == accession
    # MOD annotations and UniProt reviews meet through the explicit entity catalog.
    return accession in identifiers and not subject.startswith("ComplexPortal:")


EXPERIMENTAL_CODES = frozenset({"EXP", "IDA", "IPI", "IMP", "IGI", "IEP", "HTP", "HDA", "HMP", "HGI", "HEP"})
CURATED_CODES = frozenset({"IKR", "IRD", "ISS", "ISO", "ISA", "ISM", "IGC", "RCA", "TAS", "NAS", "IC"})


def evidence_group(code: str) -> str:
    if code == "IBA":
        return "iba"
    if code in EXPERIMENTAL_CODES:
        return "experimental"
    if code in CURATED_CODES:
        return "curated"
    if code == "IEA":
        return "iea"
    return "other"


def priority_rank(code: str) -> int:
    if code == "IBA":
        return 0
    if code in EXPERIMENTAL_CODES:
        return 1
    if code in CURATED_CODES:
        return 2
    if code == "IEA":
        return 3
    if code == "ND":
        return 5
    return 4


def _core_function_terms(core_fn: dict) -> list[dict]:
    found = []
    for key in (
        "molecular_function",
        "contributes_to_molecular_function",
        "proposed_molecular_function",
        "in_complex",
    ):
        val = core_fn.get(key)
        if isinstance(val, dict) and val.get("id"):
            found.append({"id": val["id"], "label": val.get("label") or val["id"], "role": key})
    for key in ("directly_involved_in", "locations"):
        for val in core_fn.get(key) or []:
            if isinstance(val, dict) and val.get("id"):
                found.append({"id": val["id"], "label": val.get("label") or val["id"], "role": key})
    return found


def gene_detail(path: Path, record: dict) -> dict:
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        annotations = (
            db.execute(
                """SELECT * EXCLUDE (search_text, gene_identifiers, is_a_closure)
               FROM annotations WHERE gene_id=? ORDER BY aspect, term_label, evidence_code, id""",
                [record["id"]],
            )
            .to_arrow_table()
            .to_pylist()
        )
        # Earlier builds have only gene-level summaries. Do not read mutable YAML
        # to silently enrich a frozen build with newer review decisions.
        columns = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='reviews'"
        ).fetchall()
        has_snapshots = ("review_document",) in columns
        sources = (
            db.execute(
                "SELECT review_path, review_sha256, review_document FROM reviews WHERE gene_id=? ORDER BY review_path",
                [record["id"]],
            ).fetchall()
            if has_snapshots
            else []
        )
        reviews, entries = [], []
        core_term_ids: set[str] = set()
        for source, checksum, raw in sources:
            document = json.loads(raw)
            review_id = checksum[:16]
            reviews.append({"id": review_id, "path": source, "sha256": checksum, "document": document})
            for cf in document.get("core_functions") or []:
                for item in _core_function_terms(cf):
                    core_term_ids.add(item["id"])
            for index, entry in enumerate(document.get("existing_annotations") or []):
                entries.append(
                    {
                        "id": f"{review_id}:{index}",
                        "review_id": review_id,
                        "subject_id": review_subject_id(document["id"]),
                        "entry": entry,
                        "annotation_ids": [],
                    }
                )
        term_closures: dict[str, set[str]] = {}
        if core_term_ids:
            placeholders = ",".join("?" for _ in core_term_ids)
            for tid, closure in db.execute(
                f"SELECT id, coalesce(isa_partof_closure, [id]) FROM terms WHERE id IN ({placeholders})",
                list(core_term_ids),
            ).fetchall():
                term_closures[tid] = set(closure or [tid])

    def redundancy_context(row: dict) -> tuple[str, str, str]:
        form = row.get("gene_product_form_id") or ""
        if form == (row.get("canonical_subject_id") or ""):
            form = ""
        return (
            form,
            row.get("relation") or "",
            row.get("annotation_extensions") or "",
        )

    positive_closures: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    for annotation in annotations:
        closure = set(annotation.pop("isa_partof_closure", None) or [annotation["ontology_class_id"]])
        annotation["_closure"] = closure
        if not annotation["negation"]:
            ctx = redundancy_context(annotation)
            positive_closures.setdefault(ctx, {}).setdefault(annotation["ontology_class_id"], set()).update(
                closure
            )
        if tid := annotation["ontology_class_id"]:
            term_closures.setdefault(tid, closure)

    subsumed_by_context: dict[tuple[str, str, str], set[str]] = {}
    for ctx, closures_by_term in positive_closures.items():
        subsumed: set[str] = set()
        for tid, closure in closures_by_term.items():
            for ancestor in closure:
                if ancestor != tid:
                    subsumed.add(ancestor)
        subsumed_by_context[ctx] = subsumed

    for annotation in annotations:
        if "is_redundant" not in annotation or annotation["is_redundant"] is None:
            ctx = redundancy_context(annotation)
            annotation["is_redundant"] = bool(
                not annotation["negation"]
                and annotation["ontology_class_id"] in subsumed_by_context.get(ctx, set())
            )
        annotation["review_entry_ids"] = []
        matched_actions: list[str] = []
        for entry in entries:
            if matches(annotation, entry["entry"], entry["subject_id"], record["identifiers"]):
                annotation["review_entry_ids"].append(entry["id"])
                entry["annotation_ids"].append(annotation["id"])
                action = (entry["entry"].get("review") or {}).get("action") or "PENDING"
                if action not in matched_actions:
                    matched_actions.append(action)
        if not annotation.get("review_actions"):
            annotation["review_actions"] = matched_actions

    positive_iba = [a for a in annotations if a["evidence_code"] == "IBA" and not a["negation"]]
    iba_by_term: dict[str, dict] = {}
    for a in positive_iba:
        tid = a["ontology_class_id"]
        if tid not in iba_by_term:
            iba_by_term[tid] = {
                "id": tid,
                "label": a["term_label"],
                "aspect": a["aspect"],
                "closure": a["_closure"],
                "is_redundant": bool(a["is_redundant"]),
                "actions": list(a.get("review_actions") or []),
            }

    core_iba_agreements = []
    all_core_closures: dict[str, set[str]] = {}
    for review in reviews:
        for cf_index, cf in enumerate(review["document"].get("core_functions") or []):
            cf_terms = _core_function_terms(cf)
            for item in cf_terms:
                all_core_closures[item["id"]] = term_closures.get(item["id"], {item["id"]})
            matches_list = []
            seen_iba = set()
            for item in cf_terms:
                cf_id = item["id"]
                cf_closure = term_closures.get(cf_id, {cf_id})
                for iba_id, iba_info in iba_by_term.items():
                    key = (cf_id, iba_id)
                    if key in seen_iba:
                        continue
                    relation = None
                    if iba_id == cf_id:
                        relation = "exact"
                    elif cf_id in iba_info["closure"]:
                        relation = "narrower"
                    elif iba_id in cf_closure:
                        relation = "broader"
                    if relation:
                        seen_iba.add(key)
                        matches_list.append(
                            {
                                "core_term_id": cf_id,
                                "core_term_label": item["label"],
                                "iba_term_id": iba_id,
                                "iba_term_label": iba_info["label"],
                                "relation": relation,
                                "iba_actions": iba_info["actions"],
                            }
                        )
            status = "none"
            if any(m["relation"] == "exact" for m in matches_list):
                status = "exact"
            elif matches_list:
                status = "close"
            elif positive_iba:
                status = "unmatched"
            core_iba_agreements.append(
                {
                    "review_id": review["id"],
                    "core_index": cf_index,
                    "status": status,
                    "has_iba": bool(positive_iba),
                    "matches": matches_list,
                }
            )

    iba_core_function = None
    if not all_core_closures and positive_iba:
        active_iba = [a for a in positive_iba if not a["is_redundant"]] or positive_iba
        seen_terms: set[str] = set()
        by_aspect: dict[str, list[dict]] = {"F": [], "P": [], "C": []}
        panther_ids: list[str] = []
        refs: list[str] = []
        for a in active_iba:
            tid = a["ontology_class_id"]
            if tid not in seen_terms:
                seen_terms.add(tid)
                by_aspect.setdefault(a["aspect"], []).append(
                    {"id": tid, "label": a["term_label"], "relation": a["relation"]}
                )
            for token in (a.get("with_or_from") or "").replace("|", ",").split(","):
                token = token.strip()
                if token.startswith("PANTHER:") and token not in panther_ids:
                    panther_ids.append(token)
            for ref in a.get("references") or []:
                if ref not in refs:
                    refs.append(ref)
        iba_core_function = {
            "source": "PAINT / IBA",
            "annotation_count": len(positive_iba),
            "non_redundant_term_count": len(seen_terms),
            "molecular_functions": by_aspect.get("F", []),
            "directly_involved_in": by_aspect.get("P", []),
            "locations": by_aspect.get("C", []),
            "panther_families": panther_ids,
            "references": refs,
        }

    for annotation in annotations:
        closure = annotation.pop("_closure")
        tid = annotation["ontology_class_id"]
        code = annotation["evidence_code"]
        annotation["evidence_group"] = evidence_group(code)
        annotation["priority_rank"] = priority_rank(code)

        core_rel = None
        if not annotation["negation"] and all_core_closures:
            if tid in all_core_closures:
                core_rel = "exact"
            elif any(cid in closure for cid in all_core_closures):
                core_rel = "narrower"
            elif any(tid in c_closure for c_closure in all_core_closures.values()):
                core_rel = "broader"
        annotation["core_agreement"] = core_rel

        iba_rel = None
        if not annotation["negation"] and iba_by_term:
            if tid in iba_by_term:
                iba_rel = "exact"
            elif any(iid in closure for iid in iba_by_term):
                iba_rel = "narrower"
            elif any(tid in info["closure"] for info in iba_by_term.values()):
                iba_rel = "broader"
        annotation["iba_agreement"] = iba_rel

        actions = set(annotation.get("review_actions") or [])
        if "ACCEPT" in actions or core_rel == "exact":
            ag_rank = 0
        elif "KEEP_AS_NON_CORE" in actions or core_rel in ("narrower", "broader"):
            ag_rank = 1
        elif code == "IBA" or iba_rel in ("exact", "narrower"):
            ag_rank = 2
        elif "MODIFY" in actions:
            ag_rank = 4
        elif "MARK_AS_OVER_ANNOTATED" in actions:
            ag_rank = 5
        elif "REMOVE" in actions or "DONT_ANNOTATE" in actions:
            ag_rank = 6
        else:
            ag_rank = 3
        annotation["agreement_rank"] = ag_rank

    annotations.sort(
        key=lambda a: (
            a["priority_rank"],
            bool(a["is_redundant"]),
            a["aspect"],
            a["term_label"],
            a["evidence_code"],
            a["id"],
        )
    )

    return {
        "record": record,
        "annotations": annotations,
        "annotation_count": len(annotations),
        "reviews": reviews,
        "review_entries": entries,
        "core_iba_agreements": core_iba_agreements,
        "iba_core_function": iba_core_function,
        "review_snapshots_available": has_snapshots,
        "review_match_policy": "Context matched by subject, term, evidence, reference, negation and isoform; relation, extensions and with/from are not adjudicated by this match. Decisions are not applied.",
    }
