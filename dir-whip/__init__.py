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

try:
    from . import audit, config, events, report, sessions, state, stats, verdict
except ImportError:
    import audit
    import config
    import events
    import report
    import sessions
    import state
    import stats
    import verdict

try:
    from .paths import relativize_target
except ImportError:
    from paths import relativize_target

logger = logging.getLogger("dir-whip")

# Spec 5.11: the plugin's ONLY tool (OpenAI function-call format required by
# Hermes tools.registry). Registered at register() via ctx.register_tool.
ALLOW_PATH_TOOL_SCHEMA = {
    "name": "dir_whip_allow_path",
    "description": (
        "Add an absolute path to the dir-whip runtime allowlist so "
        "file operations under that path are exempt for this session (Tier 0). "
        "Use when the user explicitly specifies a path to write to."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to allow (forward slashes)",
            }
        },
        "required": ["path"],
    },
}

# Spec 3.1: bundled skill description (frontmatter + register_skill).
# Trigger words within the first 57 chars; avoids "organize/clean up
# sessions" phrasing (F4). Matches SKILL.md frontmatter description.
SKILL_DESCRIPTION = (
    "Use when creating, saving, writing, moving, or deleting files in a "
    "Hermes workspace, organizing deliverables, or auditing workspace "
    "compliance."
)


def register(ctx):
    """Register dir-whip hooks, tool and event bus (5.7/5.8/5.14).

    Hooks: pre_tool_call, on_session_start, post_tool_call,
    post_approval_response, pre_command, subagent_start, subagent_stop,
    transform_tool_result (5.18 L1 notice). Tool: dir_whip_allow_path
    (the plugin's ONLY tool). Event bus: capability detected via
    hasattr(ctx, "emit"); absent -> silent degradation. Fail-open: any
    registration error logs a warning; the plugin is disabled but Hermes
    continues normally.
    """
    try:
        state.session.registered_ctx = ctx
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
            return
        # 5.18: top-level session start clears the audit state (pending
        # violations, leftover pre snapshots, cap warning); child sessions
        # skip and inherit the parent's latched state.
        audit._audit_session_start(session_id)
        config.runtime_allowlist_clear()
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
        working_dir_root, _ = verdict._resolved_config()
        if not verdict.discipline_applies(cwd, working_dir_root):
            state.session.reminder_status = "skipped-outside"
            logger.debug(
                "dir-whip: session-start reminder skipped "
                "(agent CWD outside the Working Directory)"
            )
            return
        if ctx and hasattr(ctx, "inject_message"):
            injected = ctx.inject_message(verdict.REMINDER_MESSAGE)
            if injected:
                state.session.reminder_status = "injected"
            else:
                state.session.reminder_status = "unavailable"
                logger.debug(
                    "dir-whip: session-start reminder skipped "
                    "(inject_message unavailable)"
                )
        else:
            state.session.reminder_status = "unavailable"
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


def _allow_path_handler(args, **kwargs):
    """Registered allow_path handler: config tool + allowlisted event (5.14)."""
    try:
        path = args.get("path") if isinstance(args, dict) else args
        result = config.dir_whip_allow_path(args, **kwargs)
        if path:
            working_dir_root, _ = verdict._resolved_config()
            events._bus_emit("allowlisted", {
                "outcome": "allowlisted",
                "rule_key": "runtime-allowlist",
                "target": relativize_target(path, working_dir_root),
            })
        return result
    except Exception as exc:
        logger.debug("dir-whip: allow_path handler error (fail-open): %s", exc)
        return None


__all__ = ["register"]
