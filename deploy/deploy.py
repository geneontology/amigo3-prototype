"""Install a verified release on EC2. Run as root through SSM, not through the web app."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from release import extract_release, sha256

CONFIG = Path("/etc/amigo3")
STATE = Path("/var/lib/amigo3")
COMPOSE = Path("/opt/amigo3/compose.yaml")


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def atomic_write(path, content, mode=0o600):
    temporary = path.with_name(path.name + ".tmp")
    # Set permissions before writing, including when refreshing OAuth secrets.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(content)
    temporary.replace(path)


def compose(env_file, *args, **kwargs):
    # Explicit values take precedence over ambient shell variables.
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    return run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE), *args],
        env={**os.environ, **values},
        **kwargs,
    )


def credentials(config):
    result = run(
        [
            "aws",
            "--region",
            config["region"],
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            config["secret"],
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        capture_output=True,
    )
    secret = json.loads(result.stdout)
    values = [secret["github_client_id"], secret["github_client_secret"]]
    if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", v) for v in values):
        raise ValueError("GitHub client ID/secret must be nonempty single-line credentials")
    atomic_write(
        CONFIG / "github.env",
        f"AMIGO3_GITHUB_CLIENT_ID={values[0]}\nAMIGO3_GITHUB_CLIENT_SECRET={values[1]}\n",
    )


def install(key, checksum):
    if not key.startswith("releases/") or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Supply a releases/... S3 object key and its SHA256")
    config = json.loads((CONFIG / "deployment.json").read_text())
    env_file = STATE / "candidate.env"
    current = STATE / "current.env"
    previous = current.read_text() if current.exists() else None
    with tempfile.TemporaryDirectory(dir=STATE) as temporary:
        archive = Path(temporary) / "release.tar.gz"
        run(
            [
                "aws",
                "--region",
                config["region"],
                "s3",
                "cp",
                f"s3://{config['bucket']}/{key}",
                str(archive),
                "--only-show-errors",
            ]
        )
        if sha256(archive) != checksum:
            raise ValueError("Archive checksum mismatch; live deployment unchanged")
        destination = STATE / "releases" / uuid.uuid4().hex
        try:
            manifest = extract_release(archive, destination)
            if manifest["image"].split("@", 1)[0] != config["repository"]:
                raise ValueError("Release image must belong to this deployment's ECR repository")
            run(["chown", "-R", "8983:8983", str(destination / "solr")])
            credentials(config)
            registry = config["repository"].split("/", 1)[0]
            password = run(
                ["aws", "--region", config["region"], "ecr", "get-login-password"], capture_output=True
            ).stdout
            # Store ECR credentials only in this temporary directory.
            os.environ["DOCKER_CONFIG"] = temporary
            run(
                ["docker", "login", "--username", "AWS", "--password-stdin", registry],
                input=password,
                capture_output=True,
            )
            atomic_write(
                env_file,
                f"RELEASE_DIR={destination}\nAPP_IMAGE={manifest['image']}\n"
                f"AMIGO_HOSTNAME={config['hostname']}\nSITE_ACCESS={config['site_access']}\n",
            )
            compose(env_file, "pull")
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        # Single-instance cutover has a brief outage. Old data/images remain for rollback.
        try:
            if previous:
                compose(current, "down")
            compose(env_file, "up", "-d", "--wait", "--wait-timeout", "240")
            core = "amigo3_" + manifest["build_id"]
            for collection, expected in manifest["counts"].items():
                result = compose(
                    env_file,
                    "exec",
                    "-T",
                    "solr",
                    "wget",
                    "-qO-",
                    f"http://localhost:8983/solr/{core}/select?q=collection:{collection}&rows=0",
                    capture_output=True,
                )
                if json.loads(result.stdout)["response"]["numFound"] != expected:
                    raise ValueError(f"Restored Solr count mismatch: {collection}")
        except Exception:
            compose(env_file, "down")
            if previous:
                compose(current, "up", "-d", "--wait", "--wait-timeout", "240")
                print("Previous release restored.")
            raise
        if previous:
            atomic_write(STATE / "previous.env", previous)
        atomic_write(current, env_file.read_text())
        env_file.unlink()
        print(f"Active build: {manifest['build_id']}\nRelease directory: {destination}")


def rollback():
    current, previous = STATE / "current.env", STATE / "previous.env"
    if not current.is_file() or not previous.is_file():
        raise ValueError("No previous successful deployment to restore")
    old, new = current.read_text(), previous.read_text()
    compose(current, "down")
    try:
        compose(previous, "up", "-d", "--wait", "--wait-timeout", "240")
    except Exception:
        compose(previous, "down")
        compose(current, "up", "-d", "--wait", "--wait-timeout", "240")
        raise
    atomic_write(current, new)
    atomic_write(previous, old)
    print("Previous release restored. Login sessions reset.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", nargs="?", help="S3 object key under releases/")
    parser.add_argument("sha256", nargs="?")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo")
    if not args.rollback and (not args.key or not args.sha256):
        parser.error("Provide the S3 object key and SHA256, or --rollback")
    with (STATE / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            rollback()
        else:
            install(args.key, args.sha256)


if __name__ == "__main__":
    main()
