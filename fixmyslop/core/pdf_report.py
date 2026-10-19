"""
PDF export with no dependencies.

A small PDF 1.4 writer (standard Helvetica/Courier fonts, WinAnsi text, A4 pages) and the report
layout: score and summary first, then findings grouped by file, then how to fix each rule.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from fixmyslop.core.issue import ScanReport
from fixmyslop.macros import APP_NAME, APP_VERSION, SEVERITY_RANK

PAGE_W, PAGE_H = 595.28, 841.89      # A4 in points
MARGIN = 48
WIDTH = PAGE_W - 2 * MARGIN

_SEVERITY_RGB = {
    "CRITICAL": (0.71, 0.14, 0.09), "HIGH": (0.77, 0.20, 0.04), "MEDIUM": (0.71, 0.28, 0.03),
    "LOW": (0.28, 0.33, 0.40), "INFO": (0.60, 0.64, 0.70),
}
_INK, _MUTED, _FAINT, _RULE = (0.11, 0.11, 0.10), (0.42, 0.42, 0.40), (0.60, 0.60, 0.58), (0.90, 0.90, 0.88)
_FONTS = {"regular": "F1", "bold": "F2", "mono": "F3"}
_WEIGHTS = {"CRITICAL": 10, "HIGH": 5, "MEDIUM": 2, "LOW": 1, "INFO": 0}

# Helvetica advance widths (1/1000 em) for ASCII 32..126, from the standard AFM metrics.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
         556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778,
         722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
         278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def _width(text: str, font: str, size: float) -> float:
    if font == "mono":
        return len(text) * 0.6 * size
    scale = 1.07 if font == "bold" else 1.0       # bold is slightly wider; close enough for wrapping
    return sum(_HELV[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text) * size / 1000 * scale


_JOINERS = re.compile("[︎️‍]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]+")


def _encode(text: str) -> bytes:
    """WinAnsi bytes for a PDF string literal. The standard fonts have no emoji: they print as [emoji]."""
    text = _EMOJI.sub("[emoji]", _JOINERS.sub("", str(text).replace("\t", "    ")))
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if _width(candidate, font, size) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while _width(word, font, size) > width:          # hard-break very long tokens (paths, URLs)
                cut = max(1, int(len(word) * width / _width(word, font, size)) - 1)
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


class _Document:
    """Just enough PDF: pages of text, filled rectangles and lines."""

    def __init__(self):
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self.footer = ""
        self.new_page()

    def new_page(self):
        self.pages.append([])
        self.y = MARGIN

    def ensure(self, height: float):
        if self.y + height > PAGE_H - MARGIN - 18:
            self.new_page()

    def text(self, x: float, text: str, font="regular", size=10.0, rgb=_INK, y: float | None = None):
        y = self.y if y is None else y
        r, g, b = rgb
        self.pages[-1].append(
            b"BT /%s %.1f Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET\n"
            % (_FONTS[font].encode(), size, r, g, b, x, PAGE_H - y - size, _encode(text)))

    def rect(self, x: float, y: float, w: float, h: float, rgb):
        r, g, b = rgb
        self.pages[-1].append(b"%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f\n"
                              % (r, g, b, x, PAGE_H - y - h, w, h))

    def rule(self, gap: float = 8):
        self.rect(MARGIN, self.y, WIDTH, 0.6, _RULE)
        self.y += gap

    def paragraph(self, text: str, x: float = MARGIN, font="regular", size=9.5, rgb=_INK, leading=1.35,
                  width: float | None = None, max_lines: int = 0):
        lines = _wrap(text, font, size, width or (MARGIN + WIDTH - x))
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][: max(0, len(lines[-1]) - 3)] + "..."
        for line in lines:
            self.ensure(size * leading)
            self.text(x, line, font, size, rgb)
            self.y += size * leading

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")                      # filled in once the page tree exists
        pages_id = add(b"")
        fonts = [add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name)
                 for name in (b"Helvetica", b"Helvetica-Bold", b"Courier")]
        resources = b"<< /Font << /F1 %d 0 R /F2 %d 0 R /F3 %d 0 R >> >>" % tuple(fonts)
        kids = []
        total = len(self.pages)
        for number, ops in enumerate(self.pages, 1):
            footer = (b"BT /F1 8 Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET\n"
                      % (*_FAINT, MARGIN, 28, _encode(f"{self.footer}  |  page {number} of {total}")))
            stream = b"".join(ops) + footer
            content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources %s "
                            b"/Contents %d 0 R >>" % (pages_id, PAGE_W, PAGE_H, resources, content)))
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
        objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % k for k in kids), len(kids))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, catalog, xref)
        return bytes(out)


def render_pdf(report: ScanReport, relpath: Callable[[str], str]) -> bytes:
    """The report as PDF bytes."""
    doc = _Document()
    project = Path(report.repo_path).name or str(report.repo_path)    # never print the local user path
    doc.footer = f"{APP_NAME} {APP_VERSION}  |  {project}"
    issues = report.issues
    counts = Counter(i.severity for i in issues)

    # Header and score
    doc.text(MARGIN, f"{APP_NAME} report", "bold", 10, _MUTED)
    doc.y += 16
    doc.paragraph(project, font="bold", size=20, leading=1.2)
    doc.y += 2
    doc.paragraph(f"{datetime.now():%Y-%m-%d %H:%M}  |  {report.scanned_files} files  |  "
                  f"{report.lines_scanned:,} lines  |  {report.scan_time_s:.1f}s", size=9, rgb=_MUTED)
    doc.y += 14
    score = f"{report.slop_score:g}"
    doc.text(MARGIN, score, "bold", 34)
    x = MARGIN + _width(score, "bold", 34) + 8
    doc.text(x, "/ 100 slop", "regular", 11, _MUTED, y=doc.y + 18)
    doc.text(x + 70, f"Grade {report.grade or '-'}", "bold", 12, y=doc.y + 17)
    doc.y += 46
    summary = "  |  ".join(f"{sev.title()} {counts[sev]}" for sev in SEVERITY_RANK if counts.get(sev))
    doc.paragraph(f"{len(issues)} issue(s)" + (f":  {summary}" if summary else ""), size=10)
    doc.paragraph("Score = severity-weighted findings per 1,000 lines (0 is clean). "
                  "Grades: A < 10, B < 25, C < 45, D < 65, F.", size=8.5, rgb=_FAINT)
    doc.y += 10

    if not issues:
        doc.rule(16)
        doc.paragraph("No slop found. Nothing to fix.", font="bold", size=13, rgb=(0.03, 0.46, 0.28))
        return doc.render()

    # By category and top rules
    doc.rule(12)
    doc.paragraph("By category", font="bold", size=11)
    doc.y += 2
    for category, n in Counter(i.category for i in issues).most_common():
        doc.ensure(14)
        doc.text(MARGIN, str(n), "bold", 9.5)
        doc.text(MARGIN + 34, category, "regular", 9.5)
        doc.y += 13
    doc.y += 8
    doc.paragraph("Most frequent", font="bold", size=11)
    doc.y += 2
    titles = {}
    for issue in issues:
        titles.setdefault(issue.rule_id or issue.source_tool, issue.title)
    for rule_id, n in Counter(i.rule_id or i.source_tool for i in issues).most_common(10):
        doc.ensure(14)
        doc.text(MARGIN, str(n), "bold", 9.5)
        doc.text(MARGIN + 34, rule_id, "mono", 8.5)
        doc.text(MARGIN + 210, titles[rule_id][:60], "regular", 9.5, _MUTED)
        doc.y += 13

    # Findings by file, worst first
    by_file = defaultdict(list)
    for issue in issues:
        by_file[relpath(issue.file_path)].append(issue)
    ordered_files = sorted(by_file.items(), key=lambda item: (
        min(SEVERITY_RANK.get(i.severity, 9) for i in item[1]),
        -sum(_WEIGHTS.get(i.severity, 1) for i in item[1]), item[0]))
    doc.new_page()
    doc.paragraph("Findings by file", font="bold", size=14, leading=1.3)
    doc.y += 6
    for path, file_issues in ordered_files:
        doc.ensure(44)
        doc.rule(6)
        doc.paragraph(f"{path}  ({len(file_issues)})", font="bold", size=10)
        doc.y += 2
        for issue in sorted(file_issues, key=lambda i: (SEVERITY_RANK.get(i.severity, 9), i.line_start)):
            doc.ensure(30)
            rgb = _SEVERITY_RGB.get(issue.severity, _MUTED)
            doc.text(MARGIN, f"L{issue.line_start}", "mono", 8, _FAINT)
            doc.text(MARGIN + 38, issue.severity, "bold", 7.5, rgb)
            title_x = MARGIN + 88
            title_lines = _wrap(issue.title, "regular", 9.5, WIDTH - 88 - 150)
            doc.text(title_x, title_lines[0], "regular", 9.5)
            doc.text(MARGIN + WIDTH - 148, (issue.rule_id or issue.source_tool)[:30], "mono", 7.5, _MUTED)
            doc.y += 12.5
            for extra in title_lines[1:]:
                doc.text(title_x, extra, "regular", 9.5)
                doc.y += 12.5
            if issue.evidence:
                doc.paragraph(" ".join(issue.evidence.split()), x=title_x, font="mono", size=7.5,
                              rgb=_MUTED, max_lines=2)
            doc.y += 4

    # How to fix each rule (stated once)
    fixes = {}
    for issue in sorted(issues, key=lambda i: i.rule_id or ""):
        if issue.suggested_fix:
            fixes.setdefault(issue.rule_id or issue.source_tool, issue.suggested_fix)
    if fixes:
        doc.new_page()
        doc.paragraph("How to fix each rule", font="bold", size=14, leading=1.3)
        doc.y += 6
        for rule_id, fix in fixes.items():
            doc.ensure(34)
            doc.paragraph(rule_id, font="mono", size=8.5)
            doc.paragraph(fix, x=MARGIN + 12, size=9.5)
            doc.y += 6
        doc.y += 6
        doc.paragraph("Hand this to your agent: fixmyslop scan -f prompt  (or copy the fix prompt from the "
                      "HTML report), then re-scan to verify.", size=9, rgb=_MUTED)
    return doc.render()
