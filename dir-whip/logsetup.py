"""Dedicated diagnostic log setup (spec 5.13 v2.8 R5) — dir-whip.log.

Attaches a DEBUG-full rotating file handler to the existing 'dir-whip'
logger at register() so every logger.debug/info/warning line is captured
in <HERMES_HOME>/dir-whip/dir-whip.log — including the DEBUG breadcrumbs
below the host agent.log INFO+ threshold (allow-class verdicts, fail-open
handling paths). Three-tier fail-open handler chain:
concurrent_log_handler.ConcurrentRotatingFileHandler (cross-process-safe
rotation, installed in the host venv; a third-party import does not
violate the ADR-0007 core zero-host-import red line) -> stdlib
logging.handlers.RotatingFileHandler -> console only (no file handler).
Parameters: maxBytes=5 MiB, backupCount=3, delay=True (file created on
first write), encoding="utf-8". Privacy: absolute paths ARE allowed (a
local diagnostic file; diagnostic value first; no secret-class content
in dir-whip messages). Known limitations (spec 5.13): with one desktop
process serving multiple profiles the log lands in the registering
profile's directory and lines from other profiles interleave; the stdlib
fallback path has the known Windows multi-process rotation WinError 32
risk. No host imports (ADR-0007); the path layout mirrors
stats.stats_jsonl_path via paths.profile_home (SCR-026/027
profile-aware recognition).
"""

import logging
import logging.handlers

from . import state
from .paths import get_hermes_home, profile_home

# Tier 1: cross-process-safe rotation (host venv). The try-import is the
# ADR-0007 same-pattern fail-open loading; absence -> None -> stdlib tier.
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

    Profile-aware placement mirroring stats.stats_jsonl_path (spec 5.13
    R5; single source of truth for the log path — report.py reuses this):
    the path follows the SESSION profile (set at on_session_start) via
    paths.profile_home, so a profile-home process (HERMES_HOME IS
    <root>/profiles/<name>) uses HERMES_HOME itself while a root-home
    process resolves HERMES_HOME/profiles/<name>. When no session profile
    is set yet (register-time attach), HERMES_HOME is used directly.
    """
    home = get_hermes_home()
    if state.session.session_profile:
        home = profile_home(home, state.session.session_profile)
    return home / "dir-whip" / LOG_FILE_NAME


def setup():
    """Attach the diagnostic file handler to the 'dir-whip' logger.

    Idempotent via state.session.log_handler_installed (never attaches
    twice). Fail-open: any failure degrades down the three-tier chain and
    never raises — log setup must not break registration. The flag is set
    only when a file handler was actually installed; a total degradation
    (console only) leaves it False so a later call may retry.
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

    Tier 1: ConcurrentRotatingFileHandler (cross-process-safe rotation).
    Tier 2: stdlib RotatingFileHandler. Tier 3: skip the file handler
    entirely (console only). The logger level is raised to DEBUG so the
    breadcrumbs below the host INFO+ threshold are captured; records keep
    propagating, so host INFO+ handlers are unchanged.
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
