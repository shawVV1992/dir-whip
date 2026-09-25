"""Statistics: in-memory counters, session context, stats.jsonl persistence + 5 MiB rollover (spec 5.13).

Counters keyed outcome x tool x rule_key x is_subagent; the stats state
lives in state.stats; no host imports (core discipline).

Layer: core
Refs: spec 5.13, SCR-027, SCR-035
Key exports:
  - stats_record -- bump counters + append one stats.jsonl event line; never raises.
  - stats_set_session -- attach provided session context fields to persisted events.
  - stats_backfill_session -- set session_id only when currently empty (race-safe under the stats lock).
  - stats_snapshot -- deep copy of the counters (outcome x tool x rule_key x is_subagent).
  - stats_end_session -- close the session context fields (counters kept).
  - stats_reset -- clear in-memory counters + session context at register/re-register.
  - stats_jsonl_path -- report-facing stats.jsonl location (session profile home).
"""

import copy
import datetime
import json
import logging
import os

from . import state

from .paths import dirwhip_home, relativize_target

logger = logging.getLogger("dir-whip")

STATS_ROLLOVER_BYTES = 5 * 1024 * 1024
STATS_JSONL_NAME = "stats.jsonl"
STATS_ARCHIVE_NAME = "stats.jsonl.1"


def stats_reset():
    """Clear in-memory stats (counters + session context).

    Called at register/re-register so no counters or session fields leak
    into the next session.
    """
    with state.stats.lock:
        state.stats.counters.clear()
        _reset_stats_session_locked()


def _reset_stats_session_locked():
    """Reset the stats session fields; callers must hold state.stats.lock."""
    state.stats.session["profile"] = None
    state.stats.session["session_id"] = None
    state.stats.session["is_subagent"] = False
    state.stats.session["started_at"] = None


def stats_end_session():
    """Close the stats session context (counters kept).

    Clears the session fields (profile / session_id / is_subagent /
    started_at) so a closed child session's context never leaks into
    later events; in-memory counters are untouched.
    """
    with state.stats.lock:
        _reset_stats_session_locked()


def stats_set_session(profile=None, session_id=None, is_subagent=None, started_at=None):
    """Attach session context to persisted stats events.

    Only the provided fields are updated (None leaves a field unchanged);
    the full reset is stats_reset().
    """
    with state.stats.lock:
        if profile is not None:
            state.stats.session["profile"] = str(profile)
        if session_id is not None:
            state.stats.session["session_id"] = str(session_id)
        if is_subagent is not None:
            state.stats.session["is_subagent"] = bool(is_subagent)
        if started_at is not None:
            state.stats.session["started_at"] = str(started_at)


def stats_backfill_session(session_id):
    """Set the stats session_id only when it is currently empty.

    The check-and-set runs entirely under state.stats.lock so a
    concurrent first call cannot be overwritten (stats_set_session
    cannot be reused here: its lock acquisition is not reentrant). Only
    session_id is touched; profile / started_at stay unknown when unset.
    """
    if not session_id:
        return
    with state.stats.lock:
        if not state.stats.session.get("session_id"):
            state.stats.session["session_id"] = str(session_id)


def stats_snapshot():
    """Return a deep copy of the counters (outcome x tool x rule_key x is_subagent)."""
    with state.stats.lock:
        return copy.deepcopy(state.stats.counters)


def _now_iso():
    """Local time as an ISO-8601 string (seconds precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def stats_jsonl_path():
    """stats.jsonl location: the session profile's home dir-whip dir.

    The path follows the SESSION profile (set at on_session_start), so a
    default-profile session's events land in the ROOT home's dir-whip
    dir, not the register-time active profile's. No session profile set
    yet -> HERMES_HOME directly (register-time behavior).
    """
    return dirwhip_home(state.session.session_profile) / STATS_JSONL_NAME


def _append_stats_event(event):
    """Append one JSON line to stats.jsonl (O_APPEND, rollover at 5MB).

    Single-process assumption: appends are atomic via os.open O_APPEND; the
    rollover rename tolerates a missing source (another process already
    rolled). Raises on failure; callers swallow and log (fail-open).
    """
    path = stats_jsonl_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # surfaced by the os.open failure below
    try:
        if path.is_file() and path.stat().st_size > STATS_ROLLOVER_BYTES:
            try:
                os.replace(path, path.with_name(STATS_ARCHIVE_NAME))
            except FileNotFoundError:
                pass  # another process already rolled
    except Exception:
        pass  # rollover is best-effort; the append below still runs
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(fd, (json.dumps(event) + "\n").encode("utf-8"))
    finally:
        if fd is not None:
            os.close(fd)


def stats_record(outcome, tool, rule_key, target=None, reason=None,
                 is_subagent=None, working_dir_root=None):
    """Record one guard verdict: bump counters + append one stats.jsonl line.

    outcome x tool x rule_key counters are split by is_subagent; each
    event persists session + event fields. Never raises: a failed stats
    write is logged and does NOT affect the verdict (fail-open logging).
    """
    if is_subagent is None:
        is_subagent = state.stats.session.get("is_subagent", False)
    is_subagent = bool(is_subagent)
    with state.stats.lock:
        by_outcome = state.stats.counters.setdefault(outcome, {})
        by_tool = by_outcome.setdefault(tool, {})
        by_rule = by_tool.setdefault(rule_key, {})
        by_rule[is_subagent] = by_rule.get(is_subagent, 0) + 1
        try:
            _append_stats_event({
                "profile": state.stats.session.get("profile"),
                "session_id": state.stats.session.get("session_id"),
                "is_subagent": is_subagent,
                "started_at": state.stats.session.get("started_at"),
                "ts": _now_iso(),
                "outcome": outcome,
                "reason": reason,
                "tool": tool,
                "rule_key": rule_key,
                "target": relativize_target(target, working_dir_root),
            })
        except Exception as exc:
            logger.debug("dir-whip: stats write failed (ignored): %s", exc)


__all__ = [
    "stats_record",
    "stats_set_session",
    "stats_backfill_session",
    "stats_snapshot",
    "stats_end_session",
    "stats_reset",
    "stats_jsonl_path",
]
