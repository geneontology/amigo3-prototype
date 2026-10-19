"""Cutover and rollback behavior without AWS calls or container mutations."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_release import packaged_release, release  # noqa: F401


@pytest.fixture
def installer(packaged_release, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setitem(sys.modules, "release", release)
    spec = importlib.util.spec_from_file_location("installer", Path(__file__).parents[1] / "deploy/deploy.py")
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    state, config = tmp_path / "state", tmp_path / "config"
    (state / "releases").mkdir(parents=True)
    config.mkdir()
    (config / "deployment.json").write_text(
        json.dumps(
            {
                "region": "us-east-1",
                "bucket": "test-bucket",
                "repository": "example/repo",
                "hostname": "amigo.example.org",
                "site_access": "private",
                "secret": "test-secret",
            }
        )
    )
    current = (
        "RELEASE_DIR=/previous\nAPP_IMAGE=example/repo@sha256:"
        + "a" * 64
        + "\nAMIGO_HOSTNAME=amigo.example.org\nSITE_ACCESS=private\n"
    )
    (state / "current.env").write_text(current)
    monkeypatch.setattr(deploy, "STATE", state)
    monkeypatch.setattr(deploy, "CONFIG", config)
    monkeypatch.setattr(deploy, "credentials", lambda cfg: None)
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "unused-docker-config"))
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == "aws" and args[3:5] == ["s3", "cp"]:
            shutil.copyfile(packaged_release, args[6])
        return SimpleNamespace(stdout="test-token")

    monkeypatch.setattr(deploy, "run", run)
    return deploy, packaged_release, commands, current


def test_checksum_failure_never_stops_live_stack(installer, monkeypatch):
    deploy, _, commands, previous = installer
    monkeypatch.setattr(deploy, "compose", lambda *args, **kwargs: pytest.fail("Must not touch Docker"))
    with pytest.raises(ValueError, match="checksum"):
        deploy.install("releases/corrupt.tar.gz", "0" * 64)
    assert len(commands) == 1  # Only the download.
    assert (deploy.STATE / "current.env").read_text() == previous


def test_failed_readiness_restores_previous_release(installer, monkeypatch):
    deploy, archive, _, previous = installer
    calls = []

    def compose(env, *args, **kwargs):
        calls.append((env.name, args[0]))
        if env.name == "candidate.env" and args[0] == "up":
            raise subprocess.CalledProcessError(1, "compose up")

    monkeypatch.setattr(deploy, "compose", compose)
    with pytest.raises(subprocess.CalledProcessError):
        deploy.install("releases/test.tar.gz", release.sha256(archive))
    assert calls == [
        ("candidate.env", "pull"),
        ("current.env", "down"),
        ("candidate.env", "up"),
        ("candidate.env", "down"),
        ("current.env", "up"),
    ]
    assert (deploy.STATE / "current.env").read_text() == previous


def test_success_records_previous_after_count_verification(installer, monkeypatch):
    deploy, archive, _, previous = installer
    calls = []

    def compose(env, *args, **kwargs):
        calls.append(args[0])
        if args[0] == "exec":
            values = dict(line.split("=", 1) for line in env.read_text().splitlines())
            manifest = json.loads((Path(values["RELEASE_DIR"]) / "release.json").read_text())
            collection = args[-1].split("collection:")[1].split("&")[0]
            return SimpleNamespace(
                stdout=json.dumps({"response": {"numFound": manifest["counts"][collection]}})
            )

    monkeypatch.setattr(deploy, "compose", compose)
    deploy.install("releases/test.tar.gz", release.sha256(archive))
    assert "exec" in calls
    assert (deploy.STATE / "previous.env").read_text() == previous
    assert (deploy.STATE / "current.env").read_text() != previous
    assert not (deploy.STATE / "candidate.env").exists()
