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
import shutil

try:
    from . import state
except ImportError:
    import state

try:
    from .config import get_cached_config, write_audit_enabled, write_audit_entry_cap
except ImportError:
    from config import get_cached_config, write_audit_enabled, write_audit_entry_cap

try:
    from .stats import record as _stats_record
except ImportError:
    from stats import record as _stats_record

try:
    from .events import _bus_emit, emit
except ImportError:
    from events import _bus_emit, emit

try:
    from .paths import (
        _get_hermes_home,
        _profile_home,
        relativize_target,
        within_working_dir,
    )
except ImportError:
    from paths import (
        _get_hermes_home,
        _profile_home,
        relativize_target,
        within_working_dir,
    )

try:
    from .sessions import (
        _is_child_session,
        _record_top_session,
        owner_session,
    )
except ImportError:
    from sessions import (
        _is_child_session,
        _record_top_session,
        owner_session,
    )

# SCR-044 R5 (spec 5.19): the script-vector binding observer lives in
# session_dirs; the audit -> session_dirs direction is sanctioned (the
# reverse import would be a cycle and does not exist).
try:
    from . import session_dirs
except ImportError:
    import session_dirs

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
    """Thin delegate to sessions.owner_session (SCR-044 R3).

    Owner resolution moved next to the session topology it reads
    (state.session: session_parents / top_session); this same-name private
    delegate keeps every pending read/write call site here unchanged.
    Semantics identical: explicit parent > top_session fallback; None when
    unknown -- callers fall back to the session id itself.
    """
    return owner_session(session_id)


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

    SCR-041 R1 (spec 5.18 v2.9): the classification here is CONFIG-only
    (honor_runtime_allowlist=False) -- a runtime-allowlist entry is
    prospective-only and never settles a recorded violation; config
    allowlist files/dirs entries and session-dir containment still
    settle. Shared by the L3 gate and the continuation nudge.
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
                path, working_dir_root, allowlist or [], is_subagent=False,
                honor_runtime_allowlist=False,
            )
            if verdict["outcome"] == "block" and verdict["rule_key"] == "root-file":
                unresolved.append(path)
        return sorted(unresolved)
    except Exception as exc:
        logger.debug("dir-whip: audit settlement check error (fail-open): %s", exc)
        return sorted(audit_pending_snapshot(session_id))


def _remediation_instruction(paths_display):
    """Shared remediation sentence (5.18 v2.8 R1, single source of truth):
    the exact dir_whip_settle(paths=[...]) call form with absolute
    forward-slash paths and the quarantine location under the dir-whip
    home (<profile home>/dir-whip/audit-quarantine/; SCR-043 R5 moved it
    out of the workspace root -- layout-aware via paths._profile_home,
    the stats.jsonl / dir-whip.log family). Used by BOTH the L1 notice
    and the continuation nudge; the L3 gate message keeps its
    2026-08-26 short form. allow_path is never mentioned (settle-first
    ruling 2026-08-27). Fail-open: home unresolved -> the literal <home>
    placeholder."""
    try:
        home = _get_hermes_home()
        if state.session.session_profile:
            home = _profile_home(home, state.session.session_profile)
    except Exception:
        home = None
    quarantine = "%s/dir-whip/audit-quarantine/" % (
        str(home).replace("\\", "/") if home else "<home>"
    )
    return (
        "Remediate now: call dir_whip_settle(paths=[%s]) to move the "
        "file(s) into quarantine (%s), or move them manually into a "
        "Session Directory" % (
            ", ".join(
                '"%s"' % str(path).replace("\\", "/")
                for path in paths_display
            ),
            quarantine,
        )
    )


def _audit_notice_message(paths):
    """The single L1 notice text (5.18, v2.9 R4): the paths and the
    remediation via the shared _remediation_instruction helper (single
    source of truth with the continuation nudge). One notice per result
    listing every unannounced violation; only this notice ever enters
    the conversation (context hygiene). v2.9 R4: the config-allowlist
    option is attributed to the USER ("ask the user to add") with the
    exact command instruction and the latch-period freeze explicit
    (all writes frozen incl. config edits)."""
    lines = [
        "[dir-whip] Write audit: the following file(s) were written to the "
        "Working Directory root outside any Session Directory:"
    ]
    for path in paths:
        lines.append("  - %s" % str(path).replace("\\", "/"))
    lines.append(
        _remediation_instruction(paths)
        + " (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). To keep the "
        "file(s) at the root, ask the user to add them to the allowlist "
        "files entries in dir-whip-config.yaml (files: [notes.txt]) — "
        "give them the exact command to run: /dir-whip allow <path> — "
        "while the block is active all writes are frozen (config edits "
        "included). Further writes to the Working Directory are blocked "
        "until then."
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
        # R4 lazy registration: the settle tool enters the registry on the
        # FIRST notice fire (not at register() -- the eager tool surface is
        # pinned to dir_whip_allow_path alone). Registration failure must
        # never eat the notice (fail-open inside the helper).
        _lazy_register_settle_tool()
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
        # v2.9 R4 (SCR-041): the config-allowlist option is attributed to
        # the USER with the exact command and the latch-period freeze
        # explicit. The subagent variant and the settle call line below
        # are unchanged.
        lines.append(
            "Fix: move the file(s) into a Session Directory "
            "(YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/), or ask the user "
            "to add them to the allowlist files entries in "
            "dir-whip-config.yaml (files: [notes.txt]) — give them the "
            "exact command: /dir-whip allow <path> — while the block is "
            "active all writes are frozen (config edits included)."
        )
        # v2.7 R4 ruling (2026-08-26): the gate blocks remediation mv/rm,
        # so the message must name the tool channel or the loop never
        # closes. Subagent variant stays report-to-parent only.
        lines.append(
            "Remediate now: call dir_whip_settle(paths=[%s])." % ", ".join(
                '"%s"' % path for path in display_paths
            )
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
        # SCR-044 R5 (spec 5.19): script-vector creation observer.
        # Fires only when a pending_create marker exists; binds the
        # FIRST new compliant session dir under the root and ALWAYS
        # consumes the marker (a failed script leaves no ghost slot).
        if state.session_dirs.pending_create:
            session_dirs.observe_added(
                working_dir_root, session_id, diff.get("added", []),
            )
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


# ---------------------------------------------------------------- Same-turn self-heal (5.18 v2.7 R4/R5)

# dir_whip_settle tool schema (OpenAI function-call format, same contract
# as ALLOW_PATH_TOOL_SCHEMA). Defined HERE (not __init__.py) because the
# lazy registration fires from transform_tool_result without register()
# having run (test contract: first notice fire registers the tool).
SETTLE_TOOL_SCHEMA = {
    "name": "dir_whip_settle",
    "description": (
        "Move files that the dir-whip write audit flagged in the Working "
        "Directory root into the audit quarantine (.hermes/audit-quarantine/), "
        "settling the write block. Hard-constrained to paths currently "
        "listed as unresolved by the write-audit notice/gate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Paths to settle (absolute, forward slashes, as listed "
                    "by the write-audit notice; relative to the Working "
                    "Directory root tolerated)"
                ),
            }
        },
        "required": ["paths"],
    },
}


def _settle_tool_handler(args, **kwargs):
    """Registered dir_whip_settle handler: JSON-string tool result (R4)."""
    try:
        paths = args.get("paths") if isinstance(args, dict) else args
        return json.dumps(
            audit_settle_paths(kwargs.get("session_id"), paths)
        )
    except Exception as exc:
        logger.debug("dir-whip: settle handler error (fail-open): %s", exc)
        return json.dumps({"error": "settle failed"})


def _lazy_register_settle_tool():
    """Register dir_whip_settle on the FIRST L1 notice fire (R4).

    The registry has no timing constraint (verified: the host rebuilds the
    per-turn tool list), so a late registration is visible from the next
    turn on. Idempotent by nature (re-register overwrites); attempted once
    per notice fire (fire-once per violation batch keeps this rare).
    Fail-open: any error is logged and never blocks the notice.
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


def _audit_pending_remove(session_id, key):
    """Drop one settled path from the owner's pending set (R4)."""
    owner = _audit_owner_session(session_id) or session_id
    with state.audit.lock:
        state.audit.pending.get(owner, {}).pop(key, None)


def _record_settle_stats(working_dir_root):
    """Record one settle action (plan R4): stats + log only, NO bus event
    (the 5.14 emit surface stays at 7 events).

    Two counter shapes are maintained: the standard nested verdict counter
    via stats.record (which also appends the stats.jsonl line, 5.13 D3)
    AND the flat ("allow", "settle", "write-audit-settle") tuple key that
    the v0.5.0 acceptance test reads from stats_snapshot().
    """
    try:
        _stats_record(
            "allow", "settle", "write-audit-settle",
            target=None, reason="same-turn self-heal settlement",
            working_dir_root=working_dir_root,
        )
        with state.stats.lock:
            flat_key = ("allow", "settle", "write-audit-settle")
            state.stats.counters[flat_key] = (
                state.stats.counters.get(flat_key, 0) + 1
            )
    except Exception as exc:
        logger.debug("dir-whip: settle stats error (ignored): %s", exc)


def _record_settle_rejected(reason, is_subagent=False):
    """Record one settle rejection/failure (SCR-040 R4, 5.13 v2.8): stats
    row + WARNING log only, NO bus event (the 5.14 emit surface stays at
    7 events).

    reason is a category code -- subagent-rejected / invalid-paths /
    not-in-pending / move-failed; raw paths are never carried (5.13
    privacy). The block outcome cannot ride events.emit (it would fan
    out a generic blocked bus event), so it uses the stats channel
    directly. Fail-open: never raises.
    """
    try:
        _stats_record(
            "block", "settle", "write-audit-settle-rejected",
            target=None, reason=reason, is_subagent=is_subagent,
        )
        logger.warning("dir-whip: settle rejected (%s)", reason)
    except Exception as exc:
        logger.debug(
            "dir-whip: settle-rejected stats error (ignored): %s", exc
        )


def audit_settle_paths(session_id, paths):
    """dir_whip_settle core (5.18 R4): move pending root writes into the
    audit quarantine, settling the L3 latch.

    Hard constraints: subagent sessions rejected (remediation is the
    parent's job); ONLY paths currently in this session's pending set are
    accepted (zero arbitrary filesystem capability -- unknown paths are
    rejected before any filesystem action, all-or-nothing); relative args
    are resolved against working_dir_root then matched against the
    normalized pending keys. Each accepted path is shutil.move'd into
    <dir-whip home>/audit-quarantine/<YYYYMMDD_HHMMSS>/ (SCR-043 R5:
    layout-aware profile home, the stats.jsonl family -- relocated out
    of the workspace root; legacy <root>/.hermes/ quarantine data is
    NOT migrated; audit-safe: the snapshot only judges root-top-level
    FILE entries) and dropped from the pending set. A pending path that
    no longer exists is an idempotent successful no-op settlement
    (2026-08-26 ruling; matches the latch's lexists semantics). Returns
    {"settled": [<root-relative paths>]} on success (relative for
    privacy) or {"error": "<reason>"} on rejection/failure -- fail-open:
    a move error leaves the latch latched.
    """
    try:
        if session_id and _is_child_session(session_id):
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
        pending = audit_pending_snapshot(session_id)
        # Validate EVERY path against the pending set BEFORE touching the
        # filesystem (all-or-nothing; zero arbitrary move capability).
        keys = []
        for path in paths:
            if not isinstance(path, str) or not path.strip():
                _record_settle_rejected("invalid-paths")
                return {"error": "invalid path entry: %r" % (path,)}
            candidate = path if os.path.isabs(path) else os.path.join(
                working_dir_root, path
            )
            key = _audit_norm_path(candidate)
            if key not in pending:
                _record_settle_rejected("not-in-pending")
                return {"error": "path is not in the pending violation "
                                 "set: %s" % str(path).replace("\\", "/")}
            keys.append(key)
        # SCR-043 R5: the quarantine lives under the dir-whip home
        # (<profile home>/dir-whip/audit-quarantine/<ts>/), layout-aware
        # via paths._profile_home -- the stats.jsonl / dir-whip.log
        # family. Out of the workspace root; no legacy data migration.
        home = _get_hermes_home()
        if state.session.session_profile:
            home = _profile_home(home, state.session.session_profile)
        quarantine_dir = os.path.join(
            str(home), "dir-whip", "audit-quarantine",
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        settled = []
        for key in keys:
            if not os.path.lexists(key):
                # Idempotent no-op: user already removed/moved it.
                _audit_pending_remove(session_id, key)
                settled.append(relativize_target(key, working_dir_root))
                continue
            os.makedirs(quarantine_dir, exist_ok=True)
            dest = os.path.join(quarantine_dir, os.path.basename(key))
            stem, ext = os.path.splitext(dest)
            suffix = 1
            while os.path.lexists(dest):
                dest = "%s_%d%s" % (stem, suffix, ext)
                suffix += 1
            shutil.move(key, dest)
            _audit_pending_remove(session_id, key)
            settled.append(relativize_target(key, working_dir_root))
        _record_settle_stats(working_dir_root)
        return {"settled": settled}
    except Exception as exc:
        _record_settle_rejected("move-failed")
        logger.debug("dir-whip: settle_paths error (fail-open): %s", exc)
        return {"error": "settle failed: %s" % exc}


# SCR-040 R2: session-cumulative continuation-nudge cap (hardcoded, no
# config key). At most 3 nudges per session lifetime; the host's per-turn
# verify-nudge budget (max_verify_nudges) remains the outer bound.
PRE_VERIFY_NUDGE_CAP = 3


def audit_pre_verify_nudge(session_id=None, changed_paths=None, **kwargs):
    """pre_verify continuation fallback decision (5.18 v2.8 R1/R2).

    Nudge ({"action": "continue", ...}) only when the host reports file
    mutations this turn (changed_paths non-empty) AND this session still
    has unresolved pending violations; any other case returns None so the
    turn finishes naturally. Subagent sessions no-op (remediation is the
    parent's job). Session-cumulative cap: at most PRE_VERIFY_NUDGE_CAP
    nudges per session lifetime (counter in state.audit.nudge_counts,
    reset at session start); after the cap the hook returns None and the
    turn finishes naturally. The host's per-turn verify-nudge budget
    remains the outer bound; the host `attempt` kwarg is ignored.
    Fail-open: any exception -> None.
    """
    try:
        if not changed_paths:
            return None
        if session_id and _is_child_session(session_id):
            return None
        if not write_audit_enabled():
            return None
        unresolved = audit_unresolved_paths(session_id)
        if not unresolved:
            return None
        with state.audit.lock:
            count = state.audit.nudge_counts.get(session_id, 0)
            if count >= PRE_VERIFY_NUDGE_CAP:
                return None
            state.audit.nudge_counts[session_id] = count + 1
        # SCR-040 R4 (5.13 v2.8): observability row for the actual nudge
        # fire -- allow/verify, target=None (the paths are already carried
        # by the violation events; privacy does not repeat them), reason
        # carries the 1-based session-cumulative attempt ordinal (the cap
        # counter value AFTER increment). Allow outcome -> no bus fanout
        # (the 5.14 emit surface stays at 7).
        emit(
            "allow", "verify", "pre-verify-nudge", None,
            "continuation nudge issued (attempt %d)" % (count + 1),
            session_id, False,
        )
        display = [str(path).replace("\\", "/") for path in unresolved]
        # v2.9 R4 third-review tail (SCR-041): the resolution choice is
        # presented to the USER; the keep-at-root command carries the
        # REAL absolute forward-slash path(s) (copy-paste runnable; the
        # /dir-whip allow command accepts whitespace-separated batches).
        keep_command = "/dir-whip allow %s" % " ".join(display)
        return {
            "action": "continue",
            "message": (
                "[dir-whip] %d unresolved root write(s) remain at the "
                "Working Directory root. %s. Present the resolution "
                "choice to the user: move the file(s) (settle), or keep "
                "them at the root — for the keep-at-root choice, give "
                "the user the exact command to run: %s. Finish only "
                "after settlement or the user's decision."
                % (len(display), _remediation_instruction(display),
                   keep_command)
            ),
        }
    except Exception as exc:
        logger.debug("dir-whip: pre_verify nudge error (fail-open): %s", exc)
        return None


# Public thin aliases (SCR-035 interface convergence point).
classify_diff = audit_classify_diff
unresolved_paths = audit_unresolved_paths
transform_tool_result = on_transform_tool_result
pending_snapshot = audit_pending_snapshot
pending_add = audit_pending_add
pending_clear = audit_pending_clear
mark_announced = audit_mark_announced
settle_paths = audit_settle_paths
pre_verify_nudge = audit_pre_verify_nudge

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
    "settle_paths",
    "pre_verify_nudge",
]


def _audit_session_start(session_id):
    """Top-level session start: clear this session's pending violations
    and leftover pre snapshots, reset the one-time cap warning and the
    continuation-nudge cap counter (SCR-040 R2), and record the current
    top-level session (child-inheritance fallback)."""
    try:
        audit_pending_clear(session_id)
        with state.audit.lock:
            stale = [k for k in state.audit.pre_snapshots if k[0] == session_id]
            for k in stale:
                state.audit.pre_snapshots.pop(k, None)
            # SCR-040 R2: the nudge cap counter resets at session start
            # (same place session-start clears pending).
            state.audit.nudge_counts.pop(session_id, None)
            # Lock note (31.13, Controller addition #4): cap_warned stays
            # under the pending lock; SCR-044 R3 moved top_session to
            # state.session (plain write via _record_top_session, as
            # before).
            _record_top_session(session_id)
            state.audit.cap_warned = False
    except Exception as exc:
        logger.debug("dir-whip: audit session start error: %s", exc)
