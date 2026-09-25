"""Dedicated diagnostic log setup -- dir-whip.log (spec 5.13).

Profile-aware DEBUG-full file log attached to the "dir-whip" logger at
register(), capturing breadcrumbs below the host agent.log INFO+
threshold. Fail-open tier chain: CLH ConcurrentRotatingFileHandler ->
stdlib RotatingFileHandler -> console only. Rotation 5 MiB x 3,
delay=True, utf-8; absolute paths are allowed (local diagnostic file,
no secret-class content). Known limits: multi-profile interleave in one
desktop process; stdlib-tier WinError 32 risk on Windows.

Layer: core
Refs: spec 5.13, SCR-026, SCR-027
Key exports:
  - setup -- attach the diagnostic file handler; idempotent, fail-open, never raises.
  - diagnostic_log_path -- the session profile's dir-whip.log path (single source for report.py).
"""

import logging
import logging.handlers

from . import state
from .paths import dirwhip_home

# Tier 1: cross-process-safe rotation (host venv); absence -> stdlib tier.
try:
    from concurrent_log_handler import ConcurrentRotatingFileHandler
except ImportError:
    ConcurrentRotatingFileHandler = None

logger = logging.getLogger("dir-whip")

LOG_FILE_NAME = "dir-whip.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB (aligned with the host agent.log convention)
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def diagnostic_log_path():
    """dir-whip.log location: the session profile's home dir-whip dir.

    Profile-aware via paths.dirwhip_home, mirroring stats_jsonl_path: a
    profile-home process (HERMES_HOME IS <root>/profiles/<name>) uses
    HERMES_HOME itself, a root-home process resolves
    HERMES_HOME/profiles/<name>; no session profile set yet
    (register-time attach) uses HERMES_HOME directly.
    """
    return dirwhip_home(state.session.session_profile) / LOG_FILE_NAME


def setup():
    """Attach the diagnostic file handler to the 'dir-whip' logger.

    Idempotent via state.session.log_handler_installed. Fail-open: never
    raises -- log setup must not break registration. The flag is set only
    when a file handler was installed; console-only degradation leaves it
    False so a later call may retry.
    """
    if state.session.log_handler_installed:
        return
    try:
        handler = _attach_handler()
    except Exception as exc:
        logger.debug(
            "dir-whip: diagnostic log setup failed (console only): %s", exc
        )
        return
    if handler is not None:
        state.session.log_handler_installed = True


def _attach_handler():
    """Build and attach one file handler; return it (None = console only).

    Tier 1 CLH -> tier 2 stdlib -> tier 3 no file handler. The logger
    level is raised to DEBUG so sub-INFO breadcrumbs are captured;
    records keep propagating, so host INFO+ handlers are unchanged.
    """
    log_path = diagnostic_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # surfaced by the first emit failure (fail-open)
    handler = None
    if ConcurrentRotatingFileHandler is not None:
        try:
            handler = ConcurrentRotatingFileHandler(
                str(log_path),
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                delay=True,
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(
                "dir-whip: CLH handler unavailable (%s); stdlib fallback", exc
            )
            handler = None
    if handler is None:
        try:
            handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                delay=True,
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(
                "dir-whip: stdlib handler unavailable (%s); console only", exc
            )
            return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    dir_logger = logging.getLogger("dir-whip")
    dir_logger.setLevel(logging.DEBUG)
    dir_logger.addHandler(handler)
    return handler


__all__ = ["setup", "diagnostic_log_path"]
