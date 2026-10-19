"""Project metadata, narratives and explicit references from repository pages."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

import duckdb
import pyarrow as pa
import yaml
from markdown_it import MarkdownIt

from amigo3.parse import normalize_id
from amigo3.reports import render_markdown

STRINGS = ("id", "label", "description", "kind", "maturity", "source_path", "search_text")
LISTS = (
    "tags",
    "project_species",
    "project_collections",
    "declared_genes",
    "gene_ids",
    "term_ids",
    "family_ids",
    "model_ids",
    "module_ids",
    "report_ids",
    "project_ids",
    "rule_ids",
)
SCHEMA = pa.schema(
    [(k, pa.string()) for k in STRINGS]
    + [(k, pa.list_(pa.string())) for k in LISTS]
    + [("resource_count", pa.int64())]
)
# Frontmatter keys naming a project's main deliverable, dates and people, in priority order.
PRIMARY_KEYS = ("primary_artifact", "primary_artifacts", "main_artifact")
DATE_KEYS = {
    "created": "Created",
    "date": "Date",
    "start_date": "Started",
    "updated": "Updated",
    "last_updated": "Updated",
    "modified": "Updated",
    "end_date": "Ended",
}
PEOPLE_KEYS = {
    "owner": "Owner",
    "owners": "Owners",
    "lead": "Lead",
    "author": "Author",
    "authors": "Authors",
    "contributors": "Contributors",
    "maintainers": "Maintainers",
}
RELATIONS = {
    "genes": "gene_ids",
    "terms": "term_ids",
    "families": "family_ids",
    "gocams": "model_ids",
    "modules": "module_ids",
    "reports": "report_ids",
    "projects": "project_ids",
    "rules": "rule_ids",
}


def parse_project(raw):
    text = raw.replace("\r\n", "\n").lstrip("\ufeff")
    match = re.match(r"^---\n(.*?)\n---\s*\n", text, re.S)
    if match:
        metadata = yaml.load(match[1], Loader=yaml.CSafeLoader)
        if isinstance(metadata, dict):
            return metadata, text[match.end() :].strip()
    return {}, text.strip()


def strings(value):
    return [str(x) for x in value] if isinstance(value, list) else [str(value)] if value else []


def discover(root):
    return sorted(p for p in (root / "projects").glob("*.md") if p.name != "README.md") if root else []


def hrefs(value):
    """Targets from a string, a list of strings, or {href, title} mappings."""
    for item in value if isinstance(value, list) else [value] if value else []:
        if isinstance(item, dict) and item.get("href"):
            yield str(item["href"]), str(item.get("title") or "")
        elif isinstance(item, str) and item:
            yield item, ""


def link_texts(body):
    """The first non-empty visible text of each Markdown link, keyed by target."""
    result = {}
    for token in MarkdownIt("js-default").parse(body):
        target, text = None, []
        for child in token.children or []:
            if child.type == "link_open":
                target, text = child.attrGet("href"), []
            elif child.type == "link_close" and target:
                label = re.sub(r"\s+", " ", "".join(text)).strip()
                if label and not result.get(target):
                    result[target] = label
                target = None
            elif target and child.type in ("text", "code_inline"):
                text.append(child.content)
    return result


def display_path(key, target, stem):
    """A path distinguishing same-named files, relative to the project's own folder."""
    return (key or urlsplit(target).path or target).removeprefix("projects/" + stem + "/")


def destinations(metadata, body):
    result = {}
    for token in MarkdownIt("js-default").parse(body):
        for child in token.children or []:
            if child.type in ("link_open", "image"):
                target = child.attrGet("href" if child.type == "link_open" else "src")
                if target:
                    result[target] = "Linked resource"
    for target in strings(metadata.get("related_projects")):
        result[target] = "Related project"
    manifest = metadata.get("manifest") or {}
    if isinstance(manifest, dict):
        for kind, items in manifest.items():
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("href"):
                    result[str(item["href"])] = str(item.get("title") or kind.capitalize())
    for key in PRIMARY_KEYS:
        for target, title in hrefs(metadata.get(key)):
            result.setdefault(target, title or "Primary artifact")
    # Repository-relative paths in inline code are explicit references too.
    for target in re.findall(r"`((?:genes|modules|gocams|interpro|projects|rules)/[^`\s]+)`", body):
        result.setdefault(target, "Source reference")
    return result


def local_path(root, page, target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
        return None
    name = unquote(parsed.path)
    path = (
        (
            root
            if name.startswith(("genes/", "modules/", "gocams/", "interpro/", "projects/", "rules/"))
            else page.parent
        )
        / name
    ).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def input_paths(root):
    paths = set(discover(root))
    for page in list(paths):
        metadata, body = parse_project(page.read_text(encoding="utf-8-sig"))
        for target in destinations(metadata, body):
            path = local_path(root, page, target)
            # Only explicitly referenced files; no recursive copying of analyses.
            if path and path.is_file():
                paths.add(path)
    return sorted(paths)


def primary_artifacts(metadata, resources, natives):
    """Main deliverables: declared, else manifest entries, else the top-level linked page."""
    linked = {r["path"]: r for r in resources if r["available"]} | natives
    manifest = metadata.get("manifest")
    choices = (
        ("declared", [t for k in PRIMARY_KEYS for t, _ in hrefs(metadata.get(k))]),
        (
            "manifest",
            [t for v in manifest.values() for t, _ in hrefs(v)] if isinstance(manifest, dict) else [],
        ),
    )
    for basis, targets in choices:
        found = [linked[t] for t in dict.fromkeys(targets) if t in linked]
        if found:
            return found[:3], basis
    pages = [
        r for r in resources if r["available"] and r.get("media_type") in ("text/html", "application/pdf")
    ] + [r for r in natives.values() if r["url"].startswith("/reports/")]
    if not pages:
        return [], ""

    # The shallowest page wins, then index/report names, then the order linked.
    def rank(item):
        where = item[1]["display_path"]
        return where.count("/"), not re.search(r"index|report", where.rsplit("/", 1)[-1], re.I), item[0]

    return [min(enumerate(pages), key=rank)[1]], "inferred"


def people_and_dates(metadata):
    def name(value):
        return str(value.get("name") or value.get("id") or "") if isinstance(value, dict) else str(value)

    dates = [dict(label=label, value=str(metadata[k])) for k, label in DATE_KEYS.items() if metadata.get(k)]
    people = []
    for key, label in PEOPLE_KEYS.items():
        values = metadata.get(key)
        values = [
            n for n in map(name, values if isinstance(values, list) else [values] if values else []) if n
        ]
        if values:
            people.append(dict(label=label, values=values))
    return dict(dates=dates, people=people)


def load_projects(db, root, expected, work):
    db.execute("CREATE TABLE project_documents(id VARCHAR, document_json VARCHAR)")
    paths, aliases, gene_paths, symbols = {}, defaultdict(set), defaultdict(set), defaultdict(set)
    for gene, symbol, species, ids in db.execute(
        "SELECT id, symbol, species, identifiers FROM genes"
    ).fetchall():
        symbols[species, symbol].add(gene)
        for alias in ids:
            aliases[normalize_id(alias)].add(gene)
    for gene, review_paths in db.execute("SELECT gene_id, review_path FROM gene_reviews").fetchall():
        for path in review_paths.split("|"):
            gene_paths[path].add(gene)
            gene_paths[str(Path(path).parent)].add(gene)
    species_keys = dict(db.execute("SELECT unnest(aliases), key FROM species").fetchall())
    for path, ids in gene_paths.items():
        if len(ids) == 1:
            paths[path] = ("genes", next(iter(ids)))
    for collection, path, identifier in db.execute(
        "SELECT collection, path, record_id FROM knowledge_sources"
    ).fetchall():
        paths[path] = (collection, identifier)
    rule_paths = defaultdict(set)
    for rule_id, rule_path in db.execute("SELECT id, source_path FROM rules").fetchall():
        rule_paths[rule_path].add(rule_id)
    report_rows = db.execute("SELECT id, source_path FROM reports").fetchall()
    for identifier, path in report_rows:
        paths[path] = ("reports", identifier)
    for page in discover(root):
        paths[str(page.relative_to(root))] = ("projects", "PROJECT:" + page.stem)
    # Published HTML counterparts of indexed source pages link to native detail pages.
    for path, record in list(paths.items()):
        if path.endswith((".md", ".yaml")):
            paths[str(Path(path).with_suffix(".html"))] = record
    records, documents, asset_rows = [], [], {}
    assets = work / "report-assets"

    def capture(path, name, role):
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if expected.get(str(path.relative_to(root))) != checksum:
            raise ValueError(f"Project source changed during build: {path}")
        (assets / checksum).write_bytes(raw)
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset_rows[checksum] = (checksum, path.name, media)
        return dict(
            path=name,
            filename=path.name,
            role=role,
            available=True,
            sha256=checksum,
            media_type=media,
            url="/api/report-assets/" + checksum,
        )

    for page in discover(root):
        relative = str(page.relative_to(root))
        source = capture(page, page.name, "source")
        metadata, body = parse_project((assets / source["sha256"]).read_text(encoding="utf-8-sig"))
        identifier = "PROJECT:" + page.stem
        heading = re.search(r"^#\s+(.+)", body, re.M)
        label = str(metadata.get("title") or (heading[1] if heading else page.stem))
        paragraphs = [
            p.strip()
            for p in body.split("\n\n")
            if p.strip() and not p.lstrip().startswith(("#", "|", "```", "!["))
        ]
        record = {
            **{k: [] for k in LISTS},
            "id": identifier,
            "label": label,
            "description": re.sub(r"\s+", " ", paragraphs[0])[:400] if paragraphs else "",
            "kind": "Project",
            "maturity": str(metadata.get("maturity") or metadata.get("status") or "UNSPECIFIED"),
            "tags": strings(metadata.get("tags")),
            "project_species": strings(metadata.get("species")),
            "project_collections": strings(metadata.get("collections")),
            "declared_genes": strings(metadata.get("genes")),
            "source_path": relative,
        }
        refs = {c: set() for c in RELATIONS}
        # Symbols resolve only within explicitly declared species, retaining all candidates.
        for species in record["project_species"]:
            species = species_keys.get(species, species)
            for gene in record["declared_genes"]:
                refs["genes"].update(symbols[species, gene])
        # A UniProt column is an explicit identifier source, even without CURIE prefixes.
        uniprot_column = None
        for line in body.splitlines():
            if not line.strip().startswith("|"):
                uniprot_column = None
                continue
            cells = line.strip().strip("|").split("|")
            headers = [i for i, cell in enumerate(cells) if "uniprot" in cell.lower()]
            if headers and uniprot_column is None:
                uniprot_column = headers[0]
                continue
            if uniprot_column is not None and uniprot_column < len(cells):
                for accession in re.findall(
                    r"\b[A-Z0-9]{6}(?:[A-Z0-9]{4})?(?:-\d+)?\b", cells[uniprot_column]
                ):
                    refs["genes"].update(aliases["UniProtKB:" + accession])
        for alias in re.findall(r"\b(?:UniProtKB:[A-Z0-9-]+|HGNC:\d+|PomBase:[\w.]+)\b", body):
            refs["genes"].update(aliases[normalize_id(alias)])
        # Typed identifiers are references in the narrative, not annotation assertions.
        for collection, pattern in (
            ("terms", r"\bGO:\d{7}\b"),
            ("families", r"\bPANTHER:PTHR\d+\b"),
            ("gocams", r"\bgomodel:[a-zA-Z0-9]+\b"),
            ("modules", r"\bMODULE:[a-zA-Z0-9_]+\b"),
        ):
            candidates = sorted(set(re.findall(pattern, body)))
            if candidates:
                refs[collection].update(
                    r[0]
                    for r in db.execute(
                        f"SELECT id FROM {collection} WHERE id IN (SELECT unnest(?::VARCHAR[]))", [candidates]
                    ).fetchall()
                )
        resources, link_map, natives, texts = [], {}, {}, link_texts(body)
        for target, title in destinations(metadata, body).items():
            path = local_path(root, page, target)
            key = str(path.relative_to(root)) if path else ""
            # Link text, else the project-relative path: many artifacts are just index.html.
            where = display_path(key, target, page.stem)
            generic = title in ("Linked resource", "Source reference")
            info = dict(label=texts.get(target) or ("" if generic else title) or where, display_path=where)
            if key in rule_paths:
                refs["rules"].update(rule_paths[key])
                link_map[target] = "/?" + urlencode(
                    {
                        "collection": "rules",
                        "filters": json.dumps([dict(id="source", field="source_path", values=[key])]),
                    }
                )
                natives[target] = dict(path=target, url=link_map[target], available=True, **info)
                continue
            match = paths.get(key)
            if match:
                collection, target_id = match
                if target_id != identifier:
                    refs[collection].add(target_id)
                link_map[target] = "/" + collection + "/" + quote(target_id, safe="")
                natives[target] = dict(path=target, url=link_map[target], available=True, **info)
                continue
            parsed = urlsplit(target)
            if parsed.scheme in ("http", "https"):
                resources.append(
                    dict(path=target, title=title, url=target, external=True, available=True, **info)
                )
            elif path and path.is_file():
                file = capture(path, target, title)
                resources.append({**file, **info})
                link_map[target] = file["url"]
            elif path:
                resources.append(
                    dict(path=target, title=title, filename=Path(parsed.path).name, available=False, **info)
                )
        primary, basis = primary_artifacts(metadata, resources, natives)
        # Reports stored beneath a project's own folder are direct project resources.
        prefix = "projects/" + page.stem + "/"
        refs["reports"].update(i for i, p in report_rows if p.startswith(prefix))
        refs["rules"].update(i for p, ids in rule_paths.items() if p.startswith(prefix) for i in ids)
        for collection, field in RELATIONS.items():
            record[field] = sorted(refs[collection])
        record["resource_count"] = sum(r["available"] for r in resources)
        record["search_text"] = " ".join([relative, label, body, json.dumps(metadata, default=str)])
        records.append(record)
        documents.append(
            (
                identifier,
                json.dumps(
                    dict(
                        body=body,
                        metadata=metadata,
                        source=source,
                        resources=resources,
                        links=link_map,
                        summary=dict(primary=primary, basis=basis, **people_and_dates(metadata)),
                    ),
                    default=str,
                ),
            )
        )
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    db.register("_projects", table)
    db.execute("CREATE TABLE projects AS SELECT * FROM _projects")
    db.unregister("_projects")
    db.execute("""UPDATE rules SET project_ids = linked.ids FROM (
        SELECT rule_id, list(id ORDER BY id) AS ids FROM
        (SELECT id, unnest(rule_ids) AS rule_id FROM projects) GROUP BY rule_id
    ) linked WHERE rules.id = linked.rule_id""")
    if documents:
        db.executemany("INSERT INTO project_documents VALUES (?, ?)", documents)
    if asset_rows:
        # Reuse the immutable artifact store, including content shared with reports.
        existing = {r[0] for r in db.execute("SELECT sha256 FROM report_assets").fetchall()}
        rows = [row for key, row in asset_rows.items() if key not in existing]
        if rows:
            db.executemany("INSERT INTO report_assets VALUES (?, ?, ?)", rows)
    return {"projects": len(records), "assets": len(asset_rows)}


def project_detail(path, record):
    with duckdb.connect(str(path / "integration.duckdb"), read_only=True) as db:
        document = json.loads(
            db.execute("SELECT document_json FROM project_documents WHERE id=?", [record["id"]]).fetchone()[0]
        )
        related = {}
        for collection, field in RELATIONS.items():
            ids = record[field]
            if not ids:
                continue
            label = "symbol" if collection == "genes" else "label"
            rows = (
                db.execute(
                    f"SELECT id, {label} AS label FROM {collection} WHERE id IN (SELECT unnest(?::VARCHAR[])) ORDER BY label, id LIMIT 100",
                    [ids],
                )
                .to_arrow_table()
                .to_pylist()
            )
            related[collection] = dict(total=len(ids), items=rows, field=field)
    body = document.pop("body")
    files = [r for r in document["resources"] if not r.get("external") and r.get("available")]
    files += [
        dict(path=target, url=url, available=True, media_type="text/plain")
        for target, url in document.pop("links").items()
        if not any(f["path"] == target for f in files)
    ]
    # Preserve project-local section links using the reader's generated heading IDs.
    slugs = defaultdict(int)
    for token in MarkdownIt("js-default").parse(body):
        if token.type == "inline" and token.map and body.splitlines()[token.map[0]].startswith("#"):
            slug = re.sub(r"[^\w\s-]", "", token.content.lower()).replace(" ", "-")
            suffix = "-" + str(slugs[slug]) if slugs[slug] else ""
            slugs[slug] += 1
            files.append(
                dict(
                    path="#" + slug + suffix,
                    url=f"#project-heading-{sum(slugs.values()) - 1}",
                    available=True,
                    media_type="text/plain",
                )
            )
    html, toc = render_markdown(body, files, prefix="project")
    return dict(record=record, **document, html=html, toc=toc, related=related)
