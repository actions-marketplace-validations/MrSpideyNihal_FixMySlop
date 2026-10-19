"""
Docs slop rules — READMEs and Markdown docs written (and never re-read) by an AI.

Two kinds of tells:
- the doc is wrong about the repo: `npm run` scripts that don't exist, links to missing files,
  `github.com/yourusername/...` clone commands, chatbot replies pasted in;
- the generated-README look: emoji on every heading, hype copy, "Made with ❤️" sign-offs.
The first kind is checked against the real project, so it's objective.
"""
import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote

from fixmyslop.core.slop.rule import FileContext, Hit, rule, RULES, KIND_DOC
from fixmyslop.core.slop.rules_ui import EMOJI_RE
from fixmyslop.macros import SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, CATEGORY_DOCS

DOC = (KIND_DOC,)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")   # any indent: fences inside list items are indented
# Files GitHub renders for visitors; docs-site sources (Docusaurus, MkDocs...) use routes, not file paths.
_REPO_DOC = re.compile(
    r"^(readme|contributing|changelog|security|code_of_conduct|support|development|setup|install|usage)\b",
    re.IGNORECASE)


def _fenced(ctx: FileContext) -> set[int]:
    """Line numbers inside fenced code blocks (cached on the context)."""
    cached = getattr(ctx, "_doc_fenced", None)
    if cached is not None:
        return cached
    fenced, opener = set(), None
    for n, line in enumerate(ctx.lines, 1):
        match = _FENCE.match(line)
        if opener is None and match:
            opener = match.group(1)[0] * len(match.group(1))
            fenced.add(n)
        elif opener is not None:
            fenced.add(n)
            if line.strip().startswith(opener):
                opener = None
    ctx._doc_fenced = fenced
    return fenced


def _prose(ctx: FileContext) -> Iterable[tuple[int, str]]:
    """(line number, text) for lines outside code fences and HTML comments.

    Inline code and quoted text are removed: a doc quoting "seamless" or "Certainly! Here's..." to
    talk about it isn't using that voice.
    """
    fenced = _fenced(ctx)
    for n, line in enumerate(ctx.lines, 1):
        if n not in fenced and not ctx.is_comment(n):
            yield n, _QUOTED.sub("", line)


_QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”")


# ─── Chatbot residue ─────────────────────────────────────────────────────────

_CHATBOT = re.compile(
    r"^\W*(?:certainly|sure|absolutely|of course)[!,.]\s+(?:here(?:'s| is| are)|below)"
    r"|\bas an ai(?: language model| assistant)?\b"
    r"|\bi hope this (?:helps|readme)\b"
    r"|\bfeel free to (?:customi[sz]e|modify|adjust|tweak) (?:this|the) (?:readme|template|content|sections?)\b"
    r"|\bhere(?:'s| is) (?:a|an|the|your) (?:comprehensive|detailed|professional|updated|polished|complete) readme\b"
    r"|\[(?:insert|add)\s[^\]]{0,30}\b(?:here|screenshot|image|link|url|description|name|logo|gif|demo)\b[^\]]{0,12}\](?!\()"
    r"|\blet me know if you(?:'d| would)? (?:like|want|need)\b",
    re.IGNORECASE)


@rule(
    "docs/chatbot-residue", "Chatbot reply pasted into the docs", SEVERITY_HIGH, CATEGORY_DOCS, DOC,
    why="\"Certainly! Here's a comprehensive README\", \"[Insert screenshot here]\", \"Let me know if you'd like "
        "changes\": the model's reply was pasted in whole and nobody read it.",
    fix="Delete the chat framing and fill in or remove every bracketed placeholder.",
    skip_tests=True,
)
def check_chatbot_residue(ctx: FileContext) -> Iterable[Hit]:
    """Flag lines that are the assistant talking, not documentation."""
    for n, text in _prose(ctx):
        match = _CHATBOT.search(text)
        if match:
            yield Hit(n, ctx.lines[n - 1].strip(), match.group(0))


# ─── Placeholders ────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(
    r"github\.com/your[-_]?(?:user(?:name)?|github(?:[-_]?username)?|org(?:anization)?|name|handle)/"
    r"|github\.com/[\w.-]+/your[-_]?(?:repo(?:sitory)?|project)(?:[-_]name)?\b"
    r"|\byour[-_.]?email@|\byou@example\.com\b"
    r"|\[your name\]|\byour name here\b|\byour[-_]project[-_]name\b",
    re.IGNORECASE)
_FORK = re.compile(r"\bfork", re.IGNORECASE)


@rule(
    "docs/placeholder", "Template placeholder left in the docs", SEVERITY_MEDIUM, CATEGORY_DOCS, DOC,
    why="`git clone https://github.com/yourusername/project` and \"[Your Name]\" are AI template text. The "
        "install steps don't work for anyone who copies them.",
    fix="Replace placeholders with the real repository URL, author and contact details.",
    skip_tests=True,
)
def check_placeholder(ctx: FileContext) -> Iterable[Hit]:
    """Flag template placeholders; fork instructions ("clone your fork") legitimately use them."""
    if ctx.path.name.lower().startswith("contributing"):
        return
    for n, line in enumerate(ctx.lines, 1):
        match = _PLACEHOLDER.search(line)
        if not match or ctx.is_comment(n):
            continue
        nearby = " ".join(ctx.lines[max(0, n - 4):n])
        if _FORK.search(nearby):
            continue
        yield Hit(n, line.strip(), match.group(0))


# ─── Commands and links that don't exist ─────────────────────────────────────

_RUN_SCRIPT = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+run\s+([A-Za-z0-9][\w:.\-]*)|\bnpm\s+(test|start)\b")


_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "vendor", ".venv", "venv"}


def _all_scripts(ctx: FileContext) -> set:
    """Every script name in any package.json of the project (cached per scan)."""
    project = ctx.project
    cached = getattr(project, "_doc_all_scripts", None)
    if cached is not None:
        return cached
    names: set = set()
    root = getattr(project, "root", ctx.path.parent)
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        if "package.json" in files:
            try:
                data = json.loads((Path(folder) / "package.json").read_text(encoding="utf-8"))
                names |= set((data.get("scripts") or {}).keys())
            except (OSError, ValueError, AttributeError):
                continue
    if project is not None:
        project._doc_all_scripts = names
    return names


def _package_scripts(md: Path, root: Path) -> Optional[tuple[set, bool]]:
    """Scripts from the nearest package.json at or above the doc (and the root one), plus a workspaces flag."""
    scripts, workspaces, found = set(), False, False
    for folder in [md.parent, *md.parent.parents]:
        candidate = folder / "package.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            found = True
            scripts |= set((data.get("scripts") or {}).keys())
            workspaces = workspaces or bool(data.get("workspaces"))
        if folder == root or root not in folder.parents:
            break
    return (scripts, workspaces) if found else None


@rule(
    "docs/missing-script", "Docs run a script package.json doesn't have", SEVERITY_MEDIUM, CATEGORY_DOCS, DOC,
    why="The README says `npm run test` (or `lint`, `db:migrate`...) but package.json has no such script. "
        "AI writes the commands a project like this usually has, not the ones this one has.",
    fix="Use the real script names from package.json, or add the missing script if the docs are right.",
    skip_tests=True, aggregate_per_file=True,
)
def check_missing_script(ctx: FileContext) -> Iterable[Hit]:
    """Compare `npm run X` / `pnpm run X` / `npm test` in the docs with package.json scripts."""
    root = getattr(ctx.project, "root", ctx.path.parent)
    package = _package_scripts(ctx.path, root)
    if package is None:
        return
    scripts, workspaces = package
    for n, line in enumerate(ctx.lines, 1):
        for match in _RUN_SCRIPT.finditer(line):
            name = match.group(1) or match.group(2)
            if name in scripts or name == "start" and not scripts:
                continue
            if name in _all_scripts(ctx) or re.search(rf"[\"']{re.escape(name)}[\"']\s*:", ctx.text):
                continue  # another package in the repo has it, or the doc tells you to add it
            if workspaces and re.search(r"--filter|--workspace|\s-w\s|-F\s", line):
                continue
            yield Hit(n, line.strip(), f"no \"{name}\" script in package.json")
            break


_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)|\b(?:src|href)\s*=\s*\"([^\"]+)\"")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\{|\$|%7B)", re.IGNORECASE)
_LICENSE_MENTION = re.compile(r"\bsee (?:the )?LICENSE(?:\.md|\.txt)?(?: file)?\b|\bLICENSE(?:\.md|\.txt)? file\b")


@rule(
    "docs/dead-link", "Docs link to a file that doesn't exist", SEVERITY_MEDIUM, CATEGORY_DOCS, DOC,
    why="Links and images pointing at files that aren't in the repo (`docs/setup.md`, `screenshots/demo.png`, "
        "the LICENSE the README mentions): the docs were generated for a project that doesn't exist.",
    fix="Point the link at the real file, add the file, or remove the link.",
    skip_tests=True,
)
def check_dead_link(ctx: FileContext) -> Iterable[Hit]:
    """Relative links/images in READMEs and repo docs must resolve to a file or folder."""
    if not _REPO_DOC.match(ctx.path.name):
        return
    root = getattr(ctx.project, "root", ctx.path.parent)
    fenced = _fenced(ctx)
    for n, line in enumerate(ctx.lines, 1):
        if n in fenced or ctx.is_comment(n):
            continue
        for match in _LINK.finditer(line):
            target = unquote((match.group(1) or match.group(2)).split("#")[0].split("?")[0])
            if not target or _EXTERNAL.match(target):
                continue
            base = root if target.startswith("/") else ctx.path.parent
            if not (base / target.lstrip("/")).exists():
                yield Hit(n, line.strip(), target)
        if _LICENSE_MENTION.search(line) and not any(root.glob("LICEN[CS]E*")) \
                and not any(ctx.path.parent.glob("LICEN[CS]E*")):
            yield Hit(n, line.strip(), "LICENSE (no license file in the repo)")


# ─── The generated-README look ───────────────────────────────────────────────

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(?:\*\*|__)?(.)")
_STATUS_MARK = re.compile("[✅❌✔✖☑⚠⭕⛔]")   # ✅ ❌ ✔ ✖ ☑ ⚠ ⭕ ⛔


@rule(
    "docs/emoji-headings", "Emoji on every heading and bullet", SEVERITY_LOW, CATEGORY_DOCS, DOC,
    why="`## 🚀 Features`, `## 🛠️ Tech Stack`, `- ✨ **Fast**`: an emoji in front of every heading and "
        "feature bullet is the default look of AI-generated READMEs.",
    fix="Drop the decorative emoji; keep plain headings. ✅/❌ as real status markers in a table are fine.",
    skip_tests=True, aggregate_per_file=True,
)
def check_emoji_headings(ctx: FileContext) -> Iterable[Hit]:
    """Flag docs where most headings, or many bullets, start with a decorative emoji."""
    headings, decorated, bullets = 0, [], []
    for n, text in _prose(ctx):
        heading = _HEADING.match(text)
        if heading:
            headings += 1
            title = heading.group(1).strip().lstrip("*_[")
            if title and EMOJI_RE.match(title) and not _STATUS_MARK.match(title):
                decorated.append(Hit(n, text.strip()))
            continue
        bullet = _BULLET.match(text)
        if bullet and EMOJI_RE.match(bullet.group(1)) and not _STATUS_MARK.match(bullet.group(1)):
            bullets.append(Hit(n, text.strip()))
    # Emoji feature bullets alone are an old human habit (Vite's README); decorated headings are the tell.
    if len(decorated) >= 3 and len(decorated) >= 0.4 * headings:
        yield from sorted(decorated + bullets, key=lambda h: h.line)


_HYPE = re.compile(
    r"\brevolutioni[sz](?:e|es|ing)\b|\bgame[- ]chang(?:er|ing)\b|\bcutting[- ]edge\b"
    r"|\bnext[- ]gen(?:eration)?\b|\bseamless(?:ly)?\b|\beffortless(?:ly)?\b|\bsupercharg(?:e|es|ed|ing)\b"
    r"|\bunlock(?:s|ing)? the (?:full )?(?:power|potential)\b|\bharness(?:es|ing)? the power\b"
    r"|\belevate (?:your|the)\b|\bempower(?:s|ing)? (?:you|users|developers|teams|businesses)\b"
    r"|\b(?:blazing(?:ly)?|lightning)[- ]fast\b|\brobust,? (?:and )?scalable\b|\benterprise[- ]grade\b"
    r"|\bworld[- ]class\b|\bin today'?s (?:fast[- ]paced|digital|modern)\b|\blook no further\b"
    r"|\bto the next level\b|\ba testament to\b|\bdelves?\b|\bcomprehensive (?:solution|suite|platform)\b"
    r"|\bmade with (?:❤️|❤|♥|love)|\bhappy coding\b|\bdon'?t forget to (?:star|give)\b",
    re.IGNORECASE)


_LINK_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\[[^\]]+\]\([^)]+\)")
_LINK_TEXT = re.compile(r"\[[^\]]*\]\([^)]*\)|<a\b[^>]*>.*?</a>", re.IGNORECASE)   # names, not claims


@rule(
    "docs/hype-copy", "Generated hype in the docs", SEVERITY_LOW, CATEGORY_DOCS, DOC,
    why="\"Seamless\", \"cutting-edge\", \"unlock the power of\", \"Made with ❤️\", \"Happy coding!\": stacked "
        "up, they're the voice of an AI README, and they tell the reader nothing.",
    fix="Say what the project does and for whom, with specifics (\"Converts Figma frames to React components\").",
    skip_tests=True, aggregate_per_file=True,
)
def check_hype_copy(ctx: FileContext) -> Iterable[Hit]:
    """Flag docs with three or more hype phrases (one "seamless" is just a word)."""
    hits, phrases = [], 0
    for n, text in _prose(ctx):
        if _LINK_BULLET.match(text):
            continue  # awesome-lists quote other projects' taglines
        found = [m.group(0) for m in _HYPE.finditer(_LINK_TEXT.sub("", text))]
        if found:
            phrases += len(found)
            hits.append(Hit(n, ctx.lines[n - 1].strip(), ", ".join(found)))
    if phrases >= 3:
        yield from hits


# ─── Scope ───────────────────────────────────────────────────────────────────

_DOC_DIRS = {"docs", "doc", ".github"}


def _is_project_doc(ctx: FileContext) -> bool:
    """The project's own docs: READMEs anywhere, root-level Markdown, and files under docs/ or .github/.

    Markdown elsewhere (blog posts, CMS content, other projects' READMEs kept as data) isn't
    documentation of this repo.
    """
    parts = ctx.rel.split("/")
    if "template" in ctx.rel.lower():
        return False  # issue/PR templates are fill-in-the-blanks by design
    return _REPO_DOC.match(parts[-1]) is not None or len(parts) == 1 or bool(_DOC_DIRS & set(parts[:-1]))


def _only_project_docs(rule_id: str) -> None:
    original = RULES[rule_id].check

    def check(ctx: FileContext) -> Iterable[Hit]:
        return original(ctx) if _is_project_doc(ctx) else ()

    RULES[rule_id].check = check


for _rule_id in [r for r in RULES if r.startswith("docs/")]:
    _only_project_docs(_rule_id)
