"""Screen a predetermined, study-balanced panel beyond Hallmarks.

Fetch freezes source membership/provenance before scoring. Screen reuses the
production ConnectivityTest on one prepared reference graph. Confirm uses the
full production enrichment path, with a per-publication cap for diversity.
"""

import argparse
import csv
import gzip
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx
from screen_network_examples import FIELDS, run

from amigo3.enrichment import EnrichmentRequest, benjamini_hochberg
from amigo3.genesets import map_members
from amigo3.network_enrichment import ConnectivityTest, enrich_network, prepare

SERVICE = "https://mygeneset.info/v1"
QUERIES = {
    "CGP": "taxid:9606 AND msigdb.subcategory.code:cgp",
    "C7": "taxid:9606 AND msigdb.category.code:c7",
    "C6": "taxid:9606 AND msigdb.category.code:c6",
}
QUOTAS = {"CGP": 240, "C7": 120, "C6": 40}
ANCHORS = ["SANA_RESPONSE_TO_IFNG_UP", "FRASOR_RESPONSE_TO_ESTRADIOL_UP"]
SELECTION = (
    "Before scoring: human-mapped CGP/C7/C6 signatures ending _UP or _DN, "
    "20–1000 source members and a publication ID; choose one contrast per publication "
    "within each collection by SHA256('network-illustrations-v1|' + collection + '|' + key), "
    "retaining both directions where available. Quotas CGP=240, C7=120, C6=40 sets; "
    "plus two named previously bundled experimental anchors (SANA IFNG and FRASOR estradiol). The bundled DANG MYC set is excluded because it compiles a target database rather than one experimental contrast. No score-based fetch selection. "
    "Confirmation: strongest sets, at most two per publication; signatures explicitly described as meta-analysis or derived from multiple datasets are not eligible illustrations. BY is within-set, not panel-wide."
)


def order(collection, key):
    return hashlib.sha256(f"network-illustrations-v1|{collection}|{key}".encode()).hexdigest()


def fetch(build, out):
    cache = Path(".runtime/perturbation-sources")
    cache.mkdir(parents=True, exist_ok=True)
    catalog, selected, inventories = [], {}, {}
    with httpx.Client(timeout=60) as client:
        for collection, query in QUERIES.items():
            hits, total = [], None
            offset = 0
            while total is None or offset < total:
                response = client.get(
                    f"{SERVICE}/query",
                    params={
                        "q": query,
                        "size": 1000,
                        "from": offset,
                        "fields": "name,description,count,taxid,msigdb",
                    },
                ).raise_for_status()
                data = response.json()
                total = data["total"]
                hits.extend(data["hits"])
                offset += len(data["hits"])
                assert data["hits"] or offset == total
            assert len({d["_id"] for d in hits}) == total
            inventories[collection] = total
            by_study = defaultdict(lambda: defaultdict(list))
            for doc in hits:
                doc["screen_collection"] = collection
                catalog.append(doc)
                pmid = str(doc.get("msigdb", {}).get("pmid") or "")
                if (
                    doc["_id"] == "DANG_MYC_TARGETS_UP"
                    or not pmid
                    or not 20 <= doc.get("count", 0) <= 1000
                    or not re.search(r"_(UP|DN)$", doc["_id"])
                ):
                    continue
                contrast = re.sub(r"_(UP|DN)$", "", doc["_id"])
                by_study[pmid][contrast].append(doc)
            count = 0
            for pmid in sorted(by_study, key=lambda x: order(collection, x)):
                contrasts = by_study[pmid]
                contrast = min(contrasts, key=lambda x: order(collection, x))
                for doc in sorted(contrasts[contrast], key=lambda d: d["_id"]):
                    if count >= QUOTAS[collection]:
                        break
                    selected[doc["_id"]] = {"collection": collection, "selection": "study_balanced"}
                    count += 1
                if count >= QUOTAS[collection]:
                    break
            print(
                collection,
                "catalog",
                total,
                "eligible publications",
                len(by_study),
                "selected",
                count,
                flush=True,
            )
        for identifier in ANCHORS:
            selected.setdefault(identifier, {"collection": "CGP", "selection": "prespecified_anchor"})
        (out / "catalog.json.gz").write_bytes(gzip.compress(json.dumps(catalog).encode(), mtime=0))
        metadata = client.get(f"{SERVICE}/metadata").raise_for_status().json()

        # Bulk read avoids hundreds of individual requests and API rate limits.
        missing = [
            identifier for identifier in sorted(selected) if not (cache / f"{identifier}.json").exists()
        ]
        for offset in range(0, len(missing), 50):
            identifiers = missing[offset : offset + 50]
            response = client.post(
                f"{SERVICE}/geneset", data={"ids": ",".join(identifiers)}
            ).raise_for_status()
            batch = response.json()
            assert {doc.get("_id") for doc in batch} == set(identifiers)
            for doc in batch:
                (cache / f"{doc['_id']}.json").write_text(json.dumps(doc))
        docs = []
        for identifier in sorted(selected):
            content = (cache / f"{identifier}.json").read_bytes()
            docs.append((json.loads(content), hashlib.sha256(content).hexdigest()))
    print("Downloaded", len(docs), "sets; mapping identifiers in one batch", flush=True)
    flat = [gene for doc, _ in docs for gene in doc["genes"]]
    with duckdb.connect(str(build / "integration.duckdb"), read_only=True) as db:
        mapped = map_members(db, "human", flat)["members"]
    panel, offset = [], 0
    for doc, checksum in docs:
        rows = mapped[offset : offset + len(doc["genes"])]
        offset += len(rows)
        genes = list(dict.fromkeys(row["gene"]["id"] if row["gene"] else row["label"] for row in rows))
        mapped_ids = sorted({row["gene"]["id"] for row in rows if row["gene"]})
        panel.append(
            {
                "id": doc["_id"],
                **selected[doc["_id"]],
                "description": doc.get("description"),
                "source_url": f"{SERVICE}/geneset/{doc['_id']}",
                "source_sha256": checksum,
                "source_members": len(doc["genes"]),
                "mapped": len(mapped_ids),
                "unmapped": sum(not row["gene"] for row in rows),
                "mapped_ids": mapped_ids,
                "genes": genes,
                "provenance": doc.get("msigdb", {}),
            }
        )
    payload = {
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE,
        "queries": QUERIES,
        "catalog_counts": inventories,
        "build_id": build.name,
        "source_versions": {"msigdb": metadata["src"]["msigdb"]["version"]},
        "license": "MSigDB CC BY 4.0; https://www.gsea-msigdb.org/gsea/msigdb_license_terms.jsp",
        "selection": SELECTION,
        "provenance_exclusions": {
            "DANG_MYC_TARGETS_UP": "Target database compilation, not one experimental contrast; removed before scoring."
        },
        "illustration_exclusions": {
            doc[
                "id"
            ]: "Description explicitly identifies a multi-dataset meta-analysis; retained in screen, excluded from experimental illustrations."
            for doc in panel
            if re.search(r"meta.analysis|multiple datasets", doc.get("description", ""), re.I)
        },
        "sets": panel,
    }
    (out / "inputs.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(
        "Froze",
        len(panel),
        "sets from",
        len({str(d["provenance"]["pmid"]) for d in panel}),
        "publications",
        flush=True,
    )


def screen(build, out):
    payload = json.loads((out / "inputs.json").read_text())
    assert payload["build_id"] == build.name
    panel = payload["sets"]
    request = EnrichmentRequest(
        genes=panel[0]["genes"],
        species="human",
        method="neighborhood",
        network_taxon="NCBITaxon:9606",
        network_permutations=1999,
        network_seed=0,
        min_term_size=2,
        max_term_size=1000,
        max_results=5000,
    )
    ctx = prepare(build, request)
    memberships = ctx["memberships"]
    harmonic = sum(1 / i for i in range(1, len(memberships) + 1))
    all_scores = {}
    with (out / "screen.tsv").open("w") as handle:
        writer = csv.DictWriter(handle, FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for index, doc in enumerate(panel):
            start = time.monotonic()
            used = set(doc["mapped_ids"]) & ctx["universe"]
            if not used:
                print(doc["id"], "no eligible genes", flush=True)
                all_scores[doc["id"]] = {"eligible": 0, "terms": []}
                continue
            test = ConnectivityTest(ctx["adjacency"], used, memberships)
            observed, ps, null = test.run(1999, 0, "greater")
            bh = benjamini_hochberg(ps, len(memberships))
            terms = []
            for i, (identifier, members) in enumerate(memberships.items()):
                expected = test.expectation(members)
                terms.append(
                    {
                        "id": identifier,
                        **ctx["descriptions"][identifier],
                        "study_count": len(used & members),
                        "term_size": len(members),
                        "connections": observed[i],
                        "expected": expected,
                        "fold_enrichment": observed[i] / expected if expected else None,
                        "p_value": ps[i],
                        "fdr": bh[i],
                        "by_fdr": min(1.0, bh[i] * harmonic),
                    }
                )
            terms.sort(key=lambda t: (t["by_fdr"], t["p_value"], -t["study_count"], t["id"]))
            sig = sum(t["by_fdr"] <= 0.05 for t in terms)
            all_scores[doc["id"]] = {
                "eligible": len(used),
                "null": null,
                "terms": [
                    {
                        k: t[k]
                        for k in [
                            "id",
                            "study_count",
                            "term_size",
                            "connections",
                            "expected",
                            "p_value",
                            "fdr",
                            "by_fdr",
                        ]
                    }
                    for t in terms
                ],
            }
            # Verify reuse of fixed context against the complete production path.
            if index < 3:
                full = enrich_network(build, request.model_copy(update={"genes": doc["genes"]}), build.name)
                assert full["counts"]["study"] == len(used)
                by_id = {t["id"]: t for t in full["terms"]}
                assert all(
                    all(
                        t[k] == by_id[t["id"]][k]
                        for k in ["p_value", "by_fdr", "connections", "expected", "study_count"]
                    )
                    for t in terms
                )
                (out / "screen-protocol.json").write_text(
                    json.dumps(
                        {
                            "parameters": full["parameters"],
                            "build_id": build.name,
                            "coverage": full["network"]["coverage"],
                            "selection": SELECTION,
                            "neighborhoods": {
                                k: v
                                for k, v in full["network"]["neighborhoods"].items()
                                if k not in {"nodes", "edges"}
                            },
                            "implementation": "Production prepare once, then production ConnectivityTest for every study; all scores cross-checked against full enrich_network for first three inputs.",
                        },
                        indent=2,
                    )
                    + "\n"
                )
            for rank, term in enumerate(terms[:10], 1):
                writer.writerow(
                    dict(
                        geneset=doc["id"],
                        draws=1999,
                        seed=0,
                        input=len(doc["genes"]),
                        eligible=len(used),
                        tests=len(memberships),
                        significant_by=sig,
                        rank=rank,
                        neighborhood=term["id"],
                        label=term["label"],
                        seeds=",".join(g["symbol"] for g in term["seeds"]),
                        bp="; ".join(g["label"] for g in term["go_context"]),
                        study_members=term["study_count"],
                        members=term["term_size"],
                        connections=term["connections"],
                        expected=term["expected"],
                        fold=term["fold_enrichment"],
                        p_value=term["p_value"],
                        by_fdr=term["by_fdr"],
                        bh_fdr=term["fdr"],
                        url="",
                    )
                )
            handle.flush()
            if index % 10 == 0 or sig:
                print(
                    index + 1,
                    "/",
                    len(panel),
                    doc["id"],
                    "eligible",
                    len(used),
                    "best_BY",
                    round(terms[0]["by_fdr"], 6),
                    "significant",
                    sig,
                    "seconds",
                    round(time.monotonic() - start, 1),
                    flush=True,
                )
    (out / "screen-scores.json.gz").write_bytes(gzip.compress(json.dumps(all_scores).encode(), mtime=0))


def confirm(build, out, top):
    payload = json.loads((out / "inputs.json").read_text())
    docs = {d["id"]: d for d in payload["sets"]}
    with (out / "screen.tsv").open() as f:
        rows = [r for r in csv.DictReader(f, delimiter="\t") if r["rank"] == "1"]
    rows.sort(
        key=lambda r: (float(r["by_fdr"]), -int(r["significant_by"]), float(r["p_value"]), r["geneset"])
    )
    chosen, publications = [], defaultdict(int)
    for row in rows:
        doc = docs[row["geneset"]]
        if re.search(r"meta.analysis|multiple datasets", doc.get("description", ""), re.I):
            continue
        pmid = str(doc["provenance"]["pmid"])
        if publications[pmid] >= 2:
            continue
        publications[pmid] += 1
        chosen.append(doc["id"])
        if len(chosen) == top:
            break
    # Shared driver records full production results and repeats all three seeds.
    run(build, out, "confirm", top, [0, 1, 2], selected_ids=chosen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["fetch", "screen", "confirm"])
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("docs/data/perturbation-example-screen"))
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.phase == "fetch":
        fetch(args.build, args.out)
    elif args.phase == "screen":
        screen(args.build, args.out)
    else:
        confirm(args.build, args.out, args.top)


if __name__ == "__main__":
    main()
