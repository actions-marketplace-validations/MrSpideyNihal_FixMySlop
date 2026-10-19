"""
Security rules — the vulnerabilities vibe-coded apps ship most often:
committed secrets, secrets exposed to the browser bundle, string-built SQL,
wildcard CORS, disabled TLS verification, raw HTML injection.
"""
import re
from typing import Iterable

from fixmyslop.core.slop.rule import (
    FileContext, Hit, rule, line_rule,
    KIND_CODE, KIND_SCRIPT, KIND_MARKUP, KIND_ANY, KIND_ENV,
)
from fixmyslop.macros import SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, CATEGORY_SECURITY, is_env_template
from fixmyslop.utils.git_utils import is_ignored

_LEAKED_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-(?:proj-|ant-(?:api\d+-)?)?[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{35}|[sr]k_live_[0-9a-zA-Z]{20,}|glpat-[0-9A-Za-z_\-]{20}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----)"
)


def redact(secret: str) -> str:
    """Keep a short recognisable prefix of a secret, hide the rest."""
    if secret.startswith("-----BEGIN"):
        return secret
    return secret[:6] + "…[redacted]"


def redact_secrets(text: str) -> str:
    """Redact every provider-format key found in a piece of text."""
    if not text:
        return text
    return _LEAKED_KEY.sub(lambda m: redact(m.group(0)), text)


@rule(
    "sec/leaked-key", "Leaked API key / private key", SEVERITY_CRITICAL, CATEGORY_SECURITY, (KIND_ANY,),
    why="A provider key or private key committed to source is compromised the moment it's pushed — "
        "bots scrape GitHub for these within minutes.",
    fix="Remove the key from the code, ROTATE it with the provider (it's already leaked), and load it "
        "from an environment variable / secret manager.",
    skip_tests=True,
)
def check_leaked_key(ctx: FileContext) -> Iterable[Hit]:
    """Flag provider key formats; evidence is redacted so reports never re-leak the key."""
    for n, line in enumerate(ctx.lines, 1):
        match = _LEAKED_KEY.search(line)
        if match and KIND_ENV in ctx.kinds:
            assign = _ENV_ASSIGN.match(line)
            if assign and _PUBLIC_BY_DESIGN.search(assign.group(1)):
                continue  # e.g. VITE_..._MAPS_BROWSER_KEY: a browser key, restricted by referrer
        if match:
            key = match.group(0)
            evidence = line.strip().replace(key, redact(key))
            yield Hit(n, evidence, redact(key))


_CRED_ASSIGN = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*?(?:api_?key|apikey|secret|password|passwd|auth_?token|access_?token"
    r"|private_?key|client_?secret)[A-Za-z0-9_]*)[\"']?\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUE = re.compile(
    r"your|xxx|example|changeme|placeholder|dummy|sample|fake|redacted|\*\*\*|<|\$\{|\{\{|%\(|"
    r"process\.env|environ|getenv|replace|todo|insert|enter",
    re.IGNORECASE,
)
_NON_SECRET_NAME = re.compile(
    r"(?:_url|_uri|_name|_field|_header|_env|_path|_label|_placeholder|_param|_type|_key_id|_length"
    r"|_prefix|_regex|_pattern|_message|_error|_input|_column)$",
    re.IGNORECASE,
)


@rule(
    "sec/hardcoded-credential", "Hardcoded password / secret", SEVERITY_HIGH, CATEGORY_SECURITY,
    (KIND_CODE, KIND_MARKUP),
    why="Credentials in source end up in git history, logs, and every clone of the repo.",
    fix="Move the value to an environment variable or secret manager and read it at runtime; rotate "
        "it if it was ever real.",
    skip_tests=True,
)
def check_hardcoded_credential(ctx: FileContext) -> Iterable[Hit]:
    """Flag `password = "..."`-style assignments with non-placeholder values."""
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        for match in _CRED_ASSIGN.finditer(line):
            name, value = match.group(1), match.group(2)
            if _NON_SECRET_NAME.search(name) or _PLACEHOLDER_VALUE.search(value):
                continue
            if value.lower() == name.lower() or value.isalpha() and len(value) < 16:
                continue
            if _LEAKED_KEY.search(value):
                continue  # reported by sec/leaked-key
            yield Hit(n, line.strip().replace(value, "***"), name)
            break


_ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_ENV_SECRET_NAME = re.compile(
    r"key|secret|token|passw|pwd|credential|private|service_role|dsn|webhook", re.IGNORECASE)
_URL_WITH_PASSWORD = re.compile(r"://[^:/@\s]+:[^@\s]+@(?!localhost|127\.0\.0\.1|0\.0\.0\.0|db\b|postgres\b)")
# Shipped to the browser by design (Supabase anon/publishable keys, Stripe pk_, restricted Maps keys):
# committing them leaks nothing. Real secrets behind a public prefix are sec/public-env-secret's job.
_PUBLIC_BY_DESIGN = re.compile(
    r"publishable|anon|browser|public_key|site_key|measurement_id|tracking_id|project_id", re.IGNORECASE)
_PUBLIC_PREFIX = re.compile(r"^(?:NEXT_PUBLIC|VITE|REACT_APP|EXPO_PUBLIC|PUBLIC|NUXT_PUBLIC|GATSBY)_")
_PUBLIC_VALUE = ("sb_publishable_", "pk_live_", "pk_test_")


def _env_public(name: str, value: str) -> bool:
    """An env var meant to be public: browser-prefixed, or named/shaped like a publishable key."""
    return bool(_PUBLIC_PREFIX.match(name) or _PUBLIC_BY_DESIGN.search(name)) or value.startswith(_PUBLIC_VALUE)


@rule(
    "sec/env-secret", "Real secrets in an env file git will commit", SEVERITY_HIGH, CATEGORY_SECURITY, (KIND_ENV,),
    why="This .env file holds real keys and git doesn't ignore it, so it's already committed or the next "
        "`git add .` will commit it. AI-built projects often ship without a .gitignore entry for .env.",
    fix="Add `.env*` and `!.env.example` to .gitignore, run `git rm --cached <file>`, keep placeholder "
        "values in .env.example, and rotate every key that was ever pushed.",
)
def check_env_secret(ctx: FileContext) -> Iterable[Hit]:
    """One finding per env file: real-looking secret values in a file git doesn't ignore."""
    if is_env_template(ctx.path.name) or is_ignored(ctx.path):
        return
    secrets = []
    for n, line in enumerate(ctx.lines, 1):
        match = _ENV_ASSIGN.match(line)
        if not match or ctx.is_comment(n):
            continue
        name, value = match.group(1), match.group(2).strip("\"'")
        if len(value) < 8 or _PLACEHOLDER_VALUE.search(value) or _env_public(name, value):
            continue
        if _ENV_SECRET_NAME.search(name) or _URL_WITH_PASSWORD.search(value):
            secrets.append((n, name))
    if secrets:
        n, name = secrets[0]
        names = ", ".join(s[1] for s in secrets[:4]) + (" ..." if len(secrets) > 4 else "")
        yield Hit(n, f"{name}=***", f"{len(secrets)} secret value(s): {names}")


line_rule(
    "sec/public-env-secret", "Secret in a public env var", SEVERITY_HIGH, CATEGORY_SECURITY,
    (KIND_CODE, KIND_MARKUP, KIND_ENV),
    why="NEXT_PUBLIC_ / VITE_ / REACT_APP_ / EXPO_PUBLIC_ variables are inlined into the browser "
        "bundle. Anyone can read a secret, service-role key or LLM API key placed there.",
    fix="Move the call that needs this secret to a server route / edge function and keep the variable "
        "server-only (no public prefix). Rotate the key.",
    pattern=r"\b(?:NEXT_PUBLIC|VITE|REACT_APP|EXPO_PUBLIC|PUBLIC|NUXT_PUBLIC|GATSBY)_[A-Z0-9_]*"
            r"(?:SECRET|PRIVATE|PASSWORD|SERVICE_ROLE|OPENAI|ANTHROPIC|GEMINI|GROQ|STRIPE_SK|SK_LIVE)[A-Z0-9_]*",
    flags=0,
)

line_rule(
    "sec/sql-injection", "SQL built with string formatting", SEVERITY_HIGH, CATEGORY_SECURITY, (KIND_CODE,),
    why="Interpolating values into SQL lets attackers rewrite the query.",
    fix="Use parameterised queries / the ORM's query builder and pass values as parameters.",
    pattern=r"f[\"'](?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b[^\"']*\{"
            r"|[\"'](?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b[^\"']*[\"']\s*(?:\+|%)\s*[\w(]"
            r"|`(?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b[^`]*\$\{"
            r"|[\"'](?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b[^\"']*[\"']\s*\.format\(",
    flags=0, skip_tests=True,
)

line_rule(
    "sec/cors-wildcard", "CORS allows any origin", SEVERITY_MEDIUM, CATEGORY_SECURITY, (KIND_CODE,),
    why="`*` CORS (often with credentials) is the default AI scaffolds ship; it lets any website call "
        "your API from a user's browser.",
    fix="Allow only the specific origins that need access (read them from config).",
    pattern=r"Access-Control-Allow-Origin[\"']?\s*[,:]\s*[\"']\*[\"']|allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]"
            r"|\borigin\s*:\s*[\"']\*[\"']|\bcors\(\s*\)|CORS_ALLOW_ALL_ORIGINS\s*=\s*True|CORS\(\s*app\s*\)",
    skip_tests=True,
)

line_rule(
    "sec/tls-disabled", "TLS verification disabled", SEVERITY_HIGH, CATEGORY_SECURITY, (KIND_CODE,),
    why="Turning off certificate checks makes every request man-in-the-middle-able. Agents do it to "
        "'fix' SSL errors.",
    fix="Remove the override and fix the certificate chain (or point to the right CA bundle).",
    pattern=r"(?:[(,]\s*|^\s*)verify\s*=\s*False\b|rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED[\"']?\s*\]?\s*=\s*[\"']?0"
            r"|InsecureSkipVerify:\s*true|ssl\._create_unverified_context",
    flags=0, skip_tests=True,
)

line_rule(
    "sec/raw-html", "Unescaped HTML injection", SEVERITY_MEDIUM, CATEGORY_SECURITY, (KIND_SCRIPT, KIND_MARKUP),
    why="Rendering raw HTML from data (dangerouslySetInnerHTML, v-html, innerHTML) is the classic XSS hole.",
    fix="Render text normally. If HTML is truly required, sanitize it first (e.g. DOMPurify) right at "
        "the call site.",
    pattern=r"dangerouslySetInnerHTML|\bv-html\s*=|\{@html\s|\.innerHTML\s*=\s*(?![\"'`]\s*[\"'`])|\.outerHTML\s*=",
    flags=0, skip_tests=True,
)

line_rule(
    "sec/eval", "eval / new Function", SEVERITY_HIGH, CATEGORY_SECURITY, (KIND_SCRIPT, KIND_MARKUP),
    why="eval executes arbitrary code; with any user-controlled input it's remote code execution.",
    fix="Parse data with JSON.parse or a real parser; dispatch on known keys instead of building code.",
    pattern=r"(?<![\w.])eval\s*\(|\bnew Function\s*\(",
    flags=0, skip_tests=True,
)
