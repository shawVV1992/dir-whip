"""dir-whip plugin for Hermes.

Assembly layer (SCR-035, task 31.13): register(ctx) + the eight thin hook
adapters + the SINGLE fail-open try/except layer for hook dispatch. The
plugin's modules (verdict/audit/sessions/events/terminal/paths/config/
stats/state/report) are pure decision/state layers; the host API is
touched ONLY here (ADR-0007).
"""

import datetime
import json
import logging
import os
from pathlib import Path

try:
    from hermes_cli.tools.terminal_tool import get_session_cwd as _get_session_cwd
except ImportError:
    _get_session_cwd = None

try:
    from agent.runtime_cwd import resolve_agent_cwd as _resolve_agent_cwd
except ImportError:
    _resolve_agent_cwd = None

# R7 project-mode exemption (spike 39.R4.0, host v2026.8.13-3440-g79b8703d0):
# hermes_cli.projects_db exposes connect_closing() (per-profile projects.db,
# WAL + idempotent schema) and get_active_id(conn) (project_meta KV). The
# import happens ONLY in this assembly layer (ADR-0007 core zero-host-import
# red line); absence -> None -> no exemption (= pre-R7 behavior).
try:
    from hermes_cli.projects_db import (
        connect_closing as _projects_connect_closing,
        get_active_id as _projects_get_active_id,
    )
except ImportError:
    _projects_connect_closing = None
    _projects_get_active_id = None

try:
    from . import audit, config, events, logsetup, report, sessions, session_dirs, state, stats, verdict
except ImportError:
    import audit
    import config
    import events
    import logsetup
    import report
    import sessions
    import session_dirs
    import state
    import stats
    import verdict

try:
    from .paths import (
        _paths_equal,
        normalize_target,
        relativize_target,
        within_working_dir,
    )
except ImportError:
    from paths import (
        _paths_equal,
        normalize_target,
        relativize_target,
        within_working_dir,
    )

logger = logging.getLogger("dir-whip")

# Spec 5.11: the plugin's ONLY tool (OpenAI function-call format required by
# Hermes tools.registry). Registered at register() via ctx.register_tool.
# v2.9 (SCR-041 R3): optional confirm parameter + two-step flow description
# (call without confirm to obtain the briefing, relay it to the user,
# re-call with confirm=true only after explicit user approval).
ALLOW_PATH_TOOL_SCHEMA = {
    "name": "dir_whip_allow_path",
    "description": (
        "Add an absolute path to the dir-whip runtime allowlist so "
        "file operations under that path are exempt for this session (Tier 0). "
        "Use when the user explicitly specifies a path to write to. "
        "Two-step confirmation: call WITHOUT confirm to obtain the "
        "user-confirmation briefing, relay it to the user, then re-call with "
        "confirm=true ONLY after the user explicitly approves."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to allow (forward slashes)",
            },
            "confirm": {
                "type": "boolean",
                "description": (
                    "true ONLY after the user explicitly approved the "
                    "briefing payload from the first call (default false)"
                ),
            },
        },
        "required": ["path"],
    },
}

# Spec 5.11 v2.9 (SCR-041 R2a): subagent rejection, parent-guidance variant.
ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.\n"
    "Exemptions are granted by the user via the main agent. Write to the target\n"
    "directory passed by the parent agent, or report back so the parent can ask\n"
    "the user."
)

# Spec 5.11 v2.9 (SCR-041 R2b): Working Directory root rejection.
ALLOW_PATH_ROOT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.\n"
    "Allow a specific file or subdirectory path instead; workspace-wide\n"
    "exemptions belong in dir-whip-config.yaml (allowlist dirs) authored by the user."
)

# Spec 5.11 v2.11 (SCR-043 R2c): outside-root rejection -- no entry is
# needed there; writes outside the Working Directory are allowed and
# logged (external-write), so the retry is direct.
ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the path is outside the Working Directory; no allowlist\n"
    "entry is needed. Writes there are allowed and logged (external-write).\n"
    "Retry the write directly at the requested path."
)

# Spec 5.11 v2.9 (SCR-041 R3): two-step confirmation payload ("<path>"
# substituted with the forward-slash form of the requested path).
ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE = (
    "[dir-whip] CONFIRMATION REQUIRED: adding \"%s\" to the runtime allowlist\n"
    "exempts ALL file operations under it from the guard for the rest of this\n"
    "session. Previously recorded root writes under it are NOT remediated by\n"
    "this exemption (they stay pending until settled). The entry expires\n"
    "automatically when the session ends; persistent exemptions belong in\n"
    "dir-whip-config.yaml\n"
    "(allowlist files/dirs, removable via /dir-whip remove).\n"
    "Present this to the user and ask for explicit approval. Re-call\n"
    "dir_whip_allow_path(path=..., confirm=true) ONLY after the user approves."
)

# Spec 5.11 v2.9 (SCR-041 R3): latch-context conditional line, appended to
# the payload only when the pending set is non-empty (latch active).
ALLOW_PATH_LATCH_CONTEXT_LINE = (
    "NOTE: a settlement block is currently active \u2014 present the resolution "
    "choice to the user: move the file(s) (settle), or keep them at the root "
    "(give the user the exact command: /dir-whip allow <path>). Writes stay "
    "frozen until then."
)

# Spec 3.1: bundled skill description (frontmatter + register_skill).
# Trigger words within the first 57 chars; avoids "organize/clean up
# sessions" phrasing (F4). Matches SKILL.md frontmatter description.
SKILL_DESCRIPTION = (
    "Use when creating, saving, writing, moving, or deleting files in a "
    "Hermes workspace, organizing deliverables, or auditing workspace "
    "compliance."
)


def _project_active_probe():
    """Host projects.db probe (R7): (active_id, [folder paths]) or None.

    Reads the ACTIVE project via get_active_id, then its folder paths from
    project_folders (primary_path + folders; the folder set is what the
    exemption containment matches against). Called at on_start (the active
    pointer is per-profile global and varies across sessions), never cached
    at register. Fail-open: ANY error (import absent, db locked, schema
    drift) -> None -> no exemption.
    """
    try:
        if _projects_connect_closing is None or _projects_get_active_id is None:
            return None
        with _projects_connect_closing() as conn:
            active_id = _projects_get_active_id(conn)
            if not active_id:
                return None
            rows = conn.execute(
                "SELECT path FROM project_folders WHERE project_id = ?",
                (active_id,),
            ).fetchall()
            folders = [str(row[0]) for row in rows if row and row[0]]
            return (str(active_id), folders)
    except Exception as exc:
        logger.debug("dir-whip: project probe failed (fail-open): %s", exc)
        return None


def register(ctx):
    """Register dir-whip hooks, tool and event bus (5.7/5.8/5.14).

    Hooks: pre_tool_call, on_session_start, post_tool_call,
    post_approval_response, pre_command, subagent_start, subagent_stop,
    transform_tool_result (5.18 L1 notice), pre_verify (5.18 R5
    continuation fallback). Tool: dir_whip_allow_path (the plugin's ONLY
    eager tool; dir_whip_settle registers lazily on the first L1 notice
    fire, R4). Event bus: capability detected via
    hasattr(ctx, "emit"); absent -> silent degradation. Fail-open: any
    registration error logs a warning; the plugin is disabled but Hermes
    continues normally.
    """
    try:
        state.session.registered_ctx = ctx
        # SCR-040 R5: dedicated diagnostic log dir-whip.log — attach FIRST
        # so every later register-time breadcrumb is captured (fail-open:
        # setup() runs its own three-tier degradation chain and never
        # raises; a log failure must not break registration).
        logsetup.setup()
        # P6 (31.13): precompute plugin paths/version once at register;
        # message building and report rendering read state instead of
        # __file__ (always the bundled copies inside the plugin package).
        plugin_dir = str(Path(__file__).resolve().parent)
        state.session.plugin_dir = plugin_dir
        state.session.script_resolver_path = os.path.normpath(
            os.path.join(plugin_dir, "skills", "workspace-organization", "scripts")
        )
        state.session.skill_md_path = os.path.join(
            plugin_dir, "skills", "workspace-organization", "SKILL.md"
        )
        state.session.plugin_version = report._plugin_version()
        # Assembly-layer injection (ADR-0007): wire the audit classifier
        # BEFORE any hook can fire.
        audit.set_classifier(verdict.classify_target)
        # Host API injection slots (ADR-0007): session CWD accessor +
        # agent CWD accessor (R2 conditional injection) filled at register
        # time; absent host API -> None -> on_start always injects.
        state.session.session_cwd_fn = _get_session_cwd
        state.session.agent_cwd_fn = _resolve_agent_cwd
        # R7 project probe slot (ADR-0007): filled at register ONLY when the
        # host module is importable (same shape as session_cwd_fn/agent_cwd_fn:
        # absent host API -> None -> no exemption); the probe itself runs per
        # session start (active pointer varies).
        state.session.project_active_fn = (
            _project_active_probe
            if (
                _projects_connect_closing is not None
                and _projects_get_active_id is not None
            )
            else None
        )
        try:
            state.session.emit_enabled = bool(getattr(ctx, "emit", None))
        except Exception:
            state.session.emit_enabled = False
        config.reset_cache()
        config.get_cached_config(ctx)
        ctx.register_hook("pre_tool_call", _guard_hook)
        ctx.register_hook("on_session_start", on_start)
        ctx.register_hook("post_tool_call", on_post_tool_call)
        ctx.register_hook("post_approval_response", on_post_approval_response)
        ctx.register_hook("pre_command", on_pre_command)
        ctx.register_hook("subagent_start", on_subagent_start)
        ctx.register_hook("subagent_stop", on_subagent_stop)
        ctx.register_hook("transform_tool_result", on_transform_tool_result)
        # 5.18 R5 / v2.8 R2: pre_verify continuation fallback. The nudge
        # budget is the plugin-side SESSION-CUMULATIVE cap=3
        # (audit.PRE_VERIFY_NUDGE_CAP, counter reset at session start);
        # the host's per-turn max_verify_nudges budget remains the outer
        # bound.
        ctx.register_hook("pre_verify", on_pre_verify)
        if hasattr(ctx, "register_tool"):
            try:
                ctx.register_tool(
                    "dir_whip_allow_path",
                    toolset="dir-whip",
                    schema=ALLOW_PATH_TOOL_SCHEMA,
                    handler=_allow_path_handler,
                )
            except Exception as exc:
                logger.warning("dir-whip: register_tool failed: %s", exc)
        # Spec 5.7 command (/dir-whip merged report, SCR-029) lives in
        # report.py (D3).
        report.register_dir_whip_commands(ctx)
        # Spec 5.17: bundled skill (opt-in, qualified name) + discipline prompt.
        try:
            skill_md = Path(state.session.skill_md_path)
            if skill_md.is_file() and hasattr(ctx, "register_skill"):
                ctx.register_skill(
                    "workspace-organization", skill_md, description=SKILL_DESCRIPTION
                )
            else:
                logger.debug(
                    "dir-whip: register_skill skipped (bundled SKILL.md "
                    "or ctx.register_skill unavailable)"
                )
        except Exception as exc:
            logger.warning("dir-whip: register_skill failed: %s", exc)
        logger.debug("dir-whip: registered successfully")
    except Exception as exc:
        logger.warning("dir-whip: registration failed: %s", exc)


# ---------------------------------------------------------------- Hook adapters (fail-open single layer)

def _guard_hook(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call hook adapter (5.8: never raises; fail-open -> None)."""
    try:
        return verdict.guard(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: guard hook error (fail-open): %s", exc)
        return None


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
        status, session_id, sessions._is_child_session(session_id),
    )


def on_start(session_id, model=None, platform=None, **kwargs):
    """on_session_start hook adapter (5.4): top-level sessions only.

    Top-level: clear the runtime allowlist, reset the fail-open warning
    flag, inject the discipline reminder. Child sessions (session_id in
    child_session_ids) SKIP all three. Gateway degrade: inject_message
    unavailable or falsy -> DEBUG log, no crash.
    """
    try:
        if sessions._is_child_session(session_id):
            state.session.reminder_status = "skipped-child"
            # 5.13 v2.8: the five-state stats outlet covers skipped-child
            # too (the report Reminder line is removed in v2.8).
            _record_session_reminder(session_id, "skipped-child")
            return
        # 5.18: top-level session start clears the audit state (pending
        # violations, leftover pre snapshots, cap warning); child sessions
        # skip and inherit the parent's latched state.
        audit._audit_session_start(session_id)
        # SCR-044 R5 (CLR-1, spec 5.19): top-level session start clears
        # the session-dir claim + pending marker (child sessions returned
        # above and inherit the parent's slot).
        session_dirs.on_session_start(session_id)
        config.runtime_allowlist_clear()
        # SCR-041 R3: the confirmation-issued set follows the runtime
        # allowlist lifecycle -- cleared at every top-level session start.
        with state.session.lock:
            state.session.confirmation_issued.clear()
        verdict._reset_fail_open_flag()
        ctx = verdict._get_ctx()
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
                    if active_id and verdict.project_exemption_applies(
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
        working_dir_root, _ = verdict._resolved_config()
        if not verdict.discipline_applies(cwd, working_dir_root):
            state.session.reminder_status = "skipped-outside"
            _record_session_reminder(session_id, "skipped-outside")
            logger.debug(
                "dir-whip: session-start reminder skipped "
                "(agent CWD outside the Working Directory)"
            )
            return
        if ctx and hasattr(ctx, "inject_message"):
            injected = ctx.inject_message(verdict.REMINDER_MESSAGE)
            if injected:
                state.session.reminder_status = "injected"
                _record_session_reminder(session_id, "injected")
            else:
                state.session.reminder_status = "unavailable"
                _record_session_reminder(session_id, "unavailable")
                logger.debug(
                    "dir-whip: session-start reminder skipped "
                    "(inject_message unavailable)"
                )
        else:
            state.session.reminder_status = "unavailable"
            _record_session_reminder(session_id, "unavailable")
            logger.debug(
                "dir-whip: session-start reminder skipped "
                "(inject_message unavailable)"
            )
    except Exception as exc:
        logger.debug("dir-whip: session start hook error: %s", exc)


def on_post_tool_call(tool_name=None, args=None, result=None, task_id=None,
                      session_id=None, status=None, **kwargs):
    """post_tool_call observer adapter (5.13 D2): write-class completion.

    Records the completion + result state of write_file / patch / terminal
    calls with rule_key ``landed:<tool>``; other tools are ignored.
    """
    try:
        if tool_name not in ("write_file", "patch", "terminal"):
            return
        working_dir_root, _ = verdict._resolved_config()
        targets = verdict._extract_target_paths(tool_name, args) if isinstance(args, dict) else []
        target = targets[0] if targets else None
        # 5.18: terminal re-scan -> diff -> violation classification. Runs
        # alongside (never instead of) the landed: observation below; a
        # blocked-at-pre call has no pre snapshot and skips here.
        if tool_name == "terminal":
            audit._audit_post_check(
                session_id, task_id, is_subagent=sessions._is_child_session(session_id),
            )
        events.emit(
            "allow", tool_name, "landed:" + str(tool_name), target,
            "write tool call completed (status: %s)" % (status or "ok"),
            session_id, sessions._is_child_session(session_id),
        )
    except Exception as exc:
        logger.debug("dir-whip: post_tool_call hook error: %s", exc)


def on_post_approval_response(choice=None, session_key=None, surface=None,
                              command=None, pattern_key=None, **kwargs):
    """post_approval_response observer adapter (5.13 D2) + approval events (5.14).

    Granted/denied mapped from the host choice vocabulary. approval-resolved
    is ALWAYS emitted with the outcome; approval-requested is emitted ONLY
    when the payload exposes a request/entry state (verified absent in the
    local hermes-agent payloads). Privacy: no command/description text.
    """
    try:
        granted = verdict._approval_granted(choice)
        rule_key = "approval:granted" if granted else "approval:denied"
        events.emit(
            "allow" if granted else "block", "approval", rule_key, None,
            "host approval %s" % ("granted" if granted else "denied"),
            kwargs.get("session_id"), False,
        )
        events._bus_emit("approval-resolved", {
            "outcome": "granted" if granted else "denied",
            "rule_key": rule_key,
        })
        if "request" in kwargs or "entry" in kwargs:
            events._bus_emit("approval-requested", {
                "outcome": "requested",
                "rule_key": "approval-requested",
            })
    except Exception as exc:
        logger.debug("dir-whip: post_approval_response hook error: %s", exc)


def on_pre_command(surface=None, command=None, alias_used=None, args_raw=None,
                   session_key=None, platform=None, **kwargs):
    """pre_command observer adapter (5.15): record only, never block.

    The host ignores the return value; always returns None. Records
    surface / command / alias_used plus args_raw / session_key / platform
    when present, rule_key ``pre-command:<command>``.
    """
    try:
        working_dir_root, _ = verdict._resolved_config()
        detail = {"surface": surface, "alias_used": alias_used}
        if args_raw is not None:
            detail["args_raw"] = args_raw
        if session_key is not None:
            detail["session_key"] = session_key
        if platform is not None:
            detail["platform"] = platform
        events.emit(
            "allow", "command", "pre-command:" + str(command or ""), None,
            json.dumps(detail), None, False,
        )
    except Exception as exc:
        logger.debug("dir-whip: pre_command hook error: %s", exc)
    return None


def on_subagent_start(child_session_id=None, child_role=None, child_goal=None,
                      parent_session_id=None, parent_turn_id=None,
                      parent_subagent_id=None, child_subagent_id=None, **kwargs):
    """subagent_start hook adapter (5.4): dispatch to sessions."""
    try:
        return sessions.subagent_start(
            child_session_id, child_role, child_goal,
            parent_session_id, parent_turn_id,
            parent_subagent_id, child_subagent_id, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_start hook error (fail-open): %s", exc)
        return None


def on_subagent_stop(child_session_id=None, child_subagent_id=None,
                     child_role=None, child_status=None, duration_ms=None,
                     **kwargs):
    """subagent_stop hook adapter (5.4): dispatch to sessions."""
    try:
        return sessions.subagent_stop(
            child_session_id, child_subagent_id,
            child_role, child_status, duration_ms, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_stop hook error (fail-open): %s", exc)
        return None


def on_transform_tool_result(tool_name=None, args=None, result=None,
                             session_id=None, task_id=None, **kwargs):
    """transform_tool_result hook adapter (5.18 L1 notice): dispatch to audit."""
    try:
        return audit.transform_tool_result(
            tool_name, args, result, session_id, task_id, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: transform_tool_result hook error (fail-open): %s", exc)
        return None


def on_pre_verify(session_id=None, changed_paths=None, **kwargs):
    """pre_verify hook adapter (5.18 R5 continuation fallback): dispatch to
    audit. Returns {"action": "continue", "message": ...} when this turn
    mutated files AND unresolved pending violations remain; None otherwise
    (turn finishes naturally). Fail-open: never raises."""
    try:
        return audit.pre_verify_nudge(
            session_id, changed_paths, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: pre_verify hook error (fail-open): %s", exc)
        return None


def _briefing_issued(path):
    """True when the path already received its confirmation payload this
    session (SCR-041 R3 confirmation-issued set; casefold-insensitive on
    the forward-slash form, mirroring the runtime allowlist matching)."""
    normalized = str(path).replace("\\", "/").casefold()
    with state.session.lock:
        return any(
            normalized == str(e).replace("\\", "/").casefold()
            for e in state.session.confirmation_issued
        )


def _briefing_mark(path):
    """Record the path in the session-memory confirmation-issued set."""
    normalized = str(path).replace("\\", "/")
    with state.session.lock:
        state.session.confirmation_issued.add(normalized)


def _resolves_to_root(path, working_dir_root):
    """True when path normalizes to the Working Directory root itself
    (SCR-041 R2b; case/slash/dot-segment variants included via the
    existing normalization helpers)."""
    try:
        normalized = normalize_target(str(path), working_dir_root)
    except Exception:
        return False
    return _paths_equal(normalized, working_dir_root)


def _confirmation_payload(path, session_id):
    """The 5.11 v2.9 confirmation payload for path; the latch-context
    conditional line is appended when the pending set is non-empty (latch
    active). Fail-open: an unresolved-paths check failure omits the line."""
    payload = ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE % (
        str(path).replace("\\", "/")
    )
    try:
        if audit.audit_unresolved_paths(session_id):
            payload = payload + "\n" + ALLOW_PATH_LATCH_CONTEXT_LINE
    except Exception as exc:
        logger.debug(
            "dir-whip: allow_path latch-context check failed (fail-open): %s",
            exc,
        )
    return payload


def _allow_path_handler(args, **kwargs):
    """Registered allow_path handler (spec 5.11 v2.9/v2.11).

    Entry gating in strict order (SCR-041 R2 + SCR-043 R2c): subagent
    rejection -> Working Directory root rejection -> outside-root
    rejection -> two-step user confirmation (SCR-041 R3): the first
    call (confirm absent/false) returns the confirmation payload WITHOUT
    adding and records the path in the session-memory confirmation-issued
    set; confirm=true adds ONLY an already-briefed path (an unbriefed
    confirm=true re-issues the payload and marks the path briefed --
    confirm never adds on its own). A successful add keeps the existing
    flow: config tool + allowlisted bus event (5.14) + the symmetric
    runtime-allowlist-add stats row (SCR-040 R4, 5.13). Rejections
    record block stats rows that are bus-skipped (rule_keys in
    events._BUS_SKIP_RULE_KEYS). Fail-open: never raises.
    """
    try:
        path = args.get("path") if isinstance(args, dict) else args
        confirm = bool(args.get("confirm")) if isinstance(args, dict) else False
        session_id = kwargs.get("session_id")
        # R2a: subagents are rejected before any other check (the sanction
        # flows top-down only; parent-guidance variant, 5.11 v2.9).
        if sessions._is_child_session(session_id):
            events.emit(
                "block", "allow-path", "allow-path-subagent-rejected", None,
                "subagent-rejected", session_id, True,
            )
            return ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE
        # R2b: the Working Directory root itself is never allowlisted.
        if path:
            working_dir_root, _ = verdict._resolved_config()
            if working_dir_root and _resolves_to_root(path, working_dir_root):
                events.emit(
                    "block", "allow-path", "allow-path-root-rejected", None,
                    "root-target", session_id, False,
                )
                return ALLOW_PATH_ROOT_REJECTED_MESSAGE
            # R2c (SCR-043): an outside-root path is never allowlisted --
            # no entry is needed there (writes are allowed and logged,
            # external-write). Same lexical domain as the classify chain
            # (normalize_target + within_working_dir; no hand-rolled
            # prefix comparison).
            if working_dir_root and not within_working_dir(
                normalize_target(str(path), working_dir_root), working_dir_root
            ):
                events.emit(
                    "block", "allow-path", "allow-path-external-rejected",
                    None, "external-target", session_id, False,
                )
                return ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
        # R3: two-step user confirmation (main-agent path only).
        if path:
            briefed = _briefing_issued(path)
            if not briefed:
                _briefing_mark(path)
                if confirm:
                    logger.debug(
                        "dir-whip: allow_path confirmation payload re-issued "
                        "(confirm=true without a prior briefing): %s",
                        str(path).replace("\\", "/"),
                    )
                else:
                    logger.debug(
                        "dir-whip: allow_path confirmation payload issued "
                        "(first call): %s",
                        str(path).replace("\\", "/"),
                    )
            if not (confirm and briefed):
                return _confirmation_payload(path, session_id)
        # Confirmed add (existing flow unchanged). SCR-043 R3: the root is
        # resolved BEFORE the add call and passed through so the add layer
        # can assert the strict-subtree value domain (None = fail-open,
        # assertion skipped).
        working_dir_root, _ = verdict._resolved_config()
        result = config.dir_whip_allow_path(
            args, working_dir_root=working_dir_root, **kwargs
        )
        if path:
            events._bus_emit("allowlisted", {
                "outcome": "allowlisted",
                "rule_key": "runtime-allowlist",
                "target": relativize_target(path, working_dir_root),
            })
            # SCR-040 R4 (5.13 v2.8): symmetric stats row -- allow/
            # allow-path; the emit channel relativizes the target (same
            # privacy shape as the bus event above). Allow outcome -> no
            # extra bus fanout (the 5.14 emit surface stays at 7).
            events.emit(
                "allow", "allow-path", "runtime-allowlist-add", path,
                "runtime allowlist entry added", session_id,
                sessions._is_child_session(session_id),
            )
        return result
    except Exception as exc:
        logger.debug("dir-whip: allow_path handler error (fail-open): %s", exc)
        return None


__all__ = ["register"]
