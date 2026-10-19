# FixMySlop benchmark

How often are FixMySlop's findings right, and does it tell AI-built code apart from hand-built code?
Everything here is reproducible: `python benchmarks/run.py`.

## Corpus

13 public repositories pinned to exact commits ([repos.json](repos.json)):

- **9 AI-built apps:** projects made with AI app builders (found via GitHub search for Lovable projects).
- **4 hand-built repos** as a control group: shadcn/taxonomy, vercel/commerce, nextjs/saas-starter, shadcn/next-template.

Scans use the deterministic rules only (`--no-static`), so results don't depend on the installed ruff/bandit version.

## Precision: 95%

80 findings were drawn **at random** (seed 2026) from all 989 findings across the corpus. Each was judged by
reading the offending code in context; ambiguous ones were checked against the surrounding source.
Every verdict, with notes, is in [labels.json](labels.json).

| | Count |
|---|---|
| True positives | 76 |
| False positives | 4 |
| **Precision** | **95%** |

The 4 false positives were: a pulsing dot that really did mean "live", a dropdown wrapper that closes on click
(event delegation), and two overlay cases (a blurred modal backdrop and a large shadow on a dialog). The overlay cases
were fixed after the review; the other two remain known limitations.

An earlier sample (before a round of fixes) surfaced more false-positive patterns — click-outside modal backdrops,
dialog bodies that only call `stopPropagation()`, `.catch(() => null)` fallbacks, `any` inside prompt strings,
generated files, and shadcn `asChild` buttons. Each became a fix plus a regression test in `tests/test_slop_rules.py`.

## Separation

| Repo | Group | Score | Grade | Issues | Lines |
|---|---|---|---|---|---|
| clippr-v2-lovable | AI | 25.7 | C | 526 | 65,145 |
| dl-life-canvas | AI | 20.8 | B | 59 | 11,446 |
| aura-wardrobe-intelligence | AI | 13.4 | B | 263 | 67,890 |
| municipal-connect | AI | 8.7 | A | 19 | 9,352 |
| leadmachine-leadcatch-lovable | AI | 5.9 | A | 45 | 20,013 |
| lumina-lovable-concept | AI | 4.8 | A | 11 | 8,593 |
| usama-showcase-sparkle | AI | 4.3 | A | 30 | 27,914 |
| money-kalkulacka | AI | 1.5 | A | 4 | 10,818 |
| lovable-portfoilo | AI | 0.0 | A | 0 | 6,182 |
| commerce | hand | 9.4 | A | 12 | 3,559 |
| taxonomy | hand | 5.8 | A | 13 | 7,177 |
| saas-starter | hand | 3.0 | A | 4 | 3,495 |
| next-template | hand | 0.0 | A | 0 | 510 |

- AI-built apps: **4.2 findings per 1,000 lines**; hand-built: **2.0** — about 2× more.
- Every hand-built repo grades A. The worst AI-built apps grade B–C.
- **The grade alone is not an "AI detector".** Several AI-built apps grade A: their authors clearly steered the
  design and cleaned up the output. FixMySlop judges the code, not who (or what) wrote it.

## Limitations — read before quoting the numbers

- **Single reviewer.** Labels were made by one reviewer (an AI assistant), not independently verified.
- **Precision only.** Recall (how much slop is missed) isn't measured here: there's no ground-truth list of every
  problem in these repos. The bundled `examples/` and the rule tests check that known patterns are caught.
- **Small, frontend-heavy corpus.** 13 repos, mostly React/Next.js. Backend rules (empty tests, duplicate
  functions, Go/Java error handling) are covered by tests but barely exercised by this corpus.
- **Pattern-based.** Rules read source text (and Python ASTs), not rendered pages, so styling that only exists at
  runtime is invisible.

Contributions of labeled findings — especially from other stacks — are very welcome: run `run.py --sample N`,
review `sample.json`, and send the verdicts.
