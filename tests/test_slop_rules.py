"""Tests for the deterministic slop rules — each rule must fire on slop and stay quiet on clean code."""
from pathlib import Path

import pytest

from fixmyslop.core.slop.engine import SlopEngine


def scan(tmp_path: Path, name: str, content: str, extra: dict | None = None) -> list:
    """Write files into a throwaway project and return rule ids found in `name`."""
    for rel, text in (extra or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    engine = SlopEngine(tmp_path)
    issues = engine.check_file(path, content) + engine.finalize()
    return [i.rule_id for i in issues]


@pytest.mark.parametrize("rule_id, name, sloppy, clean", [
    ("ui/ai-gradient", "Hero.tsx",
     '<div className="bg-gradient-to-r from-purple-600 to-pink-500" />',
     '<div className="bg-slate-900" />'),
    ("ui/ai-gradient", "style.css",
     ".hero { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }",
     ".hero { background: var(--surface); }"),
    ("ui/gradient-text", "Hero.tsx",
     '<h1 className="bg-clip-text text-transparent">Hi</h1>',
     '<h1 className="text-slate-900">Hi</h1>'),
    ("ui/marketing-slop", "Hero.tsx",
     "<p>Unlock the power of AI to supercharge your workflow</p>",
     "<p>Invoices are matched to bank transactions every night.</p>"),
    ("ui/fake-social-proof", "Hero.tsx",
     "<p>Trusted by 10,000+ happy users</p>",
     "<p>Used by the finance team at our company</p>"),
    ("ui/placeholder-copy", "Hero.tsx",
     "<p>Lorem ipsum dolor sit amet</p>",
     "<p>Real copy</p>"),
    ("ui/blur-blobs", "Hero.tsx",
     '<div className="absolute rounded-full bg-pink-400 blur-3xl" />',
     '<div className="rounded-full bg-pink-400" />'),
    ("a11y/img-alt", "Hero.tsx",
     '<img src="/a.png" />',
     '<img src="/a.png" alt="Dashboard screenshot" />'),
    ("a11y/clickable-div", "Hero.tsx",
     "<div onClick={() => go()}>Go</div>",
     '<button type="button" onClick={() => go()}>Go</button>'),
    ("a11y/icon-button", "Hero.tsx",
     "<button onClick={close}>\n  <XIcon />\n</button>",
     '<button onClick={close} aria-label="Close">\n  <XIcon />\n</button>'),
    ("a11y/dead-link", "Hero.tsx",
     '<a href="#">Docs</a>',
     '<a href="/docs">Docs</a>'),
    ("a11y/focus-removed", "Hero.tsx",
     '<input className="outline-none border" />',
     '<input className="outline-none focus-visible:ring-2 border" />'),
    ("slop/elided-code", "api.ts",
     "export function a() {}\n// ... existing code ...\n",
     "export function a() {}\n// Retries are handled by the caller.\n"),
    ("slop/placeholder-impl", "auth.py",
     "def login(u):\n    # In a real application, you would hash the password\n    return True\n",
     "def login(u):\n    # bcrypt cost 12 keeps hashing under 250ms on our servers\n    return True\n"),
    ("slop/swallowed-exception", "io.py",
     "try:\n    x = 1\nexcept Exception:\n    pass\n",
     "try:\n    x = 1\nexcept ValueError:\n    pass\n"),
    ("slop/bare-except", "io.py",
     "try:\n    x = 1\nexcept:\n    raise\n",
     "try:\n    x = 1\nexcept OSError:\n    raise\n"),
    ("slop/empty-catch", "api.ts",
     "try { run() } catch (e) {}",
     "try { run() } catch (e) { report(e) }"),
    ("slop/narrating-comment", "app.py",
     "# Initialize the user list\nuser_list = []\n",
     "# Kept sorted so bisect lookups stay O(log n)\nuser_list = []\n"),
    ("slop/stub-function", "svc.py",
     'def charge(card):\n    """Charge the card."""\n    pass\n',
     'def charge(card):\n    """Charge the card."""\n    return gateway.charge(card)\n'),
    ("slop/debugger", "app.ts",
     "function f() {\n  debugger;\n}\n",
     "function f() {\n  return 1;\n}\n"),
    ("slop/emoji-log", "server.js",
     'console.log("🚀 Server started");',  # fixmyslop-ignore[slop/emoji-log] fixture
     'console.log("Server started on port %d", port);'),
    ("sec/leaked-key", "config.py",
     'KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n',
     'KEY = os.environ["OPENAI_API_KEY"]\n'),
    ("sec/hardcoded-credential", "config.py",
     'DB_PASSWORD = "hunter2-prod-99"\n',
     'DB_PASSWORD = os.environ.get("DB_PASSWORD")\n'),
    ("sec/public-env-secret", "client.ts",
     "const k = process.env.NEXT_PUBLIC_OPENAI_API_KEY;",  # fixmyslop-ignore[sec/public-env-secret] fixture
     "const k = process.env.NEXT_PUBLIC_APP_URL;"),
    ("sec/sql-injection", "db.py",
     'cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n',
     'cur.execute("SELECT * FROM users WHERE id = %s", (uid,))\n'),
    ("sec/cors-wildcard", "main.py",
     'app.add_middleware(CORSMiddleware, allow_origins=["*"])\n',
     'app.add_middleware(CORSMiddleware, allow_origins=settings.origins)\n'),
    ("sec/tls-disabled", "net.py",
     "requests.get(url, verify=False)\n",
     "requests.get(url, timeout=10)\n"),
    ("sec/raw-html", "Post.tsx",
     "<div dangerouslySetInnerHTML={{ __html: body }} />",
     "<div>{body}</div>"),
])
def test_rule_fires_on_slop_and_not_on_clean(tmp_path, rule_id, name, sloppy, clean):
    """Every rule: positive example flagged, negative example quiet."""
    assert rule_id in scan(tmp_path / "bad", name, sloppy)
    assert rule_id not in scan(tmp_path / "good", name, clean)


def test_threshold_rules_need_repetition(tmp_path):
    """hover-scale only reports once the pattern is repeated across the project."""
    once = '<div className="hover:scale-105" />\n'
    assert "ui/hover-scale" not in scan(tmp_path / "a", "A.tsx", once)
    assert "ui/hover-scale" in scan(tmp_path / "b", "A.tsx", once * 5)


def test_rainbow_palette_is_project_level(tmp_path):
    """Seven-plus accent families across the project triggers one finding."""
    colors = " ".join(f"text-{c}-500" for c in
                      ["red", "orange", "green", "teal", "sky", "violet", "pink", "lime"])
    found = scan(tmp_path, "A.tsx", f'<div className="{colors}" />')
    assert found.count("ui/rainbow-palette") == 1


def test_ui_rules_skip_test_files(tmp_path):
    """UI/copy rules don't fire on tests or stories."""
    assert "ui/marketing-slop" not in scan(tmp_path, "src/__tests__/Hero.test.tsx",
                                           "<p>Revolutionize everything</p>")


def test_python_phantom_and_undeclared_imports(tmp_path):
    """Unknown package → phantom; installed-but-undeclared → undeclared; declared/stdlib/local → fine."""
    code = ("import os\nimport yaml\nimport totally_made_up_pkg_xyz\nimport localmod\nimport pytest\n")
    found = scan(tmp_path, "app.py", code, extra={"requirements.txt": "pyyaml\n", "localmod.py": "X = 1\n"})
    assert found.count("deps/phantom-import") == 1
    assert "deps/undeclared-import" in found  # pytest is installed but not declared


def test_guarded_imports_are_ignored(tmp_path):
    """Optional imports inside try/except ImportError are not phantom."""
    code = "try:\n    import totally_made_up_pkg_xyz\nexcept ImportError:\n    totally_made_up_pkg_xyz = None\n"
    assert "deps/phantom-import" not in scan(tmp_path, "app.py", code, extra={"requirements.txt": ""})


def test_js_imports(tmp_path):
    """npm packages are checked against package.json; aliases, relatives and builtins are fine."""
    code = ('import React from "react";\nimport x from "@/lib/x";\nimport y from "./y";\n'
            'import fs from "node:fs";\nimport path from "path";\nimport z from "react-quantum-forms";\n')
    found = scan(tmp_path, "src/App.tsx", code, extra={"package.json": '{"dependencies": {"react": "18"}}'})
    assert found.count("deps/phantom-import") == 1


def test_inline_suppression(tmp_path):
    """fixmyslop-ignore[rule] on the line or the line above silences that rule only."""
    code = ('// fixmyslop-ignore[ui/gradient-text] brand requirement\n'
            '<h1 className="bg-clip-text text-transparent">Hi</h1>\n'
            '<a href="#">x</a>\n')
    found = scan(tmp_path, "A.tsx", code)
    assert "ui/gradient-text" not in found
    assert "a11y/dead-link" in found


def test_file_level_ignore(tmp_path):
    """fixmyslop-ignore-file in the header skips the file."""
    assert scan(tmp_path, "A.tsx", "// fixmyslop-ignore-file\n<a href=\"#\">x</a>\n") == []


def test_config_disable_and_exclude(tmp_path):
    """.fixmyslop.yml can disable rule groups and exclude paths."""
    (tmp_path / ".fixmyslop.yml").write_text('disable: ["a11y"]\nexclude: ["legacy/**"]\n', encoding="utf-8")
    assert "a11y/dead-link" not in scan(tmp_path, "A.tsx", '<a href="#">x</a>')
    assert scan(tmp_path, "legacy/B.tsx", '<a href="#">x</a> <p>Lorem ipsum</p>') == []


def test_secrets_are_redacted(tmp_path):
    """The leaked key never appears in evidence or descriptions."""
    path = tmp_path / "c.py"
    content = 'KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n'
    path.write_text(content, encoding="utf-8")
    issues = SlopEngine(tmp_path).check_file(path, content)
    leaked = [i for i in issues if i.rule_id == "sec/leaked-key"]
    assert leaked
    assert "abcdefghijklmnop" not in leaked[0].evidence + leaked[0].description


# ─── Real-world calibration (from scanning Lovable/v0 apps vs hand-built repos) ─────────

@pytest.mark.parametrize("line", [
    'style={{ background: "linear-gradient(135deg, oklch(0.62 0.24 292), oklch(0.46 0.24 296))" }}',
    '<div className="bg-[radial-gradient(circle_at_17%_4%,rgb(139_92_246_/_0.34),transparent_38%)]" />',
    '<div className="bg-gradient-to-b from-blue-500 to-violet-500" />',
    '"radial-gradient(circle, rgba(80,170,255,.35), rgba(175,80,255,.3) 45%, transparent 72%)",',
])
def test_ai_gradient_any_color_syntax(tmp_path, line):
    assert "ui/ai-gradient" in scan(tmp_path, "A.tsx", line)


@pytest.mark.parametrize("line", [
    '<div className="bg-gradient-to-b from-slate-50 to-white" />',
    'style={{ background: "linear-gradient(180deg, #ffffff, #f4f4f5)" }}',
    '<div className="[mask-image:radial-gradient(circle,#000_52%,transparent_74%)]" />',
])
def test_neutral_gradients_are_fine(tmp_path, line):
    assert "ui/ai-gradient" not in scan(tmp_path, "A.tsx", line)


def test_gradient_through_css_variables(tmp_path):
    css = ":root { --primary: 262 83% 58%; --accent: 330 81% 60%; }\n" \
          ".hero { background: linear-gradient(135deg, hsl(var(--primary)), hsl(var(--accent))); }\n"
    assert "ui/ai-gradient" in scan(tmp_path, "index.css", css)


def test_neon_glow(tmp_path):
    glow = '<b className="shadow-[0_0_40px_rgba(139,92,246,0.5)]" />\n'
    assert "ui/neon-glow" in scan(tmp_path / "a", "A.tsx", glow * 3)
    assert "ui/neon-glow" not in scan(tmp_path / "b", "A.tsx", '<b className="shadow-[0_1px_2px_rgba(0,0,0,0.1)]" />\n' * 3)


def test_vendored_shadcn_components_are_skipped(tmp_path):
    extra = {"components.json": "{}"}
    code = '<input className="outline-none" />\n<div onClick={x}>y</div>\n'
    assert scan(tmp_path, "src/components/ui/input.tsx", code, extra=extra) == []
    assert "a11y/focus-removed" in scan(tmp_path, "src/components/Form.tsx", code, extra=extra)


def test_noisy_rules_aggregate_per_file(tmp_path):
    code = "".join(f"const v{n} = x as any;\n" for n in range(8))
    path = tmp_path / "a.ts"
    path.write_text(code, encoding="utf-8")
    engine = SlopEngine(tmp_path)
    issues = [i for i in engine.check_file(path, code) + engine.finalize() if i.rule_id == "slop/ts-any"]
    assert len(issues) == 1 and "8 occurrences" in issues[0].description


def test_commented_out_code_is_not_narration(tmp_path):
    code = '// import { supabase } from "@/integrations/supabase/client";\nexport const x = 1;\n'
    assert "slop/narrating-comment" not in scan(tmp_path, "client.ts", code)


def test_design_context_reads_project_tokens(tmp_path):
    from fixmyslop.core.slop.theme import ThemeIndex
    (tmp_path / "components.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"dependencies": {"lucide-react": "1"}}', encoding="utf-8")
    (tmp_path / "index.css").write_text(
        ":root { --primary: 221 83% 53%; --background: 0 0% 100%; --radius: 0.5rem; }\n"
        "body { font-family: 'Geist', sans-serif; }\n", encoding="utf-8")
    context = ThemeIndex.build(tmp_path).design_context()
    assert "blue" in context and "bg-primary" in context
    assert "lucide-react" in context and "Geist" in context and "0.5rem" in context


# ─── 2025–26 editorial dark-mode AI look (reported from a real FocusFlow extension) ─────

def test_new_ai_look_fixture_is_caught():
    """The FocusFlow-style demo graded A before these rules existed."""
    from pathlib import Path
    root = Path(__file__).parent.parent / "examples" / "slop-2026"
    engine = SlopEngine(root)
    issues = []
    for path in sorted(root.iterdir()):
        issues += engine.check_file(path, path.read_text(encoding="utf-8"))
    issues += engine.finalize()
    found = {i.rule_id for i in issues}
    assert {"ui/aphorism-copy", "ui/fake-chart", "ui/neon-glow", "ui/gimmick-animation",
            "ui/eyebrow-labels", "ui/two-tone-headline"} <= found


@pytest.mark.parametrize("rule_id, name, sloppy, clean", [
    ("ui/two-tone-headline", "Hero.tsx",
     '<h1 className="text-5xl">Make space for <span className="text-emerald-300">what matters.</span></h1>',
     '<h1 className="text-5xl">Block distracting sites during focus hours</h1>'),
    ("ui/fake-chart", "Card.tsx",
     "const sparkData = [12, 18, 9, 24, 30, 22, 28];",
     "const sparkData = usage.map((d) => d.minutes);"),
    ("ui/gimmick-animation", "dot.css",
     ".status-dot { animation: pulse 2s ease-in-out infinite; }",
     ".spinner { animation: spin 1s linear infinite; }"),
])
def test_new_ai_look_rules(tmp_path, rule_id, name, sloppy, clean):
    assert rule_id in scan(tmp_path / "bad", name, sloppy)
    assert rule_id not in scan(tmp_path / "good", name, clean)


def test_aphorism_copy_needs_repetition(tmp_path):
    one = "<p>Your data stays yours.</p>\n"
    assert "ui/aphorism-copy" not in scan(tmp_path / "a", "A.tsx", one)
    assert "ui/aphorism-copy" in scan(tmp_path / "b", "A.tsx", one + "<p>Less noise. More focus.</p>\n")


def test_eyebrow_labels_only_when_overused(tmp_path):
    label = '<p className="text-xs uppercase tracking-widest">Section</p>\n'
    assert "ui/eyebrow-labels" not in scan(tmp_path / "a", "A.tsx", label * 2)
    assert "ui/eyebrow-labels" in scan(tmp_path / "b", "A.tsx", label * 5)


def test_eyebrow_class_defined_in_css(tmp_path):
    css = {"styles.css": ".kicker { font-size: 11px; text-transform: uppercase; letter-spacing: .2em; }"}
    html = '<p class="kicker">One</p>\n' * 4
    assert "ui/eyebrow-labels" in scan(tmp_path, "index.html", html, extra=css)


def test_colored_radial_glow_any_hue(tmp_path):
    glow = '.orb { background: radial-gradient(circle, rgba(134, 239, 172, 0.35), transparent 70%); }\n'
    assert "ui/neon-glow" in scan(tmp_path / "a", "a.css", glow * 2)
    mask = '.fade { mask-image: radial-gradient(circle, #000 50%, transparent 80%); }\n'
    assert "ui/neon-glow" not in scan(tmp_path / "b", "a.css", mask * 2)


def test_icon_button_accepts_non_english_labels(tmp_path):
    """A button with a visible label in any language is not icon-only (found on a Russian-language app)."""
    code = "<button onClick={signOut}>\n  <LogOut className=\"h-4 w-4\" />\n  <span>Выйти</span>\n</button>\n"
    assert "a11y/icon-button" not in scan(tmp_path, "Shell.tsx", code)


def test_design_tokens_are_not_palette_sprawl(tmp_path):
    """Hex colors defined as CSS variables are the design system; only raw usage counts as sprawl."""
    tokens = "".join(f"  --c{n}: #{n:02x}{n:02x}{n:02x};\n" for n in range(40))
    assert "ui/palette-sprawl" not in scan(tmp_path / "a", "theme.css", ":root {\n" + tokens + "}\n")
    raw = "".join(f".c{n} {{ color: #{n:02x}{n:02x}{n:02x}; }}\n" for n in range(40))
    assert "ui/palette-sprawl" in scan(tmp_path / "b", "theme.css", raw)



# ─── Found on a real single-file AI page (room booking site) that scanned as "A" ─────────

def test_single_file_page_with_long_lines_is_scanned(tmp_path):
    """AI pages often put all CSS on a few huge lines; that must not be mistaken for a minified bundle."""
    from fixmyslop.core.scanner import Scanner
    css = "".join(f".c{n}{{color:#{n:06x};padding:{n}px}}" for n in range(400))
    page = tmp_path / "booking.html"
    page.write_text(f"<style>\n{css}\n</style>\n<a href=\"#\">x</a>\n", encoding="utf-8")
    assert [p.name for p, _ in Scanner(str(tmp_path)).scan()] == ["booking.html"]
    bundle = tmp_path / "vendor.js"
    bundle.write_text("var a=1;" * 2000, encoding="utf-8")
    assert "vendor.js" not in [p.name for p, _ in Scanner(str(tmp_path)).scan()]


def test_css_universal_selector_line_is_not_a_comment(tmp_path):
    line = "*{box-sizing:border-box}body{font-family:Inter,sans-serif}.field input{outline:0}\n"
    found = scan(tmp_path, "page.html", "<style>\n" + line + "</style>\n")
    assert "a11y/focus-removed" in found and "ui/default-font" in found


@pytest.mark.parametrize("rule_id, sloppy, clean", [
    ("ui/fake-social-proof", "<span>Loved by <strong>2,400+ happy travelers</strong></span>",
     '<select><option value="5">5+ guests</option></select>'),
    ("ui/fake-ratings", "const stays=[{name:'Loft',rating:'4.96',reviews:84}];",
     "const stays = await fetchStays();"),
    ("ui/fake-feature", "button.onclick = () => alert('Sign-in preview: connect your authentication provider here.');",
     "button.onclick = () => signIn();"),
    ("ui/two-tone-headline", "<style>.hero h1 em{color:#63845c}</style>\n<h1>Find a place<br>to <em>feel at home.</em></h1>",
     "<h1>Rooms in Pune from ₹950 a night</h1>"),
    ("ui/emoji-icons", '<span class="logo">⌂</span><span>✦ Handpicked</span>\n<p>✿</p>\n<p>⌖ Pune</p>\n',
     "<kbd>⌘K</kbd>\n"),
])
def test_room_booking_patterns(tmp_path, rule_id, sloppy, clean):
    assert rule_id in scan(tmp_path / "bad", "page.html", sloppy + "\n")
    assert rule_id not in scan(tmp_path / "good", "page.html", clean + "\n")


def test_aphorism_lovely_little_stays_voice(tmp_path):
    copy = "<p>Stays with soul</p>\n<p>Made for the way you want to travel.</p>\n<p>Good stays. Good stories.</p>\n"
    assert scan(tmp_path, "page.html", copy).count("ui/aphorism-copy") == 3


def test_placeholders_are_not_fake_testimonials(tmp_path):
    form = '<input placeholder="Jane Doe" />\n<input placeholder="Acme Inc." />\n'
    assert "ui/fake-social-proof" not in scan(tmp_path, "Form.tsx", form)


# ─── backend rules ───────────────────────────────────────────────────────────

def test_empty_tests_python_and_js(tmp_path):
    py = ("def test_creates_user():\n    create_user('a')\n\n"
          "def test_always_true():\n    assert True\n\n"
          "def test_real():\n    assert create_user('a').name == 'a'\n\n"
          "def test_raises():\n    with pytest.raises(ValueError):\n        create_user('')\n")
    found = [i for i in scan(tmp_path / "py", "tests/test_users.py", py) if i == "slop/empty-test"]
    assert len(found) == 2
    js = ("it('renders', () => {\n  render(<App />);\n});\n"
          "it('shows title', () => {\n  render(<App />);\n  expect(screen.getByText('Hi')).toBeTruthy();\n});\n")
    assert scan(tmp_path / "js", "src/App.test.tsx", js).count("slop/empty-test") == 1
    assert "slop/empty-test" not in scan(tmp_path / "app", "src/users.py", py)   # only in test files


def test_duplicate_functions_across_files(tmp_path):
    body = ("    total = 0\n    for item in items:\n        if item.active:\n            total += item.price\n"
            "    if total > 100:\n        total = total * 0.9\n    return round(total, 2)\n")
    (tmp_path / "a.py").write_text("def order_total(items):\n" + body, encoding="utf-8")
    renamed = body.replace("total", "amount").replace("items", "rows").replace("item", "row")
    (tmp_path / "b.py").write_text("def cart_sum(rows):\n" + renamed, encoding="utf-8")
    engine = SlopEngine(tmp_path)
    for name in ("a.py", "b.py"):
        engine.check_file(tmp_path / name, (tmp_path / name).read_text(encoding="utf-8"))
    dupes = [i for i in engine.finalize() if i.rule_id == "slop/duplicate-function"]
    assert len(dupes) == 1 and "order_total" in dupes[0].description


def test_pointless_wrappers_need_repetition(tmp_path):
    wrappers = "".join(f"def get_{n}(x):\n    return fetch_{n}(x)\n\n" for n in range(3))
    assert "slop/pointless-wrapper" in scan(tmp_path / "a", "svc.py", wrappers)
    assert "slop/pointless-wrapper" not in scan(tmp_path / "b", "svc.py", "def get(x):\n    return fetch(x)\n")


@pytest.mark.parametrize("rule_id, name, sloppy, clean", [
    ("slop/catch-rethrow", "a.py", "try:\n    run()\nexcept ValueError as e:\n    raise e\n",
     "try:\n    run()\nexcept ValueError as e:\n    raise RuntimeError('run failed') from e\n"),
    ("slop/catch-rethrow-js", "a.ts", "try { run() } catch (e) { throw e }", "try { run() } catch (e) { throw new Error(`run: ${e}`) }"),
    ("slop/ignored-error", "main.go", "if err != nil {\n}\n".replace("\n}", " }"), "if err != nil { return err }"),
    ("slop/ignored-error", "main.go", "if err != nil { return nil }", "if err != nil { return fmt.Errorf(\"load: %w\", err) }"),
    ("slop/empty-catch", "Main.java", "try { run(); } catch (IOException e) {}", "try { run(); } catch (IOException e) { log.error(\"run\", e); }"),
    ("slop/print-stacktrace", "Main.java", "try { run(); } catch (Exception e) { e.printStackTrace(); }",
     "try { run(); } catch (Exception e) { throw new RuntimeException(e); }"),
])
def test_backend_rules(tmp_path, rule_id, name, sloppy, clean):
    assert rule_id in scan(tmp_path / "bad", name, sloppy + "\n")
    assert rule_id not in scan(tmp_path / "good", name, clean + "\n")


def test_empty_test_understands_helpers_and_smoke_tests(tmp_path):
    js = ('const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9);\n'
          'test("unit prices", () => {\n  close(price(3), 0.225);\n});\n'
          'test("recording never throws", async () => {\n  await record();\n});\n')
    assert "slop/empty-test" not in scan(tmp_path / "js", "src/usage.test.ts", js)
    py = ("def check_user(u):\n    assert u.id\n\n"
          "def test_create():\n    check_user(create())\n\n"
          "def test_import_does_not_raise():\n    import app\n")
    assert "slop/empty-test" not in scan(tmp_path / "py", "tests/test_users.py", py)


def test_syntax_rules_ignore_text_in_python(tmp_path):
    doc = 'GUIDE = "Never swallow errors: no `catch {}`, no `if err != nil { return nil }`."\n'
    found = scan(tmp_path, "guide.py", doc)
    assert "slop/empty-catch" not in found and "slop/ignored-error" not in found


# ─── precision fixes from the hand-reviewed benchmark sample ────────────────

@pytest.mark.parametrize("rule_id, name, not_slop", [
    ("a11y/clickable-div", "M.tsx", '<div className="fixed inset-0 bg-black/60" onClick={close}>\n'),
    ("a11y/clickable-div", "M.tsx", "<div className=\"rounded-2xl\" onClick={(e) => e.stopPropagation()}>\n"),
    ("a11y/icon-button", "N.tsx", '<Button asChild size="icon">\n  <Link to="/x" aria-label="Partners"><Icon /></Link>\n</Button>\n'),
    ("a11y/focus-removed", "C.tsx", '"relative flex items-center outline-none aria-selected:bg-accent"\n'),
    ("slop/empty-catch", "api.ts", "const body = await request.json().catch(() => null);\n"),
    ("slop/ts-any", "p.ts", 'const p = "- code: any article number printed on the label";\n' * 6),
])
def test_benchmark_false_positives_stay_fixed(tmp_path, rule_id, name, not_slop):
    assert rule_id not in scan(tmp_path, name, not_slop)


def test_generated_files_are_not_judged(tmp_path):
    gen = "/* eslint-disable */\n// This file was automatically generated by TanStack Router.\nexport const x: any = 1;\n"
    assert scan(tmp_path, "src/routeTree.gen.ts", gen) == []


def test_overlays_may_blur_and_cast_big_shadows(tmp_path):
    overlay = '<div className="fixed inset-0 bg-black/60 backdrop-blur-sm" />\n' * 4
    assert "ui/glassmorphism" not in scan(tmp_path / "a", "M.tsx", overlay)
    dialog = '<div className="fixed inset-x-3 top-1/2 rounded-2xl shadow-2xl" />\n' * 9
    assert "ui/heavy-shadows" not in scan(tmp_path / "b", "M.tsx", dialog)
    cards = '<div className="rounded-xl bg-white/10 backdrop-blur-lg shadow-2xl" />\n' * 9
    found = scan(tmp_path / "c", "Cards.tsx", cards)
    assert "ui/glassmorphism" in found and "ui/heavy-shadows" in found


def test_registry_lookup_streams_and_retries(monkeypatch, tmp_path):
    """--online must not download multi-MB package metadata, and must survive one network blip."""
    import requests
    from fixmyslop.core.slop.rules_deps import DependencyIndex
    calls = []

    class Response:
        def __init__(self, status):
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("stream")))
        if "flaky" in url and len([c for c in calls if "flaky" in c[0]]) == 1:
            raise requests.ConnectionError("blip")
        return Response(404 if "made-up" in url else 200)

    monkeypatch.setattr(requests, "get", fake_get)
    index = DependencyIndex(tmp_path, online=True)
    assert index.exists_on_registry("made-up-pkg", "npm") is False
    assert index.exists_on_registry("flaky-pkg", "npm") is True        # retried after the blip
    assert index.exists_on_registry("requests", "pypi") is True
    assert all(stream for _, stream in calls)
    assert any("pypi.org/simple/requests" in url for url, _ in calls)


def test_metacall_false_positives_stay_fixed(tmp_path):
    """Found scanning metacall/deploy: hand-written code that must not be flagged."""
    js = ("import { ok, strictEqual as eq } from 'assert';\n"
          "describe('deploy', () => {\n"
          "  it('parses args', () => {\n"
          "    const args = parse(['--dev']);\n"
          "    eq(args.dev, true);\n"
          "  });\n"
          "  it('works', () => { ok(run()); });\n"
          "});\n")
    assert "slop/empty-test" not in scan(tmp_path / "a", "src/test/cli.spec.ts", js)

    commented = ("const main = () => {\n"
                 "  run();\n"
                 "};\n"
                 "// const old = () => {\n"
                 "// ...\n"
                 "// };\n"
                 "main();\n")
    assert "slop/elided-code" not in scan(tmp_path / "b", "hooks/pre-push.js", commented)

    elided = "function save() {\n  validate();\n  // ...\n  persist();\n}\n"
    assert "slop/elided-code" in scan(tmp_path / "c", "src/save.js", elided)

    todo = "export async function poll() {\n  // TODO: Implement polling with waitFor\n  return fetchStatus();\n}\n"
    assert "slop/placeholder-impl" not in scan(tmp_path / "d", "src/logs.ts", todo)


def test_more_languages_are_scanned(tmp_path):
    ruby = "def total(items)\n  sum = 0\n  # ... existing code ...\n  sum\nend\n"
    assert "slop/elided-code" in scan(tmp_path / "rb", "lib/total.rb", ruby)
    dart = "void load() {\n  try {\n    fetch();\n  } catch (e) {}\n}\n"
    assert "slop/empty-catch" in scan(tmp_path / "dart", "lib/load.dart", dart)
    groovy = "def load() {\n  try { run() } catch (Exception e) { e.printStackTrace() }\n}\n"
    assert "slop/print-stacktrace" in scan(tmp_path / "groovy", "src/Load.groovy", groovy)


ENV = ("# Supabase\n"
       "SUPABASE_URL=https://abcd.supabase.co\n"
       "SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.c2VydmljZV9yb2xl\n"
       "DATABASE_URL=postgres://admin:hunter2pass@db.example-prod.io:5432/app\n"
       "PORT=3000\n")


def test_env_secret_in_a_file_git_will_commit(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert "sec/env-secret" in scan(tmp_path, ".env", ENV)
    # ignored by git: the normal, safe setup
    (tmp_path / ".gitignore").write_text(".env*\n!.env.example\n", encoding="utf-8")
    assert "sec/env-secret" not in scan(tmp_path, ".env", ENV)
    # templates hold placeholders and are meant to be committed
    template = "OPENAI_API_KEY=your-openai-key\nDATABASE_URL=postgres://user:pass@localhost:5432/app\n"
    assert "sec/env-secret" not in scan(tmp_path, ".env.example", template)
    assert "sec/env-secret" not in scan(tmp_path / "local", ".env",
                                        "DATABASE_URL=postgres://postgres:postgres@localhost:5432/dev\n")


def test_env_public_by_design_keys_stay_quiet(tmp_path):
    """Lovable/Supabase apps commit browser keys on purpose; that's not a leak."""
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    lovable = ('SUPABASE_PROJECT_ID="nwoxhqzlmabcdefghijk"\n'
               'SUPABASE_PUBLISHABLE_KEY="sb_publishable_AbCdEfGhIjKlMnOpQrStUv"\n'
               'VITE_SUPABASE_PUBLISHABLE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.YW5vbg"\n'
               'VITE_SUPABASE_URL="https://nwoxhqzlmabcdefghijk.supabase.co"\n'
               'VITE_LOVABLE_CONNECTOR_GOOGLE_MAPS_BROWSER_KEY="AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"\n')
    found = scan(tmp_path, ".env", lovable)
    assert "sec/env-secret" not in found and "sec/leaked-key" not in found
    assert "sec/env-secret" in scan(tmp_path, ".env", lovable + "STRIPE_SECRET_KEY=rk_live_51HxyzAbcdEfgh\n")


def test_env_files_are_scanned_and_public_secrets_flagged(tmp_path):
    from fixmyslop.core.scanner import Scanner
    found = scan(tmp_path, ".env.local", "NEXT_PUBLIC_OPENAI_API_KEY=sk-proj-abc\nNEXT_PUBLIC_SITE=https://x.dev\n")  # fixmyslop-ignore[sec/public-env-secret]: test fixture
    assert "sec/public-env-secret" in found
    names = {p.name for p in Scanner(str(tmp_path)).files()}
    assert ".env.local" in names
    (tmp_path / "env.ts").write_text("export const env = {}\n", encoding="utf-8")
    assert "slop/elided-code" not in scan(tmp_path, "env.ts", "export const env = {}\n")


def test_placeholder_keep_it_simple_for_now(tmp_path):
    code = "export function windowName() {\n  // Native bindings would give the real window; keeping it simple for now\n  return 'Desktop';\n}\n"
    assert "slop/placeholder-impl" in scan(tmp_path, "src/window.ts", code)
