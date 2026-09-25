"""dir_whip_allow_path entry-gating chain + the runtime-allowlist state operations -- the runtime exemption home (spec 5.7, spec 5.11).

Two halves of one theme: the entry-gating chain (subagent rejection ->
root rejection -> outside-root rejection -> two-step user confirmation)
and the process-lifetime exemption state it feeds (add / segment-
boundary check / snapshot / clear + narrow config-cache refresh). Core
discipline: no host imports; guard is imported function-locally only
(cycle break). The assembly layer keeps the _allow_path_handler thin
fail-open adapter (tests call it directly).

Layer: core
Refs: spec 5.7, spec 5.11, SCR-043
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
import threading

from . import config, state, subagents
from .audit import pending_violation_paths
# Message templates live in the core leaf messages.py; same-name aliases
# keep runtime_allowlist.* call sites and test import paths unchanged
# (the EXTERNAL rejection message is single-sourced there).
from .messages import (
    ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE,
    ALLOW_PATH_EMPTY_REJECTED_MESSAGE,
    ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE,
    ALLOW_PATH_LATCH_CONTEXT_LINE,
    ALLOW_PATH_ROOT_REJECTED_MESSAGE,
    ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE,
    ALLOW_PATH_TOOL_CONFIRM_DESCRIPTION,
    ALLOW_PATH_TOOL_DESCRIPTION,
    ALLOW_PATH_TOOL_PATH_DESCRIPTION,
    RUNTIME_ALLOWLIST_ADDED_TEMPLATE,
)
from .events import (
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
    within_working_dir,
)

logger = logging.getLogger("dir-whip")

# ---------------------------------------------------------------- Runtime allowlist state


_runtime_allowlist = set()
_runtime_allowlist_lock = threading.Lock()


def _normalize_allowlist_path(path):
    """Normalize a path for allowlist comparison (forward slashes)."""
    if path is None:
        return ""
    return str(path).replace("\\", "/")


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
    normalized = _normalize_allowlist_path(path)
    if not normalized:
        return ALLOW_PATH_EMPTY_REJECTED_MESSAGE
    if working_dir_root is not None and not within_working_dir(
        normalize_target(normalized, working_dir_root), working_dir_root
    ):
        return ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
    with _runtime_allowlist_lock:
        _runtime_allowlist.add(normalized)
    logger.debug("dir-whip: runtime allowlist added: %s", normalized)
    return RUNTIME_ALLOWLIST_ADDED_TEMPLATE % normalized


def is_runtime_allowlisted(path):
    """Check a path against the runtime allowlist (normalized slashes).

    Segment-boundary match (case-insensitive): an entry exempts ITSELF
    (file-level registration) and everything UNDER it (directory subtree,
    entry with or without a trailing slash). A bare string prefix does
    NOT match -- allowing "docs" does not exempt a same-prefix sibling
    like "docs_secret/x.txt". casefold and the forward-slash
    _normalize_allowlist_path lexical domain are kept.
    """
    normalized = _normalize_allowlist_path(path).casefold()
    with _runtime_allowlist_lock:
        return any(
            normalized == ec or normalized.startswith(ec.rstrip("/") + "/")
            for ec in (e.casefold() for e in _runtime_allowlist)
        )


def runtime_allowlist_snapshot():
    """Return a copy of the runtime allowlist (debug/testing)."""
    with _runtime_allowlist_lock:
        return set(_runtime_allowlist)


def runtime_allowlist_clear():
    """Clear the runtime allowlist (session-start scope reset).

    The dir_whip_allow_path tool grants a session-scoped exemption
    ("exempt for this session"); the guard must not keep allowing a path
    across sessions in the same process. on_session_start calls this so
    each new session starts without leftover allowlist entries.
    """
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()


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

def _refresh_allowlist_cache():
    """Narrow cache refresh for the unified allowlist.

    Invalidates the cache so the next get_cached_config / classify_target
    sees the updated file. Delegates to config.invalidate_config_cache;
    config.reset_cache clears this module's state via a function-local
    import (the cycle-break idiom on that side).
    """
    return config.invalidate_config_cache()


def refresh_allowlist_cache():
    """Public alias for narrow allowlist cache refresh."""
    return _refresh_allowlist_cache()


# ---------------------------------------------------------------- Tool schema + entry gating (spec 5.11)

# The plugin's ONLY tool (OpenAI function-call format required by the host
# tools registry); registered at register() via ctx.register_tool. Optional
# confirm parameter + two-step flow: call without confirm to obtain the
# confirmation payload, relay it to the user, re-call with confirm=true
# only after explicit user approval.
ALLOW_PATH_TOOL_SCHEMA = {
    "name": "dir_whip_allow_path",
    "description": ALLOW_PATH_TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": ALLOW_PATH_TOOL_PATH_DESCRIPTION,
            },
            "confirm": {
                "type": "boolean",
                "description": ALLOW_PATH_TOOL_CONFIRM_DESCRIPTION,
            },
        },
        "required": ["path"],
    },
}

def _confirmation_issued(path):
    """True when the path already received its confirmation payload this
    session (casefold-insensitive on the forward-slash form, mirroring
    the runtime allowlist matching)."""
    normalized = str(path).replace("\\", "/").casefold()
    with state.session.lock:
        return any(
            normalized == str(e).replace("\\", "/").casefold()
            for e in state.session.confirmation_issued
        )


def _confirmation_mark(path):
    """Record the path in the session-memory confirmation-issued set."""
    normalized = str(path).replace("\\", "/")
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
    payload = ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE % (
        str(path).replace("\\", "/")
    )
    try:
        if pending_violation_paths(session_id):
            payload = payload + "\n" + ALLOW_PATH_LATCH_CONTEXT_LINE
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

    Function-local import = cycle break (guard consumes this module's
    gating chain through the assembly layer).
    """
    from .guard import resolved_config
    # Subagents are rejected before any other check (the sanction flows
    # top-down only; parent-guidance variant).
    if subagents._is_subagent_session(session_id):
        emit(
            "block", "allow-path", RULE_KEY_ALLOW_PATH_SUBAGENT_REJECTED, None,
            "subagent-rejected", session_id, True,
        )
        return ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE
    if not path:
        return None
    # The Working Directory root itself is never allowlisted.
    working_dir_root, _ = resolved_config()
    if working_dir_root and _is_working_dir_root(path, working_dir_root):
        emit(
            "block", "allow-path", RULE_KEY_ALLOW_PATH_ROOT_REJECTED, None,
            "root-target", session_id, False,
        )
        return ALLOW_PATH_ROOT_REJECTED_MESSAGE
    # An outside-root path is never allowlisted -- no entry is needed
    # there (writes are allowed and logged, external-write). Same lexical
    # domain as the classify chain (normalize_target + within_working_dir;
    # no hand-rolled prefix comparison).
    if working_dir_root and not within_working_dir(
        normalize_target(str(path), working_dir_root), working_dir_root
    ):
        emit(
            "block", "allow-path", RULE_KEY_ALLOW_PATH_EXTERNAL_REJECTED,
            None, "external-target", session_id, False,
        )
        return ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
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
                str(path).replace("\\", "/"),
            )
        else:
            logger.debug(
                "dir-whip: allow_path confirmation payload issued "
                "(first call): %s",
                str(path).replace("\\", "/"),
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
    from .guard import resolved_config
    working_dir_root, _ = resolved_config()
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
            "allow", "allow-path", RULE_KEY_RUNTIME_ALLOWLIST_ADD, path,
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
