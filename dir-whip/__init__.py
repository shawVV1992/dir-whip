"""dir-whip plugin for Hermes -- assembly layer over the pure decision/state modules: register(ctx) + the hook-adapter surface + the ONLY host-API touch point (SCR-035, ADR-0007).

Three guarded host imports (absence -> None -> documented fallback): hermes_cli.tools.terminal_tool.get_session_cwd and agent.runtime_cwd.resolve_agent_cwd fill the CWD injection slots (missing accessor -> on_start always injects); hermes_cli.projects_db.connect_closing / get_active_id fill the project-active probe slot (missing -> no SCR-039 R7 project-mode exemption). Single fail-open try/except layer for hook dispatch: any registration error logs a warning and Hermes continues normally. SCR-050 v3 R6.2 (spec 5.1 v2.19): every hook adapter is a THIN dispatch -- the session-start decision chain lives in lifecycle.py, the guard chain in verdict.py, the observers in sessions/audit/events.

Layer: assembly
Refs: spec 3.1, spec 5.4, spec 5.7, spec 5.8, spec 5.11, spec 5.13, spec 5.14, spec 5.15, spec 5.17, spec 5.18, spec 5.19, SCR-035, SCR-039, SCR-040, SCR-041, SCR-044, SCR-045, SCR-048, SCR-050, ADR-0007
Key exports:
  - register -- register the host hooks, the dir_whip_allow_path tool, the /dir-whip command, the bundled skill and the event bus (single fail-open layer).
  - _guard_hook, on_start, on_post_tool_call, on_post_approval_response, on_pre_command, on_subagent_start, on_subagent_stop, on_transform_tool_result, on_pre_verify -- the thin host-hook adapters; each fail-open, never raises.
  - state.session.session_cwd_fn / agent_cwd_fn / project_active_fn -- injected host-API slots (ADR-0007); unimportable host API -> None -> documented fallback.
"""

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

from . import allow_path, audit, config, events, lifecycle, logsetup, report, sessions, session_dirs, state, stats, verdict
from .events import (
    RULE_KEY_APPROVAL_DENIED,
    RULE_KEY_APPROVAL_GRANTED,
    RULE_KEY_APPROVAL_REQUESTED,
)

logger = logging.getLogger("dir-whip")

# Spec 5.11 allow_path surface (SCR-045 R4): the entry-gating chain, its
# helpers and the five message/schema constants live in the core module
# allow_path.py; re-exported here so historical import paths (tests read
# dirwhip.ALLOW_PATH_*) stay identical. The EXTERNAL rejection message is
# single-sourced in config.py (PB-2: config must not import the assembly
# layer, ADR-0007 direction; both layers answer identically).
from .allow_path import (
    ALLOW_PATH_TOOL_SCHEMA,
    ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE,
    ALLOW_PATH_ROOT_REJECTED_MESSAGE,
    ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE,
    ALLOW_PATH_LATCH_CONTEXT_LINE,
)
from .config import ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE

# Spec 3.1 (v2.15 DF-13 convergence): bundled skill description
# (frontmatter + register_skill). E1 (SKILL.md frontmatter) and E2 (this
# constant) are the SAME converged sentence (equality-locked, DF-t3).
# Trigger words within the first 57 chars; avoids "organize/clean up
# sessions" phrasing (F4).
SKILL_DESCRIPTION = (
    "Use when creating, saving, writing, moving, or deleting files, "
    "organizing deliverables, designing workspace layout, auditing "
    "workspace compliance, or locating and reusing files from past "
    "sessions. Enforces session directory discipline and two-step "
    "confirmation for destructive operations."
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
        state.session.plugin_version = report.plugin_version()
        # Assembly-layer injection (ADR-0007): wire the audit classifier
        # BEFORE any hook can fire.
        audit.set_classifier(verdict.classify_target)
        # SCR-044 R7: the orphan-scan classifier is wired the same way
        # (ADR-0007 inject-don't-import; session_dirs never imports
        # verdict).
        session_dirs.set_classifier(verdict.classify_target)
        # SCR-048 R1 (spec 5.19): restore the write-through claims store
        # BEFORE any hook can fire, so a host restart re-arms the
        # per-session slot (fail-open inside load_claims; validation
        # drops entries whose root/dir is gone).
        session_dirs.load_claims()
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
        # Spec 5.17: bundled skill (opt-in, qualified name) + discipline
        # block (SCR-052 R1: stale "discipline prompt" wording corrected --
        # the always-on prompt was removed; the once-per-session block
        # replaced it).
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
    """Pre-tool-call hook adapter (5.8: never raises; fail-open -> None).

    SCR-048 R6 (5.13 v2.16): lazy stats session-field backfill -- after a
    host process restart (on_session_start not yet re-fired) the first
    guarded call whose payload carries a session_id restores stats
    attribution from this call onward. Existing values are NEVER
    overwritten (idempotent); profile / started_at stay unknown (None).
    """
    try:
        session_id = kwargs.get("session_id")
        if session_id and not state.stats.session.get("session_id"):
            # Lock-held check-and-set (SCR-048 R6 follow-up): the outer
            # unlocked read is only a fast path.
            stats.stats_backfill_session(session_id)
        return verdict.guard(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: guard hook error (fail-open): %s", exc)
        return None


def on_start(session_id, model=None, platform=None, **kwargs):
    """on_session_start hook adapter (5.4): thin fail-open dispatch.

    SCR-050 v3 R6.2 (spec 5.1 v2.19): the session-start decision chain
    (child short-circuit, state resets, SCR-027 profile re-resolution,
    R2 cwd conditional injection, R7 project exemption, reminder
    lifecycle, advisory orphan scan) lives in the core module
    lifecycle.py; this adapter only dispatches and swallows failures
    (5.8 fail-open single layer).
    """
    try:
        lifecycle.session_start(session_id, state.session.registered_ctx)
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
        # Seed the config cache / session root (SCR-045 R2: explicit
        # side-effect call; the resolved values are not needed here).
        config.ensure_session_root()
        targets = verdict.extract_target_paths(tool_name, args) if isinstance(args, dict) else []
        target = targets[0] if targets else None
        # 5.18: terminal re-scan -> diff -> violation classification. Runs
        # alongside (never instead of) the landed: observation below; a
        # blocked-at-pre call has no pre snapshot and skips here.
        if tool_name == "terminal":
            audit.audit_post_check(
                session_id, task_id, is_subagent=sessions._is_subagent_session(session_id),
            )
        events.emit(
            "allow", tool_name, "landed:" + str(tool_name), target,
            "write tool call completed (status: %s)" % (status or "ok"),
            session_id, sessions._is_subagent_session(session_id),
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
        granted = verdict.approval_granted(choice)
        rule_key = (
            RULE_KEY_APPROVAL_GRANTED if granted
            else RULE_KEY_APPROVAL_DENIED
        )
        events.emit(
            "allow" if granted else "block", "approval", rule_key, None,
            "host approval %s" % ("granted" if granted else "denied"),
            kwargs.get("session_id"), False,
        )
        events.bus_emit("approval-resolved", {
            "outcome": "granted" if granted else "denied",
            "rule_key": rule_key,
        })
        if "request" in kwargs or "entry" in kwargs:
            events.bus_emit("approval-requested", {
                "outcome": "requested",
                "rule_key": RULE_KEY_APPROVAL_REQUESTED,
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
        # Seed the config cache / session root (SCR-045 R2: explicit
        # side-effect call; the resolved values are not needed here).
        config.ensure_session_root()
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
    """transform_tool_result hook adapter (5.18 L1 notice + 5.17 fallback).

    SCR-050 v3 R6.2: dispatches to audit first; then applies the one-shot
    REMINDER fallback note (lifecycle.append_reminder_fallback) to the
    (possibly audit-adjusted) string result when an unavailable session
    start armed it."""
    try:
        adjusted = audit.transform_tool_result(
            tool_name, args, result, session_id, task_id, **kwargs,
        )
        return lifecycle.append_reminder_fallback(
            adjusted, result, session_id
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


def _allow_path_handler(args, **kwargs):
    """Thin adapter over allow_path.handle (SCR-045 R4).

    The entry-gating chain (subagent -> root -> outside-root rejection,
    two-step confirmation, confirmed add) moved to the core module
    allow_path.py. This historical name stays because tests call it
    directly (11 sites) and register() hands it to ctx.register_tool.
    The fail-open single layer lives here (handle itself never catches).
    """
    try:
        return allow_path.handle(args, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: allow_path handler error (fail-open): %s", exc)
        return None


# Declared surface (SCR-052 R1 G10.7): __all__ aligned with the module
# docstring export list -- register plus the thin hook adapters.
__all__ = [
    "register",
    "_guard_hook",
    "on_start",
    "on_post_tool_call",
    "on_post_approval_response",
    "on_pre_command",
    "on_subagent_start",
    "on_subagent_stop",
    "on_transform_tool_result",
    "on_pre_verify",
]
