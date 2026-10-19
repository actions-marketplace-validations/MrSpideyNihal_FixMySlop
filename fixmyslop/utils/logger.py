"""
Centralized logger — call get_logger(__name__) in every module.
Logs to console (WARNING+) and to file (DEBUG+).
"""
import logging
from fixmyslop.macros import LOG_FILE


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)

    # File handler — DEBUG level. Skipped where the home directory is read-only (serverless functions,
    # read-only containers): logging must never stop a scan.
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(fh)
    except OSError:
        pass

    # Console handler — WARNING level (INFO goes to the log file only, keeping CLI output clean)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)
    return logger
