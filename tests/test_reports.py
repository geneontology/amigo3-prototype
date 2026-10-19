import hashlib

from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.reports import attachments, render_markdown, split_report


def test_reports_search_relationships_and_frozen_downloads(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        query = {
            "collection": "reports",
            "search": "distinctiveflavorkinase",
            "facets": {"provider": {"field": "provider"}},
        }
        found = client.post("/api/query", json=query).json()
        assert found["total"] == 1
        assert found["facets"]["provider"]["buckets"] == [{"value": "openscientist", "count": 1}]
        report = found["hits"][0]
        assert "search_text" not in report
        assert report["gene_ids"] == ["HGNC:1"]
        assert report["artifact_count"] == 1
        detail = client.get("/api/reports/" + report["id"]).json()
        assert "<table>" in detail["html"]
        assert "<script>" not in detail["html"]
        assert 'href="javascript:' not in detail["html"]
        assert "<img" not in detail["html"]
        assert "Prompt-only-token" in detail["prompt"]
        assert "Prompt-only-token" not in detail["html"]
        assert not client.post("/api/query", json={**query, "search": "Prompt-only-token"}).json()["total"]
        assert len(detail["toc"]) == 1
        assert detail["genes"] == [{"id": "HGNC:1", "label": "GENE1"}]
        assert len(detail["files"]) == 3  # CSV, missing PDF, citation sidecar; no traversal
        csv = next(f for f in detail["files"] if f["filename"] == "evidence.csv")
        response = client.get(csv["url"])
        assert hashlib.sha256(response.content).hexdigest() == csv["sha256"]
        assert response.headers["content-disposition"].startswith("attachment;")
        (built_data / "review_repo/genes/human/GENE1/evidence.csv").write_text("Changed upstream")
        assert client.get(csv["url"]).content == response.content
        original = client.get(detail["source"]["url"])
        assert hashlib.sha256(original.content).hexdigest() == detail["source"]["sha256"]
        assert client.get("/api/report-assets/" + "f" * 64).status_code == 404
        assert client.get("/api/reports/REPORT:missing").status_code == 404
        assert client.get("/reports/" + report["id"]).status_code == 200
        assert client.get("/api/genes/HGNC:1").json()["related"]["reports"]["total"] == 2
        assert client.get("/api/resources/modules/MODULE:test").json()["related"]["reports"]["total"] == 1
        assert client.post("/api/query", json={"collection": "reports"}).json()["total"] == 3


def test_report_formats_and_local_artifact_boundary(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("hello")
    (tmp_path / "image.png").write_bytes(b"png")
    (tmp_path / "outside").symlink_to("/etc/hosts")
    assets = attachments(
        path, {"artifacts": [{"path": "../outside"}, {"path": "outside"}]}, "![figure](image.png)"
    )
    assert assets == [("image.png", "attachment", tmp_path / "image.png")]
    metadata, body, prompt = split_report(
        "---\r\nprovider: openscientist\r\n---\r\n## Question\r\nA prompt\r\n## Output\r\n# Answer"
    )
    assert metadata == {"provider": "openscientist"}
    assert body == "# Answer" and "A prompt" in prompt
    assert split_report("# Plain report")[1:] == ("# Plain report", "")
    html, _ = render_markdown(
        "![figure](image.png)",
        [
            {
                "path": "image.png",
                "available": True,
                "media_type": "image/png",
                "url": "/api/report-assets/test",
            }
        ],
    )
    assert 'src="/api/report-assets/test"' in html


def test_echoed_prompt_separated_from_report():
    _, body, prompt = split_report(
        "## Question\nOriginal question\n## Output\nQuestion: repeated context\n# Gene Research for Functional Annotation\nInstructions\n## Research report: Pol5\nFindings"
    )
    assert body.startswith("## Research report: Pol5")
    assert "Instructions" in prompt and "Original question" in prompt
