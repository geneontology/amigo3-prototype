"""Checksum-addressed upstream downloads; rebuilds never silently refresh inputs."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

TAXONOMY_URL = "https://rest.uniprot.org/taxonomy/stream?format=json&fields=id,scientific_name,lineage&query="


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def source_entries(config: dict) -> list[dict]:
    """Configured sources, with species taxa and a UniProt taxonomy source for them.

    >>> config = {
    ...     "species": {"fly": {"label": "Fruit fly", "taxon": "NCBITaxon:7227"}},
    ...     "sources": [{"id": "fly_gaf", "kind": "gaf", "species": "fly", "url": "u"}],
    ... }
    >>> fly, taxonomy = source_entries(config)
    >>> fly["taxon"]
    'NCBITaxon:7227'
    >>> taxonomy["url"].endswith("tax_id%3A7227")
    True
    """
    species = config["species"]
    entries = [
        {**entry, "taxon": species[entry["species"]]["taxon"]} if "species" in entry else entry
        for entry in config["sources"]
    ]
    taxa = sorted(
        {s["taxon"] for s in species.values()} | {t for e in entries for t in e.get("allowed_taxa", [])},
        key=lambda t: int(t.removeprefix("NCBITaxon:")),
    )
    query = " OR ".join("tax_id:" + t.removeprefix("NCBITaxon:") for t in taxa)
    taxonomy = {
        "id": "taxonomy",
        "kind": "uniprot_taxonomy",
        "url": TAXONOMY_URL + quote(query),
        "loaded_species": species,
    }
    return [*entries, taxonomy]


def fetch(config: Path, data: Path, refresh: bool = False) -> dict:
    entries = source_entries(yaml.safe_load(config.read_text()))
    manifest_path = data / "sources.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    def download(entry: dict) -> tuple[str, dict]:
        source_id = entry["id"]
        old = previous.get(source_id)
        if not refresh and old and old["url"] == entry["url"]:
            path = data / old["path"]
            if path.exists() and digest(path) == old["sha256"]:
                print(f"Cached {source_id}", flush=True)
                return source_id, {**old, **entry}
        temp = raw / f"{source_id}.download"
        print(f"Fetching {source_id}", flush=True)
        try:
            with httpx.stream("GET", entry["url"], follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                with temp.open("wb") as out:
                    for block in response.iter_bytes():
                        out.write(block)
                metadata = {
                    k: response.headers[k] for k in ("etag", "last-modified") if k in response.headers
                }
            sha = digest(temp)
            suffix = ".gz" if entry["url"].endswith(".gz") else ".txt"
            path = raw / f"{source_id}-{sha[:16]}{suffix}"
            os.replace(temp, path)
            print(f"Fetched {source_id}: {path.stat().st_size:,} bytes", flush=True)
            return source_id, {
                **entry,
                "sha256": sha,
                "path": str(path.relative_to(data)),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            }
        finally:
            temp.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        manifest = dict(pool.map(download, entries))
    write_json(manifest_path, manifest)
    return manifest
