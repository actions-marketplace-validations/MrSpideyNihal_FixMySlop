"""Reproduce the FixMySlop benchmark.

    python benchmarks/run.py                 # clone pinned repos (cached), scan, print the table
    python benchmarks/run.py --sample 80     # also draw a random sample of findings for manual review

Scans use the deterministic rules only (`--no-static`) so results don't depend on which ruff/bandit
version is installed. Precision is computed from benchmarks/labels.json (hand-reviewed findings).
"""
import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from fixmyslop.cli.cli_app import run_scan  # noqa: E402
from fixmyslop.core.report_builder import relative_path  # noqa: E402


def checkout(repo: dict, cache: Path) -> Path:
    target = cache / repo["name"]
    if not (target / ".git").exists():
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", repo["url"], str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "--quiet", repo["commit"]], check=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(HERE / ".cache"), help="where to clone the repos")
    parser.add_argument("--sample", type=int, default=0, help="write N random findings to sample.json for review")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    repos = json.loads((HERE / "repos.json").read_text(encoding="utf-8"))["repos"]
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    results, findings = [], []
    for repo in repos:
        path = checkout(repo, cache)
        report, _ = run_scan([str(path)], use_static=False, min_severity="low", use_baseline=False)
        rules = Counter(i.rule_id for i in report.issues)
        results.append({"group": repo["group"], "name": repo["name"], "score": report.slop_score,
                        "grade": report.grade, "issues": len(report.issues), "lines": report.lines_scanned,
                        "rules": dict(rules)})
        for issue in report.issues:
            findings.append({"repo": repo["name"], "group": repo["group"], "file": relative_path(report, issue.file_path),
                             "line": issue.line_start, "rule": issue.rule_id, "evidence": issue.evidence[:200]})
        print(f"{repo['group']:5} {repo['name'][:32]:32} {report.slop_score:5} {report.grade}  "
              f"{len(report.issues):4} issues  {report.lines_scanned:6} lines", flush=True)

    (HERE / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

    labels_path = HERE / "labels.json"
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))["labels"]
        verdicts = Counter(label["verdict"] for label in labels)
        reviewed = verdicts["tp"] + verdicts["fp"]
        if reviewed:
            print(f"\nPrecision on {reviewed} hand-reviewed findings: {verdicts['tp'] / reviewed:.0%} "
                  f"({verdicts['tp']} true, {verdicts['fp']} false positives)")

    if args.sample:
        random.Random(args.seed).shuffle(findings)  # nosec B311 - sampling for review, not security
        (HERE / "sample.json").write_text(json.dumps(findings[:args.sample], indent=1, ensure_ascii=False),
                                          encoding="utf-8")
        print(f"\nWrote {min(args.sample, len(findings))} findings to benchmarks/sample.json for review")


if __name__ == "__main__":
    main()
