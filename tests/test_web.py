"""Tests for the local web app (`fixmyslop ui`): API behaviour and the HTTP security model."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from fixmyslop.web.server import UIApp, _Handler

SLOP = '<div className="bg-gradient-to-r from-purple-600 to-pink-500">\n  <a href="#">x</a>\n</div>\n'


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "18"}}', encoding="utf-8")
    (tmp_path / "Hero.tsx").write_text(SLOP, encoding="utf-8")
    (tmp_path / "util.py").write_text("# Initialize the items list\nitems = []\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(project):
    app = UIApp(project)
    handler = type("Handler", (_Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(url, token=None, body=None, host=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-FixMySlop-Token"] = token
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, res.read().decode("utf-8"), dict(res.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def test_page_embeds_token_and_strict_headers(server):
    app, base = server
    status, body, headers = call(base + "/")
    assert status == 200 and app.token in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert "https://" not in body.replace("https://github.com/MrSpideyNihal/FixMySlop", "")  # no CDNs


def test_api_requires_token(server):
    _, base = server
    assert call(base + "/api/state")[0] == 403
    assert call(base + "/api/scan", body={})[0] == 403


def test_rejects_foreign_host_header(server):
    """DNS-rebinding protection: a page on evil.example resolving to 127.0.0.1 must be refused."""
    app, base = server
    assert call(base + "/", host="evil.example")[0] == 403
    assert call(base + "/api/state", token=app.token, host="evil.example")[0] == 403


def test_scan_fix_and_brief_flow(server, project):
    app, base = server
    status, body, _ = call(base + "/api/scan", token=app.token, body={"static": False})
    report = json.loads(body)
    assert status == 200 and report["grade"] in "ABCDF"
    rules = {i["rule"] for i in report["issues"]}
    assert {"ui/ai-gradient", "a11y/dead-link", "slop/narrating-comment"} <= rules
    assert all(not Path(i["file"]).is_absolute() for i in report["issues"])

    _, body, _ = call(base + "/api/brief?file=Hero.tsx", token=app.token)
    assert "Hero.tsx" in json.loads(body)["text"] and "util.py" not in json.loads(body)["text"]

    _, body, _ = call(base + "/api/fix-safe", token=app.token, body={})
    result = json.loads(body)
    assert result["applied"] == 1
    assert "Initialize" not in (project / "util.py").read_text(encoding="utf-8")
    assert result["report"]["previous"] is not None          # history drives the score delta


def test_snippet_is_confined_to_project(server):
    app, base = server
    status, body, _ = call(base + "/api/snippet?file=Hero.tsx&line=2", token=app.token)
    assert status == 200 and any("href" in line for line in json.loads(body)["lines"])
    assert call(base + "/api/snippet?file=../../etc/passwd&line=1", token=app.token)[0] == 400


def test_rule_toggle_writes_project_config(server, project):
    app, base = server
    call(base + "/api/rule", token=app.token, body={"id": "ui/ai-gradient", "enabled": False})
    assert "ui/ai-gradient" in (project / ".fixmyslop.yml").read_text(encoding="utf-8")
    _, body, _ = call(base + "/api/scan", token=app.token, body={"static": False})
    assert "ui/ai-gradient" not in {i["rule"] for i in json.loads(body)["issues"]}
    call(base + "/api/rule", token=app.token, body={"id": "ui/ai-gradient", "enabled": True})
    _, body, _ = call(base + "/api/rules", token=app.token)
    assert next(r for r in json.loads(body)["rules"] if r["id"] == "ui/ai-gradient")["enabled"] is True


def test_install_integration_and_status(server, project):
    app, base = server
    _, body, _ = call(base + "/api/integration", token=app.token, body={"id": "claude"})
    status = {i["id"]: i["installed"] for i in json.loads(body)["integrations"]}
    assert status["claude"] is True and status["cursor"] is False
    assert (project / ".claude" / "skills" / "fixmyslop" / "SKILL.md").exists()


def test_switch_project_validates_path(server, tmp_path):
    app, base = server
    assert call(base + "/api/project", token=app.token, body={"path": str(tmp_path / "nope")})[0] == 400


def test_snippet_redacts_secrets(server, project):
    app, base = server
    (project / "config.py").write_text('KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    _, body, _ = call(base + "/api/snippet?file=config.py&line=1", token=app.token)
    assert "abcdefghijklmnop" not in body and "redacted" in body


def test_baseline_from_the_app(server, project):
    app, base = server
    _, body, _ = call(base + "/api/baseline", token=app.token, body={})
    report = json.loads(body)
    assert report["issues"] == [] and report["baselined"] > 0
    assert json.loads(call(base + "/api/state", token=app.token)[1])["baseline"] is True
    _, body, _ = call(base + "/api/baseline", token=app.token, body={"clear": True})
    assert json.loads(body)["issues"] and not (project / ".fixmyslop-baseline.json").exists()


def test_snippet_works_for_a_subfolder_of_a_git_repo(tmp_path):
    """Monorepos: open apps/web inside a repo; issue paths are repo-relative, previews must still resolve."""
    import subprocess
    repo = tmp_path / "monorepo"
    app_dir = repo / "apps" / "web"
    app_dir.mkdir(parents=True)
    (app_dir / "Hero.tsx").write_text(SLOP, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    ui = UIApp(app_dir)
    report = ui.post("/api/scan", {"static": False})
    issue = next(i for i in report["issues"] if i["rule"] == "ui/ai-gradient")
    assert issue["file"] == "apps/web/Hero.tsx"
    snippet = ui.get("/api/snippet", {"file": issue["file"], "line": str(issue["line"])})
    assert any("from-purple-600" in line for line in snippet["lines"])


def test_export_every_format_from_the_app(server):
    import base64
    app, base = server
    assert call(base + "/api/export?fmt=pdf", token=app.token)[0] == 400          # scan first
    call(base + "/api/scan", token=app.token, body={"static": False})
    for fmt, marker in (("pdf", b"%PDF-1.4"), ("md", b"# FixMySlop report"), ("html", b"<!DOCTYPE html>"),
                        ("csv", b"Severity,"), ("json", b'"issues"'), ("sarif", b'"version": "2.1.0"')):
        status, body, _ = call(base + f"/api/export?fmt={fmt}", token=app.token)
        file = json.loads(body)
        assert status == 200 and file["name"].endswith("." + fmt)
        assert marker in base64.b64decode(file["base64"]), fmt
    assert call(base + "/api/export?fmt=exe", token=app.token)[0] == 400
    assert call(base + "/api/export?fmt=pdf")[0] == 403                           # token required
