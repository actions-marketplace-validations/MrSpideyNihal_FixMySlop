"""
`fixmyslop ui` — a local web app on top of the same pipeline as `fixmyslop scan`.

Security model (it's a local dev tool, but it can read and write project files):
- binds to 127.0.0.1 only
- rejects requests whose Host header isn't localhost (DNS-rebinding protection)
- every /api call needs a per-process random token that is only embedded in the page
- file reads and writes are confined to the selected project root
- no external network requests; the page is a single self-contained file
"""
import json
import secrets
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fixmyslop.macros import APP_VERSION, CONFIG_DIR, PROJECT_CONFIG_FILE, SLOP_TOOL
from fixmyslop.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PORT = 7777
MAX_BODY = 64 * 1024
_RECENTS_FILE = CONFIG_DIR / "ui.json"
_GROUPS = {
    "ui": "UI slop", "a11y": "Accessibility", "slop": "Code slop", "sec": "Security", "deps": "Dependencies",
}


# ─── state ───────────────────────────────────────────────────────────────────

class UIState:
    """What the app knows between requests."""

    def __init__(self, root: Optional[Path]):
        self.root: Optional[Path] = root
        self.report_payload: Optional[dict] = None
        self.last = None                      # (report, engine) of the latest scan
        self.lock = threading.Lock()

    # recents ---------------------------------------------------------------
    @staticmethod
    def recents() -> list[str]:
        try:
            data = json.loads(_RECENTS_FILE.read_text(encoding="utf-8"))
            return [p for p in data.get("recent", []) if Path(p).is_dir()][:8]
        except (OSError, ValueError):
            return []

    @staticmethod
    def remember(path: Path):
        recent = [str(path)] + [p for p in UIState.recents() if p != str(path)]
        try:
            _RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _RECENTS_FILE.write_text(json.dumps({"recent": recent[:8]}, indent=2), encoding="utf-8")
        except OSError as e:
            logger.debug("Could not save recents: %s", e)


# ─── payload builders ────────────────────────────────────────────────────────

def _rule_meta() -> dict:
    from fixmyslop.core.slop import RULES
    from fixmyslop.core.slop.fixers import FIXERS
    return {
        rule.id: {"title": rule.title, "why": rule.why, "fix": rule.fix, "severity": rule.severity,
                  "category": rule.category, "group": rule.id.split("/")[0], "autofix": rule.id in FIXERS}
        for rule in RULES.values()
    }


def _history(root: Path, entry: Optional[dict] = None) -> list[dict]:
    """Read (and optionally append to) the project's scan history."""
    from fixmyslop.core.agents import work_dir
    path = work_dir(root) / "history.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    if entry is not None:
        history = (history + [entry])[-50:]
        path.write_text(json.dumps(history), encoding="utf-8")
    return history


def report_payload(report, engine) -> dict:
    """Everything the UI needs about one scan, with project-relative paths."""
    from fixmyslop.core.report_builder import relative_path
    from fixmyslop.core.slop.fixers import FIXERS
    from fixmyslop.macros import SEVERITY_RANK

    meta = _rule_meta()
    issues = []
    for index, issue in enumerate(sorted(report.issues, key=lambda i: (
            SEVERITY_RANK.get(i.severity, 9), relative_path(report, i.file_path), i.line_start))):
        detail, why = issue.description, ""
        if issue.source_tool == SLOP_TOOL and issue.rule_id in meta:
            why = meta[issue.rule_id]["why"]
            detail = issue.description[: -len(why)].rstrip(" .") if issue.description.endswith(why) else ""
        issues.append({
            "id": index,
            "file": relative_path(report, issue.file_path),
            "line": issue.line_start,
            "severity": issue.severity,
            "rule": issue.rule_id or issue.source_tool,
            "tool": issue.source_tool,
            "category": issue.category,
            "title": issue.title,
            "detail": detail,
            "why": why,
            "evidence": issue.evidence,
            "fix": issue.suggested_fix,
            "autofix": issue.rule_id in FIXERS,
        })
    root = Path(report.repo_path)
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "score": report.slop_score,
             "grade": report.grade, "issues": len(report.issues)}
    history = _history(root, entry)
    return {
        "project": {"root": str(root), "name": root.name},
        "score": report.slop_score,
        "grade": report.grade,
        "files": report.scanned_files,
        "lines": report.lines_scanned,
        "seconds": round(report.scan_time_s, 2),
        "baselined": report.baselined,
        "scannedAt": entry["at"],
        "previous": history[-2] if len(history) > 1 else None,
        "designContext": engine.theme.design_context(),
        "issues": issues,
    }


def integrations_status(root: Path) -> list[dict]:
    """Which agent/CI integrations are installed in the project."""
    from fixmyslop.core.slop.brief import GUIDE_START

    def has(rel: str, needle: str = "") -> bool:
        path = root / rel
        if not path.is_file():
            return False
        if not needle:
            return True
        try:
            return needle in path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False

    return [
        {"id": "claude", "name": "Claude Code",
         "what": "Skill + MCP tool + a hook that checks every file Claude edits + rules in CLAUDE.md",
         "installed": has(".claude/settings.json", "fixmyslop hook") and has(".claude/skills/fixmyslop/SKILL.md")},
        {"id": "cursor", "name": "Cursor", "what": "Project rule + MCP tool",
         "installed": has(".cursor/rules/fixmyslop.mdc")},
        {"id": "agents", "name": "Codex, Gemini, Jules & others", "what": "Anti-slop rules in AGENTS.md",
         "installed": has("AGENTS.md", GUIDE_START)},
        {"id": "github", "name": "GitHub Actions", "what": "Annotate pull requests and fail on HIGH issues",
         "installed": has(".github/workflows/fixmyslop.yml")},
        {"id": "pre-commit", "name": "pre-commit", "what": "Block slop before it's committed",
         "installed": has(".pre-commit-config.yaml", "fixmyslop")},
    ]


def rules_payload(root: Optional[Path]) -> dict:
    from fixmyslop.core.slop.config import load_config
    config = load_config(root) if root else None
    rules = []
    for rule_id, meta in sorted(_rule_meta().items(), key=lambda kv: (list(_GROUPS).index(kv[1]["group"])
                                                                       if kv[1]["group"] in _GROUPS else 9, kv[0])):
        rules.append({"id": rule_id, **meta, "enabled": config.rule_enabled(rule_id) if config else True})
    return {"groups": _GROUPS, "rules": rules, "configFile": PROJECT_CONFIG_FILE}


def set_rule_enabled(root: Path, rule_id: str, enabled: bool) -> list[str]:
    """Update the project's .fixmyslop.yml `disable` list; returns the new list."""
    import yaml
    from fixmyslop.core.slop.config import DEFAULT_CONFIG_YAML
    path = root / PROJECT_CONFIG_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, yaml.YAMLError):
        data = None
    if not isinstance(data, dict):
        data = yaml.safe_load(DEFAULT_CONFIG_YAML) or {}
    disabled = [d for d in (data.get("disable") or []) if d != rule_id]
    if not enabled:
        disabled.append(rule_id)
    data["disable"] = sorted(set(disabled))
    path.write_text("# FixMySlop configuration — edited by `fixmyslop ui`\n"
                    + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return data["disable"]


def read_snippet(root: Path, rel: str, line: int, radius: int = 6) -> dict:
    """Lines around `line` of a file inside the project root."""
    from fixmyslop.utils.file_utils import read_file_safe
    path = (root / rel).resolve()
    path.relative_to(root.resolve())          # raises ValueError outside the project
    text = read_file_safe(path) or ""
    lines = text.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    from fixmyslop.core.slop.rules_security import redact_secrets
    # Same promise as every report: provider keys never appear in full, even from your own files.
    return {"file": rel, "start": start, "line": line, "lines": [redact_secrets(x) for x in lines[start - 1:end]]}


# ─── HTTP ────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "FixMySlop"
    app: "UIApp"

    def log_message(self, fmt, *args):  # quiet by default
        logger.debug("ui %s", fmt % args)

    # helpers -----------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                         "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200):
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-FixMySlop-Token", ""), self.app.token)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}

    # routing -----------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, self.app.page(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        if not self._authorized():
            return self._error(HTTPStatus.FORBIDDEN, "missing token")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            self._json(self.app.get(url.path, query))
        except FileNotFoundError as e:
            self._error(HTTPStatus.NOT_FOUND, str(e))
        except ValueError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        url = urlparse(self.path)
        if not url.path.startswith("/api/") or not self._authorized():
            return self._error(HTTPStatus.FORBIDDEN, "missing token")
        try:
            self._json(self.app.post(url.path, self._body()))
        except FileNotFoundError as e:
            self._error(HTTPStatus.NOT_FOUND, str(e))
        except ValueError as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # surface failures in the UI instead of a dropped connection
            logger.exception("UI request failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")


class UIApp:
    """API implementation (kept separate from HTTP so it can be tested directly)."""

    def __init__(self, root: Optional[Path] = None):
        self.state = UIState(root.resolve() if root else None)
        self.token = secrets.token_urlsafe(32)
        from fixmyslop.utils import update_check
        self._update = update_check.start()                    # PyPI check in the background, once a day

    def page(self) -> bytes:
        html = resources.files("fixmyslop.web").joinpath("app.html").read_text(encoding="utf-8")
        boot = json.dumps(self.get("/api/state", {})).replace("</", "<\\/")   # first paint without a round trip
        return (html.replace("__TOKEN__", self.token).replace("__VERSION__", APP_VERSION)
                .replace("__BOOT__", boot)).encode("utf-8")

    def _root(self) -> Path:
        if self.state.root is None:
            raise ValueError("Choose a project folder first")
        return self.state.root

    # GET ----------------------------------------------------------------------
    def get(self, path: str, query: dict) -> dict:
        if path == "/api/state":
            root = self.state.root
            return {
                "version": APP_VERSION,
                "project": {"root": str(root), "name": root.name} if root else None,
                "recent": self.state.recents(),
                "report": self.state.report_payload,
                "integrations": integrations_status(root) if root else [],
                "baseline": bool(root and (root / ".fixmyslop-baseline.json").is_file()),
            }
        if path == "/api/rules":
            return rules_payload(self.state.root)
        if path == "/api/snippet":
            # Issue paths are relative to the scan root, which is the enclosing git repo when the opened
            # folder is a subfolder of one (monorepos) — resolve against that, not the opened folder.
            payload = self.state.report_payload
            base = Path(payload["project"]["root"]) if payload else self._root()
            return read_snippet(base, query.get("file", ""), int(query.get("line") or 1))
        if path == "/api/brief":
            return {"text": self._brief(query.get("file") or None)}
        if path == "/api/update":
            from fixmyslop.utils.update_check import UPGRADE_COMMAND
            return {"latest": self._update(2.0), "current": APP_VERSION, "command": UPGRADE_COMMAND}
        if path == "/api/export":
            return self._export(query.get("fmt", ""))
        raise FileNotFoundError(path)

    _EXPORTS = {"pdf": "application/pdf", "md": "text/markdown", "html": "text/html", "csv": "text/csv",
                "json": "application/json", "sarif": "application/sarif+json"}

    def _export(self, fmt: str) -> dict:
        """The latest scan as a downloadable file (the page turns it into a download)."""
        import base64
        import tempfile
        from fixmyslop.core.report_builder import ReportBuilder
        if fmt not in self._EXPORTS:
            raise ValueError(f"Unknown export format: {fmt}")
        if not self.state.last:
            raise ValueError("Scan the project first")
        report, _ = self.state.last
        builder = ReportBuilder()
        if fmt == "pdf":
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "report.pdf"
                builder.export_pdf(report, str(out))
                data = out.read_bytes()
        else:
            data = builder.export(report, {"md": "markdown"}.get(fmt, fmt)).encode("utf-8")
        name = f"fixmyslop-{Path(report.repo_path).name or 'report'}.{fmt}"
        return {"name": name, "mime": self._EXPORTS[fmt], "base64": base64.b64encode(data).decode("ascii")}

    # POST ---------------------------------------------------------------------
    def post(self, path: str, body: dict) -> dict:
        if path == "/api/project":
            folder = Path(str(body.get("path", "")).strip().strip('"')).expanduser()
            if not folder.is_dir():
                raise ValueError(f"Not a folder: {folder}")
            self.state.root = folder.resolve()
            self.state.report_payload = None
            self.state.remember(self.state.root)
            return self.get("/api/state", {})
        if path == "/api/scan":
            return self._scan(static=bool(body.get("static", True)), changed=bool(body.get("changed")))
        if path == "/api/fix-safe":
            return self._fix_safe()
        if path == "/api/rule":
            disabled = set_rule_enabled(self._root(), str(body["id"]), bool(body.get("enabled")))
            return {"disable": disabled}
        if path == "/api/integration":
            return self._install(str(body.get("id", "")))
        if path == "/api/baseline":
            return self._baseline(clear=bool(body.get("clear")))
        raise FileNotFoundError(path)

    # actions ------------------------------------------------------------------
    def _scan(self, static: bool = True, changed: bool = False) -> dict:
        from fixmyslop.cli.cli_app import run_scan
        root = self._root()
        with self.state.lock:
            report, engine = run_scan([str(root)], use_static=static, changed=changed)
            self.state.report_payload = report_payload(report, engine)
            self.state.last = (report, engine)
        return self.state.report_payload

    def _fix_safe(self) -> dict:
        from fixmyslop.cli.cli_app import run_scan
        from fixmyslop.core.slop.fixers import apply_safe_fixes
        root = self._root()
        with self.state.lock:
            report, _ = run_scan([str(root)])
            before = report.slop_score
            applied = apply_safe_fixes(report.issues)
        result = self._scan()
        return {"applied": sum(applied.values()), "files": len(applied), "before": before, "report": result}

    def _baseline(self, clear: bool = False) -> dict:
        from fixmyslop.cli.cli_app import run_scan
        from fixmyslop.core.slop.baseline import BASELINE_FILE, write_baseline
        root = self._root()
        if clear:
            (root / BASELINE_FILE).unlink(missing_ok=True)
        else:
            with self.state.lock:
                report, engine = run_scan([str(root)], min_severity="info", use_baseline=False)
                write_baseline(root, report.issues, engine.relpath)
        return self._scan()

    def _brief(self, rel_file: Optional[str]) -> str:
        from fixmyslop.cli.cli_app import run_scan
        from fixmyslop.core.report_builder import relative_path
        from fixmyslop.core.slop.brief import build_fix_brief
        report, engine = self.state.last or run_scan([str(self._root())])
        issues = report.issues
        if rel_file:
            issues = [i for i in issues if relative_path(report, i.file_path) == rel_file]
        return build_fix_brief(issues, lambda p: relative_path(report, p), targets=rel_file or ".",
                               design_context=engine.theme.design_context())

    def _install(self, target: str) -> dict:
        from fixmyslop.core import integrations as integ
        installers = {
            "claude": integ.install_claude, "cursor": integ.install_cursor, "agents": integ.install_agents_md,
            "github": integ.install_github, "pre-commit": integ.install_pre_commit,
        }
        if target not in installers:
            raise ValueError(f"Unknown integration: {target}")
        root = self._root()
        messages = [integ.write_config(root)] + installers[target](root)
        return {"messages": messages, "integrations": integrations_status(root)}


def serve(root: Optional[Path], port: int = DEFAULT_PORT, open_browser: bool = True):
    """Run the UI until Ctrl-C."""
    app = UIApp(root)
    handler = type("Handler", (_Handler,), {"app": app})
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)   # port busy: pick a free one
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    print(f"FixMySlop UI running at {url}  (Ctrl-C to stop)", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
