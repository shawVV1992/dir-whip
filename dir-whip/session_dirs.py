"""Per-session unique Session Directory lifecycle: claims / pending binding / orphan scan + persistent claims sidecar (SCR-044 R5, SCR-048 R1, spec 5.19).

Pure decision layer (imports state / sessions / config / paths / events / terminal only, NEVER audit or verdict; no host imports, ADR-0007): one Session Directory per conversation -- claims maps the owner session (sessions.owner_session: subagent -> parent attribution) to the bound root-relative first segment (Windows casefold), pending_create marks a script creation in flight; a session-dir ALLOW whose first segment does not exist binds, a second creation blocks rule_key session-dir-limit (blocks emit through events: stats accumulate + generic blocked bus fanout; session-dir-limit deliberately NOT in events._BUS_SKIP_RULE_KEYS, the 7-emits manifest surface unchanged), a write into an existing dir passes unbound (creation-count semantics, BND-5), an mv rename of the bound dir transfers the claim (MV-1), and the R7 orphan scan consumes the injected classify chain (set_classifier, ADR-0007) advisory-only. Claims are write-through mirrored to session-claims.json in the profile-independent default dir-whip home (atomic tmp+replace, fail-open, 64-entry ts-LRU) and restored at register() for CLR-1 resume; every top-level session start clears except a restored live claim, state.reset_all clears the file too (CLR-2).

Layer: core
Refs: spec 5.19, SCR-044 R5, SCR-044 R6, SCR-044 R7, SCR-048 R1, SCR-048 R2, ADR-0006, ADR-0007, ADR-0015
Key exports:
  - guard_create -- session-dir creation gate: bind / mv-transfer / session-dir-limit block (single enforcement point).
  - guard_script -- create_session_dir.py script gate: arm the pending marker or block.
  - observe_added -- audit post-diff binding observer: bind the first compliant added dir, consume the marker.
  - on_session_start -- clear claim + marker; keep a restored live claim (CLR-1 resume).
  - load_claims -- restore persistent claims at register(); live root/dir entries only.
  - scan_orphans -- advisory top-level orphan notice at session start; None when clean.
  - set_classifier -- wire the classification chain for the orphan scan (assembly injection).
"""

import json
import logging
import os
import time

logger = logging.getLogger("dir-whip")

from . import state

from .paths import is_inside_session_dir

from .events import RULE_KEY_SESSION_DIR, SESSION_DIR_LIMIT_RULE_KEY, emit

# Message templates: centralized in the core leaf module messages.py
# (spec 5.20, SCR-047 R1, ADR-0014); same-name aliases keep every
# session_dirs.* call site, __all__ entry and test import path unchanged.
from .messages import (
    ORPHAN_NOTICE_CREATE_RELOCATE_LINE,
    ORPHAN_NOTICE_HEADER,
    ORPHAN_NOTICE_MV_LINE,
    ORPHAN_NOTICE_TAIL,
    SESSION_DIR_LIMIT_BLOCK_MESSAGE,
    SESSION_DIR_LIMIT_SUBAGENT_MESSAGE,
)

from .paths import dirwhip_home, is_absolute_any, paths_equal

from .sessions import owner_session

from .terminal import is_session_dir_script, terminal_cp_mv_src

# Spec 5.19: the per-session uniqueness rule_key is defined in events.py
# (SCR-052 R1 single definition point) and re-exported here under the
# retained historical name (consumer/test import paths unchanged).

# Spec 5.19 (SCR-048 R1): persistent claims store constants.
CLAIMS_STORE_NAME = "session-claims.json"
CLAIMS_STORE_VERSION = 1
CLAIMS_STORE_CAP = 64  # ts-LRU entry cap

# Spec 5.19 message templates live in messages.py (spec 5.20, SCR-047
# R1); the same-name imports above are the aliases (<root>/<claim> are
# substituted at build time by _limit_block, forward-slash rendering).


# ---------------------------------------------------------------- Claims persistence

def _claims_store_path():
    """Persistent claims store path (spec 5.19).

    PROFILE-INDEPENDENT by design: dirwhip_home(None), the default home
    segment -- after a host restart on_session_start has not fired, so
    state.session.session_profile is None and a per-profile home is not
    findable. Windows HOME resolution is delegated to paths.py.
    """
    return dirwhip_home(None) / CLAIMS_STORE_NAME


def _make_meta(name, working_dir_root):
    """Persistence sidecar for one claim (spec 5.19 entry fields)."""
    return {
        "root": str(working_dir_root) if working_dir_root else None,
        "dir": str(name),
        "profile": state.session.session_profile,
        "ts": time.time(),
        "restored": False,
    }


def _is_entry_alive(working_dir_root, name):
    """True when `working_dir_root/name` is still a directory on disk."""
    try:
        return bool(working_dir_root) and bool(name) and os.path.isdir(
            os.path.join(str(working_dir_root), str(name))
        )
    except Exception:
        return False


def _cap_payload(payload, keep_owner=None):
    """ts-LRU eviction above CLAIMS_STORE_CAP (the active bind wins ties)."""
    if len(payload) <= CLAIMS_STORE_CAP:
        return payload
    items = sorted(
        payload.items(),
        key=lambda kv: (kv[0] == keep_owner, kv[1]["ts"]),
        reverse=True,
    )
    return dict(items[:CLAIMS_STORE_CAP])


def _build_claims_payload(keep_owner=None):
    """Serializable claims mapping (caller holds state.session_dirs.lock).

    Dead entries (root/dir no longer a directory) are dropped EXCEPT the
    owner just bound/re-bound by the current operation -- at guard time
    the create action has not run yet, so the active entry is pending
    creation, not dead. Entries without sidecar metadata (a direct
    container write) are not persisted.
    """
    payload = {}
    for owner, name in state.session_dirs.claims.items():
        meta = state.session_dirs.claim_meta.get(owner)
        if not meta or not meta.get("root"):
            continue
        if owner != keep_owner and not _is_entry_alive(meta.get("root"), name):
            continue
        ts = meta.get("ts")
        payload[owner] = {
            "root": str(meta.get("root")),
            "dir": str(name),
            "profile": meta.get("profile"),
            "ts": ts if isinstance(ts, (int, float)) else 0,
        }
    return _cap_payload(payload, keep_owner)


def _persist_locked(keep_owner=None):
    """Write the claims store atomically (caller holds the lock).

    Fail-open: any IO/encoding error is logged at DEBUG only and ignored
    -- the in-memory container stays authoritative (5.8, the
    stats._append_stats_event tolerance pattern).
    """
    try:
        payload = _build_claims_payload(keep_owner)
        path = _claims_store_path()
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # surfaced by the write below
        data = json.dumps(
            {"version": CLAIMS_STORE_VERSION, "claims": payload}
        )
        with open(str(tmp_path), "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(str(tmp_path), str(path))
    except Exception as exc:
        logger.debug(
            "dir-whip: session-claims persist failed (ignored): %s", exc
        )


def load_claims():
    """Restore the persistent claims at register() (spec 5.19).

    Restores only entries whose `root/dir` is still a directory on disk;
    every other entry is dropped. Restored entries are marked
    `restored=True` in the sidecar metadata so on_session_start can tell
    a resume from a fresh bind. Fail-open: missing file / corrupt JSON /
    IO errors restore nothing and log DEBUG only (5.8).
    """
    try:
        path = _claims_store_path()
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("claims") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            logger.debug(
                "dir-whip: session-claims load ignored (malformed store)"
            )
            return
        with state.session_dirs.lock:
            for owner, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                working_dir_root = entry.get("root")
                name = entry.get("dir")
                if not _is_entry_alive(working_dir_root, name):
                    continue
                state.session_dirs.claims[str(owner)] = str(name)
                state.session_dirs.claim_meta[str(owner)] = {
                    "root": str(working_dir_root),
                    "dir": str(name),
                    "profile": entry.get("profile"),
                    "ts": entry.get("ts"),
                    "restored": True,
                }
    except Exception as exc:
        logger.debug(
            "dir-whip: session-claims load failed (ignored): %s", exc
        )


def clear_claims_store():
    """Delete the persistent claims store (CLR-2 revision, spec 5.19).

    Called by state.reset_all through a function-local import (a
    module-level state -> session_dirs edge would be a cycle). Fail-open:
    never raises (a missing file is the normal case).
    """
    try:
        _claims_store_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug(
            "dir-whip: session-claims clear failed (ignored): %s", exc
        )


# ---------------------------------------------------------------- State access

def _owner(session_id):
    """Owner resolution (subagent -> parent attribution)."""
    return owner_session(session_id) or session_id


def _claim_of(owner):
    with state.session_dirs.lock:
        return state.session_dirs.claims.get(owner)


def _bind(owner, name, working_dir_root=None):
    """First bind (idempotent: an existing claim is never overwritten).

    Write-through (spec 5.19): a new claim persists synchronously; the
    freshly bound entry is exempt from the dead-directory GC because the
    create action has not run yet at guard time (pending creation).
    """
    with state.session_dirs.lock:
        existing = state.session_dirs.claims.get(owner)
        if existing is not None:
            return existing
        if working_dir_root is None:
            working_dir_root = state.session.working_dir_root
        state.session_dirs.claims[owner] = name
        state.session_dirs.claim_meta[owner] = _make_meta(name, working_dir_root)
        _persist_locked(owner)
        return name


def _rebind(owner, name, working_dir_root=None):
    """Claim transfer (mv rename of the bound dir, MV-1) + write-through."""
    with state.session_dirs.lock:
        state.session_dirs.claims[owner] = name
        meta = state.session_dirs.claim_meta.get(owner)
        if working_dir_root is None:
            working_dir_root = state.session.working_dir_root
        if meta is None:
            state.session_dirs.claim_meta[owner] = _make_meta(name, working_dir_root)
        else:
            meta["dir"] = str(name)
            if working_dir_root:
                meta["root"] = str(working_dir_root)
        _persist_locked(owner)


def _is_slot_occupied(owner):
    """True when the conversation's slot is taken: a claim OR an
    in-flight script creation (the pending marker counts as the
    claim, BLK-4)."""
    with state.session_dirs.lock:
        return (
            owner in state.session_dirs.claims
            or owner in state.session_dirs.pending_create
        )


def _heal_missing_claim(owner, working_dir_root):
    """Release a claim whose bound directory vanished (spec 5.19, SCR-048 R2).

    A claim exists but `working_dir_root/<claim>` is no longer a directory
    on disk -> pop the claim + its sidecar metadata and persist (write-through
    deletion). Called ahead of the occupied determination in guard_create
    and guard_script (ahead of the MV-1 rename branch -- a vanished
    directory cannot be an mv source); after healing the normal free-slot
    flow rebinds. Fail-open: returns True when a claim was released,
    False otherwise; never raises.
    """
    try:
        with state.session_dirs.lock:
            claim = state.session_dirs.claims.get(owner)
            if claim is None or _is_entry_alive(working_dir_root, claim):
                return False
            state.session_dirs.claims.pop(owner, None)
            state.session_dirs.claim_meta.pop(owner, None)
            _persist_locked()
            return True
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs heal error (fail-open): %s", exc
        )
        return False


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


def is_creation_signal(target, working_dir_root, verdict=None):
    """Unified creation-signal predicate (v2.12 concept, SCR-052 4.5
    naming): TRUE when this action WILL CREATE a Session Directory --
    the target classifies T3 session-dir ALLOW and its first-segment
    directory does not yet exist on disk (mkdir, implicit write_file
    parent creation and terminal touch/redirect alike).

    verdict is the caller-held classify-chain result for the target (the
    shared-chain dict guard_create receives from verdict._evaluate_target);
    the T3 ALLOW half reads it -- re-running the chain here would need the
    allowlist guard_create does not carry and could diverge from the real
    verdict. verdict=None falls back to the config-kernel compliant-name
    check (_is_compliant, ADR-0006) -- the same T3-shape half the
    post-diff observer applies to script-created dirs, whose existence
    half rides the armed pending_create marker instead of a disk probe.

    Fail-open: any error -> False (never raises).
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
        elif not _is_compliant(working_dir_root, first_seg):
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
    verdict fix_line, the conditional orphan move line (verdict) and
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
# header/tail pinned) live in messages.py (spec 5.20, SCR-047 R1); the
# same-name imports above are the aliases. ADVISE-ONLY: the notice is
# plain TEXT -- it never blocks, never deletes, and lands at most once
# per top-level session start (fire-once by construction).


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
    lines.append(ORPHAN_NOTICE_CREATE_RELOCATE_LINE)
    lines.append(
        "  %s" % script_invocation_line("<task_name>", working_dir_root)
    )
    lines.append(ORPHAN_NOTICE_MV_LINE)
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
    - occupied slot whose claimed dir vanished (SCR-048 R2): the claim is
      released (pop + persist) and the normal free-slot flow applies --
      no phantom block, no mv-from-a-deleted-dir;
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
            or verdict.get("rule_key") != RULE_KEY_SESSION_DIR
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
        if claim is None and not _is_slot_occupied(owner):
            # SCR-052 4.5: the named creation-signal predicate (T3 ALLOW
            # via the caller-held chain verdict + first-segment absent).
            if is_creation_signal(normalized, working_dir_root, verdict=verdict):
                _bind(owner, first_seg, working_dir_root)
            return None
        if exists:
            return None  # existing other session dir: no bind (BND-5)
        # SCR-048 R2 (spec 5.19): heal a vanished claimed dir ahead of the
        # occupied determination / MV-1 branch; after healing the normal
        # free-slot binding flow applies.
        if _heal_missing_claim(owner, working_dir_root):
            claim = _claim_of(owner)
        if claim is None and not _is_slot_occupied(owner):
            if is_creation_signal(normalized, working_dir_root, verdict=verdict):
                _bind(owner, first_seg, working_dir_root)
            return None
        if tokens and target is not None:
            src = terminal_cp_mv_src(tokens, target)
            if src is not None and _same_name(
                _token_first_segment(src, working_dir_root), claim
            ):
                _rebind(owner, first_seg, working_dir_root)  # mv rename
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

    is_session_dir_script(tokens) False -> None (no interference). A
    vanished claimed dir is released first (SCR-048 R2: no phantom
    block); with the slot still occupied (claim OR pending marker) ->
    session-dir-limit block. Otherwise the pending_create marker is
    armed for the audit post-diff binding observer and the command
    proceeds (the normal uncertain-tier allow+log still fires downstream,
    OB-5).
    """
    try:
        if not is_session_dir_script(tokens):
            return None
        owner = _owner(session_id)
        # SCR-048 R2 (spec 5.19): heal before the occupied determination;
        # a released claim frees the slot, a pending marker still blocks.
        _heal_missing_claim(owner, working_dir_root)
        if _is_slot_occupied(owner):
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
                return _bind(owner, name, working_dir_root)
        return None
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs observe_added error (fail-open): %s", exc
        )
        return None


def on_session_start(session_id):
    """Top-level session start: clear the session's claim + pending
    marker (CLR-1) with the v2.16 resume exception (spec 5.19).

    Resume (ADR-0015 D2): when a RESTORED claim (loaded at register()
    into the in-memory sidecar; write-through keeps the store consistent,
    so no second file read happens here) still holds this session AND
    `root/dir` is still a directory on disk, the claim is KEPT. Every
    other case pops the claim + pending marker and persists the deletion.
    Called from the assembly layer's on_start AFTER the child-session
    skip, so child sessions inherit the parent's slot (unchanged).
    """
    try:
        with state.session_dirs.lock:
            meta = state.session_dirs.claim_meta.get(session_id)
            claim = state.session_dirs.claims.get(session_id)
            if (
                claim is not None
                and meta is not None
                and meta.get("restored")
                and _is_entry_alive(meta.get("root"), claim)
            ):
                state.session_dirs.pending_create.pop(session_id, None)
                return
            state.session_dirs.claims.pop(session_id, None)
            state.session_dirs.pending_create.pop(session_id, None)
            state.session_dirs.claim_meta.pop(session_id, None)
            _persist_locked()
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
    "CLAIMS_STORE_NAME",
    "CLAIMS_STORE_VERSION",
    "CLAIMS_STORE_CAP",
    "guard_create",
    "guard_script",
    "observe_added",
    "on_session_start",
    "claim_of",
    "load_claims",
    "clear_claims_store",
    "scripts_path",
    "script_invocation_line",
    "set_classifier",
    "scan_orphans",
    "is_creation_signal",
]
