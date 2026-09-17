"""Verdict emission deep module: stats counters + stats.jsonl + leveled log + 5.14 bus fanout in one emit (spec 5.13, spec 5.14).

Records one single-line verdict event per guard decision and fans out verdict-derived bus events (blocked / external-write) under a geometric outside-root basis; working_dir_root and profile resolve from state here while session_id / is_subagent stay explicit emit params (Ruling 4). No host imports (SCR-035 core discipline, ADR-0007); extracted from dir_whip.py (task 31.10). SCR-052 R1: the package-wide RULE_KEY_* constant home -- every static rule_key (Part 3 B/C/D tables) is defined here once and referenced by the emission sites; values are frozen verbatim (AC-2).

Layer: core
Refs: spec 5.13, spec 5.14, SCR-035, SCR-041 R2, SCR-043 R2, SCR-043 R3, SCR-045 R6, SCR-052 R1, ADR-0007
Key exports:
  - RULE_KEY_* -- the frozen static rule_key constants (single definition point; SCR-052 R1).
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

# ---------------------------------------------------------------- Frozen rule_key constants (SCR-052 R1; AC-2)
# Single definition point for every static rule_key (Part 3 B/C/D tables in
# internal/CONTEXT.md). VALUES ARE FROZEN: byte-identical to the former
# inline literals (pure refactor red line). The dynamic prefixes
# pre-command:<command> / landed:<tool> stay built at their emission sites.

# Guard verdict rule_keys (B table).
RULE_KEY_EXTERNAL_WRITE = "external-write"
RULE_KEY_RUNTIME_ALLOWLIST = "runtime-allowlist"
RULE_KEY_TIER0_ALLOWLIST = "tier0-allowlist"
RULE_KEY_ALLOWED_FILE = "allowed-file"
RULE_KEY_SESSION_DIR = "session-dir"
RULE_KEY_ROOT_FILE = "root-file"
RULE_KEY_NON_SESSION_DIR = "non-session-dir"
RULE_KEY_FAIL_OPEN = "fail-open"
RULE_KEY_TERMINAL_WRITE_UNCERTAIN = "terminal-write-uncertain"

# Terminal interception rule_keys (C table).
RULE_KEY_TERMINAL_REDIRECT = "terminal-redirect"
RULE_KEY_TERMINAL_TOUCH = "terminal-touch"
RULE_KEY_TERMINAL_CP_MV = "terminal-cp-mv"
RULE_KEY_TERMINAL_MKDIR = "terminal-mkdir"
RULE_KEY_TERMINAL_DOWNLOAD = "terminal-download"

# Audit / session / observe rule_keys (D table static keys). The retained
# historical name carries the session-dir-limit value (migrated from
# session_dirs.py, name kept per SCR-052 R1).
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
RULE_KEY_SUBAGENT_START = "subagent-start"
RULE_KEY_SUBAGENT_STOP = "subagent-stop"
RULE_KEY_APPROVAL_GRANTED = "approval:granted"
RULE_KEY_APPROVAL_DENIED = "approval:denied"
RULE_KEY_APPROVAL_REQUESTED = "approval-requested"

# Verdict rule_keys that never fan out to the bus (5.14): their callers
# emit their own bus events (approval verdicts, the audit gate block, the
# audit violation verdict) or are allow_path entry-gating rejections
# (SCR-041 R2 + SCR-043 R3, 5.11 -- stats row only, no generic blocked
# fanout).
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
    """Emit ONE single-line structured verdict event (5.13 logging part).

    Levels (SCR-043 R2): block / fail-open -> WARNING; a GEOMETRICALLY
    outside-root target (same normalize_target + within_working_dir
    domain as the classify chain) or the external-write outcome string
    (fallback: root unresolved / target None fail-open shapes) -> INFO;
    other allows -> DEBUG. Also records the verdict via stats (counters +
    stats.jsonl append). Verdict-derived bus events (blocked /
    external-write, 5.14) use the same geometric basis and are emitted
    unless the rule_key is in _BUS_SKIP_RULE_KEYS (callers that handle
    their own events, e.g. approval). working_dir_root and profile
    resolve from state (state.session.working_dir_root /
    state.session.session_profile); session_id / is_subagent describe
    the judged call's session and are explicit params. Never raises
    (fail-open, 5.8).
    """
    try:
        working_dir_root = state.session.working_dir_root
        stats_record(
            outcome, tool, rule_key, target=target, reason=reason,
            is_subagent=bool(is_subagent), working_dir_root=working_dir_root,
        )
        rel_target = relativize_target(target, working_dir_root)
        # SCR-043 R2: the log/bus routing basis is GEOMETRIC (computed
        # fresh, chain-homologous); the outcome string stays as the
        # fallback so fail-open shapes keep their levels.
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
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        line = json.dumps(event)
        if outcome in ("block", "fail-open"):
            logger.warning("dir-whip: verdict %s", line)
        elif outside or outcome == "external-write":
            logger.info("dir-whip: verdict %s", line)
        else:
            logger.debug("dir-whip: verdict %s", line)
        # 5.14: verdict-derived bus events (privacy-shaped relative target),
        # same geometric basis as the log routing; _BUS_SKIP_RULE_KEYS
        # respected unchanged.
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
    """Emit a bare-name dir-whip event (5.14); silent degradation.

    Bus absent (capability flag off, no ctx, or ctx.emit missing) or emit
    raising -> exactly ONE DEBUG log line per emission attempt, no error.
    The host forces the ``dir-whip:`` namespace, so only the bare
    name is passed (a namespaced name raises ValueError, fail-closed).
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


# Single authoritative names (SCR-052 R1 alias convergence: the former
# module-tail emit = _emit_verdict / bus_emit = _bus_emit aliases are gone;
# emit() calls bus_emit() directly).

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
    "RULE_KEY_SUBAGENT_START",
    "RULE_KEY_SUBAGENT_STOP",
    "RULE_KEY_APPROVAL_GRANTED",
    "RULE_KEY_APPROVAL_DENIED",
    "RULE_KEY_APPROVAL_REQUESTED",
]
