"""
`fixmyslop init` — wire FixMySlop into the tools people already use:
Claude Code (live hook + CLAUDE.md), Codex/others (AGENTS.md), Cursor rules,
GitHub Actions (SARIF annotations on PRs) and pre-commit.

Every installer is idempotent: running init twice changes nothing.
"""
import json
from pathlib import Path

from fixmyslop.core.mcp_server import write_mcp_json
from fixmyslop.core.skill import install_skill
from fixmyslop.core.slop.brief import ANTI_SLOP_GUIDE, upsert_guide
from fixmyslop.core.slop.config import DEFAULT_CONFIG_YAML
from fixmyslop.macros import APP_VERSION, PROJECT_CONFIG_FILE

HOOK_COMMAND = "fixmyslop hook"
HOOK_MATCHER = "Write|Edit|MultiEdit"

GITHUB_WORKFLOW = """\
name: FixMySlop

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write

jobs:
  slop:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: MrSpideyNihal/FixMySlop@v__VERSION__
        with:
          fail-on: high
          changed-only: ${{ github.event_name == 'pull_request' }}
"""

PRE_COMMIT_ENTRY = """\
  - repo: https://github.com/MrSpideyNihal/FixMySlop
    rev: v0.6.7
    hooks:
      - id: fixmyslop
"""


def write_config(root: Path) -> str:
    """Create .fixmyslop.yml if missing."""
    path = root / PROJECT_CONFIG_FILE
    if path.exists():
        return f"{PROJECT_CONFIG_FILE} already exists"
    path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    return f"created {PROJECT_CONFIG_FILE}"


def install_claude(root: Path) -> list[str]:
    """Add the PostToolUse hook to .claude/settings.json and the guidelines to CLAUDE.md."""
    done = []
    settings_path = root / ".claude" / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return [f"skipped: {settings_path} is not valid JSON — add the hook manually"]
    post = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    already = any(
        HOOK_COMMAND in (hook.get("command") or "")
        for entry in post if isinstance(entry, dict)
        for hook in entry.get("hooks", []) if isinstance(hook, dict)
    )
    if already:
        done.append("Claude Code hook already installed")
    else:
        post.append({"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": HOOK_COMMAND}]})
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        done.append("added PostToolUse hook to .claude/settings.json (checks every file Claude edits)")
    done.append(f"{upsert_guide(root / 'CLAUDE.md')} anti-slop rules in CLAUDE.md")
    done.append(install_skill(root / ".claude").replace(root.as_posix() + "/", ""))
    done.append(write_mcp_json(root / ".mcp.json").replace(root.as_posix() + "/", ""))
    return done


def install_agents_md(root: Path) -> list[str]:
    """Add the guidelines to AGENTS.md (read by Codex, Cursor, Gemini, Copilot agents, etc.)."""
    return [f"{upsert_guide(root / 'AGENTS.md')} anti-slop rules in AGENTS.md"]


def install_cursor(root: Path) -> list[str]:
    """Write a Cursor project rule."""
    path = root / ".cursor" / "rules" / "fixmyslop.mdc"
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(
        "---\ndescription: Anti-slop rules enforced by FixMySlop\nalwaysApply: true\n---\n\n" + ANTI_SLOP_GUIDE,
        encoding="utf-8",
    )
    return [f"{'updated' if existed else 'created'} .cursor/rules/fixmyslop.mdc",
            write_mcp_json(root / ".cursor" / "mcp.json").replace(root.as_posix() + "/", "")]


def install_github(root: Path) -> list[str]:
    """Add a GitHub Actions workflow that annotates PRs and gates merges."""
    path = root / ".github" / "workflows" / "fixmyslop.yml"
    if path.exists():
        return [".github/workflows/fixmyslop.yml already exists"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GITHUB_WORKFLOW.replace("__VERSION__", APP_VERSION), encoding="utf-8")
    return ["created .github/workflows/fixmyslop.yml (PR annotations + fail on HIGH)"]


def install_pre_commit(root: Path) -> list[str]:
    """Add the pre-commit hook entry."""
    path = root / ".pre-commit-config.yaml"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "FixMySlop" in text or "fixmyslop" in text:
            return ["pre-commit hook already configured"]
        if "repos:" not in text:
            return ["skipped: .pre-commit-config.yaml has no `repos:` key — add the entry manually"]
        separator = "" if text.endswith("\n") else "\n"
        path.write_text(text + separator + PRE_COMMIT_ENTRY, encoding="utf-8")
        return ["added fixmyslop to .pre-commit-config.yaml"]
    path.write_text("repos:\n" + PRE_COMMIT_ENTRY, encoding="utf-8")
    return ["created .pre-commit-config.yaml"]


def detect_targets(root: Path) -> list[str]:
    """Integrations worth installing by default, based on what the repo already uses."""
    targets = []
    if (root / ".claude").exists() or (root / "CLAUDE.md").exists():
        targets.append("claude")
    if (root / "AGENTS.md").exists():
        targets.append("agents")
    if (root / ".cursor").exists() or (root / ".cursorrules").exists():
        targets.append("cursor")
    return targets
