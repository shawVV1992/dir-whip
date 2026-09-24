"""Root write audit core: snapshot/diff/classify kernels + pending-violation store + pre/post pairing (spec 5.18).

Detection backbone shared by the L1/L3 conversation surface (audit_prompts.py) and the L4 settlement family (settle.py): pre/post snapshot pairing, the session-scoped pending-violation store (the L3 latch input), the settlement judgment re-scan and the terminal post-check. The classification chain is INJECTED via set_classifier (assembly layer, ADR-0007 inject-don't-import) to break the audit<->guard cycle; depends on paths/state/events/config/subagents/session_dirs + stdlib only, no host imports (SCR-035 core discipline, ADR-0007); extracted from dir_whip.py (task 31.12); the L1/L3/nudge surface split out to audit_prompts.py and the L4 family to settle.py at SCR-055 R4.

Layer: core
Refs: spec 5.18, spec v2.6 B2, SCR-035, SCR-044 R5, SCR-045 R6, SCR-050 v3 R6.1, SCR-055 R4, ADR-0007
Key exports:
  - set_classifier -- wire the classification chain (assembly-layer injection).
  - snapshot -- read-only top-level root snapshot; None on OSError (fail-open).
  - classify_diff -- four-state snapshot diff -> {violations, recorded}; deletions record-only.
  - audit_norm_path -- deterministic pending-set key (absolute + native-normalized; consumer: settle.py).
  - pending_violation_snapshot / pending_violation_paths -- read-only pending-set views (L3 gate input / settlement judgment).
  - pre_snapshot -- pre snapshot for an allowed terminal call (cap-guarded).
  - audit_post_check -- terminal re-scan/diff/violation post-check (SCR-050 v3 R6.1 public; consumer: the assembly post_tool_call observer).
"""

import datetime
import logging
import os

from . import state

from .config import get_cached_config

from .events import (
    RULE_KEY_ROOT_FILE,
    RULE_KEY_WRITE_AUDIT_VIOLATION,
    bus_emit,
    emit,
)

from .paths import (
    relativize_target,
    within_working_dir,
)

from .subagents import owner_session

# SCR-044 R5 (spec 5.19): the script-vector binding observer lives in
# session_dirs; the audit -> session_dirs direction is sanctioned (the
# reverse import would be a cycle and does not exist).
from . import session_dirs

logger = logging.getLogger("dir-whip")

# Audit entry guardrail (spec 5.6/5.18 v2.8 R7 de-configuration): the DoS
# cap is an internal audit-owned constant (SCR-045 R2 moved it home from
# the config shim); no config key adjusts it.
WRITE_AUDIT_ENTRY_CAP = 2000

# Classification chain, injected by the assembly layer (register() now;
# __init__.py at 31.13). Unwired -> RuntimeError (production-unreachable:
# register() wires before any hook runs; the fail-open hook adapter
# catches it).
_classify_fn = None


def set_classifier(fn):
    """Wire the classification chain (assembly-layer injection, ADR-0007)."""
    global _classify_fn
    _classify_fn = fn


def snapshot(working_dir_root):
    """Snap the top-level entries of working_dir_root (spec 5.18 mechanism).

    Recorded per entry: (st_size, st_mtime_ns, is_dir) -- exactly the
    fidelity the diff needs. Scan OSError -> None (fail-open; callers
    silently skip the audit round). Never raises.
    """
    try:
        entries = {}
        with os.scandir(working_dir_root) as it:
            for entry in it:
                st = entry.stat()
                entries[entry.name] = (st.st_size, st.st_mtime_ns, entry.is_dir())
        return entries
    except OSError:
        return None


def diff_snapshots(before, after):
    """Four-state diff between two snapshots (spec 5.18).

    Returns {"added", "modified", "deleted", "unrelated"} name lists
    (name-sorted): new entries, same-name entries whose (size, mtime_ns,
    is_dir) changed, vanished entries, and unchanged entries. Pure --
    no filesystem access.
    """
    before = before or {}
    after = after or {}
    before_keys = set(before)
    after_keys = set(after)
    common = before_keys & after_keys
    return {
        "added": sorted(after_keys - before_keys),
        "modified": sorted(
            name for name in common if before[name] != after[name]
        ),
        "deleted": sorted(before_keys - after_keys),
        "unrelated": sorted(
            name for name in common if before[name] == after[name]
        ),
    }


def classify_diff(diff, before, after, working_dir_root, allowlist,
                        is_subagent=False):
    """Classify a snapshot diff into violations (spec 5.18, v2.6 B2).

    Only FILE entries are judged (is_dir -> never a violation; directory
    mtimes -- session dirs, `.git/` -- are ignored). A
    violation is a NEW or MODIFIED root-level file that classifies as a
    root-file block through the shared chain: not on the allowlist file
    entries, not under an allowlist prefix, not inside any session directory
    (the same allowlist key the guard reads, so the layers never disagree).
    Deletions are RECORD-ONLY (5.8 delete principle) -- surfaced in
    "recorded", never judged.

    Returns {"violations": [abs paths], "recorded": [deleted abs paths]}.
    """
    pending_violations = []
    deleted = []
    for name in list(diff.get("added", [])) + list(diff.get("modified", [])):
        info = (after or {}).get(name)
        if info is None or info[2]:
            continue  # directory entries never violate (5.18)
        abs_path = os.path.join(working_dir_root, name)
        if _classify_fn is None:
            raise RuntimeError("audit classifier not wired")
        verdict = _classify_fn(
            abs_path, working_dir_root, allowlist, is_subagent
        )
        if verdict["outcome"] == "block" and verdict["rule_key"] == RULE_KEY_ROOT_FILE:
            pending_violations.append(abs_path)
    for name in diff.get("deleted", []):
        info = (before or {}).get(name)
        if info is None or info[2]:
            continue
        deleted.append(os.path.join(working_dir_root, name))
    # Return keys are the frozen classify_diff contract ("violations" /
    # "recorded"); the locals carry the SCR-052 G3 semantics
    # (pending_violations / deleted record-only bookkeeping).
    return {
        "violations": sorted(pending_violations),
        "recorded": sorted(deleted),
    }


def audit_norm_path(path):
    """Deterministic pending-set key: absolute + native-normalized.

    SCR-055 R4 public (cross-module consumer: settle.py); the audit-internal
    callers use the same single normalization point.
    """
    return os.path.normpath(str(path))


def _audit_now():
    """ISO-8601 timestamp (seconds precision) for first_seen."""
    return datetime.datetime.now().isoformat(timespec="seconds")


# SCR-052 R1: the former _audit_owner_session thin delegate (a call-site
# preservation shim for subagents.owner_session, SCR-044 R3) is inlined --
# owner resolution is called directly as owner_session(session_id).


def pending_violation_snapshot(session_id=None):
    """Read-only copy of a session's pending violations (L3 gate).

    The L3 gate reads this set; keys are absolute normpath'd paths, each
    value is {"first_seen": ISO-8601, "announced": bool}. "announced" is
    flipped by L1 (mark_announced) so the fire-once notice never
    repeats; first_seen is preserved across re-detections. Child sessions
    resolve into the parent's set.
    """
    owner = owner_session(session_id) or session_id
    with state.audit.lock:
        return {
            path: dict(entry)
            for path, entry in state.audit.pending_violations.get(owner, {}).items()
        }


def pending_violation_add(session_id, path, first_seen=None):
    """Add one pending violation (detection fills this structure).

    Existing entries are kept untouched on re-detection (first_seen and
    announced survive, so L1 fire-once semantics hold across rounds).
    """
    owner = owner_session(session_id) or session_id
    key = audit_norm_path(path)
    with state.audit.lock:
        bucket = state.audit.pending_violations.setdefault(owner, {})
        if key in bucket:
            return
        bucket[key] = {
            "first_seen": first_seen or _audit_now(),
            "announced": False,
        }


def pending_violation_clear(session_id):
    """Clear a session's pending violations (top-level session start)."""
    with state.audit.lock:
        state.audit.pending_violations.pop(session_id, None)


def mark_announced(session_id, path):
    """Flip the fire-once announced flag (L1 notice lane calls this)."""
    owner = owner_session(session_id) or session_id
    key = audit_norm_path(path)
    with state.audit.lock:
        entry = state.audit.pending_violations.get(owner, {}).get(key)
        if entry:
            entry["announced"] = True


def pending_violation_paths(session_id, working_dir_root=None, allowlist=None):
    """Settlement judgment for the L3 gate (the gate's unresolved input):
    re-scan the
    root and return the pending paths that STILL violate (file present and
    still classifying as an unprotected root-level file). A pending path
    is settled when it is gone, moved outside the root, or legalized
    (allowlist file / prefix / session dir). Fail-open: a failed re-scan keeps
    the full pending set (the gate stays latched).

    SCR-041 R1 (spec 5.18 v2.9): the classification here is CONFIG-only
    (honor_runtime_allowlist=False) -- a runtime-allowlist entry is
    prospective-only and never settles a recorded violation; config
    allowlist files/dirs entries and session-dir containment still
    settle. Shared by the L3 gate and the continuation nudge.
    """
    try:
        pending = pending_violation_snapshot(session_id)
        if not pending:
            return []
        if working_dir_root is None:
            working_dir_root, allowlist = get_cached_config(
                state.session.registered_ctx
            )
        if working_dir_root is None:
            return sorted(pending)
        after = snapshot(working_dir_root)
        if after is None:
            return sorted(pending)
        unresolved = []
        for path in pending:
            if not os.path.lexists(path):
                continue  # gone -> settled
            if not within_working_dir(path, working_dir_root):
                continue  # moved outside the root -> settled
            if _classify_fn is None:
                raise RuntimeError("audit classifier not wired")
            verdict = _classify_fn(
                path, working_dir_root, allowlist or [], is_subagent=False,
                honor_runtime_allowlist=False,
            )
            if verdict["outcome"] == "block" and verdict["rule_key"] == RULE_KEY_ROOT_FILE:
                unresolved.append(path)
        return sorted(unresolved)
    except Exception as exc:
        logger.debug("dir-whip: audit settlement check error (fail-open): %s", exc)
        return sorted(pending_violation_snapshot(session_id))


def pre_snapshot(session_id, task_id, working_dir_root, allowlist):
    """Take the pre snapshot for an ALLOWED terminal call (5.18).

    allowlist is the PARSED {files, dirs} mapping (SCR-045 R7: the
    caller parses at the call site; the 1-tuple transport hack is gone
    and the value is stored as-is). Root entry count above
    WRITE_AUDIT_ENTRY_CAP -> round skipped + ONE WARNING per session
    (not repeated). Scan OSError -> fail-open (no snapshot stored, so
    the post skips). Any exception -> nothing (fail-open, 5.8).
    """
    try:
        snap = snapshot(working_dir_root)
        if snap is None:
            return
        cap = WRITE_AUDIT_ENTRY_CAP
        if len(snap) > cap:
            if not state.audit.cap_warned:
                state.audit.cap_warned = True
                logger.warning(
                    "dir-whip: write audit skipped: root entry count %d "
                    "exceeds write_audit_entry_cap %d", len(snap), cap,
                )
            return
        with state.audit.lock:
            state.audit.pre_snapshots[(session_id, task_id)] = (
                snap, working_dir_root, allowlist,
            )
    except Exception as exc:
        logger.debug("dir-whip: audit pre-snapshot error (fail-open): %s", exc)


def audit_post_check(session_id, task_id, is_subagent=False):
    """Post terminal re-scan: diff the pre snapshot and classify (5.18, v2.6 B2).

    Pops the (session_id, task_id) pairing; no pairing (blocked-at-pre,
    cap skip, disabled, scan failure) -> nothing. Each violation joins the
    session's pending set and emits ONE write-audit-violation verdict
    event (tool="audit", relative target, 5.13 privacy; bus_event=False)
    plus the 5.14 write-audit-violation bus sidecar with a relative path,
    the session-scope flag and first_seen. Deletions are record-only,
    never events. The L1 notice is NOT an event (5.18). Fail-open: never
    raises.
    """
    try:
        with state.audit.lock:
            record = state.audit.pre_snapshots.pop((session_id, task_id), None)
        if record is None:
            return
        before, working_dir_root, allowlist = record
        after = snapshot(working_dir_root)
        if after is None:
            return
        diff = diff_snapshots(before, after)
        # SCR-044 R5 (spec 5.19): script-vector creation observer.
        # Fires only when a pending_create marker exists; binds the
        # FIRST new compliant session dir under the root and ALWAYS
        # consumes the marker (a failed script leaves no ghost slot).
        if state.session_dirs.pending_create:
            session_dirs.observe_added(
                working_dir_root, session_id, diff.get("added", []),
            )
        classified = classify_diff(
            diff, before, after, working_dir_root, list(allowlist), is_subagent,
        )
        for path in classified["violations"]:
            pending_violation_add(session_id, path)
            emit(
                "block", "audit", RULE_KEY_WRITE_AUDIT_VIOLATION, path,
                "root write audit violation (5.18)", session_id, is_subagent,
            )
            bus_emit("write-audit-violation", {
                "outcome": "block",
                "rule_key": RULE_KEY_WRITE_AUDIT_VIOLATION,
                "path": relativize_target(path, working_dir_root),
                "is_subagent": bool(is_subagent),
                "first_seen": (
                    pending_violation_snapshot(session_id)
                    .get(audit_norm_path(path), {})
                    .get("first_seen")
                ),
            })
    except Exception as exc:
        logger.debug("dir-whip: audit post check error (fail-open): %s", exc)


# Single authoritative names (SCR-052 R1 alias convergence; SCR-055 R4: the
# L1/L3/nudge surface moved to audit_prompts.py and the L4 settlement family
# to settle.py -- the defs above carry this module's public names directly).

__all__ = [
    "set_classifier",
    "snapshot",
    "classify_diff",
    "audit_norm_path",
    "pending_violation_paths",
    "pending_violation_snapshot",
    "pending_violation_add",
    "pending_violation_clear",
    "mark_announced",
    "pre_snapshot",
    "audit_post_check",
]
