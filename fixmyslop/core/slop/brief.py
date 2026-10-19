"""
Agent hand-off: the fix brief (what to fix, where, how) and the standing
anti-slop guidelines that get installed into CLAUDE.md / AGENTS.md / Cursor rules.

FixMySlop finds slop deterministically; frontier agents are great at fixing
it once they're told *exactly* what's wrong. The brief is that contract.
"""
from collections import defaultdict
from pathlib import Path
from typing import Callable

from fixmyslop.core.issue import Issue
from fixmyslop.core.slop.rule import RULES
from fixmyslop.macros import SEVERITY_RANK, SLOP_TOOL

_UI_DIRECTION = """\
## UI direction (applies to every ui/* item)
- Keep the project's existing design tokens (tailwind config, CSS variables, theme). Do NOT invent new colors.
- One accent color + neutrals. Red/amber/green only for real error/warning/success states.
- No gradient text, no purple→pink gradients, no decorative blurred blobs, no glassmorphism stacks.
- No glowing orbs or colored glows in any hue, no forever-pulsing status dots, no two-tone accent headlines,
  no uppercase letter-spaced "eyebrow" label above every section.
- Charts must come from real data; with no data, show an empty state instead of decorative bars.
- Replace emoji used as icons with the icon library already in package.json (or remove them).
- Rewrite marketing copy and taglines in plain, specific language ("Blocks the sites you choose during the
  hours you set"), not hype ("revolutionize") or aphorisms ("make space for what matters", "intentional by
  design", "less X. more Y."). Never invent stats, ratings, logos or testimonials —
  if real ones aren't available, remove the element and mention it in your summary.
- Prefer borders/spacing over heavy shadows; motion only where it communicates state.
"""

_RULES_OF_ENGAGEMENT = """\
## Rules of engagement
- Fix exactly the items below. Keep changes minimal and behavior-preserving; don't refactor unrelated code.
- Do not leave placeholder comments ("... existing code ...", "in a real app ...") or TODO stubs.
- Never delete functionality to make a finding disappear.
- If an item is a genuine false positive, leave the code and add `{marker}[<rule-id>]` with a short reason
  in a comment on the line above.
- When done, run `fixmyslop scan {targets} --fail-on low` and repeat until it passes.

## Production bar (every fix, not a rewrite)
Each fix must be what you'd ship to real users, but stay inside the items below.
- Errors: handle them for real. Log with context or show the user a state they can act on; never just silence
  a finding with an empty comment. Fire-and-forget code (telemetry, prefetch) may ignore errors with a comment
  saying why.
- Accessibility: keyboard focus visible, every control has an accessible name, images have alt text.
- Security: no secrets in code or committed env files. If a real key was ever committed, say it must be
  rotated; removing it from the file isn't enough.
- No dead code left behind: when a finding shows a file or function is an unused duplicate, delete it rather
  than keeping both.
- Verify before you finish: run the project's type check, lint, tests and build (whatever package.json /
  pyproject defines) before and after your changes. Your changes must not add new errors. List any
  pre-existing failures in your summary instead of hiding them.
- Summarize what you changed per file, anything you left as a false positive (and why), and anything the
  user must do themselves (rotate a key, choose a font, supply real testimonials).
"""

_DOCS_DIRECTION = """\
## Docs direction (applies to every docs/* item)
- Docs must match the repo: use the real script names from package.json, the real repository URL, and links to
  files that exist. If the docs describe something that doesn't exist, fix the docs; add the missing piece
  only when the docs are clearly right (e.g. a LICENSE file for the license the README states).
- Plain headings without emoji; say what the project does and how to run it, with specifics instead of hype.
- Remove chatbot framing ("Certainly! Here's...") and fill in or delete every bracketed placeholder.
"""


def build_fix_brief(issues: list[Issue], relpath: Callable[[str], str], targets: str = ".",
                    design_context: str = "") -> str:
    """Render a markdown task list an agent (Claude Code, Codex, Cursor…) can execute."""
    if not issues:
        return "# FixMySlop fix brief\n\nNothing to fix — the scan is clean.\n"

    by_file: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        by_file[relpath(issue.file_path)].append(issue)

    def file_rank(item):
        path, file_issues = item
        return (min(SEVERITY_RANK.get(i.severity, 9) for i in file_issues), path)

    has_ui = any(i.rule_id.startswith(("ui/", "a11y/")) for i in issues)
    out = [
        "# FixMySlop fix brief",
        "",
        f"FixMySlop found **{len(issues)} issue(s)** in **{len(by_file)} file(s)**. "
        "Fix them in order (most severe files first).",
        "",
        _RULES_OF_ENGAGEMENT.format(marker="fixmyslop-ignore", targets=targets),
    ]
    if has_ui:
        out.append(_UI_DIRECTION)
        if design_context:
            out += ["## This project's design system (use it)", design_context, ""]
    if any(i.rule_id.startswith("docs/") for i in issues):
        out.append(_DOCS_DIRECTION)

    fix_hints: dict[str, str] = {}
    out.append("## Issues")
    for path, file_issues in sorted(by_file.items(), key=file_rank):
        out.append("")
        out.append(f"### `{path}`")
        for issue in sorted(file_issues, key=lambda i: (i.line_start, SEVERITY_RANK.get(i.severity, 9))):
            rule = issue.rule_id or issue.source_tool
            detail = _detail(issue)
            out.append(f"- [ ] **L{issue.line_start}** `{rule}` ({issue.severity}) — {issue.title}"
                       + (f": {detail}" if detail and detail != issue.title else ""))
            if issue.evidence:
                out.append(f"      `{_clip(issue.evidence)}`")
            if issue.suggested_fix and issue.source_tool == SLOP_TOOL:
                fix_hints.setdefault(rule, issue.suggested_fix)

    if fix_hints:
        out += ["", "## How to fix each rule"]
        for rule, hint in sorted(fix_hints.items()):
            out.append(f"- `{rule}`: {hint}")
    out.append("")
    return "\n".join(out)


def _detail(issue: Issue) -> str:
    """The hit-specific part of a description (the generic rationale lives in 'How to fix')."""
    if issue.source_tool != SLOP_TOOL:
        return issue.description
    known = RULES.get(issue.rule_id)
    text = issue.description
    if known and text.endswith(known.why):
        text = text[: -len(known.why)].rstrip(" .")
    elif known:
        text = ""
    return text if len(text) < 160 else text[:157] + "..."


def _clip(text: str, limit: int = 140) -> str:
    """Single-line, length-limited evidence snippet safe inside backticks."""
    text = " ".join(text.split()).replace("`", "'")
    return text if len(text) <= limit else text[: limit - 3] + "..."


GUIDE_START = "<!-- fixmyslop:start -->"
GUIDE_END = "<!-- fixmyslop:end -->"

ANTI_SLOP_GUIDE = """\
## Anti-slop rules (enforced by FixMySlop)

These rules are checked automatically by `fixmyslop scan`. Code that violates them will be flagged.

### Code
- Never write placeholder implementations ("in a real app you would…", "simplified for demo", `pass` stubs).
  Implement it properly or raise an explicit error and say so.
- Never elide code with comments like `// ... existing code ...` or `# rest of the file unchanged`.
- No comments that narrate the next line (`# Initialize the list`). Comment *why*, not *what*.
- Never swallow exceptions (`except Exception: pass`, empty `catch {}`). Handle, log with context, or re-raise.
- Don't import packages that aren't in the manifest. Never guess a package name — check it exists first.
- No `any`, `@ts-ignore`, or file-wide lint/type suppressions to make errors disappear.
- No emoji in log messages; no `console.log`/`debugger`/`breakpoint()` left behind.

### UI
- Use the existing design tokens. One accent color plus neutrals; don't add one-off hex colors or `w-[347px]` values.
- No purple→pink gradients, gradient text, glassmorphism stacks, decorative blurred blobs, or `hover:scale-105` on everything.
- No glowing orbs or colored glows (any hue), pulsing status dots, two-tone accent headlines, or uppercase letter-spaced
  "eyebrow" labels above every section. Charts only from real data — otherwise an empty state.
- No emoji as icons — use the project's icon library.
- Copy must be plain and specific. Never write "revolutionize", "seamless", "unlock the power of", or calm-sounding
  aphorisms ("make space for what matters", "intentional by design", "your data stays yours", "less X. more Y."),
  and never invent stats, ratings, logos, or testimonials.
- Accessibility is not optional: alt text on images, `<button>` not clickable `<div>`, `aria-label`
  on icon-only buttons, visible focus styles, real `href`s.

### Security
- Never hardcode keys, tokens or passwords. Never put secrets in `NEXT_PUBLIC_`/`VITE_` variables.
- Parameterised SQL only. No wildcard CORS, no `verify=False`, no unsanitized `innerHTML`/`dangerouslySetInnerHTML`.

Before finishing a task, run `fixmyslop scan --changed --fail-on medium` and fix what it reports.
"""


def guide_block() -> str:
    """The guidelines wrapped in idempotent markers."""
    return f"{GUIDE_START}\n{ANTI_SLOP_GUIDE}{GUIDE_END}\n"


def upsert_guide(path: Path) -> str:
    """Insert or refresh the guidelines block in a markdown file. Returns 'created'|'updated'|'added'."""
    block = guide_block()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    if GUIDE_START in text and GUIDE_END in text:
        start = text.index(GUIDE_START)
        end = text.index(GUIDE_END) + len(GUIDE_END)
        new_text = text[:start] + block.rstrip("\n") + text[end:]
        path.write_text(new_text, encoding="utf-8")
        return "updated"
    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + separator + block, encoding="utf-8")
    return "added"


def compact_findings(issues: list[Issue], relpath: Callable[[str], str], limit: int = 200) -> str:
    """Token-efficient findings for agents: one line per issue, each rule's fix stated once."""
    if not issues:
        return "No issues found."
    ordered = sorted(issues, key=lambda i: (SEVERITY_RANK.get(i.severity, 9), relpath(i.file_path), i.line_start))
    lines = []
    fixes: dict[str, str] = {}
    for issue in ordered[:limit]:
        rule = issue.rule_id or issue.source_tool
        detail = _detail(issue)
        lines.append(f"{relpath(issue.file_path)}:{issue.line_start} {issue.severity} {rule} "
                     f"{issue.title}" + (f" ({detail})" if detail and detail != issue.title else ""))
        if issue.suggested_fix:
            fixes.setdefault(rule, issue.suggested_fix)
    if len(ordered) > limit:
        lines.append(f"... {len(ordered) - limit} more")
    if fixes:
        lines.append("")
        lines.append("How to fix:")
        lines += [f"- {rule}: {hint}" for rule, hint in sorted(fixes.items())]
    return "\n".join(lines)
