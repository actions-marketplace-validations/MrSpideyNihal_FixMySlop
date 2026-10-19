// FixMySlop site: theme, GitHub stars, tabs, copy buttons, and the scan form.
(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode: not remembered */ } },
  };

  // Theme: follows the system until the visitor picks one.
  const saved = store.get("fms-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("themeBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("fms-theme", next);
  });

  // GitHub stars (anonymous API, cached for an hour so visitors don't hit the rate limit).
  const showStars = (n) => {
    if (typeof n !== "number") return;
    $("starCount").textContent = n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
    $("starCount").hidden = false;
  };
  const cached = JSON.parse(store.get("fms-stars") || "null");
  if (cached && Date.now() - cached.at < 3600_000) showStars(cached.n);
  else fetch("https://api.github.com/repos/MrSpideyNihal/FixMySlop")
    .then((r) => (r.ok ? r.json() : null))
    .then((repo) => {
      if (!repo) return;
      showStars(repo.stargazers_count);
      store.set("fms-stars", JSON.stringify({ n: repo.stargazers_count, at: Date.now() }));
    })
    .catch(() => { /* offline or rate-limited: the button still links to GitHub */ });

  // Toast
  let toastTimer;
  function toast(message) {
    const t = $("toast");
    t.textContent = message;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
  }

  // Copy buttons on every code block
  document.querySelectorAll(".code").forEach((block) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn small copy";
    button.textContent = "Copy";
    button.addEventListener("click", async () => {
      const text = block.querySelector("pre").textContent;
      try { await navigator.clipboard.writeText(text); toast("Copied"); }
      catch { toast("Select the text and copy it manually"); }
    });
    block.append(button);
  });

  // Tabs (WAI-ARIA pattern: arrow keys move between tabs)
  document.querySelectorAll("[data-tabs]").forEach((root) => {
    const tabs = [...root.querySelectorAll('[role="tab"]')];
    const select = (tab) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        $(t.getAttribute("aria-controls")).hidden = !on;
      });
      tab.focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (step) { e.preventDefault(); select(tabs[(i + step + tabs.length) % tabs.length]); }
        if (e.key === "Home") { e.preventDefault(); select(tabs[0]); }
        if (e.key === "End") { e.preventDefault(); select(tabs[tabs.length - 1]); }
      });
    });
  });

  // Badge snippet for the last scanned repo
  function showBadge(slug) {
    const origin = location.origin;
    $("badgePreview").src = `/badge/${slug}`;
    $("badgePreview").alt = `FixMySlop grade for ${slug}`;
    $("badgeRepo").textContent = slug;
    $("badgeCode").textContent = `[![FixMySlop grade](${origin}/badge/${slug})](${origin}/r/${slug})`;
  }
  showBadge(store.get("fms-last") || "MrSpideyNihal/FixMySlop");

  // Scan form
  const OWNER = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  const REPO = /^[A-Za-z0-9._-]{1,100}$/;
  function parseRepo(text) {
    const value = text.trim().replace(/^(?:https?:\/\/)?(?:www\.)?github\.com\//i, "");
    let [owner = "", repo = ""] = value.split(/[/#?]/);
    repo = repo.replace(/\.git$/i, "");
    return OWNER.test(owner) && REPO.test(repo) && repo !== "." && repo !== ".." ? `${owner}/${repo}` : null;
  }

  const status = $("status");
  function showStatus(...nodes) { status.replaceChildren(...nodes); }
  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  }
  function showError(message, hint) {
    showStatus(el("div", { className: "error" }, el("b", {}, message), el("span", { className: "muted" }, hint || "")));
  }

  let busy = false;
  async function scan(slug) {
    if (busy) return;
    busy = true;
    $("scanBtn").disabled = true;
    const started = Date.now();
    const label = el("span", {});
    const tick = () => {
      const s = Math.round((Date.now() - started) / 1000);
      label.textContent = `${s < 4 ? "Downloading" : "Scanning"} ${slug}… ${s}s`;
    };
    tick();
    const timer = setInterval(tick, 1000);
    showStatus(el("div", { className: "row" }, el("span", { className: "spinner" }), label));

    const url = `/r/${slug}`;
    const controller = new AbortController();
    const abort = setTimeout(() => controller.abort(), 65_000);
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) {
        let detail = {};
        try { detail = JSON.parse(response.headers.get("X-FixMySlop-Error") || "{}"); } catch { detail = {}; }
        showError(detail.message || `The scan failed (HTTP ${response.status}).`,
          detail.hint || "Try again, or run the CLI locally.");
        return;
      }
      store.set("fms-last", slug);
      location.href = url;               // the report is now cached, so this loads instantly
    } catch (e) {
      showError(e.name === "AbortError" ? "The scan took too long." : "Couldn't reach the scanner.",
        "Large repositories are best scanned locally: pipx install fixmyslop, then fixmyslop scan.");
    } finally {
      clearInterval(timer);
      clearTimeout(abort);
      busy = false;
      $("scanBtn").disabled = false;
    }
  }

  $("scanForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const slug = parseRepo($("repo").value);
    if (!slug) {
      showError("That doesn't look like a GitHub repository.", "Paste a link like github.com/owner/repo, or just owner/repo.");
      $("repo").focus();
      return;
    }
    scan(slug);
  });
  document.querySelectorAll("[data-example]").forEach((pill) => pill.addEventListener("click", () => {
    $("repo").value = pill.dataset.example;
    scan(pill.dataset.example);
  }));
})();
