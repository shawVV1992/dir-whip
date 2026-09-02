"""Per-session unique Session Directory lifecycle (SCR-044 R5, spec 5.19)
+ the R7 on_session_start orphan scan (advisory).

Pure decision layer: statically imports state / sessions / config /
paths / events / terminal only -- NEVER audit or verdict (no import
cycle: audit imports this module for the post-diff binding observer).
No host imports (ADR-0007 core discipline). The R7 scan consumes the
classify chain through an injected slot (set_classifier, same
ADR-0007 pattern as audit) instead of importing verdict.

Slot model: one Session Directory per conversation. state.session_dirs
claims maps the owner session (sessions.owner_session: subagent ->
parent attribution, mirroring the audit pending propagation) to the
bound dir name (root-relative first segment, Windows-casefold
comparison); pending_create marks a script creation in flight.

Binding semantics (creation-count, user ruling 2026-09-01):

- Unified creation signal = the classify verdict is a session-dir ALLOW
  and the target's first-segment directory does not exist yet -- the
  action will CREATE it. Covers mkdir, write_file implicit parent
  creation, touch, redirect with no per-tool special-casing. Static
  vectors bind at guard time.
- Script vector (create_session_dir.py via the R1 predicate):
  guard_script arms pending_create; the audit post-diff callback
  observe_added binds the FIRST new compliant dir under the root and
  ALWAYS consumes the marker (a failed script leaves no ghost slot,
  OB-2; multiple additions bind the first, OB-3; non-compliant
  additions never bind, OB-4).
- A second creation while the slot is occupied blocks with rule_key
  session-dir-limit (a pending marker counts as the claim, BLK-4).
  Writing into an EXISTING other session dir passes without binding
  and consumes no slot (creation-count semantics, BND-5). mv renaming
  the bound dir to a new compliant name transfers the claim (the
  terminal_cp_mv_src helper re-derives the mv source, form b: MV-1 vs
  BLK-5).
- T1 runtime / T2 config allowlist verdicts never reach the gate (the
  single enforcement point hangs on the session-dir rule_key only,
  EX-1 / EX-2).

Messages: SESSION_DIR_LIMIT_BLOCK_MESSAGE (+ subagent variant) are
verbatim-locked templates; blocks emit through events with the
session-dir-limit rule_key, so stats accumulate via the setdefault
chain and the generic dir-whip:blocked bus fanout fires (the key is
deliberately NOT in events._BUS_SKIP_RULE_KEYS; the 7-emits manifest
surface stays unchanged).

Session-lifetime memory: cleared at every top-level session start
(CLR-1) and by state.reset_all (CLR-2); a restart loses it (accepted,
same class as the runtime allowlist).
"""

import logging
import os

logger = logging.getLogger("dir-whip")

from . import state

from .config import is_inside_session_dir

from .events import emit

from .paths import is_absolute_any, paths_equal

from .sessions import owner_session

from .terminal import is_session_dir_script, terminal_cp_mv_src

# Spec 5.19: rule_key of the per-session uniqueness block.
SESSION_DIR_LIMIT_RULE_KEY = "session-dir-limit"

# Spec 5.19 verbatim locks. <root>/<claim> are substituted at build
# time (forward-slash rendering, same message convention as verdict).
SESSION_DIR_LIMIT_BLOCK_MESSAGE = (
    "BLOCKED: One session directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "User-specified path -> dir_whip_allow_path first."
)

# Subagent variant (verdict subagent block-message convention): the
# escape lines are replaced by the parent-target guidance -- subagents
# never create session directories nor hold allow_path sanctions.
SESSION_DIR_LIMIT_SUBAGENT_MESSAGE = (
    "BLOCKED: One session directory per conversation.\n"
    "This conversation already uses: %(root)s/%(claim)s\n"
    "Write deliverables to %(claim)s/Outputs/ (scratch: %(claim)s/.tmp/).\n"
    "Fix: write to the target directory passed by the parent agent."
)


# ---------------------------------------------------------------- State access

def _owner(session_id):
    """Owner resolution (subagent -> parent attribution)."""
    return owner_session(session_id) or session_id


def _claim_of(owner):
    with state.session_dirs.lock:
        return state.session_dirs.claims.get(owner)


def _bind(owner, name):
    """First bind (idempotent: an existing claim is never overwritten)."""
    with state.session_dirs.lock:
        return state.session_dirs.claims.setdefault(owner, name)


def _rebind(owner, name):
    """Claim transfer (mv rename of the bound dir, MV-1)."""
    with state.session_dirs.lock:
        state.session_dirs.claims[owner] = name


def _slot_occupied(owner):
    """True when the conversation's slot is taken: a claim OR an
    in-flight script creation (the pending marker counts as the
    claim, BLK-4)."""
    with state.session_dirs.lock:
        return (
            owner in state.session_dirs.claims
            or owner in state.session_dirs.pending_create
        )


# ---------------------------------------------------------------- Pure helpers

def _same_name(a, b):
    """Session-dir name comparison: Windows casefold (BND-7).

    One-line delegate to paths.paths_equal (SCR-045 R7 single source);
    the None-guard stays (two Nones are NOT equal names).
    """
    if a is None or b is None:
        return False
    return paths_equal(a, b)


def _first_segment(normalized, working_dir_root):
    """Root-relative first path segment of a normalized absolute target
    (the claim value domain)."""
    rel = os.path.relpath(str(normalized), str(working_dir_root))
    return rel.replace("\\", "/").split("/")[0]


def _token_first_segment(token, working_dir_root):
    """Root-relative first segment of a raw command token: relative
    tokens contribute their leading segment directly; absolute tokens
    are related against the root. None when unrelatable."""
    tok = str(token).strip("\"'").replace("\\", "/")
    if is_absolute_any(tok):
        try:
            rel = os.path.relpath(tok, str(working_dir_root))
        except ValueError:
            return None
        tok = rel.replace("\\", "/")
    return tok.split("/")[0]


def _is_compliant(working_dir_root, name):
    """Compliant session-dir name check through the config kernel
    (ADR-0006: SESSION_DIR_RE is never duplicated here)."""
    return is_inside_session_dir(
        os.path.join(str(working_dir_root), name), working_dir_root
    )


def _limit_block(working_dir_root, claim, is_subagent, tool_name, target,
                 session_id):
    """Emit the session-dir-limit block verdict (stats accumulate via
    the setdefault chain; generic blocked bus fanout fires) and return
    the block dict."""
    template = (
        SESSION_DIR_LIMIT_SUBAGENT_MESSAGE
        if is_subagent
        else SESSION_DIR_LIMIT_BLOCK_MESSAGE
    )
    message = template % {
        "root": str(working_dir_root).replace("\\", "/"),
        "claim": str(claim).replace("\\", "/") if claim else "",
    }
    emit(
        "block", tool_name, SESSION_DIR_LIMIT_RULE_KEY, target,
        "per-session session directory limit", session_id, is_subagent,
    )
    return {"action": "block", "message": message}


# ---------------------------------------------------------------- Message builders

def scripts_path():
    """Resolved skills scripts directory (SCR-044 R6: single source).

    D11 precomputed slot (state.session.script_resolver_path, set at
    register) when present; __file__-based derivation for unregistered
    direct calls. Forward-slash rendering (message convention).
    """
    resolved = state.session.script_resolver_path
    if not resolved:
        resolved = os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "skills", "workspace-organization", "scripts",
            )
        )
    return str(resolved).replace("\\", "/")


def script_invocation_line(task, working_dir_root):
    """create_session_dir.py command line (SCR-044 R6, MB-1/MB-2).

    The SINGLE source for the invocation shape -- consumed by the
    verdict fix_line, the conditional orphan rename line (verdict) and
    the R7 orphan-notice cleanup hint. `<task>` may stay a placeholder
    (verdict passes "<task_name>").
    """
    return "python %s/create_session_dir.py %s --workspace %s" % (
        scripts_path(), task, str(working_dir_root).replace("\\", "/"),
    )


# ---------------------------------------------------------------- Orphan scan (R7)

# Classification chain, injected by the assembly layer (SCR-044 R7;
# ADR-0007 inject-don't-import, mirroring audit.set_classifier -- the
# verdict module imports this one, so a static verdict import is a
# cycle). Unwired -> scan_orphans fails open to None
# (production-unreachable: register() wires before any hook runs).
_classify_fn = None

# R7 advisory notice verbatim locks (testing-standards 7.14.7 O-1:
# header/tail pinned). ADVISE-ONLY: the notice is plain TEXT -- it
# never blocks, never deletes, and lands at most once per top-level
# session start (fire-once by construction).
ORPHAN_NOTICE_HEADER = (
    "NOTICE: Working Directory root has entries outside a session directory:"
)
ORPHAN_NOTICE_TAIL = (
    "If a project directory, add it to the allowlist dirs in "
    "HERMES_HOME/dir-whip/dir-whip-config.yaml (relative to the Working "
    "Directory root)."
)


def set_classifier(fn):
    """Wire the classification chain (assembly-layer injection)."""
    global _classify_fn
    _classify_fn = fn


def _orphan_notice(working_dir_root, names):
    """Build the advisory notice (O-1 shape): verbatim header, one
    listed entry per orphan, cleanup guidance -- the create + relocate
    path via the shared R6 builder (MB-2 single source) -- then the
    allowlist registration alternative (verbatim tail)."""
    lines = [ORPHAN_NOTICE_HEADER]
    lines.extend("  - %s" % name for name in names)
    lines.append("Create a session directory, then relocate them:")
    lines.append(
        "  %s" % script_invocation_line("<task_name>", working_dir_root)
    )
    lines.append('  mv "<root>/<entry>" "<session_dir>/Outputs/"')
    lines.append(ORPHAN_NOTICE_TAIL)
    return "\n".join(lines)


def scan_orphans(working_dir_root, allowlist=None):
    """Advisory orphan scan at top-level session start (SCR-044 R7,
    spec 5.4). Returns ONE compact notice string, or None.

    Filter: every TOP-LEVEL entry of the root passes through the
    injected classify_target -- a T4 block verdict = orphan candidate;
    T0-T3 (external / runtime allowlist / config allowlist / valid
    session dir) are auto-exempt. No hand-written exclusion list and
    no second session-dir regex (ADR-0006: no new vector). The
    session's own bound dir is compliant by definition (T3), so it can
    never appear (O-8).

    Semantics: advise-only (never a block dict, never a deletion,
    O-6); called once per top-level session start so the notice is
    fire-once by construction; unresolved/missing root -> None without
    raising (O-7); the CWD-outside-root and child-session skips live
    upstream in the assembly flow (O-4 / O-5). Fail-open: any error
    -> None, session start is never broken (5.8).
    """
    try:
        root = str(working_dir_root) if working_dir_root else None
        if not root or not os.path.isdir(root) or _classify_fn is None:
            return None
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return None
        orphans = []
        for name in names:
            verdict = _classify_fn(
                os.path.join(root, name), root, allowlist
            )
            if (
                isinstance(verdict, dict)
                and verdict.get("outcome") == "block"
            ):
                orphans.append(name)
        if not orphans:
            return None
        return _orphan_notice(root, orphans)
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs scan_orphans error (fail-open): %s", exc
        )
        return None


# ---------------------------------------------------------------- Gates

def guard_create(verdict, normalized, working_dir_root, session_id=None,
                 is_subagent=False, tool_name=None, target=None, tokens=None):
    """Session-dir creation gate (spec 5.19) -- the SINGLE enforcement
    point, mounted in verdict._evaluate_target right after classify.

    A no-op (returns None) for every verdict whose rule_key is not
    session-dir: T1 runtime / T2 config allowlist allows are exempt by
    structure (EX-1 / EX-2). On the session-dir branch:

    - the bound dir itself (Windows casefold): allow (BND-6 / BND-7);
    - free slot + first-segment dir absent (creation signal): BIND and
      allow (BND-1..4);
    - occupied slot + first-segment dir absent: an mv rename OF the
      bound dir (terminal_cp_mv_src over tokens) transfers the claim
      and allows (MV-1); anything else blocks session-dir-limit
      (BLK-1/2/5);
    - first-segment dir EXISTS: allow, no bind, no slot consumed
      (creation-count semantics, BND-5).

    Fail-open: any error allows (5.8). Returns the block dict or None.
    """
    try:
        if (
            not isinstance(verdict, dict)
            or verdict.get("outcome") != "allow"
            or verdict.get("rule_key") != "session-dir"
        ):
            return None
        owner = _owner(session_id)
        first_seg = _first_segment(normalized, working_dir_root)
        if not first_seg or first_seg == ".":
            return None
        claim = _claim_of(owner)
        if claim is not None and _same_name(claim, first_seg):
            return None  # the bound dir itself (BND-6 / BND-7)
        exists = os.path.isdir(os.path.join(str(working_dir_root), first_seg))
        if claim is None and not _slot_occupied(owner):
            if not exists:
                _bind(owner, first_seg)  # static creation signal
            return None
        if exists:
            return None  # existing other session dir: no bind (BND-5)
        if tokens and target is not None:
            src = terminal_cp_mv_src(tokens, target)
            if src is not None and _same_name(
                _token_first_segment(src, working_dir_root), claim
            ):
                _rebind(owner, first_seg)  # mv rename of the bound dir
                return None
        return _limit_block(
            working_dir_root, claim, is_subagent, tool_name, normalized,
            session_id,
        )
    except Exception as exc:
        logger.debug(
            "dir-whip: session-dir guard_create error (fail-open): %s", exc
        )
        return None


def guard_script(tokens, working_dir_root, session_id=None, is_subagent=False,
                 tool_name="terminal"):
    """Session-dir creation SCRIPT gate (spec 5.19), consulted by
    verdict._guard_terminal BEFORE the heredoc blanket demotion (BLK-3:
    the heredoc form stays gated).

    is_session_dir_script(tokens) False -> None (no interference). With
    the slot occupied (claim OR pending marker) -> session-dir-limit
    block. Otherwise the pending_create marker is armed for the audit
    post-diff binding observer and the command proceeds (the normal
    uncertain-tier allow+log still fires downstream, OB-5).
    """
    try:
        if not is_session_dir_script(tokens):
            return None
        owner = _owner(session_id)
        if _slot_occupied(owner):
            return _limit_block(
                working_dir_root, _claim_of(owner), is_subagent, tool_name,
                None, session_id,
            )
        with state.session_dirs.lock:
            state.session_dirs.pending_create[owner] = True
        return None
    except Exception as exc:
        logger.debug(
            "dir-whip: session-dir guard_script error (fail-open): %s", exc
        )
        return None


def observe_added(working_dir_root, session_id=None, added=()):
    """Script-vector binding observer (spec 5.19), called from the
    audit post-diff path (audit._audit_post_check) after an allowed
    terminal command.

    Consumes the owner's pending_create marker UNCONDITIONALLY (OB-2:
    a failed script leaves no ghost slot) and binds the FIRST new
    compliant session dir among `added` (the audit diff passes
    name-sorted additions; OB-3 first-bind, OB-4 non-compliant never
    binds). Returns the bound name or None. Fail-open: never raises.
    """
    try:
        owner = _owner(session_id)
        with state.session_dirs.lock:
            had_pending = state.session_dirs.pending_create.pop(owner, None)
        if had_pending is None or _claim_of(owner) is not None:
            return None
        for name in added or ():
            if name and _is_compliant(working_dir_root, name):
                return _bind(owner, name)
        return None
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs observe_added error (fail-open): %s", exc
        )
        return None


def on_session_start(session_id):
    """Top-level session start: clear the session's claim + pending
    marker (CLR-1). Called from the assembly layer's on_start AFTER the
    child-session skip, so child sessions inherit the parent's slot."""
    try:
        with state.session_dirs.lock:
            state.session_dirs.claims.pop(session_id, None)
            state.session_dirs.pending_create.pop(session_id, None)
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs session start error (fail-open): %s", exc
        )


def claim_of(session_id):
    """Public read: the owner-resolved bound dir name (or None)."""
    try:
        return _claim_of(_owner(session_id))
    except Exception:
        return None


__all__ = [
    "SESSION_DIR_LIMIT_RULE_KEY",
    "SESSION_DIR_LIMIT_BLOCK_MESSAGE",
    "SESSION_DIR_LIMIT_SUBAGENT_MESSAGE",
    "ORPHAN_NOTICE_HEADER",
    "ORPHAN_NOTICE_TAIL",
    "guard_create",
    "guard_script",
    "observe_added",
    "on_session_start",
    "claim_of",
    "scripts_path",
    "script_invocation_line",
    "set_classifier",
    "scan_orphans",
]
