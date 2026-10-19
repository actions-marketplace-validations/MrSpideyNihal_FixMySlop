"""
Backend slop rules — what AI agents leave behind in application code beyond the
obvious placeholders: tests that test nothing, copy-pasted functions, wrappers
that only forward arguments, try/except blocks that only re-raise, and Go errors
that silently vanish.
"""
import ast
import hashlib
import re
from typing import Iterable

from fixmyslop.core.slop.rule import (
    FileContext, Hit, Rule, RULES, rule, line_rule,
    KIND_CODE, KIND_PYTHON, KIND_SCRIPT,
)
from fixmyslop.macros import SEVERITY_MEDIUM, SEVERITY_LOW, CATEGORY_AI_SMELL

_ASSERTISH = re.compile(r"assert|raises|expect|fail|verify|should|check|snapshot", re.IGNORECASE)


def _tree(ctx: FileContext):
    """Parse once per file (shared with rules_code's cache attribute)."""
    tree = getattr(ctx, "_py_tree", None)
    if tree is None and not hasattr(ctx, "_py_tree"):
        try:
            tree = ast.parse(ctx.text)
        except (SyntaxError, ValueError):
            tree = None
        ctx._py_tree = tree
    return tree


def _call_name(node) -> str:
    func = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


# ─── tests that test nothing ─────────────────────────────────────────────────

_SMOKE_NAME = re.compile(r"throw|raise|crash|error|exception|smoke|does_?not|doesnt|never|works|runs?\b|imports?\b",
                         re.IGNORECASE)


def _python_assert_helpers(tree: ast.AST) -> set:
    """Module-level helper functions that themselves assert (e.g. `def close(a, b): assert ...`)."""
    helpers = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test"):
            if _python_test_checks_something(node, set()):
                helpers.add(node.name)
    return helpers


def _python_test_checks_something(func: ast.AST, helpers: set) -> bool:
    """True when a test function contains a real assertion (directly or via an asserting helper)."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and _call_name(node).split(".")[-1] in helpers:
            return True
        if isinstance(node, ast.Assert):
            if isinstance(node.test, ast.Constant) and bool(node.test.value):
                continue                                   # `assert True` checks nothing
            return True
        if isinstance(node, ast.Call) and _ASSERTISH.search(_call_name(node)):
            return True
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.With) and any(_ASSERTISH.search(_call_name(i.context_expr))
                                              for i in node.items if isinstance(i.context_expr, ast.Call)):
            return True
    return False


_JS_TEST = re.compile(r"\b(?:it|test)(?:\.only|\.concurrent)?\s*\(\s*(['\"`])(?:(?!\1).)*\1\s*,\s*(?:async\s*)?"
                      r"(?:\([^)]*\)|\w+)\s*=>\s*\{")
_JS_CHECKS = re.compile(r"\bexpect\s*\(|\bassert\b|\.should\b|\btoMatch|\bt\.(?:is|deepEqual|true|throws)\(|\bthrow\b")


_JS_HELPER = re.compile(r"\b(?:const|let|function)\s+(\w+)\s*(?:=\s*(?:async\s*)?\([^)]*\)\s*=>|\([^)]*\)\s*\{)"
                        r"([^\n]*)")


_ASSERT_MODULES = r"(?:node:)?assert(?:/strict)?|chai|power-assert|expect|@jest/globals|vitest|uvu/assert|tap|ava"
_IMPORTED_NAMES = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*['\"](?:" + _ASSERT_MODULES + r")['\"]"
    r"|(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['\"](?:" + _ASSERT_MODULES + r")['\"]\s*\)"
    r"|import\s+(\w+)\s+from\s*['\"](?:" + _ASSERT_MODULES + r")['\"]"
    r"|import\s*\*\s*as\s+(\w+)\s+from\s*['\"](?:" + _ASSERT_MODULES + r")['\"]")


def _js_imported_asserts(text: str) -> set:
    """Functions imported from assertion libraries: `import { ok, strictEqual } from 'assert'`."""
    names = set()
    for match in _IMPORTED_NAMES.finditer(text):
        group = match.group(1) or match.group(2)
        if group:
            for part in group.split(","):
                alias = part.strip().split(" as ")[-1].split(":")[-1].strip()
                if re.fullmatch(r"\w+", alias or ""):
                    names.add(alias)
        else:
            names.add(match.group(3) or match.group(4))
    return {n for n in names if n not in ("describe", "it", "test", "beforeEach", "afterEach", "vi", "jest")}


def _js_assert_helpers(text: str) -> set:
    """Local helpers whose definition asserts (`const close = (a, b) => assert.ok(...)`)."""
    helpers = _js_imported_asserts(text)
    for match in _JS_HELPER.finditer(text):
        rest = match.group(2)
        if rest.rstrip().endswith("{"):
            rest = _brace_block(text, text.index("{", match.end() - 1))
        if _JS_CHECKS.search(rest):
            helpers.add(match.group(1))
    return helpers


def _brace_block(text: str, open_index: int) -> str:
    """Text inside the {...} block starting at open_index (best effort, string-aware)."""
    depth, quote, i = 0, "", open_index
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
        i += 1
    return text[open_index + 1:]


@rule(
    "slop/empty-test", "Test that checks nothing", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, (KIND_PYTHON, KIND_SCRIPT),
    why="A test with no assertion (or only `assert True`) always passes. AI agents write these to make a "
        "suite look complete; they give false confidence and catch no regressions.",
    fix="Assert the behavior the test name promises (return values, side effects, raised errors), or delete "
        "the test.",
)
def check_empty_test(ctx: FileContext) -> Iterable[Hit]:
    """Flag pytest/unittest and Jest/Vitest/Mocha tests without assertions."""
    if not ctx.is_test:
        return
    if ctx.has(KIND_PYTHON):
        tree = _tree(ctx)
        if tree is None:
            return
        helpers = _python_assert_helpers(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                decorators = {_call_name(d) for d in node.decorator_list}
                if any("skip" in d or "xfail" in d for d in decorators) or _SMOKE_NAME.search(node.name):
                    continue
                if not _python_test_checks_something(node, helpers):
                    yield Hit(node.lineno, ctx.line_text(node.lineno).strip(), f"`{node.name}` asserts nothing")
        return
    helpers = _js_assert_helpers(ctx.text)
    helper_call = re.compile(r"\b(?:" + "|".join(map(re.escape, helpers)) + r")\s*\(") if helpers else None
    for match in _JS_TEST.finditer(ctx.text):
        title = match.group(0)
        if _SMOKE_NAME.search(title.split(",")[0]):
            continue
        body = _brace_block(ctx.text, match.end() - 1)
        if not _JS_CHECKS.search(body) and not (helper_call and helper_call.search(body)):
            line = ctx.line_of(match.start())
            yield Hit(line, ctx.line_text(line).strip())


# ─── duplicated functions (project-wide) ─────────────────────────────────────

class _Anonymize(ast.NodeTransformer):
    """Rename variables/arguments so copies that only differ in naming still match."""

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node):
        node.arg = "_"
        node.annotation = None
        return node


def _python_fingerprints(ctx: FileContext):
    tree = _tree(ctx)
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(getattr(node.body[0], "value", None), ast.Constant)) else node.body
        statements = sum(1 for _ in ast.walk(ast.Module(body=body, type_ignores=[])) if isinstance(_, ast.stmt))
        if statements < 6:
            continue
        clone = _Anonymize().visit(ast.parse(ast.unparse(ast.Module(body=body, type_ignores=[]))))
        yield node.lineno, node.name, ast.dump(clone)


_JS_FUNC = re.compile(r"\bfunction\s+(\w+)\s*\([^)]*\)\s*\{|\b(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{")


def _js_fingerprints(ctx: FileContext):
    for match in _JS_FUNC.finditer(ctx.text):
        body = _brace_block(ctx.text, match.end() - 1)
        normalized = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.DOTALL)
        normalized = re.sub(r"\s+", "", normalized)
        if len(normalized) < 240 or normalized.count(";") + normalized.count("\n") < 4:
            continue
        yield ctx.line_of(match.start()), match.group(1) or match.group(2), normalized


@rule(
    "slop/duplicate-function", "Duplicated function", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, (KIND_PYTHON, KIND_SCRIPT),
    why="The same function body exists in several places (only names differ). Agents re-generate helpers "
        "instead of finding the existing one; every bug now has to be fixed N times.",
    fix="Keep one implementation in a shared module, import it everywhere else, and delete the copies.",
    skip_tests=True,
)
def check_duplicate_function(ctx: FileContext) -> Iterable[Hit]:
    """Record function fingerprints; the engine reports duplicates in finalize()."""
    project = getattr(ctx, "project", None)
    if project is None or not hasattr(project, "record"):
        return
    source = _python_fingerprints(ctx) if ctx.has(KIND_PYTHON) else _js_fingerprints(ctx)
    for line, name, fingerprint in source:
        digest = hashlib.blake2b(fingerprint.encode("utf-8"), digest_size=16).hexdigest()
        project.record("slop/duplicate-function", digest, (str(ctx.path), line, name, ctx.line_text(line).strip()))
    yield from ()


# ─── wrappers that only forward ──────────────────────────────────────────────

@rule(
    "slop/pointless-wrapper", "Function that only forwards its arguments", SEVERITY_LOW, CATEGORY_AI_SMELL,
    (KIND_PYTHON,),
    why="`def get_user(id): return fetch_user(id)` adds a name and a stack frame, not behavior. Layers of these "
        "are a hallmark of over-abstracted AI code.",
    fix="Call the underlying function directly and delete the wrapper (unless it's a deliberate public API).",
    skip_tests=True, project_threshold=3,
)
def check_pointless_wrapper(ctx: FileContext) -> Iterable[Hit]:
    tree = _tree(ctx)
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.decorator_list:
            continue
        if node.name.startswith("__"):
            continue
        body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(getattr(node.body[0], "value", None), ast.Constant)) else node.body
        if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
            continue
        call = body[0].value
        params = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
        args = [a.id for a in call.args if isinstance(a, ast.Name)]
        if not params or call.keywords or len(args) != len(call.args) or args != params:
            continue
        if "super" in _call_name(call):
            continue
        yield Hit(node.lineno, ctx.line_text(node.lineno).strip(), f"`{node.name}` only calls `{_call_name(call)}`")


# ─── try/except that only re-raises ─────────────────────────────────────────

@rule(
    "slop/catch-rethrow", "Catch block that only re-raises", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_PYTHON,),
    why="`except Exception as e: raise e` does nothing except add noise (and, with `raise e`, a confusing "
        "traceback line).",
    fix="Remove the try/except, or actually handle the error (add context, translate it, clean up).",
)
def check_catch_rethrow(ctx: FileContext) -> Iterable[Hit]:
    tree = _tree(ctx)
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or node.finalbody or node.orelse:
            continue
        for handler in node.handlers:
            if len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise):
                raised = handler.body[0].exc
                if raised is None or (isinstance(raised, ast.Name) and raised.id == handler.name):
                    if len(node.handlers) == 1:
                        yield Hit(handler.lineno, ctx.line_text(handler.lineno).strip())


line_rule(
    "slop/catch-rethrow-js", "Catch block that only rethrows", SEVERITY_LOW, CATEGORY_AI_SMELL, (KIND_SCRIPT,),
    why="`catch (e) { throw e }` adds nothing; the error would propagate anyway.",
    fix="Remove the try/catch, or handle the error (add context, recover, clean up).",
    pattern=r"\bcatch\s*\(\s*(\w+)\s*\)\s*\{\s*throw\s+\1\s*;?\s*\}",
)

# ─── other languages ─────────────────────────────────────────────────────────

line_rule(
    "slop/ignored-error", "Go error silently dropped", SEVERITY_MEDIUM, CATEGORY_AI_SMELL, (KIND_CODE,),
    why="`if err != nil {}` or `if err != nil { return nil }` makes the failure disappear: callers see "
        "success and the real error is lost.",
    fix="Return the error (wrapped with context: fmt.Errorf(\"...: %w\", err)) or handle it explicitly.",
    pattern=r"\bif\s+err\s*!=\s*nil\s*\{\s*(?:return\s+nil\s*)?\}",
)

line_rule(
    "slop/print-stacktrace", "Exception only printed (printStackTrace)", SEVERITY_LOW, CATEGORY_AI_SMELL,
    (KIND_CODE,),
    why="Catching an exception and only calling printStackTrace() hides it in stderr and carries on in an "
        "unknown state.",
    fix="Log it through the app's logger with context and rethrow, or handle it properly.",
    pattern=r"\bcatch\s*\([^)]*\)\s*\{\s*\w+\.printStackTrace\(\)\s*;?\s*\}",
    skip_tests=True,
)

def _only_in(rule_id: str, extensions: set):
    """Restrict a rule to files whose syntax it actually describes (never text inside Python strings)."""
    original = RULES[rule_id].check

    def check(ctx: FileContext) -> Iterable[Hit]:
        if ctx.ext in extensions:
            yield from original(ctx)

    RULES[rule_id].check = check


_BRACE_LANGS = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".vue", ".svelte", ".astro",
                ".java", ".cs", ".kt", ".kts", ".swift", ".php", ".scala", ".dart", ".groovy"}
# Empty catch blocks look the same in Java, C#, Kotlin, Swift, PHP, Dart and JS.
RULES["slop/empty-catch"].kinds = frozenset({KIND_CODE})
_only_in("slop/empty-catch", _BRACE_LANGS)
_only_in("slop/print-stacktrace", {".java", ".kt", ".kts", ".groovy"})
_only_in("slop/ignored-error", {".go"})
assert isinstance(RULES["slop/empty-catch"], Rule)
