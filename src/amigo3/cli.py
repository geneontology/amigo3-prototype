"""Small, composable pipeline commands."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="AmiGO 3 integration and browser")
    parser.add_argument("--data", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Cache upstream files by checksum")
    fetch.add_argument("--config", type=Path, default=Path("config/sources.yaml"))
    fetch.add_argument("--refresh", action="store_true")
    build = commands.add_parser("build", help="Integrate cached inputs and export Parquet")
    build.add_argument("--reviews", type=Path, help="Optional ai-gene-review genes/ directory")
    build.add_argument(
        "--external-sets", type=Path, help="Preload configured external GAF sets for comparison"
    )
    build.add_argument(
        "--repository",
        type=Path,
        help="AIGR repository containing genes, modules, gocams and interpro/panther",
    )
    publish = commands.add_parser("publish", help="Load Parquet into a dedicated Solr core")
    publish.add_argument("--solr", default="http://127.0.0.1:8987/solr")
    network = commands.add_parser("gene-network", help="Export the GO-CAM gene network and coverage report")
    network.add_argument("--build-id", help="Read a particular immutable build (default: current build)")
    network.add_argument("--taxon", help="Restrict to an exact model taxon, e.g. NCBITaxon:9606")
    network.add_argument("--summary", action="store_true", help="Only emit coverage and projection policy")
    network.add_argument("--output", type=Path, help="Write JSON to this file (default: stdout)")
    serve = commands.add_parser("serve", help="Serve the collection API and browser")
    serve.add_argument("--backend", choices=("solr", "duckdb"), default="solr")
    serve.add_argument("--solr", default="http://127.0.0.1:8987/solr")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8030)
    serve.add_argument(
        "--genesets-url",
        default="https://mygeneset.info/v1",
        help="MyGeneset API for live gene set search; pass '' to disable (bundled examples remain)",
    )
    serve.add_argument("--chat-command", help="Enable local ACP chat, e.g. 'claude-agent-acp' (no shell)")
    args = parser.parse_args()
    if args.command == "fetch":
        from amigo3.sources import fetch

        fetch(args.config, args.data, args.refresh)
    elif args.command == "build":
        from amigo3.build import build

        build(args.data, args.reviews, args.repository, args.external_sets)
    elif args.command == "gene-network":
        from amigo3.catalog import Catalog
        from amigo3.gene_network import gene_network

        try:
            catalog = Catalog(args.data, args.build_id)
            result = gene_network(catalog.path).result(args.taxon, args.summary)
        except ValueError as error:
            parser.error(str(error))
        text = json.dumps({"build_id": catalog.build_id, **result}, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text)
        else:
            print(text, end="")
    elif args.command == "publish":
        from amigo3.catalog import Catalog
        from amigo3.solr import publish

        publish(Catalog(args.data), args.solr)
    elif args.command == "serve":
        import uvicorn

        from amigo3.api import create_app
        from amigo3.genesets import GenesetClient

        command = shlex.split(args.chat_command) if args.chat_command else None
        if args.chat_command:
            if args.host not in {"127.0.0.1", "localhost", "::1"}:
                parser.error("Agent chat currently requires a loopback --host")
            if not command or not shutil.which(command[0]):
                parser.error("Chat agent executable not found; install it first or use an absolute path")
            command[0] = str(Path(shutil.which(command[0])).resolve())
            try:
                import mcp  # noqa: F401
                import websockets  # noqa: F401
            except ImportError:
                parser.error("Install chat dependencies with: uv sync --extra chat")
        uvicorn.run(
            create_app(
                args.data,
                args.backend,
                args.solr,
                chat_command=command,
                genesets=GenesetClient(args.genesets_url),
            ),
            host=args.host,
            port=args.port,
        )
