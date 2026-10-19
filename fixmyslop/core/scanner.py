"""
Scanner — walks a repo/folder, collects supported files,
respects .gitignore, skips binary, minified, generated and oversized files.
"""
import fnmatch
from pathlib import Path
from typing import Iterator, Callable, Optional, Iterable
from fixmyslop.macros import SUPPORTED_EXTENSIONS, DEFAULT_MAX_FILE_SIZE_KB, is_scannable
from fixmyslop.utils.file_utils import read_file_safe, parse_gitignore
from fixmyslop.utils.git_utils import list_files
from fixmyslop.utils.logger import get_logger

logger = get_logger(__name__)


class Scanner:
    """
    Walks a directory tree and yields file paths ready for analysis.
    Uses `git ls-files` inside repositories (exact .gitignore semantics) and a
    .gitignore-aware walk elsewhere. Skips node_modules/venv/__pycache__/etc.
    """

    SKIP_DIRS = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "env", "dist", "build", ".next", ".nuxt", "target",
        ".idea", ".vscode", "coverage", ".mypy_cache", ".ruff_cache",
        ".svelte-kit", ".turbo", ".output", ".vercel", ".fixmyslop", "vendor",
        ".pytest_cache", ".tox", "site-packages",
    }
    SKIP_FILE_PATTERNS = ("*.min.js", "*.min.css", "*.bundle.js", "*.chunk.js", "*-lock.*", "*.d.ts", "*.map")

    def __init__(
        self,
        repo_path: str,
        max_file_size_kb: int = DEFAULT_MAX_FILE_SIZE_KB,
        extensions: Optional[set] = None,
        only_files: Optional[Iterable[Path]] = None,
    ):
        """Initialize scanner with repo path or file path and filters.

        only_files restricts the scan to an explicit list (e.g. changed files).
        """
        self.repo_path = Path(repo_path)
        self.max_file_size_kb = max_file_size_kb
        self.extensions = extensions or SUPPORTED_EXTENSIONS
        self._only_files = [Path(p) for p in only_files] if only_files is not None else None
        self._is_single_file = self.repo_path.is_file()
        if self._is_single_file:
            self._gitignore_patterns = []
        else:
            self._gitignore_patterns = parse_gitignore(self.repo_path / ".gitignore")
        self._cache: Optional[list[Path]] = None
        self.skipped_minified: list[Path] = []

    def scan(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Iterator[tuple[Path, str]]:
        """
        Yield (file_path, file_content) for each supported file.
        Calls progress_callback(current, total, filename) if provided.
        """
        all_files = self.files()
        total = len(all_files)
        logger.info("Found %d files to scan in %s", total, self.repo_path)

        for i, path in enumerate(all_files):
            content = read_file_safe(path, self.max_file_size_kb)
            if content is None:
                continue
            if not self._is_single_file and _looks_minified(path, content):
                logger.info("Skipping minified/bundled file: %s", path)
                self.skipped_minified.append(path)
                continue
            if progress_callback:
                progress_callback(i + 1, total, path.name)
            yield path, content

    def files(self) -> list[Path]:
        """Return (and cache) the sorted list of files to scan."""
        if self._cache is None:
            self._cache = sorted(set(self._collect_files()))
        return self._cache

    def _accept(self, item: Path, rel_parts: tuple) -> bool:
        """Filter by skip dirs, extension, generated-file patterns and size."""
        if any(part in self.SKIP_DIRS for part in rel_parts[:-1]):
            return False
        if not is_scannable(item, self.extensions):
            return False
        if any(fnmatch.fnmatch(item.name, pattern) for pattern in self.SKIP_FILE_PATTERNS):
            return False
        try:
            if item.stat().st_size > self.max_file_size_kb * 1024:
                logger.debug("Skipping oversized file: %s", item)
                return False
        except OSError:
            return False
        return True

    def _collect_files(self) -> Iterator[Path]:
        """Walk the repo and collect all supported, non-ignored files."""
        if self._only_files is not None:
            base = self.repo_path.resolve()
            for path in self._only_files:
                try:
                    parts = path.resolve().relative_to(base).parts
                except ValueError:
                    parts = (path.name,)
                if path.is_file() and self._accept(path, parts):
                    yield path
            return

        if self._is_single_file:
            path = self.repo_path
            if is_scannable(path, self.extensions):
                try:
                    if path.stat().st_size <= self.max_file_size_kb * 1024:
                        yield path
                except OSError:
                    pass
            return

        git_files = list_files(self.repo_path)
        if git_files is not None:
            for item in git_files:
                rel = item.relative_to(self.repo_path)
                if self._accept(item, rel.parts):
                    yield item
            return

        for item in self.repo_path.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(self.repo_path)
            if self._is_gitignored(rel):
                continue
            if self._accept(item, rel.parts):
                yield item

    def _is_gitignored(self, rel: Path) -> bool:
        """Approximate .gitignore matching for non-git directories (no negation support)."""
        rel_posix = rel.as_posix()
        for raw in self._gitignore_patterns:
            if raw.startswith("!"):
                continue
            pattern = raw.lstrip("/")
            if pattern.endswith("/"):
                name = pattern.rstrip("/")
                if any(fnmatch.fnmatch(part, name) for part in rel.parts[:-1]):
                    return True
                continue
            if "/" in pattern:
                if fnmatch.fnmatch(rel_posix, pattern) or rel_posix.startswith(pattern + "/"):
                    return True
            elif any(fnmatch.fnmatch(part, pattern) for part in rel.parts):
                return True
        return False

    @property
    def file_count(self) -> int:
        """Return total number of scannable files without reading them."""
        return len(self.files())


def _looks_minified(path: Path, content: str) -> bool:
    """Bundled/minified JS or CSS: very long lines on average and at least one huge line.

    Never applies to HTML or components: AI-generated single-file pages often put all
    their CSS on a few long lines, and those are exactly the files worth scanning.
    """
    if path.suffix.lower() not in (".js", ".mjs", ".cjs", ".css"):
        return False
    if len(content) < 5000:
        return False
    lines = content.splitlines() or [content]
    return len(content) / len(lines) > 500 and max(len(line) for line in lines) > 2000
