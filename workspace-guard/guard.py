"""Core guard logic: hook registration and tool-call interception."""

import hashlib
import logging
import ntpath
import os
import re

try:
    from .config import (
        AUTO_UPDATE_TOOL_SCHEMA,
        REGISTER_TOOL_SCHEMA,
        _format_memo,
        _get_hermes_home,
        _list_profiles,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        load_memo,
        reset_cache,
        runtime_allowlist_clear,
        sync_memo,
        terminal_guard_enabled,
        workspace_guard_allow_path,
        workspace_guard_auto_update_workspace,
        workspace_guard_register_workspace,
    )
except ImportError:
    from config import (
        AUTO_UPDATE_TOOL_SCHEMA,
        REGISTER_TOOL_SCHEMA,
        _format_memo,
        _get_hermes_home,
        _list_profiles,
        get_cached_config,
        is_exempt,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        load_memo,
        reset_cache,
        runtime_allowlist_clear,
        sync_memo,
        terminal_guard_enabled,
        workspace_guard_allow_path,
        workspace_guard_auto_update_workspace,
        workspace_guard_register_workspace,
    )

# get_session_cwd is a Hermes runtime API (tools/terminal_tool.py) absent
# from the test venv. Guarded module-level import so guard.py never crashes
# when it is unavailable; callers fall back to working_dir_root (SCR-001 5.3).
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

REMINDER_MESSAGE = (
    "[workspace-guard] Active. File writes in the Default Working Directory "
    "must be inside a Session Directory (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). "
    "Use create_session_dir.py to create one before writing files."
)

# SCR-004: one-time fail-open warning when working_dir_root cannot be
# resolved (guard disabled). Fires at most once per session; the flag is
# reset at on_session_start so each new session re-warns until the guard
# is fixed.
FAIL_OPEN_WARNING_MESSAGE = (
    "[workspace-guard] WARNING: The guard is DISABLED because the Default "
    "Working Directory could not be resolved. File writes are NOT being "
    "enforced.\n"
    "\n"
    "Check your profile's config.yaml (terminal.cwd) or guard-config.yaml "
    "(working_dir_root). Fix the configuration and restart the session."
)

# SCR-002 (task 9.6): runtime allowlist tool schema (spec-change-002 2.5).
# Registered at register() via ctx.register_tool so the agent can explicitly
# allow a user-specified path (Tier 0) without editing guard-config.yaml.
# OpenAI function-call format required by Hermes tools.registry (SCR-008:
# a bare JSON schema left parameters/description empty and the tool unusable).
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

# Context management: stored at register() time
_registered_ctx = None

# SCR-004: set after the first fail-open warning attempt so it fires at
# most once per session (de-duplication).
_fail_open_warned = False


def register(ctx):
    """Register workspace-guard hooks with the Hermes plugin context.

    SCR-001 (task 9.6): performs a FULL memo sync after the config load.
    Fail-open: a sync error logs a warning but never fails registration.
    SCR-002 (task 9.6): registers the runtime allowlist tool
    (workspace_guard_allow_path) when the ctx supports register_tool; a ctx
    without it must not break registration.
    SCR-011 (task 14.6): registers the /workspace-guard quick command
    (workspace_status | workspace_update) and the two memo tools
    (workspace_guard_auto_update_workspace,
    workspace_guard_register_workspace), guarded the same way.
    """
    global _registered_ctx
    try:
        _registered_ctx = ctx
        reset_cache()
        get_cached_config(ctx)

        # Full memo sync at register time (SCR-001 2.4). Fail-open: never
        # fail registration over a sync error.
        try:
            sync_memo()
        except Exception as exc:
            logger.warning("workspace-guard: memo sync at register failed: %s", exc)

        ctx.register_hook("pre_tool_call", _guard_hook)
        ctx.register_hook("on_session_start", _session_start_hook)

        # Runtime allowlist tool (SCR-002 2.5). The plugin's own tool call
        # is never intercepted by pre_tool_call. Guarded with hasattr +
        # try/except so a ctx without register_tool still registers.
        if hasattr(ctx, "register_tool"):
            try:
                ctx.register_tool(
                    "workspace_guard_allow_path",
                    toolset="workspace-guard",
                    schema=ALLOW_PATH_TOOL_SCHEMA,
                    handler=workspace_guard_allow_path,
                )
                # SCR-011 (task 14.6): memo sync tool + registration tool.
                # The registration handler gets ctx injected at call time so
                # it stays ACTIVE-PROFILE-ONLY (SCR-011 2.2); a ctx-less
                # dispatch (e.g. tests) is rejected by the handler itself.
                ctx.register_tool(
                    "workspace_guard_auto_update_workspace",
                    toolset="workspace-guard",
                    schema=AUTO_UPDATE_TOOL_SCHEMA,
                    handler=workspace_guard_auto_update_workspace,
                )
                ctx.register_tool(
                    "workspace_guard_register_workspace",
                    toolset="workspace-guard",
                    schema=REGISTER_TOOL_SCHEMA,
                    handler=lambda args, **kw: workspace_guard_register_workspace(
                        args, ctx=_registered_ctx, **kw
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "workspace-guard: register_tool failed: %s", exc
                )

        # SCR-011 (task 14.6): /workspace-guard quick command. Guarded so a
        # ctx without register_command still registers.
        if hasattr(ctx, "register_command"):
            try:
                ctx.register_command(
                    "workspace-guard",
                    _workspace_guard_cmd,
                    description="Show or update the profile workspace memo",
                    args_hint="workspace_status|workspace_update",
                )
            except Exception as exc:
                logger.warning(
                    "workspace-guard: register_command failed: %s", exc
                )

        logger.debug("workspace-guard: registered successfully")
    except Exception as exc:
        logger.warning("workspace-guard: registration failed: %s", exc)


def _guard_hook(tool_name, args, task_id=None, **kwargs):
    """Pre-tool-call hook: block writes outside session directory."""
    try:
        return _guard_logic(tool_name, args, task_id, **kwargs)
    except Exception as exc:
        logger.debug("workspace-guard: guard hook error (fail-open): %s", exc)
        return None


def _guard_logic(tool_name, args, task_id=None, **kwargs):
    """Core judgment flow for the guard (spec 5.3 unified chain)."""
    # Step 1: non-intercepted tool -> allow
    if tool_name not in INTERCEPTED_TOOLS:
        return None

    # Get cached config (still a 2-tuple: (working_dir_root, exempt_paths))
    ctx_proxy = _get_ctx()
    working_dir_root, exempt_paths = get_cached_config(ctx_proxy)

    # Guard-disabled shortcut: no working_dir_root -> warn once (SCR-004)
    # then allow. The warning is advisory only; enforcement is impossible
    # without a working_dir_root, so fail-open (return None) is preserved.
    if working_dir_root is None:
        _warn_fail_open_once(ctx_proxy)
        return None

    # Step 2: extract target path(s)
    # Terminal lane (SCR-002): tiered block/approve/allow for the whole
    # command; write_file/patch continue through the shared path extractor.
    if tool_name == "terminal":
        return _guard_terminal(args, task_id, working_dir_root, exempt_paths)

    target_paths = _extract_target_paths(tool_name, args)
    if not target_paths:
        return None

    # Steps 4-5: resolve -> normalize -> classify each target. First
    # blocking result returns immediately (early-return preserved).
    for target in target_paths:
        abs_target = _resolve_target(target, task_id, working_dir_root)
        normalized = normalize_target(abs_target, working_dir_root)
        result = classify_target(normalized, working_dir_root, exempt_paths)
        if result is not None:
            return result

    return None


# ---------------------------------------------------------------- Case-insensitive comparisons
# SCR-006 (task 9.10): Windows-only casefold comparisons (os.name == "nt")
# for the exempt prefix match, AGENTS.md equality, and memo cross-profile
# prefix. POSIX keeps exact comparison. The *_ci helpers ARE the Windows
# comparison path (always casefold); call sites consult them only when
# os.name == "nt", after the exact checks run first, so same-case behavior
# is unchanged on every platform and config.is_exempt is untouched for
# its other callers.


def _exempt_prefix_ci(target, exempt):
    """Windows exempt prefix match (SCR-006, task 9.10).

    True when `target` starts with `exempt` after both sides are
    forward-slash normalized and casefolded. Mirrors config.is_exempt's
    startswith semantics (no path-delimiter requirement). This is the
    Windows comparison path; it is always case-insensitive.
    """
    return target.replace("\\", "/").casefold().startswith(
        exempt.replace("\\", "/").casefold()
    )


def _is_exempt_ci(target, exempt_paths):
    """Tier 0 exempt check (SCR-006, task 9.10).

    Windows: case-insensitive prefix match (via _exempt_prefix_ci).
    POSIX: delegates to config.is_exempt (exact comparison, unchanged).
    config.is_exempt itself is never modified, so its behavior for other
    callers is preserved.
    """
    if os.name != "nt":
        return is_exempt(target, exempt_paths)
    return any(_exempt_prefix_ci(target, exempt) for exempt in exempt_paths)


def _paths_equal_ci(a, b):
    """Windows path equality (SCR-006, task 9.10).

    True when both sides are equal after forward-slash normalization and
    casefolding. This is the Windows comparison path; it is always
    case-insensitive.
    """
    return a.replace("\\", "/").casefold() == b.replace("\\", "/").casefold()


def _prefix_ci(target, workspace):
    """Windows workspace prefix match (SCR-006, task 9.10).

    True when `target` equals `workspace` or starts with `workspace + "/"`
    (the "/" delimiter keeps sibling prefixes from matching) after
    forward-slash normalization and casefolding both sides. This is the
    Windows comparison path; it is always case-insensitive.
    """
    target_fwd = target.replace("\\", "/").casefold()
    ws_fwd = workspace.replace("\\", "/").casefold()
    return target_fwd == ws_fwd or target_fwd.startswith(ws_fwd + "/")


def classify_target(target, working_dir_root, exempt_paths):
    """Classify a single normalized absolute target (spec 5.3).

    Returns None (allow), a block dict, or an approve dict (cross-profile
    write, SCR-001).

    Tier 0 (exempt_paths + runtime allowlist) is checked first for EVERY
    target, including targets under another profile's workspace (explicit
    exemption wins, SCR-001). Targets outside working_dir_root that fall
    under another valid profile's workspace (per the memo) return an
    approve dict (human-approval gate); other external targets -> allow
    (jurisdiction). Under working_dir_root: root files listed in
    allowed_root_files and valid Session Directories allow; everything
    else blocks.
    """
    # Tier 0: explicit user-specified paths always allow (SCR-001);
    # exempt prefix match is case-insensitive on Windows (SCR-006, task 9.10)
    if _is_exempt_ci(target, exempt_paths) or is_runtime_allowlisted(target):
        return None

    # Jurisdiction: outside working_dir_root -> cross-profile check or
    # external allow. Cross-profile branch (SCR-001, task 9.4 part 2):
    # targets under ANOTHER valid profile's workspace get a human-approval
    # gate instead of a silent allow. Fail-open: any exception in the memo
    # load or classification -> None (allow), never crash the agent.
    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        return None
    if rel.startswith(".."):
        try:
            memo = load_memo()
            current_profile = getattr(_get_ctx(), "profile_name", None)
            hit = _cross_profile_hit(target, memo, current_profile)
            if hit:
                entry = memo.get("profiles", {}).get(hit, {})
                workspace = entry.get("workspace") or ""
                target_fwd = target.replace(os.sep, "/")
                message = (
                    "[workspace-guard] WARNING: This write targets another "
                    "profile's workspace.\n"
                    "\n"
                    "Target: %s\n"
                    "Profile: %s (workspace: %s)\n"
                    "Active profile: %s\n"
                    "\n"
                    "This usually happens when the Hermes Desktop app "
                    "switches profiles without refreshing the workspace. "
                    "Please confirm you intend to write to this profile's "
                    "workspace.\n"
                    "\n"
                    "Note: choosing [a]lways approves writes to the '%s' "
                    "profile's workspace only; other profiles will still "
                    "prompt."
                    % (target_fwd, hit, workspace, current_profile, hit)
                )
                return {
                    "action": "approve",
                    "message": message,
                    "rule_key": "cross-profile-write:" + hit,
                }
        except Exception as exc:
            logger.debug(
                "workspace-guard: cross-profile detection failed "
                "(fail-open): %s",
                exc,
            )
        return None

    # Under working_dir_root: a root file (directly under the root, i.e.
    # relpath without separators) whose basename is in allowed_root_files is
    # allowed; any other root file blocks. D1 (SCR-011 2.2): the guard reads
    # the SAME allowed_root_files key the audit reads, so guard and audit
    # never disagree about root files. STRICT: config/key absent -> empty
    # whitelist -> every root file blocks (fail-closed). Basename match is
    # case-insensitive on Windows (SCR-006 task 9.10 style), exact on POSIX.
    if "/" not in rel.replace("\\", "/"):
        base = os.path.basename(target)
        for allowed in _allowed_root_files():
            if base == allowed or (
                os.name == "nt" and base.casefold() == allowed.casefold()
            ):
                return None

    # Inside a valid session directory -> allowed
    if is_inside_session_dir(target, working_dir_root):
        return None

    # Otherwise: BLOCK (message verbatim, unchanged)
    target_fwd = target.replace(os.sep, "/")
    wdr_fwd = working_dir_root.replace(os.sep, "/")
    scripts_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "skills", "productivity",
                     "workspace-organization", "scripts")
    ).replace(os.sep, "/")
    # SCR-013 (task 16.6): the runtime config lives at
    # HERMES_HOME/workspace-guard/, not inside the plugin directory.
    config_dir = os.path.normpath(
        str(_get_hermes_home() / "workspace-guard")
    ).replace(os.sep, "/")
    message = (
        "BLOCKED: File operations in the Default Working Directory require "
        "a Session Directory.\n"
        "Target: %s\n"
        "Fix: Create a session directory first:\n"
        "  python %s/create_session_dir.py <task_name> "
        "--workspace %s\n"
        "Then write to its Outputs/ or .tmp/ subdirectory.\n"
        "If this is a project directory, add it to exempt_paths in "
        "%s/guard-config.yaml" % (target_fwd, scripts_dir, wdr_fwd, config_dir)
    )
    logger.warning("workspace-guard: BLOCKED write to %s", target_fwd)
    return {"action": "block", "message": message}


def _extract_target_paths(tool_name, args):
    """Extract target file path(s) from tool arguments."""
    if not isinstance(args, dict):
        return []

    if tool_name == "write_file":
        path = args.get("path")
        return [path] if path else []

    if tool_name == "patch":
        # mode=replace: single path
        path = args.get("path")
        if path:
            return [path]
        # mode=patch: parse V4A format
        patch_content = args.get("patch", "")
        if patch_content:
            return PATCH_FILE_RE.findall(patch_content)

    return []


# ---------------------------------------------------------------- Path normalization
# SCR-006 (task 9.9): pure helpers + dispatcher, both platform branches
# unit-testable on any host OS. normalize_target is wired into the
# judgment chain as step 0 (_guard_logic / _guard_terminal).


def _normalize_windows(path, working_dir_root):
    """Normalize a target path on Windows (MSYS mapping + drive inheritance).

    1. Map MSYS forward-slash forms to drive-qualified paths:
       /c/..., //c/... -> C:/<rest>; /cygdrive/c/... -> C:/<rest>.
       Drive letter case-insensitive; output drive uppercased.
       UNC paths (//server/share) do not match these regexes.
    2. os.path.normpath (separator and dot-segment normalization).
    3. Drive inheritance: rooted paths that still lack a drive
       (\\HermesWorkspace\\..., unmapped /usr/bin -> \\usr\\bin) get the
       drive of working_dir_root; skipped if working_dir_root has no drive.
    4. Fail-open: a path that STILL has no drive after inheritance (e.g.
       drive-less rooted target + drive-less working_dir_root) is
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


def _resolve_target(target, task_id, working_dir_root):
    """Resolve a target to absolute for classification (SCR-001, task 9.5).

    Absolute targets are returned unchanged (normalization happens elsewhere).
    Relative targets resolve against the actual session CWD
    (get_session_cwd(task_id)) -- never working_dir_root when the session CWD
    is known, so a stale/wrong workspace is classified correctly. When the
    session CWD is unrecorded (None), fall back to working_dir_root
    (conservative: keeps the write under guard jurisdiction). Never uses
    os.getcwd() (the plugin process CWD may differ from the session CWD).
    Returns the joined absolute path; no normpath/normalize here (separate
    step).
    """
    if os.path.isabs(target):
        return target

    base = None
    if callable(get_session_cwd):
        try:
            base = get_session_cwd(task_id)
        except Exception as exc:
            logger.debug(
                "workspace-guard: get_session_cwd(%r) failed, falling back to "
                "working_dir_root: %s", task_id, exc
            )
    if not base:
        logger.debug(
            "workspace-guard: session CWD unrecorded for task %r, resolving "
            "relative target against working_dir_root", task_id
        )
        base = working_dir_root
    return os.path.join(base, target)


def _cross_profile_hit(target, memo, current_profile):
    """Detect cross-profile workspace hits (SCR-001, task 9.4 part 1).

    Pure helper: returns the name of a profile whose workspace contains
    `target`, or None. The current profile's own entry is EXCLUDED (its
    writes go through the existing within-workspace checks, spec 2.2).
    Only entries with status == "valid" AND a non-null, non-empty
    workspace qualify. "Under a workspace" is a forward-slash prefix
    match: target == workspace or target starts with workspace + "/".
    When several profiles match, the choice is deterministic: the
    alphabetically first matching profile name. Malformed memos (non-dict,
    missing "profiles", non-dict "profiles") return None (fail-open, never
    raises).

    Not yet wired into classify_target (wiring is task 9.4 part 2).
    """
    if not isinstance(memo, dict):
        return None
    profiles = memo.get("profiles")
    if not isinstance(profiles, dict):
        return None

    target_fwd = target.replace("\\", "/")
    hits = []
    for name, entry in profiles.items():
        if name == current_profile:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "valid":
            continue
        workspace = entry.get("workspace")
        if not isinstance(workspace, str) or not workspace:
            continue
        ws_fwd = workspace.replace("\\", "/")
        if (
            target_fwd == ws_fwd
            or target_fwd.startswith(ws_fwd + "/")
            or (os.name == "nt" and _prefix_ci(target_fwd, ws_fwd))
        ):
            hits.append(name)

    if not hits:
        return None
    # Deterministic tie-break: first in sorted profile names.
    return sorted(hits)[0]


def _warn_fail_open_once(ctx):
    """Inject the one-time fail-open warning (SCR-004, section 2.3).

    Fires at most once per session: the module-level _fail_open_warned
    flag short-circuits after the first attempt. Swallows ALL exceptions
    (ctx None, inject_message missing or raising) so the guard never
    crashes. Logs the warning regardless of injection success.
    """
    global _fail_open_warned
    if _fail_open_warned:
        return
    _fail_open_warned = True
    logger.warning(
        "workspace-guard: guard DISABLED (working_dir_root unresolved); "
        "file writes are not being enforced"
    )
    try:
        if ctx and hasattr(ctx, "inject_message"):
            ctx.inject_message(FAIL_OPEN_WARNING_MESSAGE)
    except Exception:
        pass


def _reset_fail_open_flag():
    """Reset the one-time fail-open warning flag (test hook, SCR-004).

    The flag lives in guard.py (config.reset_cache cannot see it), so this
    guard-level reset is what the test fixture and _session_start_hook use
    to clear it between sessions/tests.
    """
    global _fail_open_warned
    _fail_open_warned = False


def _sync_memo_if_stale():
    """Incremental memo sync check (SCR-001 2.4, task 9.6).

    Compares the live profile set (_list_profiles() names) against the
    memo's existing profile keys. New or deleted profiles -> re-run the
    full sync_memo(); otherwise skip. Fail-open: any error is logged and
    skipped so the session-start reminder flow is never affected.
    """
    try:
        live = {name for name, _ in _list_profiles()}
        memo = load_memo()
        memo_profiles = memo.get("profiles", {}) if isinstance(memo, dict) else {}
        if not isinstance(memo_profiles, dict):
            memo_profiles = {}
        if set(live) == set(memo_profiles):
            return
        sync_memo()
    except Exception as exc:
        logger.debug(
            "workspace-guard: incremental memo sync check failed (fail-open): %s",
            exc,
        )


def _session_start_hook(session_id=None, model=None, platform=None, **kwargs):
    """Session start hook: inject workspace discipline reminder.

    Resets the SCR-004 fail-open warning flag FIRST so a guard that stays
    disabled re-warns the user at the start of each new session, clears the
    runtime allowlist (session-scoped exemption, finding 2026-08-07), then
    runs the incremental memo sync check (SCR-001 2.4, task 9.6), then
    proceeds with the regular reminder injection.

    Reminder delivery is CLI/TUI-only (ctx.inject_message requires the CLI
    _cli_ref; gateway/desktop sessions log a skip and rely on BLOCK messages
    + the skill for discipline teaching -- SCR-007, decision 2026-08-07).
    """
    try:
        _reset_fail_open_flag()
        runtime_allowlist_clear()
        _sync_memo_if_stale()
        ctx_proxy = _get_ctx()
        working_dir_root, _ = get_cached_config(ctx_proxy)
        if working_dir_root is None:
            return
        ctx = _get_ctx()
        if ctx and hasattr(ctx, "inject_message"):
            injected = ctx.inject_message(REMINDER_MESSAGE)
            if not injected:
                logger.debug(
                    "workspace-guard: session-start reminder skipped "
                    "(inject_message unavailable; CLI/TUI-only feature, "
                    "gateway/desktop sessions rely on BLOCK messages + skill)"
                )
    except Exception as exc:
        logger.debug("workspace-guard: session start hook error: %s", exc)


def _get_ctx():
    """Return the registered ctx."""
    return _registered_ctx


# ---------------------------------------------------------------- SCR-011: quick command + root exemption

def _allowed_root_files():
    """Root-file whitelist from guard-config.yaml (D1, SCR-011 2.2).

    Reads the SAME allowed_root_files key the audit reads (via
    workspace_resolver.py), so guard and audit never disagree about root
    files. STRICT fallback: config missing/unreadable -> empty list ->
    every root file blocks (fail-closed, matching the audit's
    over-report).
    """
    try:
        return load_guard_config().get("allowed_root_files") or []
    except Exception:
        return []


def _workspace_guard_cmd(raw_args):
    """Slash command handler (SCR-011 2.6): /workspace-guard.

    workspace_status (default): read-only memo display (synced_at +
    per-profile workspace / status / changed_at). workspace_update: full
    memo sync (user-triggered), returns the full memo display. Unknown
    subcommand: usage line. Never raises (errors become the message).
    """
    try:
        tokens = (raw_args or "").strip().split()
        sub = tokens[0].lower() if tokens else "workspace_status"
        if sub == "workspace_status":
            return _format_memo(load_memo())
        if sub == "workspace_update":
            memo = sync_memo()
            return "[workspace-guard] Memo updated.\n" + _format_memo(memo)
        return "Usage: /workspace-guard workspace_status | workspace_update"
    except Exception as exc:
        return "[workspace-guard] Command failed: %s" % exc


# ---------------------------------------------------------------- Terminal parsing
# SCR-002: lightweight shell tokenizer + tiered write-target extraction.
# Pure module-level helpers (stdlib only, no filesystem access, never
# raise on odd input). Wired into _guard_logic via _guard_terminal
# (Tier 1 block / Tier 2 approve / Tier 3 allow). Contract per
# docs/spec-change-002-terminal-write-interception.md sections 2.3/2.4.

# Redirect operators are emitted by _tokenize_command as standalone tokens
# (">", ">>", "2>", "&>", plus fd variants "1>", "1>>", "2>>"), so the
# target extractor detects them by exact membership and takes the next
# plain token as the target.
_REDIRECT_TOKENS = frozenset((">", ">>", "1>", "2>", "1>>", "2>>", "&>"))
_OPERATOR_TOKENS = frozenset(("|", "&")) | _REDIRECT_TOKENS
_NESTED_SHELLS = frozenset(("bash", "sh", "powershell", "pwsh"))

# python -c literal open('path', 'w'|'a') -> high-confidence target.
_OPEN_LITERAL_RE = re.compile(
    r"""open\s*\(\s*(['"])([^'"]*)\1\s*,\s*(['"])([wa][^'"]*)\3\s*\)"""
)
# python -c open(...) with a w/a mode but a non-literal path -> Tier 2.
_OPEN_WRITE_INTENT_RE = re.compile(r"""open\s*\([^)]*['"][waWA]""")
# node -e write intent markers -> Tier 2.
_NODE_WRITE_INTENT_RE = re.compile(
    r"writeFile|writeFileSync|appendFile|openSync|open\s*\("
)


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


def _extract_terminal_write_targets(command):
    """Extract high-confidence write targets from a terminal command.

    Returns a (targets, uncertain) tuple:
      - Tier 1 structures (redirect targets, touch args, cp/mv dest,
        tee [-a] arg, curl -o / wget -O value, dd of= value, sed -i
        edited file, python -c literal open('path', 'w'|'a')) yield
        explicit targets with uncertain=False.
      - Tier 2 write intent (python/node -c/-e non-literal paths, nested
        bash -c / sh -c / powershell -Command, curl -O, wget without -O)
        yields uncertain=True (targets may be empty).
      - Read-only / unparseable input yields ([], False).
    Conservative: never speculative targets; never raises.
    """
    if not isinstance(command, str):
        return [], False
    try:
        tokens = _tokenize_command(command)
    except Exception:
        return [], False
    if not tokens:
        return [], False

    targets = []
    uncertain = False
    n = len(tokens)
    first = tokens[0]

    # Tier 2: nested shell -- inner command cannot be confidently parsed.
    if first in _NESTED_SHELLS and any(
        t == "-c" or t.lower() == "-command" for t in tokens
    ):
        return [], True

    # Redirects: an operator token's next plain token is its target.
    redirect_target_idx = set()
    for i, tok in enumerate(tokens):
        if tok in _REDIRECT_TOKENS and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt not in _OPERATOR_TOKENS:
                targets.append(nxt)
                redirect_target_idx.add(i + 1)

    def _plain(i):
        """Token i is a usable plain word (not operator / not a redirect target)."""
        return (
            0 <= i < n
            and tokens[i] not in _OPERATOR_TOKENS
            and i not in redirect_target_idx
        )

    # touch: every non-flag argument names a file to create.
    if first == "touch":
        for i in range(1, n):
            tok = tokens[i]
            if tok in _OPERATOR_TOKENS or i in redirect_target_idx:
                continue
            if tok.startswith("-"):
                continue
            targets.append(tok)

    # cp / mv: the last plain argument is the destination.
    if first in ("cp", "mv"):
        for i in range(n - 1, -1, -1):
            if not _plain(i) or tokens[i].startswith("-"):
                continue
            targets.append(tokens[i])
            break

    # tee: the argument after an optional -a/--append flag is the log file.
    if "tee" in tokens:
        for i, tok in enumerate(tokens):
            if tok != "tee":
                continue
            j = i + 1
            if j < n and tokens[j] in ("-a", "--append"):
                j += 1
            if _plain(j):
                targets.append(tokens[j])

    # curl: -o <file> is explicit; bare -O derives the name from the URL.
    if first == "curl":
        if "-o" in tokens:
            j = tokens.index("-o") + 1
            if _plain(j):
                targets.append(tokens[j])
        if "-O" in tokens:
            uncertain = True

    # wget: -O <file> is explicit; without it the name derives from the URL.
    if first == "wget":
        if "-O" in tokens:
            j = tokens.index("-O") + 1
            if _plain(j):
                targets.append(tokens[j])
        else:
            uncertain = True

    # dd: of=<path> value.
    if first == "dd":
        for tok in tokens[1:]:
            if tok.startswith("of="):
                targets.append(tok[3:])

    # sed -i [.bak]: skip the flag and the expression; the edited file is
    # the token two places after the flag.
    if first == "sed":
        for i, tok in enumerate(tokens):
            if tok == "-i" or tok.startswith("-i."):
                if _plain(i + 2):
                    targets.append(tokens[i + 2])
                break

    # python -c: literal open('path', 'w'|'a') is high-confidence; any
    # other open(...) with a w/a mode is Tier 2 (path uncertain).
    if first in ("python", "python3", "py") and "-c" in tokens:
        j = tokens.index("-c") + 1
        if j < n:
            code = tokens[j]
            literal = _OPEN_LITERAL_RE.search(code)
            if literal:
                targets.append(literal.group(2))
            elif _OPEN_WRITE_INTENT_RE.search(code):
                uncertain = True

    # node -e: write intent without a literal path is Tier 2.
    if first == "node" and "-e" in tokens:
        j = tokens.index("-e") + 1
        if j < n and _NODE_WRITE_INTENT_RE.search(tokens[j]):
            uncertain = True

    # Deduplicate while preserving first-seen order.
    seen = set()
    result = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result, uncertain


# ---------------------------------------------------------------- Terminal guard wiring (SCR-002, task 10.3/10.4)
# _guard_terminal is the terminal lane of _guard_logic. Tier 1 targets
# resolve against the terminal base chain (workdir -> session CWD ->
# working_dir_root, never os.getcwd()) and classify through the shared
# classify_target chain (Tier 0 exempt/allowlist, session-dir allow,
# cross-profile approve, BLOCK). Tier 2 (write intent, uncertain target)
# escalates to an approve gate keyed per command+base_dir. Strictest-wins:
# any block outranks any cross-profile approve. Fail-open: any exception
# -> None (allow).


def _terminal_session_cwd(task_id):
    """Session CWD for terminal resolution (guarded; None when unavailable)."""
    if callable(get_session_cwd):
        try:
            return get_session_cwd(task_id)
        except Exception as exc:
            logger.debug(
                "workspace-guard: get_session_cwd(%r) failed: %s", task_id, exc
            )
    return None


def _terminal_base(args, task_id, working_dir_root):
    """Resolve the terminal relative-target base (spec 5.3 step 4).

    Chain: args["workdir"] -> get_session_cwd(task_id) -> working_dir_root.
    Never os.getcwd(): the plugin process CWD may differ from the session
    CWD, so a stale cwd must not be used as the resolution base.
    """
    base = args.get("workdir") if isinstance(args, dict) else None
    if not base:
        base = _terminal_session_cwd(task_id)
    if not base:
        base = working_dir_root
    return base


def _resolve_terminal_target(target, base):
    """Resolve a terminal write target against the relative-target base."""
    if os.path.isabs(target):
        return target
    return os.path.join(base, target)


def _terminal_base_dir(base):
    """Normalize the base dir for the approve rule_key (spec 5.10).

    Absolute, forward-slash separators, casefolded on Windows so separator
    / case variants of the same directory hash identically.
    """
    base_dir = os.path.abspath(base) if base else ""
    base_dir = base_dir.replace("\\", "/")
    if os.name == "nt":
        base_dir = base_dir.casefold()
    return base_dir


def _guard_terminal(args, task_id, working_dir_root, exempt_paths):
    """Terminal write interception (SCR-002 tiered block/approve/allow).

    - command missing / not a string / empty -> None (allow).
    - terminal_guard_enabled() False -> None (safety valve, default on).
    - Tier 2 (write intent, uncertain target) -> approve gate with rule_key
      "workspace-guard:terminal-write:<sha256(command + NUL + base_dir)[:12]>".
    - Tier 1 targets: resolve -> normalize -> classify_target each;
      strictest-wins (any block -> that block; else any cross-profile
      approve -> that approve; else allow).
    - Read-only / unparseable (no targets, not uncertain) -> None (allow).
    - Any exception -> None (fail-open).
    """
    try:
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str) or not command:
            return None
        if not terminal_guard_enabled():
            return None

        targets, uncertain = _extract_terminal_write_targets(command)
        base = _terminal_base(args, task_id, working_dir_root)

        # Tier 2: write intent detected but the target path is uncertain.
        if uncertain:
            base_dir = _terminal_base_dir(base)
            digest = hashlib.sha256(
                (command + "\x00" + base_dir).encode("utf-8")
            ).hexdigest()
            rule_key = "workspace-guard:terminal-write:" + digest[:12]
            message = (
                "[workspace-guard] WARNING: This terminal command appears "
                "to write files, but the target path is uncertain:\n"
                "\n"
                "Command: %s\n"
                "Directory: %s\n"
                "\n"
                "Please confirm you intend to run this command here. "
                "Choosing [a]lways approves only this exact command run "
                "from this directory; the same command from another "
                "directory will prompt again." % (command, base_dir)
            )
            return {"action": "approve", "message": message, "rule_key": rule_key}

        # Tier 1 / Tier 3: classify each extracted target (strictest wins).
        approve_result = None
        for target in targets:
            abs_target = _resolve_terminal_target(target, base)
            normalized = normalize_target(abs_target, working_dir_root)
            result = classify_target(normalized, working_dir_root, exempt_paths)
            if result is None:
                continue
            if result.get("action") == "block":
                # Block outranks any approve (spec 5.3 step 6). Pass the
                # classify_target block dict through unchanged (its message
                # already carries the write_file remediation guidance).
                return result
            if result.get("action") == "approve":
                approve_result = result
        return approve_result
    except Exception as exc:
        logger.debug(
            "workspace-guard: terminal guard error (fail-open): %s", exc
        )
        return None
