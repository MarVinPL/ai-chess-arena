"""Regression: authenticated Worker metrics page emits a single HTTP response."""
from pathlib import Path
from unittest.mock import Mock

import server


class PageHandler:
    path = "/remote-workers"

    def __init__(self, logged_in):
        self.logged_in = logged_in
        self.responses = []

    def authenticated_user(self):
        return "admin" if self.logged_in else None

    def send_html_file(self, filename):
        self.responses.append(("html", filename))

    def send_json(self, body, status=200, *_):
        self.responses.append(("json", status, body))

    def send_error(self, status):
        self.responses.append(("error", status))


def test_remote_worker_page_returns_exactly_one_html_response():
    handler = PageHandler(logged_in=True)
    server.Handler.do_GET(handler)
    assert handler.responses == [("html", "remote_workers.html")]


def test_remote_worker_page_requires_browser_session():
    handler = PageHandler(logged_in=False)
    server.Handler.do_GET(handler)
    assert handler.responses == [(
        "json", 401, {"error": "Wymagane logowanie", "login_url": "/login"},
    )]


def test_remote_worker_page_composition_preserves_auth_before_render():
    source = Path(server.__file__).read_text(encoding="utf-8")
    get = source.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index("if path == '/remote-workers':")
    assert "self.send_html_file('remote_workers.html')\n            return" in get
