"""Run the site locally the way Vercel serves it: python site/dev.py [port]

Static files from public/ (with the headers from vercel.json), /r/<owner>/<repo> and /badge/<owner>/<repo>
rewritten to the same Python handlers the Vercel functions use.
"""
import json
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE / "lib"))
from fms_site import badge_response, report_response  # noqa: E402

CONFIG = json.loads((SITE / "vercel.json").read_text(encoding="utf-8"))
STATIC_HEADERS = {h["key"]: h["value"] for rule in CONFIG["headers"] for h in rule["headers"]}
ROUTES = [(re.compile(r"^/r/([^/]+)/([^/]+)$"), report_response), (re.compile(r"^/badge/([^/]+)/([^/]+)$"), badge_response)]


class Dev(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        for pattern, view in ROUTES:
            match = pattern.match(url.path)
            if match:
                return self._reply(*view({**query, "repo": f"{match[1]}/{match[2]}"}))
        name = url.path.strip("/") or "index.html"
        path = (SITE / "public" / name).resolve()
        if not path.suffix and path.with_suffix(".html").is_file():
            path = path.with_suffix(".html")                         # cleanUrls
        if SITE / "public" not in path.parents or not path.is_file():
            return self._reply(404, {"Content-Type": "text/plain"}, b"not found")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._reply(200, {**STATIC_HEADERS, "Content-Type": mime}, path.read_bytes())

    def _reply(self, status, headers, body):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    print(f"FixMySlop site on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
