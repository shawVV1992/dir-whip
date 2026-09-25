"""dir-whip plugin for Hermes -- assembly layer over the pure decision/state modules: register(ctx) + the hook-adapter surface + the ONLY host-API touch point (ADR-0007).

Three guarded host imports (absence -> None -> documented fallback):
get_session_cwd / resolve_agent_cwd fill the CWD injection slots (missing
accessor -> on_start always injects); projects_db.connect_closing /
get_active_id fill the project-active probe slot (missing -> no
project-mode exemption). Single fail-open try/except layer for hook
dispatch: every hook adapter is a THIN dispatch; any registration error
logs a warning and Hermes continues normally.

Layer: assembly
Refs: spec 5.7, spec 5.8, ADR-0007
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

# Project-mode exemption: projects_db imported ONLY here.
try:
    from hermes_cli.projects_db import (
        connect_closing as _projects_connect_closing,
        get_active_id as _projects_get_active_id,
    )
except ImportError:
    _projects_connect_closing = None
    _projects_get_active_id = None

from . import allowlist_writer, audit, audit_prompts, claims, classify, config, events, guard, logsetup, report, runtime_allowlist, session_dirs, session_start, state, stats, subagents, terminal
from .events import (
    RULE_KEY_APPROVAL_DENIED,
    RULE_KEY_APPROVAL_GRANTED,
    RULE_KEY_APPROVAL_REQUESTED,
)

logger = logging.getLogger("dir-whip")

# Plugin-owned tools excluded from the unseen probe (D4, v2.25 SCR-057).
_PLUGIN_TOOLS = frozenset(("dir_whip_allow_path", "dir_whip_settle"))


def _observe_unseen_tool(tool_name, session_id):
    """One DEBUG observation per (session, tool) for tools outside the
    write-class set (v2.25 SCR-057 D4 probe): the next unobserved
    write-capable channel surfaces here instead of via an incident.
    Excludes plugin-owned tools; throttled per session+tool (mirrors the
    one-time fail-open warning precedent); fail-open, never raises.
    """
    try:
        if not tool_name or tool_name in _PLUGIN_TOOLS:
            return
        key = (session_id, tool_name)
        with state.session.lock:
            if key in state.session.unseen_tools:
                return
            state.session.unseen_tools.add(key)
        events.emit(
            "allow", tool_name, "unseen:" + str(tool_name), None,
            "non-write-class tool observed (once per session)", session_id,
            subagents._is_subagent_session(session_id),
        )
    except Exception as exc:
        logger.debug("dir-whip: unseen probe failed (fail-open): %s", exc)

# Runtime allowlist surface: see runtime_allowlist.py.
from .runtime_allowlist import (
    ALLOW_PATH_TOOL_SCHEMA,
    ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE,
    ALLOW_PATH_ROOT_REJECTED_MESSAGE,
    ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE,
    ALLOW_PATH_LATCH_CONTEXT_LINE,
)
from .config import ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE

# Bundled skill description (spec 3.1; converged sentence).
SKILL_DESCRIPTION = (
    "Use when creating, saving, writing, moving, or deleting files, "
    "organizing deliverables, designing workspace layout, auditing "
    "workspace compliance, or locating and reusing files from past "
    "sessions. Enforces session directory discipline and two-step "
    "confirmation for destructive operations."
)


def _project_active_probe():
    """Host projects.db probe: (active_id, [folder paths]) or None.

    Reads the ACTIVE project via get_active_id, then its folder paths
    from project_folders (primary_path + folders; the folder set is what
    the exemption containment matches against). Called at on_start,
    never cached at register. Fail-open: ANY error -> None.
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
    """Register dir-whip hooks, tool, command, skill and event bus.

    Hooks: pre_tool_call / on_session_start / post_tool_call /
    post_approval_response / pre_command / subagent_start / subagent_stop /
    transform_tool_result (L1 notice) / pre_verify (continuation
    fallback). Tool: dir_whip_allow_path (the ONLY eager tool;
    dir_whip_settle registers lazily on the first L1 notice fire).
    Event bus: hasattr(ctx, "emit") -> silent degradation when absent.
    Fail-open: any registration error logs a warning; the plugin is
    disabled but Hermes continues normally. Three stage calls (state
    wiring / hooks / tool+command+skill).
    """
    try:
        _wire_register_state(ctx)
        _register_hooks(ctx)
        _register_tool_command_skill(ctx)
        logger.debug("dir-whip: registered successfully")
    except Exception as exc:
        logger.warning("dir-whip: registration failed: %s", exc)


def _wire_register_state(ctx):
    """Register-time state wiring (single fail-open layer in register)."""
    state.session.registered_ctx = ctx
    # Attach the diagnostic log FIRST (fail-open).
    logsetup.setup()
    # Precompute plugin paths/version once (message building and report
    # rendering read state, never __file__).
    plugin_dir = str(Path(__file__).resolve().parent)
    state.session.plugin_dir = plugin_dir
    state.session.script_resolver_path = os.path.normpath(
        os.path.join(plugin_dir, "skills", "workspace-organization", "scripts")
    )
    state.session.skill_md_path = os.path.join(
        plugin_dir, "skills", "workspace-organization", "SKILL.md"
    )
    state.session.plugin_version = report.plugin_version()
    # Assembly-layer injection (ADR-0007): wire the audit + orphan-scan
    # classifiers BEFORE any hook can fire (inject-don't-import).
    audit.set_classifier(classify.classify_target)
    session_dirs.set_classifier(classify.classify_target)
    # Restore the write-through claims store BEFORE any hook fires
    # (fail-open, validation drops dead entries).
    claims.load_claims()
    # Host API injection slots (ADR-0007): absent host API -> None ->
    # on_start always injects.
    state.session.session_cwd_fn = _get_session_cwd
    state.session.agent_cwd_fn = _resolve_agent_cwd
    # Project probe slot: filled only when the host module imported (same
    # shape as the CWD slots); the probe itself runs per session.
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


def _register_hooks(ctx):
    """Register the nine host hooks."""
    ctx.register_hook("pre_tool_call", _guard_hook)
    ctx.register_hook("on_session_start", on_start)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_approval_response", on_post_approval_response)
    ctx.register_hook("pre_command", on_pre_command)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)
    # pre_verify continuation fallback; nudge budget = session-cumulative
    # cap=3 (audit_prompts.PRE_VERIFY_NUDGE_CAP).
    ctx.register_hook("pre_verify", on_pre_verify)


def _register_tool_command_skill(ctx):
    """Eager tool + /dir-whip command + bundled skill."""
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
    # The /dir-whip command (merged report) lives in report.py.
    report.register_dir_whip_commands(ctx)
    # Bundled skill (opt-in, qualified name); the once-per-session
    # discipline block replaced the always-on prompt.
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


def _guard_hook(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call hook adapter (never raises; fail-open -> None).

    Lazy stats session-field backfill after a host restart -- the first
    session_id-carrying call restores attribution (idempotent; profile /
    started_at stay unknown).
    """
    try:
        session_id = kwargs.get("session_id")
        if session_id and not state.stats.session.get("session_id"):
            # Lock-held check-and-set; unlocked read = fast path.
            stats.stats_backfill_session(session_id)
        return guard.guard(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: guard hook error (fail-open): %s", exc)
        return None


def on_start(session_id, model=None, platform=None, **kwargs):
    """on_session_start hook adapter: thin fail-open dispatch.

    The session-start decision chain lives in session_start.py (see its
    module header); this adapter dispatches and swallows failures.
    """
    try:
        session_start.session_start(session_id, state.session.registered_ctx)
    except Exception as exc:
        logger.debug("dir-whip: session start hook error: %s", exc)


def on_post_tool_call(tool_name=None, args=None, result=None, task_id=None,
                      session_id=None, status=None, **kwargs):
    """post_tool_call observer adapter: records write-class tool
    completions (rule_key ``landed:<tool>``); paired tools (terminal /
    execute_code) additionally run the 5.18 re-scan; other tools ignored."""
    try:
        if tool_name not in guard.WRITE_CLASS_TOOLS:
            _observe_unseen_tool(tool_name, session_id)
            return
        # Seed the config cache / session root (side-effect call).
        config.ensure_session_root()
        targets = guard.extract_target_paths(tool_name, args) if isinstance(args, dict) else []
        target = targets[0] if targets else None
        # Paired-tool re-scan -> diff -> violation classification; runs
        # alongside (never instead of) the landed: observation below.
        if tool_name in audit.AUDIT_PAIRED_TOOLS:
            audit.audit_post_check(
                session_id, task_id, is_subagent=subagents._is_subagent_session(session_id),
            )
        events.emit(
            "allow", tool_name, "landed:" + str(tool_name), target,
            "write tool call completed (status: %s)" % (status or "ok"),
            session_id, subagents._is_subagent_session(session_id),
        )
    except Exception as exc:
        logger.debug("dir-whip: post_tool_call hook error: %s", exc)


def on_post_approval_response(choice=None, session_key=None, surface=None,
                              command=None, pattern_key=None, **kwargs):
    """post_approval_response observer adapter + approval events.

    Granted/denied mapped from the host choice vocabulary; approval-resolved
    always emitted, approval-requested only with a request/entry state in
    the payload. Privacy: no command/description text.
    """
    try:
        granted = guard.approval_granted(choice)
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
    """pre_command observer adapter: record only, never block (host
    ignores the return; always None). Records surface / command / alias_used
    + args_raw / session_key / platform, rule_key ``pre-command:<command>``."""
    try:
        # Seed the config cache / session root (side-effect call).
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
    """subagent_start hook adapter: dispatch to subagents."""
    try:
        return subagents.subagent_start(
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
    """subagent_stop hook adapter: dispatch to subagents."""
    try:
        return subagents.subagent_stop(
            child_session_id, child_subagent_id,
            child_role, child_status, duration_ms, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_stop hook error (fail-open): %s", exc)
        return None


def on_transform_tool_result(tool_name=None, args=None, result=None,
                             session_id=None, task_id=None, **kwargs):
    """transform_tool_result hook adapter (L1 notice + fallback).

    audit_prompts first, then the one-shot REMINDER fallback note
    (session_start.append_reminder_fallback) on the result when an
    unavailable start armed it.
    """
    try:
        adjusted = audit_prompts.transform_tool_result(
            tool_name, args, result, session_id, task_id, **kwargs,
        )
        return session_start.append_reminder_fallback(
            adjusted, result, session_id
        )
    except Exception as exc:
        logger.debug("dir-whip: transform_tool_result hook error (fail-open): %s", exc)
        return None


def on_pre_verify(session_id=None, changed_paths=None, **kwargs):
    """pre_verify hook adapter (continuation fallback): dispatch to
    audit_prompts; {"action": "continue", "message": ...} when this turn
    mutated files AND unresolved violations remain, else None (fail-open)."""
    try:
        return audit_prompts.pre_verify_nudge(
            session_id, changed_paths, **kwargs,
        )
    except Exception as exc:
        logger.debug("dir-whip: pre_verify hook error (fail-open): %s", exc)
        return None


def _allow_path_handler(args, **kwargs):
    """Thin adapter over runtime_allowlist.handle.

    Entry-gating lives in runtime_allowlist.py; the name stays for tests +
    ctx.register_tool; fail-open layer lives here.
    """
    try:
        return runtime_allowlist.handle(args, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: allow_path handler error (fail-open): %s", exc)
        return None


# Declared surface: register + the thin hook adapters.
__all__ = [
    "register", "_guard_hook", "on_start", "on_post_tool_call",
    "on_post_approval_response", "on_pre_command", "on_subagent_start",
    "on_subagent_stop", "on_transform_tool_result", "on_pre_verify",
]
