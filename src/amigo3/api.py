"""Generic collection, query, resolution, and provenance endpoints."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Literal

import duckdb
import httpx
from fastapi import FastAPI, HTTPException
from fastapi import Query as Param
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from amigo3.auth import AuthConfig, attach_auth
from amigo3.catalog import Catalog
from amigo3.cell_types import cell_type_detail
from amigo3.chat import attach_chat
from amigo3.chemicals import chemical_detail
from amigo3.details import gene_related, resource_detail, term_detail
from amigo3.duck import DuckBackend
from amigo3.enrichment import ASPECTS, EVIDENCE, EXCLUSIONS, METHODS, TARGETS, EnrichmentRequest, enrich
from amigo3.gene import gene_detail
from amigo3.gene_network import gene_network
from amigo3.genesets import GenesetClient
from amigo3.genesets import geneset as geneset_detail
from amigo3.genesets import options as geneset_options
from amigo3.genesets import search as geneset_search
from amigo3.megamodels import ConnectionRequest, MegamodelRequest, NetworkRequest, model_index, network
from amigo3.network_enrichment import NetworkEdgesRequest
from amigo3.network_enrichment import edge_details as network_edge_details
from amigo3.network_enrichment import options as network_enrichment_options
from amigo3.ontology_graph import ontology_graph
from amigo3.parse import identifier_variants
from amigo3.predictions import FACETS, STATUSES, claim_rows, export_tsv, list_sets
from amigo3.projects import project_detail
from amigo3.query import Collection, Filter, Query
from amigo3.reactions import reaction_details
from amigo3.references import reference_detail
from amigo3.reports import report_detail
from amigo3.rules import rule_detail
from amigo3.satisfiability import SatisfiabilityRequest, module_satisfiability, satisfiability_options
from amigo3.solr import SolrBackend
from amigo3.web_assets import BrowserFiles


class ReviewRequest(BaseModel):
    annotation_ids: list[str] = Field(min_length=1, max_length=100)
    view: str | None = None


def create_app(
    data: Path = Path("data"),
    backend: str = "solr",
    solr_url: str = "http://127.0.0.1:8987/solr",
    chat_command: list[str] | None = None,
    auth_config: AuthConfig | None = None,
    genesets: GenesetClient | None = None,
):
    build_id = None
    if backend == "solr" and (data / "published.json").exists():
        build_id = json.loads((data / "published.json").read_text())["build_id"]
    catalog = Catalog(data, build_id)
    engine = SolrBackend(catalog, solr_url) if backend == "solr" else DuckBackend(catalog)
    app = FastAPI(
        title="AmiGO 3 collection API",
        version="0.1.0",
        description="Generic search and faceting over immutable biological data collections. All requests use a pinned build.",
    )
    attach_auth(app, auth_config if auth_config is not None else AuthConfig.from_env())
    browser_files = BrowserFiles()
    app.state.catalog = catalog
    app.state.engine = engine
    prediction_sets = {s["id"]: s for s in list_sets(catalog.path)}

    def prediction_set(identifier, view):
        if view and view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        if identifier not in prediction_sets:
            raise HTTPException(404, "External set is not available in this build")
        return prediction_sets[identifier]

    @app.get("/api/predictions")
    def predictions():
        return {
            "build_id": catalog.build_id,
            "sets": list(prediction_sets.values()),
            "statuses": STATUSES,
            "facet_labels": FACETS,
        }

    @app.get("/api/predictions/{identifier}/claims")
    def prediction_claims(
        identifier: str,
        view: str | None = None,
        status: str = "",
        aspect: str = "",
        q: str = Param("", max_length=200),
        mapping_status: str = Param("", max_length=300),
        model_type: str = Param("", max_length=300),
        evidence_code: str = Param("", max_length=300),
        gene: str = Param("", max_length=200),
        offset: int = Param(0, ge=0),
        size: int = Param(50, ge=1, le=200),
    ):
        prediction_set(identifier, view)
        return {
            "build_id": catalog.build_id,
            "set_id": identifier,
            **claim_rows(
                catalog.path,
                identifier,
                status,
                aspect,
                q,
                offset,
                size,
                mapping_status=mapping_status,
                model_type=model_type,
                evidence_code=evidence_code,
                gene=gene,
            ),
        }

    @app.get("/api/predictions/{identifier}/export")
    def prediction_export(
        identifier: str,
        view: str | None = None,
        status: str = "",
        aspect: str = "",
        q: str = Param("", max_length=200),
        mapping_status: str = Param("", max_length=300),
        model_type: str = Param("", max_length=300),
        evidence_code: str = Param("", max_length=300),
        gene: str = Param("", max_length=200),
        format: Literal["tsv", "json"] = "tsv",
    ):
        source = prediction_set(identifier, view)
        if format == "json":
            return JSONResponse(
                {"build_id": catalog.build_id, **source},
                headers={
                    "Content-Disposition": f'attachment; filename="{identifier}-{catalog.build_id}.json"'
                },
            )
        filters = {
            "status": status,
            "aspect": aspect,
            "q": q,
            "mapping_status": mapping_status,
            "model_type": model_type,
            "evidence_code": evidence_code,
            "gene": gene,
        }
        result = claim_rows(catalog.path, identifier, size=None, include_facets=False, **filters)
        return Response(
            export_tsv(result["rows"], catalog.build_id, identifier, filters),
            media_type="text/tab-separated-values",
            headers={"Content-Disposition": f'attachment; filename="{identifier}-{catalog.build_id}.tsv"'},
        )

    @app.get("/api/predictions/{identifier}/sources/{kind}")
    def prediction_source(identifier: str, kind: str, view: str | None = None):
        source = prediction_set(identifier, view)
        if kind not in source["files"]:
            raise HTTPException(404, "Source artifact not found")
        path = catalog.path / source["files"][kind]["path"]
        return FileResponse(path, filename=path.name)

    # Match per-set routes before the catch-all gene identifier path.
    @app.get("/api/predictions/gene/{identifier:path}")
    def gene_predictions(
        identifier: str,
        view: str | None = None,
        status: str = "",
        offset: int = Param(0, ge=0),
        size: int = Param(50, ge=1, le=200),
    ):
        if view and view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        record("genes", identifier)
        return {
            "build_id": catalog.build_id,
            "gene_id": identifier,
            "statuses": STATUSES,
            "sets": [{"id": s["id"], "label": s["label"]} for s in prediction_sets.values()],
            **claim_rows(catalog.path, None, status=status, gene=identifier, offset=offset, size=size),
        }

    @app.get("/predictions", include_in_schema=False)
    def predictions_page():
        return browser_files.page(browser_files.root / "predictions.html")

    @app.exception_handler(ValueError)
    async def invalid_query(request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(httpx.HTTPError)
    async def backend_error(request, error):
        return JSONResponse(
            status_code=503,
            content={"detail": "Search backend unavailable. Check the Solr process and published build."},
        )

    @app.get("/api/health")
    def health():
        engine.query(Query(collection="terms", select=["id"]))
        return {"status": "ok", "backend": engine.name, "build_id": catalog.build_id}

    @app.get("/api/collections")
    def collections():
        return {
            "build_id": catalog.build_id,
            "backend": engine.name,
            "collections": catalog.collections,
            "capabilities": {
                "facets": "terms",
                "facet_exclusions": True,
                "counts": "exact records",
                "pagination": "build-pinned cursor",
                "read_only": True,
            },
        }

    @app.post("/api/query")
    def query(request: Query):
        return engine.query(request)

    @app.get("/api/resolve")
    def resolve(identifier: str):
        candidates = identifier_variants(identifier)
        if ":" not in identifier:
            candidates.extend(["UniProtKB:" + identifier, "PomBase:" + identifier, "WB:" + identifier])
        return engine.query(
            Query(
                collection="genes", filters=[Filter(id="identifier", field="identifiers", values=candidates)]
            )
        )

    @app.get("/api/records/{collection}/{identifier:path}")
    def record(collection: Collection, identifier: str):
        response = engine.query(
            Query(collection=collection, filters=[Filter(id="id", field="id", values=[identifier])])
        )
        if not response["hits"]:
            raise HTTPException(404, "Record not found in this build")
        return {"build_id": catalog.build_id, "record": response["hits"][0]}

    @app.post("/api/annotation-reviews")
    def annotation_reviews(request: ReviewRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            if not db.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='annotation_review_links'"
            ).fetchone()[0]:
                raise HTTPException(409, "Rebuild to index annotation review assessments")
            rows = db.execute(
                """SELECT l.annotation_id, e.id, e.path, e.sha256, e.subject_id, e.entry_json
                FROM annotation_review_links l JOIN annotation_review_entries e ON e.id=l.review_entry_id
                WHERE l.annotation_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY l.annotation_id, e.id""",
                [request.annotation_ids],
            ).fetchall()
        assessments = {identifier: [] for identifier in request.annotation_ids}
        for annotation_id, identifier, path, checksum, subject, raw in rows:
            assessments[annotation_id].append(
                {
                    "id": identifier,
                    "path": path,
                    "sha256": checksum,
                    "subject_id": subject,
                    "entry": json.loads(raw),
                }
            )
        return {"build_id": catalog.build_id, "assessments": assessments}

    @app.get("/api/genes/{identifier:path}")
    def gene(identifier: str):
        result = record("genes", identifier)
        ann_col = copy.deepcopy(catalog.collections["annotations"])
        if "is_redundant" not in ann_col["fields"]:
            ann_col["fields"]["is_redundant"] = {
                "type": "boolean",
                "multivalued": False,
                "label": ann_col.get("field_labels", {}).get("is_redundant", "Redundant (broader term)"),
                "facetable": True,
            }
            if "is_redundant" not in ann_col["facets"]:
                ann_col["facets"].append("is_redundant")
            for group in ann_col.get("facet_groups", []):
                if group.get("id") == "annotation" and "is_redundant" not in group["fields"]:
                    group["fields"].append("is_redundant")
        return {
            "build_id": catalog.build_id,
            "annotation_collection": ann_col,
            **gene_detail(catalog.path, result["record"]),
            "related": gene_related(catalog.path, identifier),
        }

    @app.get("/genes/{identifier:path}", include_in_schema=False)
    def gene_page(identifier: str):
        return browser_files.page(browser_files.root / "gene.html")

    @app.get("/api/megamodels")
    def megamodel_options():
        return {"build_id": catalog.build_id, **model_index(catalog.path).options()}

    @app.post("/api/megamodels")
    def megamodel(request: MegamodelRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return {"build_id": catalog.build_id, **model_index(catalog.path).explore(request)}

    @app.get("/megamodels", include_in_schema=False)
    def megamodel_page():
        return browser_files.page(browser_files.root / "megamodels.html")

    @app.post("/api/megamodels/network")
    def model_network(request: NetworkRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return {"build_id": catalog.build_id, **network(model_index(catalog.path), engine, request)}

    @app.post("/api/megamodels/connection")
    def model_connection(request: ConnectionRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return {"build_id": catalog.build_id, **model_index(catalog.path).connection(request)}

    @app.get("/api/gene-network")
    def projected_gene_network(taxon: str | None = None, summary: bool = False, view: str | None = None):
        if view and view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return {"build_id": catalog.build_id, **gene_network(catalog.path).result(taxon, summary)}

    @app.get("/api/enrichment")
    def enrichment_options():
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            species = db.execute(
                "SELECT species, count(DISTINCT gene_id) FROM annotations GROUP BY species ORDER BY species"
            ).fetchall()
            in_models = {}
            if db.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='gocams'"
            ).fetchone()[0]:
                in_models = dict(
                    db.execute(
                        """SELECT g.species, count(DISTINCT g.id) FROM (SELECT unnest(gene_ids) AS id FROM gocams) m
                        JOIN genes g USING (id) GROUP BY g.species"""
                    ).fetchall()
                )
        return {
            "build_id": catalog.build_id,
            "species": [
                {"value": s, "annotated_genes": n, "gocam_genes": in_models.get(s, 0)} for s, n in species
            ],
            "targets": TARGETS,
            "methods": METHODS,
            "aspects": ASPECTS,
            "evidence": EVIDENCE,
            "exclusions": EXCLUSIONS,
        }

    @app.post("/api/enrichment")
    def enrichment(request: EnrichmentRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return enrich(catalog.path, request, catalog.build_id)

    @app.get("/api/enrichment/network")
    def network_options():
        return {"build_id": catalog.build_id, **network_enrichment_options(catalog.path)}

    @app.post("/api/enrichment/network/edges")
    def enrichment_edges(request: NetworkEdgesRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        return network_edge_details(catalog.path, request, catalog.build_id)

    genesets = genesets or GenesetClient()

    def external(call):
        try:
            return call()
        except KeyError:  # Before LookupError, its base class.
            raise HTTPException(404, "Gene set not found") from None
        except LookupError as error:
            raise HTTPException(503, str(error)) from None
        except httpx.HTTPError:
            raise HTTPException(502, "MyGeneset is unavailable; bundled examples remain usable") from None

    @app.get("/api/genesets")
    def geneset_list(species: str):
        return {"build_id": catalog.build_id, **geneset_options(catalog.path, species, genesets)}

    @app.get("/api/genesets/search")
    def geneset_find(
        species: str,
        q: str = Param(min_length=2, max_length=200),
        collection: Literal[
            "signatures", "regulatory", "chemicals", "disease", "pathways", "all"
        ] = "signatures",
        size: int = Param(20, ge=1, le=50),
    ):
        return external(lambda: geneset_search(catalog.path, species, q, genesets, collection, size))

    @app.get("/api/genesets/{identifier:path}")
    def geneset_get(identifier: str, species: str):
        return {
            "build_id": catalog.build_id,
            **external(lambda: geneset_detail(catalog.path, species, identifier, genesets)),
        }

    @app.get("/enrichment", include_in_schema=False)
    def enrichment_page():
        return browser_files.page(browser_files.root / "enrichment.html")

    @app.get("/help/network-enrichment", include_in_schema=False)
    def network_enrichment_help():
        return browser_files.page(browser_files.root / "network-enrichment-help.html")

    @app.get("/api/ontology/{identifier:path}")
    def ontology(identifier: str, view: str | None = None):
        if view and view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            records = (
                db.execute(
                    "SELECT id, isa_partof_closure FROM terms WHERE id=? OR list_contains(alt_ids, ?) ORDER BY id",
                    [identifier, identifier],
                )
                .to_arrow_table()
                .to_pylist()
            )
            if not records:
                raise HTTPException(404, "Unknown ontology term")
            return {"build_id": catalog.build_id, **ontology_graph(db, records[0])}

    @app.get("/api/terms/{identifier:path}")
    def term(identifier: str):
        found = engine.query(
            Query(collection="terms", filters=[Filter(id="term", field="id", values=[identifier])])
        )
        if not found["hits"]:
            found = engine.query(
                Query(
                    collection="terms", filters=[Filter(id="alternate", field="alt_ids", values=[identifier])]
                )
            )
        if not found["hits"]:
            raise HTTPException(404, "Term not found in this build")
        return {
            "build_id": catalog.build_id,
            **term_detail(catalog.path, found["hits"][0]),
            "reaction_sources": reaction_sources(),
            "differentiation_sources": {
                k: s
                for k, s in catalog.manifest["sources"].items()
                if s["kind"] in ("go_plus_json", "cl_json")
            },
        }

    def reaction_sources():
        return {
            key: source
            for key, source in catalog.manifest["sources"].items()
            if source["kind"] in ("rhea_rdf", "rhea_go")
        }

    @app.get("/api/reactions/{identifier:path}")
    def reaction(identifier: str):
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            rows = reaction_details(db, reaction_id=identifier)
        if not rows:
            raise HTTPException(404, "Reaction not found in this build")
        return {"build_id": catalog.build_id, "record": rows[0], "sources": reaction_sources()}

    @app.get("/api/chemical-structures/{identifier:path}")
    def chemical_structure(identifier: str, sha256: str | None = None):
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            if "chemical_structures" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}:
                raise HTTPException(404, "No structures in this build")
            found = db.execute(
                "SELECT svg_path, svg_sha256 FROM chemical_structures WHERE chemical_id=?", [identifier]
            ).fetchone()
        if not found or (sha256 and sha256 != found[1]):
            raise HTTPException(404, "Structure not found in this build")
        return FileResponse(
            catalog.path / found[0],
            media_type="image/svg+xml",
            headers={
                "ETag": '"' + found[1] + '"',
                "Cache-Control": "public, max-age=31536000, immutable" if sha256 else "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/cell_types/{identifier:path}")
    def cell_type(identifier: str):
        result = record("cell_types", identifier)
        return {
            "build_id": catalog.build_id,
            **cell_type_detail(catalog.path, result["record"]),
            "source": catalog.manifest["sources"].get(result["record"]["source_id"]),
        }

    @app.get("/api/chemicals/{identifier:path}")
    def chemical(identifier: str):
        result = record("chemicals", identifier)
        detail = chemical_detail(catalog.path, result["record"])
        return {
            "build_id": catalog.build_id,
            **detail,
            "source": catalog.manifest["sources"].get(result["record"]["source_id"]),
        }

    @app.get("/api/resources/{collection}/{identifier:path}")
    def resource(collection: Literal["gocams", "modules", "families", "rules"], identifier: str):
        result = record(collection, identifier)
        detail = (
            rule_detail(catalog.path, result["record"])
            if collection == "rules"
            else resource_detail(catalog.path, collection, result["record"])
        )
        return {"build_id": catalog.build_id, **detail}

    @app.get("/api/satisfiability")
    def satisfiability_choices(module: str):
        result = record("modules", module)
        return {"build_id": catalog.build_id, **satisfiability_options(catalog.path, result["record"])}

    @app.post("/api/satisfiability")
    def satisfiability(request: SatisfiabilityRequest):
        if request.view and request.view != catalog.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        result = record("modules", request.module)
        return {
            "build_id": catalog.build_id,
            "module": request.module,
            **module_satisfiability(catalog.path, result["record"], request),
        }

    @app.get("/api/projects/{identifier:path}")
    def project(identifier: str):
        result = record("projects", identifier)
        return {"build_id": catalog.build_id, **project_detail(catalog.path, result["record"])}

    @app.get("/projects/{identifier:path}", include_in_schema=False)
    def project_page(identifier: str):
        return browser_files.page(browser_files.root / "project.html")

    @app.get("/api/reports/{identifier:path}")
    def research_report(identifier: str):
        result = record("reports", identifier)
        return {"build_id": catalog.build_id, **report_detail(catalog.path, result["record"])}

    @app.get("/reports/{identifier:path}", include_in_schema=False)
    def report_page(identifier: str):
        return browser_files.page(browser_files.root / "report.html")

    @app.get("/api/references/{identifier:path}")
    def reference(identifier: str):
        result = record("references", identifier)
        return {"build_id": catalog.build_id, **reference_detail(catalog.path, result["record"])}

    @app.get("/references/{identifier:path}", include_in_schema=False)
    def reference_page(identifier: str):
        return browser_files.page(browser_files.root / "reference.html")

    @app.get("/api/report-assets/{checksum}")
    def report_asset(checksum: str):
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise HTTPException(404, "Asset not found")
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            row = db.execute(
                "SELECT filename, media_type FROM report_assets WHERE sha256=? LIMIT 1", [checksum]
            ).fetchone()
        if not row:
            raise HTTPException(404, "Asset not found")
        image = row[1] in ("image/png", "image/jpeg", "image/gif", "image/webp")
        return FileResponse(
            catalog.path / "report-assets" / checksum,
            filename=row[0],
            media_type=row[1],
            content_disposition_type="inline" if image else "attachment",
            headers={
                "X-Content-Type-Options": "nosniff",
                "ETag": checksum,
                "Content-Security-Policy": "sandbox",
            },
        )

    @app.get("/api/snapshots/{checksum}")
    def snapshot(checksum: str):
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            if not db.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='knowledge_sources'"
            ).fetchone()[0]:
                raise HTTPException(404, "No repository snapshots in this build")
            row = db.execute(
                "SELECT raw_text FROM knowledge_sources WHERE sha256=? LIMIT 1", [checksum]
            ).fetchone()
            if row is None and "rules" in catalog.collections:
                row = db.execute(
                    "SELECT raw_text FROM rule_sources WHERE sha256=? LIMIT 1", [checksum]
                ).fetchone()
        if row is None:
            raise HTTPException(404, "Snapshot not found")
        return Response(
            row[0].encode("utf-8"),
            media_type="text/plain",
            headers={"ETag": checksum, "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/terms/{identifier:path}", include_in_schema=False)
    @app.get("/cell_types/{identifier:path}", include_in_schema=False)
    @app.get("/chemicals/{identifier:path}", include_in_schema=False)
    @app.get("/gocams/{identifier:path}", include_in_schema=False)
    @app.get("/modules/{identifier:path}", include_in_schema=False)
    @app.get("/rules/{identifier:path}", include_in_schema=False)
    @app.get("/families/{identifier:path}", include_in_schema=False)
    def resource_page(identifier: str):
        return browser_files.page(browser_files.root / "resource.html")

    @app.get("/api/provenance/{identifier:path}")
    def provenance(identifier: str):
        with duckdb.connect(str(catalog.path / "integration.duckdb"), read_only=True) as db:
            row = db.execute(
                "SELECT source_id, source_line, raw_record FROM source_annotations WHERE id=?", [identifier]
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Source annotation not found")
        source, line, raw = row
        return {
            "build_id": catalog.build_id,
            "source": catalog.manifest["sources"][source],
            "line": line,
            "raw_record": raw,
            "header": catalog.manifest["source_headers"][source],
        }

    @app.get("/api/manifest")
    def manifest():
        return catalog.manifest

    @app.get("/species.js")
    def species_script():
        """species.js, preceded by this build's species labels so pages can label synchronously."""
        labels = {
            name: entry["label"]
            for key, entry in catalog.manifest["species"].items()
            for name in [key, *entry.get("aliases", [])]
        }
        script = (browser_files.root / "species.js").read_text()
        return Response(
            f"const SPECIES_LABELS = {json.dumps(labels)};\n{script}",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/report")
    def report():
        return json.loads((catalog.path / "report.json").read_text())

    attach_chat(app, catalog, chat_command, browser_files)
    app.mount("/", browser_files, name="browser")
    return app
