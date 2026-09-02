"""dir_whip_allow_path entry-gating chain (spec 5.11 v2.9/v2.11).

Extracted from the __init__.py assembly layer (SCR-045 R4): the tool
handler's full gating logic — subagent rejection -> Working Directory
root rejection -> outside-root rejection -> two-step user confirmation —
plus its helpers and verbatim message constants. The EXTERNAL rejection
message stays single-sourced in config.py (the config add layer asserts
the same value domain and must not import the assembly layer,
ADR-0007); it is imported here so both layers answer identically.

Core module discipline: no host imports (ADR-0007). Depends on
config / sessions / verdict / events / paths / audit / state; nothing
imports this module back (no cycle). The assembly layer keeps the
_allow_path_handler thin adapter (fail-open single layer; tests call it
directly) and re-exports the five moved names.
"""

import logging

from . import config, sessions, state, verdict
from .audit import audit_unresolved_paths
from .config import ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
from .events import bus_emit, emit
from .paths import (
    normalize_target,
    paths_equal,
    relativize_target,
    within_working_dir,
)

logger = logging.getLogger("dir-whip")

# Spec 5.11: the plugin's ONLY tool (OpenAI function-call format required by
# Hermes tools.registry). Registered at register() via ctx.register_tool.
# v2.9 (SCR-041 R3): optional confirm parameter + two-step flow description
# (call without confirm to obtain the briefing, relay it to the user,
# re-call with confirm=true only after explicit user approval).
ALLOW_PATH_TOOL_SCHEMA = {
    "name": "dir_whip_allow_path",
    "description": (
        "Add an absolute path to the dir-whip runtime allowlist so "
        "file operations under that path are exempt for this session (Tier 0). "
        "Use when the user explicitly specifies a path to write to. "
        "Two-step confirmation: call WITHOUT confirm to obtain the "
        "user-confirmation briefing, relay it to the user, then re-call with "
        "confirm=true ONLY after the user explicitly approves."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to allow (forward slashes)",
            },
            "confirm": {
                "type": "boolean",
                "description": (
                    "true ONLY after the user explicitly approved the "
                    "briefing payload from the first call (default false)"
                ),
            },
        },
        "required": ["path"],
    },
}

# Spec 5.11 v2.9 (SCR-041 R2a): subagent rejection, parent-guidance variant.
ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.\n"
    "Exemptions are granted by the user via the main agent. Write to the target\n"
    "directory passed by the parent agent, or report back so the parent can ask\n"
    "the user."
)

# Spec 5.11 v2.9 (SCR-041 R2b): Working Directory root rejection.
ALLOW_PATH_ROOT_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.\n"
    "Allow a specific file or subdirectory path instead; workspace-wide\n"
    "exemptions belong in dir-whip-config.yaml (allowlist dirs) authored by the user."
)

# Spec 5.11 v2.9 (SCR-041 R3): two-step confirmation payload ("<path>"
# substituted with the forward-slash form of the requested path).
ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE = (
    "[dir-whip] CONFIRMATION REQUIRED: adding \"%s\" to the runtime allowlist\n"
    "exempts ALL file operations under it from the guard for the rest of this\n"
    "session. Previously recorded root writes under it are NOT remediated by\n"
    "this exemption (they stay pending until settled). The entry expires\n"
    "automatically when the session ends; persistent exemptions belong in\n"
    "dir-whip-config.yaml\n"
    "(allowlist files/dirs, removable via /dir-whip remove).\n"
    "Present this to the user and ask for explicit approval. Re-call\n"
    "dir_whip_allow_path(path=..., confirm=true) ONLY after the user approves."
)

# Spec 5.11 v2.9 (SCR-041 R3): latch-context conditional line, appended to
# the payload only when the pending set is non-empty (latch active).
ALLOW_PATH_LATCH_CONTEXT_LINE = (
    "NOTE: a settlement block is currently active \u2014 present the resolution "
    "choice to the user: move the file(s) (settle), or keep them at the root "
    "(give the user the exact command: /dir-whip allow <path>). Writes stay "
    "frozen until then."
)


def _briefing_issued(path):
    """True when the path already received its confirmation payload this
    session (SCR-041 R3 confirmation-issued set; casefold-insensitive on
    the forward-slash form, mirroring the runtime allowlist matching)."""
    normalized = str(path).replace("\\", "/").casefold()
    with state.session.lock:
        return any(
            normalized == str(e).replace("\\", "/").casefold()
            for e in state.session.confirmation_issued
        )


def _briefing_mark(path):
    """Record the path in the session-memory confirmation-issued set."""
    normalized = str(path).replace("\\", "/")
    with state.session.lock:
        state.session.confirmation_issued.add(normalized)


def _resolves_to_root(path, working_dir_root):
    """True when path normalizes to the Working Directory root itself
    (SCR-041 R2b; case/slash/dot-segment variants included via the
    existing normalization helpers)."""
    try:
        normalized = normalize_target(str(path), working_dir_root)
    except Exception:
        return False
    return paths_equal(normalized, working_dir_root)


def _confirmation_payload(path, session_id):
    """The 5.11 v2.9 confirmation payload for path; the latch-context
    conditional line is appended when the pending set is non-empty (latch
    active). Fail-open: an unresolved-paths check failure omits the line."""
    payload = ALLOW_PATH_CONFIRMATION_PAYLOAD_TEMPLATE % (
        str(path).replace("\\", "/")
    )
    try:
        if audit_unresolved_paths(session_id):
            payload = payload + "\n" + ALLOW_PATH_LATCH_CONTEXT_LINE
    except Exception as exc:
        logger.debug(
            "dir-whip: allow_path latch-context check failed (fail-open): %s",
            exc,
        )
    return payload


def handle(args, session_id=None, **kwargs):
    """The allow_path tool's entry-gating chain (spec 5.11 v2.9/v2.11).

    Entry gating in strict order (SCR-041 R2 + SCR-043 R2c): subagent
    rejection -> Working Directory root rejection -> outside-root
    rejection -> two-step user confirmation (SCR-041 R3): the first
    call (confirm absent/false) returns the confirmation payload WITHOUT
    adding and records the path in the session-memory confirmation-issued
    set; confirm=true adds ONLY an already-briefed path (an unbriefed
    confirm=true re-issues the payload and marks the path briefed --
    confirm never adds on its own). A successful add keeps the existing
    flow: config tool + allowlisted bus event (5.14) + the symmetric
    runtime-allowlist-add stats row (SCR-040 R4, 5.13). Rejections
    record block stats rows that are bus-skipped (rule_keys in
    events._BUS_SKIP_RULE_KEYS). The fail-open catch lives in the
    assembly-layer adapter, not here.
    """
    path = args.get("path") if isinstance(args, dict) else args
    confirm = bool(args.get("confirm")) if isinstance(args, dict) else False
    # R2a: subagents are rejected before any other check (the sanction
    # flows top-down only; parent-guidance variant, 5.11 v2.9).
    if sessions.is_child(session_id):
        emit(
            "block", "allow-path", "allow-path-subagent-rejected", None,
            "subagent-rejected", session_id, True,
        )
        return ALLOW_PATH_SUBAGENT_REJECTED_MESSAGE
    # R2b: the Working Directory root itself is never allowlisted.
    if path:
        working_dir_root, _ = verdict.resolved_config()
        if working_dir_root and _resolves_to_root(path, working_dir_root):
            emit(
                "block", "allow-path", "allow-path-root-rejected", None,
                "root-target", session_id, False,
            )
            return ALLOW_PATH_ROOT_REJECTED_MESSAGE
        # R2c (SCR-043): an outside-root path is never allowlisted --
        # no entry is needed there (writes are allowed and logged,
        # external-write). Same lexical domain as the classify chain
        # (normalize_target + within_working_dir; no hand-rolled
        # prefix comparison).
        if working_dir_root and not within_working_dir(
            normalize_target(str(path), working_dir_root), working_dir_root
        ):
            emit(
                "block", "allow-path", "allow-path-external-rejected",
                None, "external-target", session_id, False,
            )
            return ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
    # R3: two-step user confirmation (main-agent path only).
    if path:
        briefed = _briefing_issued(path)
        if not briefed:
            _briefing_mark(path)
            if confirm:
                logger.debug(
                    "dir-whip: allow_path confirmation payload re-issued "
                    "(confirm=true without a prior briefing): %s",
                    str(path).replace("\\", "/"),
                )
            else:
                logger.debug(
                    "dir-whip: allow_path confirmation payload issued "
                    "(first call): %s",
                    str(path).replace("\\", "/"),
                )
        if not (confirm and briefed):
            return _confirmation_payload(path, session_id)
    # Confirmed add (existing flow unchanged). SCR-043 R3: the root is
    # resolved BEFORE the add call and passed through so the add layer
    # can assert the strict-subtree value domain (None = fail-open,
    # assertion skipped).
    working_dir_root, _ = verdict.resolved_config()
    result = config.dir_whip_allow_path(
        args, working_dir_root=working_dir_root, session_id=session_id,
        **kwargs,
    )
    if path:
        bus_emit("allowlisted", {
            "outcome": "allowlisted",
            "rule_key": "runtime-allowlist",
            "target": relativize_target(path, working_dir_root),
        })
        # SCR-040 R4 (5.13 v2.8): symmetric stats row -- allow/
        # allow-path; the emit channel relativizes the target (same
        # privacy shape as the bus event above). Allow outcome -> no
        # extra bus fanout (the 5.14 emit surface stays at 7).
        emit(
            "allow", "allow-path", "runtime-allowlist-add", path,
            "runtime allowlist entry added", session_id,
            sessions.is_child(session_id),
        )
    return result
