"""Package an immutable build with a committed Solr backup; no cloud credentials needed."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOLR_VERSION = "9.9.0"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def request_json(url, params=None, body=None):
    if params:
        url += "?" + urlencode(params)
    request = Request(url, data=json_bytes(body) if body is not None else None)
    request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def safe_name(name):
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and ".." not in path.parts and str(path) == name)


def validate_manifest(manifest):
    if manifest.get("format") != 1 or manifest.get("solr_version") != SOLR_VERSION:
        raise ValueError("Unsupported release format or Solr version")
    build_id = manifest.get("build_id", "")
    if not re.fullmatch(r"[0-9a-f]{16}", build_id):
        raise ValueError("Invalid build ID")
    if not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}", manifest.get("image", "")):
        raise ValueError("Supply an immutable image reference: registry/repository@sha256:...")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not files:
        raise ValueError("Release has no files")
    for name, checksum in files.items():
        if not safe_name(name) or name == "release.json" or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid release file entry")
        if not name.startswith(("data/", "solr/")):
            raise ValueError("Unexpected release file location")
    required = {
        "data/current.json",
        "data/published.json",
        f"data/builds/{build_id}/integration.duckdb",
        f"data/builds/{build_id}/manifest.json",
        f"solr/amigo3_{build_id}/core.properties",
        f"solr/amigo3_{build_id}/conf/solrconfig.xml",
    }
    if not required <= files.keys():
        raise ValueError("Release is missing required data or Solr configuration")
    counts = manifest.get("counts")
    if (
        not isinstance(counts, dict)
        or not counts
        or any(
            not re.fullmatch(r"[a-z_]+", name) or type(count) is not int or count < 0
            for name, count in counts.items()
        )
    ):
        raise ValueError("Invalid collection counts")
    return manifest


def extract_release(archive, target):
    """Accept only regular files, with bounded metadata and verified contents."""
    target = Path(target)
    target.mkdir(exist_ok=False)
    target.chmod(0o755)
    with tarfile.open(archive, "r:gz") as tar:
        entries = tar.getmembers()
        names = set()
        for entry in entries:
            if not entry.isfile() or not safe_name(entry.name) or entry.name in names:
                raise ValueError("Release contains an unsafe or duplicate archive entry")
            names.add(entry.name)
        entry = tar.getmember("release.json")
        if entry.size > 10_000_000:
            raise ValueError("Release metadata is too large")
        manifest = validate_manifest(json.load(tar.extractfile(entry)))
        if names != set(manifest["files"]) | {"release.json"}:
            raise ValueError("Archive contents disagree with release manifest")
        for entry in entries:
            path = target / entry.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(entry) as source, path.open("xb") as dest:
                while chunk := source.read(1024 * 1024):
                    dest.write(chunk)
            path.chmod(0o644)
            if entry.name != "release.json" and sha256(path) != manifest["files"][entry.name]:
                raise ValueError(f"Checksum mismatch: {entry.name}")
    for path in target.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    return manifest


def package(data, core_dir, solr_url, image, output, revision, config_dir=None):
    data, core_dir, output = Path(data), Path(core_dir), Path(output)
    if output.exists():
        raise ValueError("Output already exists; use a new release filename")
    receipt = json.loads((data / "published.json").read_text())
    build_id = receipt["build_id"]
    if not re.fullmatch(r"[0-9a-f]{16}", build_id):
        raise ValueError("Invalid published build ID")
    core = "amigo3_" + build_id
    if receipt["core"] != core or core_dir.name != core:
        raise ValueError("Select the core directory matching published.json")
    conf = Path(config_dir) if config_dir else core_dir / "conf"
    if not conf.is_dir() and not config_dir:
        # CoreAdmin CREATE with configSet references a shared config directory;
        # it does not necessarily copy configuration into the core instance.
        properties = (core_dir / "core.properties").read_text()
        match = re.search(r"^configSet=([A-Za-z0-9_-]+)$", properties, re.MULTILINE)
        if match:
            conf = core_dir.parent / "configsets" / match[1] / "conf"
    if not (conf / "solrconfig.xml").is_file():
        raise ValueError("Cannot locate effective core configuration; supply --config-dir")
    build = data / "builds" / build_id
    manifest = json.loads((build / "manifest.json").read_text())
    if (build / "integration.duckdb.wal").exists():
        raise ValueError("Close/checkpoint the DuckDB writer before packaging")
    if receipt["counts"] != manifest["counts"]:
        raise ValueError("Published counts disagree with build manifest")
    for name, checksum in manifest["files"].items():
        if not safe_name(name) or sha256(build / name) != checksum:
            raise ValueError(f"Build checksum mismatch: {name}")
    solr_url = solr_url.rstrip("/")
    version = request_json(solr_url + "/admin/info/system")["lucene"]["solr-spec-version"]
    if version != SOLR_VERSION:
        raise ValueError(f"Expected Solr {SOLR_VERSION}; got {version}")
    for collection, count in receipt["counts"].items():
        result = request_json(f"{solr_url}/{core}/select", {"q": f"collection:{collection}", "rows": 0})
        if result["response"]["numFound"] != count:
            raise ValueError(f"Solr count mismatch: {collection}")
    request_json(f"{solr_url}/{core}/update", body={"commit": {}})
    backup = "release-" + uuid.uuid4().hex
    request_json(f"{solr_url}/{core}/replication", {"command": "backup", "name": backup})
    snapshot = core_dir / "data" / ("snapshot." + backup)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status = (
            request_json(f"{solr_url}/{core}/replication", {"command": "details"})
            .get("details", {})
            .get("backup", {})
        )
        if status.get("status") == "success" and status.get("snapshotName") == backup:
            break
        if status.get("snapShootException") or status.get("status") == "failed":
            raise ValueError("Solr backup failed: " + str(status.get("snapShootException", status)))
        time.sleep(1)
    else:
        raise TimeoutError("Solr backup did not finish within ten minutes")
    if not snapshot.is_dir() or not list(snapshot.glob("segments_*")):
        raise ValueError("Backup not readable; core-dir must be the host-side Solr core directory")
    files = {}
    for root, prefix in [
        (build, f"data/builds/{build_id}"),
        (conf, f"solr/{core}/conf"),
        (snapshot, f"solr/{core}/data/index"),
    ]:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlinks cannot be packaged: {path}")
            if path.is_file() and path.name != "write.lock":
                files[f"{prefix}/{path.relative_to(root).as_posix()}"] = path
    files["data/current.json"] = json_bytes({"build_id": build_id})
    files["data/published.json"] = json_bytes({**receipt, "url": "http://solr:8983/solr"})
    files[f"solr/{core}/core.properties"] = f"name={core}\n".encode()
    release = validate_manifest(
        {
            "format": 1,
            "build_id": build_id,
            "solr_version": SOLR_VERSION,
            "image": image,
            "source_revision": revision,
            "counts": receipt["counts"],
            "files": {
                name: hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else sha256(value)
                for name, value in files.items()
            },
        }
    )
    files["release.json"] = json_bytes(release)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=1) as tar:
            for name, value in files.items():
                entry = tarfile.TarInfo(name)
                entry.mode = 0o644
                entry.size = len(value) if isinstance(value, bytes) else value.stat().st_size
                with io.BytesIO(value) if isinstance(value, bytes) else value.open("rb") as source:
                    tar.addfile(entry, source)
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = sha256(output)
    output.with_name(output.name + ".sha256").write_text(f"{checksum}  {output.name}\n")
    print(f"Release: {output}\nSHA256: {checksum}\nSolr backup retained at: {snapshot}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--core-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, help="Override the effective Solr conf directory")
    parser.add_argument("--solr", default="http://127.0.0.1:8987/solr")
    parser.add_argument("--image", required=True, help="ECR repository@sha256:digest of matching application")
    parser.add_argument("--revision", required=True, help="Git commit used to build the application image")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.data, args.core_dir, args.solr, args.image, args.output, args.revision, args.config_dir)


if __name__ == "__main__":
    main()
