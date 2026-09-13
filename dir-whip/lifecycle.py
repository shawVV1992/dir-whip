"""Session-start orchestration deep module: session_start(session_id, ctx) -- reminder lifecycle + R2 cwd conditional injection + R7 project exemption + discipline predicates + orphan-scan dispatch (spec 5.4, spec 5.17).

SCR-050 v3 R6.2 (spec 5.1 v2.19): the decision chain moved VERBATIM from the __init__.py assembly layer (on_start was a fat adapter); the assembly hook adapters are now thin fail-open dispatches. Fail-open posture inherited: session_start itself never catches top-level (the adapter does); the inline cwd/project probe guards moved unchanged. Depends on verdict ONLY for reset_fail_open_flag / resolved_config (one-way; verdict never imports lifecycle at module level -- its same-name predicate aliases are lazy delegation stubs, state.py cycle-break precedent).

Layer: core
Refs: spec 5.4, spec 5.17, SCR-027, SCR-039, SCR-040, SCR-041, SCR-044, SCR-048, SCR-050
Key exports:
  - session_start -- top-level session-start decision chain; child sessions short-circuit to skipped-child.
  - append_reminder_fallback -- one-shot REMINDER tail note after an unavailable session start (5.17 fallback channel).
  - discipline_applies -- True = inject the session-start reminder; missing cwd/root fails open to True.
  - project_exemption_applies -- True = CWD under an active host project folder (reminder skipped); fail-open False.
"""

import datetime
import json
import logging

from . import audit, config, events, sessions, session_dirs, state, stats

from .messages import REMINDER_MESSAGE

from .paths import within_working_dir

# One-way lifecycle -> verdict edge (spec 5.1 v2.19 dependency figure):
# only the fail-open latch reset and the cached (root, allowlist) reader
# are consumed here; verdict imports nothing from this module.
from . import verdict

logger = logging.getLogger("dir-whip")


def _record_session_reminder(session_id, status):
    """One session-reminder stats row at a terminal reminder state
    (SCR-040 R4, 5.13 v2.8): allow/session, reason = the state literal
    (injected | skipped-outside | skipped-child | skipped-project |
    unavailable -- all five states observable here; this is the five-state
    outlet after the v2.8 report Reminder line's removal), target=None.
    One row per session start; child sessions record their own
    skipped-child state. Allow outcome -> no bus fanout (the 5.14 emit
    surface stays at 7). Fail-open: events.emit never raises."""
    events.emit(
        "allow", "session", "session-reminder", None,
        status, session_id, sessions.is_child(session_id),
    )


def _record_orphan_notice(session_id):
    """One orphan-notice stats row when the R7 advisory notice is
    delivered at session start (SCR-044 R7: allow/session/orphan-notice
    via the events/stats setdefault chain, same non-verdict advisory
    convention as _record_session_reminder; allow outcome -> no bus
    fanout, the 5.14 emit surface stays at 7). Top-level path only, so
    is_subagent is False by construction. Fail-open: events.emit never
    raises."""
    events.emit(
        "allow", "session", "orphan-notice", None,
        "orphan scan notice at session start", session_id, False,
    )


def _record_reminder_fallback(session_id):
    """One session-reminder-fallback stats row (SCR-048 R4, 5.17/5.13).

    Fired by the one-shot transform_tool_result fallback note when the
    session-start reminder outcome was unavailable; top-level only.
    Stats-only: the allow outcome with target None fans out NO bus
    event (the 5.14 emit surface stays at 7). Fail-open: events.emit
    never raises."""
    events.emit(
        "allow", "session", "session-reminder-fallback", None,
        "session-start reminder re-delivered on the first eligible "
        "tool result",
        session_id, False,
    )


def _inject_reminder(ctx, session_id):
    """Inject the session-start discipline reminder (5.4 R2/R6).

    The injected/unavailable two arms collapsed into one helper
    (SCR-045 R7): inject when the ctx channel exists and accepts the
    message; otherwise record unavailable with the same debug line.
    v2.16 SCR-048 R4 (5.17): the unavailable arm subdivides the stats
    reason into ``unavailable:no-ctx`` / ``unavailable:no-method`` /
    ``unavailable:falsy-return`` (the ``unavailable`` prefix retained for
    compatibility), arms the one-shot transform_tool_result fallback
    flag, and the debug line records the method-existence detail.
    """
    has_method = bool(ctx) and callable(getattr(ctx, "inject_message", None))
    if has_method and ctx.inject_message(REMINDER_MESSAGE):
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
    """Error-result eligibility check (same shape as the audit L1 notice,
    5.18): a JSON object carrying an "error" key and nothing else of note
    (<=2 keys) is not decorated."""
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2


def _append_reminder_fallback(audited_result, original_result, session_id):
    """One-shot REMINDER tail note after an unavailable session start (5.17).

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
        if sessions.is_child(session_id):
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
        return text + "\n\n" + REMINDER_MESSAGE
    except Exception as exc:
        logger.debug("dir-whip: reminder fallback failed (fail-open): %s", exc)
        return audited_result


def discipline_applies(cwd, working_dir_root):
    """Conditional-injection predicate (spec 5.4, v2.7 R2).

    Pure decision: True = inject the session-start reminder. None-safe
    fail-open (missing cwd OR unresolved root -> True = current
    behavior); containment reuses paths.within_working_dir (equality
    counts as inside; Windows casefold rules on any host, SCR-006).
    """
    try:
        if not cwd or not working_dir_root:
            return True
        return within_working_dir(cwd, working_dir_root)
    except Exception:
        return True


def project_exemption_applies(cwd, folders):
    """Project-mode injection exemption predicate (R7, spec 3.2 Layer 0).

    Pure decision: True = the agent CWD falls under an ACTIVE host
    project folder -> skip the session-start reminder entirely (project
    mode has its own layout; the Working Directory discipline does not
    apply). Containment per folder reuses paths.within_working_dir
    (prefix-inclusive, equality counts as inside; Windows casefold rules
    on any host, SCR-006). Fail-open: missing cwd / folders / any error
    -> False (no exemption = current behavior).
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


def session_start(session_id, ctx):
    """Top-level session-start decision chain (5.4; SCR-050 v3 R6.2:
    moved verbatim from the __init__.py on_start adapter).

    Top-level: clear the runtime allowlist, reset the fail-open warning
    flag, inject the discipline reminder, advisory orphan scan. Child
    sessions (session_id in child_session_ids) SKIP all three. Gateway
    degrade: inject_message unavailable or falsy -> DEBUG log, no crash.
    Fail-open: the assembly adapter catches any top-level error (5.8).
    """
    if sessions.is_child(session_id):
        state.session.reminder_status = "skipped-child"
        # 5.13 v2.8: the five-state stats outlet covers skipped-child
        # too (the report Reminder line is removed in v2.8).
        _record_session_reminder(session_id, "skipped-child")
        return
    # SCR-048 R4 (5.17): the fallback flag is reset at the START of
    # every top-level session start (before injection); only the
    # unavailable arm of _inject_reminder below sets it. Child
    # sessions return above and never touch the parent's pending flag.
    state.session.reminder_pending_fallback = False
    # 5.18: top-level session start clears the audit state (pending
    # violations, leftover pre snapshots, cap warning); child sessions
    # skip and inherit the parent's latched state.
    audit.session_start(session_id)
    # SCR-044 R5 (CLR-1, spec 5.19): top-level session start clears
    # the session-dir claim + pending marker (child sessions returned
    # above and inherit the parent's slot).
    session_dirs.on_session_start(session_id)
    config.runtime_allowlist_clear()
    # SCR-041 R3: the confirmation-issued set follows the runtime
    # allowlist lifecycle -- cleared at every top-level session start.
    with state.session.lock:
        state.session.confirmation_issued.clear()
    verdict.reset_fail_open_flag()
    profile = getattr(ctx, "profile_name", None) if ctx else None
    # SCR-027: session-scoped resolution — re-resolve working_dir_root
    # from THIS session's profile (child sessions skip and inherit).
    config.set_session_profile(profile)
    config.refresh_resolution(ctx)
    stats.set_session(
        profile=profile,
        session_id=session_id,
        is_subagent=False,
        started_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    # R2 conditional injection three steps: cwd -> predicate -> inject.
    cwd = None
    cwd_fn = getattr(state.session, "agent_cwd_fn", None)
    if callable(cwd_fn):
        try:
            cwd = cwd_fn()
        except Exception as exc:
            logger.debug("dir-whip: resolve_agent_cwd failed: %s", exc)
            cwd = None
    # R7 project-mode exemption: an ACTIVE host project whose folders
    # contain the agent CWD skips the reminder entirely (project mode
    # has its own layout). Evaluated HERE at session start (the active
    # pointer varies across sessions), BEFORE the discipline predicate;
    # any probe failure fails open to the normal flow.
    if cwd:
        project_fn = getattr(state.session, "project_active_fn", None)
        if callable(project_fn):
            project_info = None
            try:
                project_info = project_fn()
            except Exception as exc:
                logger.debug(
                    "dir-whip: project_active_fn failed: %s", exc
                )
                project_info = None
            if project_info:
                active_id, folders = project_info
                if active_id and project_exemption_applies(
                    cwd, folders
                ):
                    state.session.reminder_status = "skipped-project"
                    _record_session_reminder(session_id, "skipped-project")
                    logger.debug(
                        "dir-whip: session-start reminder skipped "
                        "(active project %s contains the agent CWD)",
                        active_id,
                    )
                    return
    working_dir_root, allowlist = verdict.resolved_config()
    if not discipline_applies(cwd, working_dir_root):
        state.session.reminder_status = "skipped-outside"
        _record_session_reminder(session_id, "skipped-outside")
        logger.debug(
            "dir-whip: session-start reminder skipped "
            "(agent CWD outside the Working Directory)"
        )
        return
    _inject_reminder(ctx, session_id)
    # SCR-044 R7 (spec 5.4): advisory orphan scan -- ONE call after
    # the REMINDER injection (child sessions returned at the top, so
    # subagents never scan; CWD-outside sessions returned at the
    # skipped-outside branch). Decision logic lives in session_dirs;
    # advise-only TEXT, never a block action. Stats row lands via
    # the events/stats setdefault chain when the notice is delivered.
    notice = session_dirs.scan_orphans(working_dir_root, allowlist)
    if notice and ctx is not None and hasattr(ctx, "inject_message"):
        if ctx.inject_message(notice):
            _record_orphan_notice(session_id)


# SCR-050 v3 R6.2: the assembly transform_tool_result adapter calls this
# public entry (audit first, then the one-shot fallback note).
append_reminder_fallback = _append_reminder_fallback

__all__ = [
    "session_start",
    "append_reminder_fallback",
    "discipline_applies",
    "project_exemption_applies",
]
