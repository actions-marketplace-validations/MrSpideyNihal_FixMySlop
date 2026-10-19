"""Logging must never stop a scan, even where the home directory is read-only (serverless, containers)."""
import logging

from fixmyslop.utils import logger as logger_module


def test_logger_works_without_a_writable_log_file(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")                    # mkdir under a file fails with OSError
    monkeypatch.setattr(logger_module, "LOG_FILE", blocker / "logs" / "fixmyslop.log")

    log = logger_module.get_logger("fixmyslop.test.readonly")
    log.warning("still works")

    assert not any(isinstance(h, logging.FileHandler) for h in log.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in log.handlers)
