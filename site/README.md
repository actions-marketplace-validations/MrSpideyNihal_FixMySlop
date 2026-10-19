# FixMySlop website

Paste a public GitHub repository and get the FixMySlop report: score, findings by file, the fix prompt, and
downloads (PDF, Markdown, JSON, CSV, SARIF). Runs on Vercel: static pages plus two Python functions.

| Path | What it does |
|---|---|
| `/` | Landing page: scan box, what it checks, CLI and agent setup, badge, accuracy, limits |
| `/r/<owner>/<repo>` | Downloads the repo, scans it, returns the report (`?format=pdf` and friends for files) |
| `/badge/<owner>/<repo>` | SVG badge with the grade, for READMEs |

## How a scan works

The default branch is downloaded as one tarball from `codeload.github.com` (no git, no submodules, no GitHub
API quota), extracted into a temporary folder, scanned with the published `fixmyslop` package, and deleted.
Nothing from the repository is installed or executed: FixMySlop only reads files.

Limits, in [lib/fms_site.py](lib/fms_site.py): public repositories only, 60 MB download, 25,000 files,
250 MB extracted, symlinks and paths outside the folder ignored, `node_modules` and build folders skipped.
Responses are cached at the edge for an hour, so a repeat scan of the same repo is free.

## Run it locally

```bash
pip install -r site/requirements.txt
python site/dev.py
```

Then open http://127.0.0.1:3000. `dev.py` serves the pages with the same headers and rewrites as
[vercel.json](vercel.json) and calls the same Python code as the Vercel functions.

## Deploy to Vercel

1. In Vercel, **Add New → Project** and import this GitHub repository.
2. Set **Root Directory** to `site`. Framework preset: **Other**. No build command.
3. Deploy. Vercel installs `requirements.txt` for the functions in `api/` and serves `public/`.

The free Hobby plan is for personal, non-commercial projects; a commercial deployment needs Pro. Add rate
limiting in **Firewall** before sharing the link widely (for example 20 scans per minute per IP on `/r/*`).

Tests: `pytest tests/test_site.py` from the repository root (no network needed).
