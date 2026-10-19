"""Regenerate README screenshots: python screenshots/make_screenshots.py

- screenshots/cli.svg                 terminal output of `fixmyslop scan examples/slop-app`
- screenshots/app-*.png               the `fixmyslop ui` app (needs Chrome or Edge installed)
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402

from fixmyslop.cli import cli_output  # noqa: E402
from fixmyslop.cli.cli_app import run_scan  # noqa: E402
from fixmyslop.core.report_builder import relative_path  # noqa: E402
from fixmyslop.macros import APP_NAME, APP_VERSION  # noqa: E402
from fixmyslop.web.server import UIApp, _Handler  # noqa: E402

OUT = Path(__file__).resolve().parent
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]


def find_browser():
    for candidate in BROWSERS:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    return None


def shoot(browser, url, out, scheme, size="1280,860", wait_for=None):
    """Screenshot a URL. Prefers Playwright (waits for real content), falls back to --headless --screenshot."""
    width, height = (int(x) for x in size.split(","))
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            chromium = pw.chromium.launch(executable_path=browser if Path(browser).exists() else None)
            page = chromium.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
            page.goto(url)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=30000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(out))
            chromium.close()
    except ImportError:
        profile = tempfile.mkdtemp(prefix="fms-shot-")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                        f"--window-size={size}",
                        f"--blink-settings=preferredColorScheme={0 if scheme == 'dark' else 1}",
                        f"--screenshot={out}", url], capture_output=True, timeout=120)
        shutil.rmtree(profile, ignore_errors=True)
    print("wrote", out)


def main():
    demo = ROOT / "examples" / "slop-app"
    report, _ = run_scan([str(demo)], use_static=False)

    console = Console(record=True, width=92, force_terminal=True, color_system="truecolor")
    cli_output.console = console
    console.print("[dim]$[/dim] fixmyslop scan examples/slop-app\n")
    cli_output.print_banner(APP_NAME, APP_VERSION)
    cli_output.print_slop_report(report, lambda p: relative_path(report, p), limit=14,
                                 fixable_count=3, agent_hint="Claude Code", target="examples/slop-app")
    console.save_svg(str(OUT / "cli.svg"), title="fixmyslop scan")
    print("wrote", OUT / "cli.svg")

    browser = find_browser()
    if not browser:
        print("No Chrome/Edge found: skipping PNG screenshots")
        return

    # The app: scan a throwaway copy of the demo so nothing in the repo changes.
    work = Path(tempfile.mkdtemp(prefix="fms-app-")) / "my-website"
    shutil.copytree(demo, work)
    app = UIApp(work)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_Handler,), {"app": app}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    request = urllib.request.Request(base + "/api/scan", data=json.dumps({"static": False}).encode(),
                                     headers={"X-FixMySlop-Token": app.token, "Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=120).read()  # nosec B310 - our own 127.0.0.1 server
    ready = {"overview": ".score", "issues": ".code .hit"}
    for view in ("overview", "issues"):
        for scheme in ("light", "dark"):
            shoot(browser, f"{base}/#/{view}", OUT / f"app-{view}-{scheme}.png", scheme, wait_for=ready[view])
    httpd.shutdown()
    shutil.rmtree(work.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
