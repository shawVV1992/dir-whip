"""Structured verdict events (spec 5.13 logging part + 5.14 bus fanout) — deep module.

Records one single-line verdict event per guard decision (stats counters +
stats.jsonl append + leveled log) and fans out verdict-derived bus events.
Session context: working_dir_root and profile resolve from state inside
this module; session_id / is_subagent describe the judged call's session
and stay explicit emit params (Ruling 4). No host imports (SCR-035 core
module discipline, ADR-0007). Extracted from dir_whip.py (task 31.10).
"""

import datetime
import json
import logging

from . import state

from .paths import normalize_target, relativize_target, within_working_dir

from .stats import record as stats_record

logger = logging.getLogger("dir-whip")

# Verdict rule_keys that never fan out to the bus (5.14): their callers
# emit their own bus events (approval verdicts, the audit gate block, the
# audit violation verdict) or are allow_path entry-gating rejections
# (SCR-041 R2 + SCR-043 R3, 5.11 -- stats row only, no generic blocked
# fanout).
_BUS_SKIP_RULE_KEYS = frozenset((
    "approval:granted",
    "approval:denied",
    "write-audit-gate-block",
    "write-audit-violation",
    "allow-path-subagent-rejected",
    "allow-path-root-rejected",
    "allow-path-external-rejected",
))


def _verdict_reason(outcome):
    """Short reason string for a verdict event (5.13)."""
    if outcome == "external-write":
        return "target outside working_dir_root"
    return None


def _emit_verdict(outcome, tool, rule_key, target, reason, session_id, is_subagent):
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
    resolve from state (state.session.session_root /
    state.session.session_profile); session_id / is_subagent describe
    the judged call's session and are explicit params. Never raises
    (fail-open, 5.8).
    """
    try:
        working_dir_root = state.session.session_root
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
            _bus_emit("blocked", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
        elif (
            (outside or outcome == "external-write")
            and rule_key not in _BUS_SKIP_RULE_KEYS
        ):
            _bus_emit("external-write", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
    except Exception as exc:
        logger.debug("dir-whip: verdict emission failed (fail-open): %s", exc)


def _bus_emit(event_name, payload):
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


# Public thin alias (SCR-035 interface convergence point).
emit = _emit_verdict

__all__ = ["emit"]
