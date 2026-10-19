"""
Rule model, file context, and registry for the deterministic slop engine.

Every rule is a pure function over a FileContext that yields Hit objects.
No LLM, no network — rules must be fast and reproducible so they can run
on every keystroke of an agent (Claude Code hook) and in CI.
"""
import bisect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from fixmyslop.macros import is_env_file, CATEGORY_UI_SLOP, CATEGORY_A11Y

# ─── File kinds ──────────────────────────────────────────────────────────────
# A file can have several kinds (a .tsx file is script + markup).
KIND_PYTHON = "python"
KIND_SCRIPT = "script"      # JS/TS family
KIND_TS = "ts"
KIND_MARKUP = "markup"      # renders UI: html, jsx, tsx, vue, svelte, astro
KIND_STYLE = "style"        # css family (or files with <style> blocks)
KIND_CODE = "code"          # any programming language
KIND_DOC = "doc"           # Markdown docs
KIND_ENV = "env"           # .env files
KIND_ANY = "any"

_EXT_KINDS = {
    ".py": {KIND_PYTHON, KIND_CODE},
    ".js": {KIND_SCRIPT, KIND_CODE},
    ".mjs": {KIND_SCRIPT, KIND_CODE},
    ".cjs": {KIND_SCRIPT, KIND_CODE},
    ".ts": {KIND_SCRIPT, KIND_TS, KIND_CODE},
    ".mts": {KIND_SCRIPT, KIND_TS, KIND_CODE},
    ".cts": {KIND_SCRIPT, KIND_TS, KIND_CODE},
    ".jsx": {KIND_SCRIPT, KIND_MARKUP, KIND_CODE},
    ".tsx": {KIND_SCRIPT, KIND_TS, KIND_MARKUP, KIND_CODE},
    ".vue": {KIND_SCRIPT, KIND_MARKUP, KIND_STYLE, KIND_CODE},
    ".svelte": {KIND_SCRIPT, KIND_MARKUP, KIND_STYLE, KIND_CODE},
    ".astro": {KIND_SCRIPT, KIND_MARKUP, KIND_STYLE, KIND_CODE},
    ".html": {KIND_MARKUP, KIND_STYLE},
    ".htm": {KIND_MARKUP, KIND_STYLE},
    ".css": {KIND_STYLE},
    ".scss": {KIND_STYLE},
    ".sass": {KIND_STYLE},
    ".less": {KIND_STYLE},
    ".md": {KIND_DOC},
    ".mdx": {KIND_DOC},
    ".markdown": {KIND_DOC},
}


def kinds_for(ext: str) -> frozenset:
    """Return the set of kinds for a file extension (always includes 'any')."""
    kinds = set(_EXT_KINDS.get(ext.lower(), {KIND_CODE}))
    kinds.add(KIND_ANY)
    return frozenset(kinds)


TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|__mocks__|spec|specs|fixtures|e2e|cypress|stories)/"
    r"|(_test|\.test|\.spec|_spec|\.stories)\.\w+$"
    r"|(^|/)(test_[^/]*|conftest)\.py$"
    r"|\.pyi$",
    re.IGNORECASE,
)

# Whole-line comment openers per family.
_HASH_LANGS = {".rb"}            # code languages whose comments start with #
_HASH_COMMENT = re.compile(r"^\s*#(?!!)")
_SLASH_COMMENT = re.compile(r"^\s*(//|\{/\*|\*(?=\s|$)|/\*)")
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"


@dataclass(frozen=True)
class Hit:
    """One rule match inside a file."""

    line: int
    evidence: str = ""
    message: str = ""
    end_line: int = 0
    severity: str = ""      # overrides the rule's default severity when set


# A `/` starts a regex literal (not division) after an operator, opening bracket or keyword.
# Not after < or >: in JSX those are tags (</p>, />), not comparisons.
_REGEX_CAN_START = re.compile(r"(?:[(,=:\[!&|?{};+\-*%~^]|\breturn|\btypeof|^)\s*$")


def mask_multiline_templates(text: str) -> str:
    """Blank the contents of JS/TS template literals that span lines, keeping offsets and newlines.

    `const sample = \\`import x from 'pkg'\\n...\\`` is text shown to users, not an import. Single-line
    templates (`\\`px-${pad}\\``) are left alone. Comments and quoted strings are skipped so a backtick
    inside them doesn't open a template.
    """
    out = list(text)
    n = len(text)
    i = 0
    stack: list[list] = []   # frames: ["tpl", start] or ["expr", brace_depth]

    def close_template(start: int, end: int) -> None:
        if "\n" in text[start:end]:
            for k in range(start + 1, end):
                if out[k] != "\n":
                    out[k] = " "

    while i < n:
        c = text[i]
        frame = stack[-1] if stack else None
        if frame is not None and frame[0] == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
                close_template(frame[1], i)
            elif c == "$" and text.startswith("${", i):
                stack.append(["expr", 0])
                i += 2
                continue
            i += 1
            continue
        # code (top level or inside ${ ... })
        if c == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if c == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "/" and _REGEX_CAN_START.search(text, max(0, i - 12), i):
            j, in_class = i + 1, False   # regex literal: /"/g must not open a string
            while j < n and text[j] != "\n" and (in_class or text[j] != "/"):
                if text[j] == "\\":
                    j += 1
                elif text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
            continue
        if c == "`":
            stack.append(["tpl", i])
        elif frame is not None and c == "{":
            frame[1] += 1
        elif frame is not None and c == "}":
            if frame[1] == 0:
                stack.pop()
            else:
                frame[1] -= 1
        i += 1
    return "".join(out)


class FileContext:
    """Everything a rule needs to know about one file."""

    def __init__(self, path: Path, rel: str, text: str, project=None):
        """Index the file for fast line lookups and comment detection."""
        self.path = path
        self.rel = rel.replace("\\", "/")
        self.text = text
        self.lines = text.splitlines()
        self.ext = path.suffix.lower()
        self.kinds = frozenset({KIND_ENV, KIND_ANY}) if is_env_file(path.name) else kinds_for(self.ext)
        self.is_test = bool(TEST_PATH_RE.search(self.rel))
        self.project = project
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        # Code rules read code_text/code_lines: in JS/TS, multi-line template strings (code samples shown
        # in the UI, HTML/SQL templates) are blanked out so their contents aren't mistaken for real code.
        self.code_text = mask_multiline_templates(text) if KIND_SCRIPT in self.kinds else text
        self.code_lines = self.code_text.splitlines() if self.code_text is not text else self.lines
        self.comment_lines = self._find_comment_lines()

    def _find_comment_lines(self) -> set[int]:
        """Return 1-based numbers of lines that are entirely comments."""
        result: set[int] = set()
        is_python = (KIND_PYTHON in self.kinds or KIND_ENV in self.kinds
                     or self.path.suffix.lower() in _HASH_LANGS)
        is_doc = KIND_DOC in self.kinds        # Markdown: only <!-- --> is a comment (# is a heading)
        in_block = False
        in_html = False
        for n, line in enumerate(self.code_lines, 1):
            stripped = line.strip()
            if in_html:
                result.add(n)
                if _HTML_COMMENT_CLOSE in stripped:
                    in_html = False
                continue
            if in_block:
                result.add(n)
                if "*/" in stripped:
                    in_block = False
                continue
            if stripped.startswith(_HTML_COMMENT_OPEN):
                result.add(n)
                if _HTML_COMMENT_CLOSE not in stripped:
                    in_html = True
                continue
            if is_doc:
                continue
            if is_python:
                if _HASH_COMMENT.match(line):
                    result.add(n)
                continue
            if _SLASH_COMMENT.match(line):
                result.add(n)
                if (stripped.startswith("/*") or stripped.startswith("{/*")) and "*/" not in stripped:
                    in_block = True
        return result

    def is_comment(self, line_no: int) -> bool:
        """True when the given 1-based line is a whole-line comment."""
        return line_no in self.comment_lines

    def line_of(self, offset: int) -> int:
        """Convert a character offset into a 1-based line number."""
        return bisect.bisect_right(self._line_starts, offset)

    def joined(self, lines: list[str]) -> str:
        """`lines` joined with plain newlines (no \\r), cached per list, for whole-file prefilter searches."""
        cache = self.__dict__.setdefault("_joined", {})
        key = id(lines)
        if key not in cache:
            cache[key] = "\n".join(lines)
        return cache[key]

    def line_text(self, line_no: int) -> str:
        """Return the raw text of a 1-based line ('' when out of range)."""
        if 1 <= line_no <= len(self.lines):
            return self.lines[line_no - 1]
        return ""

    def has(self, kind: str) -> bool:
        """True when this file is of the given kind."""
        return kind in self.kinds


CheckFn = Callable[[FileContext], Iterable[Hit]]


@dataclass
class Rule:
    """Metadata + check function for one slop rule."""

    id: str
    title: str
    severity: str
    category: str
    kinds: frozenset
    why: str
    fix: str
    check: Optional[CheckFn] = None
    skip_tests: bool = False
    project_threshold: int = 0     # >0: only report when the project total reaches this
    once_per_project: bool = False
    fixable: bool = False
    aggregate_per_file: bool = False   # collapse many hits in one file into a single finding

    def applies_to(self, ctx: FileContext) -> bool:
        """True when this rule should run on the given file."""
        if self.skip_tests and ctx.is_test:
            return False
        return bool(self.kinds & ctx.kinds)


RULES: dict[str, Rule] = {}


def rule(id: str, title: str, severity: str, category: str, kinds: Iterable[str],
         why: str, fix: str, **options) -> Callable[[CheckFn], CheckFn]:
    """Decorator registering a check function as a rule."""
    def decorator(fn: CheckFn) -> CheckFn:
        RULES[id] = Rule(
            id=id, title=title, severity=severity, category=category,
            kinds=frozenset(kinds), why=why, fix=fix, check=fn, **options,
        )
        return fn
    return decorator


def line_rule(id: str, title: str, severity: str, category: str, kinds: Iterable[str],
              why: str, fix: str, pattern: str, flags: int = re.IGNORECASE,
              scope: str = "code", unless_inside: Optional[str] = None, **options) -> None:
    """Register a rule that matches a regex against individual lines.

    scope: "code" skips comment lines, "comment" only checks comment lines,
    "all" checks every line.
    unless_inside: a regex for spans (e.g. placeholder="...") where a match doesn't count.
    """
    regex = re.compile(pattern, flags)
    # One search over the whole file first: most rules match nothing in most files, and skipping them
    # is most of the scan time. MULTILINE keeps ^/$ per line, so any per-line match is also found here.
    prefilter = re.compile(pattern, flags | re.MULTILINE)
    shield = re.compile(unless_inside, re.IGNORECASE) if unless_inside else None
    reads_markup = category in (CATEGORY_UI_SLOP, CATEGORY_A11Y)   # class strings live in templates too

    def check(ctx: FileContext) -> Iterable[Hit]:
        lines = ctx.lines if reads_markup else ctx.code_lines
        if not prefilter.search(ctx.joined(lines)):
            return
        for n, line in enumerate(lines, 1):
            is_comment = ctx.is_comment(n)
            if scope == "code" and is_comment:
                continue
            if scope == "comment" and not is_comment:
                continue
            shielded = [m.span() for m in shield.finditer(line)] if shield else []
            for match in regex.finditer(line):
                if any(a <= match.start() < b for a, b in shielded):
                    continue
                yield Hit(n, line.strip(), match.group(0).strip())
                break

    rule(id, title, severity, category, kinds, why, fix, **options)(check)


def find_tag_end(text: str, start: int) -> int:
    """Return the index of the '>' closing a tag opened at `start`.

    Aware of quotes and JSX braces, so `onClick={() => x}` does not end the tag.
    Returns -1 when the tag never closes.
    """
    depth = 0
    quote = ""
    i = start + 1
    limit = min(len(text), start + 4000)
    while i < limit:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == ">" and depth == 0:
            return i
        i += 1
    return -1


def iter_tags(ctx: FileContext, names: Iterable[str]) -> Iterable[tuple[str, str, int, int]]:
    """Yield (tag_name, attrs_text, start_offset, end_offset) for opening tags."""
    pattern = re.compile(r"<(" + "|".join(names) + r")(?=[\s/>])")
    for match in pattern.finditer(ctx.text):
        end = find_tag_end(ctx.text, match.start())
        if end == -1:
            continue
        line = ctx.line_of(match.start())
        if ctx.is_comment(line):
            continue
        yield match.group(1), ctx.text[match.end():end], match.start(), end
