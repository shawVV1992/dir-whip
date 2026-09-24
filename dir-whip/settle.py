"""L4 settlement family: dir_whip_settle tool surface + pending-path quarantine moves (spec 5.18 v2.7 R4/R5; split out of audit.py at SCR-055 R4).

The same-turn self-heal channel: validates EVERY requested path against the session's pending-violation set (zero arbitrary filesystem capability, all-or-nothing), moves accepted paths into <dir-whip home>/audit-quarantine/<YYYYMMDD_HHMMSS>/ (profile-aware, outside the workspace root) and drops them from the pending set. Registers dir_whip_settle LAZILY on the first L1 notice fire (the eager tool surface stays dir_whip_allow_path alone); the hook-side caller reaches lazy_register_settle_tool through a function-local import, so the module-level edge graph stays settle -> audit only. Depends on audit (pending API + key normalization), config/paths/stats/events/messages/subagents/state + stdlib only; no host imports (ADR-0007).

Layer: core+registration-helper
Refs: spec 5.18, spec v2.7 R4, spec v2.8 R1, SCR-040 R4, SCR-043 R5, SCR-045 R6, SCR-055 R4, ADR-0007
Key exports:
  - SETTLE_TOOL_SCHEMA -- dir_whip_settle OpenAI function schema (lazy-registration payload).
  - settle_paths -- dir_whip_settle core: quarantine pending root writes, settling the L3 latch.
  - lazy_register_settle_tool -- register dir_whip_settle on the first L1 notice fire (idempotent, fail-open).
"""

import datetime
import json
import logging
import os
import shutil

from . import state

from .audit import (
    audit_norm_path,
    pending_violation_snapshot,
)

from .config import get_cached_config

from .events import (
    RULE_KEY_WRITE_AUDIT_SETTLE,
    RULE_KEY_WRITE_AUDIT_SETTLE_REJECTED,
)

# Message templates: centralized in the core leaf module messages.py
# (spec 5.20, SCR-047 R1, ADR-0014).
from .messages import (
    SETTLE_TOOL_DESCRIPTION,
    SETTLE_TOOL_PATHS_DESCRIPTION,
)

from .paths import (
    dirwhip_home,
    relativize_target,
)

from .stats import stats_record

from .subagents import owner_session

# SCR-055 R4: the subagent gate reads the subagents-module attribute
# (SCR-050 v3 R6.1 TS-1 bans module-level private imports; attribute
# access is the sanctioned form).
from . import subagents

logger = logging.getLogger("dir-whip")


# ---------------------------------------------------------------- dir_whip_settle tool surface (5.18 v2.7 R4)

# dir_whip_settle tool schema (OpenAI function-call format, same contract
# as ALLOW_PATH_TOOL_SCHEMA). Defined HERE (not __init__.py) because the
# lazy registration fires from transform_tool_result without register()
# having run (test contract: first notice fire registers the tool).
# Description texts live in messages.py (spec 5.20, SCR-047 R1).
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
    """Registered dir_whip_settle handler: JSON-string tool result (R4)."""
    try:
        paths = args.get("paths") if isinstance(args, dict) else args
        return json.dumps(
            settle_paths(kwargs.get("session_id"), paths)
        )
    except Exception as exc:
        logger.debug("dir-whip: settle handler error (fail-open): %s", exc)
        return json.dumps({"error": "settle failed"})


def lazy_register_settle_tool():
    """Register dir_whip_settle on the FIRST L1 notice fire (R4).

    SCR-055 R4 public (cross-module consumer: audit_prompts.transform_tool_result,
    function-local import). The registry has no timing constraint (verified:
    the host rebuilds the per-turn tool list), so a late registration is
    visible from the next turn on. Idempotent by nature (re-register
    overwrites); attempted once per notice fire (fire-once per violation
    batch keeps this rare). Fail-open: any error is logged and never
    blocks the notice.
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
    """Drop one settled path from the owner's pending set (R4)."""
    owner = owner_session(session_id) or session_id
    with state.audit.lock:
        state.audit.pending_violations.get(owner, {}).pop(key, None)


def _record_settle_stats(working_dir_root):
    """Record one settle action (plan R4): stats + log only, NO bus event
    (the 5.14 emit surface stays at 7 events).

    Two counter shapes are maintained: the standard nested verdict counter
    via stats.record (which also appends the stats.jsonl line, 5.13 D3)
    AND the flat ("allow", "settle", RULE_KEY_WRITE_AUDIT_SETTLE) tuple key that
    the v0.5.0 acceptance test reads from stats_snapshot().
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
    """Move one accepted pending key into the quarantine (R4).

    A key that no longer exists is an idempotent successful no-op
    settlement (2026-08-26 ruling; matches the latch's lexists
    semantics). Returns the root-relative display path.
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
        # SCR-043 R5: the quarantine lives under the dir-whip home
        # (<profile home>/dir-whip/audit-quarantine/<ts>/), layout-aware
        # via paths.profile_home -- the stats.jsonl / dir-whip.log
        # family. Out of the workspace root; no legacy data migration.
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


# Declared L4 surface (SCR-055 R4): schema + the settlement core + the lazy
# registration entry (consumers: tests / the tool registry / audit_prompts).
__all__ = [
    "SETTLE_TOOL_SCHEMA",
    "lazy_register_settle_tool",
    "settle_paths",
]
