"""Verdict chain: guard decision, classification, terminal interception
(spec 5.3/5.10/5.12) -the plugin's core guard logic.

Pure decision layer: no host imports, no hook registration (the assembly
layer in __init__.py owns hooks and fail-open). Depends on the lower
layers paths/terminal/events/state/config + sessions/audit (sanctioned
import-back of pre-existing dependencies). Extracted from dir_whip.py
(task 31.13). Spec v2.6 B2: unified allowlist .
"""

import logging
import os
import re

try:
    from . import state
except ImportError:
    import state

try:
    from .audit import (
        _audit_gate_block,
        _audit_gate_unresolved,
        _audit_pre_snapshot,
    )
except ImportError:
    from audit import (
        _audit_gate_block,
        _audit_gate_unresolved,
        _audit_pre_snapshot,
    )

try:
    from .config import (
        get_cached_config,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        terminal_guard_enabled,
    )
except ImportError:
    from config import (
        get_cached_config,
        is_inside_session_dir,
        is_runtime_allowlisted,
        load_guard_config,
        terminal_guard_enabled,
    )

try:
    from .events import _verdict_reason, emit
except ImportError:
    from events import _verdict_reason, emit

try:
    from .paths import is_absolute_any, normalize_target, within_working_dir
except ImportError:
    from paths import is_absolute_any, normalize_target, within_working_dir

try:
    from .sessions import _is_child_session
except ImportError:
    from sessions import _is_child_session

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

# Unified allowlist helpers (spec v2.6 B2)
try:
    from .allowlist import is_allowlist_file, is_allowlist_prefix, parse_allowlist
except ImportError:
    try:
        from allowlist import is_allowlist_file, is_allowlist_prefix, parse_allowlist  # type: ignore
    except ImportError:
        # Fallback stubs (should never happen in repo)
        def parse_allowlist(raw):  # type: ignore
            return {"files": set(), "prefixes": set()}
        def is_allowlist_file(name, parsed):  # type: ignore
            return False
        def is_allowlist_prefix(path, parsed):  # type: ignore
            return False

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

# Spec 3.7/5.17: always-on discipline prompt (<=200 chars, four elements:
# classify before write / session-dir writes / no root writes / when
# blocked). The full C6 template is delivered by the block message, NOT
# by this prompt.
DISCIPLINE_PROMPT = (
    "[dir-whip] 写前分类：首写前必分类并创建会话目录，任何创建或写入前先说明目标类别（会话目录 / 根白名单文件 / 外部路径）。"
    "会话目录落盘：工作目录内写入必须落入会话目录 Outputs/ 或 .tmp/。"
    "根目录禁写：工作目录根只允许白名单文件、会话目录和 .hermes/。"
    "被拦截时：遵循拦截消息创建会话目录后重试，回复 [Reason]/[Next]，不要重试同一路径。"
)


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
    # 5.13: verdicts split by is_subagent -child membership in the
    # child_session_ids set (5.4) implies a subagent write.
    if not is_subagent and session_id and _is_child_session(session_id):
        is_subagent = True
    ctx = _get_ctx()
    working_dir_root, allowlist = get_cached_config(ctx)

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
                                        allowlist)
    if unresolved:
        return _audit_gate_block(tool_name, session_id, is_subagent,
                                 working_dir_root, unresolved)

    if tool_name == "terminal":
        result = _guard_terminal(
            args, task_id, working_dir_root, allowlist, is_subagent, session_id
        )
        # 5.18 audit pre-snapshot runs ONLY when the front layer decided
        # to allow -- this covers every command-will-execute path (heredoc
        # demotion, guard-disabled, device exemption, uncertain tier);
        # blocked calls never snapshot (nothing to pair at post).
        if result is None:
            _audit_pre_snapshot(session_id, task_id, working_dir_root, allowlist)
        return result

    target_paths = _extract_target_paths(tool_name, args)
    if not target_paths:
        return None

    for target in target_paths:
        abs_target = _resolve_target(target, task_id, working_dir_root)
        normalized = normalize_target(abs_target, working_dir_root)
        verdict = classify_target(normalized, working_dir_root, allowlist, is_subagent)
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


# ---------------------------------------------------------------- Observation helpers

def _resolved_config():
    """Cached (working_dir_root, allowlist); (None, []) on failure."""
    try:
        return get_cached_config(_get_ctx())
    except Exception:
        return (None, [])


def _approval_granted(choice):
    """Map host approval choices to granted/denied (5.13 D2)."""
    return str(choice or "").strip().lower() in _APPROVAL_GRANTED_CHOICES


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


def _parsed_allowlist_raw(raw):
    """Parse raw allowlist list into {files, prefixes} via allowlist module."""
    try:
        return parse_allowlist(raw)
    except Exception:
        return {"files": set(), "prefixes": set()}


def _parsed_allowlist():
    """Load and parse allowlist from dir-whip-config.yaml (fresh read)."""
    try:
        raw = load_guard_config().get("allowlist") or []
        return _parsed_allowlist_raw(raw)
    except Exception:
        return {"files": set(), "prefixes": set()}


def classify_target(target, working_dir_root, allowlist=None, is_subagent=False):
    """Classify a single normalized absolute target (spec 5.3 step 6, v2.6 B2).

    Returns a verdict dict:
      {"outcome": "allow", "rule_key": ...}                      -> allow
      {"outcome": "external-write", "rule_key": "external-write"} -> allow + log
      {"outcome": "block", "rule_key": ..., "message": ...}      -> block

    Order: Tier 0 (allowlist prefix + runtime allowlist) first; under
    working_dir_root -> allowlist file at root, then valid Session
    Directory, then BLOCK; outside working_dir_root (incl. sibling profile
    dirs) -> external-write. There is NO approve tier. Casefold handling
    delegated to allowlist module.
    """
    # Resolve parsed allowlist: prefer passed allowlist, else fresh load.
    if isinstance(allowlist, dict) and "files" in allowlist and "prefixes" in allowlist:
        parsed = allowlist
    elif allowlist is not None:
        # allowlist is expected to be list of discriminated strings (cached)
        parsed = _parsed_allowlist_raw(allowlist)
    else:
        parsed = _parsed_allowlist()

    # Tier 0: allowlist prefix OR runtime allowlist -> ALLOW
    if is_allowlist_prefix(target, parsed):
        return {"outcome": "allow", "rule_key": "tier0-allowlist"}
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
        # Root file: allowlist file check (spec 5.6 B2)
        base = os.path.basename(target)
        if is_allowlist_file(base, parsed):
            return {"outcome": "allow", "rule_key": "allowed-file"}

    if is_inside_session_dir(target, working_dir_root):
        return {"outcome": "allow", "rule_key": "session-dir"}

    rule_key = "root-file" if "/" not in rel_fwd else "non-session-dir"
    return {
        "outcome": "block",
        "rule_key": rule_key,
        "message": _block_message(target, working_dir_root, is_subagent),
    }


def _block_message(target, working_dir_root, is_subagent=False):
    """Exact block message (spec 5.3; C6-aligned, v2.6 B2).

    Subagent variant: the fix line is replaced by the parent-target
    guidance -- subagents never create session directories.
    """
    target_fwd = str(target).replace("\\", "/")
    wdr_fwd = str(working_dir_root).replace("\\", "/")
    if is_subagent:
        fix_line = "Fix: write to the target directory passed by the parent agent."
    else:
        # D11: scripts path precomputed at register (P6, 31.13):
        # <plugin_dir>/skills/workspace-organization/scripts; falls back
        # to the __file__-based derivation for unregistered direct calls.
        scripts_path = state.session.script_resolver_path
        if not scripts_path:
            scripts_path = os.path.normpath(
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "skills", "workspace-organization", "scripts",
                )
            )
        scripts_path = scripts_path.replace("\\", "/")
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
        "If this is a project directory, add it to allowlist as prefix:<abs-path> in "
        "HERMES_HOME/dir-whip/dir-whip-config.yaml (e.g. prefix:E:/HermesWorkspace/learn/projects/foo)\n"
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


def _guard_terminal(args, task_id, working_dir_root, allowlist,
                    is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers, v2.6 B2).

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
            verdict = classify_target(normalized, working_dir_root, allowlist, is_subagent)
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
