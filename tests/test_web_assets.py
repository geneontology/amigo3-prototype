import re

from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.web_assets import WEB_DIRECTORY, BrowserFiles


def test_all_browser_pages_version_assets_and_revalidate(built_data):
    with TestClient(create_app(built_data, "duckdb")) as client:
        for path in (
            "/",
            "/index.html",
            "/genes/test",
            "/terms/test",
            "/reports/test",
            "/projects/test",
            "/chat",
            "/enrichment",
            "/help/network-enrichment",
        ):
            response = client.get(path)
            assert response.status_code == 200
            if path == "/chat":
                assert response.text.count('href="/predictions"') == 1
                nav = re.search(r'<nav class="site-nav"[^>]*>(.*?)</nav>', response.text, re.DOTALL)
                assert nav and 'href="/predictions"' in nav[1]
                assert '<a href="/api/manifest">View data provenance →</a>' in response.text
            assert response.headers["cache-control"] == "no-cache"
            assets = re.findall(r'(?:src|href)="(/[^"?]+\.(?:js|css)\?v=[a-f0-9]{16})"', response.text)
            assert assets, path
            assert not re.search(r'(?:src|href)="/[^"?]+\.(?:js|css)"', response.text)
            for asset in assets:
                assert client.get(asset).status_code == 200
        index = WEB_DIRECTORY / "index.html"
        old_etag = FileResponse(index, stat_result=index.stat()).headers["etag"]
        response = client.get("/", headers={"If-None-Match": old_etag})
        assert response.status_code == 200  # Must replace cached pre-versioning HTML.
        assert "/presentation.js?v=" in response.text
        response = client.get("/presentation.js")
        assert response.headers["cache-control"] == "no-cache"
        conditional = client.get("/presentation.js", headers={"If-None-Match": response.headers["etag"]})
        assert conditional.status_code == 304
        assert conditional.headers["cache-control"] == "no-cache"


def test_version_changes_with_content_even_if_timestamp_and_size_do_not(tmp_path):
    import os

    script = tmp_path / "app.js"
    script.write_text("version=1;")
    page = tmp_path / "index.html"
    page.write_text('<script src="/app.js"></script>')
    files = BrowserFiles(tmp_path)
    first = files.page(page).body
    stat = script.stat()
    script.write_text("version=2;")
    os.utime(script, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert files.page(page).body != first
