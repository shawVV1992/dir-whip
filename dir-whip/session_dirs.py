"""Per-session unique Session Directory gates: creation gating + advisory orphan scan + script message builders (spec 5.19).

Enforcement side of one Session Directory per conversation: a session-dir
ALLOW whose first segment does not exist binds through claims.bind
(creation signal); a second creation blocks rule_key session-dir-limit
(the block emits through events: stats + generic blocked bus fanout);
an mv rename of the bound dir transfers the claim; the orphan scan
consumes the injected classify chain (ADR-0007), advisory-only. Claim
store lives in claims.py (direction: session_dirs -> claims only).

Layer: core
Refs: spec 5.19, SCR-044, SCR-048
Key exports:
  - guard_create -- session-dir creation gate: bind / mv-transfer / session-dir-limit block (single enforcement point).
  - guard_script -- create_session_dir.py script gate: arm the pending marker or block.
  - scan_orphans -- advisory top-level orphan notice at session start; None when clean.
  - is_creation_signal -- unified creation-signal predicate (T3-shape + first-segment absent).
  - set_classifier -- wire the classification chain for the orphan scan (assembly injection).
  - scripts_path / script_invocation_line -- resolved scripts dir + create_session_dir.py command line (single source).
"""

import logging
import os

from . import state

from .claims import (
    bind, claim_of_owner, heal_missing_claim, is_compliant,
    is_slot_occupied, owner_of, rebind, same_name,
)

from .events import RULE_KEY_SESSION_DIR, SESSION_DIR_LIMIT_RULE_KEY, emit

# Message templates live in the core leaf messages.py; same-name aliases
# keep session_dirs.* call sites, __all__ entries and test import paths
# unchanged.
from .messages import (
    ORPHAN_NOTICE_CREATE_RELOCATE_LINE,
    ORPHAN_NOTICE_HEADER,
    ORPHAN_NOTICE_MV_LINE,
    ORPHAN_NOTICE_TAIL,
    SESSION_DIR_LIMIT_BLOCK_MESSAGE,
    SESSION_DIR_LIMIT_SUBAGENT_MESSAGE,
)

from .paths import is_absolute_any

logger = logging.getLogger("dir-whip")

# The session-dir-limit rule_key is defined in events.py (single
# definition point); this module re-exports it for consumer/test import
# paths. <root>/<claim> are substituted at build time by _limit_block.


# ---------------------------------------------------------------- Path predicates

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


def is_creation_signal(target, working_dir_root, verdict=None):
    """Unified creation-signal predicate: TRUE when this action WILL
    CREATE a Session Directory -- the target classifies T3 session-dir
    ALLOW and its first-segment directory does not yet exist on disk
    (mkdir, implicit write_file parent creation, terminal touch/redirect).

    verdict is the caller-held classify-chain result; the T3 ALLOW half
    reads it (re-running the chain would need the allowlist guard_create
    does not carry and could diverge). verdict=None falls back to the
    compliant-name check (is_compliant) -- the same T3-shape half the
    post-diff observer applies to script-created dirs. Fail-open: any
    error -> False (never raises).
    """
    try:
        first_seg = _first_segment(target, working_dir_root)
        if not first_seg or first_seg == ".":
            return False
        if verdict is not None:
            if (
                not isinstance(verdict, dict)
                or verdict.get("outcome") != "allow"
                or verdict.get("rule_key") != RULE_KEY_SESSION_DIR
            ):
                return False
        elif not is_compliant(working_dir_root, first_seg):
            return False
        return not os.path.isdir(
            os.path.join(str(working_dir_root), first_seg)
        )
    except Exception:
        return False


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
    """Resolved skills scripts directory (single source).

    The precomputed slot (state.session.script_resolver_path, set at
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
    """create_session_dir.py command line.

    The SINGLE source for the invocation shape -- consumed by the verdict
    fix_line, the conditional orphan move line and the orphan-notice
    cleanup hint. `<task>` may stay a placeholder (verdict passes
    "<task_name>").
    """
    return "python %s/create_session_dir.py %s --workspace %s" % (
        scripts_path(), task, str(working_dir_root).replace("\\", "/"),
    )


# ---------------------------------------------------------------- Orphan scan

# Classification chain, injected by the assembly layer (inject-don't-
# import, mirroring audit.set_classifier -- classify statically imports
# this module, so a static import back is a cycle). Unwired ->
# scan_orphans fails open to None (register() wires before any hook).
_classify_fn = None

# The advisory notice lines live in messages.py. ADVISE-ONLY: plain TEXT,
# never blocks, never deletes, at most once per top-level session start.


def set_classifier(fn):
    """Wire the classification chain (assembly-layer injection)."""
    global _classify_fn
    _classify_fn = fn


def _orphan_notice(working_dir_root, names):
    """Build the advisory notice: header, one listed entry per orphan,
    cleanup guidance (the create + relocate path via the shared builder),
    then the allowlist registration alternative (tail)."""
    lines = [ORPHAN_NOTICE_HEADER]
    lines.extend("  - %s" % name for name in names)
    lines.append(ORPHAN_NOTICE_CREATE_RELOCATE_LINE)
    lines.append(
        "  %s" % script_invocation_line("<task_name>", working_dir_root)
    )
    lines.append(ORPHAN_NOTICE_MV_LINE)
    lines.append(ORPHAN_NOTICE_TAIL)
    return "\n".join(lines)


def scan_orphans(working_dir_root, allowlist=None):
    """Advisory orphan scan at top-level session start (spec 5.4);
    returns ONE compact notice string, or None.

    Filter: every TOP-LEVEL entry of the root passes through the injected
    classify_target -- a T4 block verdict = orphan candidate; T0-T3
    (external / runtime allowlist / config allowlist / valid session dir)
    are auto-exempt (no hand-written exclusion list; no second
    session-dir regex). Advise-only: never a block dict, never a
    deletion; fire-once by construction; unresolved/missing root -> None;
    CWD-outside-root and child-session skips live upstream in the
    assembly flow. Fail-open: any error -> None.
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
    """Session-dir creation gate -- the SINGLE enforcement point, mounted
    from classify.evaluate_target right after classify.

    A no-op (returns None) for every verdict whose rule_key is not
    session-dir (T1 runtime / T2 config allowlist allows are exempt by
    structure). On the session-dir branch:

    - the bound dir itself (Windows casefold): allow;
    - free slot + first-segment dir absent (creation signal): BIND and
      allow;
    - occupied slot whose claimed dir vanished: the claim is released
      (pop + persist) and the normal free-slot flow applies -- no
      phantom block, no mv-from-a-deleted-dir;
    - occupied slot + first-segment dir absent: an mv rename OF the bound
      dir (terminal_cp_mv_src over tokens) transfers the claim and
      allows; anything else blocks session-dir-limit;
    - first-segment dir EXISTS: allow, no bind, no slot consumed
      (creation-count semantics).

    Fail-open: any error allows. Returns the block dict or None.
    """
    try:
        if (
            not isinstance(verdict, dict)
            or verdict.get("outcome") != "allow"
            or verdict.get("rule_key") != RULE_KEY_SESSION_DIR
        ):
            return None
        owner = owner_of(session_id)
        first_seg = _first_segment(normalized, working_dir_root)
        if not first_seg or first_seg == ".":
            return None
        claim = claim_of_owner(owner)
        if claim is not None and same_name(claim, first_seg):
            return None  # the bound dir itself
        exists = os.path.isdir(os.path.join(str(working_dir_root), first_seg))
        if claim is None and not is_slot_occupied(owner):
            # The named creation-signal predicate (T3 ALLOW via the
            # caller-held chain verdict + first-segment absent).
            if is_creation_signal(normalized, working_dir_root, verdict=verdict):
                bind(owner, first_seg, working_dir_root)
            return None
        if exists:
            return None  # existing other session dir: no bind
        # Heal a vanished claimed dir ahead of the occupied determination
        # / mv branch; after healing the normal free-slot flow applies.
        if heal_missing_claim(owner, working_dir_root):
            claim = claim_of_owner(owner)
        if claim is None and not is_slot_occupied(owner):
            if is_creation_signal(normalized, working_dir_root, verdict=verdict):
                bind(owner, first_seg, working_dir_root)
            return None
        if tokens and target is not None:
            # Function-local import = cycle break (terminal statically
            # consumes this gate surface).
            from .terminal import terminal_cp_mv_src
            src = terminal_cp_mv_src(tokens, target)
            if src is not None and same_name(
                _token_first_segment(src, working_dir_root), claim
            ):
                rebind(owner, first_seg, working_dir_root)  # mv rename
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
    terminal.guard_terminal BEFORE the heredoc blanket demotion (the
    heredoc form stays gated).

    is_session_dir_script(tokens) False -> None (no interference). A
    vanished claimed dir is released first (no phantom block); with the
    slot still occupied (claim OR pending marker) -> session-dir-limit
    block. Otherwise the pending_create marker is armed for the audit
    post-diff binding observer and the command proceeds (the normal
    uncertain-tier allow + log still fires downstream).
    """
    try:
        # Function-local import = cycle break (terminal statically
        # consumes this gate surface).
        from .terminal import is_session_dir_script
        if not is_session_dir_script(tokens):
            return None
        owner = owner_of(session_id)
        # Heal before the occupied determination; a released claim frees
        # the slot, a pending marker still blocks.
        heal_missing_claim(owner, working_dir_root)
        if is_slot_occupied(owner):
            return _limit_block(
                working_dir_root, claim_of_owner(owner), is_subagent, tool_name,
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


__all__ = [
    "SESSION_DIR_LIMIT_RULE_KEY",
    "SESSION_DIR_LIMIT_BLOCK_MESSAGE",
    "SESSION_DIR_LIMIT_SUBAGENT_MESSAGE",
    "ORPHAN_NOTICE_HEADER",
    "ORPHAN_NOTICE_TAIL",
    "guard_create",
    "guard_script",
    "scripts_path",
    "script_invocation_line",
    "set_classifier",
    "scan_orphans",
    "is_creation_signal",
]
