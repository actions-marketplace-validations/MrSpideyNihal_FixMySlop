"""
Interactive, self-contained HTML report — FixMySlop's main visual UI.

One file, no external requests: findings grouped by file, severity/category
filters, search, and "copy fix prompt" buttons that put an agent-ready brief
on the clipboard for one file or the whole project.
"""
import html
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from fixmyslop.core.issue import ScanReport
from fixmyslop.core.slop.brief import build_fix_brief
from fixmyslop.macros import APP_NAME, APP_VERSION, SEVERITY_RANK

_WEIGHTS = {"CRITICAL": 10, "HIGH": 5, "MEDIUM": 2, "LOW": 1, "INFO": 0}


def render_html(report: ScanReport, relpath: Callable[[str], str]) -> str:
    """Render the report page."""
    by_file = defaultdict(list)
    for issue in report.issues:
        by_file[relpath(issue.file_path)].append(issue)

    files = []
    def weight(item):
        _, issues = item
        return (min(SEVERITY_RANK.get(i.severity, 9) for i in issues),
                -sum(_WEIGHTS.get(i.severity, 1) for i in issues), item[0])

    for path, issues in sorted(by_file.items(), key=weight):
        ordered = sorted(issues, key=lambda i: (SEVERITY_RANK.get(i.severity, 9), i.line_start))
        files.append({
            "path": path,
            "brief": build_fix_brief(ordered, relpath, targets=path, design_context=report.design_context),
            "issues": [{
                "line": i.line_start,
                "sev": i.severity,
                "rule": i.rule_id or i.source_tool,
                "cat": i.category,
                "title": i.title,
                "desc": i.description,
                "evidence": i.evidence,
                "fix": i.suggested_fix,
                "tool": i.source_tool,
            } for i in ordered],
        })

    data = {
        "files": files,
        "brief": build_fix_brief(report.issues, relpath, design_context=report.design_context),
        "counts": Counter(i.severity for i in report.issues),
        "categories": sorted({i.category for i in report.issues}),
    }
    project = Path(report.repo_path).name or report.repo_path   # never leak the local user path
    data["meta"] = {"project": project, "score": f"{report.slop_score:g}", "grade": report.grade or "-",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "files": report.scanned_files,
                    "lines": f"{report.lines_scanned:,}"}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    esc = html.escape
    meta = (f"{esc(project)} · {report.scanned_files} files · {report.lines_scanned:,} lines · "
            f"{report.scan_time_s:.2f}s · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return (_TEMPLATE
            .replace("__TITLE__", f"{APP_NAME} report")
            .replace("__SCORE__", f"{report.slop_score:g}")
            .replace("__GRADE__", esc(report.grade or "-"))
            .replace("__TOTAL__", str(len(report.issues)))
            .replace("__META__", meta)
            .replace("__VERSION__", APP_VERSION)
            .replace("__DATA__", payload))


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f7f7f5;--surface:#ffffff;--fg:#1c1c1a;--muted:#6b6b66;--faint:#9a9a94;--line:#e6e5e1;--code:#f1f0ec;
  --crit:#b42318;--high:#c4320a;--med:#b54708;--low:#475467;--info:#98a2b3;--ok:#067647;
  --radius:8px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#121211;--surface:#1a1a19;--fg:#ecebe7;--muted:#a3a29c;--faint:#77766f;--line:#2b2b29;--code:#222220;
  --crit:#f97066;--high:#fb6514;--med:#fdb022;--low:#98a2b3;--info:#667085;--ok:#47cd89;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}
header{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-end;justify-content:space-between;margin-bottom:24px}
h1{font-size:15px;font-weight:600;margin:0 0 4px;letter-spacing:.01em}
.meta{color:var(--muted);font-size:12px;overflow-wrap:anywhere;margin:0}
.score{display:flex;align-items:baseline;gap:14px}
.score b{font-size:44px;line-height:1;font-weight:650;font-variant-numeric:tabular-nums}
.score .of{color:var(--muted)}
.grade{font-size:13px;font-weight:600;border:1px solid var(--line);border-radius:6px;padding:2px 8px;background:var(--surface)}
.toolbar{position:sticky;top:0;z-index:2;background:var(--bg);display:flex;flex-wrap:wrap;gap:8px;
  align-items:center;padding:12px 0;border-bottom:1px solid var(--line);margin-bottom:20px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);
  color:var(--fg);border-radius:999px;padding:4px 10px;font:inherit;font-size:12px;cursor:pointer}
.chip[aria-pressed="false"]{color:var(--faint);background:transparent}
.dot{width:8px;height:8px;border-radius:50%;background:var(--c)}
select,input[type=search]{font:inherit;font-size:13px;color:var(--fg);background:var(--surface);
  border:1px solid var(--line);border-radius:6px;padding:5px 8px}
input[type=search]{flex:1;min-width:160px}
.btn{font:inherit;font-size:13px;font-weight:500;border-radius:6px;padding:6px 12px;cursor:pointer;
  border:1px solid var(--fg);background:var(--fg);color:var(--bg)}
.btn.ghost{background:transparent;color:var(--fg);border-color:var(--line)}
.btn:hover{opacity:.88}
:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:24px;align-items:start}
nav{position:sticky;top:64px;max-height:calc(100vh - 80px);overflow:auto;font-size:13px}
nav a{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:8px;color:var(--fg);
  text-decoration:none;padding:5px 8px;border-radius:6px}
nav a:hover{background:var(--surface)}
nav .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav .dir{grid-column:1;font-size:11px;color:var(--faint);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;direction:rtl;text-align:left}
nav .n{grid-row:1;grid-column:2;font-variant-numeric:tabular-nums;color:var(--muted)}
.file{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:16px}
.file-head{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 16px;
  border-bottom:1px solid var(--line)}
.file-head code{font:500 13px var(--mono);overflow-wrap:anywhere}
.issue{padding:12px 16px;border-top:1px solid var(--line)}
.issue:first-of-type{border-top:0}
.row{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.ln{font:12px var(--mono);color:var(--faint);min-width:44px}
.sev{font-size:11px;font-weight:600;letter-spacing:.04em;color:var(--c)}
.rule{font:12px var(--mono);color:var(--muted)}
.title{font-weight:550}
.desc{color:var(--muted);margin:4px 0 0 54px}
pre{margin:8px 0 0 54px;background:var(--code);border-radius:6px;padding:8px 10px;overflow-x:auto;
  font:12px/1.5 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
.fix{margin:8px 0 0 54px}
.fix b{font-weight:600}
.desc code,.fix code,.title code{font:12px var(--mono);background:var(--code);border-radius:4px;padding:1px 4px}
.empty{padding:48px 0;text-align:center;color:var(--muted)}
.clean{padding:64px 0;text-align:center}
.clean b{display:block;font-size:20px;margin-bottom:4px;color:var(--ok)}
footer{margin-top:32px;color:var(--faint);font-size:12px}
.toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--fg);color:var(--bg);
  padding:8px 14px;border-radius:6px;font-size:13px;opacity:0;transition:opacity .15s;pointer-events:none}
.toast.show{opacity:1}
@media (max-width:820px){
  .layout{grid-template-columns:minmax(0,1fr)}
  nav{display:none}
  .toolbar{position:static}
  .desc,pre,.fix{margin-left:0}
}
@media (prefers-reduced-motion:reduce){.toast{transition:none}}
@media print{
  :root{--bg:#fff;--surface:#fff;--fg:#111;--muted:#555;--faint:#777;--line:#ddd;--code:#f4f4f2}
  body{font-size:11px}
  main{max-width:none;padding:0}
  .toolbar,nav,.btn,.toast{display:none!important}
  .layout{display:block}
  .file{break-inside:auto;border-color:#ccc}
  .issue{break-inside:avoid}
  pre{white-space:pre-wrap}
}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>FixMySlop report</h1>
      <p class="meta">__META__</p>
    </div>
    <div class="score" aria-label="Slop score __SCORE__ out of 100, grade __GRADE__">
      <b>__SCORE__</b><span class="of">/ 100 slop</span><span class="grade">Grade __GRADE__</span>
    </div>
  </header>

  <div class="toolbar" role="toolbar" aria-label="Filters">
    <span id="sevChips"></span>
    <select id="cat" aria-label="Category"><option value="">All categories</option></select>
    <input type="search" id="q" placeholder="Filter by file, rule or text" aria-label="Search findings">
    <select id="export" aria-label="Export the findings shown">
      <option value="">Export…</option>
      <option value="pdf">PDF (save from print)</option>
      <option value="md">Markdown</option>
      <option value="csv">CSV</option>
      <option value="json">JSON</option>
    </select>
    <button class="btn" id="copyAll" type="button">Copy fix prompt (all)</button>
  </div>

  <div class="layout">
    <nav id="nav" aria-label="Files"></nav>
    <section id="list" aria-live="polite"></section>
  </div>
  <footer>FixMySlop __VERSION__ · Paste a fix prompt into Claude Code, Codex or Cursor, or run <code>fixmyslop fix</code>.</footer>
</main>
<div class="toast" id="toast" role="status"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById("data").textContent);
  const SEVS = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
  const COLOR = {CRITICAL:"var(--crit)",HIGH:"var(--high)",MEDIUM:"var(--med)",LOW:"var(--low)",INFO:"var(--info)"};
  const active = new Set(SEVS);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const md = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const chips = $("sevChips");
  SEVS.filter(s => data.counts[s]).forEach(s => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.setAttribute("aria-pressed", "true");
    b.innerHTML = `<span class="dot" style="--c:${COLOR[s]}"></span>${s.toLowerCase()} ${data.counts[s]}`;
    b.onclick = () => { active.has(s) ? active.delete(s) : active.add(s);
      b.setAttribute("aria-pressed", active.has(s)); render(); };
    chips.appendChild(b);
  });
  data.categories.forEach(c => $("cat").add(new Option(c, c)));
  $("cat").onchange = render;
  $("q").oninput = render;

  function visible(file) {
    const cat = $("cat").value, q = $("q").value.trim().toLowerCase();
    return file.issues.filter(i => active.has(i.sev) && (!cat || i.cat === cat) &&
      (!q || (file.path + " " + i.rule + " " + i.title + " " + i.evidence).toLowerCase().includes(q)));
  }

  function render() {
    const list = $("list"), nav = $("nav");
    if (!data.files.length) {
      list.innerHTML = '<div class="clean"><b>No slop found.</b>Nothing to fix.</div>';
      nav.innerHTML = ""; return;
    }
    let html = "", navHtml = "", shown = 0;
    data.files.forEach((f, idx) => {
      const issues = visible(f);
      if (!issues.length) return;
      shown += issues.length;
      const cut = f.path.lastIndexOf("/");
      navHtml += `<a href="#f${idx}" title="${esc(f.path)}"><span class="name">${esc(f.path.slice(cut + 1))}</span>` +
        `<span class="n">${issues.length}</span>` +
        (cut > 0 ? `<span class="dir">${esc(f.path.slice(0, cut))}&lrm;</span>` : "") + `</a>`;
      html += `<article class="file" id="f${idx}"><div class="file-head"><code>${esc(f.path)}</code>` +
        `<button class="btn ghost" type="button" data-copy="${idx}">Copy fix prompt</button></div>` +
        issues.map(i => `<div class="issue"><div class="row"><span class="ln">L${i.line}</span>` +
          `<span class="sev" style="--c:${COLOR[i.sev]}">${i.sev}</span><span class="title">${md(i.title)}</span>` +
          `<span class="rule">${esc(i.rule)}</span></div>` +
          (i.desc && i.desc !== i.title ? `<p class="desc">${md(i.desc)}</p>` : "") +
          (i.evidence ? `<pre>${esc(i.evidence)}</pre>` : "") +
          (i.fix ? `<p class="fix"><b>Fix:</b> ${md(i.fix)}</p>` : "") + `</div>`).join("") + `</article>`;
    });
    list.innerHTML = html || '<div class="empty">No findings match these filters.</div>';
    nav.innerHTML = navHtml;
    list.querySelectorAll("[data-copy]").forEach(b => b.onclick = () => copy(data.files[+b.dataset.copy].brief));
  }

  function copy(text) {
    const done = () => { const t = $("toast"); t.textContent = "Fix prompt copied — paste it into your agent";
      t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1800); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => fallback(text, done));
    else fallback(text, done);
  }
  function fallback(text, done) {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove(); done();
  }
  $("copyAll").onclick = () => copy(data.brief);
  if (!data.files.length) $("copyAll").hidden = true;

  // Export what's on screen (filters apply). PDF goes through the browser's print dialog.
  function shownFiles() {
    return data.files.map(f => ({path: f.path, issues: visible(f)})).filter(f => f.issues.length);
  }
  function download(name, type, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {type}));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  const m = data.meta, base = `fixmyslop-${m.project}`.replace(/[^\w.-]+/g, "-");
  const exporters = {
    md() {
      const files = shownFiles(), n = files.reduce((a, f) => a + f.issues.length, 0);
      let out = `# FixMySlop report: ${m.project}\n\n**Slop score ${m.score}/100 · grade ${m.grade}** · ${n} issues\n\n` +
        `${m.date} · ${m.files} files · ${m.lines} lines\n\n`;
      const fixes = {};
      files.forEach(f => {
        out += `## \`${f.path}\` (${f.issues.length})\n\n`;
        f.issues.forEach(i => {
          out += `- **L${i.line}** ${i.sev} · ${i.title} \`${i.rule}\`\n`;
          if (i.evidence) out += `  \`${i.evidence.replace(/\s+/g, " ").replace(/`/g, "'").slice(0, 160)}\`\n`;
          if (i.fix && !fixes[i.rule]) fixes[i.rule] = i.fix;
        });
        out += "\n";
      });
      const rules = Object.keys(fixes).sort();
      if (rules.length) out += "## How to fix each rule\n\n" + rules.map(r => `- \`${r}\`: ${fixes[r]}`).join("\n") + "\n";
      download(`${base}.md`, "text/markdown", out);
    },
    csv() {
      const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const rows = [["Severity", "File", "Line", "Category", "Rule", "Title", "Evidence", "Fix"].map(q).join(",")];
      shownFiles().forEach(f => f.issues.forEach(i =>
        rows.push([i.sev, f.path, i.line, i.cat, i.rule, i.title, i.evidence, i.fix].map(q).join(","))));
      download(`${base}.csv`, "text/csv", "\ufeff" + rows.join("\r\n"));
    },
    json() {
      download(`${base}.json`, "application/json", JSON.stringify({report: m, findings: shownFiles()}, null, 2));
    },
    pdf() { window.print(); },
  };
  $("export").onchange = (e) => { const fmt = e.target.value; e.target.value = ""; if (fmt) exporters[fmt](); };
  render();
})();
</script>
</body>
</html>
"""
