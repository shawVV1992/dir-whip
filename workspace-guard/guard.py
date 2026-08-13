"""Core guard logic: pre-tool-call interception and decision chain (v0.2.0).

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

try:
    from .config import (
        _relativize_target,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        register_workspace_guard_commands,
        reset_cache,
        runtime_allowlist_clear,
        stats_record,
        stats_set_session,
        terminal_guard_enabled,
        workspace_guard_allow_path,
    )
except ImportError:
    from config import (
        _relativize_target,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        register_workspace_guard_commands,
        reset_cache,
        runtime_allowlist_clear,
        stats_record,
        stats_set_session,
        terminal_guard_enabled,
        workspace_guard_allow_path,
    )

# get_session_cwd is a Hermes runtime API (tools/terminal_tool.py) absent
# from the test venv. Guarded module-level import so guard.py never crashes
# when unavailable; callers fall back to working_dir_root. Tests inject a
# fake via _session_cwd_fn.
try:
    from hermes_cli.tools.terminal_tool import get_session_cwd
except Exception:
    get_session_cwd = None

logger = logging.getLogger("workspace-guard")

INTERCEPTED_TOOLS = ("write_file", "patch", "terminal")
PATCH_FILE_RE = re.compile(r"^\*\*\* Update File:\s*(.+)$", re.MULTILINE)

# MSYS-style forward-slash drive forms (SCR-006, task 9.9).
# Matches /c/..., //c/... (single drive letter) but NOT UNC \\server\share.
_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")

# Spec 5.12 (term-updated): injected once per session when the guard is
# disabled because working_dir_root could not be resolved.
FAIL_OPEN_WARNING_MESSAGE = (
    "[workspace-guard] WARNING: The guard is DISABLED because the Working "
    "Directory\n"
    "could not be resolved. File writes are NOT being enforced.\n"
    "Check guard-config.yaml (working_dir_root) or your profile's config.yaml\n"
    "(terminal.cwd) and restart the session."
)

# Spec 5.11: the plugin's ONLY tool (OpenAI function-call format required by
# Hermes tools.registry). Registered at register() via ctx.register_tool.
ALLOW_PATH_TOOL_SCHEMA = {
    "name": "workspace_guard_allow_path",
    "description": (
        "Add an absolute path to the workspace-guard runtime allowlist so "
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
    "[workspace-guard] Active. File writes in the Working Directory must be "
    "inside a Session Directory (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). "
    "Use create_session_dir.py to create one before writing files."
)

# Spec 5.13 D2: host approval choices that count as granted (verified
# against the local hermes-agent approval.py choice vocabulary).
_APPROVAL_GRANTED_CHOICES = frozenset(
    ("approve", "always", "session", "granted", "allow", "smart_approve")
)

# Terminal coarse tiers (spec 5.10). Redirect operators are emitted by
# _tokenize_command as standalone tokens; block-tier targets are exact
# membership + next plain token. Everything else with write intent is
# ALLOW + LOG (terminal-write-uncertain), never approved or blocked.
_REDIRECT_TOKENS = frozenset((">", ">>", "1>", "2>", "1>>", "2>>", "&>"))
_OPERATOR_TOKENS = frozenset(("|", "&")) | _REDIRECT_TOKENS
_NESTED_SHELLS = frozenset(("bash", "sh", "powershell", "pwsh"))
_UNCERTAIN_COMMANDS = frozenset(
    ("python", "python3", "py", "node", "sed", "tee", "curl", "wget", "dd")
)
_NON_LITERAL_RE = re.compile(r"[$`]")


def register(ctx):
    """Register workspace-guard hooks, tool and event bus (5.7/5.8/5.14).

    Hooks: pre_tool_call, on_session_start, post_tool_call,
    post_approval_response, pre_command, subagent_start, subagent_stop.
    Tool: workspace_guard_allow_path (the plugin's ONLY tool). Event bus:
    capability detected via hasattr(ctx, "emit"); absent -> silent
    degradation. Fail-open: any registration error logs a warning; the
    plugin is disabled but Hermes continues normally.
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
        if hasattr(ctx, "register_tool"):
            try:
                ctx.register_tool(
                    "workspace_guard_allow_path",
                    toolset="workspace-guard",
                    schema=ALLOW_PATH_TOOL_SCHEMA,
                    handler=_allow_path_handler,
                )
            except Exception as exc:
                logger.warning("workspace-guard: register_tool failed: %s", exc)
        # Spec 5.7 commands (status | stats | doctor) live in config.py (D3).
        register_workspace_guard_commands(ctx)
        logger.debug("workspace-guard: registered successfully")
    except Exception as exc:
        logger.warning("workspace-guard: registration failed: %s", exc)


def _guard_hook(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call hook (5.8: never raises; fail-open -> None)."""
    try:
        return guard(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("workspace-guard: guard hook error (fail-open): %s", exc)
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

    if tool_name == "terminal":
        return _guard_terminal(
            args, task_id, working_dir_root, exempt_paths, is_subagent, session_id
        )

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
    """Return the registered ctx (tests set guard._registered_ctx)."""
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
            logger.warning("workspace-guard: verdict %s", line)
        elif outcome == "external-write":
            logger.info("workspace-guard: verdict %s", line)
        else:
            logger.debug("workspace-guard: verdict %s", line)
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
        logger.debug("workspace-guard: verdict emission failed (fail-open): %s", exc)


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
    """Emit a bare-name workspace-guard event (5.14); silent degradation.

    Bus absent (capability flag off, no ctx, or ctx.emit missing) or emit
    raising -> exactly ONE DEBUG log line per emission attempt, no error.
    The host forces the ``workspace-guard:`` namespace, so only the bare
    name is passed (a namespaced name raises ValueError, fail-closed).
    """
    try:
        if not _emit_enabled:
            logger.debug(
                "workspace-guard: event bus unavailable, skipping emit(%s)",
                event_name,
            )
            return
        ctx = _get_ctx()
        if not ctx or not callable(getattr(ctx, "emit", None)):
            logger.debug(
                "workspace-guard: event bus unavailable, skipping emit(%s)",
                event_name,
            )
            return
        ctx.emit(event_name, payload or {})
    except Exception as exc:
        logger.debug(
            "workspace-guard: event emit failed for %s (fail-open): %s",
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
        result = workspace_guard_allow_path(args, **kwargs)
        if path:
            working_dir_root, _ = _resolved_config()
            _bus_emit("allowlisted", {
                "outcome": "allowlisted",
                "rule_key": "runtime-allowlist",
                "target": _relativize_target(path, working_dir_root),
            })
        return result
    except Exception as exc:
        logger.debug("workspace-guard: allow_path handler error (fail-open): %s", exc)
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
        runtime_allowlist_clear()
        _reset_fail_open_flag()
        ctx = _get_ctx()
        profile = getattr(ctx, "profile_name", None) if ctx else None
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
                    "workspace-guard: session-start reminder skipped "
                    "(inject_message unavailable)"
                )
        else:
            logger.debug(
                "workspace-guard: session-start reminder skipped "
                "(inject_message unavailable)"
            )
    except Exception as exc:
        logger.debug("workspace-guard: session start hook error: %s", exc)


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
        _emit_verdict(
            "allow", tool_name, "landed:" + str(tool_name), target,
            reason="write tool call completed (status: %s)" % (status or "ok"),
            working_dir_root=working_dir_root,
            is_subagent=_is_child_session(session_id),
            session_id=session_id,
        )
    except Exception as exc:
        logger.debug("workspace-guard: post_tool_call hook error: %s", exc)


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
        logger.debug("workspace-guard: post_approval_response hook error: %s", exc)


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
        logger.debug("workspace-guard: pre_command hook error: %s", exc)
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
        logger.debug("workspace-guard: subagent_start hook error: %s", exc)


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
        logger.debug("workspace-guard: subagent_stop hook error: %s", exc)


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
    unavailable). Tests inject a fake via guard._session_cwd_fn."""
    if callable(_session_cwd_fn):
        try:
            return _session_cwd_fn(task_id)
        except Exception as exc:
            logger.debug(
                "workspace-guard: get_session_cwd(%r) failed: %s", task_id, exc
            )
    return None


def _resolve_target(target, task_id, working_dir_root):
    """Resolve a target to absolute (spec 5.3 step 4).

    Relative targets resolve against the session CWD; when unrecorded
    (None) fall back to working_dir_root (conservative, DEBUG log). Never
    uses os.getcwd() (the plugin process CWD may differ).
    """
    if os.path.isabs(target):
        return target

    base = _session_cwd(task_id)
    if not base:
        logger.debug(
            "workspace-guard: session CWD unrecorded for task %r, resolving "
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

    if os.name == "nt":
        drive, _ = ntpath.splitdrive(path)
        if not drive:
            logger.warning(
                "workspace-guard: target %r unclassifiable after "
                "normalization (no drive); treating as external "
                "(fail-open)",
                path,
            )

    return path


def _normalize_posix(path):
    """Normalize a target path on POSIX hosts (normpath identity)."""
    return os.path.normpath(path)


def normalize_target(path, working_dir_root):
    """Normalize a target path before classification (chain step 0)."""
    if os.name == "nt":
        return _normalize_windows(path, working_dir_root)
    return _normalize_posix(path)


# ---------------------------------------------------------------- Classification (spec 5.3 steps 6/7)

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

    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        # Different drive on Windows: cannot relate -> external.
        return {"outcome": "external-write", "rule_key": "external-write"}
    if rel.startswith(".."):
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
    """Root-file whitelist from guard-config.yaml (spec 5.6).

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
        # workspace-organization/scripts (plugin_dir = directory of guard.py).
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
        "HERMES_HOME/workspace-guard/guard-config.yaml\n"
        "Reply using the [Reason]/[Next] template." % (target_fwd, fix_line)
    )


# ---------------------------------------------------------------- Terminal coarse tiers (spec 5.10)

def _tokenize_command(command):
    """Split a shell command into tokens (lightweight, POSIX-ish).

    Respects single quotes (fully literal), double quotes (backslash only
    escapes " \\ $ ` inside), and backslash escaping outside quotes.
    Unquoted whitespace separates tokens. Redirect operators (>, >>, 2>,
    &>, 1>, 1>>, 2>>) and pipes / background ampersands are emitted as
    standalone operator tokens. Lenient by design: unclosed quotes and
    malformed input never raise (the remainder is absorbed into the
    current token).
    """
    if not isinstance(command, str):
        return []
    tokens = []
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c in " \t\n\r":
            i += 1
            continue
        if c == "|":
            tokens.append("|")
            i += 1
            continue
        if c == "&":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append("&>")
                i += 2
            else:
                tokens.append("&")
                i += 1
            continue
        if c == ">":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append(">>")
                i += 2
            else:
                tokens.append(">")
                i += 1
            continue

        # Word start: handle quoting and escapes until an unquoted
        # whitespace or operator is reached.
        word = []
        in_single = False
        in_double = False
        while i < n:
            c = command[i]
            if in_single:
                if c == "'":
                    in_single = False
                else:
                    word.append(c)
                i += 1
                continue
            if in_double:
                if c == '"':
                    in_double = False
                    i += 1
                    continue
                if c == "\\" and i + 1 < n and command[i + 1] in ('"', "\\", "$", "`"):
                    word.append(command[i + 1])
                    i += 2
                    continue
                word.append(c)
                i += 1
                continue
            if c == "'":
                in_single = True
                i += 1
                continue
            if c == '"':
                in_double = True
                i += 1
                continue
            if c == "\\":
                if i + 1 < n:
                    word.append(command[i + 1])
                    i += 2
                else:
                    word.append("\\")
                    i += 1
                continue
            if c in " \t\n\r" or c in "|&>":
                break
            word.append(c)
            i += 1

        w = "".join(word)
        # Glued fd redirect: "2>" / "2>>" (also "1>", "1>>").
        if w in ("1", "2") and i < n and command[i] == ">":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append(w + ">>")
                i += 2
            else:
                tokens.append(w + ">")
                i += 1
            continue
        tokens.append(w)

    return tokens


def _terminal_block_targets(tokens):
    """Block-tier write targets (spec 5.10): redirects, touch args, cp/mv
    destination. Returns a list of (target, rule_key) pairs.

    Non-literal targets (containing $ or `) are skipped -- they fall into
    the uncertain tier (allow + log) instead.
    """
    out = []
    n = len(tokens)
    redirect_idx = set()
    for i, tok in enumerate(tokens):
        if tok in _REDIRECT_TOKENS and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt not in _OPERATOR_TOKENS and not _NON_LITERAL_RE.search(nxt):
                out.append((nxt, "terminal-redirect"))
                redirect_idx.add(i + 1)

    first = tokens[0]
    if first == "touch":
        for i in range(1, n):
            tok = tokens[i]
            if tok in _OPERATOR_TOKENS or i in redirect_idx or tok.startswith("-"):
                continue
            if not _NON_LITERAL_RE.search(tok):
                out.append((tok, "terminal-touch"))

    if first in ("cp", "mv"):
        for i in range(n - 1, -1, -1):
            tok = tokens[i]
            if tok in _OPERATOR_TOKENS or i in redirect_idx or tok.startswith("-"):
                continue
            if not _NON_LITERAL_RE.search(tok):
                out.append((tok, "terminal-cp-mv"))
            break

    return out


def _terminal_uncertain(tokens):
    """Uncertain write-intent detection (5.10 allow-and-log tier).

    Nested shells (bash -c / sh -c / powershell -Command), any command
    starting with python/node/sed/tee/curl/wget/dd, or any non-literal
    ($ or `) token -> True.
    """
    if not tokens:
        return False
    first = tokens[0]
    if first in _UNCERTAIN_COMMANDS:
        return True
    if first in _NESTED_SHELLS and any(
        t == "-c" or t.lower() == "-command" for t in tokens
    ):
        return True
    return any(_NON_LITERAL_RE.search(t) for t in tokens)


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
    if os.path.isabs(target):
        return target
    return os.path.join(base, target)


def _guard_terminal(args, task_id, working_dir_root, exempt_paths,
                    is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers).

    - terminal_guard disabled -> terminal never blocked (DEBUG log).
    - Block tier: redirect / touch / cp-mv targets classify through the
      shared chain; strictest wins (any block -> block).
    - Uncertain tier: nested shells, python/node/sed/tee/curl/wget/dd,
      dynamic paths -> ALLOW + LOG (rule_key terminal-write-uncertain),
      NO approval gate.
    - Read-only / unparseable -> allow (no verdict event).
    - Any exception -> None (fail-open).
    """
    try:
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str) or not command:
            return None
        if not terminal_guard_enabled():
            logger.debug(
                "workspace-guard: terminal guard disabled via terminal_guard config"
            )
            return None

        tokens = _tokenize_command(command)
        if not tokens:
            return None
        base = _terminal_base(args, task_id, working_dir_root)

        for target, rule_key in _terminal_block_targets(tokens):
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
        logger.debug("workspace-guard: terminal guard error (fail-open): %s", exc)
        return None
