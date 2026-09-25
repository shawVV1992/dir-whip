"""Session-start orchestration deep module: session_start(session_id, ctx) -- reminder lifecycle + conditional cwd injection + project exemption + discipline predicates + orphan-scan dispatch (spec 5.4, spec 5.17).

The decision chain lives here; the assembly hook adapters are thin
fail-open dispatches (session_start itself never catches top-level).
Depends on guard for reset_fail_open_flag / resolved_config and on
runtime_allowlist for the session-scope reset (both one-way: guard never
imports session_start at module level).

Layer: core
Refs: spec 5.4, spec 5.17, SCR-050
Key exports:
  - session_start -- top-level session-start decision chain; child sessions short-circuit to skipped-child.
  - append_reminder_fallback -- one-shot REMINDER tail note after an unavailable session start (5.17 fallback channel).
  - discipline_applies -- True = inject the session-start reminder; missing cwd/root fails open to True.
  - project_exemption_applies -- True = CWD under an active host project folder (reminder skipped); fail-open False.
"""

import datetime
import json
import logging

from . import audit_prompts, claims, config, events, runtime_allowlist, subagents, session_dirs, state, stats

from .events import (RULE_KEY_ORPHAN_NOTICE, RULE_KEY_SESSION_REMINDER, RULE_KEY_SESSION_REMINDER_FALLBACK)

from .messages import DISCIPLINE_BLOCK_MESSAGE

from .paths import within_working_dir

# One-way session_start -> guard edge: only the fail-open latch reset and
# the cached (root, allowlist) reader are consumed here; guard imports
# nothing from this module.
from . import guard

logger = logging.getLogger("dir-whip")


def _record_session_reminder(session_id, status):
    """One session-reminder stats row at a terminal reminder state:
    allow/session, reason = the state literal (injected | skipped-outside
    | skipped-child | skipped-project | unavailable -- all five states are
    observable here), target=None. One row per session start; child
    sessions record their own skipped-child state. Allow outcome -> no bus
    fanout. Fail-open: events.emit never raises."""
    events.emit(
        "allow", "session", RULE_KEY_SESSION_REMINDER, None,
        status, session_id, subagents._is_subagent_session(session_id),
    )


def _record_orphan_notice(session_id):
    """One orphan-notice stats row when the advisory notice is delivered
    at session start (same non-verdict advisory convention as
    _record_session_reminder; allow outcome -> no bus fanout). Top-level
    path only, so is_subagent is False by construction. Fail-open:
    events.emit never raises."""
    events.emit(
        "allow", "session", RULE_KEY_ORPHAN_NOTICE, None,
        "orphan scan notice at session start", session_id, False,
    )


def _record_reminder_fallback(session_id):
    """One session-reminder-fallback stats row.

    Fired by the one-shot transform_tool_result fallback note when the
    session-start reminder outcome was unavailable; top-level only.
    Stats-only: the allow outcome with target None fans out NO bus event.
    Fail-open: events.emit never raises."""
    events.emit(
        "allow", "session", RULE_KEY_SESSION_REMINDER_FALLBACK, None,
        "session-start reminder re-delivered on the first eligible "
        "tool result",
        session_id, False,
    )


def _inject_reminder(ctx, session_id):
    """Inject the session-start discipline reminder.

    Inject when the ctx channel exists and accepts the message (status
    injected); otherwise status unavailable, with the stats reason
    subdivided into ``unavailable:no-ctx`` / ``unavailable:no-method`` /
    ``unavailable:falsy-return`` (the ``unavailable`` prefix retained for
    compatibility), the one-shot transform_tool_result fallback flag
    armed, and the debug line recording the method-existence detail.
    """
    has_method = bool(ctx) and callable(getattr(ctx, "inject_message", None))
    if has_method and ctx.inject_message(DISCIPLINE_BLOCK_MESSAGE):
        state.session.reminder_status = "injected"
        _record_session_reminder(session_id, "injected")
        return
    if not ctx:
        sub = "no-ctx"
    elif not has_method:
        sub = "no-method"
    else:
        sub = "falsy-return"
    state.session.reminder_status = "unavailable"
    state.session.reminder_pending_fallback = True
    _record_session_reminder(session_id, "unavailable:" + sub)
    logger.debug(
        "dir-whip: session-start reminder %s "
        "(inject_message present=%s); fallback armed",
        "unavailable:" + sub, has_method,
    )


def _is_error_json_result(result):
    """Error-result eligibility check (same shape as the audit L1 notice):
    a JSON object carrying an "error" key and nothing else of note
    (<=2 keys) is not decorated."""
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2


def _append_reminder_fallback(audited_result, original_result, session_id):
    """One-shot REMINDER tail note after an unavailable session start.

    Eligibility: top-level session only, flag armed by the unavailable
    branch of _inject_reminder, and the result must be a string (error
    JSON results are NOT decorated). A non-eligible call does NOT consume
    the flag -- the note waits for the next eligible call. The base text
    is the audit-adjusted return when there is one (the note lands after
    it, tail append), else the original result. Fire-once: on firing the
    flag is cleared and the session-reminder-fallback stats row is
    recorded. Never raises; on any failure the audited result is returned
    untouched (fail-open)."""
    try:
        if not state.session.reminder_pending_fallback:
            return audited_result
        if subagents._is_subagent_session(session_id):
            return audited_result
        text = (
            audited_result if isinstance(audited_result, str)
            else original_result
        )
        if not isinstance(text, str):
            return audited_result
        if _is_error_json_result(text):
            return audited_result
        state.session.reminder_pending_fallback = False
        _record_reminder_fallback(session_id)
        return text + "\n\n" + DISCIPLINE_BLOCK_MESSAGE
    except Exception as exc:
        logger.debug("dir-whip: reminder fallback failed (fail-open): %s", exc)
        return audited_result


def discipline_applies(cwd, working_dir_root):
    """Conditional-injection predicate (spec 5.4).

    Pure decision: True = inject the session-start reminder. None-safe
    fail-open (missing cwd OR unresolved root -> True = current
    behavior); containment reuses paths.within_working_dir (equality
    counts as inside; Windows casefold rules on any host).
    """
    try:
        if not cwd or not working_dir_root:
            return True
        return within_working_dir(cwd, working_dir_root)
    except Exception:
        return True


def project_exemption_applies(cwd, folders):
    """Project-mode injection exemption predicate (spec 3.2 Layer 0).

    Pure decision: True = the agent CWD falls under an ACTIVE host
    project folder -> skip the session-start reminder entirely (project
    mode has its own layout; the Working Directory discipline does not
    apply). Containment per folder reuses paths.within_working_dir
    (prefix-inclusive, equality counts as inside; Windows casefold rules
    on any host). Fail-open: missing cwd / folders / any error -> False
    (no exemption = current behavior).
    """
    try:
        if not cwd or not folders:
            return False
        for folder in folders:
            if folder and within_working_dir(cwd, folder):
                return True
        return False
    except Exception:
        return False


def _reset_session_scope(session_id):
    """Top-level session-start resets.

    The fallback flag is reset at the START of every top-level session
    start (before injection); only the unavailable arm of _inject_reminder
    sets it. The audit state (pending violations, leftover pre snapshots,
    cap warning) and the session-dir claim + pending marker are cleared
    (child sessions return upstream and inherit the parent's slots); the
    runtime allowlist and the confirmation-issued set follow the same
    top-level lifecycle; the fail-open warning flag is reset.
    """
    state.session.reminder_pending_fallback = False
    audit_prompts.on_session_start(session_id)
    claims.on_session_start(session_id)
    runtime_allowlist.runtime_allowlist_clear()
    with state.session.lock:
        state.session.confirmation_issued.clear()
    guard.reset_fail_open_flag()


def _bind_session_profile(session_id, ctx):
    """Re-resolve the SESSION's profile + working_dir_root and attribute
    stats (child sessions return upstream and inherit)."""
    profile = getattr(ctx, "profile_name", None) if ctx else None
    config.set_session_profile(profile)
    config.refresh_resolution(ctx)
    stats.stats_set_session(
        profile=profile,
        session_id=session_id,
        is_subagent=False,
        started_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )


def _agent_cwd():
    """Conditional-injection step 1: the agent CWD via the injected
    accessor (None when absent or failing; never raises)."""
    cwd = None
    cwd_fn = getattr(state.session, "agent_cwd_fn", None)
    if callable(cwd_fn):
        try:
            cwd = cwd_fn()
        except Exception as exc:
            logger.debug("dir-whip: resolve_agent_cwd failed: %s", exc)
            cwd = None
    return cwd


def _project_skip_id(cwd):
    """Project-mode exemption probe: the active project id when the
    CWD falls under one of its folders (project mode has its own layout,
    the Working Directory discipline does not apply), else None.

    Evaluated HERE at session start (the active pointer varies across
    sessions); any probe failure fails open to the normal flow.
    """
    project_fn = getattr(state.session, "project_active_fn", None)
    if not callable(project_fn):
        return None
    try:
        project_info = project_fn()
    except Exception as exc:
        logger.debug("dir-whip: project_active_fn failed: %s", exc)
        return None
    if not project_info:
        return None
    active_id, folders = project_info
    if active_id and project_exemption_applies(cwd, folders):
        return active_id
    return None


def _deliver_orphan_notice(working_dir_root, allowlist, session_id, ctx):
    """Advisory orphan scan (spec 5.4): ONE call after the reminder
    injection (child sessions returned at the top, so subagents never
    scan; CWD-outside sessions returned at the skipped-outside branch).
    Decision logic lives in session_dirs; advise-only TEXT, never a block
    action. The stats row lands when the notice is delivered."""
    notice = session_dirs.scan_orphans(working_dir_root, allowlist)
    if notice and ctx is not None and hasattr(ctx, "inject_message"):
        if ctx.inject_message(notice):
            _record_orphan_notice(session_id)


def session_start(session_id, ctx):
    """Top-level session-start decision chain (spec 5.4).

    Top-level: clear the runtime allowlist, reset the fail-open warning
    flag, inject the discipline reminder, advisory orphan scan. Child
    sessions (session_id in child_session_ids) SKIP all three. Gateway
    degrade: inject_message unavailable or falsy -> DEBUG log, no crash.
    Fail-open: the assembly adapter catches any top-level error.
    """
    if subagents._is_subagent_session(session_id):
        state.session.reminder_status = "skipped-child"
        # The five-state stats outlet covers skipped-child too.
        _record_session_reminder(session_id, "skipped-child")
        return
    _reset_session_scope(session_id)
    _bind_session_profile(session_id, ctx)
    cwd = _agent_cwd()
    if cwd:
        active_id = _project_skip_id(cwd)
        if active_id:
            state.session.reminder_status = "skipped-project"
            _record_session_reminder(session_id, "skipped-project")
            logger.debug(
                "dir-whip: session-start reminder skipped "
                "(active project %s contains the agent CWD)",
                active_id,
            )
            return
    working_dir_root, allowlist = guard.resolved_config()
    if not discipline_applies(cwd, working_dir_root):
        state.session.reminder_status = "skipped-outside"
        _record_session_reminder(session_id, "skipped-outside")
        logger.debug(
            "dir-whip: session-start reminder skipped "
            "(agent CWD outside the Working Directory)"
        )
        return
    _inject_reminder(ctx, session_id)
    _deliver_orphan_notice(working_dir_root, allowlist, session_id, ctx)


# The assembly transform_tool_result adapter calls this public entry
# (audit first, then the one-shot fallback note).
append_reminder_fallback = _append_reminder_fallback

__all__ = [
    "session_start",
    "append_reminder_fallback",
    "discipline_applies",
    "project_exemption_applies",
]
