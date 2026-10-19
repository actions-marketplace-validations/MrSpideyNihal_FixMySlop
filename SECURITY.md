# Security policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub's private vulnerability reporting](https://github.com/MrSpideyNihal/FixMySlop/security/advisories/new)
rather than a public issue. You can expect an acknowledgement within 72 hours.

Useful details: the FixMySlop version (`fixmyslop version`), how you ran it (CLI, `fixmyslop ui`, MCP server,
Claude Code hook, GitHub Action), and a minimal reproduction.

## Supported versions

Security fixes go into the latest release on PyPI and npm.

## What FixMySlop does and doesn't do

- **No network access** by default. Scans are local and offline. The only network features are opt-in:
  `--online` (checks package names against PyPI/npm) and `--llm` (sends file contents to the model endpoint
  *you* configure).
- **Secrets stay redacted.** Provider keys found in your code are redacted in every output: terminal, JSON,
  SARIF, HTML, fix prompts, and the app's code preview.
- **`fixmyslop ui`** binds to 127.0.0.1 only, rejects requests whose Host header isn't localhost (DNS-rebinding
  protection), requires a random per-process token on every API call, confines file reads/writes to the opened
  project, and serves a strict Content-Security-Policy with no external requests.
- **Writes to your project** only happen when you ask: `fix --safe-only` / `fix`, `init`, `baseline`, and the
  app's "Apply safe fixes", rule toggles and Setup buttons.
- **`fix --agent ...`** runs the coding agent CLI you choose (Claude Code, Codex, Gemini, or your own
  `--agent-cmd`) in your project with edit permissions. Review the changes with `git diff`.

## Releases

PyPI releases are published from GitHub Actions with trusted publishing (no long-lived tokens) and carry
PyPI attestations. npm releases are published from the same workflow with npm provenance.
