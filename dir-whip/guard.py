"""Verdict chain: guard / classify_target / discipline_applies / terminal interception -- the plugin's core guard logic (spec 5.3, spec 5.10, spec 5.12).

Pure decision layer: no host imports, no hook registration (the __init__.py assembly layer owns hooks and fail-open); depends on the lower layers paths/terminal/events/state/config plus the sanctioned import-back of subagents/audit/audit_prompts; extracted from dir_whip.py (task 31.13). Unified allowlist model per spec v2.6 B2.

Layer: core
Refs: spec 5.3, spec 5.10, spec 5.12, spec v2.6 B2, SCR-050
Key exports:
  - guard -- pre-tool-call decision chain; None = allow, a block dict = block.
  - classify_target -- single-target classification: allow / external-write / block.
  - discipline_applies -- delegation alias (canonical home session_start.py, SCR-050 v3 R6.2); True = inject the session-start reminder.
  - project_exemption_applies -- delegation alias (canonical home session_start.py, SCR-050 v3 R6.2); True = CWD under an active host project folder.
  - extract_target_paths -- write_file / patch target path(s); empty list when absent.
  - reset_fail_open_flag -- reset the one-time fail-open warning flag.
  - resolved_config -- cached (working_dir_root, allowlist); (None, []) on failure.
  - approval_granted -- host approval choice -> granted/denied (SCR-050 v3 R6.1 public; consumer: the assembly approval observer).
"""

import logging
import os
import re

from . import state

from .audit import pre_snapshot
from .audit_prompts import gate_block, gate_unresolved

from .config import (
    get_cached_config,
    is_runtime_allowlisted,
    load_guard_config,
)

from .events import (
    RULE_KEY_ALLOWED_FILE,
    RULE_KEY_EXTERNAL_WRITE,
    RULE_KEY_FAIL_OPEN,
    RULE_KEY_NON_SESSION_DIR,
    RULE_KEY_ROOT_FILE,
    RULE_KEY_RUNTIME_ALLOWLIST,
    RULE_KEY_SESSION_DIR,
    RULE_KEY_TIER0_ALLOWLIST,
    RULE_KEY_TERMINAL_WRITE_UNCERTAIN,
    emit,
)

# Message templates: centralized in the core leaf module messages.py
# (spec 5.20, SCR-047 R1, ADR-0014); same-name aliases keep every
# guard.* call site and test import path unchanged.
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
    FAIL_OPEN_WARNING_MESSAGE,
)

from .paths import (
    is_absolute_any,
    is_inside_session_dir,
    normalize_target,
    within_working_dir,
)

from . import session_dirs
from . import subagents

# SCR-050 v3 R6.1 (spec 5.1 v2.19 seam discipline): the lexer surface is
# consumed via its declared public names; the device-path exemption is a
# predicate (deep module: hide the data, expose the judgment).
from .terminal import (
    is_device_path,
    is_terminal_uncertain,
    terminal_block_targets,
    tokenize_command,
)

# Unified allowlist helpers (spec v2.7 R9 structured mapping)
from .allowlist import is_allowlist_dir, is_allowlist_file, parse_allowlist

logger = logging.getLogger("dir-whip")

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
        result = _guard_terminal(
            args, task_id, working_dir_root, allowlist, is_subagent, session_id
        )
        # 5.18 audit pre-snapshot runs ONLY when the front layer decided
        # to allow -- this covers every command-will-execute path (heredoc
        # demotion, guard-disabled, device exemption, uncertain tier);
        # blocked calls never snapshot (nothing to pair at post).
        if result is None:
            pre_snapshot(
                session_id, task_id, working_dir_root,
                _parsed_allowlist_raw(allowlist),
            )
        return result

    target_paths = extract_target_paths(tool_name, args)
    if not target_paths:
        return None

    for target in target_paths:
        act = _evaluate_target(
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


def _outcome_reason(outcome):
    """Short reason string for a verdict event (5.13).

    SCR-050 v3 R6.1: inlined from events._verdict_reason (single
    consumer -- dead-surface policy: move, don't proliferate).
    """
    if outcome == "external-write":
        return "target outside working_dir_root"
    return None


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

    session_cwd = _session_cwd(task_id)
    if not session_cwd:
        logger.debug(
            "dir-whip: session CWD unrecorded for task %r, resolving "
            "relative target against working_dir_root", task_id
        )
    return os.path.join(session_cwd or working_dir_root, target)


def _parsed_allowlist_raw(raw):
    """Parse raw allowlist value into {files, dirs} via allowlist module."""
    try:
        return parse_allowlist(raw)
    except Exception:
        return {"files": set(), "dirs": set()}


def _parsed_allowlist():
    """Load and parse allowlist from dir-whip-config.yaml (fresh read)."""
    try:
        raw = load_guard_config().get("allowlist") or []
        return _parsed_allowlist_raw(raw)
    except Exception:
        return {"files": set(), "dirs": set()}


def _resolve_parsed_allowlist(allowlist):
    """Interpret the allowlist argument (v2.7 structured model).

    - dict with files/dirs keys -> parsed mapping (raw config value or
      an already-parsed dict; both carry the same keys).
    - anything else (legacy flat list, None) -> parse_allowlist
      (fail-closed: legacy values yield empty sets).
    """
    try:
        if isinstance(allowlist, dict) and "files" in allowlist and "dirs" in allowlist:
            return allowlist
        return _parsed_allowlist_raw(allowlist)
    except Exception:
        return {"files": set(), "dirs": set()}


def classify_target(target, working_dir_root, allowlist=None, is_subagent=False,
                    honor_runtime_allowlist=True):
    """Classify a single normalized absolute target (spec 5.3 step 6, v2.7 R9).

    Returns a verdict dict:
      {"outcome": "allow", "rule_key": ...}                      -> allow
      {"outcome": "external-write", "rule_key": RULE_KEY_EXTERNAL_WRITE} -> allow + log
      {"outcome": "block", "rule_key": ..., "message": ...}      -> block

    Order (SCR-043 R1, spec 5.3 v2.11): scope first -- T0 outside
    working_dir_root (incl. sibling profile dirs) -> external-write
    ALWAYS (a runtime entry can no longer mask the signal); then, inside
    the root, on-the-spot grant beats persistent config beats session
    structure: T1 runtime allowlist, T2 config allowlist (dirs subtree /
    root-level file; dual rule_keys kept), T3 valid Session Directory,
    T4 BLOCK (incl. the root itself: rel == "." -> root-file). There is
    NO approve tier. Casefold handling delegated to allowlist module.

    honor_runtime_allowlist (SCR-041 R1, spec 5.18 v2.9): when False the
    runtime-allowlist check is skipped entirely (config-only judgment --
    allowlist files/dirs + session-dir containment). Default True
    preserves the guard/diff behavior exactly; the settlement re-scan
    (pending_violation_paths) passes False so a runtime exemption never
    settles a recorded violation (prospective-only semantics).
    """
    # Resolve parsed allowlist: prefer passed allowlist, else fresh load.
    parsed = _resolve_parsed_allowlist(allowlist)

    # T0: scope first (SCR-043 R1) -- outside-root is ALWAYS external-write
    if not within_working_dir(target, working_dir_root):
        return {"outcome": "external-write", "rule_key": RULE_KEY_EXTERNAL_WRITE}

    # T1: runtime allowlist (value domain = strict subtree of root, R3 gating)
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
    # root itself falls through to T4, SCR-043 R1/C3).
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


def _orphan_move_line(target, working_dir_root):
    """Conditional orphan repair line (spec 5.3 v2.12 R6, MSG-4/5).

    When the target's FIRST segment under the working root exists on
    disk as a directory with a non-compliant name (an orphan candidate,
    feedback/15 double-directory incident), return the executable
    relocation line -- run the script (the fix block's
    session_dirs.script_invocation_line output) THEN move the orphan
    into the created session dir's Outputs/. None otherwise (MSG-5:
    absent dir / compliant name; also for the root target itself).
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
    """Exact block message (spec 5.3; C6-aligned, v2.6 B2; v2.12 R6:
    the command line is built by the shared session_dirs builder (MB-2),
    the uniqueness line is appended to both top-level variants (MSG-3),
    and a conditional orphan move line follows when the target's
    top-level directory already exists non-compliant (MSG-4/5)).

    Subagent variant: the fix line is replaced by the parent-target
    guidance -- subagents never create session directories; no
    uniqueness / move lines (MSG-6).
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


def _terminal_base(args, task_id, working_dir_root):
    """Resolve the terminal relative-target base (spec 5.3 step 4).

    Chain: args["workdir"] -> get_session_cwd(task_id) -> working_dir_root.
    Never os.getcwd(). SCR-052 G2: the resolved base carries the
    working_dir_root name (the effective root for THIS terminal call).
    """
    working_dir_root = (
        (args.get("workdir") if isinstance(args, dict) else None)
        or _session_cwd(task_id)
        or working_dir_root
    )
    return working_dir_root


def _resolve_terminal_target(target, working_dir_root):
    """Resolve a terminal write target against the relative-target base."""
    if is_absolute_any(target):
        return target
    return os.path.join(working_dir_root, target)


def _evaluate_target(target, tool_name, working_dir_root, allowlist,
                     is_subagent, session_id, is_terminal,
                     task_id=None, terminal_working_dir_root=None,
                     rule_key=None, tokens=None):
    """Evaluate one write target through the shared chain (spec 5.3
    step 4-6; SCR-044 R2 convergence).

    Shared shape: resolve -> normalize -> classify -> session-dir gate
    -> emit -> block. is_terminal carries the only two loop differences
    (guard write loop vs terminal block-target loop):

    - is_terminal=True (terminal): device paths are exempt BEFORE
      normalization (4.3) and emit uses the EXTRACTED rule_key
      (terminal-touch / terminal-redirect / terminal-cp-mv, passed via
      rule_key); resolution goes through the terminal working_dir_root
      (the effective base for this call: workdir arg -> session CWD ->
      working_dir_root; SCR-052 G2 family name -- a distinguishable name
      is required because the chain's working_dir_root param is the
      config root); the raw command tokens ride along for the
      session-dir mv-source lookup.
    - is_terminal=False (write_file / patch): emit uses the classify
      rule_key (root-file / non-session-dir / session-dir /
      runtime-allowlist / external-write); resolution goes through the
      session CWD chain.

    SCR-044 R5 (spec 5.19): the SINGLE session-dir-limit enforcement
    point sits between classify and emit -- session_dirs.guard_create
    is a no-op for every non-session-dir rule_key (T1/T2 exempt by
    structure), binds a first creation, transfers an mv rename of the
    bound dir, or overrides the allow with a block dict (the block
    event is emitted inside the gate).

    Returns the block dict on block, else None; caller loops keep
    first-block-wins ordering.
    """
    if is_terminal:
        # 4.3 device paths are exempt BEFORE normalization: no
        # verdict/stats event, no drive-inherited path fabrication.
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


def _guard_terminal(args, task_id, working_dir_root, allowlist,
                    is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers, v2.6 B2).

    - Always on (5.10 v2.8 R7: the terminal_guard config key is removed;
      enforcement is unconditional, no switch).
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

        tokens = tokenize_command(command)
        if not tokens:
            return None
        terminal_working_dir_root = _terminal_base(
            args, task_id, working_dir_root
        )

        # SCR-044 R5 (spec 5.19): session-dir script gate BEFORE the
        # heredoc blanket demotion -- a second create_session_dir.py
        # attempt is blocked even in heredoc form (BLK-3); a first
        # attempt arms the pending_create marker that the audit
        # post-diff observer consumes (OB-1/OB-2).
        act = session_dirs.guard_script(
            tokens, working_dir_root, session_id, is_subagent,
        )
        if act:
            return act

        # 4.4 heredoc blanket demotion: never parse the body, never block.
        if "<<" in command:
            emit(
                "allow", "terminal", RULE_KEY_TERMINAL_WRITE_UNCERTAIN, None,
                "heredoc detected, blanket demotion", session_id, is_subagent,
            )
            return None

        for target, rule_key in terminal_block_targets(tokens):
            act = _evaluate_target(
                target, "terminal", working_dir_root, allowlist,
                is_subagent, session_id, is_terminal=True,
                terminal_working_dir_root=terminal_working_dir_root,
                rule_key=rule_key, tokens=tokens,
            )
            if act:
                return act

        if is_terminal_uncertain(tokens):
            emit(
                "allow", "terminal", RULE_KEY_TERMINAL_WRITE_UNCERTAIN, None,
                "write intent detected, target uncertain", session_id, is_subagent,
            )
            return None
        return None
    except Exception as exc:
        logger.debug("dir-whip: terminal guard error (fail-open): %s", exc)
        return None


# Single authoritative names (SCR-052 R1 alias convergence: the former
# module-tail extract_target_paths/reset_fail_open_flag/resolved_config/
# approval_granted alias lines are gone; the defs above carry the public
# names. Approval vocabulary went public at SCR-050 v3 R6.1, assembly
# consumer on_post_approval_response; spec 5.1 v2.19 seam discipline).

# SCR-050 v3 R6.1: declared public surface (AC-9). The discipline_applies
# / project_exemption_applies predicates move to session_start.py at R6.2
# (same-name aliases stay).
__all__ = [
    "guard",
    "classify_target",
    "discipline_applies",
    "project_exemption_applies",
    "approval_granted",
    "extract_target_paths",
    "reset_fail_open_flag",
    "resolved_config",
    "FAIL_OPEN_WARNING_MESSAGE",
]
