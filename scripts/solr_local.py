"""Start a repository-local Solr when a Docker daemon is unavailable."""

import argparse
import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import httpx

VERSION = "9.9.0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / ".runtime"
    root.mkdir(exist_ok=True)
    name = f"solr-{VERSION}-slim"
    binary = root / name / "bin" / "solr"
    if not binary.exists() and args.action == "start":
        archive = root / f"{name}.tgz"
        url = f"https://archive.apache.org/dist/solr/solr/{VERSION}/{archive.name}"
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            expected = client.get(url + ".sha512")
            expected.raise_for_status()
            if not archive.exists():
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with archive.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
            if hashlib.sha512(archive.read_bytes()).hexdigest() != expected.text.split()[0]:
                raise SystemExit("Solr archive checksum mismatch; remove it and retry")
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")
    env = {**os.environ, "SOLR_JETTY_HOST": "127.0.0.1"}
    command = [str(binary), args.action, "-p", "8987"]
    if args.action == "start":
        command.extend(["-m", "2g", "--host", "127.0.0.1"])
    subprocess.run(command, env=env, check=True)


if __name__ == "__main__":
    main()
