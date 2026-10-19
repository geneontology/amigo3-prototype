"""Fetch pinned browser libraries and an RO causal-relation label/sign lookup."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parents[1] / "src/amigo3/web"
PACKAGES = {
    "cytoscape": ("3.34.3", "dist/cytoscape.min.js"),
    "cytoscape-dagre": ("4.0.1", "dist/cytoscape-dagre.min.js"),
    "@dagrejs/dagre": ("3.0.0", None),
    "@dagrejs/graphlib": ("3.0.4", None),
}
RO = "https://raw.githubusercontent.com/oborel/obo-relations/v2026-09-04/ro.json"


def main():
    vendor = DEST / "vendor"
    vendor.mkdir(exist_ok=True)
    manifest = {}
    with httpx.Client(follow_redirects=True, timeout=90) as client:
        for name, (version, source) in PACKAGES.items():
            metadata = client.get(f"https://registry.npmjs.org/{name}/{version}")
            metadata.raise_for_status()
            dist = metadata.json()["dist"]
            response = client.get(dist["tarball"])
            response.raise_for_status()
            raw = response.content
            # npm supplies a SHA-512 integrity digest for the package tarball.
            import base64

            assert dist["integrity"] == "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                targets = {"package/LICENSE": name.split("/")[-1] + ".LICENSE"}
                if source:
                    targets["package/" + source] = source.split("/")[-1]
                for member, destination in targets.items():
                    (vendor / destination).write_bytes(archive.extractfile(member).read())
            manifest[name] = dict(version=version, url=dist["tarball"], integrity=dist["integrity"])
        response = client.get(RO)
        response.raise_for_status()
        graph = response.json()["graphs"][0]
        parents = {}
        for edge in graph["edges"]:
            if edge["pred"] == "subPropertyOf":
                parents.setdefault(edge["sub"], []).append(edge["obj"])

        def ancestors(identifier, seen=None):
            seen = set() if seen is None else seen
            if identifier in seen:
                return seen
            seen.add(identifier)
            for parent in parents.get(identifier, []):
                ancestors(parent, seen)
            return seen

        prefix = "http://purl.obolibrary.org/obo/RO_"
        relations = {}
        for node in graph["nodes"]:
            lineage = ancestors(node["id"])
            if not {prefix + "0002411", prefix + "0002211"} & lineage:
                continue
            positive = bool({prefix + "0002304", prefix + "0002213"} & lineage)
            negative = bool({prefix + "0002305", prefix + "0002212"} & lineage)
            sign = (
                "positive"
                if positive and not negative
                else "negative"
                if negative and not positive
                else "neutral"
            )
            relations[node["id"].replace(prefix, "RO:")] = dict(label=node.get("lbl", node["id"]), sign=sign)
        result = dict(
            source=RO,
            version=graph.get("meta", {}).get("version"),
            sha256=hashlib.sha256(response.content).hexdigest(),
            relations=relations,
        )
        (DEST / "assets/causal-relations.json").write_text(json.dumps(result, indent=2) + "\n")
    (vendor / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Vendored pinned libraries and {len(relations)} causal relation labels")


if __name__ == "__main__":
    main()
