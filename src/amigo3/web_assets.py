"""Serve browser pages with content-versioned scripts and stylesheets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

WEB_DIRECTORY = Path(__file__).parent / "web"
ASSET_REFERENCE = re.compile(r'((?:src|href)=")(/[^"?]+\.(?:js|css))(")')


class BrowserFiles(StaticFiles):
    def __init__(self, directory: Path = WEB_DIRECTORY):
        super().__init__(directory=directory, html=True)
        self.root = directory.resolve()

    def page(self, path: Path):
        def version(match):
            asset = (self.root / match[2].lstrip("/")).resolve()
            asset.relative_to(self.root)
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
            return f"{match[1]}{match[2]}?v={digest}{match[3]}"

        content = path.read_text()
        if self.root == WEB_DIRECTORY.resolve():
            if 'href="/predictions"' not in content:
                content = content.replace(
                    '<a href="/api/manifest">Data sources</a>',
                    '<a href="/predictions">Predictions</a><a href="/api/manifest">Data sources</a>',
                    1,
                )
            content = content.replace("</head>", '<script src="/auth.js" defer></script></head>')
        return HTMLResponse(
            ASSET_REFERENCE.sub(version, content),
            headers={"Cache-Control": "no-cache"},
        )

    def is_not_modified(self, response_headers, request_headers):
        # A browser may still hold a pre-versioning HTML ETag. Even unchanged
        # HTML source must be rendered again when one of its assets changes.
        if response_headers.get("content-type", "").startswith("text/html"):
            return False
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, FileResponse) and response.media_type == "text/html":
            return self.page(Path(response.path))
        # Unversioned URLs remain compatible, but must not stay heuristically fresh.
        if Path(path).suffix in {".js", ".css"}:
            response.headers["Cache-Control"] = "no-cache"
        return response
