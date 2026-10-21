"""The 'new version available' notice: once a day, never in CI, off with FIXMYSLOP_NO_UPDATE_CHECK, never installs."""
import json
import time

import pytest
from typer.testing import CliRunner

from fixmyslop.utils import update_check


@pytest.fixture()
def check_on(monkeypatch, tmp_path):
    """Turn the check on (conftest turns it off), isolate the cache, and fake PyPI."""
    monkeypatch.delenv("FIXMYSLOP_NO_UPDATE_CHECK", raising=False)
    for name in update_check._CI_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(update_check, "CACHE_FILE", tmp_path / "update.json")
    calls = []

    def fake_pypi(timeout=update_check.TIMEOUT):
        calls.append(time.time())
        return "99.0.0"

    monkeypatch.setattr(update_check, "fetch_latest", fake_pypi)
    return calls


@pytest.mark.parametrize("latest, current, newer", [
    ("0.6.8", "0.6.7", True), ("0.7.0", "0.6.12", True), ("0.6.10", "0.6.9", True),
    ("0.6.7", "0.6.7", False), ("0.6.6", "0.6.7", False), ("1.0.0rc1", "0.9.9", True),
])
def test_version_comparison(latest, current, newer):
    assert update_check.is_newer(latest, current) is newer


def test_tells_you_about_a_newer_version_and_caches_for_a_day(check_on):
    assert update_check.available_update() == "99.0.0"
    assert update_check.available_update() == "99.0.0"
    assert len(check_on) == 1                                   # second call came from the cache
    cache = json.loads(update_check.CACHE_FILE.read_text())
    assert cache["latest"] == "99.0.0"
    assert "pip install -U fixmyslop" in update_check.notice("99.0.0")


def test_off_switch_and_ci(check_on, monkeypatch):
    monkeypatch.setenv("FIXMYSLOP_NO_UPDATE_CHECK", "1")
    assert update_check.available_update() is None
    monkeypatch.delenv("FIXMYSLOP_NO_UPDATE_CHECK")
    monkeypatch.setenv("CI", "true")
    assert update_check.available_update() is None
    assert check_on == []                                       # PyPI never contacted


def test_offline_is_silent_and_waits_a_day(check_on, monkeypatch):
    monkeypatch.setattr(update_check, "fetch_latest", lambda timeout=1: None)
    assert update_check.available_update() is None
    assert json.loads(update_check.CACHE_FILE.read_text())["latest"] is None


def test_up_to_date_shows_nothing(check_on, monkeypatch):
    monkeypatch.setattr(update_check, "fetch_latest", lambda timeout=1: update_check.APP_VERSION)
    assert update_check.available_update() is None


def test_version_command_prints_the_notice(check_on):
    from fixmyslop.cli.cli_app import app
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "99.0.0 is available" in result.output and "pip install -U fixmyslop" in result.output


def test_app_reports_the_update(check_on, tmp_path):
    from fixmyslop.web.server import UIApp
    data = UIApp(tmp_path).get("/api/update", {})
    assert data["latest"] == "99.0.0" and data["command"] == "pip install -U fixmyslop"
