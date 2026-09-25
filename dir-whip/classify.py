"""Unified verdict chain: T0-T4 classify_target + shared evaluation + block-message assembly (spec 5.3, spec 5.18).

THE single classification definition: the guard front layer, the audit
diff and the session-dir gates all consume classify_target through
set_classifier injection (ADR-0007). Scope-first: T0 out-of-root is
ALWAYS external-write, then T1 runtime allowlist > T2 config allowlist
> T3 Session Directory > T4 block (root included); no approve tier.
evaluate_target is the shared resolve -> normalize -> classify ->
session-dir gate -> emit -> block wrapper; pure decision layer.

Layer: core
Refs: spec 5.3, spec 5.10, spec 5.18
Key exports:
  - classify_target -- unified T0-T4 verdict chain (single definition; front + audit layers share).
  - evaluate_target -- shared target evaluation: resolve -> normalize -> classify -> session-dir gate -> emit -> block.
  - session_cwd -- session CWD accessor for relative-target resolution (terminal consumer).
  - parsed_allowlist_raw -- fail-closed raw-allowlist parser (guard consumer).
"""

import logging
import os

from . import state

# Unified allowlist helpers (structured mapping)
from .allowlist import is_allowlist_dir, is_allowlist_file, parse_allowlist

from .runtime_allowlist import is_runtime_allowlisted

from .events import (
    RULE_KEY_ALLOWED_FILE,
    RULE_KEY_EXTERNAL_WRITE,
    RULE_KEY_NON_SESSION_DIR,
    RULE_KEY_ROOT_FILE,
    RULE_KEY_RUNTIME_ALLOWLIST,
    RULE_KEY_SESSION_DIR,
    RULE_KEY_TIER0_ALLOWLIST,
    emit,
)

# Message templates live in the core leaf messages.py; same-name aliases
# keep classify.* call sites and test import paths unchanged.
from .messages import (
    BLOCK_MESSAGE_ALLOWLIST_HINT_LINE,
    BLOCK_MESSAGE_FIX_LINE_TEMPLATE,
    BLOCK_MESSAGE_HEADER_LINE,
    BLOCK_MESSAGE_NEXT_LINE,
    BLOCK_MESSAGE_REASON_LINE,
    BLOCK_MESSAGE_SUBAGENT_FIX_LINE,
    BLOCK_MESSAGE_SUBAGENT_NEXT_LINE,
    BLOCK_MESSAGE_SUBAGENT_REASON_LINE,
    BLOCK_MESSAGE_UNIQUENESS_LINE,
)

from .paths import (
    is_absolute_any,
    is_inside_session_dir,
    normalize_target,
    within_working_dir,
)

from . import session_dirs

logger = logging.getLogger("dir-whip")


# ---------------------------------------------------------------- Target resolution (spec 5.3 step 4)

def session_cwd(task_id):
    """Session CWD for relative-target resolution (guarded; None when
    unavailable). Tests inject a fake via state.session.session_cwd_fn.
    """
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

    cwd = session_cwd(task_id)
    if not cwd:
        logger.debug(
            "dir-whip: session CWD unrecorded for task %r, resolving "
            "relative target against working_dir_root", task_id
        )
    return os.path.join(cwd or working_dir_root, target)


def _resolve_terminal_target(target, working_dir_root):
    """Resolve a terminal write target against the relative-target base."""
    if is_absolute_any(target):
        return target
    return os.path.join(working_dir_root, target)


# ---------------------------------------------------------------- Allowlist parsing

def parsed_allowlist_raw(raw):
    """Parse raw allowlist value into {files, dirs} via allowlist module."""
    try:
        return parse_allowlist(raw)
    except Exception:
        return {"files": set(), "dirs": set()}


def _resolve_parsed_allowlist(allowlist):
    """Interpret the allowlist argument (structured model).

    - dict with files/dirs keys -> parsed mapping (raw config value or
      an already-parsed dict; both carry the same keys).
    - anything else (legacy flat list, None) -> parse_allowlist
      (fail-closed: legacy values yield empty sets).
    """
    try:
        if isinstance(allowlist, dict) and "files" in allowlist and "dirs" in allowlist:
            return allowlist
        return parsed_allowlist_raw(allowlist)
    except Exception:
        return {"files": set(), "dirs": set()}


# ---------------------------------------------------------------- Verdict helpers (spec 5.13)

def _outcome_reason(outcome):
    """Short reason string for a verdict event."""
    if outcome == "external-write":
        return "target outside working_dir_root"
    return None


# ---------------------------------------------------------------- Block message assembly (spec 5.3)

def _orphan_move_line(target, working_dir_root):
    """Conditional orphan repair line (spec 5.3).

    When the target's FIRST segment under the working root exists on disk
    as a directory with a non-compliant name (an orphan candidate),
    return the executable relocation line -- run the script (the fix
    block's session_dirs.script_invocation_line output) THEN move the
    orphan into the created session dir's Outputs/. None otherwise
    (absent dir / compliant name / the root target itself).
    """
    try:
        rel = os.path.relpath(str(target), str(working_dir_root))
        first = rel.replace("\\", "/").split("/")[0]
        if not first or first == ".":
            return None
        first_path = os.path.join(str(working_dir_root), first)
        if not os.path.isdir(first_path):
            return None
        if is_inside_session_dir(first_path, str(working_dir_root)):
            return None
        return 'mv "%s/%s" "<session_dir>/Outputs/"' % (
            str(working_dir_root).replace("\\", "/"), first
        )
    except Exception:
        return None


def _block_message(target, working_dir_root, is_subagent=False):
    """Exact block message (spec 5.3).

    The command line is built by the shared session_dirs builder; the
    uniqueness line is appended to both top-level variants, and a
    conditional orphan move line follows when the target's top-level
    directory already exists non-compliant. Subagent variant: the fix
    line is replaced by parent-target guidance -- subagents never create
    session directories; no uniqueness / move lines.
    """
    target_fwd = str(target).replace("\\", "/")
    if is_subagent:
        fix_line = BLOCK_MESSAGE_SUBAGENT_FIX_LINE
        post_lines = ""
        reason_line = BLOCK_MESSAGE_SUBAGENT_REASON_LINE
        next_line = BLOCK_MESSAGE_SUBAGENT_NEXT_LINE
    else:
        fix_line = (
            BLOCK_MESSAGE_FIX_LINE_TEMPLATE
            % session_dirs.script_invocation_line(
                "<task_name>", working_dir_root
            )
        )
        post_lines = BLOCK_MESSAGE_UNIQUENESS_LINE
        rename_line = _orphan_move_line(target, working_dir_root)
        if rename_line:
            post_lines += "\n" + rename_line
        reason_line = BLOCK_MESSAGE_REASON_LINE
        next_line = BLOCK_MESSAGE_NEXT_LINE
    return "\n".join(
        (
            BLOCK_MESSAGE_HEADER_LINE,
            "Target: %s" % target_fwd,
            fix_line + post_lines,
            BLOCK_MESSAGE_ALLOWLIST_HINT_LINE,
            reason_line,
            next_line,
        )
    )


# ---------------------------------------------------------------- Verdict chain (spec 5.3 step 6)

def classify_target(target, working_dir_root, allowlist=None, is_subagent=False,
                    honor_runtime_allowlist=True):
    """Classify a single normalized absolute target (spec 5.3 step 6).

    Returns a verdict dict:
      {"outcome": "allow", "rule_key": ...}                      -> allow
      {"outcome": "external-write", "rule_key": RULE_KEY_EXTERNAL_WRITE} -> allow + log
      {"outcome": "block", "rule_key": ..., "message": ...}      -> block

    Scope first: T0 outside working_dir_root (incl. sibling profile
    dirs) -> external-write ALWAYS (a runtime entry can never mask the
    signal); then, inside the root, on-the-spot grant beats persistent
    config beats session structure: T1 runtime allowlist, T2 config
    allowlist (dirs subtree / root-level file; dual rule_keys kept), T3
    valid Session Directory, T4 BLOCK (incl. the root itself: rel == "."
    -> root-file). There is NO approve tier; casefold handling is
    delegated to the allowlist module.

    honor_runtime_allowlist: when False the runtime-allowlist check is
    skipped entirely (config-only judgment -- allowlist files/dirs +
    session-dir containment). Default True preserves the guard/diff
    behavior; the settlement re-scan passes False so a runtime exemption
    never settles a recorded violation (prospective-only semantics).
    """
    # Resolve parsed allowlist: prefer passed allowlist, else fresh load.
    parsed = _resolve_parsed_allowlist(allowlist)

    # T0: scope first -- outside-root is ALWAYS external-write
    if not within_working_dir(target, working_dir_root):
        return {"outcome": "external-write", "rule_key": RULE_KEY_EXTERNAL_WRITE}

    # T1: runtime allowlist (strict subtree of root gating)
    if honor_runtime_allowlist and is_runtime_allowlisted(target):
        return {"outcome": "allow", "rule_key": RULE_KEY_RUNTIME_ALLOWLIST}

    # T2: config allowlist -- dirs subtree (dual rule_keys kept)
    if is_allowlist_dir(target, working_dir_root, parsed):
        return {"outcome": "allow", "rule_key": RULE_KEY_TIER0_ALLOWLIST}

    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        # Mixed drive/UNC pair on Windows: cannot relate -> external.
        return {"outcome": "external-write", "rule_key": RULE_KEY_EXTERNAL_WRITE}
    rel_fwd = rel.replace("\\", "/")
    # T2 root-level file (rel == "." never reaches the file check: the
    # root itself falls through to T4).
    if rel != "." and "/" not in rel_fwd:
        base = os.path.basename(target)
        if is_allowlist_file(base, parsed):
            return {"outcome": "allow", "rule_key": RULE_KEY_ALLOWED_FILE}

    # T3: session dir
    if is_inside_session_dir(target, working_dir_root):
        return {"outcome": "allow", "rule_key": RULE_KEY_SESSION_DIR}

    # T4: block (incl. root itself: rel == "." -> root-file)
    rule_key = RULE_KEY_ROOT_FILE if "/" not in rel_fwd else RULE_KEY_NON_SESSION_DIR
    return {
        "outcome": "block",
        "rule_key": rule_key,
        "message": _block_message(target, working_dir_root, is_subagent),
    }


# ---------------------------------------------------------------- Shared evaluation (spec 5.3 step 4-6)

def evaluate_target(target, tool_name, working_dir_root, allowlist,
                    is_subagent, session_id, is_terminal,
                    task_id=None, terminal_working_dir_root=None,
                    rule_key=None, tokens=None):
    """Evaluate one write target through the shared chain (spec 5.3 step 4-6).

    Shared shape: resolve -> normalize -> classify -> session-dir gate
    -> emit -> block. is_terminal is the only loop difference:
    terminal=True exempts device paths BEFORE normalization, emits the
    extracted rule_key (terminal-touch / -redirect / -cp-mv), resolves
    through the terminal working_dir_root (workdir arg -> session CWD ->
    working_dir_root) and rides the raw command tokens for the mv-source
    lookup; terminal=False (write_file / patch) emits the classify
    rule_key through the session CWD chain.

    The SINGLE session-dir-limit enforcement point sits between classify
    and emit: session_dirs.guard_create no-ops for non-session-dir
    rule_keys (T1/T2 exempt by structure), binds a first creation,
    transfers an mv rename of the bound dir, or overrides the allow with
    a block dict (the block event is emitted inside the gate). Returns
    the block dict on block, else None; caller loops keep first-block-wins
    ordering.
    """
    if is_terminal:
        # Device paths are exempt BEFORE normalization: no verdict/stats
        # event, no drive-inherited path fabrication. Function-local
        # import = cycle break (terminal statically consumes this chain).
        from .terminal import is_device_path
        if is_device_path(target):
            return None
        abs_target = _resolve_terminal_target(
            target, terminal_working_dir_root
        )
    else:
        abs_target = _resolve_target(target, task_id, working_dir_root)
    normalized = normalize_target(abs_target, working_dir_root)
    verdict = classify_target(normalized, working_dir_root, allowlist, is_subagent)
    limit_block = session_dirs.guard_create(
        verdict, normalized, working_dir_root, session_id, is_subagent,
        tool_name=tool_name, target=target, tokens=tokens,
    )
    if limit_block:
        return limit_block
    emit_rule_key = rule_key if is_terminal else verdict["rule_key"]
    if verdict["outcome"] == "block":
        reason = (
            "terminal write target blocked" if is_terminal
            else "write blocked by guard rule"
        )
        emit(
            "block", tool_name, emit_rule_key, normalized, reason,
            session_id, is_subagent,
        )
        return {"action": "block", "message": verdict["message"]}
    emit(
        verdict["outcome"], tool_name, emit_rule_key, normalized,
        _outcome_reason(verdict["outcome"]), session_id, is_subagent,
    )
    return None


__all__ = [
    "classify_target",
    "evaluate_target",
    "session_cwd",
    "parsed_allowlist_raw",
]
