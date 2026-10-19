"""Screen a frozen Hallmark panel for illustrative neighborhood results.

Run fetch once, screen all sets at default precision, then confirm the top sets
at higher precision with independent seeds. BY values are within each gene set;
selection across this panel is exploratory, not a panel-wide significance test.
"""

import argparse
import csv
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import httpx

from amigo3.enrichment import EnrichmentRequest
from amigo3.genesets import map_members
from amigo3.network_enrichment import enrich_network

SERVICE = "https://mygeneset.info/v1"
FIELDS = [
    "geneset",
    "draws",
    "seed",
    "input",
    "eligible",
    "tests",
    "significant_by",
    "rank",
    "neighborhood",
    "label",
    "seeds",
    "bp",
    "study_members",
    "members",
    "connections",
    "expected",
    "fold",
    "p_value",
    "by_fdr",
    "bh_fdr",
    "url",
]


def fetch(build, out):
    with httpx.Client(timeout=60) as client:
        query = "taxid:9606 AND msigdb.category.code:h"
        catalog = (
            client.get(f"{SERVICE}/query", params={"q": query, "size": 100, "fields": "name,count"})
            .raise_for_status()
            .json()
        )
        assert catalog["total"] == len(catalog["hits"]) == 50
        metadata = client.get(f"{SERVICE}/metadata").raise_for_status().json()

        def get(identifier):
            response = client.get(f"{SERVICE}/geneset/{identifier}").raise_for_status()
            return response.json(), hashlib.sha256(response.content).hexdigest()

        with ThreadPoolExecutor(max_workers=4) as pool:
            docs = list(pool.map(get, sorted(x["_id"] for x in catalog["hits"])))
    panel = []
    with duckdb.connect(str(build / "integration.duckdb"), read_only=True) as db:
        for doc, checksum in docs:
            assert doc["source"] == "msigdb" and str(doc["taxid"]) == "9606"
            mapped = map_members(db, "human", doc["genes"])
            panel.append(
                {
                    "id": doc["_id"],
                    "description": doc.get("description"),
                    "source_url": f"{SERVICE}/geneset/{doc['_id']}",
                    "source_sha256": checksum,
                    "source_members": len(doc["genes"]),
                    "mapped": mapped["mapped"],
                    "unmapped": mapped["unmapped"],
                    "genes": mapped["genes"],
                }
            )
    payload = {
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE,
        "query": query,
        "build_id": build.name,
        "source_versions": {k: v.get("version") for k, v in metadata.get("src", {}).items() if k == "msigdb"},
        "license": "MSigDB CC BY 4.0; https://www.gsea-msigdb.org/gsea/msigdb_license_terms.jsp",
        "sets": panel,
    }
    (out / "inputs.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Froze {len(panel)} Hallmark sets", flush=True)


def run(build, out, phase, top, seeds, selected_ids=None):
    payload = json.loads((out / "inputs.json").read_text())
    assert payload["build_id"] == build.name, "Use the input mapping's pinned build"
    panel = payload["sets"]
    draws = 1999 if phase == "screen" else 19999
    if phase == "confirm":
        with (out / "screen.tsv").open() as handle:
            rows = [r for r in csv.DictReader(handle, delimiter="\t") if r["rank"] == "1"]
        rows.sort(
            key=lambda r: (float(r["by_fdr"]), -int(r["significant_by"]), float(r["p_value"]), r["geneset"])
        )
        chosen = selected_ids or [r["geneset"] for r in rows[:top]]
        panel = sorted((x for x in panel if x["id"] in chosen), key=lambda x: chosen.index(x["id"]))
    else:
        seeds = [0]
    raw = Path(".runtime/network-example-screen")
    raw.mkdir(parents=True, exist_ok=True)
    with (out / f"{phase}.tsv").open("w") as handle:
        writer = csv.DictWriter(handle, FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for doc in panel:
            for seed in seeds:
                request = EnrichmentRequest(
                    genes=doc["genes"],
                    species="human",
                    method="neighborhood",
                    network_taxon="NCBITaxon:9606",
                    network_permutations=draws,
                    network_seed=seed,
                    max_results=5000,
                    min_term_size=2,
                    max_term_size=1000,
                    network_alternative="greater",
                    background=None,
                )
                start = time.monotonic()
                result = enrich_network(build, request, build.name)
                (raw / f"{doc['id']}-{draws}-{seed}.json").write_text(
                    json.dumps({"request": request.model_dump(), "result": result})
                )
                protocol = {
                    "build_id": build.name,
                    "parameters": {k: v for k, v in result["parameters"].items() if k != "network_seed"},
                    "seeds": seeds,
                    "selected_sets": [item["id"] for item in panel],
                    "coverage": result["network"]["coverage"],
                    "projection": result["network"]["policy"],
                    "neighborhoods": {
                        k: v
                        for k, v in result["network"]["neighborhoods"].items()
                        if k not in {"nodes", "edges"}
                    },
                    "selection": payload.get(
                        "selection",
                        "All 50 human Hallmarks for screening; top sets by minimum within-set BY FDR for confirmation. No panel-wide correction.",
                    ),
                }
                (out / f"{phase}-protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
                terms = sorted(
                    result["terms"], key=lambda t: (t["by_fdr"], t["p_value"], -t["study_count"], t["id"])
                )
                sig = sum(t["by_fdr"] <= 0.05 for t in terms)
                for rank, term in enumerate(terms[:10], 1):
                    url = "/enrichment?" + urlencode(
                        {
                            "genes": ",".join(doc["genes"]),
                            "species": "human",
                            "method": "neighborhood",
                            "view": build.name,
                            "network_permutations": draws,
                            "network_seed": seed,
                            "graph_term": term["id"],
                            "overview_top": 10,
                            "overview_min_study": 2,
                            "overview_fdr": 1,
                            "fdr": 0.05,
                        }
                    )
                    writer.writerow(
                        dict(
                            geneset=doc["id"],
                            draws=draws,
                            seed=seed,
                            input=result["counts"]["input"],
                            eligible=result["counts"]["study"],
                            tests=result["counts"]["tests"],
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
                            url=url if rank == 1 else "",
                        )
                    )
                handle.flush()
                best = terms[0] if terms else {}
                print(
                    doc["id"],
                    "seed",
                    seed,
                    "eligible",
                    result["counts"]["study"],
                    "best_BY",
                    round(best.get("by_fdr", 1), 6),
                    "significant",
                    sig,
                    "seconds",
                    round(time.monotonic() - start, 1),
                    flush=True,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["fetch", "screen", "confirm"])
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("docs/data/network-example-screen"))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.phase == "fetch":
        fetch(args.build, args.out)
    else:
        run(args.build, args.out, args.phase, args.top, args.seeds)


if __name__ == "__main__":
    main()
