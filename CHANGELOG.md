# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/); while in 0.x, minor versions may
include breaking changes.

## 0.6.8

- **"New version available" notice.** `fixmyslop scan`, `fixmyslop version` and the app (a dismissible banner)
  say when a newer FixMySlop is on PyPI, with the upgrade command. At most one check a day, a 1.5 s timeout,
  silent offline, skipped in CI and machine-readable output, off with `FIXMYSLOP_NO_UPDATE_CHECK=1`. It never
  installs anything.
- "1 file", not "1 files", in scan summaries.

## 0.6.7

- FixMySlop no longer crashes where the home directory is read-only (serverless functions, read-only containers,
  locked-down CI). The debug log file is skipped when it can't be created. Found deploying the website to Vercel.
- Website: Vercel now detects both Python functions (each `api/*.py` declares `class handler`).

## 0.6.6

- **Website** ([site/](site/)): paste a public GitHub repo, get the report, download it as PDF, Markdown, JSON,
  CSV or SARIF, and add a grade badge to your README. Deploys to Vercel; nothing from the scanned repo is executed.
- **Scans are about 2x faster on Tailwind-heavy code.** Three UI rules re-scanned each line from every character
  position; they now scan each line once. Line rules also skip files they can't match. Findings are identical
  (checked on the benchmark and 64 AI-built sites).

## 0.6.5

**Reports you can hand to someone**
- PDF export rebuilt with a built-in writer: no extra installs. Before, it needed `fpdf2`; without it, `report.pdf`
  silently got HTML inside. The PDF now has the score, summary by category and rule, findings grouped by file with
  evidence, and how to fix each rule.
- Markdown export restructured the same way (summary tables, grouped by file, each fix stated once).
- **Export menu** in the HTML report (PDF via print, Markdown, CSV, JSON; respects the current filters) and in the
  app's Overview (PDF, Markdown, HTML, CSV, JSON, SARIF).
- Shared reports show the project name instead of the full local path.

## 0.6.4

- **Code shown to users isn't treated as code.** In JS/TS, the contents of multi-line template strings (API
  examples in a docs modal, inline `<script>` strings) are skipped by code rules. Before, a SaaS site's
  "API docs" modal showing `import { Client } from '@acme/sdk'` was reported as a HIGH missing package. On
  64 AI-built sites this removed 12 false findings (7 missing-package, 4 empty-catch, 1 console.log) and
  lost no real ones. UI rules still read template strings, since class lists live there too.
- `slop/placeholder-impl` catches "keeping it simple for now".

## 0.6.3

**Docs slop (new)**: READMEs and Markdown docs are scanned (`.md`, `.mdx`; READMEs, root-level docs, `docs/`, `.github/`).
- Checked against the repo: `docs/missing-script` (`npm run test` with no such script), `docs/dead-link` (links
  and images to missing files, a LICENSE that isn't there), `docs/placeholder` (`github.com/yourusername/...`), <!-- fixmyslop-ignore[docs/placeholder]: example -->
  `docs/chatbot-residue` ("Certainly! Here's a comprehensive README", "[Insert screenshot here]").
- Style: `docs/emoji-headings` (emoji on most headings) and `docs/hype-copy` (three or more hype phrases).
- Calibrated on 130 READMEs and 59 cloned repos: 0 of 20 well-known hand-written READMEs (React, Django, Vite,
  FastAPI...) flagged; all 28 missing-script, dead-link and placeholder findings in the cloned repos were correct
  on review. Emoji headings are the weakest signal (some older human READMEs use them too), so they're LOW.

**Fix brief**: a "Production bar" section. Every fix must be production-grade (real error handling, accessible,
no committed secrets, dead duplicates deleted), the agent runs the project's type check, lint, tests and build
before and after, and reports pre-existing failures instead of hiding them. Scoped to the listed items: no rewrites.
Plus a "Docs direction" section when docs/* issues are present.

Tested against [metacall/deploy](https://github.com/metacall/deploy) (main plus all 201 PRs), a hand-written
TypeScript CLI. It found 29 false positives, all fixed:
- `slop/empty-test` understands functions imported from assertion libraries
  (`import { ok, strictEqual } from 'assert'`, chai, `node:assert/strict`, vitest, ...).
- `slop/elided-code` no longer flags a bare `// ...` inside commented-out code; it still flags one that
  stands in for code between real lines.
- `slop/placeholder-impl` no longer treats a plain `TODO: implement X` as AI hedging.

**Env files**
- `.env`, `.env.local`, `.env.production`... are now scanned. New `sec/env-secret`: real secret values in an env
  file git doesn't ignore (already committed, or the next `git add .` will commit it). A gitignored `.env` and
  `.env.example` placeholders stay quiet; local dev URLs (`localhost`) don't count.
- `sec/public-env-secret` and `sec/leaked-key` also run on env files (`NEXT_PUBLIC_OPENAI_API_KEY=...`).

**Languages**
- Now scanned: Scala, Dart, Groovy, Kotlin scripts (`.kts`) and C/C++ headers. Some rules already mentioned
  these languages, but their files were never picked up.
- Ruby `#` comments are recognized, so comment checks (elided code, narrating comments) work in `.rb` files.
- README: new "Supported languages" table (what runs for each language) and "What it can't catch".

## 0.6.2
- `--online` package checks no longer download full registry metadata (multi-MB for popular npm packages), which could time out and silently skip the check; one retry on network errors.

## 0.6.1
- `fixmyslop ui`: code previews failed when the opened folder is a subfolder of a git repo (monorepos).

## 0.6.0

**Adopt it on an existing codebase**
- **Baseline mode**: `fixmyslop baseline` accepts today's issues; later scans and CI report only new slop.
  Fingerprints ignore line numbers, so editing above old issues doesn't resurface them. Also in the app (Setup).

**Backend rules**
- `slop/empty-test` — tests (pytest, Jest, Vitest, Mocha) that assert nothing or only `assert True`;
  understands local asserting helpers and "does not throw" smoke tests.
- `slop/duplicate-function` — the same function body in several places (variable names ignored).
- `slop/pointless-wrapper`, `slop/catch-rethrow`, `slop/catch-rethrow-js`.
- Go: `slop/ignored-error` (`if err != nil {}` / `return nil`). Java/Kotlin: `slop/print-stacktrace`.
  `slop/empty-catch` now covers Java, C#, Kotlin, Swift, PHP and Dart.

**UI rules**
- New: `ui/fake-feature` (buttons that only `alert('…preview…')`), `ui/fake-ratings` (hard-coded ratings and
  review counts).
- `ui/fake-social-proof` knows more audiences ("2,400+ happy travelers") and needs a real social-proof number
  (100+), so "5+ guests" in a dropdown is fine; form placeholders are ignored.
- `ui/aphorism-copy` catches more of the "lovely little stays" voice; `ui/two-tone-headline` catches headlines
  styled through `h1 em { color }`; symbols like ⌂ ✦ ✿ count as icons.

**Accuracy** (from a hand-reviewed benchmark — see [benchmarks/BENCHMARK.md](benchmarks/BENCHMARK.md), 95% precision)
- Fixed: single-file HTML pages with long CSS lines were skipped as "minified" and reported as grade A.
- Fixed: a line starting with the CSS `*` selector was treated as a comment.
- Fixed false positives: modal backdrops and `stopPropagation()` wrappers (clickable-div), shadcn `asChild`
  buttons, `aria-selected:` focus styles, `.catch(() => null)` fallbacks, `any` inside strings, overlays with
  blur/big shadows, Java/Go syntax rules matching text inside Python strings.
- Generated files (`*.gen.ts`, `@generated`, "do not edit") are only checked for leaked secrets.
- A scan that finds no supported files now says so instead of "No slop found".

**Trust**
- Reproducible benchmark (`benchmarks/`), SECURITY.md, this changelog, npm provenance on releases.

## 0.5.0
- `fixmyslop ui`: a rules-first local web app replacing the PyQt desktop app.
- Non-English button labels no longer flagged as icon-only; design tokens don't count as palette sprawl;
  code previews redact keys; "AI Smell" renamed "Code Slop".

## 0.4.0
- Rules for the newer AI UI look: `ui/aphorism-copy`, `ui/eyebrow-labels`, `ui/two-tone-headline`,
  `ui/fake-chart`; glow orbs in any hue; pulsing status dots.

## 0.3.x
- Deterministic rules engine, agent hand-off (`fix`, MCP server, Claude Code hook, `init`), SARIF, interactive
  HTML report, GitHub Action, pre-commit hook, npx launcher, calibration on real AI-built apps.
