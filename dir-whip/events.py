"""Verdict emission deep module: stats counters + stats.jsonl + leveled log + bus fanout in one emit (spec 5.13, spec 5.14).

Records one single-line verdict event per guard decision and fans out
verdict-derived bus events (blocked / external-write) under a geometric
outside-root basis; working_dir_root and profile resolve from state,
session_id / is_subagent stay explicit params. No host imports; single
definition point for every static RULE_KEY_* (values frozen).

Layer: core
Refs: spec 5.13, spec 5.14, SCR-052
Key exports:
  - RULE_KEY_* -- the frozen static rule_key constants (single definition point).
  - emit -- emit ONE verdict event (stats + jsonl + leveled log + bus sidecar); never raises.
  - bus_emit -- bare-name dir-whip bus event emit; silent degradation when the bus is absent.
"""

import datetime
import json
import logging

from . import state

from .paths import normalize_target, relativize_target, within_working_dir

from .stats import stats_record

logger = logging.getLogger("dir-whip")

# ---------------------------------------------------------------- Frozen rule_key constants
# Single definition point for every static rule_key; VALUES ARE FROZEN.
# The dynamic prefixes pre-command:<command> / landed:<tool> stay built
# at their emission sites.

# Guard verdict rule_keys.
RULE_KEY_EXTERNAL_WRITE = "external-write"
RULE_KEY_RUNTIME_ALLOWLIST = "runtime-allowlist"
RULE_KEY_TIER0_ALLOWLIST = "tier0-allowlist"
RULE_KEY_ALLOWED_FILE = "allowed-file"
RULE_KEY_SESSION_DIR = "session-dir"
RULE_KEY_ROOT_FILE = "root-file"
RULE_KEY_NON_SESSION_DIR = "non-session-dir"
RULE_KEY_FAIL_OPEN = "fail-open"
RULE_KEY_TERMINAL_WRITE_UNCERTAIN = "terminal-write-uncertain"

# Terminal interception rule_keys.
RULE_KEY_TERMINAL_REDIRECT = "terminal-redirect"
RULE_KEY_TERMINAL_TOUCH = "terminal-touch"
RULE_KEY_TERMINAL_CP_MV = "terminal-cp-mv"
RULE_KEY_TERMINAL_MKDIR = "terminal-mkdir"
RULE_KEY_TERMINAL_DOWNLOAD = "terminal-download"

# Audit / session / observe rule_keys.
SESSION_DIR_LIMIT_RULE_KEY = "session-dir-limit"
RULE_KEY_WRITE_AUDIT_VIOLATION = "write-audit-violation"
RULE_KEY_WRITE_AUDIT_GATE_BLOCK = "write-audit-gate-block"
RULE_KEY_WRITE_AUDIT_SETTLE_REJECTED = "write-audit-settle-rejected"
RULE_KEY_WRITE_AUDIT_SETTLE = "write-audit-settle"
RULE_KEY_PRE_VERIFY_NUDGE = "pre-verify-nudge"
RULE_KEY_RUNTIME_ALLOWLIST_ADD = "runtime-allowlist-add"
RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED = "allow-path-subagent-rejected"
RULE_KEY_ALLOW_PATH_ROOT_REJECTED = "allow-path-root-rejected"
RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED = "allow-path-external-rejected"
RULE_KEY_SESSION_REMINDER = "session-reminder"
RULE_KEY_SESSION_REMINDER_FALLBACK = "session-reminder-fallback"
RULE_KEY_ORPHAN_NOTICE = "orphan-notice"
RULE_KEY_ORPHAN_NOTICE_FALLBACK = "orphan-notice-fallback"
RULE_KEY_SUBAGENT_START = "subagent-start"
RULE_KEY_SUBAGENT_STOP = "subagent-stop"
RULE_KEY_APPROVAL_GRANTED = "approval:granted"
RULE_KEY_APPROVAL_DENIED = "approval:denied"
RULE_KEY_APPROVAL_REQUESTED = "approval-requested"

# Verdict rule_keys that never fan out to the bus: their callers emit
# their own bus events (approval verdicts, audit gate block, audit
# violation) or are allow_path entry-gating rejections (stats row only).
_BUS_SKIP_RULE_KEYS = frozenset((
    RULE_KEY_APPROVAL_GRANTED,
    RULE_KEY_APPROVAL_DENIED,
    RULE_KEY_WRITE_AUDIT_GATE_BLOCK,
    RULE_KEY_WRITE_AUDIT_VIOLATION,
    RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED,
    RULE_KEY_ALLOW_PATH_ROOT_REJECTED,
    RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED,
))


def _verdict_reason(outcome):
    """Short reason string for a verdict event (5.13)."""
    if outcome == "external-write":
        return "target outside working_dir_root"
    return None


def emit(outcome, tool, rule_key, target, reason, session_id, is_subagent):
    """Emit ONE single-line structured verdict event.

    Levels: block / fail-open -> WARNING; a GEOMETRICALLY outside-root
    target (same normalize_target + within_working_dir domain as the
    classify chain) or the external-write outcome string (fail-open
    fallback shapes) -> INFO; other allows -> DEBUG. Also records the
    verdict via stats (counters + stats.jsonl). Verdict-derived bus
    events (blocked / external-write) use the same geometric basis and
    skip _BUS_SKIP_RULE_KEYS. working_dir_root / profile resolve from
    state; session_id / is_subagent describe the judged call. Never
    raises (fail-open).
    """
    try:
        working_dir_root = state.session.working_dir_root
        stats_record(
            outcome, tool, rule_key, target=target, reason=reason,
            is_subagent=bool(is_subagent), working_dir_root=working_dir_root,
        )
        rel_target = relativize_target(target, working_dir_root)        # The log/bus routing basis is GEOMETRIC (computed fresh,
        # chain-homologous); the outcome string stays as fallback so
        # fail-open shapes keep their levels.
        outside = (
            bool(target) and bool(working_dir_root)
            and not within_working_dir(
                normalize_target(target, working_dir_root), working_dir_root
            )
        )
        event = {
            "outcome": outcome,
            "reason": reason,
            "tool": tool,
            "target": rel_target,
            "rule_key": rule_key,
            "is_subagent": bool(is_subagent),
            "session_id": session_id,
            # v2.25 SCR-057 D1: multi-profile attribution inside one
            # desktop-process log (session_id alone is ambiguous).
            "profile": state.session.session_profile,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        line = json.dumps(event)
        if outcome in ("block", "fail-open"):
            logger.warning("dir-whip: verdict %s", line)
        elif outside or outcome == "external-write":
            logger.info("dir-whip: verdict %s", line)
        else:
            logger.debug("dir-whip: verdict %s", line)
        # Verdict-derived bus events (privacy-shaped relative target),
        # same geometric basis as the log routing.
        if outcome == "block" and rule_key not in _BUS_SKIP_RULE_KEYS:
            bus_emit("blocked", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
        elif (
            (outside or outcome == "external-write")
            and rule_key not in _BUS_SKIP_RULE_KEYS
        ):
            bus_emit("external-write", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
    except Exception as exc:
        logger.debug("dir-whip: verdict emission failed (fail-open): %s", exc)


def bus_emit(event_name, payload):
    """Emit a bare-name dir-whip event; silent degradation.

    Bus absent (capability flag off, no ctx, or ctx.emit missing) or emit
    raising -> exactly ONE DEBUG log line per attempt, no error. The host
    forces the ``dir-whip:`` namespace, so only the bare name is passed
    (a namespaced name raises ValueError, fail-closed).
    """
    try:
        if not state.session.emit_enabled:
            logger.debug(
                "dir-whip: event bus unavailable, skipping emit(%s)",
                event_name,
            )
            return
        ctx = state.session.registered_ctx
        if not ctx or not callable(getattr(ctx, "emit", None)):
            logger.debug(
                "dir-whip: event bus unavailable, skipping emit(%s)",
                event_name,
            )
            return
        ctx.emit(event_name, payload or {})
    except Exception as exc:
        logger.debug(
            "dir-whip: event emit failed for %s (fail-open): %s",
            event_name, exc,
        )


__all__ = [
    "emit",
    "bus_emit",
    "SESSION_DIR_LIMIT_RULE_KEY",
    "RULE_KEY_EXTERNAL_WRITE",
    "RULE_KEY_RUNTIME_ALLOWLIST",
    "RULE_KEY_TIER0_ALLOWLIST",
    "RULE_KEY_ALLOWED_FILE",
    "RULE_KEY_SESSION_DIR",
    "RULE_KEY_ROOT_FILE",
    "RULE_KEY_NON_SESSION_DIR",
    "RULE_KEY_FAIL_OPEN",
    "RULE_KEY_TERMINAL_WRITE_UNCERTAIN",
    "RULE_KEY_TERMINAL_REDIRECT",
    "RULE_KEY_TERMINAL_TOUCH",
    "RULE_KEY_TERMINAL_CP_MV",
    "RULE_KEY_TERMINAL_MKDIR",
    "RULE_KEY_TERMINAL_DOWNLOAD",
    "RULE_KEY_WRITE_AUDIT_VIOLATION",
    "RULE_KEY_WRITE_AUDIT_GATE_BLOCK",
    "RULE_KEY_WRITE_AUDIT_SETTLE_REJECTED",
    "RULE_KEY_WRITE_AUDIT_SETTLE",
    "RULE_KEY_PRE_VERIFY_NUDGE",
    "RULE_KEY_RUNTIME_ALLOWLIST_ADD",
    "RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED",
    "RULE_KEY_ALLOW_PATH_ROOT_REJECTED",
    "RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED",
    "RULE_KEY_SESSION_REMINDER",
    "RULE_KEY_SESSION_REMINDER_FALLBACK",
    "RULE_KEY_ORPHAN_NOTICE",
    "RULE_KEY_ORPHAN_NOTICE_FALLBACK",
    "RULE_KEY_SUBAGENT_START",
    "RULE_KEY_SUBAGENT_STOP",
    "RULE_KEY_APPROVAL_GRANTED",
    "RULE_KEY_APPROVAL_DENIED",
    "RULE_KEY_APPROVAL_REQUESTED",
]
