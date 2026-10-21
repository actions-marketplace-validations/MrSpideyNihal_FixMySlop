"""
Report builder — takes a list of Issue objects and assembles
a structured ScanReport with scoring and export methods.
"""
import csv
import io
import json
import dataclasses
from collections import Counter
from datetime import datetime
from pathlib import Path
from fixmyslop.core.issue import Issue, ScanReport
from fixmyslop.core.slop.engine import slop_score, grade
from fixmyslop.core.slop.brief import build_fix_brief, compact_findings
from fixmyslop.core.slop.sarif import to_sarif
from fixmyslop.macros import (
    SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM,
    REPORT_FORMAT_MARKDOWN, REPORT_FORMAT_HTML, REPORT_FORMAT_JSON,
    REPORT_FORMAT_CSV, REPORT_FORMAT_PDF, REPORT_FORMAT_SARIF, REPORT_FORMAT_PROMPT,
    APP_NAME, APP_VERSION, SEVERITY_RANK,
    SCAN_MODE_DEEP, DEFAULT_SCAN_MODE,
)
from fixmyslop.utils.logger import get_logger

logger = get_logger(__name__)


def _scan_mode_summary(scan_mode: str) -> str:
    """Format the scan mode label for summaries and exports."""
    if scan_mode == "rules":
        return "RULES (deterministic)"
    return "DEEP 🔍" if scan_mode == SCAN_MODE_DEEP else "TURBO ⚡"


def _count(n: int, noun: str) -> str:
    """'1 file', '3 files'."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def severity_sort_key(issue: Issue):
    """Sort by real severity order (not alphabetical), then file and line."""
    return (SEVERITY_RANK.get(issue.severity, 9), issue.file_path, issue.line_start)


def relative_path(report: ScanReport, file_path: str) -> str:
    """Path relative to the scanned root (POSIX), falling back to the input."""
    base = Path(report.repo_path)
    if base.is_file():
        base = base.parent
    try:
        return Path(file_path).resolve().relative_to(base.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(file_path).as_posix()


class ReportBuilder:
    """
    Assembles a ScanReport from issues and exports to
    Markdown, HTML, JSON, CSV, SARIF, an agent fix brief, or PDF.
    Computes a 'slop score' (0-100) representing overall debt level.
    """

    SEVERITY_WEIGHTS = {
        SEVERITY_CRITICAL: 10,
        SEVERITY_HIGH: 5,
        SEVERITY_MEDIUM: 2,
    }

    def build(
        self,
        repo_path: str,
        issues: list[Issue],
        total_files: int,
        scanned_files: int,
        scan_time_s: float,
        model_used: str,
        scan_mode: str = DEFAULT_SCAN_MODE,
        lines_scanned: int = 0,
    ) -> ScanReport:
        """Build and return a ScanReport from raw scan data.

        With lines_scanned the score is a density per 1k lines (comparable across
        repos of any size); without it, the legacy per-file formula is used.
        """
        report = ScanReport(
            repo_path=repo_path,
            scan_time_s=scan_time_s,
            total_files=total_files,
            scanned_files=scanned_files,
            issues=issues,
            model_used=model_used,
            scan_mode=scan_mode,
            lines_scanned=lines_scanned,
        )
        if lines_scanned > 0:
            report.slop_score = slop_score(issues, lines_scanned)
        else:
            report.slop_score = self._compute_slop_score(issues, scanned_files)
        report.grade = grade(report.slop_score)
        logger.info(
            "Report built: %d issues, slop score %.1f",
            len(issues), report.slop_score,
        )
        return report

    def _compute_slop_score(self, issues: list[Issue], file_count: int) -> float:
        """Compute 0-100 slop score based on weighted issue counts per file."""
        if file_count == 0:
            return 0.0
        total_weight = sum(
            self.SEVERITY_WEIGHTS.get(i.severity, 1) for i in issues
        )
        score = min(100.0, (total_weight / (file_count * 10)) * 100)
        return round(score, 1)

    def export(self, report: ScanReport, fmt: str = REPORT_FORMAT_MARKDOWN) -> str:
        """Export the report to the requested text format."""
        if fmt == REPORT_FORMAT_JSON:
            return self._to_json(report)
        elif fmt == REPORT_FORMAT_HTML:
            return self._to_html(report)
        elif fmt == REPORT_FORMAT_CSV:
            return self._to_csv(report)
        elif fmt == REPORT_FORMAT_PDF:
            return self._to_html(report)
        elif fmt == REPORT_FORMAT_SARIF:
            return to_sarif(report, lambda p: relative_path(report, p))
        elif fmt == "compact":
            summary = (f"FixMySlop: {_count(len(report.issues), 'issue')}, slop score {report.slop_score:g}/100 "
                       f"(grade {report.grade}), {_count(report.scanned_files, 'file')}")
            return summary + "\n" + compact_findings(report.issues, lambda p: relative_path(report, p))
        elif fmt == REPORT_FORMAT_PROMPT:
            return build_fix_brief(report.issues, lambda p: relative_path(report, p),
                                   design_context=report.design_context)
        else:
            return self._to_markdown(report)

    def export_pdf(self, report: ScanReport, output_path: str):
        """Export report as a PDF file (built-in writer, no extra dependencies)."""
        from fixmyslop.core.pdf_report import render_pdf
        Path(output_path).write_bytes(render_pdf(report, lambda p: relative_path(report, p)))
        logger.info("PDF report saved: %s", output_path)

    def _to_csv(self, report: ScanReport) -> str:
        """Export report as CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Severity", "File", "Line Start", "Line End",
            "Category", "Rule ID", "Title", "Description",
            "Evidence", "Suggested Fix", "Source Tool",
        ])
        for issue in sorted(report.issues, key=severity_sort_key):
            writer.writerow([
                issue.severity,
                relative_path(report, issue.file_path),
                issue.line_start,
                issue.line_end,
                issue.category,
                issue.rule_id,
                issue.title,
                issue.description,
                issue.evidence,
                issue.suggested_fix,
                issue.source_tool,
            ])
        return output.getvalue()

    def _to_markdown(self, report: ScanReport) -> str:
        """Export report as Markdown: summary, findings grouped by file, then each rule's fix once."""
        issues = report.issues
        counts = Counter(i.severity for i in issues)
        breakdown = " · ".join(f"{sev.title()} {counts[sev]}" for sev in SEVERITY_RANK if counts.get(sev))
        project = Path(report.repo_path).name or report.repo_path      # never publish the local user path
        lines = [
            f"# {APP_NAME} report: {project}",
            "",
            f"**Slop score {report.slop_score:g}/100 · grade {report.grade or '-'}** · "
            f"{len(issues)} issues" + (f" ({breakdown})" if breakdown else ""),
            "",
            f"{datetime.now():%Y-%m-%d %H:%M} · {report.scanned_files} files · {report.lines_scanned:,} lines · "
            f"{_scan_mode_summary(report.scan_mode)} · {APP_NAME} {APP_VERSION}",
            "",
        ]
        if not issues:
            return "\n".join(lines + ["No slop found. Nothing to fix.", ""])

        titles: dict[str, str] = {}
        for issue in issues:
            titles.setdefault(issue.rule_id or issue.source_tool, issue.title)
        lines += ["## Summary", "", "| Category | Issues |", "|---|---:|"]
        lines += [f"| {cat} | {n} |" for cat, n in Counter(i.category for i in issues).most_common()]
        lines += ["", "| Rule | Issues | What |", "|---|---:|---|"]
        lines += [f"| `{rule}` | {n} | {titles[rule]} |"
                  for rule, n in Counter(i.rule_id or i.source_tool for i in issues).most_common(10)]
        lines += ["", "## Issues", ""]

        by_file: dict[str, list[Issue]] = {}
        for issue in sorted(issues, key=severity_sort_key):
            by_file.setdefault(relative_path(report, issue.file_path), []).append(issue)
        for path, file_issues in by_file.items():
            lines += [f"### `{path}` ({len(file_issues)})", ""]
            for issue in sorted(file_issues, key=lambda i: (SEVERITY_RANK.get(i.severity, 9), i.line_start)):
                lines.append(f"- **L{issue.line_start}** {issue.severity} · {issue.title} "
                             f"`{issue.rule_id or issue.source_tool}`")
                if issue.evidence:
                    snippet = " ".join(issue.evidence.split()).replace("`", "'")
                    snippet = snippet if len(snippet) <= 160 else snippet[:157] + "..."
                    lines.append(f"  `{snippet}`")
            lines.append("")

        fixes: dict[str, str] = {}
        for issue in sorted(issues, key=lambda i: i.rule_id or ""):
            if issue.suggested_fix:
                fixes.setdefault(issue.rule_id or issue.source_tool, issue.suggested_fix)
        if fixes:
            lines += ["## How to fix each rule", ""]
            lines += [f"- `{rule}`: {fix}" for rule, fix in fixes.items()]
            lines += ["", "Hand the fix list to your agent with `fixmyslop scan -f prompt`, then re-scan to verify.", ""]
        return "\n".join(lines)

    def _to_json(self, report: ScanReport) -> str:
        """Export report as JSON."""
        return json.dumps(dataclasses.asdict(report), indent=2)

    def _to_html(self, report: ScanReport) -> str:
        """Export report as the interactive, self-contained HTML page."""
        from fixmyslop.core.html_report import render_html
        return render_html(report, lambda p: relative_path(report, p))
