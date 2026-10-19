"""The web scanner (site/): input parsing, safe extraction, report, exports and badge. No network."""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site" / "lib"))
import fms_site  # noqa: E402

COMMIT = "298a8857c7128a0d121e7f699dfd729f23b3966d"
SLOP_PAGE = '<div className="bg-gradient-to-r from-purple-600 to-pink-500">\n  <a href="#">Start</a>\n</div>\n'


def tarball(files: dict, extra=None) -> bytes:
    """A GitHub-style archive: one top folder, the commit in the pax global header."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": COMMIT}) as tar:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"demo-{COMMIT}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return buffer.getvalue()


@pytest.mark.parametrize("text, expected", [
    ("shadcn-ui/taxonomy", ("shadcn-ui", "taxonomy")),
    ("https://github.com/vercel/commerce", ("vercel", "commerce")),
    ("github.com/vercel/commerce/tree/main/app?x=1", ("vercel", "commerce")),
    ("  https://www.github.com/a-b/c.d_e.git  ", ("a-b", "c.d_e")),
])
def test_parse_repo_accepts_urls_and_slugs(text, expected):
    assert fms_site.parse_repo(text) == expected


@pytest.mark.parametrize("text", ["", "nope", "../etc", "a/..", "https://gitlab.com/a/b", "a b/c", "-bad/x"])
def test_parse_repo_rejects_everything_else(text):
    with pytest.raises(fms_site.ScanError) as error:
        fms_site.parse_repo(text)
    assert error.value.status == 400


def test_extract_is_confined_and_skips_the_dangerous_bits(tmp_path):
    link = tarfile.TarInfo(f"demo-{COMMIT}/evil-link")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    escape = tarfile.TarInfo(f"demo-{COMMIT}/../../escaped.txt")
    escape.size = 1
    big = tarfile.TarInfo(f"demo-{COMMIT}/assets/video.mp4")
    big.size = fms_site.MAX_FILE + 1
    archive = tarball({"src/App.tsx": SLOP_PAGE, "node_modules/x/index.js": "x"}, extra=[link, escape, big])

    commit = fms_site.extract(archive, tmp_path)

    assert commit == COMMIT
    assert (tmp_path / "src/App.tsx").read_text() == SLOP_PAGE
    assert not (tmp_path / "evil-link").exists()                 # no symlinks
    assert not (tmp_path.parent / "escaped.txt").exists()        # no path traversal
    assert not (tmp_path / "node_modules").exists()              # dependencies skipped
    assert (tmp_path / "assets/video.mp4").stat().st_size == 0   # big files kept as placeholders


def test_extract_refuses_decompression_bombs(tmp_path, monkeypatch):
    monkeypatch.setattr(fms_site, "MAX_EXTRACTED", 1000)
    archive = tarball({f"f{i}.txt": "a" * 600 for i in range(10)})
    with pytest.raises(fms_site.ScanError) as error:
        fms_site.extract(archive, tmp_path)
    assert error.value.status == 413


@pytest.fixture()
def fake_github(monkeypatch):
    files = {"package.json": '{"dependencies": {"react": "18"}}', "src/Hero.tsx": SLOP_PAGE,
             "README.md": "# Demo\n\n```bash\nnpm run test\n```\n"}
    monkeypatch.setattr(fms_site, "download", lambda owner, repo: tarball(files))
    fms_site._RECENT.clear()


def test_report_page(fake_github):
    status, headers, body = fms_site.report_response({"repo": "acme/demo"})
    page = body.decode()
    assert status == 200 and headers["X-Robots-Tag"] == "noindex"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "ui/ai-gradient" in page and "docs/missing-script" in page
    assert "acme/demo" in page and COMMIT[:7] in page              # the site bar
    assert "fms-" not in page                                      # no temp paths leak into the report
    assert "/_vercel/insights/script.js" in page                   # cookieless page-view analytics


@pytest.mark.parametrize("fmt, marker", [("pdf", b"%PDF-1.4"), ("md", b"# FixMySlop report"),
                                         ("json", b'"issues"'), ("csv", b"Severity,"), ("sarif", b'"2.1.0"')])
def test_exports(fake_github, fmt, marker):
    status, headers, body = fms_site.report_response({"repo": "acme/demo", "format": fmt})
    assert status == 200 and marker in body
    assert headers["Content-Disposition"].endswith(f'.{fmt}"')


def test_errors_are_pages_with_a_machine_readable_header(monkeypatch):
    def missing(owner, repo):
        raise fms_site.ScanError(404, f"{owner}/{repo} wasn't found.", "Check the spelling.")
    monkeypatch.setattr(fms_site, "download", missing)
    fms_site._RECENT.clear()
    status, headers, body = fms_site.report_response({"repo": "acme/nothing"})
    assert status == 404 and b"acme/nothing wasn&#x27;t found" in body
    assert json.loads(headers["X-FixMySlop-Error"])["hint"] == "Check the spelling."
    assert fms_site.report_response({"repo": "acme/demo", "format": "exe"})[0] == 400


def test_badge(fake_github):
    status, headers, body = fms_site.badge_response({"repo": "acme/demo"})
    assert status == 200 and headers["Content-Type"] == "image/svg+xml"
    assert b"slop" in body and b"grade " in body


def test_vercel_can_detect_every_function():
    """Vercel only treats api/*.py as functions when the file has a literal `class handler(...)`."""
    api = Path(__file__).resolve().parent.parent / "site" / "api"
    for path in api.glob("*.py"):
        assert "class handler(BaseHTTPRequestHandler)" in path.read_text(encoding="utf-8"), path.name
