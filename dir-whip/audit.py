"""Root write audit core + L4 settlement family: snapshot/diff/classify kernels, pending-violation store, pre/post pairing, dir_whip_settle quarantine surface (spec 5.18).

Detection backbone for the L1/L3 conversation surface
(audit_prompts.py): pre/post snapshot pairing, the session-scoped
pending-violation store, the settlement judgment re-scan, the terminal
post-check and the dir_whip_settle tool. The classification chain is
INJECTED via set_classifier to break the audit<->guard cycle. Pure
decision layer: no host imports, no audit_prompts import.

Layer: core
Refs: spec 5.18, SCR-043, ADR-0007
Key exports:
  - set_classifier -- wire the classification chain (assembly-layer injection).
  - AUDIT_PAIRED_TOOLS -- the write-class tools covered by pre/post pairing (single source).
  - snapshot / diff_snapshots -- read-only root snapshot + four-state diff.
  - classify_diff -- four-state diff -> {violations, recorded}; deletions record-only.
  - audit_norm_path -- deterministic pending-set key (absolute + native-normalized).
  - pending_violation_snapshot / pending_violation_paths -- read-only pending-set views (L3 gate input / settlement judgment).
  - pre_snapshot / audit_post_check -- allowed-terminal pre snapshot (cap-guarded) + terminal re-scan/diff violation post-check.
  - SETTLE_TOOL_SCHEMA / settle_paths / lazy_register_settle_tool -- dir_whip_settle schema, quarantine core and lazy registration.
"""

import datetime
import json
import logging
import os
import shutil

from . import state

from .config import get_cached_config

from .events import (
    RULE_KEY_ROOT_FILE,
    RULE_KEY_WRITE_AUDIT_SETTLE,
    RULE_KEY_WRITE_AUDIT_SETTLE_REJECTED,
    RULE_KEY_WRITE_AUDIT_VIOLATION,
    bus_emit,
    emit,
)

# Message templates live in the core leaf messages.py.
from .messages import (
    SETTLE_TOOL_DESCRIPTION,
    SETTLE_TOOL_PATHS_DESCRIPTION,
)

from .paths import (
    dirwhip_home,
    relativize_target,
    within_working_dir,
)

from .stats import stats_record

from .subagents import owner_session

# The script-vector binding observer lives in claims; the audit -> claims
# direction is sanctioned (the reverse import would be a cycle).
from . import claims

# The subagent gate reads the subagents-module attribute (module-level
# private imports are banned; attribute access is the sanctioned form).
from . import subagents

logger = logging.getLogger("dir-whip")

# Tools whose ALLOWED calls are covered by the pre/post snapshot pairing
# (v2.25 SCR-057: terminal + execute_code). Single definition point --
# consumed by the guard pre branch, the assembly post adapter and the L1
# transform surface; no second literal list.
AUDIT_PAIRED_TOOLS = ("terminal", "execute_code")

# Audit entry guardrail: the DoS cap is an internal audit-owned constant
# (de-configured); no config key adjusts it.
WRITE_AUDIT_ENTRY_CAP = 2000

# Classification chain, injected by the assembly layer at register().
# Unwired -> RuntimeError (production-unreachable: register() wires before
# any hook runs; the fail-open hook adapter catches it).
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
    """Classify a snapshot diff into violations (spec 5.18).

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
    # "recorded"); the locals carry the pending / deleted bookkeeping.
    return {
        "violations": sorted(pending_violations),
        "recorded": sorted(deleted),
    }


def audit_norm_path(path):
    """Deterministic pending-set key: absolute + native-normalized.

    Single normalization point for audit-internal callers and the L4
    settlement family.
    """
    return os.path.normpath(str(path))


def _audit_now():
    """ISO-8601 timestamp (seconds precision) for first_seen."""
    return datetime.datetime.now().isoformat(timespec="seconds")


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
    """Settlement judgment for the L3 gate: re-scan the root and return
    the pending paths that STILL violate (file present and still
    classifying as an unprotected root-level file). A pending path is
    settled when it is gone, moved outside the root, or legalized
    (allowlist file / dir entry / session dir). Fail-open: a failed
    re-scan keeps the full pending set (the gate stays latched).

    The classification here is CONFIG-only
    (honor_runtime_allowlist=False): a runtime-allowlist entry is
    prospective-only and never settles a recorded violation; config
    allowlist entries and session-dir containment still settle. Shared
    by the L3 gate and the continuation nudge.
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
    """Take the pre snapshot for an ALLOWED terminal call.

    allowlist is the PARSED {files, dirs} mapping (the caller parses at
    the call site; the value is stored as-is). Root entry count above
    WRITE_AUDIT_ENTRY_CAP -> round skipped + ONE WARNING per session
    (not repeated). Scan OSError -> no snapshot stored (the post skips).
    Any exception -> fail-open.
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
    """Post terminal re-scan: diff the pre snapshot and classify.

    Pops the (session_id, task_id) pairing; no pairing (blocked-at-pre,
    cap skip, disabled, scan failure) -> nothing. Each violation joins the
    session's pending set and emits ONE write-audit-violation verdict
    event (tool="audit", relative target, privacy; bus_event=False) plus
    the write-audit-violation bus sidecar with a relative path, the
    session-scope flag and first_seen. Deletions are record-only, never
    events; the L1 notice is NOT an event. Fail-open: never raises.
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
        # Script-vector creation observer: fires only when a
        # pending_create marker exists, binds the FIRST new compliant
        # session dir under the root and ALWAYS consumes the marker (a
        # failed script leaves no ghost slot).
        if state.session_dirs.pending_create:
            claims.observe_added(
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


# ---------------------------------------------------------------- L4 settlement family


# ---------------------------------------------------------------- dir_whip_settle tool surface

# dir_whip_settle tool schema (OpenAI function-call format, same contract
# as ALLOW_PATH_TOOL_SCHEMA). Defined HERE (not __init__.py) because the
# lazy registration fires from transform_tool_result without register()
# having run (test contract: first notice fire registers the tool).
# Description texts live in messages.py.
SETTLE_TOOL_SCHEMA = {
    "name": "dir_whip_settle",
    "description": SETTLE_TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": SETTLE_TOOL_PATHS_DESCRIPTION,
            }
        },
        "required": ["paths"],
    },
}


def _settle_tool_handler(args, **kwargs):
    """Registered dir_whip_settle handler: JSON-string tool result."""
    try:
        paths = args.get("paths") if isinstance(args, dict) else args
        return json.dumps(
            settle_paths(kwargs.get("session_id"), paths)
        )
    except Exception as exc:
        logger.debug("dir-whip: settle handler error (fail-open): %s", exc)
        return json.dumps({"error": "settle failed"})


def lazy_register_settle_tool():
    """Register dir_whip_settle on the FIRST L1 notice fire.

    Cross-module consumer: audit_prompts.transform_tool_result
    (function-local import). The registry has no timing constraint (the
    host rebuilds the per-turn tool list), so a late registration is
    visible from the next turn on. Idempotent by nature (re-register
    overwrites); attempted once per notice fire. Fail-open: any error is
    logged and never blocks the notice.
    """
    try:
        ctx = state.session.registered_ctx
        if ctx is not None and hasattr(ctx, "register_tool"):
            ctx.register_tool(
                "dir_whip_settle",
                toolset="dir-whip",
                schema=SETTLE_TOOL_SCHEMA,
                handler=_settle_tool_handler,
            )
    except Exception as exc:
        logger.debug("dir-whip: lazy settle registration failed: %s", exc)


def _pending_violation_remove(session_id, key):
    """Drop one settled path from the owner's pending set."""
    owner = owner_session(session_id) or session_id
    with state.audit.lock:
        state.audit.pending_violations.get(owner, {}).pop(key, None)


def _record_settle_stats(working_dir_root):
    """Record one settle action: stats + log only, NO bus event (the emit
    surface stays at 7 events).

    Two counter shapes are maintained: the standard nested verdict counter
    via stats.record (which also appends the stats.jsonl line) AND the
    flat ("allow", "settle", RULE_KEY_WRITE_AUDIT_SETTLE) tuple key that
    stats_snapshot() exposes.
    """
    try:
        stats_record(
            "allow", "settle", RULE_KEY_WRITE_AUDIT_SETTLE,
            target=None, reason="same-turn self-heal settlement",
            working_dir_root=working_dir_root,
        )
        with state.stats.lock:
            flat_key = ("allow", "settle", RULE_KEY_WRITE_AUDIT_SETTLE)
            state.stats.counters[flat_key] = (
                state.stats.counters.get(flat_key, 0) + 1
            )
    except Exception as exc:
        logger.debug("dir-whip: settle stats error (ignored): %s", exc)


def _record_settle_rejected(reason, is_subagent=False):
    """Record one settle rejection/failure: stats row + WARNING log only,
    NO bus event (the emit surface stays at 7 events).

    reason is a category code -- subagent-rejected / invalid-paths /
    not-in-pending / move-failed; raw paths are never carried (privacy).
    The block outcome cannot ride events.emit (it would fan out a generic
    blocked bus event), so it uses the stats channel directly. Fail-open:
    never raises.
    """
    try:
        stats_record(
            "block", "settle", RULE_KEY_WRITE_AUDIT_SETTLE_REJECTED,
            target=None, reason=reason, is_subagent=is_subagent,
        )
        logger.warning("dir-whip: settle rejected (%s)", reason)
    except Exception as exc:
        logger.debug(
            "dir-whip: settle-rejected stats error (ignored): %s", exc
        )


def _resolve_settle_keys(paths, working_dir_root, pending):
    """Validate EVERY path against the pending set BEFORE touching the
    filesystem (all-or-nothing; zero arbitrary move capability).

    Returns (keys, error): error is the rejection dict on the first
    invalid/unknown entry (rejection recorded first), None when every
    path is accepted.
    """
    keys = []
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            _record_settle_rejected("invalid-paths")
            return None, {"error": "invalid path entry: %r" % (path,)}
        candidate = path if os.path.isabs(path) else os.path.join(
            working_dir_root, path
        )
        key = audit_norm_path(candidate)
        if key not in pending:
            _record_settle_rejected("not-in-pending")
            return None, {"error": "path is not in the pending violation "
                                   "set: %s" % str(path).replace("\\", "/")}
        keys.append(key)
    return keys, None


def _settle_move_one(session_id, key, working_dir_root, quarantine_dir):
    """Move one accepted pending key into the quarantine.

    A key that no longer exists is an idempotent successful no-op
    settlement (matches the latch's lexists semantics). Returns the
    root-relative display path.
    """
    if not os.path.lexists(key):
        # Idempotent no-op: user already removed/moved it.
        _pending_violation_remove(session_id, key)
        return relativize_target(key, working_dir_root)
    os.makedirs(quarantine_dir, exist_ok=True)
    dest = os.path.join(quarantine_dir, os.path.basename(key))
    stem, ext = os.path.splitext(dest)
    suffix = 1
    while os.path.lexists(dest):
        dest = "%s_%d%s" % (stem, suffix, ext)
        suffix += 1
    shutil.move(key, dest)
    _pending_violation_remove(session_id, key)
    return relativize_target(key, working_dir_root)


def settle_paths(session_id, paths):
    """dir_whip_settle core: move pending root writes into the audit
    quarantine, settling the L3 latch.

    Hard constraints: subagent sessions rejected (remediation is the
    parent's job); ONLY paths currently in this session's pending set are
    accepted (zero arbitrary filesystem capability -- unknown paths are
    rejected before any filesystem action, all-or-nothing); relative args
    are resolved against working_dir_root then matched against the
    normalized pending keys. Each accepted path is shutil.move'd into
    <dir-whip home>/audit-quarantine/<YYYYMMDD_HHMMSS>/ (layout-aware
    profile home, the stats.jsonl family -- out of the workspace root;
    legacy <root>/.hermes/ quarantine data is NOT migrated) and dropped
    from the pending set; a path that no longer exists is an idempotent
    successful no-op settlement. Returns {"settled": [<root-relative
    paths>]} (relative for privacy) or {"error": "<reason>"} on
    rejection/failure -- fail-open: a move error leaves the latch
    latched.
    """
    try:
        if session_id and subagents._is_subagent_session(session_id):
            _record_settle_rejected("subagent-rejected", is_subagent=True)
            return {"error": "subagent sessions cannot settle; report the "
                             "pending path(s) to the parent agent"}
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, (list, tuple)) or not paths:
            _record_settle_rejected("invalid-paths")
            return {"error": "paths must be a non-empty list"}
        working_dir_root, _allowlist = get_cached_config(
            state.session.registered_ctx
        )
        if not working_dir_root:
            # Operation-level failure: the settle cannot proceed without
            # a resolved root (same failure class as a failed move).
            _record_settle_rejected("move-failed")
            return {"error": "working_dir_root unresolved; cannot settle"}
        pending = pending_violation_snapshot(session_id)
        keys, error = _resolve_settle_keys(paths, working_dir_root, pending)
        if error:
            return error
        # The quarantine lives under the dir-whip home (<profile
        # home>/dir-whip/audit-quarantine/<ts>/), layout-aware via
        # paths.profile_home -- the stats.jsonl / dir-whip.log family.
        # Out of the workspace root; no legacy data migration.
        home = dirwhip_home(state.session.session_profile)
        quarantine_dir = os.path.join(
            str(home), "audit-quarantine",
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        settled = [
            _settle_move_one(session_id, key, working_dir_root, quarantine_dir)
            for key in keys
        ]
        _record_settle_stats(working_dir_root)
        return {"settled": settled}
    except Exception as exc:
        _record_settle_rejected("move-failed")
        logger.debug("dir-whip: settle_paths error (fail-open): %s", exc)
        return {"error": "settle failed: %s" % exc}


__all__ = [
    "set_classifier",
    "AUDIT_PAIRED_TOOLS",
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
    "SETTLE_TOOL_SCHEMA",
    "lazy_register_settle_tool",
    "settle_paths",
]
