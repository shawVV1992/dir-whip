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
from pathlib import Path

try:
    from . import state
except ImportError:
    import state

try:
    from . import audit
except ImportError:
    import audit

try:
    from .config import (
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        refresh_resolution,
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
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        refresh_resolution,
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
    from .audit import (
        _audit_gate_block,
        _audit_gate_unresolved,
        _audit_post_check,
        _audit_pre_snapshot,
        audit_pending_clear,
        on_transform_tool_result,
    )
except ImportError:
    from audit import (
        _audit_gate_block,
        _audit_gate_unresolved,
        _audit_post_check,
        _audit_pre_snapshot,
        audit_pending_clear,
        on_transform_tool_result,
    )

try:
    from .sessions import (
        _is_child_session,
        _record_top_session,
        on_subagent_start,
        on_subagent_stop,
    )
except ImportError:
    from sessions import (
        _is_child_session,
        _record_top_session,
        on_subagent_start,
        on_subagent_stop,
    )

try:
    from .events import _bus_emit, _verdict_reason, emit
except ImportError:
    from events import _bus_emit, _verdict_reason, emit

try:
    from .report import register_dir_whip_commands
except ImportError:
    from report import register_dir_whip_commands

try:
    from .paths import (
        is_absolute_any,
        normalize_target,
        relativize_target,
        within_working_dir,
    )
except ImportError:
    from paths import (
        is_absolute_any,
        normalize_target,
        relativize_target,
        within_working_dir,
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
# fake via state.session.session_cwd_fn.
try:
    from hermes_cli.tools.terminal_tool import get_session_cwd
except Exception:
    get_session_cwd = None

# Host API injection slot (ADR-0007): filled at module load from the guarded
# import; tests inject a fake via state.session.session_cwd_fn.
state.session.session_cwd_fn = get_session_cwd

logger = logging.getLogger("dir-whip")

INTERCEPTED_TOOLS = ("write_file", "patch", "terminal")
PATCH_FILE_RE = re.compile(r"^\*\*\* Update File:\s*(.+)$", re.MULTILINE)

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

# Spec 5.12 (term-updated): injected once per session when the guard is
# disabled because working_dir_root could not be resolved.
FAIL_OPEN_WARNING_MESSAGE = (
    "[dir-whip] WARNING: The guard is DISABLED because the Working "
    "Directory\n"
    "could not be resolved. File writes are NOT being enforced.\n"
    "Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml\n"
    "(terminal.cwd) and restart the session."
)

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
# (State lives in state.audit; see state.py.)
# ================================================================


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
        # Assembly-layer injection (ADR-0007): wire the audit classifier
        # BEFORE any hook can fire (31.13 moves this to __init__.py with
        # verdict.classify_target).
        audit.set_classifier(classify_target)
        try:
            state.session.emit_enabled = bool(getattr(ctx, "emit", None))
        except Exception:
            state.session.emit_enabled = False
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
            emit(
                "block", tool_name, verdict["rule_key"], normalized,
                "write blocked by guard rule", session_id, is_subagent,
            )
            return {"action": "block", "message": verdict["message"]}
        emit(
            verdict["outcome"], tool_name, verdict["rule_key"], normalized,
            _verdict_reason(verdict["outcome"]), session_id, is_subagent,
        )
    return None


def _get_ctx():
    """Return the registered ctx (tests set state.session.registered_ctx)."""
    return state.session.registered_ctx


# ---------------------------------------------------------------- Fail-open warning (spec 5.12)

def _warn_fail_open_once(ctx, tool_name, session_id, is_subagent):
    """Inject the one-time fail-open warning + record a fail-open verdict.

    Fires at most once per session (module flag; reset by
    _reset_fail_open_flag). Gateway degrade: inject_message unavailable or
    falsy -> the WARNING log line is the delivery. Never raises.
    """
    if not state.session.fail_open_warned:
        state.session.fail_open_warned = True
        try:
            if ctx and hasattr(ctx, "inject_message"):
                ctx.inject_message(FAIL_OPEN_WARNING_MESSAGE)
        except Exception:
            pass
    emit(
        "fail-open", tool_name, "fail-open", None,
        "working_dir_root unresolved", session_id, is_subagent,
    )


def _reset_fail_open_flag():
    """Reset the one-time fail-open warning flag (26.7's on_session_start
    calls this; tests use it too)."""
    state.session.fail_open_warned = False


# ---------------------------------------------------------------- Observation hooks (spec 5.4/5.13/5.15/5.16)

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
                "target": relativize_target(path, working_dir_root),
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
        emit(
            "allow", tool_name, "landed:" + str(tool_name), target,
            "write tool call completed (status: %s)" % (status or "ok"),
            session_id, _is_child_session(session_id),
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
        emit(
            "allow" if granted else "block", "approval", rule_key, None,
            "host approval %s" % ("granted" if granted else "denied"),
            kwargs.get("session_id"), False,
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
        emit(
            "allow", "command", "pre-command:" + str(command or ""), None,
            json.dumps(detail), None, False,
        )
    except Exception as exc:
        logger.debug("dir-whip: pre_command hook error: %s", exc)
    return None


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
    unavailable). Tests inject a fake via state.session.session_cwd_fn."""
    if callable(state.session.session_cwd_fn):
        try:
            return state.session.session_cwd_fn(task_id)
        except Exception as exc:
            logger.debug(
                "dir-whip: get_session_cwd(%r) failed: %s", task_id, exc
            )
    return None


def _resolve_target(target, task_id, working_dir_root):
    """Resolve a target to absolute (spec 5.3 step 4).

    Relative targets resolve against the session CWD; when unrecorded
    (None) fall back to working_dir_root (conservative, DEBUG log). Never
    uses os.getcwd() (the plugin process CWD may differ).
    """
    if is_absolute_any(target):
        return target

    base = _session_cwd(task_id)
    if not base:
        logger.debug(
            "dir-whip: session CWD unrecorded for task %r, resolving "
            "relative target against working_dir_root", task_id
        )
        base = working_dir_root
    return os.path.join(base, target)


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

    if not within_working_dir(target, working_dir_root):
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
    if is_absolute_any(target):
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
            emit(
                "allow", "terminal", "terminal-write-uncertain", None,
                "heredoc detected, blanket demotion", session_id, is_subagent,
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
                emit(
                    "block", "terminal", rule_key, normalized,
                    "terminal write target blocked", session_id, is_subagent,
                )
                return {"action": "block", "message": verdict["message"]}
            emit(
                verdict["outcome"], "terminal", rule_key, normalized,
                _verdict_reason(verdict["outcome"]), session_id, is_subagent,
            )

        if _terminal_uncertain(tokens):
            emit(
                "allow", "terminal", "terminal-write-uncertain", None,
                "write intent detected, target uncertain", session_id, is_subagent,
            )
            return None
        return None
    except Exception as exc:
        logger.debug("dir-whip: terminal guard error (fail-open): %s", exc)
        return None





def _audit_session_start(session_id):
    """Top-level session start: clear this session's pending violations
    and leftover pre snapshots, reset the one-time cap warning, and record
    the current top-level session (child-inheritance fallback)."""
    try:
        audit_pending_clear(session_id)
        with state.audit.lock:
            stale = [k for k in state.audit.pre_snapshots if k[0] == session_id]
            for k in stale:
                state.audit.pre_snapshots.pop(k, None)
        state.audit.cap_warned = False
        _record_top_session(session_id)
    except Exception as exc:
        logger.debug("dir-whip: audit session start error: %s", exc)
