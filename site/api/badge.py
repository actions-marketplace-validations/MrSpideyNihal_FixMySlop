"""Vercel function: GET /badge/<owner>/<repo> (rewritten to /api/badge?repo=owner/repo)."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from fms_site import badge_response, serve  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801  (Vercel detects this exact statement)
    def do_GET(self):
        serve(self, badge_response)
