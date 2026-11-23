"""Web-panel static asset boundary (Issue #314).

Exact allowlist only. The HTTP composition root checks an administrator
session BEFORE delegating to this function; no Worker token can authorize
the panel or its resources.
"""
from pathlib import Path

ASSETS = {
    "/assets/arena.css": ("web/css/arena.css", "text/css; charset=utf-8"),
    "/assets/arena.js": ("web/js/arena.js", "text/javascript; charset=utf-8"),
    "/assets/navigation.js": ("web/js/navigation.js", "text/javascript; charset=utf-8"),
    "/assets/navigation.css": ("web/css/navigation.css", "text/css; charset=utf-8"),
}


def serve_panel_asset(handler, url_path, root):
    """Serve an explicitly configured path; never derive a file from the URL."""
    entry = ASSETS.get(url_path)
    if entry is None:
        handler.send_error(404)
        return
    filename, content_type = entry
    body = (Path(root) / filename).read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)
