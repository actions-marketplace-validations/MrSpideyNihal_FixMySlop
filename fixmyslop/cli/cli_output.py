"""
CLI output — Rich-powered terminal output for FixMySlop CLI.
Provides banner, report tables, issue cards, and styled messages.
"""
import io
import sys
import os

if (
    sys.platform == "win32"
    and hasattr(sys.stdout, "buffer")
    and "pytest" not in sys.modules
    and "PYTEST_CURRENT_TEST" not in os.environ
):
    try:
        current_encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
        if current_encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
    except (AttributeError, ValueError, OSError):
        pass  # stdout isn't a real stream (IDE, redirected) — keep it as is

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from fixmyslop.macros import (
    SEVERITY_CRITICAL, SEVERITY_HIGH,
    SEVERITY_MEDIUM, SEVERITY_LOW, SEVERITY_INFO,
    SCAN_MODE_TURBO, SCAN_MODE_DEEP,
)

console = Console()

# ─── Severity color mapping for Rich ────────────────────────────────────────
_SEVERITY_STYLES = {
    SEVERITY_CRITICAL: "bold red",
    SEVERITY_HIGH: "bold bright_red",
    SEVERITY_MEDIUM: "bold yellow",
    SEVERITY_LOW: "bold cyan",
    SEVERITY_INFO: "dim white",
}


def _safe_mode_icon(icon: str, fallback: str) -> str:
    """Return mode icon when encodable, otherwise an ASCII fallback."""
    try:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        icon.encode(encoding)
        return icon
    except UnicodeEncodeError:
        return fallback


def _scan_mode_label(scan_mode: str) -> str:
    """Format scan mode text for terminal output."""
    deep_icon = _safe_mode_icon("🔍", "[D]")
    turbo_icon = _safe_mode_icon("⚡", "[T]")
    return f"DEEP {deep_icon}" if scan_mode == SCAN_MODE_DEEP else f"TURBO {turbo_icon}"


def _heads_up_mode_line(scan_mode: str) -> str:
    """Return the mode line shown in the Heads Up panel."""
    deep_icon = _safe_mode_icon("🔍", "[D]")
    turbo_icon = _safe_mode_icon("⚡", "[T]")
    if scan_mode == SCAN_MODE_DEEP:
        return f"Mode: DEEP {deep_icon} (full analysis, all issues)"
    return f"Mode: TURBO {turbo_icon} (top 10 critical issues)"


def print_banner(name: str, version: str):
    """Print the FixMySlop banner."""
    banner = Text()
    banner.append(f" {name} ", style="bold black on magenta")
    banner.append(f" v{version}  ", style="bold magenta")
    banner.append("the slop linter for the AI-agent era", style="dim")
    console.print(banner)


def print_report(report):
    """Print a full scan report as a rich table."""
    mode = getattr(report, "scan_mode", SCAN_MODE_TURBO)

    # Summary panel
    summary = (
        f"[bold]Repo:[/bold] {report.repo_path}\n"
        f"[bold]Files Scanned:[/bold] {report.scanned_files}/{report.total_files}\n"
        f"[bold]Issues Found:[/bold] {len(report.issues)}\n"
        f"[bold]Slop Score:[/bold] {report.slop_score}/100\n"
        f"[bold]Mode:[/bold] {_scan_mode_label(mode)}\n"
        f"[bold]Model:[/bold] {report.model_used}\n"
        f"[bold]Scan Time:[/bold] {report.scan_time_s:.1f}s"
    )
    console.print(Panel(summary, title="Scan Summary", border_style="magenta"))

    if not report.issues:
        console.print("\n[bold green]✓ No issues found![/bold green]\n")
        return

    # Issues table
    table = Table(
        box=box.ROUNDED,
        title="Issues",
        title_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Severity", style="bold", width=10)
    table.add_column("File", style="cyan", max_width=30)
    table.add_column("Line", justify="right", width=6)
    table.add_column("Title", max_width=40)
    table.add_column("Category", style="dim", width=16)
    table.add_column("Source", style="dim", width=8)

    for issue in sorted(report.issues, key=lambda i: i.severity):
        severity_style = _SEVERITY_STYLES.get(issue.severity, "white")
        table.add_row(
            Text(issue.severity, style=severity_style),
            str(issue.file_path),
            str(issue.line_start),
            issue.title,
            issue.category,
            issue.source_tool,
        )

    console.print(table)
    console.print()

    # Severity breakdown
    _print_severity_breakdown(report.issues)


def _print_severity_breakdown(issues):
    """Print a count of issues by severity."""
    counts = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1

    parts = []
    for sev in [SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, SEVERITY_INFO]:
        if sev in counts:
            style = _SEVERITY_STYLES.get(sev, "white")
            parts.append(f"[{style}]{sev}: {counts[sev]}[/{style}]")

    console.print("  ".join(parts))
    console.print()


def print_issue(issue):
    """Print a single issue as a rich panel."""
    severity_style = _SEVERITY_STYLES.get(issue.severity, "white")
    content = (
        f"[bold]File:[/bold] {issue.file_path} (lines {issue.line_start}-{issue.line_end})\n"
        f"[bold]Category:[/bold] {issue.category}\n"
        f"[bold]Rule:[/bold] {issue.rule_id}\n"
        f"\n{issue.description}"
    )
    if issue.evidence:
        content += f"\n\n[dim]Evidence:[/dim]\n{issue.evidence}"

    console.print(Panel(
        content,
        title=f"[{severity_style}][{issue.severity}][/{severity_style}] {issue.title}",
        border_style=severity_style.split()[-1],
    ))


def print_error(message: str):
    """Print an error message."""
    console.print(f"[bold red]✗ Error:[/bold red] {escape(str(message))}")


def print_success(message: str):
    """Print a success message."""
    console.print(f"[bold green]✓[/bold green] {escape(str(message))}")


def print_info(message: str):
    """Print an info message."""
    console.print(f"[bold blue]ℹ[/bold blue] {escape(str(message))}")


def print_warning(message: str):
    """Print a warning message."""
    console.print(f"[bold yellow]⚠[/bold yellow] {escape(str(message))}")


def print_llm_warning(model: str, scan_mode: str = SCAN_MODE_TURBO):
    """Print a yellow warning about LLM analysis being slow."""
    console.print(Panel(
        f"[bold]{_heads_up_mode_line(scan_mode)}[/bold]\n\n"
        f"[bold]LLM analysis enabled[/bold] — using [cyan]{model}[/cyan]\n\n"
        "This may take [bold]1-3 minutes per file[/bold] depending on your hardware.\n"
        "Progress will update after each file completes.\n\n"
        "[dim]Tip: If LLM analysis fails, use a code-tuned model like\n"
        "qwen2.5-coder:7b for more reliable JSON output.[/dim]",
        title="⚠ Heads Up",
        border_style="yellow",
    ))
    console.print()


def print_scan_estimate(estimate: dict, file_count: int, model: str):
    """Print a scan time estimate panel."""
    content = (
        f"[bold]Files to scan[/bold]    : {file_count}\n"
        f"[bold]Model[/bold]            : [cyan]{model}[/cyan]\n"
        f"[bold]Hardware[/bold]         : {estimate['hardware']}\n"
        f"[bold]Estimated time[/bold]   : [bold yellow]{estimate['total_human']}[/bold yellow]\n"
        f"\n[dim]Tip: Use --no-llm for instant static-only scan[/dim]"
    )
    console.print(Panel(
        content,
        title="📊 Scan Estimate",
        border_style="cyan",
    ))
    console.print()


def print_analyzing(filename: str, model: str, current: int, total: int,
                    elapsed_s: float = 0.0):
    """Print per-file analysis status with elapsed time and ETA."""
    from fixmyslop.utils.system_info import format_eta
    eta = format_eta(elapsed_s, current - 1, total) if elapsed_s > 0 else "calculating..."
    elapsed_str = f"{int(elapsed_s)}s" if elapsed_s < 60 else f"{int(elapsed_s // 60)}m {int(elapsed_s % 60)}s"
    console.print(
        f"  [bold yellow]⏳[/bold yellow] [{current}/{total}] "
        f"Analyzing: [cyan]{filename}[/cyan] — "
        f"elapsed {elapsed_str} — ETA {eta}"
    )



# ─── Slop report (v0.3) ──────────────────────────────────────────────────────

_GRADE_STYLES = {"A": "bold green", "B": "green", "C": "yellow", "D": "bright_red", "F": "bold red"}
_SEVERITY_SHORT = {
    SEVERITY_CRITICAL: "CRIT", SEVERITY_HIGH: "HIGH", SEVERITY_MEDIUM: "MED",
    SEVERITY_LOW: "LOW", SEVERITY_INFO: "INFO",
}


def print_slop_report(report, relpath, limit: int = 60, fixable_count: int = 0,
                      agent_hint: str = "", target: str = "."):
    """Print findings grouped by file, a score summary, and concrete next steps."""
    from collections import Counter, defaultdict
    from fixmyslop.macros import SEVERITY_RANK

    by_file = defaultdict(list)
    for issue in report.issues:
        by_file[relpath(issue.file_path)].append(issue)

    def file_rank(item):
        path, issues = item
        return (min(SEVERITY_RANK.get(i.severity, 9) for i in issues), path)

    shown = 0
    for path, issues in sorted(by_file.items(), key=file_rank):
        if limit and shown >= limit:
            break
        console.print(f"\n[bold underline]{escape(path)}[/bold underline]")
        for issue in sorted(issues, key=lambda i: (i.line_start, SEVERITY_RANK.get(i.severity, 9))):
            if limit and shown >= limit:
                break
            style = _SEVERITY_STYLES.get(issue.severity, "white")
            label = _SEVERITY_SHORT.get(issue.severity, issue.severity[:4])
            line = Text()
            line.append(f"  {issue.line_start:>5}  ", style="dim")
            line.append(f"{label:<5}", style=style)
            line.append(f" {issue.rule_id or issue.source_tool:<26} ", style="cyan")
            line.append(issue.title)
            console.print(line, overflow="ellipsis", no_wrap=True)
            if issue.evidence:
                evidence = " ".join(issue.evidence.split())
                console.print(Text(f"         {evidence[:110]}", style="dim"), overflow="ellipsis", no_wrap=True)
            shown += 1

    hidden = len(report.issues) - shown
    if hidden > 0:
        console.print(f"\n[dim]… and {hidden} more. Use --limit 0 to show all, or -f html -s report.html.[/dim]")

    console.print()
    counts = Counter(i.severity for i in report.issues)
    categories = Counter(i.category for i in report.issues)
    grade_style = _GRADE_STYLES.get(report.grade, "bold")
    summary = Text()
    summary.append("Slop score ", style="bold")
    summary.append(f"{report.slop_score:g}/100 ", style=grade_style)
    summary.append(f" grade {report.grade or '-'}", style=grade_style)
    summary.append(
        f"   {len(report.issues)} issues · {report.scanned_files} files · "
        f"{report.lines_scanned:,} lines · {report.scan_time_s:.2f}s"
        + (f" · {report.baselined} pre-existing hidden by baseline" if getattr(report, "baselined", 0) else ""),
        style="dim",
    )
    sev_line = Text()
    for sev in SEVERITY_RANK:
        if counts.get(sev):
            sev_line.append(f"{sev} {counts[sev]}   ", style=_SEVERITY_STYLES.get(sev, "white"))
    cat_line = "  ·  ".join(f"{cat} {n}" for cat, n in categories.most_common())
    body = Text("\n").join([summary, sev_line, Text(cat_line, style="dim")]) if report.issues else summary
    console.print(Panel(body, border_style=grade_style.split()[-1], title="FixMySlop", title_align="left"))

    if not report.scanned_files:
        console.print("[bold yellow]Nothing was scanned.[/bold yellow] No supported files found here "
                      "(HTML, CSS, JS/TS, JSX/TSX, Vue, Svelte, Astro, Python, …). Check the path.\n")
        return
    if not report.issues:
        console.print("[bold green]Clean.[/bold green] "
                      + ("No new slop since the baseline.\n" if getattr(report, "baselined", 0) else "Nothing to fix.\n"))
        return
    console.print("[bold]Next[/bold]")
    steps = Table.grid(padding=(0, 3))
    steps.add_column(style="cyan", no_wrap=True)
    steps.add_column(style="dim")
    if fixable_count:
        steps.add_row(escape(f"fixmyslop fix {target} --safe-only"),
                      f"auto-fix {fixable_count} issue(s) that can't change behavior")
    steps.add_row(escape(f"fixmyslop fix {target}"),
                  f"safe fixes, then {agent_hint or 'your agent'} fixes the rest")
    steps.add_row(escape(f"fixmyslop scan {target} --open"), "interactive report with copy-to-agent prompts")
    console.print(steps)
    console.print()


def stderr_console() -> Console:
    """A console bound to stderr (for hook output and machine-format runs)."""
    return Console(stderr=True)
