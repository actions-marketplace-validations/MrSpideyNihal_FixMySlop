"""
Tell people when a newer FixMySlop is on PyPI. It never installs anything.

At most one request a day (cached in ~/.fixmyslop/update.json), a short timeout, silent when offline,
skipped in CI and when FIXMYSLOP_NO_UPDATE_CHECK is set.
"""
import json
import os
import re
import threading
import time
import urllib.request
from typing import Callable, Optional

from fixmyslop.macros import APP_VERSION, CONFIG_DIR

PYPI_URL = "https://pypi.org/pypi/fixmyslop/json"
CACHE_FILE = CONFIG_DIR / "update.json"
CHECK_EVERY = 24 * 3600
TIMEOUT = 1.5
UPGRADE_COMMAND = "pip install -U fixmyslop"
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "TF_BUILD", "JENKINS_URL")


def enabled() -> bool:
    """Off with FIXMYSLOP_NO_UPDATE_CHECK=1, and in CI (pinned versions there are on purpose)."""
    if os.environ.get("FIXMYSLOP_NO_UPDATE_CHECK", "").strip().lower() not in ("", "0", "false", "no"):
        return False
    return not any(os.environ.get(name) for name in _CI_VARS)


def _version_key(version: str) -> tuple:
    return tuple(int(m.group()) if (m := re.match(r"\d+", part)) else 0 for part in version.split("."))


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return _version_key(latest) > _version_key(current)


def _read_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(latest: Optional[str]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"checked": time.time(), "latest": latest}), encoding="utf-8")
    except OSError:
        pass                                                   # read-only home: just check again next time


def fetch_latest(timeout: float = TIMEOUT) -> Optional[str]:
    """The newest version on PyPI, or None when offline or PyPI is slow."""
    request = urllib.request.Request(PYPI_URL, headers={"User-Agent": f"fixmyslop/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310: fixed https URL
            return str(json.load(response)["info"]["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None          # offline, timed out (both OSError) or an unexpected response: no notice


def latest_version() -> Optional[str]:
    """The newest version, from the daily cache when it's fresh."""
    cache = _read_cache()
    if time.time() - float(cache.get("checked") or 0) < CHECK_EVERY:
        return cache.get("latest")
    latest = fetch_latest()
    _write_cache(latest)                                       # offline also waits a day before retrying
    return latest


def available_update() -> Optional[str]:
    """The newer version to tell the user about, or None."""
    if not enabled():
        return None
    latest = latest_version()
    return latest if latest and is_newer(latest) else None


def start() -> Callable[[float], Optional[str]]:
    """Check in the background while a scan runs; call the result with how long you're willing to wait."""
    result: list = [None]

    def work():
        result[0] = available_update()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    def finish(wait: float = 1.0) -> Optional[str]:
        thread.join(wait)
        return result[0]

    return finish


def notice(latest: str) -> str:
    return f"FixMySlop {latest} is available (you have {APP_VERSION}): {UPGRADE_COMMAND}"
