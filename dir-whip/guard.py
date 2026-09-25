"""Guard entry: pre-tool-call decision chain + write-path dispatch + discipline/approval/observability surface (spec 5.3, spec 5.12; the classify chain split to classify.py and terminal interception homed in terminal.py -- SCR-055 R6, terminal module split reverted at SCR-056 R1b).

Pure decision layer: no host imports, no hook registration (the __init__.py assembly layer owns hooks and fail-open); depends on the lower layers state/config/events/messages plus the sanctioned import-back of subagents/audit/audit_prompts; the verdict chain lives in classify.py (classify_target / evaluate_target) and the terminal loop in terminal.py (guard_terminal). Extracted from dir_whip.py (task 31.13). Unified allowlist model per spec v2.6 B2.

Layer: core
Refs: spec 5.3, spec 5.4, spec 5.12, spec 5.13, spec 5.18, spec v2.6 B2, SCR-050, SCR-055 R6
Key exports:
  - guard -- pre-tool-call decision chain; None = allow, a block dict = block.
  - discipline_applies -- delegation alias (canonical home session_start.py, SCR-050 v3 R6.2); True = inject the session-start reminder.
  - project_exemption_applies -- delegation alias (canonical home session_start.py, SCR-050 v3 R6.2); True = CWD under an active host project folder.
  - extract_target_paths -- write_file / patch target path(s); empty list when absent.
  - reset_fail_open_flag -- reset the one-time fail-open warning flag.
  - resolved_config -- cached (working_dir_root, allowlist); (None, []) on failure.
  - approval_granted -- host approval choice -> granted/denied (SCR-050 v3 R6.1 public; consumer: the assembly approval observer).
"""

import re

from . import state

from .audit import pre_snapshot
from .audit_prompts import gate_block, gate_unresolved

from .classify import evaluate_target, parsed_allowlist_raw

from .config import get_cached_config

from .events import RULE_KEY_FAIL_OPEN, emit

# Message templates: centralized in the core leaf module messages.py
# (spec 5.20, SCR-047 R1, ADR-0014); same-name aliases keep every
# guard.* call site and test import path unchanged.
from .messages import FAIL_OPEN_WARNING_MESSAGE

from . import subagents

from .terminal import guard_terminal

INTERCEPTED_TOOLS = ("write_file", "patch", "terminal")
PATCH_FILE_RE = re.compile(r"^\*\*\* Update File:\s*(.+)$", re.MULTILINE)

# Spec 5.12 / 5.4 message constants live in messages.py (spec 5.20,
# SCR-047 R1); the same-name imports above are the aliases.

def discipline_applies(cwd, working_dir_root):
    """Conditional-injection predicate (spec 5.4, v2.7 R2).

    SCR-050 v3 R6.2 (spec 5.1 v2.19): the canonical home is
    session_start.py (single-consumer move); this same-name delegation
    alias keeps guard.* / test import paths unchanged. Lazy import =
    the documented cycle-break idiom (state.py precedent): guard has
    NO module-level session_start edge (session_start imports guard for
    reset_fail_open_flag / resolved_config).
    """
    from .session_start import discipline_applies as _canonical
    return _canonical(cwd, working_dir_root)


def project_exemption_applies(cwd, folders):
    """Project-mode injection exemption predicate (R7, spec 3.2 Layer 0).

    SCR-050 v3 R6.2 (spec 5.1 v2.19): delegation alias -- canonical home
    session_start.py; same fail-open semantics (missing cwd / folders / any
    error -> False = no exemption).
    """
    from .session_start import project_exemption_applies as _canonical
    return _canonical(cwd, folders)

# Spec 5.13 D2: host approval choices that count as granted (verified
# against the local hermes-agent approval.py choice vocabulary).
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
    # 5.13: verdicts split by is_subagent -child membership in the
    # child_session_ids set (5.4) implies a subagent write.
    if not is_subagent and session_id and subagents._is_subagent_session(session_id):
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
    unresolved = gate_unresolved(session_id, working_dir_root,
                                        allowlist)
    if unresolved:
        return gate_block(tool_name, session_id, is_subagent,
                                 working_dir_root, unresolved)

    if tool_name == "terminal":
        result = guard_terminal(
            args, task_id, working_dir_root, allowlist, is_subagent, session_id
        )
        # 5.18 audit pre-snapshot runs ONLY when the front layer decided
        # to allow -- this covers every command-will-execute path (heredoc
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
    """Reset the one-time fail-open warning flag (26.7's on_session_start
    calls this; tests use it too)."""
    state.session.fail_open_warned = False


# ---------------------------------------------------------------- Observation helpers

def resolved_config():
    """Cached (working_dir_root, allowlist); (None, []) on failure."""
    try:
        return get_cached_config(_get_ctx())
    except Exception:
        return (None, [])


def approval_granted(choice):
    """Map host approval choices to granted/denied (5.13 D2)."""
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


# Single authoritative names (SCR-052 R1 alias convergence; SCR-055 R6: the
# classify chain moved to classify.py and terminal interception to
# terminal.py -- the defs above carry this module's public names).

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
