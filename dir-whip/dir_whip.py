"""Core guard logic: pre-tool-call interception and decision chain (v0.3.1).

Implements the spec 5.3 unified chain (Tier 0 exempt/allowlist -> root-file
whitelist -> session dir -> block; external -> allow + log), the 5.10 coarse
terminal tiers (block: redirect/touch/cp-mv; uncertain: allow + log), the
exact block message (main + subagent variant), the 5.12 fail-open warning,
and the 5.13 structured single-line verdict events (logging part; event-bus
emit is task 26.7). There is NO approve tier and NO memo machinery.
"""

import datetime
import json
import logging
import ntpath
import os
import re
import threading
from pathlib import Path

try:
    from .config import (
        _relativize_target,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        refresh_resolution,
        register_dir_whip_commands,
        reset_cache,
        runtime_allowlist_clear,
        set_session_profile,
        stats_record,
        stats_set_session,
        terminal_guard_enabled,
        write_audit_enabled,
        write_audit_entry_cap,
        dir_whip_allow_path,
    )
except ImportError:
    from config import (
        _relativize_target,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        refresh_resolution,
        register_dir_whip_commands,
        reset_cache,
        runtime_allowlist_clear,
        set_session_profile,
        stats_record,
        stats_set_session,
        terminal_guard_enabled,
        write_audit_enabled,
        write_audit_entry_cap,
        dir_whip_allow_path,
    )

try:
    from .terminal import (
        _DEVICE_PATHS,
        _terminal_block_targets,
        _terminal_uncertain,
        _tokenize_command,
    )
except ImportError:
    from terminal import (
        _DEVICE_PATHS,
        _terminal_block_targets,
        _terminal_uncertain,
        _tokenize_command,
    )

# get_session_cwd is a Hermes runtime API (tools/terminal_tool.py) absent
# from the test venv. Guarded module-level import so dir_whip.py never crashes
# when unavailable; callers fall back to working_dir_root. Tests inject a
# fake via _session_cwd_fn.
try:
    from hermes_cli.tools.terminal_tool import get_session_cwd
except Exception:
    get_session_cwd = None

logger = logging.getLogger("dir-whip")

INTERCEPTED_TOOLS = ("write_file", "patch", "terminal")
PATCH_FILE_RE = re.compile(r"^\*\*\* Update File:\s*(.+)$", re.MULTILINE)

# MSYS-style forward-slash drive forms (SCR-006, task 9.9).
# Matches /c/..., //c/... (single drive letter) but NOT UNC \\server\share.
_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")

# Spec 5.12 (term-updated): injected once per session when the guard is
# disabled because working_dir_root could not be resolved.
FAIL_OPEN_WARNING_MESSAGE = (
    "[dir-whip] WARNING: The guard is DISABLED because the Working "
    "Directory\n"
    "could not be resolved. File writes are NOT being enforced.\n"
    "Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml\n"
    "(terminal.cwd) and restart the session."
)

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

# Context stored at register() time.
_registered_ctx = None

# Spec 5.12: one-time fail-open warning per session (reset by
# _reset_fail_open_flag; on_session_start wiring is task 26.7).
_fail_open_warned = False

# Session-CWD accessor for relative-target resolution (spec 5.3 step 4).
# Tests inject a fake; degrades to working_dir_root when unavailable.
_session_cwd_fn = get_session_cwd

# Spec 5.4: child sessions (subagent_start -> subagent_stop) are tracked so
# on_session_start skips the top-level actions for them. Lock-guarded set.
_child_session_ids = set()
_child_session_ids_lock = threading.Lock()

# Spec 5.14: event-bus capability flag, detected at register() (hasattr
# ctx.emit). Bus absent -> no emit, no error, one DEBUG line per attempt.
_emit_enabled = False

# Spec 5.4: session-start discipline reminder (top-level sessions only).
REMINDER_MESSAGE = (
    "[dir-whip] Active. File writes in the Working Directory must be "
    "inside a Session Directory (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). "
    "Use create_session_dir.py to create one before writing files."
)

# Spec 5.13 D2: host approval choices that count as granted (verified
# against the local hermes-agent approval.py choice vocabulary).
_APPROVAL_GRANTED_CHOICES = frozenset(
    ("approve", "always", "session", "granted", "allow", "smart_approve")
)

# Spec 3.1: bundled skill description (frontmatter + register_skill).
# Trigger words within the first 57 chars; avoids "organize/clean up
# sessions" phrasing (F4). Matches SKILL.md frontmatter description.
SKILL_DESCRIPTION = (
    "Use when creating, saving, writing, moving, or deleting files in a "
    "Hermes workspace, organizing deliverables, or auditing workspace "
    "compliance."
)

# Spec 3.7/5.17: always-on discipline prompt (<=200 chars, four elements:
# classify before write / session-dir writes / no root writes / when
# blocked). The full C6 template is delivered by the block message, NOT
# by this prompt.
DISCIPLINE_PROMPT = (
    "[dir-whip] 写前分类：任何创建或写入前，先说明目标类别（会话目录 / 根白名单文件 / 外部路径）。"
    "会话目录落盘：工作目录内的写入必须落入会话目录的 Outputs/ 或 .tmp/。"
    "根目录禁写：工作目录根只允许白名单文件、会话目录和 .hermes/。"
    "被拦截时：遵循拦截消息创建会话目录后重试，回复 [Reason]/[Next]，不要重试同一路径。"
)

# ================================================================
# Root write audit state (spec 5.18). Detection backbone + the
# handling ladder: L1 fire-once notice (transform_tool_result), L2
# verdict/bus events, L3 pending-violation latch gate in guard().
# ================================================================

# One in-flight pre snapshot per (session_id, task_id) terminal call; the
# post re-scan pops it and diffs. Commands blocked at pre store nothing,
# so their post finds no pairing and skips (5.18 mechanism).
_audit_pre_snapshots = {}
_audit_pre_snapshots_lock = threading.Lock()

# Session-scoped pending violations: {abs_path: {"first_seen": iso,
# "announced": bool}} per owner session (5.18 latch). Child sessions
# resolve into the parent's set via _audit_session_parents / the most
# recent top-level session, matching the 5.4 child_session_ids gate.
_audit_pending = {}
_audit_pending_lock = threading.Lock()
_audit_session_parents = {}
_audit_top_session = None

# One-time entry-cap WARNING per top-level session (reset at session
# start, mirroring the fail-open warning flag).
_audit_cap_warned = False


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
    global _registered_ctx, _emit_enabled
    try:
        _registered_ctx = ctx
        try:
            _emit_enabled = bool(getattr(ctx, "emit", None))
        except Exception:
            _emit_enabled = False
        reset_cache()
        get_cached_config(ctx)
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
        # config.py (D3).
        register_dir_whip_commands(ctx)
        # Spec 5.17: bundled skill (opt-in, qualified name) + discipline prompt.
        try:
            skill_md = Path(__file__).parent / "skills" / "workspace-organization" / "SKILL.md"
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
        try:
            if hasattr(ctx, "register_system_prompt_section"):
                ctx.register_system_prompt_section(
                    "dir-whip-discipline", DISCIPLINE_PROMPT
                )
        except Exception as exc:
            logger.warning(
                "dir-whip: register_system_prompt_section failed: %s", exc
            )
        logger.debug("dir-whip: registered successfully")
    except Exception as exc:
        logger.warning("dir-whip: registration failed: %s", exc)


def _guard_hook(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call hook (5.8: never raises; fail-open -> None)."""
    try:
        return guard(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("dir-whip: guard hook error (fail-open): %s", exc)
        return None


def guard(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call decision chain (spec 5.3).

    Returns None (allow) or a block dict {"action": "block", "message"}.
    Intercepts ONLY write_file / patch / terminal; the guard-disabled
    shortcut (working_dir_root None) runs BEFORE path extraction.
    """
    if tool_name not in INTERCEPTED_TOOLS:
        return None

    is_subagent = bool(kwargs.get("is_subagent", False))
    session_id = kwargs.get("session_id")
    # 5.13: verdicts split by is_subagent — child membership in the
    # child_session_ids set (5.4) implies a subagent write.
    if not is_subagent and session_id and _is_child_session(session_id):
        is_subagent = True
    ctx = _get_ctx()
    working_dir_root, exempt_paths = get_cached_config(ctx)

    # Guard-disabled shortcut (5.3 step 2): one-time warning + allow.
    if working_dir_root is None:
        _warn_fail_open_once(ctx, tool_name, session_id, is_subagent)
        return None

    # L3 settlement gate (5.18): an unresolved pending violation latches
    # the NEXT write-class call until remediation. Runs BEFORE target
    # extraction / classification / the audit pre snapshot; a gated call
    # never snapshots (the command did not run). Fail-open: a gate-side
    # error allows the call (5.8), a failed re-scan keeps the latch.
    unresolved = _audit_gate_unresolved(session_id, working_dir_root,
                                        exempt_paths)
    if unresolved:
        return _audit_gate_block(tool_name, session_id, is_subagent,
                                 working_dir_root, unresolved)

    if tool_name == "terminal":
        result = _guard_terminal(
            args, task_id, working_dir_root, exempt_paths, is_subagent, session_id
        )
        # 5.18 audit pre-snapshot runs ONLY when the front layer decided
        # to allow -- this covers every command-will-execute path (heredoc
        # demotion, guard-disabled, device exemption, uncertain tier);
        # blocked calls never snapshot (nothing to pair at post).
        if result is None:
            _audit_pre_snapshot(session_id, task_id, working_dir_root, exempt_paths)
        return result

    target_paths = _extract_target_paths(tool_name, args)
    if not target_paths:
        return None

    for target in target_paths:
        abs_target = _resolve_target(target, task_id, working_dir_root)
        normalized = normalize_target(abs_target, working_dir_root)
        verdict = classify_target(normalized, working_dir_root, exempt_paths, is_subagent)
        if verdict["outcome"] == "block":
            _emit_verdict(
                "block", tool_name, verdict["rule_key"], normalized,
                reason="write blocked by guard rule", working_dir_root=working_dir_root,
                is_subagent=is_subagent, session_id=session_id,
            )
            return {"action": "block", "message": verdict["message"]}
        _emit_verdict(
            verdict["outcome"], tool_name, verdict["rule_key"], normalized,
            reason=_verdict_reason(verdict["outcome"]), working_dir_root=working_dir_root,
            is_subagent=is_subagent, session_id=session_id,
        )
    return None


def _get_ctx():
    """Return the registered ctx (tests set dir_whip._registered_ctx)."""
    return _registered_ctx


def _verdict_reason(outcome):
    """Short reason string for a verdict event (5.13)."""
    if outcome == "external-write":
        return "target outside working_dir_root"
    return None


# ---------------------------------------------------------------- Structured verdict events (spec 5.13, logging part)

def _emit_verdict(outcome, tool, rule_key, target, reason, working_dir_root,
                  is_subagent=False, session_id=None, bus_event=True):
    """Emit ONE single-line structured verdict event (5.13 logging part).

    Levels: block / fail-open -> WARNING; external-write -> INFO; other
    allows -> DEBUG. Also records the verdict via config stats (counters +
    stats.jsonl append). Verdict-derived bus events (blocked /
    external-write, 5.14) are emitted unless bus_event=False (callers that
    handle their own events, e.g. approval). Never raises (fail-open, 5.8).
    """
    try:
        stats_record(
            outcome, tool, rule_key, target=target, reason=reason,
            is_subagent=bool(is_subagent), working_dir_root=working_dir_root,
        )
        rel_target = _relativize_target(target, working_dir_root)
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
        elif outcome == "external-write":
            logger.info("dir-whip: verdict %s", line)
        else:
            logger.debug("dir-whip: verdict %s", line)
        # 5.14: verdict-derived bus events (privacy-shaped relative target).
        if bus_event and outcome == "block":
            _bus_emit("blocked", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
        elif bus_event and outcome == "external-write":
            _bus_emit("external-write", {
                "outcome": outcome,
                "rule_key": rule_key,
                "target": rel_target,
            })
    except Exception as exc:
        logger.debug("dir-whip: verdict emission failed (fail-open): %s", exc)


# ---------------------------------------------------------------- Fail-open warning (spec 5.12)

def _warn_fail_open_once(ctx, tool_name, session_id, is_subagent):
    """Inject the one-time fail-open warning + record a fail-open verdict.

    Fires at most once per session (module flag; reset by
    _reset_fail_open_flag). Gateway degrade: inject_message unavailable or
    falsy -> the WARNING log line is the delivery. Never raises.
    """
    global _fail_open_warned
    if not _fail_open_warned:
        _fail_open_warned = True
        try:
            if ctx and hasattr(ctx, "inject_message"):
                ctx.inject_message(FAIL_OPEN_WARNING_MESSAGE)
        except Exception:
            pass
    _emit_verdict(
        "fail-open", tool_name, "fail-open", None,
        reason="working_dir_root unresolved", working_dir_root=None,
        is_subagent=is_subagent, session_id=session_id,
    )


def _reset_fail_open_flag():
    """Reset the one-time fail-open warning flag (26.7's on_session_start
    calls this; tests use it too)."""
    global _fail_open_warned
    _fail_open_warned = False


# ---------------------------------------------------------------- Event bus (spec 5.14)

def _bus_emit(event_name, payload):
    """Emit a bare-name dir-whip event (5.14); silent degradation.

    Bus absent (capability flag off, no ctx, or ctx.emit missing) or emit
    raising -> exactly ONE DEBUG log line per emission attempt, no error.
    The host forces the ``dir-whip:`` namespace, so only the bare
    name is passed (a namespaced name raises ValueError, fail-closed).
    """
    try:
        if not _emit_enabled:
            logger.debug(
                "dir-whip: event bus unavailable, skipping emit(%s)",
                event_name,
            )
            return
        ctx = _get_ctx()
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


# ---------------------------------------------------------------- Observation hooks (spec 5.4/5.13/5.15/5.16)

def _is_child_session(session_id):
    """True when session_id is a live child (subagent) session (5.4)."""
    with _child_session_ids_lock:
        return session_id in _child_session_ids


def _resolved_config():
    """Cached (working_dir_root, exempt_paths); (None, []) on failure."""
    try:
        return get_cached_config(_get_ctx())
    except Exception:
        return (None, [])


def _allow_path_handler(args, **kwargs):
    """Registered allow_path handler: config tool + allowlisted event (5.14)."""
    try:
        path = args.get("path") if isinstance(args, dict) else args
        result = dir_whip_allow_path(args, **kwargs)
        if path:
            working_dir_root, _ = _resolved_config()
            _bus_emit("allowlisted", {
                "outcome": "allowlisted",
                "rule_key": "runtime-allowlist",
                "target": _relativize_target(path, working_dir_root),
            })
        return result
    except Exception as exc:
        logger.debug("dir-whip: allow_path handler error (fail-open): %s", exc)
        return None


def on_start(session_id, model=None, platform=None, **kwargs):
    """on_session_start hook (5.4): top-level sessions only.

    Top-level: clear the runtime allowlist, reset the fail-open warning
    flag, inject the discipline reminder. Child sessions (session_id in
    child_session_ids) SKIP all three. Gateway degrade: inject_message
    unavailable or falsy -> DEBUG log, no crash.
    """
    try:
        if _is_child_session(session_id):
            return
        # 5.18: top-level session start clears the audit state (pending
        # violations, leftover pre snapshots, cap warning); child sessions
        # skip and inherit the parent's latched state.
        _audit_session_start(session_id)
        runtime_allowlist_clear()
        _reset_fail_open_flag()
        ctx = _get_ctx()
        profile = getattr(ctx, "profile_name", None) if ctx else None
        # SCR-027: session-scoped resolution — re-resolve working_dir_root
        # from THIS session's profile (child sessions skip and inherit).
        set_session_profile(profile)
        refresh_resolution(ctx)
        stats_set_session(
            profile=profile,
            session_id=session_id,
            is_subagent=False,
            started_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
        if ctx and hasattr(ctx, "inject_message"):
            injected = ctx.inject_message(REMINDER_MESSAGE)
            if not injected:
                logger.debug(
                    "dir-whip: session-start reminder skipped "
                    "(inject_message unavailable)"
                )
        else:
            logger.debug(
                "dir-whip: session-start reminder skipped "
                "(inject_message unavailable)"
            )
    except Exception as exc:
        logger.debug("dir-whip: session start hook error: %s", exc)


def on_post_tool_call(tool_name=None, args=None, result=None, task_id=None,
                      session_id=None, status=None, **kwargs):
    """post_tool_call observer (5.13 D2): write-class completion.

    Records the completion + result state of write_file / patch / terminal
    calls with rule_key ``landed:<tool>``; other tools are ignored.
    """
    try:
        if tool_name not in ("write_file", "patch", "terminal"):
            return
        working_dir_root, _ = _resolved_config()
        targets = _extract_target_paths(tool_name, args) if isinstance(args, dict) else []
        target = targets[0] if targets else None
        # 5.18: terminal re-scan -> diff -> violation classification. Runs
        # alongside (never instead of) the landed: observation below; a
        # blocked-at-pre call has no pre snapshot and skips here.
        if tool_name == "terminal":
            _audit_post_check(
                session_id, task_id, is_subagent=_is_child_session(session_id),
            )
        _emit_verdict(
            "allow", tool_name, "landed:" + str(tool_name), target,
            reason="write tool call completed (status: %s)" % (status or "ok"),
            working_dir_root=working_dir_root,
            is_subagent=_is_child_session(session_id),
            session_id=session_id,
        )
    except Exception as exc:
        logger.debug("dir-whip: post_tool_call hook error: %s", exc)


def _approval_granted(choice):
    """Map host approval choices to granted/denied (5.13 D2)."""
    return str(choice or "").strip().lower() in _APPROVAL_GRANTED_CHOICES


def on_post_approval_response(choice=None, session_key=None, surface=None,
                              command=None, pattern_key=None, **kwargs):
    """post_approval_response observer (5.13 D2) + approval events (5.14).

    Granted/denied mapped from the host choice vocabulary. approval-resolved
    is ALWAYS emitted with the outcome; approval-requested is emitted ONLY
    when the payload exposes a request/entry state (verified absent in the
    local hermes-agent payloads). Privacy: no command/description text.
    """
    try:
        granted = _approval_granted(choice)
        rule_key = "approval:granted" if granted else "approval:denied"
        _emit_verdict(
            "allow" if granted else "block", "approval", rule_key, None,
            reason="host approval %s" % ("granted" if granted else "denied"),
            working_dir_root=None, session_id=kwargs.get("session_id"),
            bus_event=False,  # approval events are emitted below
        )
        _bus_emit("approval-resolved", {
            "outcome": "granted" if granted else "denied",
            "rule_key": rule_key,
        })
        if "request" in kwargs or "entry" in kwargs:
            _bus_emit("approval-requested", {
                "outcome": "requested",
                "rule_key": "approval-requested",
            })
    except Exception as exc:
        logger.debug("dir-whip: post_approval_response hook error: %s", exc)


def on_pre_command(surface=None, command=None, alias_used=None, args_raw=None,
                   session_key=None, platform=None, **kwargs):
    """pre_command observer (5.15): record only, never block.

    The host ignores the return value; always returns None. Records
    surface / command / alias_used plus args_raw / session_key / platform
    when present, rule_key ``pre-command:<command>``.
    """
    try:
        working_dir_root, _ = _resolved_config()
        detail = {"surface": surface, "alias_used": alias_used}
        if args_raw is not None:
            detail["args_raw"] = args_raw
        if session_key is not None:
            detail["session_key"] = session_key
        if platform is not None:
            detail["platform"] = platform
        _emit_verdict(
            "allow", "command", "pre-command:" + str(command or ""), None,
            reason=json.dumps(detail), working_dir_root=working_dir_root,
        )
    except Exception as exc:
        logger.debug("dir-whip: pre_command hook error: %s", exc)
    return None


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
            with _child_session_ids_lock:
                _child_session_ids.add(child_session_id)
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
        working_dir_root, _ = _resolved_config()
        _emit_verdict(
            "allow", "subagent", "subagent-start", None,
            reason=json.dumps(detail), working_dir_root=working_dir_root,
            is_subagent=True,
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
            with _child_session_ids_lock:
                _child_session_ids.discard(child_session_id)
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
        working_dir_root, _ = _resolved_config()
        _emit_verdict(
            "allow", "subagent", "subagent-stop", None,
            reason=json.dumps(detail), working_dir_root=working_dir_root,
            is_subagent=True,
        )
    except Exception as exc:
        logger.debug("dir-whip: subagent_stop hook error: %s", exc)


# ---------------------------------------------------------------- Target extraction (spec 5.3 step 3)

def _extract_target_paths(tool_name, args):
    """Extract target file path(s) from tool arguments (V4A patch format)."""
    if not isinstance(args, dict):
        return []

    if tool_name == "write_file":
        path = args.get("path")
        return [path] if path else []

    if tool_name == "patch":
        # mode=replace: single path; mode=patch: V4A "*** Update File:" lines.
        path = args.get("path")
        if path:
            return [path]
        patch_content = args.get("patch", "")
        if patch_content:
            return PATCH_FILE_RE.findall(patch_content)

    return []


def _session_cwd(task_id):
    """Session CWD for relative-target resolution (guarded; None when
    unavailable). Tests inject a fake via dir_whip._session_cwd_fn."""
    if callable(_session_cwd_fn):
        try:
            return _session_cwd_fn(task_id)
        except Exception as exc:
            logger.debug(
                "dir-whip: get_session_cwd(%r) failed: %s", task_id, exc
            )
    return None


_DRIVE_ROOTED_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute_any(target):
    """Rooted on the local OS, Windows-drive-rooted, or backslash-rooted.

    On POSIX, posixpath.isabs() returns False for Windows-style paths like
    ``E:/ws/x.txt`` or ``\\evil\\file.txt``; joining such a target onto the
    base would double-prefix it (``E:/ws/E:/ws/x.txt``). Rooted targets
    resolve as-is and the classifier then decides external vs in-workspace
    via the normalized root.
    """
    if os.path.isabs(target):
        return True
    if _DRIVE_ROOTED_RE.match(target):
        return True
    return target.startswith("\\") and not target.startswith("\\\\")


def _resolve_target(target, task_id, working_dir_root):
    """Resolve a target to absolute (spec 5.3 step 4).

    Relative targets resolve against the session CWD; when unrecorded
    (None) fall back to working_dir_root (conservative, DEBUG log). Never
    uses os.getcwd() (the plugin process CWD may differ).
    """
    if _is_absolute_any(target):
        return target

    base = _session_cwd(task_id)
    if not base:
        logger.debug(
            "dir-whip: session CWD unrecorded for task %r, resolving "
            "relative target against working_dir_root", task_id
        )
        base = working_dir_root
    return os.path.join(base, target)


# ---------------------------------------------------------------- Path normalization (SCR-006)

def _normalize_windows(path, working_dir_root):
    """Normalize a target path on Windows (MSYS mapping + drive inheritance).

    1. Map MSYS forward-slash forms to drive-qualified paths:
       /c/..., //c/... -> C:/<rest>; /cygdrive/c/... -> C:/<rest>.
       UNC paths (//server/share) do not match these regexes.
    2. os.path.normpath (separator and dot-segment normalization).
    3. Drive inheritance: rooted paths that still lack a drive get the
       drive of working_dir_root; skipped if working_dir_root has no drive.
    4. Fail-open: a path that STILL has no drive after inheritance is
       unclassifiable on Windows; log a warning and return it unchanged
       (never raise -- the caller classifies it as external and allows).
    """
    match = _MSYS_DRIVE_RE.match(path)
    if match:
        drive, rest = match.group(1), match.group(2)
        path = "%s:/%s" % (drive.upper(), rest or "")
    else:
        match = _CYGWIN_DRIVE_RE.match(path)
        if match:
            drive, rest = match.group(1), match.group(2)
            path = "%s:/%s" % (drive.upper(), rest or "")

    path = os.path.normpath(path)

    drive, _ = ntpath.splitdrive(path)
    if not drive and working_dir_root:
        root_drive, _ = ntpath.splitdrive(working_dir_root)
        if root_drive:
            path = root_drive + path

    drive, _ = ntpath.splitdrive(path)
    if not drive:
        logger.warning(
            "dir-whip: target %r unclassifiable after "
            "normalization (no drive); treating as external "
            "(fail-open)",
            path,
        )

    return path


def _normalize_posix(path):
    """Normalize a target path on POSIX hosts (normpath identity)."""
    return os.path.normpath(path)


def _looks_windowsy(path):
    """Windows-style target on ANY host (SCR-006 cross-platform).

    MSYS/Cygwin forms, drive-rooted paths, and single-backslash-rooted
    paths follow Windows normalization even on POSIX hosts (a WSL/Git-Bash
    session can carry Windows-style roots and targets).
    """
    return bool(
        _DRIVE_ROOTED_RE.match(path)
        or _MSYS_DRIVE_RE.match(path)
        or _CYGWIN_DRIVE_RE.match(path)
        or (path.startswith("\\") and not path.startswith("\\\\"))
    )


def normalize_target(path, working_dir_root):
    """Normalize a target path before classification (chain step 0)."""
    if os.name == "nt" or _looks_windowsy(path):
        return _normalize_windows(path, working_dir_root)
    return _normalize_posix(path)


# ---------------------------------------------------------------- Classification (spec 5.3 steps 6/7)

def _within_working_dir(target, working_dir_root):
    """Containment of target under working_dir_root (5.3 step 6).

    Windows-style (drive-rooted) pairs are compared case-insensitively on
    ANY host — Windows paths follow Windows matching rules even on POSIX
    (SCR-006; e.g. a WSL session carrying a Windows-style root). Native
    paths use os.path.relpath (case-sensitive on POSIX).
    """
    target_fwd = str(target).replace("\\", "/")
    root_fwd = str(working_dir_root).replace("\\", "/")
    if _DRIVE_ROOTED_RE.match(target_fwd) and _DRIVE_ROOTED_RE.match(root_fwd):
        target_cf = target_fwd.casefold()
        root_cf = root_fwd.casefold()
        if target_cf == root_cf:
            return True
        prefix = root_cf.rstrip("/") + "/"
        return target_cf.startswith(prefix)
    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        # Different drive on Windows: cannot relate -> external.
        return False
    return not rel.startswith("..")


def classify_target(target, working_dir_root, exempt_paths, is_subagent=False):
    """Classify a single normalized absolute target (spec 5.3 step 6).

    Returns a verdict dict:
      {"outcome": "allow", "rule_key": ...}                      -> allow
      {"outcome": "external-write", "rule_key": "external-write"} -> allow + log
      {"outcome": "block", "rule_key": ..., "message": ...}      -> block

    Order: Tier 0 (exempt_paths + runtime allowlist) first; under
    working_dir_root -> allowed_root_files at root, then valid Session
    Directory, then BLOCK; outside working_dir_root (incl. sibling profile
    dirs) -> external-write. There is NO approve tier.
    """
    if is_exempt(target, exempt_paths):
        return {"outcome": "allow", "rule_key": "tier0-exempt"}
    if is_runtime_allowlisted(target):
        return {"outcome": "allow", "rule_key": "runtime-allowlist"}

    if not _within_working_dir(target, working_dir_root):
        return {"outcome": "external-write", "rule_key": "external-write"}

    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        # Mixed drive/UNC pair on Windows: cannot relate -> external.
        return {"outcome": "external-write", "rule_key": "external-write"}
    rel_fwd = rel.replace("\\", "/")
    if "/" not in rel_fwd:
        # Root file: whitelist match (case-insensitive on Windows).
        base = os.path.basename(target)
        for allowed in _allowed_root_files():
            if base == allowed or (
                os.name == "nt" and base.casefold() == allowed.casefold()
            ):
                return {"outcome": "allow", "rule_key": "allowed-root-file"}

    if is_inside_session_dir(target, working_dir_root):
        return {"outcome": "allow", "rule_key": "session-dir"}

    rule_key = "root-file" if "/" not in rel_fwd else "non-session-dir"
    return {
        "outcome": "block",
        "rule_key": rule_key,
        "message": _block_message(target, working_dir_root, is_subagent),
    }


def _allowed_root_files():
    """Root-file whitelist from dir-whip-config.yaml (spec 5.6).

    Reads the SAME allowed_root_files key the audit reads, so guard and
    audit never disagree about root files. STRICT fallback: config missing
    / key absent -> empty list -> every root file blocks (fail-closed).
    """
    try:
        return load_guard_config().get("allowed_root_files") or []
    except Exception:
        return []


def _block_message(target, working_dir_root, is_subagent=False):
    """Exact block message (spec 5.3; C6-aligned).

    Subagent variant: the fix line is replaced by the parent-target
    guidance -- subagents never create session directories.
    """
    target_fwd = str(target).replace("\\", "/")
    wdr_fwd = str(working_dir_root).replace("\\", "/")
    if is_subagent:
        fix_line = "Fix: write to the target directory passed by the parent agent."
    else:
        # D11: scripts path computed at runtime: <plugin_dir>/skills/
        # workspace-organization/scripts (plugin_dir = directory of dir_whip.py).
        scripts_path = os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "skills", "workspace-organization", "scripts",
            )
        ).replace("\\", "/")
        fix_line = (
            "Fix: Create a session directory first:\n"
            "  python %s/create_session_dir.py <task_name> --workspace %s\n"
            "Then write to its Outputs/ or .tmp/ subdirectory."
            % (scripts_path, wdr_fwd)
        )
    return (
        "BLOCKED: File writes in the Working Directory require a Session "
        "Directory or an allowed root file.\n"
        "Target: %s\n"
        "%s\n"
        "If this is a project directory, add it to exempt_paths in "
        "HERMES_HOME/dir-whip/dir-whip-config.yaml\n"
        "Reply using the [Reason]/[Next] template." % (target_fwd, fix_line)
    )


def _terminal_base(args, task_id, working_dir_root):
    """Resolve the terminal relative-target base (spec 5.3 step 4).

    Chain: args["workdir"] -> get_session_cwd(task_id) -> working_dir_root.
    Never os.getcwd().
    """
    base = args.get("workdir") if isinstance(args, dict) else None
    if not base:
        base = _session_cwd(task_id)
    if not base:
        base = working_dir_root
    return base


def _resolve_terminal_target(target, base):
    """Resolve a terminal write target against the relative-target base."""
    if _is_absolute_any(target):
        return target
    return os.path.join(base, target)


def _guard_terminal(args, task_id, working_dir_root, exempt_paths,
                    is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers).

    - terminal_guard disabled -> terminal never blocked (DEBUG log).
    - Heredoc (`<<`) blanket demotion (4.4): the WHOLE command is judged
      uncertain (allow + log), no body parsing, no block extraction.
    - Block tier: redirect / touch / cp-mv targets classify through the
      shared chain, per command segment (4.1); a target that is a device
      path (4.3) is exempt BEFORE normalization and emits nothing.
    - Uncertain tier: nested shells, python/node/sed/tee/curl/wget/dd,
      dynamic paths, `=`-residue tokens -> ALLOW + LOG (rule_key
      terminal-write-uncertain), NO approval gate.
    - Read-only / unparseable -> allow (no verdict event).
    - Any exception -> None (fail-open).
    """
    try:
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str) or not command:
            return None
        if not terminal_guard_enabled():
            logger.debug(
                "dir-whip: terminal guard disabled via terminal_guard config"
            )
            return None

        tokens = _tokenize_command(command)
        if not tokens:
            return None
        base = _terminal_base(args, task_id, working_dir_root)

        # 4.4 heredoc blanket demotion: never parse the body, never block.
        if "<<" in command:
            _emit_verdict(
                "allow", "terminal", "terminal-write-uncertain", None,
                reason="heredoc detected, blanket demotion",
                working_dir_root=working_dir_root,
                is_subagent=is_subagent, session_id=session_id,
            )
            return None

        for target, rule_key in _terminal_block_targets(tokens):
            # 4.3 device paths are exempt BEFORE normalization: no
            # verdict/stats event, no drive-inherited path fabrication.
            if target in _DEVICE_PATHS:
                continue
            abs_target = _resolve_terminal_target(target, base)
            normalized = normalize_target(abs_target, working_dir_root)
            verdict = classify_target(normalized, working_dir_root, exempt_paths, is_subagent)
            if verdict["outcome"] == "block":
                _emit_verdict(
                    "block", "terminal", rule_key, normalized,
                    reason="terminal write target blocked",
                    working_dir_root=working_dir_root,
                    is_subagent=is_subagent, session_id=session_id,
                )
                return {"action": "block", "message": verdict["message"]}
            _emit_verdict(
                verdict["outcome"], "terminal", rule_key, normalized,
                reason=_verdict_reason(verdict["outcome"]),
                working_dir_root=working_dir_root,
                is_subagent=is_subagent, session_id=session_id,
            )

        if _terminal_uncertain(tokens):
            _emit_verdict(
                "allow", "terminal", "terminal-write-uncertain", None,
                reason="write intent detected, target uncertain",
                working_dir_root=working_dir_root,
                is_subagent=is_subagent, session_id=session_id,
            )
            return None
        return None
    except Exception as exc:
        logger.debug("dir-whip: terminal guard error (fail-open): %s", exc)
        return None



# ---------------------------------------------------------------- Root write audit (spec 5.18)

# The detection backbone: snapshot/diff/classify kernels, session-scoped
# pending-violation state (the L3 latch input for later lanes) and the
# pre/post hook wiring. Ladder steps (L1 notice, L2 bus sidecar, L3 gate
# block) are later lanes; everything here is fail-open (5.8).


def snapshot(root):
    """Snap the top-level entries of root (spec 5.18 mechanism).

    Recorded per entry: (st_size, st_mtime_ns, is_dir) -- exactly the
    fidelity the diff needs. Scan OSError -> None (fail-open; callers
    silently skip the audit round). Never raises.
    """
    try:
        entries = {}
        with os.scandir(root) as it:
            for entry in it:
                st = entry.stat()
                entries[entry.name] = (st.st_size, st.st_mtime_ns, entry.is_dir())
        return entries
    except OSError:
        return None


def diff_snapshots(before, after):
    """Four-state diff between two snapshots (spec 5.18).

    Returns {"added", "modified", "deleted", "unrelated"} name lists
    (name-sorted): new entries, same-name entries whose (size, mtime_ns,
    is_dir) changed, vanished entries, and unchanged entries. Pure --
    no filesystem access.
    """
    before = before or {}
    after = after or {}
    before_keys = set(before)
    after_keys = set(after)
    common = before_keys & after_keys
    return {
        "added": sorted(after_keys - before_keys),
        "modified": sorted(
            name for name in common if before[name] != after[name]
        ),
        "deleted": sorted(before_keys - after_keys),
        "unrelated": sorted(
            name for name in common if before[name] == after[name]
        ),
    }


def audit_classify_diff(diff, before, after, working_dir_root, exempt_paths,
                        is_subagent=False):
    """Classify a snapshot diff into violations (spec 5.18).

    Only FILE entries are judged (is_dir -> never a violation; directory
    mtimes -- session dirs, `.git/`, `.hermes/` -- are ignored). A
    violation is a NEW or MODIFIED root-level file that classifies as a
    root-file block through the shared chain: not on the root allowlist,
    not in exempt_paths, not inside any session directory (the same
    allowed_root_files / exempt_paths keys the guard reads, so the layers
    never disagree). Deletions are RECORD-ONLY (5.8 delete principle) --
    surfaced in "recorded", never judged.

    Returns {"violations": [abs paths], "recorded": [deleted abs paths]}.
    """
    violations = []
    recorded = []
    for name in list(diff.get("added", [])) + list(diff.get("modified", [])):
        info = (after or {}).get(name)
        if info is None or info[2]:
            continue  # directory entries never violate (5.18)
        abs_path = os.path.join(working_dir_root, name)
        verdict = classify_target(
            abs_path, working_dir_root, exempt_paths, is_subagent
        )
        if verdict["outcome"] == "block" and verdict["rule_key"] == "root-file":
            violations.append(abs_path)
    for name in diff.get("deleted", []):
        info = (before or {}).get(name)
        if info is None or info[2]:
            continue
        recorded.append(os.path.join(working_dir_root, name))
    return {"violations": sorted(violations), "recorded": sorted(recorded)}


def _audit_norm_path(path):
    """Deterministic pending-set key: absolute + native-normalized."""
    return os.path.normpath(str(path))


def _audit_now():
    """ISO-8601 timestamp (seconds precision) for first_seen."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _audit_owner_session(session_id):
    """Resolve the pending-set owner for a session (5.18 session scoping).

    Child sessions (child_session_ids gate, 5.4) write into the PARENT's
    pending set: the explicit parent link recorded by subagent_start wins;
    otherwise the most recent top-level session (the parent in the common
    sequential layout). Returns None when unknown -- callers fall back to
    the session id itself.
    """
    if session_id and _is_child_session(session_id):
        with _audit_pending_lock:
            return _audit_session_parents.get(session_id) or _audit_top_session
    return session_id


def audit_pending_snapshot(session_id=None):
    """Read-only copy of a session's pending violations (Lane 2b gate).

    The L3 gate reads this set; keys are absolute normpath'd paths, each
    value is {"first_seen": ISO-8601, "announced": bool}. "announced" is
    flipped by L1 (audit_mark_announced) so the fire-once notice never
    repeats; first_seen is preserved across re-detections. Child sessions
    resolve into the parent's set.
    """
    owner = _audit_owner_session(session_id) or session_id
    with _audit_pending_lock:
        return {
            path: dict(entry)
            for path, entry in _audit_pending.get(owner, {}).items()
        }


def audit_pending_add(session_id, path, first_seen=None):
    """Add one pending violation (detection fills this structure).

    Existing entries are kept untouched on re-detection (first_seen and
    announced survive, so L1 fire-once semantics hold across rounds).
    """
    owner = _audit_owner_session(session_id) or session_id
    key = _audit_norm_path(path)
    with _audit_pending_lock:
        bucket = _audit_pending.setdefault(owner, {})
        if key in bucket:
            return
        bucket[key] = {
            "first_seen": first_seen or _audit_now(),
            "announced": False,
        }


def audit_pending_clear(session_id):
    """Clear a session's pending violations (top-level session start)."""
    with _audit_pending_lock:
        _audit_pending.pop(session_id, None)


def audit_mark_announced(session_id, path):
    """Flip the fire-once announced flag (L1 notice lane calls this)."""
    owner = _audit_owner_session(session_id) or session_id
    key = _audit_norm_path(path)
    with _audit_pending_lock:
        entry = _audit_pending.get(owner, {}).get(key)
        if entry:
            entry["announced"] = True


def audit_unresolved_paths(session_id, working_dir_root=None, exempt_paths=None):
    """Settlement judgment for the L3 gate (Lane 2b input): re-scan the
    root and return the pending paths that STILL violate (file present and
    still classifying as an unprotected root-level file). A pending path
    is settled when it is gone, moved outside the root, or legalized
    (allowlist / exempt / session dir). Fail-open: a failed re-scan keeps
    the full pending set (the gate stays latched).
    """
    try:
        pending = audit_pending_snapshot(session_id)
        if not pending:
            return []
        if working_dir_root is None:
            working_dir_root, exempt_paths = _resolved_config()
        if working_dir_root is None:
            return sorted(pending)
        after = snapshot(working_dir_root)
        if after is None:
            return sorted(pending)
        unresolved = []
        for path in pending:
            if not os.path.lexists(path):
                continue  # gone -> settled
            if not _within_working_dir(path, working_dir_root):
                continue  # moved outside the root -> settled
            verdict = classify_target(
                path, working_dir_root, exempt_paths or [], is_subagent=False
            )
            if verdict["outcome"] == "block" and verdict["rule_key"] == "root-file":
                unresolved.append(path)
        return sorted(unresolved)
    except Exception as exc:
        logger.debug("dir-whip: audit settlement check error (fail-open): %s", exc)
        return sorted(audit_pending_snapshot(session_id))


def _audit_register_child(child_session_id, parent_session_id):
    """Record a child session's parent link (pending-set inheritance)."""
    try:
        with _audit_pending_lock:
            _audit_session_parents[child_session_id] = (
                parent_session_id or _audit_top_session
            )
    except Exception as exc:
        logger.debug("dir-whip: audit register child error: %s", exc)


def _audit_unregister_child(child_session_id):
    """Drop a child session's parent link when the subagent stops."""
    try:
        with _audit_pending_lock:
            _audit_session_parents.pop(child_session_id, None)
    except Exception as exc:
        logger.debug("dir-whip: audit unregister child error: %s", exc)


def _audit_session_start(session_id):
    """Top-level session start: clear this session's pending violations
    and leftover pre snapshots, reset the one-time cap warning, and record
    the current top-level session (child-inheritance fallback)."""
    try:
        audit_pending_clear(session_id)
        with _audit_pre_snapshots_lock:
            stale = [k for k in _audit_pre_snapshots if k[0] == session_id]
            for k in stale:
                _audit_pre_snapshots.pop(k, None)
        global _audit_cap_warned, _audit_top_session
        _audit_cap_warned = False
        _audit_top_session = session_id
    except Exception as exc:
        logger.debug("dir-whip: audit session start error: %s", exc)


# ---------------------------------------------------------------- L1 fire-once notice (spec 5.18)

def _audit_notice_message(paths):
    """The single L1 notice text (5.18): the paths and the remediation.
    One notice per result listing every unannounced violation; only this
    notice ever enters the conversation (context hygiene)."""
    lines = [
        "[dir-whip] Write audit: the following file(s) were written to the "
        "Working Directory root outside any Session Directory:"
    ]
    for path in paths:
        lines.append("  - %s" % str(path).replace("\\", "/"))
    lines.append(
        "Remediation: move the file(s) into a Session Directory "
        "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/) or add them to "
        "allowed_root_files in dir-whip-config.yaml. Further writes to "
        "the Working Directory are blocked until then."
    )
    return "\n".join(lines)


def on_transform_tool_result(tool_name=None, args=None, result=None,
                             session_id=None, task_id=None, **kwargs):
    """L1 fire-once notice hook (5.18), registered at register().

    Hermes first-party precedent (security-guidance): returning a string
    REPLACES the tool result the model sees next turn; None leaves it
    unchanged. The audit is terminal-triggered, so only TERMINAL results
    are decorated. Appends ONE notice naming every unannounced pending
    violation, then flips the announced flags (HARD fire-once constraint:
    one notice per violation, never re-appended -- context hygiene).
    Non-string results are untouched; JSON error results are not
    decorated; audit disabled or nothing unannounced -> None. Fail-open:
    any exception -> None, never raised.

    ORDERING FIX (live-verified 2026-08-22): for the terminal tool Hermes
    fires transform_tool_result BEFORE post_tool_call, so the audit re-scan
    (_audit_post_check) is run HERE first -- it pops the pre snapshot and
    fills the pending set, then the notice below reads it. The
    post_tool_call _audit_post_check call stays as an order-agnostic no-op
    fallback (the snapshot is already popped, so it skips). Because
    _audit_post_check pops its snapshot, the audit runs exactly once
    regardless of which hook fires first.
    """
    try:
        if tool_name != "terminal":
            return None
        if not write_audit_enabled():
            return None
        # Ordering fix: run the audit re-scan BEFORE reading the pending set
        # (transform fires before post_tool_call for terminal). Safe even if
        # the command was blocked-at-pre (no snapshot -> early return).
        _audit_post_check(
            session_id, task_id, is_subagent=_is_child_session(session_id),
        )
        if not isinstance(result, str):
            return None
        # Don't decorate error results (security-guidance precedent): the
        # model already has bigger problems; the notice waits for the
        # next eligible result instead.
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
                return None
        except (ValueError, TypeError):
            pass
        pending = audit_pending_snapshot(session_id)
        unannounced = [p for p, entry in pending.items() if not entry["announced"]]
        if not unannounced:
            return None
        for path in unannounced:
            audit_mark_announced(session_id, path)
        return result + "\n\n" + _audit_notice_message(unannounced)
    except Exception as exc:
        logger.debug("dir-whip: transform_tool_result error (fail-open): %s", exc)
        return None


# ---------------------------------------------------------------- L3 settlement gate (spec 5.18)

def _audit_gate_unresolved(session_id, working_dir_root, exempt_paths):
    """Unresolved pending paths for the L3 gate (empty -> gate open).

    Respects the write_audit switch (disabled -> open). A failed root
    re-scan is handled inside audit_unresolved_paths (full pending set ->
    latch stays); any other gate-side error fails OPEN (5.8 -- the gate
    never breaks the guard).
    """
    try:
        if not write_audit_enabled():
            return []
        return audit_unresolved_paths(session_id, working_dir_root, exempt_paths)
    except Exception as exc:
        logger.debug("dir-whip: audit gate check error (fail-open): %s", exc)
        return []


def _audit_gate_block_message(display_paths, is_subagent):
    """L3 gate block message (5.18): unresolved paths + remediation, with
    the C6 [Reason]/[Next] cue (subagent variant: report to the parent)."""
    lines = [
        "BLOCKED: earlier command(s) wrote file(s) to the Working Directory "
        "root that still need remediation:"
    ]
    for path in display_paths:
        lines.append("  - %s" % path)
    if is_subagent:
        lines.append("Fix: report the pending path(s) to the parent agent "
                     "for remediation (do not create a session directory).")
    else:
        lines.append(
            "Fix: move the file(s) into a Session Directory "
            "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/) or add them to "
            "allowed_root_files in dir-whip-config.yaml."
        )
    lines.append("Reply using the [Reason]/[Next] template.")
    return "\n".join(lines)


def _audit_gate_block(tool_name, session_id, is_subagent, working_dir_root,
                      unresolved):
    """Standard block-channel response for the L3 latch (5.18).

    Records a write-audit-gate-block verdict (5.13 stats/log; no generic
    blocked bus event -- the gate has its own) and emits the 5.14
    write-audit-gate-block bus event with privacy-shaped relative paths,
    then returns the block dict for the pre-tool channel.
    """
    rel_paths = [_relativize_target(path, working_dir_root) for path in unresolved]
    _emit_verdict(
        "block", tool_name, "write-audit-gate-block", None,
        reason="%d unresolved root write audit violation(s)" % len(unresolved),
        working_dir_root=working_dir_root,
        is_subagent=is_subagent, session_id=session_id, bus_event=False,
    )
    _bus_emit("write-audit-gate-block", {
        "outcome": "block",
        "rule_key": "write-audit-gate-block",
        "paths": list(rel_paths),
        "latch": "latched",
    })
    display = [str(path).replace("\\", "/") for path in unresolved]
    return {
        "action": "block",
        "message": _audit_gate_block_message(display, is_subagent),
    }


def _audit_pre_snapshot(session_id, task_id, working_dir_root, exempt_paths):
    """Take the pre snapshot for an ALLOWED terminal call (5.18).

    Audit disabled (write_audit: false) -> nothing. Root entry count
    above write_audit_entry_cap -> round skipped + ONE WARNING per session
    (not repeated). Scan OSError -> fail-open (no snapshot stored, so the
    post skips). Any exception -> nothing (fail-open, 5.8).
    """
    try:
        if not write_audit_enabled():
            return
        snap = snapshot(working_dir_root)
        if snap is None:
            return
        cap = write_audit_entry_cap()
        if len(snap) > cap:
            global _audit_cap_warned
            if not _audit_cap_warned:
                _audit_cap_warned = True
                logger.warning(
                    "dir-whip: write audit skipped: root entry count %d "
                    "exceeds write_audit_entry_cap %d", len(snap), cap,
                )
            return
        with _audit_pre_snapshots_lock:
            _audit_pre_snapshots[(session_id, task_id)] = (
                snap, working_dir_root, tuple(exempt_paths),
            )
    except Exception as exc:
        logger.debug("dir-whip: audit pre-snapshot error (fail-open): %s", exc)


def _audit_post_check(session_id, task_id, is_subagent=False):
    """Post terminal re-scan: diff the pre snapshot and classify (5.18).

    Pops the (session_id, task_id) pairing; no pairing (blocked-at-pre,
    cap skip, disabled, scan failure) -> nothing. Each violation joins the
    session's pending set and emits ONE write-audit-violation verdict
    event (tool="audit", relative target, 5.13 privacy; bus_event=False)
    plus the 5.14 write-audit-violation bus sidecar with a relative path,
    the session-scope flag and first_seen. Deletions are record-only,
    never events. The L1 notice is NOT an event (5.18). Fail-open: never
    raises.
    """
    try:
        with _audit_pre_snapshots_lock:
            record = _audit_pre_snapshots.pop((session_id, task_id), None)
        if record is None:
            return
        before, working_dir_root, exempt_paths = record
        if not write_audit_enabled():
            return
        after = snapshot(working_dir_root)
        if after is None:
            return
        diff = diff_snapshots(before, after)
        classified = audit_classify_diff(
            diff, before, after, working_dir_root, list(exempt_paths), is_subagent,
        )
        for path in classified["violations"]:
            audit_pending_add(session_id, path)
            _emit_verdict(
                "block", "audit", "write-audit-violation", path,
                reason="root write audit violation (5.18)",
                working_dir_root=working_dir_root,
                is_subagent=is_subagent, session_id=session_id,
                bus_event=False,
            )
            _bus_emit("write-audit-violation", {
                "outcome": "block",
                "rule_key": "write-audit-violation",
                "path": _relativize_target(path, working_dir_root),
                "is_subagent": bool(is_subagent),
                "first_seen": (
                    audit_pending_snapshot(session_id)
                    .get(_audit_norm_path(path), {})
                    .get("first_seen")
                ),
            })
    except Exception as exc:
        logger.debug("dir-whip: audit post check error (fail-open): %s", exc)