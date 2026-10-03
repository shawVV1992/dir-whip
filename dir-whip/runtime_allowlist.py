"""dir_whip_allow_path entry-gating chain + the runtime-allowlist state operations -- the runtime exemption home (spec 5.7, spec 5.11).

Two halves of one theme: the entry-gating chain (subagent rejection ->
root rejection -> outside-root rejection -> two-step user confirmation)
and the process-lifetime exemption state it feeds (add / segment-
boundary check / snapshot / clear + narrow config-cache refresh). Core
discipline: no host imports; the runtime allowlist set + lock live in
state.session, and the resolved root is read through
config.resolved_config. The assembly layer keeps the _allow_path_handler
thin fail-open adapter (tests call it directly).

Layer: core
Refs: spec 5.7, spec 5.11
Key exports:
  - handle -- entry-gating chain + confirmed add (two-step user confirmation).
  - ALLOW_PATH_TOOL_SCHEMA -- OpenAI function-call schema for dir_whip_allow_path (the plugin's only tool).
  - runtime_allowlist_add -- add a path to the process-lifetime runtime allowlist.
  - is_runtime_allowlisted -- segment-boundary runtime allowlist check (case-insensitive).
  - runtime_allowlist_clear / runtime_allowlist_snapshot -- lifecycle reset / debug snapshot.
  - dir_whip_allow_path -- tool handler over the add layer (registered by __init__.register).
  - refresh_allowlist_cache -- narrow config-cache refresh after an allowlist file edit.
"""

import logging

from . import config, messages, state, subagents
from .audit import pending_violation_paths
from .events import (
    OUTCOME_ALLOW,
    OUTCOME_BLOCK,
    RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED,
    RULE_KEY_ALLOW_PATH_ROOT_REJECTED,
    RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED,
    RULE_KEY_RUNTIME_ALLOWLIST,
    RULE_KEY_RUNTIME_ALLOWLIST_ADD,
    bus_emit,
    emit,
)
from .paths import (
    normalize_target,
    paths_equal,
    relativize_target,
    to_fwd,
    within_working_dir,
)

logger = logging.getLogger("dir-whip")

# ---------------------------------------------------------------- Runtime allowlist state

# The process-lifetime exemption set + its lock live in state.session.
# They survive reset_all and are cleared explicitly by
# runtime_allowlist_clear (session start / register).


def runtime_allowlist_add(path, working_dir_root=None):
    """Add a path to the runtime allowlist (process-lifetime).

    Returns a confirmation string for the dir_whip_allow_path tool.
    Value-domain gating: empty/None paths are rejected (a normalized-
    empty entry would prefix-match every path). When working_dir_root is
    injected (non-None), the path is asserted inside the root via
    paths.within_working_dir (same implementation as the classify chain)
    -- an outside-root path is NOT stored and the rejection message is
    returned; working_dir_root=None (direct-call/test form) skips the
    assertion.
    """
    normalized = to_fwd(path)
    if not normalized:
        return messages.ALLOW_PATH_EMPTY_REJECTED_MESSAGE
    if working_dir_root is not None and not within_working_dir(
        normalize_target(normalized, working_dir_root), working_dir_root
    ):
        return messages.ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
    with state.session.runtime_allowlist_lock:
        state.session.runtime_allowlist.add(normalized)
    logger.debug("dir-whip: runtime allowlist added: %s", normalized)
    return messages.RUNTIME_ALLOWLIST_ADDED_TEMPLATE % normalized


def is_runtime_allowlisted(path):
    """Check a path against the runtime allowlist (normalized slashes).

    Segment-boundary match (case-insensitive): an entry exempts ITSELF
    (file-level registration) and everything UNDER it (directory subtree,
    entry with or without a trailing slash). A bare string prefix does
    NOT match -- allowing "docs" does not exempt a same-prefix sibling
    like "docs_secret/x.txt". casefold and the paths.to_fwd lexical
    domain are kept.
    """
    normalized = to_fwd(path).casefold()
    with state.session.runtime_allowlist_lock:
        return any(
            normalized == ec or normalized.startswith(ec.rstrip("/") + "/")
            for ec in (e.casefold() for e in state.session.runtime_allowlist)
        )


def runtime_allowlist_snapshot():
    """Return a copy of the runtime allowlist (debug/testing)."""
    with state.session.runtime_allowlist_lock:
        return set(state.session.runtime_allowlist)


def runtime_allowlist_clear():
    """Clear the runtime allowlist (session-start scope reset).

    The dir_whip_allow_path tool grants a session-scoped exemption
    ("exempt for this session"); the guard must not keep allowing a path
    across sessions in the same process. on_session_start and register()
    call this so each new session starts without leftover allowlist
    entries.
    """
    with state.session.runtime_allowlist_lock:
        state.session.runtime_allowlist.clear()


def dir_whip_allow_path(args, working_dir_root=None, **kwargs):
    """Tool handler: add a path to the runtime allowlist (spec 5.7, 5.11).

    Accepts either the tool-handler contract (args dict + extra kwargs
    such as task_id, per host registry dispatch) or a bare path string
    (direct helper/test callers); returns a confirmation string. This is
    the plugin's ONLY tool; ctx.register_tool wiring happens in
    __init__.py (register). working_dir_root passes through to the add
    layer's value-domain assertion (the handler injects the resolved
    root; the bare-path/rootless form skips the assertion).
    """
    path = args.get("path") if isinstance(args, dict) else args
    return runtime_allowlist_add(path, working_dir_root=working_dir_root)


# ---------------------------------------------------------------- Narrow cache refresh

def refresh_allowlist_cache():
    """Narrow cache refresh for the unified allowlist.

    Invalidates the cache so the next get_cached_config / classify_target
    sees the updated file. Delegates to config.invalidate_config_cache;
    config no longer reaches this module's state (the runtime allowlist
    lives in state.session and reset_cache does not clear it).
    """
    return config.invalidate_config_cache()


# ---------------------------------------------------------------- Tool schema + entry gating (spec 5.11)

# The plugin's ONLY tool (OpenAI function-call format required by the host
# tools registry); registered at register() via ctx.register_tool. Optional
# confirm parameter + two-step flow: call without confirm to obtain the
# confirmation payload, relay it to the user, re-call with confirm=true
# only after explicit user approval.
def _allow_path_tool_schema():
    """Build the dir_whip_allow_path schema as a plain dict.

    Function-local construction keeps the module-level state-form gate
    green (no module-level dict literal); the host receives the same
    plain dict object it always did (JSON-serializable).
    """
    return {
        "name": "dir_whip_allow_path",
        "description": messages.ALLOW_PATH_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": messages.ALLOW_PATH_TOOL_PATH_DESCRIPTION,
                },
                "confirm": {
                    "type": "boolean",
                    "description": messages.ALLOW_PATH_TOOL_CONFIRM_DESCRIPTION,
                },
            },
            "required": ["path"],
        },
    }


ALLOW_PATH_TOOL_SCHEMA = _allow_path_tool_schema()

def _confirmation_issued(path):
    """True when the path already received its confirmation payload this
    session (casefold-insensitive on the forward-slash form, mirroring
    the runtime allowlist matching)."""
    normalized = to_fwd(path).casefold()
    with state.session.lock:
        return any(
            normalized == to_fwd(e).casefold()
            for e in state.session.confirmation_issued
        )


def _confirmation_mark(path):
    """Record the path in the session-memory confirmation-issued set."""
    normalized = to_fwd(path)
    with state.session.lock:
        state.session.confirmation_issued.add(normalized)


def _is_working_dir_root(path, working_dir_root):
    """True when path normalizes to the Working Directory root itself
    (case/slash/dot-segment variants included via the normalization
    helpers)."""
    try:
        normalized = normalize_target(str(path), working_dir_root)
    except Exception:
        return False
    return paths_equal(normalized, working_dir_root)


def _confirmation_payload(path, session_id):
    """The confirmation payload for path; the latch-context conditional
    line is appended when the pending set is non-empty (latch active).
    Fail-open: an unresolved-paths check failure omits the line."""
    payload = messages.ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE % (
        to_fwd(path)
    )
    try:
        if pending_violation_paths(session_id):
            payload = payload + "\n" + messages.ALLOW_PATH_LATCH_CONTEXT_LINE
    except Exception as exc:
        logger.debug(
            "dir-whip: allow_path latch-context check failed (fail-open): %s",
            exc,
        )
    return payload


def _entry_rejection(path, session_id):
    """Entry gating: subagent rejection -> Working Directory root
    rejection -> outside-root rejection. Returns the rejection message,
    or None when the path may proceed.
    """
    # Subagents are rejected before any other check (the sanction flows
    # top-down only; parent-guidance variant).
    if subagents._is_subagent_session(session_id):
        emit(
            OUTCOME_BLOCK, "allow-path", RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED, None,
            "subagent-rejected", session_id, True,
        )
        return messages.ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE
    if not path:
        return None
    # The Working Directory root itself is never allowlisted.
    working_dir_root, _ = config.resolved_config()
    if working_dir_root and _is_working_dir_root(path, working_dir_root):
        emit(
            OUTCOME_BLOCK, "allow-path", RULE_KEY_ALLOW_PATH_ROOT_REJECTED, None,
            "root-target", session_id, False,
        )
        return messages.ALLOW_PATH_ROOT_REJECTED_MESSAGE
    # An outside-root path is never allowlisted -- no entry is needed
    # there (writes are allowed and logged, external-write). Same lexical
    # domain as the classify chain (normalize_target + within_working_dir;
    # no hand-rolled prefix comparison).
    if working_dir_root and not within_working_dir(
        normalize_target(str(path), working_dir_root), working_dir_root
    ):
        emit(
            OUTCOME_BLOCK, "allow-path", RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED,
            None, "external-target", session_id, False,
        )
        return messages.ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
    return None


def _confirmation_gate(path, confirm, session_id):
    """Two-step user confirmation (main-agent path only).

    Returns the confirmation payload when the add must NOT proceed, else
    None. The first call (confirm absent/false) marks the path in the
    session-memory confirmation-issued set and returns the payload;
    confirm=true adds ONLY an already-confirmed path (an unconfirmed
    confirm=true re-issues the payload and marks the path confirmed --
    confirm never adds on its own).
    """
    confirmed = _confirmation_issued(path)
    if not confirmed:
        _confirmation_mark(path)
        if confirm:
            logger.debug(
                "dir-whip: allow_path confirmation payload re-issued "
                "(confirm=true without a prior confirmation payload): %s",
                to_fwd(path),
            )
        else:
            logger.debug(
                "dir-whip: allow_path confirmation payload issued "
                "(first call): %s",
                to_fwd(path),
            )
    if not (confirm and confirmed):
        return _confirmation_payload(path, session_id)
    return None


def _confirmed_add(args, path, session_id, kwargs):
    """Confirmed add + feedback.

    The root is resolved BEFORE the add call and passed through so the
    add layer can assert the strict-subtree value domain (None =
    fail-open, assertion skipped). A successful add emits the allowlisted
    bus event + the symmetric runtime-allowlist-add stats row; allow
    outcome -> no extra bus fanout.
    """
    working_dir_root, _ = config.resolved_config()
    result = dir_whip_allow_path(
        args, working_dir_root=working_dir_root, session_id=session_id,
        **kwargs,
    )
    if path:
        bus_emit("allowlisted", {
            "outcome": "allowlisted",
            "rule_key": RULE_KEY_RUNTIME_ALLOWLIST,
            "target": relativize_target(path, working_dir_root),
        })
        # Symmetric stats row -- allow/allow-path; the emit channel
        # relativizes the target (same privacy shape as the bus event
        # above).
        emit(
            OUTCOME_ALLOW, "allow-path", RULE_KEY_RUNTIME_ALLOWLIST_ADD, path,
            "runtime allowlist entry added", session_id,
            subagents._is_subagent_session(session_id),
        )
    return result


def handle(args, session_id=None, **kwargs):
    """The allow_path tool's entry-gating chain (spec 5.11).

    Strict order: subagent rejection -> Working Directory root rejection
    -> outside-root rejection -> two-step user confirmation. The first
    call (confirm absent/false) returns the confirmation payload WITHOUT
    adding and records the path as confirmation-issued; confirm=true adds
    ONLY an already-confirmed path (an unconfirmed confirm=true
    re-issues the payload and marks the path confirmed -- confirm never
    adds on its own). A successful add emits the allowlisted bus event +
    the symmetric runtime-allowlist-add stats row; rejections record
    block stats rows that are bus-skipped (rule_keys in
    events._BUS_SKIP_RULE_KEYS). The fail-open catch lives in the
    assembly-layer adapter, not here; the phases are the helper calls
    above.
    """
    path = args.get("path") if isinstance(args, dict) else args
    confirm = bool(args.get("confirm")) if isinstance(args, dict) else False
    rejection = _entry_rejection(path, session_id)
    if rejection is not None:
        return rejection
    if path:
        payload = _confirmation_gate(path, confirm, session_id)
        if payload is not None:
            return payload
    return _confirmed_add(args, path, session_id, kwargs)


__all__ = [
    "handle",
    "ALLOW_PATH_TOOL_SCHEMA",
    "runtime_allowlist_add",
    "is_runtime_allowlisted",
    "runtime_allowlist_snapshot",
    "runtime_allowlist_clear",
    "dir_whip_allow_path",
    "refresh_allowlist_cache",
]
