"""
Dependency rules — imports of packages that don't exist (hallucinated by a
model, a.k.a. slopsquatting bait) or that aren't declared in the manifest.
"""
import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

from fixmyslop.core.slop.rule import FileContext, Hit, rule, KIND_PYTHON, KIND_SCRIPT
from fixmyslop.macros import SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, CATEGORY_DEPS
from fixmyslop.utils.logger import get_logger

logger = get_logger(__name__)

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", ".nuxt", "target", ".idea", ".vscode", "coverage", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", ".tox", "site-packages", ".svelte-kit", ".turbo",
}

# Distribution name → import names, for packages whose names differ.
_PY_ALIASES = {
    "pyyaml": ["yaml"], "beautifulsoup4": ["bs4"], "pillow": ["PIL"], "scikit_learn": ["sklearn"],
    "scikit_image": ["skimage"], "opencv_python": ["cv2"], "opencv_python_headless": ["cv2"],
    "opencv_contrib_python": ["cv2"], "python_dotenv": ["dotenv"], "fpdf2": ["fpdf"],
    "python_dateutil": ["dateutil"], "pyjwt": ["jwt"], "python_jose": ["jose"],
    "python_multipart": ["multipart"], "pymupdf": ["fitz"], "pycryptodome": ["Crypto"],
    "pycryptodomex": ["Cryptodome"], "protobuf": ["google"], "google_cloud_storage": ["google"],
    "google_generativeai": ["google"], "google_genai": ["google"], "attrs": ["attr", "attrs"],
    "msgpack_python": ["msgpack"], "typing_extensions": ["typing_extensions"],
    "pywin32": ["win32api", "win32con", "win32gui", "pywintypes"], "psycopg2_binary": ["psycopg2"],
    "psycopg_binary": ["psycopg"], "mysqlclient": ["MySQLdb"], "pyserial": ["serial"],
    "pyzmq": ["zmq"], "pygithub": ["github"], "discord.py": ["discord"], "discord_py": ["discord"],
    "python_telegram_bot": ["telegram"], "ruamel.yaml": ["ruamel"], "ruamel_yaml": ["ruamel"],
    "setuptools": ["setuptools", "pkg_resources"], "typer_slim": ["typer"], "tomli": ["tomli"],
    "markdown": ["markdown"], "faiss_cpu": ["faiss"], "faiss_gpu": ["faiss"],
    "sentence_transformers": ["sentence_transformers"], "llama_cpp_python": ["llama_cpp"],
    "pyqt5": ["PyQt5"], "pyqt6": ["PyQt6"], "pyside6": ["PySide6"], "websocket_client": ["websocket"],
    "email_validator": ["email_validator"], "python_magic": ["magic"], "docx": ["docx"],
    "python_docx": ["docx"], "python_pptx": ["pptx"], "grpcio": ["grpc"], "pyopenssl": ["OpenSSL"],
    "ipython": ["IPython"], "jinja2": ["jinja2"], "markupsafe": ["markupsafe"],
}
_PY_ALWAYS_OK = {
    "__future__", "__main__", "pkg_resources", "_typeshed", "typing_extensions",
    "setuptools", "pip", "wheel", "distutils",
}

_NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto",
    "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https",
    "inspector", "module", "net", "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls", "trace_events", "tty",
    "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib", "test",
}

_JS_IMPORT = re.compile(
    r"""(?:^|[^\w.$])(?:import\s+(?:type\s+)?(?:[\w*{}\s,$]+\s+from\s+)?|export\s+(?:type\s+)?[\w*{}\s,$]+\s+from\s+|"""
    r"""require\s*\(\s*|import\s*\(\s*)["']([^"'\n]+)["']""",
    re.MULTILINE,
)


def _norm(name: str) -> str:
    """Normalise a package/import name for comparison."""
    return re.sub(r"[-_.]+", "_", name).lower()


def _req_name(spec: str) -> str:
    """Extract the distribution name from a requirement string."""
    spec = spec.strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return match.group(1) if match else ""


class DependencyIndex:
    """Project-wide knowledge needed to judge imports: manifests, local modules, aliases."""

    def __init__(self, root: Path, online: bool = False):
        """Walk the project once and index manifests and local module names."""
        self.root = root
        self.online = online
        self.py_declared: set[str] = set()
        self.py_has_manifest = False
        self.py_local: set[str] = set()
        self.js_manifests: list[Path] = []
        self.js_declared: set[str] = set()
        self.js_alias_prefixes: list[str] = ["@/", "~/", "~", "#", "$lib", "$app", "$env", "virtual:", "astro:"]
        self.top_level_dirs: set[str] = set()
        self._registry_cache: dict[str, Optional[bool]] = {}
        self._dist_map = None
        self._index()

    # ── indexing ────────────────────────────────────────────────────────────
    def _index(self):
        """Collect manifests and local module names (bounded walk)."""
        root = self.root if self.root.is_dir() else self.root.parent
        seen = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            current = Path(dirpath)
            if current == root:
                self.top_level_dirs.update(dirnames)
            for d in dirnames:
                self.py_local.add(d)
            for name in filenames:
                seen += 1
                path = current / name
                if name.endswith(".py"):
                    self.py_local.add(name[:-3])
                elif name == "package.json":
                    self._read_package_json(path)
                elif name in ("tsconfig.json", "jsconfig.json"):
                    self._read_tsconfig(path)
                elif re.match(r"requirements.*\.(txt|in)$", name) or name in ("constraints.txt",):
                    self._read_requirements(path)
                elif name == "pyproject.toml":
                    self._read_pyproject(path)
                elif name in ("setup.py", "setup.cfg", "Pipfile", "environment.yml"):
                    self._read_loose_manifest(path)
            if seen > 50000:
                logger.warning("Dependency index stopped early (very large tree)")
                break

    def _declare_py(self, dist: str):
        """Record a declared Python distribution and its known import names."""
        if not dist:
            return
        key = _norm(dist)
        self.py_declared.add(key)
        for alias in _PY_ALIASES.get(key, []):
            self.py_declared.add(_norm(alias))
        for prefix in ("python_", "py_", "django_", "flask_"):
            if key.startswith(prefix) and prefix in ("python_", "py_"):
                self.py_declared.add(key[len(prefix):])
        if key.endswith("_python"):
            self.py_declared.add(key[: -len("_python")])

    def _read_requirements(self, path: Path):
        """Parse requirements.txt-style files."""
        self.py_has_manifest = True
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith("-"):
                    self._declare_py(_req_name(line))
        except OSError:
            pass

    def _read_pyproject(self, path: Path):
        """Parse pyproject.toml dependencies (PEP 621, Poetry, PEP 735)."""
        self.py_has_manifest = True
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        data = None
        try:
            import tomllib  # Python 3.11+
            data = tomllib.loads(text)
        except Exception:
            data = None
        if data is None:
            # Fallback: every quoted requirement-looking string counts as declared.
            for spec in re.findall(r"[\"']([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*[<>=!~;@\"']", text):
                self._declare_py(spec)
            for key in re.findall(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=", text, re.MULTILINE):
                self._declare_py(key)
            return
        for spec in (data.get("build-system", {}) or {}).get("requires", []) or []:
            self._declare_py(_req_name(spec))
        project = data.get("project", {})
        self._declare_py(project.get("name", ""))
        for spec in project.get("dependencies", []) or []:
            self._declare_py(_req_name(spec))
        for group in (project.get("optional-dependencies", {}) or {}).values():
            for spec in group:
                self._declare_py(_req_name(spec))
        for group in (data.get("dependency-groups", {}) or {}).values():
            for spec in group:
                if isinstance(spec, str):
                    self._declare_py(_req_name(spec))
        poetry = data.get("tool", {}).get("poetry", {})
        self._declare_py(poetry.get("name", ""))
        for key in (poetry.get("dependencies", {}) or {}):
            self._declare_py(key)
        for group in (poetry.get("group", {}) or {}).values():
            for key in (group.get("dependencies", {}) or {}):
                self._declare_py(key)
        for key in (poetry.get("dev-dependencies", {}) or {}):
            self._declare_py(key)

    def _read_loose_manifest(self, path: Path):
        """Best-effort parse of setup.py / setup.cfg / Pipfile / environment.yml."""
        self.py_has_manifest = True
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        for spec in re.findall(r"[\"']([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;][^\"']*)?[\"']", text):
            self._declare_py(spec)
        for spec in re.findall(r"^\s*-?\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~]|$|=\s*[\"'{*])", text, re.MULTILINE):
            self._declare_py(spec)

    def _read_package_json(self, path: Path):
        """Collect dependency names (and workspace package names) from package.json."""
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return
        self.js_manifests.append(path)
        if isinstance(data.get("name"), str):
            self.js_declared.add(data["name"])
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            deps = data.get(key) or {}
            if isinstance(deps, dict):
                self.js_declared.update(deps.keys())
        imports = data.get("imports") or {}
        if isinstance(imports, dict):
            self.js_alias_prefixes.extend(k.rstrip("*") for k in imports)

    def _read_tsconfig(self, path: Path):
        """Collect path alias prefixes from tsconfig/jsconfig (JSON with comments)."""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        text = re.sub(r"(?<![:\"])//[^\n]*", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            data = json.loads(text)
        except ValueError:
            return
        paths = (data.get("compilerOptions") or {}).get("paths") or {}
        for key in paths:
            prefix = key.rstrip("*")
            if prefix:
                self.js_alias_prefixes.append(prefix)

    # ── python ──────────────────────────────────────────────────────────────
    def _installed_dists_for(self, module: str, spec) -> list[str]:
        """Distribution names providing an installed top-level module.

        Reads only top_level.txt / dist-info names in the module's own site
        directory — importlib.metadata.packages_distributions() takes seconds.
        """
        origin = getattr(spec, "origin", None) or next(iter(getattr(spec, "submodule_search_locations", None) or []), None)
        if not origin:
            return []
        site = Path(origin).parent
        if spec.submodule_search_locations:
            site = Path(next(iter(spec.submodule_search_locations))).parent
        if self._dist_map is None:
            self._dist_map = {}
        if str(site) not in self._dist_map:
            mapping: dict[str, list[str]] = {}
            try:
                for info in site.glob("*.dist-info"):
                    dist = info.name.split("-")[0]
                    top = info / "top_level.txt"
                    names = [dist]
                    if top.exists():
                        names += top.read_text(encoding="utf-8", errors="ignore").split()
                    for name in names:
                        mapping.setdefault(name, []).append(dist)
            except OSError:
                pass
            self._dist_map[str(site)] = mapping
        return self._dist_map[str(site)].get(module, [])

    def classify_python(self, module: str) -> Optional[str]:
        """Return None (fine), 'undeclared', or 'phantom' for a top-level module name."""
        key = _norm(module)
        if module in sys.stdlib_module_names or module in _PY_ALWAYS_OK:
            return None
        if module in self.py_local or key in self.py_declared:
            return None
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            return "phantom"
        if not self.py_has_manifest:
            return None
        if any(_norm(d) in self.py_declared for d in self._installed_dists_for(module, spec)):
            return None
        return "undeclared"

    # ── javascript ──────────────────────────────────────────────────────────
    def js_package_of(self, spec: str) -> Optional[str]:
        """Map an import specifier to its npm package name, or None if it's local/builtin/aliased."""
        spec = spec.split("?", 1)[0].split("!", 1)[-1]
        if not spec or spec.startswith((".", "/", "node:", "bun:", "data:", "http:", "https:", "jsr:", "npm:")):
            return None
        if any(spec.startswith(p) for p in self.js_alias_prefixes if p):
            return None
        parts = spec.split("/")
        if spec.startswith("@"):
            if len(parts) < 2:
                return None
            name = "/".join(parts[:2])
        else:
            name = parts[0]
            if name in _NODE_BUILTINS:
                return None
            if name in self.top_level_dirs or (self.root / "src" / name).exists():
                return None  # baseUrl-style absolute import of a local folder
        return name

    def classify_js(self, package: str, file_path: Path) -> Optional[str]:
        """Return None, 'undeclared', or 'phantom' for an npm package name."""
        if not self.js_manifests or package in self.js_declared:
            return None
        if package.startswith("@types/"):
            return None
        for parent in [file_path.parent, *file_path.parents]:
            if (parent / "node_modules" / package).exists():
                return "undeclared"
            if parent == self.root or parent == parent.parent:
                break
        return "phantom"

    # ── registry check (opt-in, --online) ───────────────────────────────────
    def exists_on_registry(self, name: str, ecosystem: str) -> Optional[bool]:
        """Ask PyPI / npm whether a package exists. None when the network is unavailable."""
        key = f"{ecosystem}:{name}"
        if key in self._registry_cache:
            return self._registry_cache[key]
        result: Optional[bool] = None
        import requests
        if ecosystem == "pypi":
            url = f"https://pypi.org/simple/{name}/"
        else:
            url = "https://registry.npmjs.org/" + name.replace("/", "%2F")
        for attempt in range(2):   # one retry for a network blip
            try:
                # Only the status matters: stream so popular packages' multi-MB metadata isn't downloaded.
                with requests.get(url, timeout=8, stream=True,
                                  headers={"Accept": "application/vnd.npm.install-v1+json"}) as resp:
                    if resp.status_code == 404:
                        result = False
                    elif resp.status_code == 200:
                        result = True
                break
            except requests.RequestException as e:
                logger.debug("Registry lookup failed for %s (attempt %d): %s", name, attempt + 1, e)
        self._registry_cache[key] = result
        return result


_PHANTOM_WHY = (
    "This package is not in the standard library, not a local module, not declared in any manifest, "
    "and not installed. AI models regularly invent package names — and attackers register those "
    "names with malware ('slopsquatting')."
)
_PHANTOM_FIX = (
    "Check whether the package really exists and is the one you meant. Replace it with a real, "
    "maintained package (or the standard library) and declare it in the manifest — don't just "
    "`pip install`/`npm install` the name."
)


def _phantom_message(index: DependencyIndex, name: str, ecosystem: str) -> tuple[str, str]:
    """Return (severity, message) for a phantom import, consulting the registry if enabled."""
    registry = "PyPI" if ecosystem == "pypi" else "npm"
    if index.online:
        exists = index.exists_on_registry(name, ecosystem)
        if exists is False:
            return SEVERITY_CRITICAL, f"`{name}` does not exist on {registry} — hallucinated package"
        if exists is True:
            return SEVERITY_HIGH, (f"`{name}` exists on {registry} but is not declared or installed — "
                                   "verify it's the package you intended")
    return SEVERITY_HIGH, f"`{name}` is not declared, installed, or local"


@rule(
    "deps/phantom-import", "Package may not exist", SEVERITY_HIGH, CATEGORY_DEPS,
    (KIND_PYTHON, KIND_SCRIPT), why=_PHANTOM_WHY, fix=_PHANTOM_FIX,
)
def check_phantom_import(ctx: FileContext) -> Iterable[Hit]:
    """Flag imports that resolve to nothing known."""
    for kind, name, line in _imports(ctx):
        if kind == "phantom":
            severity, message = _phantom_message(ctx.project.deps, name, "pypi" if ctx.has(KIND_PYTHON) else "npm")
            yield Hit(line, ctx.line_text(line).strip(), message, severity=severity)


@rule(
    "deps/undeclared-import", "Dependency not in manifest", SEVERITY_MEDIUM, CATEGORY_DEPS,
    (KIND_PYTHON, KIND_SCRIPT),
    why="The package works on this machine only because it happens to be installed (often as a "
        "transitive dependency). Fresh installs and CI will break.",
    fix="Add the package to requirements.txt / pyproject.toml / package.json with a version constraint.",
    skip_tests=True,
)
def check_undeclared_import(ctx: FileContext) -> Iterable[Hit]:
    """Flag imports that are installed but not declared."""
    for kind, name, line in _imports(ctx):
        if kind == "undeclared":
            yield Hit(line, ctx.line_text(line).strip(), f"`{name}` is not declared in the project manifest")


def _imports(ctx: FileContext) -> list[tuple[str, str, int]]:
    """Classify every import in a file once: [(kind, name, line)] for non-fine imports."""
    cached = getattr(ctx, "_dep_findings", None)
    if cached is not None:
        return cached
    results: list[tuple[str, str, int]] = []
    index: Optional[DependencyIndex] = getattr(getattr(ctx, "project", None), "deps", None)
    if index is not None:
        if ctx.has(KIND_PYTHON):
            results = _python_imports(ctx, index)
        elif ctx.has(KIND_SCRIPT):
            results = _js_imports(ctx, index)
    ctx._dep_findings = results
    return results


def _python_imports(ctx: FileContext, index: DependencyIndex) -> list[tuple[str, str, int]]:
    """Classify absolute imports in a Python file, skipping guarded (try/except) imports."""
    try:
        tree = ast.parse(ctx.text)
    except (SyntaxError, ValueError):
        return []
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in node.body:
                for sub in ast.walk(child):
                    guarded.add(id(sub))
    seen: set[str] = set()
    results = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for full in names:
            top = full.split(".")[0]
            if id(node) in guarded or top in seen:
                continue
            seen.add(top)
            kind = index.classify_python(top)
            if kind == "undeclared" and ctx.is_test:
                continue
            if kind:
                results.append((kind, top, node.lineno))
    return results


def _js_imports(ctx: FileContext, index: DependencyIndex) -> list[tuple[str, str, int]]:
    """Classify bare-specifier imports in a JS/TS file."""
    seen: set[str] = set()
    results = []
    for match in _JS_IMPORT.finditer(ctx.code_text):
        line = ctx.line_of(match.start(1))
        if ctx.is_comment(line):
            continue
        package = index.js_package_of(match.group(1))
        if not package or package in seen:
            continue
        seen.add(package)
        kind = index.classify_js(package, ctx.path)
        if kind:
            results.append((kind, package, line))
    return results
