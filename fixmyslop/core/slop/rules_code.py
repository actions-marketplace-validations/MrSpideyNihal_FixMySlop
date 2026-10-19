"""
Code slop rules — the tells of AI-written logic: elided code, placeholder
implementations, narrating comments, swallowed errors, debug leftovers.
"""
import ast
import re
from typing import Iterable

from fixmyslop.core.slop.rule import (
    FileContext, Hit, rule, line_rule,
    KIND_CODE, KIND_PYTHON, KIND_SCRIPT, KIND_TS, KIND_MARKUP, KIND_ANY, KIND_DOC,
)
from fixmyslop.core.slop.rules_ui import EMOJI_RE
from fixmyslop.macros import (
    SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, SEVERITY_INFO,
    CATEGORY_AI_SMELL,
)

CODE = (KIND_CODE,)

# ─── Agent artifacts ─────────────────────────────────────────────────────────

_ELIDED_WORDED = re.compile(
    r"\.\.\.\s*(?:rest of|the rest|existing|remaining|other|previous|same as|unchanged)\b"
    r"|\b(?:rest of|remainder of) (?:the )?(?:code|implementation|file|component|function|class|methods?)"
    r"\s*(?:remains?|stays?|is|unchanged|as before)"
    r"|\b(?:existing|previous|original) (?:code|implementation|logic) (?:here|unchanged|remains|goes here)\b"
    r"|\bkeep (?:the )?existing (?:code|implementation|logic)\b",
    re.IGNORECASE)
_ELIDED_BARE = re.compile(r"^\s*(?:#|//|/\*|\{/\*|<!--)\s*\.\.\.\s*(?:\*/\}?|-->)?\s*$")


@rule(
    "slop/elided-code", "AI-elided code (\"... existing code\")", SEVERITY_HIGH, CATEGORY_AI_SMELL, (KIND_ANY,),
    why="Comments like '// ... existing code ...' are what a model writes when it skips part of a "
        "file. When they land in the file itself, real code has been deleted.",
    fix="Restore the missing code from version control (git diff / git log -p) and remove the marker.",
)
def check_elided_code(ctx: FileContext) -> Iterable[Hit]:
    """Worded elision markers anywhere in comments; a bare `// ...` only between lines of real code."""
    if KIND_DOC in ctx.kinds:
        return
    def neighbour_is_code(n: int, step: int) -> bool:
        m = n + step
        while 1 <= m <= len(ctx.lines) and not ctx.line_text(m).strip():
            m += step
        return 1 <= m <= len(ctx.lines) and not ctx.is_comment(m)

    for n in sorted(ctx.comment_lines):
        line = ctx.line_text(n)
        match = _ELIDED_WORDED.search(line)
        if match:
            yield Hit(n, line.strip(), match.group(0))
        elif _ELIDED_BARE.match(line) and neighbour_is_code(n, -1) and neighbour_is_code(n, 1):
            yield Hit(n, line.strip(), "...")


line_rule(
    "slop/placeholder-impl", "\"In a real app…\" placeholder logic", SEVERITY_MEDIUM,
    CATEGORY_AI_SMELL, CODE,
    why="The model admitted this isn't the real implementation ('in a real application you would…', "
        "'simplified for demo'). It was shipped anyway.",
    fix="Implement the real behavior the comment describes, or raise a clear error / open an issue if "
        "it can't be done now. Delete the hedging comment either way.",
    pattern=r"\bin a real(?:[- ]world)? (?:app|application|implementation|world|project|scenario|system|codebase)\b"
            r"|\bin (?:a )?production,? (?:you|we)(?:'d| would| should)\b"
            r"|\bfor (?:demo|demonstration|illustration|simplicity|the sake of simplicity)(?: purposes)?\b"
            r"|\b(?:simplified|placeholder|dummy|mock|stub|hardcoded|hard-coded) (?:implementation|logic|response|version)\b"
            r"|\breplace (?:this )?with (?:your|the|an?) (?:actual|real|own)\b"
            r"|\b(?:implementation|logic|code) (?:goes|would go) here\b"
            r"|\badd (?:your|more|actual|real) (?:logic|implementation|code|validation) here\b"
            r"|\bTODO:?\s*(?:add (?:real|actual|proper)|replace (?:with|this) (?:with )?(?:real|actual))\b"
            r"|\bthis would (?:normally|typically)\b"
            r"|\bkeep(?:ing)? (?:it|this|things) simple for now\b"
            r"|\bfor now,? (?:we |I )?(?:just |simply )?(?:return|hardcode|hard-code|assume|use a (?:fixed|static|dummy))\b"
            r"|\byou (?:would|might|may|could|should) (?:want to |need to )?(?:add|implement|replace|use a real)\b",
    scope="comment", skip_tests=True,
)

line_rule(
    "slop/placeholder-credential", "Placeholder credential in code", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, CODE,
    why="'your-api-key-here' style values mean config was never wired up; the code silently fails "
        "or, worse, someone pastes a real key into source.",
    fix="Read the value from configuration / environment variables and fail loudly when it's missing.",
    pattern=r"(?:[=:(,]|\breturn)\s*[\"'](?:your[-_ ]?(?:api[-_ ]?key|secret|token|password)[-_ ]?(?:here)?|YOUR_[A-Z_]*(?:KEY|TOKEN|SECRET)"
            r"|sk-x{4,}|x{8,}|changeme|replace[-_]me|<your[-_ ][^>]+>)[\"']",
    skip_tests=True,
)


@rule(
    "slop/stub-function", "Documented stub that does nothing", SEVERITY_MEDIUM,
    CATEGORY_AI_SMELL, (KIND_PYTHON,),
    why="A documented function whose body is just `pass` is an unfinished AI stub — callers get "
        "None and no error.",
    fix="Implement the function, or raise NotImplementedError with a clear message so failures are loud.",
    skip_tests=True,
)
def check_stub_function(ctx: FileContext) -> Iterable[Hit]:
    """Flag documented pass-only functions outside abstract classes/protocols."""
    tree = _parse_python(ctx)
    if tree is None:
        return
    abstract_classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = {_name_of(b) for b in node.bases}
            if base_names & {"ABC", "Protocol", "ABCMeta", "Interface"}:
                abstract_classes.update(id(child) for child in node.body)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(node) in abstract_classes or node.name.startswith("__") or "noop" in node.name.lower():
            continue
        decorators = {_name_of(d) for d in node.decorator_list}
        if decorators & {"abstractmethod", "overload", "abstractproperty"}:
            continue
        body = list(node.body)
        has_doc = bool(body) and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str)
        rest = body[1:] if has_doc else body
        if len(rest) != 1 or not _is_noop(rest[0]):
            continue
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        source = "\n".join(ctx.lines[node.lineno - 1:end])
        if has_doc or re.search(r"\bTODO\b|implement", source, re.IGNORECASE):
            yield Hit(node.lineno, ctx.line_text(node.lineno).strip(), f"`{node.name}()` has no body")


# ─── Error handling ──────────────────────────────────────────────────────────

_BROAD = {"Exception", "BaseException"}


@rule(
    "slop/swallowed-exception", "Exception swallowed silently", SEVERITY_HIGH, CATEGORY_AI_SMELL, (KIND_PYTHON,),
    why="`except Exception: pass` hides every bug, including the ones you're about to ship. AI adds "
        "it to make errors 'go away'.",
    fix="Catch the specific exception you expect and handle it; otherwise let it propagate. At "
        "minimum log it with context (logger.exception).",
)
def check_swallowed_exception(ctx: FileContext) -> Iterable[Hit]:
    """Flag broad except handlers whose body does nothing."""
    tree = _parse_python(ctx)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_broad(node.type):
            if all(_is_noop(stmt) or isinstance(stmt, ast.Continue) for stmt in node.body):
                yield Hit(node.lineno, ctx.line_text(node.lineno).strip())


@rule(
    "slop/log-and-continue", "Exception printed, then ignored", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_PYTHON,),
    why="Catching Exception, printing it, and carrying on leaves the program in an unknown state "
        "and the error (without a traceback) buried in stdout.",
    fix="Catch a specific exception, or re-raise after logging with logger.exception(...).",
    skip_tests=True,
)
def check_log_and_continue(ctx: FileContext) -> Iterable[Hit]:
    """Flag broad handlers whose only statement is a print/log call."""
    tree = _parse_python(ctx)
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not _is_broad(node.type):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Expr):
            continue
        call = node.body[0].value
        if isinstance(call, ast.Call):
            name = _name_of(call.func)
            if name == "print":
                yield Hit(node.lineno, ctx.line_text(node.lineno).strip())


line_rule(
    "slop/bare-except", "Bare except:", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, (KIND_PYTHON,),
    why="A bare except also catches KeyboardInterrupt and SystemExit, making programs impossible to stop.",
    fix="Catch a specific exception class (or at least `except Exception:`).",
    pattern=r"^\s*except\s*:", fixable=True,
)

line_rule(
    "slop/empty-catch", "Empty catch block", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, (KIND_SCRIPT,),
    why="An empty catch silently drops errors (failed requests, bad JSON) and the UI just shows nothing.",
    fix="Handle the error: surface it to the user, log it, or rethrow. Don't catch what you can't handle.",
    pattern=r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}|\.catch\(\s*\(\s*\w*\s*\)\s*=>\s*\{\s*\}\s*\)",
)

# ─── Debug leftovers ─────────────────────────────────────────────────────────

line_rule(
    "slop/debugger", "Debugger statement left in", SEVERITY_HIGH, CATEGORY_AI_SMELL, CODE,
    why="A debugger/breakpoint call halts execution in production.",
    fix="Delete the statement.",
    pattern=r"^\s*debugger\s*;?\s*$|^\s*breakpoint\(\)\s*$|\bpdb\.set_trace\(\)|^\s*import i?pdb\b",
    flags=0, fixable=True,
)

line_rule(
    "slop/console-log", "console.log left in UI code", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_MARKUP,),
    why="Debug logging left in components leaks data to the browser console and clutters it.",
    fix="Remove the log, or use the project's logger behind a dev-only flag.",
    pattern=r"\bconsole\.log\(", flags=0, skip_tests=True, fixable=True,
)

line_rule(
    "slop/emoji-log", "Emoji in log output", SEVERITY_LOW, CATEGORY_AI_SMELL, CODE,
    why="'🚀 Server started!' / '✅ Done' logs are an AI habit; they break log parsers, grep and "
        "some terminals.",
    fix="Use plain-text log messages with structured context.",
    pattern=r"(?:\bconsole\.(?:log|info|warn|error|debug)|(?<![.\w])print|\blogger\.\w+|\blogging\.\w+)\s*\([^\n]*"
            + EMOJI_RE.pattern,
    flags=0,
)

# ─── TypeScript escape hatches ───────────────────────────────────────────────

line_rule(
    "slop/ts-any", "`any` type", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_TS,),
    why="`any` switches off the type checker — the reason you're using TypeScript.",
    fix="Use the real type, `unknown` + narrowing, or a generic.",
    pattern=r":\s*any\b(?=\s*(?:[;,)=>\]|&}]|$))|\bas any\b|<any>|\bany\[\]", flags=0,
    skip_tests=True, project_threshold=5, aggregate_per_file=True,
)

line_rule(
    "slop/ts-ignore", "@ts-ignore suppression", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_TS,),
    why="@ts-ignore hides a type error instead of fixing it — and keeps hiding new ones on that line.",
    fix="Fix the type error. If truly needed, use @ts-expect-error with a reason.",
    pattern=r"@ts-ignore\b", flags=0, scope="comment",
)

line_rule(
    "slop/blanket-suppression", "Whole-file lint/type suppression", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, CODE,
    why="Disabling the linter or type checker for an entire file is how agents make red squiggles "
        "disappear. Every future bug in the file is now invisible too.",
    fix="Remove the blanket disable and fix (or narrowly suppress) the individual findings.",
    pattern=r"@ts-nocheck\b|/\*\s*eslint-disable\s*\*/|^\s*//\s*eslint-disable\s*$"
            r"|#\s*(?:ruff|flake8):\s*noqa\s*$"
            r"|#\s*pylint:\s*skip-file",
    flags=0, scope="comment",
)

# ─── Comment slop ────────────────────────────────────────────────────────────

_NARRATE_VERBS = (
    "initialize", "initialise", "create", "creates", "define", "defines", "declare", "set", "sets",
    "import", "return", "returns", "loop", "iterate", "check", "checks", "call", "calls", "increment",
    "get", "gets", "add", "adds", "print", "prints", "log", "store", "stores", "update", "updates",
    "assign", "instantiate", "append", "render", "export", "handle", "convert", "save", "fetch",
)
_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "for", "with", "in", "on", "from", "into", "this",
    "that", "it", "its", "is", "be", "new", "variable", "function", "value", "values", "list",
    "array", "object", "if", "each", "over", "through", "by", "as", "at", "all", "our", "we",
}
_COMMENT_PREFIX = re.compile(r"^\s*(?:#+|//+|/\*+|\{/\*|\*)\s*")
_PRAGMA = re.compile(
    r"noqa|type:|pragma|eslint|@ts-|prettier|istanbul|fixmyslop|TODO|FIXME|HACK|XXX|NOTE|WARN|"
    r"because|since|otherwise|workaround|why|must|never|don't|do not|careful|important",
    re.IGNORECASE,
)
_IMPORT_NARRATION = re.compile(
    r"^(?:import|importing) (?:the )?(?:necessary|required|needed)? ?(?:libraries|modules|packages|dependencies)",
    re.IGNORECASE,
)


def _words(text: str) -> set[str]:
    """Split identifiers/prose into lowercase stems for overlap scoring."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    result = set()
    for word in re.findall(r"[A-Za-z]+", text):
        w = word.lower()
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        result.add(w)
    return result


@rule(
    "slop/narrating-comment", "Comment narrates the next line", SEVERITY_LOW, CATEGORY_AI_SMELL, CODE,
    why="'# Initialize the user list' above `user_list = []` adds noise, not information. It's the "
        "most common comment pattern in AI-written code.",
    fix="Delete comments that restate the code. Keep comments that explain *why*.",
    fixable=True,
)
def check_narrating_comment(ctx: FileContext) -> Iterable[Hit]:
    """Flag short verb-led comments whose words mostly repeat the next code line."""
    for n, line in enumerate(ctx.lines, 1):
        if not ctx.is_comment(n) or line.strip().startswith(("/*", "{/*", "*")):
            continue
        body = _COMMENT_PREFIX.sub("", line).strip().rstrip("*/}").strip().rstrip(".:")
        if not body or _PRAGMA.search(body) or len(body.split()) > 9:
            continue
        if re.search(r"[{};=]|\bfrom\s+[\"']|\w\(.*\)", body):
            continue  # commented-out code / usage example, not narration
        if _IMPORT_NARRATION.match(body):
            yield Hit(n, line.strip())
            continue
        first = body.split()[0].lower()
        if first not in _NARRATE_VERBS:
            continue
        next_code = ""
        for m in range(n + 1, min(n + 3, len(ctx.lines)) + 1):
            if ctx.line_text(m).strip() and not ctx.is_comment(m):
                next_code = ctx.line_text(m)
                break
        if not next_code:
            continue
        content = _words(body) - _STOPWORDS - {first, first.rstrip("s")}
        if not content:
            continue
        overlap = content & _words(next_code)
        if len(overlap) / len(content) >= 0.6:
            yield Hit(n, line.strip())


@rule(
    "slop/step-comments", "\"Step 1, Step 2\" tutorial comments", SEVERITY_LOW, CATEGORY_AI_SMELL, CODE,
    why="Numbered step comments read like a tutorial transcript, not maintained code.",
    fix="Delete the step labels; extract well-named functions if the steps are genuinely distinct.",
)
def check_step_comments(ctx: FileContext) -> Iterable[Hit]:
    """Flag files with 3+ 'Step N:' comments.

    Plain numbered comments ('// 1. Load .env (lowest priority)') are not flagged: humans use them to
    document precedence and fallback order, and they look the same as AI narration.
    """
    steps = [
        n for n in ctx.comment_lines
        if re.match(r"^\W*step\s+\d+\s*[:.)\-]", _COMMENT_PREFIX.sub("", ctx.line_text(n)), re.IGNORECASE)
    ]
    if len(steps) >= 3:
        for n in sorted(steps):
            yield Hit(n, ctx.line_text(n).strip())


line_rule(
    "slop/ai-voice", "AI voice in comments", SEVERITY_INFO, CATEGORY_AI_SMELL, CODE,
    why="'seamlessly', 'robust', 'leverage', 'delve' — corporate filler that marks text as generated.",
    fix="Say what the code does in plain words, or delete the comment.",
    pattern=r"\b(?:delve|seamless(?:ly)?|leverag(?:e|es|ing)|meticulous(?:ly)?|effortless(?:ly)?"
            r"|cutting[- ]edge|state[- ]of[- ]the[- ]art|robust and|comprehensive (?:solution|approach))\b",
    scope="comment", project_threshold=3,
)

# ─── helpers ─────────────────────────────────────────────────────────────────

_UNPARSED = object()


def _parse_python(ctx: FileContext):
    """Parse a Python file once per context; None on syntax errors."""
    tree = getattr(ctx, "_py_tree", _UNPARSED)
    if tree is _UNPARSED:
        try:
            tree = ast.parse(ctx.text)
        except (SyntaxError, ValueError):
            tree = None
        ctx._py_tree = tree
    return tree


def _name_of(node) -> str:
    """Return the trailing identifier of a Name/Attribute/Call node."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_broad(node) -> bool:
    """True for bare except, Exception, BaseException, or tuples containing them."""
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_name_of(elt) in _BROAD for elt in node.elts)
    return _name_of(node) in _BROAD


def _is_noop(stmt) -> bool:
    """True for pass, `...`, or a bare `return` / `return None`."""
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if isinstance(stmt, ast.Return) and (stmt.value is None or (
            isinstance(stmt.value, ast.Constant) and stmt.value.value is None)):
        return True
    return False
