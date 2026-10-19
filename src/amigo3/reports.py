"""Searchable report metadata and immutable Markdown/artifact snapshots."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

import duckdb
import pyarrow as pa
import yaml
from markdown_it import MarkdownIt

from amigo3.parse import normalize_id

STRINGS = (
    "id",
    "label",
    "description",
    "kind",
    "scope",
    "provider",
    "model",
    "organism",
    "subject_label",
    "source_path",
    "created_at",
    "search_text",
)
LISTS = ("gene_ids", "module_ids", "term_ids", "family_ids", "model_ids", "subject_ids")
INTEGERS = ("word_count", "artifact_count")
SCHEMA = pa.schema(
    [(k, pa.string()) for k in STRINGS]
    + [(k, pa.list_(pa.string())) for k in LISTS]
    + [(k, pa.int64()) for k in INTEGERS]
    + [("has_artifacts", pa.bool_())]
)


def split_report(raw):
    text = raw.replace("\r\n", "\n").lstrip("\ufeff")
    metadata = {}
    match = re.match(r"^---\n(.*?)\n---\s*\n", text, re.S)
    if match:
        try:
            parsed = yaml.load(match[1], Loader=yaml.CSafeLoader)
            if isinstance(parsed, dict):
                metadata, text = parsed, text[match.end() :]
        except yaml.YAMLError:
            pass
    parts = re.split(r"^## Output\s*$", text, maxsplit=1, flags=re.M)
    body = parts[-1].strip("\n")
    prompt = parts[0].strip() if len(parts) > 1 else ""
    lines = [line for line in body.splitlines() if line.strip()]
    if lines and sum(line.startswith("        ") for line in lines) > len(lines) / 2:
        body = re.sub(r"(?m)^ {8}", "", body)
    # Some Edison exports echo the question after the Output marker. Split only
    # when a recognizable prompt prefix and a report-start marker both exist.
    if body.lstrip().startswith(("Question:", "# Gene Research for Functional Annotation")):
        start = re.search(
            r"^#{1,3} Research report\b.*|^# (?!Gene Research for Functional Annotation).+", body, re.M | re.I
        )
        if start:
            prompt += "\n\n" + body[: start.start()].strip()
            body = body[start.start() :]
    return metadata, body.strip(), prompt.strip()


def discover(root):
    if not root:
        return []
    candidates = set()
    for folder in ("genes", "modules", "terms", "rules", "projects", "interpro"):
        candidates.update((root / folder).rglob("*-deep-research*.md"))
    for provider in ("openscientist", "falcon", "asta"):
        candidates.update((root / "genes").glob(f"*/*/*-hypotheses/*/{provider}.md"))
    return sorted(
        p
        for p in candidates
        if "citations" not in p.name and not any(x.endswith("_artifacts") for x in p.parts)
    )


def attachments(path, metadata, raw):
    """Local siblings only; never follow arbitrary paths from generated Markdown."""
    found = {}
    for item in metadata.get("artifacts") or []:
        if isinstance(item, dict) and item.get("path"):
            found[str(item["path"])] = "artifact"
    for candidate in (
        path.with_suffix(".citations.md"),
        Path(str(path) + ".citations.md"),
        path.with_name(path.stem + "-citations.md"),
    ):
        if candidate.is_file():
            found[candidate.name] = "citations"
    for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)", raw):
        if not urlsplit(target).scheme and not target.startswith(("#", "/")):
            found.setdefault(unquote(target), "attachment")
    result = []
    base = path.parent.resolve()
    for relative, role in sorted(found.items()):
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            continue
        result.append((relative, role, target if target.is_file() else None))
    return result


def report_inputs(root):
    paths = set(discover(root))
    for path in list(paths):
        raw = path.read_text(encoding="utf-8-sig")
        metadata, _, _ = split_report(raw)
        paths.update(p for _, _, p in attachments(path, metadata, raw) if p)
    return sorted(paths)


def load_reports(db, root, expected, work):
    db.execute("CREATE TABLE report_documents(id VARCHAR, document_json VARCHAR)")
    db.execute("CREATE TABLE report_assets(sha256 VARCHAR, filename VARCHAR, media_type VARCHAR)")
    assets = work / "report-assets"
    assets.mkdir()
    aliases, review_genes = defaultdict(set), defaultdict(set)
    for identifier, ids in db.execute("SELECT id, identifiers FROM genes").fetchall():
        for alias in ids:
            aliases[normalize_id(alias)].add(identifier)
    for gene, paths in db.execute("SELECT gene_id, review_path FROM gene_reviews").fetchall():
        for path in paths.split("|"):
            review_genes[str(Path(path).parent)].add(gene)
    modules = dict(
        db.execute("SELECT path, record_id FROM knowledge_sources WHERE collection='modules'").fetchall()
    )
    records, documents, asset_rows = [], [], {}

    def capture(path, role, relative):
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if expected.get(str(path.relative_to(root))) != checksum:
            raise ValueError(f"Report source changed during build: {path}")
        (assets / checksum).write_bytes(raw)
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset_rows[checksum] = (checksum, path.name, media)
        return dict(
            path=relative,
            role=role,
            filename=path.name,
            sha256=checksum,
            media_type=media,
            url="/api/report-assets/" + checksum,
            available=True,
        )

    for path in discover(root):
        relative = str(path.relative_to(root))
        source = capture(path, "source", path.name)
        raw = (assets / source["sha256"]).read_text(encoding="utf-8-sig")
        metadata, body, prompt = split_report(raw)
        variables = metadata.get("template_variables") or {}
        if not isinstance(variables, dict):
            variables = {}
        parts = Path(relative).parts
        scope = {"genes": "Gene", "modules": "Module", "terms": "Term", "rules": "Rule"}.get(
            parts[0], "Project"
        )
        hypothesis = any(x.endswith("-hypotheses") for x in parts)
        subject = str(
            variables.get("gene_symbol")
            or variables.get("gene")
            or variables.get("module_title")
            or (parts[2] if scope == "Gene" else path.stem.split("-deep-research")[0])
        )
        provider = str(
            metadata.get("provider")
            or (
                path.stem.split("-deep-research-", 1)[-1]
                if "-deep-research-" in path.stem
                else path.stem
                if hypothesis
                else "unknown"
            )
        )
        heading = next(
            (
                m[1].strip()
                for m in re.finditer(r"^#{1,3}\s+(.+)$", body, re.M)
                if m[1].strip() not in ("Question", "Output", "Gene Research for Functional Annotation")
            ),
            "",
        )
        identifier = "REPORT:" + hashlib.sha256(relative.encode()).hexdigest()[:20]
        subject_ids = []
        accession = variables.get("uniprot_accession")
        if accession and isinstance(accession, str):
            subject_ids.append(normalize_id(accession if ":" in accession else "UniProtKB:" + accession))
        gene_ids = set().union(*(aliases[x] for x in subject_ids)) if subject_ids else set()
        if scope == "Gene" and not subject_ids:
            gene_ids.update(review_genes["/".join(parts[:3])])
        module = modules.get(relative.split("-deep-research")[0] + ".yaml") if scope == "Module" else None
        files = []
        for name, role, target in attachments(path, metadata, raw):
            files.append(
                capture(target, role, name)
                if target
                else dict(path=name, role=role, filename=Path(name).name, available=False)
            )
        paragraphs = [
            p.strip()
            for p in body.split("\n\n")
            if p.strip() and not p.lstrip().startswith(("#", "|", "![", "```"))
        ]
        record = {
            **dict.fromkeys(STRINGS, ""),
            **{k: [] for k in LISTS},
            "id": identifier,
            "label": heading or subject + " — " + ("hypothesis research" if hypothesis else "deep research"),
            "description": re.sub(r"\s+", " ", paragraphs[0])[:400] if paragraphs else "",
            "provider": provider,
            "model": str(metadata.get("model") or ""),
            "scope": scope,
            "kind": "Hypothesis research" if hypothesis else "Deep research",
            "organism": str(variables.get("organism") or (parts[1] if scope == "Gene" else "")),
            "subject_label": subject,
            "subject_ids": subject_ids,
            "gene_ids": sorted(gene_ids),
            "module_ids": [module] if module else [],
            "source_path": relative,
            "created_at": str(metadata.get("start_time") or ""),
            "word_count": len(body.split()),
            "artifact_count": sum(f["available"] and f["role"] != "citations" for f in files),
            "has_artifacts": any(f["available"] and f["role"] != "citations" for f in files),
        }
        record["search_text"] = " ".join([relative, subject, provider, record["label"], *subject_ids, body])
        records.append(record)
        documents.append(
            (
                identifier,
                json.dumps(
                    dict(body=body, prompt=prompt, metadata=metadata, source=source, files=files), default=str
                ),
            )
        )
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    db.register("_reports", table)
    db.execute("CREATE TABLE reports AS SELECT * FROM _reports")
    db.unregister("_reports")
    if documents:
        db.executemany("INSERT INTO report_documents VALUES (?, ?)", documents)
    if asset_rows:
        db.executemany("INSERT INTO report_assets VALUES (?, ?, ?)", list(asset_rows.values()))
    return {"reports": len(records), "assets": len(asset_rows)}


def render_markdown(body, files, prefix="report"):
    md = MarkdownIt("js-default")
    tokens = md.parse(body)
    toc = []
    known = {f["path"]: f for f in files if f.get("available")}
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            anchor = f"{prefix}-heading-{len(toc)}"
            token.attrSet("id", anchor)
            toc.append(dict(id=anchor, label=tokens[i + 1].content, level=int(token.tag[1:])))
        for child in token.children or []:
            if child.type not in ("image", "link_open"):
                continue
            key = "src" if child.type == "image" else "href"
            target = child.attrGet(key) or ""
            file = known.get(unquote(target))
            if file and (
                child.type != "image"
                or file["media_type"] in ("image/png", "image/jpeg", "image/gif", "image/webp")
            ):
                child.attrSet(key, file["url"])
            elif child.type == "image":
                child.type, child.tag, child.content = "text", "", "[Image: " + child.content + "]"
                child.children = None
            elif urlsplit(target).scheme.lower() not in ("https", "http", "mailto"):
                child.attrSet("href", "#" + prefix + "-source")
                child.attrSet("title", "See original source for this local reference")
            else:
                child.attrSet("rel", "noopener noreferrer")
    return md.renderer.render(tokens, md.options, {}), toc


def report_detail(path, record):
    from amigo3.details import related

    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        neighbors = related(db, "report_ids", record["id"])
        document = json.loads(
            db.execute("SELECT document_json FROM report_documents WHERE id=?", [record["id"]]).fetchone()[0]
        )
        genes = db.execute(
            "SELECT id, symbol FROM genes WHERE id IN (SELECT unnest(?::VARCHAR[])) ORDER BY symbol",
            [record["gene_ids"]],
        ).fetchall()
        modules = db.execute(
            "SELECT id, label FROM modules WHERE id IN (SELECT unnest(?::VARCHAR[]))", [record["module_ids"]]
        ).fetchall()
    body, toc = render_markdown(document.pop("body"), document["files"])
    return dict(
        record=record,
        **document,
        html=body,
        toc=toc,
        related=neighbors,
        genes=[dict(id=i, label=s) for i, s in genes],
        modules=[dict(id=i, label=s) for i, s in modules],
    )
