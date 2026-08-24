"""Child-session tracking and audit parent links (spec 5.4/5.16/5.18).

Tracks subagent (child) sessions so on_session_start skips them and
verdicts split as subagent, opens/closes the child stats session context,
and records the audit parent links (child -> parent pending-set
inheritance, plus the top-level-session fallback). All state lives in
state.session / state.audit. No host imports (SCR-035 core module
discipline, ADR-0007). Extracted from dir_whip.py (task 31.11).
"""

import json
import logging

try:
    from . import state
except ImportError:
    import state

try:
    from .config import get_cached_config, stats_set_session
except ImportError:
    from config import get_cached_config, stats_set_session

try:
    from .events import emit
except ImportError:
    from events import emit

logger = logging.getLogger("dir-whip")


def _is_child_session(session_id):
    """True when session_id is a live child (subagent) session (5.4)."""
    with state.session.lock:
        return session_id in state.session.child_session_ids


def _audit_register_child(child_session_id, parent_session_id):
    """Record a child session's parent link (pending-set inheritance)."""
    try:
        with state.audit.lock:
            state.audit.session_parents[child_session_id] = (
                parent_session_id or state.audit.top_session
            )
    except Exception as exc:
        logger.debug("dir-whip: audit register child error: %s", exc)


def _audit_unregister_child(child_session_id):
    """Drop a child session's parent link when the subagent stops."""
    try:
        with state.audit.lock:
            state.audit.session_parents.pop(child_session_id, None)
    except Exception as exc:
        logger.debug("dir-whip: audit unregister child error: %s", exc)


def _record_top_session(session_id):
    """Record the current top-level session (child-inheritance fallback)."""
    state.audit.top_session = session_id


def on_subagent_start(child_session_id=None, child_role=None, child_goal=None,
                      parent_session_id=None, parent_turn_id=None,
                      parent_subagent_id=None, child_subagent_id=None, **kwargs):
    """subagent_start observer (5.16): track the child session.

    Adds child_session_id to child_session_ids (so on_session_start skips
    it and verdicts split as subagent) and opens the child stats session
    context (is_subagent=True).
    """
    try:
        if child_session_id:
            with state.session.lock:
                state.session.child_session_ids.add(child_session_id)
            # 5.18: record the parent link so the child's audit detections
            # resolve into the parent's pending-violation set.
            _audit_register_child(child_session_id, parent_session_id)
        stats_set_session(is_subagent=True)
        detail = {
            "child_session_id": child_session_id,
            "child_role": child_role,
            "child_goal": child_goal,
        }
        for key, value in (
            ("parent_session_id", parent_session_id),
            ("parent_turn_id", parent_turn_id),
            ("parent_subagent_id", parent_subagent_id),
            ("child_subagent_id", child_subagent_id),
        ):
            if value is not None:
                detail[key] = value
        # Config-cache side effect (was _resolved_config() in dir_whip.py):
        # seeds the cache / session root / registered ctx when not yet
        # initialized; the result is unused since the seven-param emit.
        get_cached_config(state.session.registered_ctx)
        emit(
            "allow", "subagent", "subagent-start", None,
            json.dumps(detail), None, True,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_start hook error: %s", exc)


def on_subagent_stop(child_session_id=None, child_subagent_id=None,
                     child_role=None, child_status=None, duration_ms=None,
                     **kwargs):
    """subagent_stop observer (5.16): untrack the child session.

    Removes child_session_id from child_session_ids and closes the child
    stats session context.
    """
    try:
        if child_session_id:
            with state.session.lock:
                state.session.child_session_ids.discard(child_session_id)
            _audit_unregister_child(child_session_id)
        # Close the child stats context: flip is_subagent back to False but
        # PRESERVE the parent session fields (profile/session_id/started_at).
        stats_set_session(is_subagent=False)
        detail = {
            "child_session_id": child_session_id,
            "child_subagent_id": child_subagent_id,
            "child_role": child_role,
            "child_status": child_status,
        }
        if duration_ms is not None:
            detail["duration_ms"] = duration_ms
        # Config-cache side effect (was _resolved_config() in dir_whip.py):
        # seeds the cache / session root / registered ctx when not yet
        # initialized; the result is unused since the seven-param emit.
        get_cached_config(state.session.registered_ctx)
        emit(
            "allow", "subagent", "subagent-stop", None,
            json.dumps(detail), None, True,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_stop hook error: %s", exc)


# Public thin aliases (SCR-035 interface convergence point).
is_child = _is_child_session
register_child = _audit_register_child
subagent_start = on_subagent_start
subagent_stop = on_subagent_stop

__all__ = ["is_child", "register_child", "subagent_start", "subagent_stop"]
