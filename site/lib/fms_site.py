"""
FixMySlop on the web: scan a public GitHub repo by URL.

The repo is downloaded as one tarball from codeload.github.com (no git, no submodules, no LFS, no GitHub
API quota), extracted with strict limits into a temporary folder, scanned, and deleted. Nothing from the
repo is ever executed: FixMySlop only reads files.

Every route returns (status, headers, body) so the same code runs as a Vercel function and in dev.py.
"""
import html
import io
import json
import os
from http.server import BaseHTTPRequestHandler
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import quote

# Serverless functions can only write to /tmp; fixmyslop < 0.6.7 creates ~/.fixmyslop on first use.
if not os.access(Path.home(), os.W_OK):
    os.environ["HOME"] = tempfile.gettempdir()

from fixmyslop import __version__ as ENGINE_VERSION  # noqa: E402

MAX_DOWNLOAD = 60 * 1024 * 1024        # compressed tarball
MAX_EXTRACTED = 250 * 1024 * 1024      # total bytes written
MAX_FILES = 25_000
MAX_FILE = 2 * 1024 * 1024             # larger files are kept as empty placeholders (links still resolve)
DOWNLOAD_TIMEOUT = 25
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "vendor", ".venv", "venv",
             "__pycache__", "coverage", ".turbo", ".svelte-kit", ".output", ".vercel", "target"}

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)

SITE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
REPORT_CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ANALYTICS = '<script src="/_vercel/insights/script.js" defer></script>'   # Vercel Web Analytics: cookieless


class ScanError(Exception):
    """A failure the visitor should see, with an HTTP status."""

    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


# ─── input ──────────────────────────────────────────────────────────────────

def parse_repo(text: str) -> tuple[str, str]:
    """'owner/repo' or a github.com URL (any page inside the repo) -> (owner, repo)."""
    text = (text or "").strip()
    match = _URL.match(text)
    owner, repo = match.groups() if match else (text.split("/") + ["", ""])[:2]
    repo = repo[:-4] if repo.lower().endswith(".git") else repo
    if not (_OWNER.match(owner) and _REPO.match(repo)) or repo in (".", ".."):
        raise ScanError(400, "That doesn't look like a GitHub repository.",
                        "Paste a link like github.com/owner/repo, or just owner/repo.")
    return owner, repo


# ─── download and extract ───────────────────────────────────────────────────

def download(owner: str, repo: str) -> bytes:
    """The default branch as a .tar.gz, refusing anything over the size limit."""
    url = f"https://codeload.github.com/{quote(owner, safe='')}/{quote(repo, safe='')}/tar.gz/HEAD"
    if not url.startswith("https://codeload.github.com/"):
        raise ScanError(400, "That doesn't look like a GitHub repository.")
    request = urllib.request.Request(url, headers={"User-Agent": f"fixmyslop-site/{ENGINE_VERSION}"})
    try:
        # nosec B310: the scheme and host are fixed and checked above; no file:// or custom schemes
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # nosec B310
            length = int(response.headers.get("Content-Length") or 0)
            if length > MAX_DOWNLOAD:
                raise _too_large()
            data = response.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ScanError(404, f"{owner}/{repo} wasn't found.",
                            "Check the spelling. Private repositories can't be scanned here: run the CLI "
                            "locally instead.") from e
        raise ScanError(502, f"GitHub answered {e.code} for {owner}/{repo}.", "Try again in a minute.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ScanError(504, "Downloading the repository from GitHub took too long.",
                        "Very large repositories are best scanned locally with the CLI.") from e
    if len(data) > MAX_DOWNLOAD:
        raise _too_large()
    return data


def _too_large() -> ScanError:
    return ScanError(413, "This repository is too large to scan on the web.",
                     "Run it locally instead: pipx install fixmyslop, then fixmyslop scan in the repo.")


def extract(archive: bytes, dest: Path) -> str:
    """Safely unpack the tarball into dest; returns the commit SHA GitHub recorded (or '')."""
    files = written = declared = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            declared += max(member.size, 0)
            if declared > 4 * MAX_EXTRACTED:               # decompression bomb, even in skipped folders
                raise _too_large()
            if not member.isfile():
                continue                                   # no symlinks, devices or hard links
            parts = PurePosixPath(member.name).parts[1:]    # drop the "repo-<ref>/" top folder
            if not parts or any(p in ("..", "") or p.startswith("/") or ":" in p for p in parts):
                continue
            if any(p in SKIP_DIRS for p in parts[:-1]):
                continue
            files += 1
            if files > MAX_FILES:
                raise ScanError(413, "This repository has too many files to scan on the web.",
                                "Run it locally instead: pipx install fixmyslop, then fixmyslop scan.")
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.size > MAX_FILE:
                target.touch()                              # keep the path so links to it still resolve
                continue
            source = tar.extractfile(member)
            data = source.read() if source else b""
            written += len(data)
            if written > MAX_EXTRACTED:
                raise _too_large()
            target.write_bytes(data)
        commit = str(tar.pax_headers.get("comment", ""))      # git archive records the commit here
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else ""


# ─── scan ───────────────────────────────────────────────────────────────────

class Scan:
    """One scanned repository: the report, its engine, and where it came from."""

    def __init__(self, owner: str, repo: str, report, engine, commit: str, seconds: float):
        self.owner, self.repo, self.report, self.engine = owner, repo, report, engine
        self.commit, self.seconds = commit, seconds

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def scan_repo(owner: str, repo: str, keep=None):
    """Download, extract, scan. `keep(scan, folder)` runs while the files still exist (for exports)."""
    from fixmyslop.cli.cli_app import run_scan

    started = time.monotonic()
    archive = download(owner, repo)
    work = Path(tempfile.mkdtemp(prefix="fms-"))
    try:
        root = work / repo                                  # folder name = project name in the report
        root.mkdir()
        commit = extract(archive, root)
        del archive
        report, engine = run_scan([str(root)], use_static=False, min_severity="low", use_baseline=False)
        scan = Scan(owner, repo, report, engine, commit, time.monotonic() - started)
        return keep(scan, root) if keep else scan
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ─── responses ──────────────────────────────────────────────────────────────

CACHE = "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400"
EXPORTS = {"md": ("markdown", "text/markdown; charset=utf-8"), "json": ("json", "application/json"),
           "csv": ("csv", "text/csv; charset=utf-8"), "sarif": ("sarif", "application/sarif+json"),
           "pdf": ("pdf", "application/pdf")}


_RECENT: dict = {}          # (owner, repo, fmt) -> (time, response): a warm instance doesn't rescan
_RECENT_TTL, _RECENT_MAX = 600, 32


def _remember(key, make):
    now = time.monotonic()
    hit = _RECENT.get(key)
    if hit and now - hit[0] < _RECENT_TTL:
        return hit[1]
    value = make()
    if len(_RECENT) >= _RECENT_MAX:
        _RECENT.pop(min(_RECENT, key=lambda k: _RECENT[k][0]))
    _RECENT[key] = (now, value)
    return value


def report_response(query: dict):
    """GET /r/<owner>/<repo>[?format=pdf|md|json|csv|sarif] -> the report page or a download."""
    try:
        owner, repo = parse_repo(query.get("repo", ""))
        fmt = (query.get("format") or "html").lower()
        if fmt != "html" and fmt not in EXPORTS:
            raise ScanError(400, f"Unknown format: {fmt}", "Use pdf, md, json, csv or sarif.")
        if fmt == "html":
            return _remember((owner.lower(), repo.lower(), fmt), lambda: _html_report(scan_repo(owner, repo)))
        return _remember((owner.lower(), repo.lower(), fmt),
                         lambda: scan_repo(owner, repo, keep=lambda scan, root: _export(scan, fmt)))
    except ScanError as e:
        return error_page(e)


def _export(scan: Scan, fmt: str):
    from fixmyslop.core.report_builder import ReportBuilder, relative_path
    from fixmyslop.core.pdf_report import render_pdf

    name, mime = EXPORTS[fmt]
    if fmt == "pdf":
        body = render_pdf(scan.report, lambda p: relative_path(scan.report, p))
    else:
        body = ReportBuilder().export(scan.report, name).encode("utf-8")
    filename = f"fixmyslop-{scan.owner}-{scan.repo}.{fmt}"
    return 200, {**SITE_HEADERS, "Content-Type": mime, "Cache-Control": CACHE, "X-Robots-Tag": "noindex",
                 "Content-Disposition": f'attachment; filename="{filename}"'}, body


def _html_report(scan: Scan):
    from fixmyslop.core.html_report import render_html

    page = render_html(scan.report, scan.engine.relpath)
    page = page.replace("</head>", _BAR_STYLE + ANALYTICS + "</head>", 1).replace("<body>", "<body>" + _bar(scan), 1)
    return 200, {**SITE_HEADERS, "Content-Type": "text/html; charset=utf-8", "Cache-Control": CACHE,
                 "Content-Security-Policy": REPORT_CSP, "X-Robots-Tag": "noindex"}, page.encode("utf-8")


def _bar(scan: Scan) -> str:
    esc = html.escape
    slug = esc(scan.slug)
    commit = (f'<a href="https://github.com/{slug}/tree/{scan.commit}" rel="noopener">{scan.commit[:7]}</a>'
              if scan.commit else "default branch")
    badge = esc(f"[![FixMySlop grade](https://{{host}}/badge/{scan.slug})](https://{{host}}/r/{scan.slug})")
    exports = " ".join(f'<a class="site-link" href="?format={fmt}" download>{label}</a>'
                       for fmt, label in (("pdf", "PDF"), ("md", "Markdown"), ("json", "JSON"),
                                          ("csv", "CSV"), ("sarif", "SARIF")))
    return f"""
<div class="site-bar">
  <a class="site-home" href="/">FixMySlop</a>
  <span class="site-meta"><a href="https://github.com/{slug}" rel="noopener">{slug}</a> at {commit}
    · scanned in {scan.seconds:.1f}s · engine {esc(ENGINE_VERSION)}</span>
  <span class="site-actions">Download: {exports}
    <button class="site-link" type="button" id="badgeBtn" data-badge="{badge}">Copy badge</button>
    <a class="site-btn" href="/">Scan another repo</a></span>
</div>
<script>
document.getElementById("badgeBtn").addEventListener("click", (e) => {{
  const text = e.currentTarget.dataset.badge.replaceAll("{{host}}", location.host);
  navigator.clipboard.writeText(text).then(() => {{ e.currentTarget.textContent = "Badge copied"; }});
}});
</script>"""


_BAR_STYLE = """<style>
.site-bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 16px;
  border-bottom:1px solid var(--line);background:var(--surface);font-size:13px}
.site-home{font-weight:650;color:var(--fg);text-decoration:none;letter-spacing:-.01em}
.site-meta{color:var(--muted);overflow-wrap:anywhere}
.site-meta a{color:inherit}
.site-actions{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;color:var(--muted)}
.site-link{color:var(--fg);background:none;border:0;padding:0;font:inherit;cursor:pointer;text-decoration:underline;
  text-underline-offset:2px}
.site-btn{color:var(--bg);background:var(--fg);border-radius:6px;padding:5px 10px;text-decoration:none;font-weight:500}
@media print{.site-bar{display:none}}
</style>"""


def error_page(error: ScanError):
    esc = html.escape
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Can't scan this repository</title>
<style>
:root{{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6e6e69;--line:#e5e5e1}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111110;--fg:#ededea;--muted:#a1a19b;--line:#272725}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:560px;margin:15vh auto;padding:0 16px}}
h1{{font-size:20px;margin:0 0 8px}} p{{color:var(--muted);margin:0 0 20px}}
a{{color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 12px;text-decoration:none}}
</style></head>
<body><main><h1>{esc(error.message)}</h1><p>{esc(error.hint)}</p><a href="/">Back to FixMySlop</a></main></body></html>"""
    headers = {**SITE_HEADERS, "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
               "X-FixMySlop-Error": json.dumps({"message": error.message, "hint": error.hint})}
    return error.status, headers, page.encode("utf-8")


def serve(request: BaseHTTPRequestHandler, view) -> None:
    """Answer a GET with a view returning (status, headers, body). Used by the Vercel functions in api/.

    Vercel only recognises a file as a function when it contains a literal `class handler(...)`, so each
    api/*.py keeps that class and delegates here.
    """
    from urllib.parse import parse_qs, urlparse

    query = {k: v[0] for k, v in parse_qs(urlparse(request.path).query).items()}
    status, headers, body = view(query)
    request.send_response(status)
    for key, value in headers.items():
        request.send_header(key, value)
    request.send_header("Content-Length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


# ─── badge ──────────────────────────────────────────────────────────────────

_GRADE_COLORS = {"A": "#067647", "B": "#4d7c0f", "C": "#a15c07", "D": "#c4320a", "F": "#b42318"}


def badge_svg(label: str, value: str, color: str) -> bytes:
    def width(text: str) -> int:
        return int(len(text) * 6.6 + 12)
    left, right = width(label), width(value)
    total = left + right
    esc = html.escape
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{esc(label)}: {esc(value)}">
<title>{esc(label)}: {esc(value)}</title>
<clipPath id="r"><rect width="{total}" height="20" rx="3"/></clipPath>
<g clip-path="url(#r)"><rect width="{left}" height="20" fill="#3f3f46"/><rect x="{left}" width="{right}" height="20" fill="{color}"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">
<text x="{left / 2}" y="14">{esc(label)}</text><text x="{left + right / 2}" y="14">{esc(value)}</text></g>
</svg>"""
    return svg.encode("utf-8")


def badge_response(query: dict):
    """GET /badge/<owner>/<repo> -> 'slop | grade B · 21.5' as SVG (cached for hours at the edge)."""
    headers = {**SITE_HEADERS, "Content-Type": "image/svg+xml",
               "Cache-Control": "public, max-age=0, s-maxage=21600, stale-while-revalidate=86400"}
    try:
        owner, repo = parse_repo(query.get("repo", ""))
        scan = scan_repo(owner, repo)
        grade = scan.report.grade or "?"
        body = badge_svg("slop", f"grade {grade} · {scan.report.slop_score:g}", _GRADE_COLORS.get(grade, "#6b7280"))
        return 200, headers, body
    except ScanError as e:
        return 200, {**headers, "Cache-Control": "public, max-age=0, s-maxage=600"}, badge_svg(
            "slop", "not found" if e.status == 404 else "unavailable", "#6b7280")
