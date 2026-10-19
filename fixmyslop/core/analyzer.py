"""
Analyzer — orchestrates static tools + LLM to produce issues for one file.
Takes a file path + content, returns a list of Issue objects.
"""
import json
import re
from pathlib import Path
from typing import Callable, Optional
from fixmyslop.core.issue import Issue
from fixmyslop.core.llm_client import LLMClient
from fixmyslop.core.static_tools import StaticToolRunner
from fixmyslop.core.slop.engine import SlopEngine
from fixmyslop.core.slop.rules_security import redact_secrets
from fixmyslop.core.prompts import SYSTEM_AUDIT_TURBO, SYSTEM_AUDIT_DEEP, AUDIT_USER_TEMPLATE
from fixmyslop.utils.logger import get_logger
from fixmyslop.macros import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SEVERITY_LOW,
    SEVERITY_INFO,
    SCAN_MODE_TURBO,
    SCAN_MODE_DEEP,
    TURBO_MAX_TOKENS,
    TURBO_MAX_ISSUES,
    TURBO_CHUNK_SIZE,
    DEEP_MAX_TOKENS,
    DEEP_MAX_ISSUES,
    DEEP_CHUNK_SIZE,
    CATEGORY_AI_SMELL,
)

logger = get_logger(__name__)

# Static-tool findings that duplicate a FixMySlop rule at the same line.
_EQUIVALENT_RULES = {
    "E722": "slop/bare-except",
    "T100": "slop/debugger",
    "S110": "slop/swallowed-exception",
    "B110": "slop/swallowed-exception",
    "S112": "slop/swallowed-exception",
    "B112": "slop/swallowed-exception",
    "S105": ("sec/hardcoded-credential", "sec/leaked-key"),
    "S106": ("sec/hardcoded-credential", "sec/leaked-key"),
    "B105": ("sec/hardcoded-credential", "sec/leaked-key"),
    "B106": ("sec/hardcoded-credential", "sec/leaked-key"),
    "S608": "sec/sql-injection",
    "B608": "sec/sql-injection",
    "S501": "sec/tls-disabled",
    "B501": "sec/tls-disabled",
}


def _repair_and_parse(raw: str) -> list:
    """Best-effort parse of LLM JSON output.

    Handles common LLM defects:
    - Markdown fences wrapping
    - Trailing commas before ] or }
    - Truncated JSON (model ran out of tokens mid-array)
    - Extra text before/after the JSON array
    - Mid-stream corruption (extracts valid objects individually)
    Returns a list of dicts, or [] on failure.
    """
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # 1. Try direct parse (clean JSON)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("issues", "findings", "results", "data"):
                value = result.get(key)
                if isinstance(value, list):
                    return value
        return []
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    # 3. Extract from first [ to last ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        # Fall back to salvaging standalone JSON objects if the model omitted array wrappers.
        objects = _extract_objects(text)
        if objects:
            logger.info("Salvaged %d valid objects from non-array JSON", len(objects))
            return objects
        return []
    text = text[start:end + 1]

    # 4. Fix trailing commas:  ,] or ,}
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 5. Try parsing the cleaned text
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass

    # 6. Truncated JSON — find last complete object and close the array
    last_brace = text.rfind("}")
    if last_brace > start:
        truncated = text[: last_brace + 1] + "]"
        truncated = re.sub(r",\s*([}\]])", r"\1", truncated)
        try:
            result = json.loads(truncated)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            pass

    # 7. Object-by-object extraction — salvage valid objects from corrupted array
    objects = _extract_objects(text)
    if objects:
        logger.info("Salvaged %d valid objects from corrupted JSON", len(objects))
    return objects


def _extract_objects(text: str) -> list[dict]:
    """Extract individual JSON objects from a malformed array using brace matching."""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            # Find matching closing brace using depth counting
            depth = 0
            in_string = False
            escape = False
            j = i
            while j < len(text):
                ch = text[j]
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = not in_string
                elif not in_string:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            # Found complete {...} block
                            candidate = text[i:j + 1]
                            try:
                                obj = json.loads(candidate)
                                if isinstance(obj, dict):
                                    results.append(obj)
                            except json.JSONDecodeError:
                                # Try fixing trailing commas in this object
                                fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                                try:
                                    obj = json.loads(fixed)
                                    if isinstance(obj, dict):
                                        results.append(obj)
                                except json.JSONDecodeError:
                                    pass  # Skip this corrupted object
                            i = j + 1
                            break
                j += 1
            else:
                break  # Unclosed brace, stop
        else:
            i += 1
    return results


class Analyzer:
    """
    Combines static analysis (Ruff, Bandit, Semgrep) with LLM-based
    deep analysis to produce a complete list of issues for a file.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None,
                 use_static_tools: bool = True,
                 max_retries: int = 2,
                 scan_mode: str = SCAN_MODE_TURBO,
                 progress_callback: Optional[Callable[[str], None]] = None,
                 slop_engine: Optional[SlopEngine] = None):
        """Initialize analyzer with optional LLM client, static tool toggle, and retry count.

        slop_engine enables the deterministic FixMySlop rules (no LLM needed).
        """
        self.llm = llm_client
        self.slop = slop_engine
        self.static = StaticToolRunner() if use_static_tools else None
        self.max_retries = max_retries
        self.scan_mode = scan_mode if scan_mode in {SCAN_MODE_TURBO, SCAN_MODE_DEEP} else SCAN_MODE_TURBO
        self._progress_callback = progress_callback

        if self.scan_mode == SCAN_MODE_TURBO:
            self.max_tokens = TURBO_MAX_TOKENS
            self.max_issues = TURBO_MAX_ISSUES
            self.chunk_size = TURBO_CHUNK_SIZE
            self.system_prompt = SYSTEM_AUDIT_TURBO
        else:
            self.max_tokens = DEEP_MAX_TOKENS
            self.max_issues = DEEP_MAX_ISSUES
            self.chunk_size = DEEP_CHUNK_SIZE
            self.system_prompt = SYSTEM_AUDIT_DEEP

        self._severity_rank = {
            SEVERITY_CRITICAL: 0,
            SEVERITY_HIGH: 1,
            SEVERITY_MEDIUM: 2,
            SEVERITY_LOW: 3,
            SEVERITY_INFO: 4,
        }

    def _emit_progress(self, message: str):
        """Emit analyzer progress to UI/CLI if a callback is configured."""
        if not self._progress_callback:
            return
        try:
            self._progress_callback(message)
        except Exception as e:  # a broken UI callback must never abort a scan
            logger.debug("Progress callback failed: %s", e)

    def prepare(self, paths: list[Path]):
        """Batch-run static tools over all files up front (one process per chunk)."""
        if self.static:
            try:
                self.static.prefetch(paths)
            except Exception as e:
                logger.warning("Static tool prefetch failed: %s", e)

    def finalize(self) -> list[Issue]:
        """Project-level findings that need every file to have been seen."""
        return self.slop.finalize() if self.slop else []

    def analyze_file(self, file_path: Path, content: str) -> list[Issue]:
        """
        Run all analysis passes on a single file.
        Returns deduplicated, merged list of Issue objects.
        """
        issues: list[Issue] = []
        language = self._detect_language(file_path)
        self._emit_progress(f"[{file_path.name}] Starting analysis...")

        # Pass 0: deterministic slop rules (instant, offline)
        if self.slop:
            try:
                issues.extend(self.slop.check_file(file_path, content))
            except Exception as e:
                logger.warning("Slop rules failed for %s: %s", file_path.name, e)

        # Pass 1: Static tools (fast, deterministic)
        if self.static:
            try:
                self._emit_progress(f"[{file_path.name}] Running static checks...")
                static_issues = self.static.run(file_path, content, language)
                issues.extend(static_issues)
                logger.debug(
                    "Static tools found %d issues in %s",
                    len(static_issues), file_path.name,
                )
                self._emit_progress(
                    f"[{file_path.name}] Static checks complete ({len(static_issues)} issues)."
                )
            except Exception as e:
                logger.warning("Static tools failed for %s: %s", file_path.name, e)
                self._emit_progress(f"[{file_path.name}] Static checks failed. Continuing...")

        # Pass 2: LLM deep analysis
        if self.llm:
            try:
                mode_name = "Turbo" if self.scan_mode == SCAN_MODE_TURBO else "Deep"
                self._emit_progress(f"[{file_path.name}] Running {mode_name} LLM analysis...")
                llm_issues = self._llm_analyze(file_path, content, language)
                issues.extend(llm_issues)
                logger.debug(
                    "LLM found %d issues in %s",
                    len(llm_issues), file_path.name,
                )
                self._emit_progress(
                    f"[{file_path.name}] LLM analysis complete ({len(llm_issues)} issues)."
                )
            except Exception as e:
                logger.warning("LLM analysis failed for %s: %s", file_path.name, e)
                self._emit_progress(f"[{file_path.name}] LLM analysis failed. Continuing...")

        deduped = self._deduplicate(issues)
        for issue in deduped:  # never copy a live secret into reports, SARIF or agent prompts
            issue.evidence = redact_secrets(issue.evidence)
        if self.scan_mode == SCAN_MODE_TURBO:
            deduped.sort(
                key=lambda issue: (
                    self._severity_rank.get(issue.severity, 99),
                    issue.line_start,
                    issue.rule_id,
                )
            )
            # Turbo caps only the (slow, noisy) LLM findings; deterministic ones are always kept.
            llm_issues = [i for i in deduped if i.source_tool == "llm"][:self.max_issues]
            final = [i for i in deduped if i.source_tool != "llm"] + llm_issues
            self._emit_progress(f"[{file_path.name}] Done ({len(final)} total issues).")
            return final

        self._emit_progress(f"[{file_path.name}] Done ({len(deduped)} total issues).")
        return deduped

    def _llm_analyze(self, file_path: Path, content: str, language: str) -> list[Issue]:
        """Send file to LLM for analysis, chunking only very large files."""
        OVERLAP = 200

        if len(content) <= self.chunk_size + OVERLAP:
            return self._llm_analyze_chunk(file_path, content, language)

        # Split into overlapping chunks for large files
        step = self.chunk_size - OVERLAP
        total_chunks = max(1, ((len(content) - 1) // step) + 1)
        self._emit_progress(
            f"[{file_path.name}] Large file detected — analyzing {total_chunks} chunks..."
        )
        logger.info(
            "File %s is %d chars — splitting into chunks of %d",
            file_path.name, len(content), self.chunk_size,
        )
        all_issues: list[Issue] = []
        offset = 0
        chunk_num = 0
        while offset < len(content):
            chunk = content[offset:offset + self.chunk_size]
            chunk_num += 1
            self._emit_progress(
                f"[{file_path.name}] LLM chunk {chunk_num}/{total_chunks}..."
            )
            logger.debug("Analyzing chunk %d (offset %d) of %s", chunk_num, offset, file_path.name)
            issues = self._llm_analyze_chunk(file_path, chunk, language)
            all_issues.extend(issues)
            offset += step

        return self._deduplicate(all_issues)[:self.max_issues]

    def _llm_analyze_chunk(self, file_path: Path, code: str, language: str) -> list[Issue]:
        """Analyze a single chunk of code with retry on parse failure."""
        for attempt in range(1, self.max_retries + 1):
            try:
                self._emit_progress(
                    f"[{file_path.name}] LLM pass {attempt}/{self.max_retries}..."
                )
                if attempt == 1:
                    if self.scan_mode == SCAN_MODE_TURBO:
                        prompt = (
                            f"Fast audit for this {language} code.\n"
                            f"File: {file_path}\n\n"
                            f"```{language}\n{code}\n```\n\n"
                            "Return ONLY a compact JSON array using schema fields: "
                            "line_start, line_end, severity, category, rule_id, "
                            "title, description, evidence.\n"
                            f"Maximum {self.max_issues} objects.\n"
                            "Prioritize CRITICAL and HIGH issues only.\n"
                            "Keep description under 12 words and evidence under 80 chars.\n"
                            "No markdown. No extra text."
                        )
                    else:
                        prompt = AUDIT_USER_TEMPLATE.format(
                            language=language,
                            file_path=str(file_path),
                            code=code,
                        )
                else:
                    prompt = (
                        f"Analyze this {language} code for bugs and security issues.\n"
                        f"File: {file_path}\n\n"
                        f"```{language}\n{code}\n```\n\n"
                        "You MUST respond with ONLY a JSON array. No text before or after.\n"
                        "Each object must have: line_start, line_end, severity, "
                        "category, rule_id, title, description, evidence.\n"
                        "If no issues found, return: []\n"
                        "Response:"
                    )
                    logger.info(
                        "Retry %d/%d for %s with simplified prompt",
                        attempt, self.max_retries, file_path.name,
                    )

                effective_max_tokens = self.max_tokens
                if self.scan_mode == SCAN_MODE_DEEP:
                    # Keep deep mode thorough while preventing multi-minute generation spikes.
                    effective_max_tokens = min(self.max_tokens, 1000)

                raw = self.llm.chat(
                    self.system_prompt,
                    prompt,
                    temperature=0.0 if self.scan_mode == SCAN_MODE_TURBO else 0.2,
                    max_tokens=effective_max_tokens,
                )
                data = _repair_and_parse(raw)

                if not data:
                    logger.warning(
                        "LLM returned no parseable issues for %s (len=%d), attempt %d",
                        file_path.name, len(raw), attempt,
                    )

                if data:
                    issues = self._build_issues(data, file_path)
                    return issues[:self.max_issues]

                if attempt < self.max_retries:
                    logger.info("LLM returned 0 issues on attempt %d, retrying...", attempt)
                    self._emit_progress(f"[{file_path.name}] LLM response unclear, retrying...")
                    continue
                return []

            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning(
                        "LLM analysis attempt %d failed for %s: %s — retrying",
                        attempt, file_path.name, e,
                    )
                    self._emit_progress(f"[{file_path.name}] LLM attempt failed, retrying...")
                    continue
                logger.warning("LLM analysis failed after %d attempts for %s: %s",
                               self.max_retries, file_path.name, e)
                return []

        return []

    def _build_issues(self, data: list[dict], file_path: Path) -> list[Issue]:
        """Convert parsed JSON dicts into Issue objects."""
        issues = []
        for item in data:
            if not isinstance(item, dict):
                continue
            issues.append(Issue(
                file_path=str(file_path),
                line_start=item.get("line_start", 0),
                line_end=item.get("line_end", 0),
                severity=item.get("severity", SEVERITY_HIGH),
                category=item.get("category", CATEGORY_AI_SMELL),
                rule_id=item.get("rule_id", "llm_detected"),
                title=item.get("title", "Issue detected"),
                description=item.get("description", ""),
                evidence=item.get("evidence", ""),
                source_tool="llm",
            ))
        return issues

    def _detect_language(self, file_path: Path) -> str:
        """Map file extension to language name for prompt context."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "javascript", ".tsx": "typescript", ".go": "go",
            ".rs": "rust", ".java": "java", ".cpp": "cpp",
            ".c": "c", ".cs": "csharp", ".rb": "ruby",
            ".php": "php", ".swift": "swift", ".kt": "kotlin",
        }
        return ext_map.get(file_path.suffix.lower(), "code")

    def _deduplicate(self, issues: list[Issue]) -> list[Issue]:
        """Remove near-duplicates: same file + line + rule, or a static-tool
        finding that repeats a FixMySlop rule on the same line."""
        own = {(i.line_start, i.rule_id) for i in issues if i.source_tool == "fixmyslop"}
        seen = set()
        unique = []
        for issue in issues:
            equivalent = _EQUIVALENT_RULES.get(issue.rule_id, ())
            if isinstance(equivalent, str):
                equivalent = (equivalent,)
            if any((issue.line_start, rule_id) in own for rule_id in equivalent):
                continue
            key = (issue.file_path, issue.line_start, issue.rule_id)
            if key not in seen:
                seen.add(key)
                unique.append(issue)
        return unique
