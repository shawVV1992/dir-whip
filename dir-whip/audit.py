"""Root write audit layer (spec 5.18) - detection backbone, pending
violations, L1 notice, L3 gate, pre/post snapshot pairing.

Snapshot/diff/classify kernels, the session-scoped pending-violation store
(the L3 latch input), the fire-once L1 notice, the settlement gate, and
the pre/post hook pairing. The classification chain is INJECTED via
set_classifier (assembly layer, ADR-0007 inject-don't-import) to break the
audit<->verdict cycle. Depends on paths/state/events/config/sessions +
stdlib only. No host imports (SCR-035 core module discipline, ADR-0007).
Extracted from dir_whip.py (task 31.12). Spec v2.6 B2 unified allowlist.
"""

import datetime
import json
import logging
import os

try:
    from . import state
except ImportError:
    import state

try:
    from .config import get_cached_config, write_audit_enabled, write_audit_entry_cap
except ImportError:
    from config import get_cached_config, write_audit_enabled, write_audit_entry_cap

try:
    from .events import _bus_emit, emit
except ImportError:
    from events import _bus_emit, emit

try:
    from .paths import relativize_target, within_working_dir
except ImportError:
    from paths import relativize_target, within_working_dir

try:
    from .sessions import _is_child_session, _record_top_session
except ImportError:
    from sessions import _is_child_session, _record_top_session

logger = logging.getLogger("dir-whip")

# Classification chain, injected by the assembly layer (register() now;
# __init__.py at 31.13). Unwired -> RuntimeError (production-unreachable:
# register() wires before any hook runs; the fail-open hook adapter
# catches it).
_classify_fn = None


def set_classifier(fn):
    """Wire the classification chain (assembly-layer injection, ADR-0007)."""
    global _classify_fn
    _classify_fn = fn


def snapshot(root):
    """Snap the top-level entries of root (spec 5.18 mechanism).

    Recorded per entry: (st_size, st_mtime_ns, is_dir) -- exactly the
    fidelity the diff needs. Scan OSError -> None (fail-open; callers
    silently skip the audit round). Never raises.
    """
    try:
        entries = {}
        with os.scandir(root) as it:
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


def audit_classify_diff(diff, before, after, working_dir_root, allowlist,
                        is_subagent=False):
    """Classify a snapshot diff into violations (spec 5.18, v2.6 B2).

    Only FILE entries are judged (is_dir -> never a violation; directory
    mtimes -- session dirs, `.git/`, `.hermes/` -- are ignored). A
    violation is a NEW or MODIFIED root-level file that classifies as a
    root-file block through the shared chain: not on the allowlist file
    entries, not under an allowlist prefix, not inside any session directory
    (the same allowlist key the guard reads, so the layers never disagree).
    Deletions are RECORD-ONLY (5.8 delete principle) -- surfaced in
    "recorded", never judged.

    Returns {"violations": [abs paths], "recorded": [deleted abs paths]}.
    """
    violations = []
    recorded = []
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
        if verdict["outcome"] == "block" and verdict["rule_key"] == "root-file":
            violations.append(abs_path)
    for name in diff.get("deleted", []):
        info = (before or {}).get(name)
        if info is None or info[2]:
            continue
        recorded.append(os.path.join(working_dir_root, name))
    return {"violations": sorted(violations), "recorded": sorted(recorded)}


def _audit_norm_path(path):
    """Deterministic pending-set key: absolute + native-normalized."""
    return os.path.normpath(str(path))


def _audit_now():
    """ISO-8601 timestamp (seconds precision) for first_seen."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _audit_owner_session(session_id):
    """Resolve the pending-set owner for a session (5.18 session scoping).

    Child sessions (child_session_ids gate, 5.4) write into the PARENT's
    pending set: the explicit parent link recorded by subagent_start wins;
    otherwise the most recent top-level session (the parent in the common
    sequential layout). Returns None when unknown -- callers fall back to
    the session id itself.
    """
    if session_id and _is_child_session(session_id):
        with state.audit.lock:
            return state.audit.session_parents.get(session_id) or state.audit.top_session
    return session_id


def audit_pending_snapshot(session_id=None):
    """Read-only copy of a session's pending violations (Lane 2b gate).

    The L3 gate reads this set; keys are absolute normpath'd paths, each
    value is {"first_seen": ISO-8601, "announced": bool}. "announced" is
    flipped by L1 (audit_mark_announced) so the fire-once notice never
    repeats; first_seen is preserved across re-detections. Child sessions
    resolve into the parent's set.
    """
    owner = _audit_owner_session(session_id) or session_id
    with state.audit.lock:
        return {
            path: dict(entry)
            for path, entry in state.audit.pending.get(owner, {}).items()
        }


def audit_pending_add(session_id, path, first_seen=None):
    """Add one pending violation (detection fills this structure).

    Existing entries are kept untouched on re-detection (first_seen and
    announced survive, so L1 fire-once semantics hold across rounds).
    """
    owner = _audit_owner_session(session_id) or session_id
    key = _audit_norm_path(path)
    with state.audit.lock:
        bucket = state.audit.pending.setdefault(owner, {})
        if key in bucket:
            return
        bucket[key] = {
            "first_seen": first_seen or _audit_now(),
            "announced": False,
        }


def audit_pending_clear(session_id):
    """Clear a session's pending violations (top-level session start)."""
    with state.audit.lock:
        state.audit.pending.pop(session_id, None)


def audit_mark_announced(session_id, path):
    """Flip the fire-once announced flag (L1 notice lane calls this)."""
    owner = _audit_owner_session(session_id) or session_id
    key = _audit_norm_path(path)
    with state.audit.lock:
        entry = state.audit.pending.get(owner, {}).get(key)
        if entry:
            entry["announced"] = True


def audit_unresolved_paths(session_id, working_dir_root=None, allowlist=None):
    """Settlement judgment for the L3 gate (Lane 2b input): re-scan the
    root and return the pending paths that STILL violate (file present and
    still classifying as an unprotected root-level file). A pending path
    is settled when it is gone, moved outside the root, or legalized
    (allowlist file / prefix / session dir). Fail-open: a failed re-scan keeps
    the full pending set (the gate stays latched).
    """
    try:
        pending = audit_pending_snapshot(session_id)
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
                path, working_dir_root, allowlist or [], is_subagent=False
            )
            if verdict["outcome"] == "block" and verdict["rule_key"] == "root-file":
                unresolved.append(path)
        return sorted(unresolved)
    except Exception as exc:
        logger.debug("dir-whip: audit settlement check error (fail-open): %s", exc)
        return sorted(audit_pending_snapshot(session_id))


def _audit_notice_message(paths):
    """The single L1 notice text (5.18, v2.6 B2): the paths and the remediation.
    One notice per result listing every unannounced violation; only this
    notice ever enters the conversation (context hygiene)."""
    lines = [
        "[dir-whip] Write audit: the following file(s) were written to the "
        "Working Directory root outside any Session Directory:"
    ]
    for path in paths:
        lines.append("  - %s" % str(path).replace("\\", "/"))
    lines.append(
        "Remediation: move the file(s) into a Session Directory "
        "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/) or add them to "
        "allowlist in dir-whip-config.yaml as file:<basename> (e.g. file:notes.txt). Further writes to "
        "the Working Directory are blocked until then."
    )
    return "\n".join(lines)


def on_transform_tool_result(tool_name=None, args=None, result=None,
                             session_id=None, task_id=None, **kwargs):
    """L1 fire-once notice hook (5.18), registered at register().

    Hermes first-party precedent (security-guidance): returning a string
    REPLACES the tool result the model sees next turn; None leaves it
    unchanged. The audit is terminal-triggered, so only TERMINAL results
    are decorated. Appends ONE notice naming every unannounced pending
    violation, then flips the announced flags (HARD fire-once constraint:
    one notice per violation, never re-appended -- context hygiene).
    Non-string results are untouched; JSON error results are not
    decorated; audit disabled or nothing unannounced -> None. Fail-open:
    any exception -> None, never raised.

    ORDERING FIX (live-verified 2026-08-22): for the terminal tool Hermes
    fires transform_tool_result BEFORE post_tool_call, so the audit re-scan
    (_audit_post_check) is run HERE first -- it pops the pre snapshot and
    fills the pending set, then the notice below reads it. The
    post_tool_call _audit_post_check call stays as an order-agnostic no-op
    fallback (the snapshot is already popped, so it skips). Because
    _audit_post_check pops its snapshot, the audit runs exactly once
    regardless of which hook fires first.
    """
    try:
        if tool_name != "terminal":
            return None
        if not write_audit_enabled():
            return None
        # Ordering fix: run the audit re-scan BEFORE reading the pending set
        # (transform fires before post_tool_call for terminal). Safe even if
        # the command was blocked-at-pre (no snapshot -> early return).
        _audit_post_check(
            session_id, task_id, is_subagent=_is_child_session(session_id),
        )
        if not isinstance(result, str):
            return None
        # Don't decorate error results (security-guidance precedent): the
        # model already has bigger problems; the notice waits for the
        # next eligible result instead.
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
                return None
        except (ValueError, TypeError):
            pass
        pending = audit_pending_snapshot(session_id)
        unannounced = [p for p, entry in pending.items() if not entry["announced"]]
        if not unannounced:
            return None
        for path in unannounced:
            audit_mark_announced(session_id, path)
        return result + "\n\n" + _audit_notice_message(unannounced)
    except Exception as exc:
        logger.debug("dir-whip: transform_tool_result error (fail-open): %s", exc)
        return None


def _audit_gate_unresolved(session_id, working_dir_root, allowlist):
    """Unresolved pending paths for the L3 gate (empty -> gate open).

    Respects the write_audit switch (disabled -> open). A failed root
    re-scan is handled inside audit_unresolved_paths (full pending set ->
    latch stays); any other gate-side error fails OPEN (5.8 -- the gate
    never breaks the guard).
    """
    try:
        if not write_audit_enabled():
            return []
        return audit_unresolved_paths(session_id, working_dir_root, allowlist)
    except Exception as exc:
        logger.debug("dir-whip: audit gate check error (fail-open): %s", exc)
        return []


def _audit_gate_block_message(display_paths, is_subagent):
    """L3 gate block message (5.18): unresolved paths + remediation, with
    the C6 [Reason]/[Next] cue (subagent variant: report to the parent)."""
    lines = [
        "BLOCKED: earlier command(s) wrote file(s) to the Working Directory "
        "root that still need remediation:"
    ]
    for path in display_paths:
        lines.append("  - %s" % path)
    if is_subagent:
        lines.append("Fix: report the pending path(s) to the parent agent "
                     "for remediation (do not create a session directory).")
    else:
        lines.append(
            "Fix: move the file(s) into a Session Directory "
            "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/) or add them to "
            "allowlist in dir-whip-config.yaml as file:<basename> (e.g. file:notes.txt)."
        )
    lines.append("Reply using the [Reason]/[Next] template.")
    return "\n".join(lines)


def _audit_gate_block(tool_name, session_id, is_subagent, working_dir_root,
                      unresolved):
    """Standard block-channel response for the L3 latch (5.18).

    Records a write-audit-gate-block verdict (5.13 stats/log; no generic
    blocked bus event -- the gate has its own) and emits the 5.14
    write-audit-gate-block bus event with privacy-shaped relative paths,
    then returns the block dict for the pre-tool channel.
    """
    rel_paths = [relativize_target(path, working_dir_root) for path in unresolved]
    emit(
        "block", tool_name, "write-audit-gate-block", None,
        "%d unresolved root write audit violation(s)" % len(unresolved),
        session_id, is_subagent,
    )
    _bus_emit("write-audit-gate-block", {
        "outcome": "block",
        "rule_key": "write-audit-gate-block",
        "paths": list(rel_paths),
        "latch": "latched",
    })
    display = [str(path).replace("\\", "/") for path in unresolved]
    return {
        "action": "block",
        "message": _audit_gate_block_message(display, is_subagent),
    }


def _audit_pre_snapshot(session_id, task_id, working_dir_root, allowlist):
    """Take the pre snapshot for an ALLOWED terminal call (5.18).

    Audit disabled (write_audit: false) -> nothing. Root entry count
    above write_audit_entry_cap -> round skipped + ONE WARNING per session
    (not repeated). Scan OSError -> fail-open (no snapshot stored, so the
    post skips). Any exception -> nothing (fail-open, 5.8).
    """
    try:
        if not write_audit_enabled():
            return
        snap = snapshot(working_dir_root)
        if snap is None:
            return
        cap = write_audit_entry_cap()
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
                snap, working_dir_root, tuple(allowlist),
            )
    except Exception as exc:
        logger.debug("dir-whip: audit pre-snapshot error (fail-open): %s", exc)


def _audit_post_check(session_id, task_id, is_subagent=False):
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
        if not write_audit_enabled():
            return
        after = snapshot(working_dir_root)
        if after is None:
            return
        diff = diff_snapshots(before, after)
        classified = audit_classify_diff(
            diff, before, after, working_dir_root, list(allowlist), is_subagent,
        )
        for path in classified["violations"]:
            audit_pending_add(session_id, path)
            emit(
                "block", "audit", "write-audit-violation", path,
                "root write audit violation (5.18)", session_id, is_subagent,
            )
            _bus_emit("write-audit-violation", {
                "outcome": "block",
                "rule_key": "write-audit-violation",
                "path": relativize_target(path, working_dir_root),
                "is_subagent": bool(is_subagent),
                "first_seen": (
                    audit_pending_snapshot(session_id)
                    .get(_audit_norm_path(path), {})
                    .get("first_seen")
                ),
            })
    except Exception as exc:
        logger.debug("dir-whip: audit post check error (fail-open): %s", exc)


# Public thin aliases (SCR-035 interface convergence point).
classify_diff = audit_classify_diff
unresolved_paths = audit_unresolved_paths
transform_tool_result = on_transform_tool_result
pending_snapshot = audit_pending_snapshot
pending_add = audit_pending_add
pending_clear = audit_pending_clear
mark_announced = audit_mark_announced

__all__ = [
    "set_classifier",
    "snapshot",
    "classify_diff",
    "unresolved_paths",
    "transform_tool_result",
    "pending_snapshot",
    "pending_add",
    "pending_clear",
    "mark_announced",
]


def _audit_session_start(session_id):
    """Top-level session start: clear this session's pending violations
    and leftover pre snapshots, reset the one-time cap warning, and record
    the current top-level session (child-inheritance fallback)."""
    try:
        audit_pending_clear(session_id)
        with state.audit.lock:
            stale = [k for k in state.audit.pre_snapshots if k[0] == session_id]
            for k in stale:
                state.audit.pre_snapshots.pop(k, None)
            # Lock strengthening (31.13, Controller addition #4): the
            # top_session / cap_warned writes share the pending lock per
            # the state.py skeleton intent.
            _record_top_session(session_id)
            state.audit.cap_warned = False
    except Exception as exc:
        logger.debug("dir-whip: audit session start error: %s", exc)
