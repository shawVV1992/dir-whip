"""Statistics: counters, jsonl persistence and rollover (spec 5.13) — pure
state module.

In-memory counters (outcome x tool x rule_key x is_subagent), session
context fields, and one-JSON-line-per-event persistence with 5MB rollover.
No host imports (SCR-035 core module discipline, ADR-0007); the stats
state lives in state.stats (task 31.9). Extracted in task 31.7 (previously
part of the config module).
"""

import copy
import datetime
import json
import logging
import os

from . import state

from .paths import get_hermes_home, profile_home, relativize_target

logger = logging.getLogger("dir-whip")

STATS_ROLLOVER_BYTES = 5 * 1024 * 1024
STATS_JSONL_NAME = "stats.jsonl"
STATS_ARCHIVE_NAME = "stats.jsonl.1"


def stats_reset():
    """Clear in-memory stats (counters + session context).

    Called at register/re-register so no counters or session fields leak
    into the next session (5.13 D2).
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
    later events; in-memory counters are untouched (5.13 D2/D3).
    """
    with state.stats.lock:
        _reset_stats_session_locked()


def stats_set_session(profile=None, session_id=None, is_subagent=None, started_at=None):
    """Attach session context to persisted stats events (5.13 session fields).

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


def stats_snapshot():
    """Return a deep copy of the counters (outcome x tool x rule_key x is_subagent)."""
    with state.stats.lock:
        return copy.deepcopy(state.stats.counters)


def _now_iso():
    """Local time as an ISO-8601 string (seconds precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _stats_jsonl_path():
    """stats.jsonl location: the session profile's home dir-whip dir.

    SCR-027: the path follows the SESSION profile (set at on_session_start),
    so a default-profile session's events land in the ROOT home's
    dir-whip dir, not the register-time active profile's. When no
    session profile is set yet, use HERMES_HOME directly (register-time
    behavior).
    """
    home = get_hermes_home()
    if state.session.session_profile:
        home = profile_home(home, state.session.session_profile)
    return home / "dir-whip" / STATS_JSONL_NAME


def _append_stats_event(event):
    """Append one JSON line to stats.jsonl (O_APPEND, rollover at 5MB).

    Single-process assumption: appends are atomic via os.open O_APPEND; the
    rollover rename tolerates a missing source (another process already
    rolled). Raises on failure; callers swallow and log (fail-open).
    """
    path = _stats_jsonl_path()
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

    outcome x tool x rule_key counters are split by is_subagent (5.13 D2);
    each event persists session + event fields (D3). Never raises: a failed
    stats write is logged and does NOT affect the verdict (5.8 fail-open
    logging).
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


# Public thin aliases (SCR-035 interface convergence point).
record = stats_record
set_session = stats_set_session
snapshot = stats_snapshot
end_session = stats_end_session
reset = stats_reset
# SCR-045 R6: the report-facing jsonl location.
stats_jsonl_path = _stats_jsonl_path

__all__ = [
    "record",
    "set_session",
    "snapshot",
    "end_session",
    "reset",
    "stats_jsonl_path",
]
