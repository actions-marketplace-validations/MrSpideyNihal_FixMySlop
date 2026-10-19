"""
Baseline: accept the issues a codebase already has, and only report new ones.

Teams can't adopt a linter that floods a 10-year-old repo with 3,000 findings.
`fixmyslop baseline` records today's findings; later scans hide anything that
was already there, so CI only fails on slop introduced from now on.

Fingerprints deliberately ignore line numbers (code moves when you edit above
it). A finding is identified by rule + file + the normalized text of the
offending line, plus an occurrence counter for identical lines in one file.
"""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from fixmyslop.core.issue import Issue

BASELINE_FILE = ".fixmyslop-baseline.json"
_VERSION = 1


def _line_text(issue: Issue, relpath: str, root: Path, cache: dict) -> str:
    """The offending source line (evidence when the rule captured it, else read from disk)."""
    if issue.evidence:
        return issue.evidence.splitlines()[0]
    if relpath not in cache:
        try:
            cache[relpath] = (root / relpath).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            cache[relpath] = []
    lines = cache[relpath]
    return lines[issue.line_start - 1] if 0 < issue.line_start <= len(lines) else ""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fingerprints(issues: list[Issue], relpath: Callable[[str], str], root: Path) -> list[str]:
    """One stable fingerprint per issue (same order as `issues`)."""
    cache: dict = {}
    seen: Counter = Counter()
    result: list[str] = [""] * len(issues)
    # Count duplicates in a stable order so the Nth identical line always gets the same fingerprint.
    order = sorted(range(len(issues)), key=lambda k: (relpath(issues[k].file_path), issues[k].line_start,
                                                      issues[k].rule_id))
    for k in order:
        issue = issues[k]
        rel = relpath(issue.file_path)
        base = "\x1f".join([issue.rule_id or issue.source_tool, rel, _normalize(_line_text(issue, rel, root, cache))])
        seen[base] += 1
        result[k] = hashlib.sha256(f"{base}\x1f{seen[base]}".encode("utf-8")).hexdigest()[:20]
    return result


def write_baseline(root: Path, issues: list[Issue], relpath: Callable[[str], str]) -> Path:
    """Record the current issues as accepted."""
    prints = fingerprints(issues, relpath, root)
    data = {
        "version": _VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "count": len(prints),
        "note": "Issues FixMySlop accepted as pre-existing. Regenerate with `fixmyslop baseline`.",
        "fingerprints": sorted(prints),
    }
    path = root / BASELINE_FILE
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return path


def load_baseline(root: Path) -> Optional[set]:
    """Fingerprints from the project's baseline file, or None when there isn't one."""
    path = root / BASELINE_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("fingerprints", []))
    except (OSError, ValueError):
        return None


def apply_baseline(issues: list[Issue], accepted: set, relpath: Callable[[str], str],
                   root: Path) -> tuple[list[Issue], int]:
    """Split issues into (new, number hidden by the baseline)."""
    prints = fingerprints(issues, relpath, root)
    new = [issue for issue, fp in zip(issues, prints, strict=True) if fp not in accepted]
    return new, len(issues) - len(new)
