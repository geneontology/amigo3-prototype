"""Deterministic, build-time chemical depictions from pinned Rhea 2D structures."""

from __future__ import annotations

import gzip
import hashlib
from urllib.parse import quote

import pyarrow as pa
from rdkit import Chem, rdBase
from rdkit.Chem.Draw import rdMolDraw2D

RENDERER = f"RDKit {rdBase.rdkitVersion}; Rhea 2D coordinates; amigo3-v1"
STRUCTURE_SCHEMA = pa.schema(
    [
        (k, pa.string())
        for k in (
            "chemical_id",
            "molblock",
            "mol_sha256",
            "svg_path",
            "svg_sha256",
            "source_id",
            "source_sha256",
            "renderer",
        )
    ]
)


def sdf_records(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as stream:
        lines = []
        for line in stream:
            if line.strip() == "$$$$":
                yield "".join(lines)
                lines = []
            else:
                lines.append(line)
        if any(line.strip() for line in lines):
            raise ValueError("Truncated Rhea SDF: missing record terminator")


def render_structure(molblock):
    # Sanitization failure is an explicit missing depiction, never a guessed molecule.
    mol = Chem.MolFromMolBlock(molblock, sanitize=True, removeHs=False, strictParsing=True)
    if mol is None or not mol.GetNumAtoms():
        raise ValueError("Unrenderable molfile")
    if mol.GetNumAtoms() > 500:
        raise ValueError("Structure exceeds depiction size limit (500 atoms)")
    if not mol.GetNumConformers() or mol.GetConformer().Is3D():
        raise ValueError("No source 2D coordinates")
    if mol.GetNumAtoms() > 1:
        points = {
            (round(p.x, 4), round(p.y, 4))
            for p in (mol.GetConformer().GetAtomPosition(i) for i in range(mol.GetNumAtoms()))
        }
        if len(points) < 2:
            raise ValueError("Degenerate source coordinates")
    drawer = rdMolDraw2D.MolDraw2DSVG(-1, -1)
    options = drawer.drawOptions()
    options.scalingFactor = 24
    options.bondLineWidth = 1.6
    options.baseFontSize = 0.65
    options.padding = 0.12
    options.clearBackground = True
    options.addStereoAnnotation = False
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def load_structures(db, data, manifest, work):
    sources = [s for s in manifest.values() if s["kind"] == "rhea_sdf"]
    if len(sources) > 1:
        raise ValueError("Use one Rhea SDF snapshot")
    chemicals = {row[0] for row in db.execute("SELECT id FROM chemicals").fetchall()}
    rows, issues, seen = [], [], set()
    if sources:
        source = sources[0]
        directory = work / "chemical-structures"
        directory.mkdir()
        for raw in sdf_records(data / source["path"]):
            identifier = raw.splitlines()[0].strip()
            if identifier not in chemicals:
                continue
            if identifier in seen:
                raise ValueError(f"Duplicate structure for {identifier}")
            seen.add(identifier)
            end = raw.find("M  END")
            if end == -1:
                issues.append({"chemical_id": identifier, "reason": "Missing molfile terminator"})
                continue
            molblock = raw[: end + len("M  END")] + "\n"
            try:
                svg = render_structure(molblock)
            except (ValueError, RuntimeError) as error:
                issues.append({"chemical_id": identifier, "reason": str(error)})
                continue
            sha = hashlib.sha256(svg.encode()).hexdigest()
            relative = f"chemical-structures/{sha}.svg"
            (work / relative).write_text(svg)
            rows.append(
                {
                    "chemical_id": identifier,
                    "molblock": molblock,
                    "mol_sha256": hashlib.sha256(molblock.encode()).hexdigest(),
                    "svg_path": relative,
                    "svg_sha256": sha,
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "renderer": RENDERER,
                }
            )
    db.register("structure_batch", pa.Table.from_pylist(rows, schema=STRUCTURE_SCHEMA))
    db.execute("CREATE TABLE chemical_structures AS SELECT * FROM structure_batch")
    db.execute("ALTER TABLE chemical_structures ADD PRIMARY KEY (chemical_id)")
    db.unregister("structure_batch")
    db.execute("ALTER TABLE chemicals ADD COLUMN has_structure BOOLEAN DEFAULT false")
    db.execute(
        "UPDATE chemicals SET has_structure=true WHERE id IN (SELECT chemical_id FROM chemical_structures)"
    )
    return {
        "rendered": len(rows),
        "unavailable": len(chemicals) - len(rows),
        "renderer": RENDERER,
        "issues": issues,
    }


def structure_details(db, identifiers):
    if "chemical_structures" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}:
        return {}
    rows = (
        db.execute(
            "SELECT * EXCLUDE(molblock, svg_path) FROM chemical_structures WHERE chemical_id IN (SELECT unnest(?))",
            [list(identifiers)],
        )
        .to_arrow_table()
        .to_pylist()
    )
    return {
        row["chemical_id"]: {
            **row,
            "url": "/api/chemical-structures/"
            + quote(row["chemical_id"], safe="")
            + "?sha256="
            + row["svg_sha256"],
        }
        for row in rows
    }
