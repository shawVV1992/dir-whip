"""Guard entry: pre-tool-call decision chain + write-path dispatch (spec 5.3, spec 5.12).

Pure decision layer: no host imports, no hook registration (the assembly
layer owns hooks and fail-open); depends on the lower layers
state/config/events/messages plus the sanctioned import-back of
subagents/audit/audit_prompts. The verdict chain lives in classify.py
and terminal handling in terminal.py.

Layer: core
Refs: spec 5.3, spec 5.4, spec 5.12
Key exports:
  - guard -- pre-tool-call decision chain; None = allow, a block dict = block.
  - discipline_applies -- delegation alias (canonical home session_start.py); True = inject the session-start reminder.
  - project_exemption_applies -- delegation alias (canonical home session_start.py); True = CWD under an active host project folder.
  - extract_target_paths -- write_file / patch target path(s); empty list when absent.
  - reset_fail_open_flag -- reset the one-time fail-open warning flag.
  - resolved_config -- cached (working_dir_root, allowlist); (None, []) on failure.
  - approval_granted -- host approval choice -> granted/denied (consumer: the assembly approval observer).
"""

import re

from . import state

from .audit import pre_snapshot
from .audit_prompts import gate_block, gate_unresolved

from .classify import evaluate_target, parsed_allowlist_raw

from .config import get_cached_config

from .events import RULE_KEY_FAIL_OPEN, emit

# Message templates live in the core leaf messages.py; same-name aliases
# keep guard.* call sites and test import paths unchanged.
from .messages import FAIL_OPEN_WARNING_MESSAGE

from . import subagents

from .terminal import guard_terminal

INTERCEPTED_TOOLS = ("write_file", "patch", "terminal")
PATCH_FILE_RE = re.compile(r"^\*\*\* Update File:\s*(.+)$", re.MULTILINE)


def discipline_applies(cwd, working_dir_root):
    """Conditional-injection predicate (spec 5.4).

    Delegation alias with canonical home session_start.py; the same
    name keeps guard.* / test import paths unchanged. Lazy import
    breaks the cycle: guard has NO module-level session_start edge
    (session_start imports guard for reset_fail_open_flag /
    resolved_config).
    """
    from .session_start import discipline_applies as _canonical
    return _canonical(cwd, working_dir_root)


def project_exemption_applies(cwd, folders):
    """Project-mode injection exemption predicate (spec 3.2 Layer 0).

    Delegation alias with canonical home session_start.py; fail-open
    semantics (missing cwd / folders or any error -> False = no
    exemption).
    """
    from .session_start import project_exemption_applies as _canonical
    return _canonical(cwd, folders)

# Host approval choices that count as granted (host approval.py vocabulary).
_APPROVAL_GRANTED_CHOICES = frozenset(
    ("approve", "always", "session", "granted", "allow", "smart_approve")
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
    # Verdicts split by is_subagent: membership in the child_session_ids
    # set implies a subagent write.
    if not is_subagent and session_id and subagents._is_subagent_session(session_id):
        is_subagent = True
    ctx = _get_ctx()
    working_dir_root, allowlist = get_cached_config(ctx)

    # Guard-disabled shortcut: one-time warning + allow.
    if working_dir_root is None:
        _warn_fail_open_once(ctx, tool_name, session_id, is_subagent)
        return None

    # L3 settlement gate: an unresolved pending violation latches the
    # NEXT write-class call until remediation. Runs BEFORE target
    # extraction / classification / the audit pre snapshot; a gated call
    # never snapshots (the command did not run). Fail-open: a gate-side
    # error allows the call, a failed re-scan keeps the latch.
    unresolved = gate_unresolved(session_id, working_dir_root,
                                        allowlist)
    if unresolved:
        return gate_block(tool_name, session_id, is_subagent,
                                 working_dir_root, unresolved)

    if tool_name == "terminal":
        result = guard_terminal(
            args, task_id, working_dir_root, allowlist, is_subagent, session_id
        )
        # Audit pre-snapshot runs ONLY when the front layer decided to
        # allow -- this covers every command-will-execute path (heredoc
        # demotion, guard-disabled, device exemption, uncertain tier);
        # blocked calls never snapshot (nothing to pair at post).
        if result is None:
            pre_snapshot(
                session_id, task_id, working_dir_root,
                parsed_allowlist_raw(allowlist),
            )
        return result

    target_paths = extract_target_paths(tool_name, args)
    if not target_paths:
        return None

    for target in target_paths:
        act = evaluate_target(
            target, tool_name, working_dir_root, allowlist, is_subagent,
            session_id, is_terminal=False, task_id=task_id,
        )
        if act:
            return act
    return None


def _get_ctx():
    """Return the registered ctx (tests set state.session.registered_ctx)."""
    return state.session.registered_ctx


# ---------------------------------------------------------------- Fail-open warning (spec 5.12)

def _warn_fail_open_once(ctx, tool_name, session_id, is_subagent):
    """Inject the one-time fail-open warning + record a fail-open verdict.

    Fires at most once per session (module flag; reset by
    reset_fail_open_flag). Gateway degrade: inject_message unavailable or
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
        "fail-open", tool_name, RULE_KEY_FAIL_OPEN, None,
        "working_dir_root unresolved", session_id, is_subagent,
    )


def reset_fail_open_flag():
    """Reset the one-time fail-open warning flag (on_session_start and
    tests call this)."""
    state.session.fail_open_warned = False


# ---------------------------------------------------------------- Observation helpers

def resolved_config():
    """Cached (working_dir_root, allowlist); (None, []) on failure."""
    try:
        return get_cached_config(_get_ctx())
    except Exception:
        return (None, [])


def approval_granted(choice):
    """Map host approval choices to granted/denied."""
    return str(choice or "").strip().lower() in _APPROVAL_GRANTED_CHOICES


# ---------------------------------------------------------------- Target extraction (spec 5.3 step 3)

def extract_target_paths(tool_name, args):
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


__all__ = [
    "guard",
    "discipline_applies",
    "project_exemption_applies",
    "approval_granted",
    "extract_target_paths",
    "reset_fail_open_flag",
    "resolved_config",
    "FAIL_OPEN_WARNING_MESSAGE",
]
