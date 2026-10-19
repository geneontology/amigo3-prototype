import hashlib

from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.projects import link_texts, local_path, parse_project, people_and_dates, primary_artifacts


def test_project_search_detail_links_and_snapshots(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        result = client.post(
            "/api/query",
            json={
                "collection": "projects",
                "search": "searchablephrase",
                "facets": {"maturity": {"field": "maturity"}},
            },
        ).json()
        assert result["total"] == 1
        r = result["hits"][0]
        assert r["id"] == "PROJECT:TEST"
        assert r["gene_ids"] == ["HGNC:1", "HGNC:2"]
        assert r["declared_genes"] == ["GENE1", "not_a_gene"]
        assert r["project_species"] == ["human"]
        assert r["module_ids"] == ["MODULE:test"]
        assert r["project_ids"] == ["PROJECT:OTHER"]
        assert result["facets"]["maturity"]["buckets"] == [{"value": "IN_PROGRESS", "count": 1}]
        assert client.post("/api/query", json={"collection": "projects"}).json()["total"] == 2
        detail = client.get("/api/projects/PROJECT:TEST").json()
        assert "<script>" not in detail["html"]
        assert 'href="/genes/HGNC%3A1"' in detail["html"]
        assert 'href="/modules/MODULE%3Atest"' in detail["html"]
        assert 'href="#project-heading-1"' in detail["html"]
        assert detail["related"]["terms"]["items"][0]["id"] == "GO:0016301"
        assert detail["related"]["genes"]["total"] == 2
        assert client.get("/api/genes/HGNC:1").json()["related"]["projects"]["total"] == 1
        assert client.get("/api/terms/GO:0016301").json()["related"]["projects"]["total"] == 1
        assert client.get("/api/resources/modules/MODULE:test").json()["related"]["projects"]["total"] == 1
        report = r["report_ids"][0]
        assert client.get("/api/reports/" + report).json()["related"]["projects"]["total"] == 1
        slides = next(f for f in detail["resources"] if f.get("filename") == "slides.html")
        response = client.get(slides["url"])
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-security-policy"] == "sandbox"
        assert hashlib.sha256(response.content).hexdigest() == slides["sha256"]
        (built_data / "review_repo/projects/TEST/slides.html").write_text("new version")
        assert client.get(slides["url"]).content == response.content
        # Hero metadata: manifest entries are the main artifact; dates and owners are surfaced.
        summary = detail["summary"]
        assert summary["basis"] == "manifest"
        assert [(p["label"], p["url"]) for p in summary["primary"]] == [("Slides", slides["url"])]
        assert summary["dates"] == [{"label": "Created", "value": "2025-01-15"}]
        assert summary["people"] == [{"label": "Owners", "values": ["Ada Lovelace", "Grace Hopper"]}]
        run = next(f for f in detail["resources"] if f["path"] == "TEST/results/run1/index.html")
        assert run["label"] == "index.html" and run["display_path"] == "results/run1/index.html"
        source = client.get(detail["source"]["url"])
        assert hashlib.sha256(source.content).hexdigest() == detail["source"]["sha256"]
        assert client.get("/projects/PROJECT:TEST").status_code == 200
        assert client.get("/api/projects/PROJECT:missing").status_code == 404


def test_project_parser_and_path_boundary(tmp_path):
    page = tmp_path / "projects" / "TEST.md"
    page.parent.mkdir()
    (page.parent / "outside").symlink_to("/etc/hosts")
    assert local_path(tmp_path, page, "outside") is None
    assert local_path(tmp_path, page, "../../secret") is None
    assert local_path(tmp_path, page, "https://example.org/a") is None
    assert (
        local_path(tmp_path, page, "../genes/human/A/A-ai-review.yaml")
        == tmp_path / "genes/human/A/A-ai-review.yaml"
    )
    metadata, body = parse_project("---\ntitle: A project\n---\n## Output\nStill project content")
    assert metadata["title"] == "A project" and body.startswith("## Output")


def resource(path, media="text/html"):
    where = path.removeprefix("P/")
    return dict(
        path=path, label=where, display_path=where, available=True, media_type=media, url="/a/" + path
    )


def test_primary_artifact_selection_and_labels():
    resources = [resource("P/a/b/index.html"), resource("P/data.csv", "text/csv"), resource("P/x/notes.html")]
    resources += [
        resource("P/x/report.pdf", "application/pdf"),
        resource("P/gone.html") | dict(available=False),
    ]
    # Without declarations, the shallowest page wins, preferring index/report names.
    assert primary_artifacts({}, resources, {}) == ([resources[3]], "inferred")
    report = dict(path="P/r.md", label="Report", display_path="r.md", url="/reports/R1", available=True)
    assert primary_artifacts({}, resources, {"P/r.md": report}) == ([report], "inferred")
    manifest = {"manifest": {"slides": [{"href": "P/x/notes.html"}, {"href": "P/missing.html"}]}}
    assert primary_artifacts(manifest, resources, {}) == ([resources[2]], "manifest")
    declared = {**manifest, "primary_artifact": "P/a/b/index.html"}
    assert primary_artifacts(declared, resources, {}) == ([resources[0]], "declared")
    assert primary_artifacts({}, resources[1:2], {}) == ([], "")
    assert link_texts("[Full **report**](a/index.html) [](b.html) [`c`](c.html)") == {
        "a/index.html": "Full report",
        "c.html": "c",
    }
    assert people_and_dates({"updated": "2025", "lead": {"id": "ORCID:1"}, "authors": []}) == {
        "dates": [{"label": "Updated", "value": "2025"}],
        "people": [{"label": "Lead", "values": ["ORCID:1"]}],
    }
