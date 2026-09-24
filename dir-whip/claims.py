"""Session-Directory claim store: in-memory owner -> bound-dir maps + write-through session-claims.json sidecar (SCR-044 R5, SCR-048 R1, spec 5.19; split out of session_dirs.py at SCR-055 R5).

The claims home: owner resolution (subagent -> parent attribution), bind / mv-transfer / release / heal operations, the slot-occupied determination and the CLR-1/CLR-2 lifecycle (restore at register, keep a restored live claim at top-level session start, clear via state.reset_all). Claims are write-through mirrored to session-claims.json in the profile-independent default dir-whip home (atomic tmp+replace, fail-open, 64-entry ts-LRU); the script-vector binding observer (observe_added) consumes the pending marker and binds the first compliant added dir. Pure decision layer (state / paths / subagents only; no host imports, ADR-0007); the creation gates that consume this API live in session_dirs.py (dependency direction: session_dirs -> claims, never the reverse).

Layer: core
Refs: spec 5.19, SCR-044 R5, SCR-048 R1, SCR-048 R2, SCR-055 R5, ADR-0006, ADR-0007, ADR-0015
Key exports:
  - load_claims / clear_claims_store -- restore the store at register() / CLR-2 delete (state.reset_all hook).
  - claim_of / claim_of_owner -- owner-resolved bound dir name (public read) / raw owner-level read.
  - owner_of / bind / rebind / is_slot_occupied / heal_missing_claim -- owner resolution + claim-slot operations (gate consumers in session_dirs.py).
  - same_name / is_compliant -- BND-7 claim-name comparison + compliant session-dir name check (shared with session_dirs.py).
  - observe_added / on_session_start -- script-vector binding observer + top-level claim lifecycle (CLR-1 resume).
  - is_entry_alive / persist_locked -- sidecar liveness probe / atomic store write.
  - CLAIMS_STORE_NAME / CLAIMS_STORE_VERSION / CLAIMS_STORE_CAP -- persistent store constants.
"""

import json
import logging
import os
import time

from . import state

from .paths import dirwhip_home, is_inside_session_dir, paths_equal

from .subagents import owner_session

logger = logging.getLogger("dir-whip")

# Spec 5.19 (SCR-048 R1): persistent claims store constants.
CLAIMS_STORE_NAME = "session-claims.json"
CLAIMS_STORE_VERSION = 1
CLAIMS_STORE_CAP = 64  # ts-LRU entry cap


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


def is_entry_alive(working_dir_root, name):
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
        if owner != keep_owner and not is_entry_alive(meta.get("root"), name):
            continue
        ts = meta.get("ts")
        payload[owner] = {
            "root": str(meta.get("root")),
            "dir": str(name),
            "profile": meta.get("profile"),
            "ts": ts if isinstance(ts, (int, float)) else 0,
        }
    return _cap_payload(payload, keep_owner)


def persist_locked(keep_owner=None):
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
                if not is_entry_alive(working_dir_root, name):
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
    module-level state -> claims edge would be a cycle). Fail-open:
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


# ---------------------------------------------------------------- Claim state

def owner_of(session_id):
    """Owner resolution (subagent -> parent attribution)."""
    return owner_session(session_id) or session_id


def claim_of_owner(owner):
    """Raw owner-level claim read (SCR-055 R5 public; gate consumer)."""
    with state.session_dirs.lock:
        return state.session_dirs.claims.get(owner)


def claim_of(session_id):
    """Public read: the owner-resolved bound dir name (or None)."""
    try:
        return claim_of_owner(owner_of(session_id))
    except Exception:
        return None


def bind(owner, name, working_dir_root=None):
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
        persist_locked(owner)
        return name


def rebind(owner, name, working_dir_root=None):
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
        persist_locked(owner)


def is_slot_occupied(owner):
    """True when the conversation's slot is taken: a claim OR an
    in-flight script creation (the pending marker counts as the
    claim, BLK-4)."""
    with state.session_dirs.lock:
        return (
            owner in state.session_dirs.claims
            or owner in state.session_dirs.pending_create
        )


def heal_missing_claim(owner, working_dir_root):
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
            if claim is None or is_entry_alive(working_dir_root, claim):
                return False
            state.session_dirs.claims.pop(owner, None)
            state.session_dirs.claim_meta.pop(owner, None)
            persist_locked()
            return True
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs heal error (fail-open): %s", exc
        )
        return False


# ---------------------------------------------------------------- Name helpers

def same_name(a, b):
    """Session-dir name comparison: Windows casefold (BND-7).

    One-line delegate to paths.paths_equal (SCR-045 R7 single source);
    the None-guard stays (two Nones are NOT equal names).
    SCR-055 R5 public (cross-module consumer: session_dirs.guard_create).
    """
    if a is None or b is None:
        return False
    return paths_equal(a, b)


def is_compliant(working_dir_root, name):
    """Compliant session-dir name check through the config kernel
    (ADR-0006: SESSION_DIR_RE is never duplicated here).

    SCR-055 R5 public (cross-module consumers: session_dirs.is_creation_signal
    + claims.observe_added).
    """
    return is_inside_session_dir(
        os.path.join(str(working_dir_root), name), working_dir_root
    )


# ---------------------------------------------------------------- Claim lifecycle

def observe_added(working_dir_root, session_id=None, added=()):
    """Script-vector binding observer (spec 5.19), called from the
    audit post-diff path (audit.audit_post_check) after an allowed
    terminal command.

    Consumes the owner's pending_create marker UNCONDITIONALLY (OB-2:
    a failed script leaves no ghost slot) and binds the FIRST new
    compliant session dir among `added` (the audit diff passes
    name-sorted additions; OB-3 first-bind, OB-4 non-compliant never
    binds). Returns the bound name or None. Fail-open: never raises.
    """
    try:
        owner = owner_of(session_id)
        with state.session_dirs.lock:
            had_pending = state.session_dirs.pending_create.pop(owner, None)
        if had_pending is None or claim_of_owner(owner) is not None:
            return None
        for name in added or ():
            if name and is_compliant(working_dir_root, name):
                return bind(owner, name, working_dir_root)
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
    Called from the session-start chain AFTER the child-session skip, so
    child sessions inherit the parent's slot (unchanged).
    """
    try:
        with state.session_dirs.lock:
            meta = state.session_dirs.claim_meta.get(session_id)
            claim = state.session_dirs.claims.get(session_id)
            if (
                claim is not None
                and meta is not None
                and meta.get("restored")
                and is_entry_alive(meta.get("root"), claim)
            ):
                state.session_dirs.pending_create.pop(session_id, None)
                return
            state.session_dirs.claims.pop(session_id, None)
            state.session_dirs.pending_create.pop(session_id, None)
            state.session_dirs.claim_meta.pop(session_id, None)
            persist_locked()
    except Exception as exc:
        logger.debug(
            "dir-whip: session_dirs session start error (fail-open): %s", exc
        )


__all__ = [
    "CLAIMS_STORE_NAME",
    "CLAIMS_STORE_VERSION",
    "CLAIMS_STORE_CAP",
    "load_claims",
    "clear_claims_store",
    "claim_of",
    "claim_of_owner",
    "owner_of",
    "bind",
    "rebind",
    "is_slot_occupied",
    "heal_missing_claim",
    "same_name",
    "is_compliant",
    "is_entry_alive",
    "persist_locked",
    "observe_added",
    "on_session_start",
]
