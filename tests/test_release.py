"""Artifact integrity, archive boundaries, and central Solr backup packaging."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "deploy/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def packaged_release(built_data, tmp_path, monkeypatch):
    build_id = json.loads((built_data / "current.json").read_text())["build_id"]
    build = built_data / "builds" / build_id
    counts = json.loads((build / "manifest.json").read_text())["counts"]
    # Do not alter the shared fixture's published pointer.
    data = tmp_path / "data"
    (data / "builds").mkdir(parents=True)
    (data / "builds" / build_id).symlink_to(build, target_is_directory=True)
    core = "amigo3_" + build_id
    (data / "published.json").write_text(json.dumps({"build_id": build_id, "core": core, "counts": counts}))
    core_dir = tmp_path / core
    (core_dir / "conf").mkdir(parents=True)
    (core_dir / "conf/solrconfig.xml").write_text("<config/>")
    (core_dir / "conf/managed-schema.xml").write_text("<schema/>")
    snapshot_name = None

    def solr(url, params=None, body=None):
        nonlocal snapshot_name
        if url.endswith("/admin/info/system"):
            return {"lucene": {"solr-spec-version": "9.9.0"}}
        if url.endswith("/select"):
            return {"response": {"numFound": counts[params["q"].split(":")[1]]}}
        if body:
            assert body == {"commit": {}}
            return {}
        if params["command"] == "backup":
            snapshot_name = "snapshot." + params["name"]
            snapshot = core_dir / "data" / snapshot_name
            snapshot.mkdir(parents=True)
            (snapshot / "segments_1").write_bytes(b"committed test index")
            return {}
        assert params["command"] == "details"
        return {
            "details": {
                "backup": {"status": "success", "snapshotName": snapshot_name.removeprefix("snapshot.")}
            }
        }

    monkeypatch.setattr(release, "request_json", solr)
    archive = tmp_path / "release.tar.gz"
    release.package(
        data, core_dir, "http://localhost:8983/solr", "example/repo@sha256:" + "a" * 64, archive, "b" * 40
    )
    return archive


def test_release_roundtrip_and_pinned_serving_pointer(packaged_release, tmp_path):
    manifest = release.extract_release(packaged_release, tmp_path / "restore")
    assert manifest["solr_version"] == "9.9.0"
    pointer = json.loads((tmp_path / "restore/data/published.json").read_text())
    assert pointer["build_id"] == manifest["build_id"]
    assert pointer["url"] == "http://solr:8983/solr"
    assert (
        packaged_release.with_name(packaged_release.name + ".sha256")
        .read_text()
        .startswith(release.sha256(packaged_release))
    )


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate", "tamper", "unexpected"])
def test_archive_attacks_and_corruption_rejected(packaged_release, tmp_path, kind):
    corrupt = tmp_path / "bad.tar.gz"
    with tarfile.open(packaged_release) as source, tarfile.open(corrupt, "w:gz") as dest:
        for entry in source:
            value = source.extractfile(entry).read()
            if kind == "tamper" and entry.name == "data/current.json":
                value = b"bad"
                entry.size = len(value)
            dest.addfile(entry, io.BytesIO(value))
        if kind != "tamper":
            name = {
                "traversal": "../escape",
                "symlink": "data/link",
                "duplicate": "release.json",
                "unexpected": "data/extra",
            }[kind]
            entry = tarfile.TarInfo(name)
            if kind == "symlink":
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/etc/passwd"
            dest.addfile(entry, io.BytesIO())
    with pytest.raises(ValueError):
        release.extract_release(corrupt, tmp_path / "rejected")
    assert not (tmp_path / "escape").exists()


def test_mutable_images_are_refused(packaged_release):
    with tarfile.open(packaged_release) as tar:
        manifest = json.load(tar.extractfile("release.json"))
    manifest["image"] = "example/repo:latest"
    with pytest.raises(ValueError, match="immutable"):
        release.validate_manifest(manifest)
