"""Audit conversation surface: L1 fire-once notice + L3 gate + continuation nudge + session-start cleanup (spec 5.18).

The audit-layer faces that touch the conversation or the hook chain: the
L1 notice appended to terminal results (fire-once per violation; also
triggers lazy settle-tool registration), the L3 latch unresolved/block
decision, the pre_verify continuation nudge and the top-level
session-start audit reset. Kernels and the pending store live in
audit.py (read-only from here); no host imports.

Layer: core
Refs: spec 5.18, SCR-040, SCR-041
Key exports:
  - transform_tool_result -- L1 fire-once notice hook; appends the settle instruction notice.
  - gate_unresolved -- unresolved pending paths for the L3 gate (empty -> gate open).
  - gate_block -- standard block-channel response for the L3 latch.
  - pre_verify_nudge -- continuation-nudge decision; None = let the turn finish naturally.
  - on_session_start -- top-level session-start audit reset (pending / pre snapshots / nudge cap).
"""

import json
import logging

from . import state

from .audit import (
    AUDIT_PAIRED_TOOLS,
    audit_post_check,
    mark_announced,
    pending_violation_clear,
    pending_violation_paths,
    pending_violation_snapshot,
)

from .events import (
    RULE_KEY_PRE_VERIFY_NUDGE,
    RULE_KEY_WRITE_AUDIT_GATE_BLOCK,
    bus_emit,
    emit,
)

# Message templates live in the core leaf messages.py.
from .messages import (
    AUDIT_NOTICE_HEADER_LINE,
    AUDIT_NOTICE_TAIL_LINE,
    GATE_BLOCK_FIX_LINE,
    GATE_BLOCK_HEADER_LINE,
    GATE_BLOCK_NEXT_LINE,
    GATE_BLOCK_REASON_LINE,
    GATE_BLOCK_SETTLE_LINE,
    GATE_BLOCK_SUBAGENT_FIX_LINE,
    GATE_BLOCK_SUBAGENT_NEXT_LINE,
    GATE_BLOCK_SUBAGENT_REASON_LINE,
    NUDGE_MESSAGE_TEMPLATE,
    SETTLE_INSTRUCTION_TEMPLATE,
)

from .paths import (
    dirwhip_home,
    relativize_target,
)

from .subagents import record_top_session

# The subagent gate reads the subagents-module attribute (module-level
# private imports are banned; attribute access is the sanctioned form).
from . import subagents

logger = logging.getLogger("dir-whip")


def _settle_instruction(paths_display):
    """Shared remediation sentence (single source of truth): the exact
    dir_whip_settle(paths=[...]) call form with absolute forward-slash
    paths and the quarantine location under the dir-whip home (<profile
    home>/dir-whip/audit-quarantine/, layout-aware via
    paths.profile_home). Used by BOTH the L1 notice and the continuation
    nudge; the L3 gate message keeps its short form. allow_path is never
    mentioned. Fail-open: home unresolved -> the literal <home>
    placeholder."""
    try:
        home = dirwhip_home(state.session.session_profile)
    except Exception:
        home = None
    quarantine = "%s/audit-quarantine/" % (
        str(home).replace("\\", "/") if home else "<home>/dir-whip"
    )
    return (
        SETTLE_INSTRUCTION_TEMPLATE
        % (
            ", ".join(
                '"%s"' % str(path).replace("\\", "/")
                for path in paths_display
            ),
            quarantine,
        )
    )


def _audit_notice_message(paths):
    """The single L1 notice text: the paths and the remediation via the
    shared _settle_instruction helper. One notice per result listing every
    unannounced violation; only this notice ever enters the conversation
    (context hygiene). The config-allowlist option is attributed to the
    USER ("ask the user to add") with the exact command instruction and
    the latch-period freeze explicit (all writes frozen incl. config
    edits)."""
    lines = [AUDIT_NOTICE_HEADER_LINE]
    for path in paths:
        lines.append("  - %s" % str(path).replace("\\", "/"))
    lines.append(
        _settle_instruction(paths)
        + AUDIT_NOTICE_TAIL_LINE
    )
    return "\n".join(lines)


def transform_tool_result(tool_name=None, args=None, result=None,
                             session_id=None, task_id=None, **kwargs):
    """L1 fire-once notice hook, registered at register().

    Returning a string REPLACES the tool result the model sees next turn;
    None leaves it unchanged. The audit pairs the write-class tools
    (terminal / execute_code), so only THOSE results are decorated.
    Appends ONE notice naming every
    unannounced pending violation, then flips the announced flags (HARD
    fire-once: one notice per violation, never re-appended). Non-string
    results are untouched; JSON error results are not decorated; nothing
    unannounced -> None. Fail-open: any exception -> None.

    Ordering (live-verified): transform_tool_result fires BEFORE
    post_tool_call for paired tools, so the audit re-scan runs HERE first --
    it pops the pre snapshot and fills the pending set, then the notice
    reads it. The post_tool_call call stays as an order-agnostic no-op
    fallback. The audit runs exactly once regardless of hook order.
    """
    try:
        if tool_name not in AUDIT_PAIRED_TOOLS:
            return None
        # Run the audit re-scan BEFORE reading the pending set (transform
        # fires before post_tool_call for terminal); safe when blocked-at-
        # pre (no snapshot -> early return).
        audit_post_check(
            session_id, task_id, is_subagent=subagents._is_subagent_session(session_id),
        )
        if not isinstance(result, str):
            return None
        # Don't decorate error results: the model already has bigger
        # problems; the notice waits for the next eligible result.
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
                return None
        except (ValueError, TypeError):
            pass
        pending = pending_violation_snapshot(session_id)
        unannounced = [p for p, entry in pending.items() if not entry["announced"]]
        if not unannounced:
            return None
        for path in unannounced:
            mark_announced(session_id, path)
        # Lazy registration: the settle tool enters the registry on the
        # FIRST notice fire (not at register() -- the eager tool surface is
        # pinned to dir_whip_allow_path alone). The L4 family lives in
        # audit.py, reached via a function-local import (call-site
        # laziness only -- audit is already module-level imported; not a
        # cycle break). Registration failure must never eat the notice
        # (fail-open inside the helper).
        from . import audit as _audit
        _audit.lazy_register_settle_tool()
        return result + "\n\n" + _audit_notice_message(unannounced)
    except Exception as exc:
        logger.debug("dir-whip: transform_tool_result error (fail-open): %s", exc)
        return None


def gate_unresolved(session_id, working_dir_root, allowlist):
    """Unresolved pending paths for the L3 gate (empty -> gate open).

    The write audit is always on (no switch). A failed root re-scan is
    handled inside pending_violation_paths (full pending set -> latch
    stays); any other gate-side error fails OPEN -- the gate never breaks
    the guard.
    """
    try:
        return pending_violation_paths(session_id, working_dir_root, allowlist)
    except Exception as exc:
        logger.debug("dir-whip: audit gate check error (fail-open): %s", exc)
        return []


def _audit_gate_block_message(display_paths, is_subagent):
    """L3 gate block message: unresolved paths + remediation, with the
    [Reason]/[Next] cue (subagent variant: report to the parent)."""
    lines = [GATE_BLOCK_HEADER_LINE]
    for path in display_paths:
        lines.append("  - %s" % path)
    if is_subagent:
        lines.append(GATE_BLOCK_SUBAGENT_FIX_LINE)
        lines.append(GATE_BLOCK_SUBAGENT_REASON_LINE)
        lines.append(GATE_BLOCK_SUBAGENT_NEXT_LINE)
    else:
        # The config-allowlist option is attributed to the USER with the
        # exact command and the latch-period freeze explicit.
        lines.append(GATE_BLOCK_FIX_LINE)
        # The gate blocks remediation mv/rm, so the message must name the
        # tool channel or the loop never closes.
        lines.append(
            GATE_BLOCK_SETTLE_LINE % ", ".join(
                '"%s"' % path for path in display_paths
            )
        )
        lines.append(GATE_BLOCK_REASON_LINE)
        lines.append(GATE_BLOCK_NEXT_LINE)
    return "\n".join(lines)


def gate_block(tool_name, session_id, is_subagent, working_dir_root,
                      unresolved):
    """Standard block-channel response for the L3 latch.

    Records a write-audit-gate-block verdict (stats/log; no generic
    blocked bus event -- the gate has its own) and emits the
    write-audit-gate-block bus event with privacy-shaped relative paths,
    then returns the block dict for the pre-tool channel.
    """
    rel_paths = [relativize_target(path, working_dir_root) for path in unresolved]
    emit(
        "block", tool_name, RULE_KEY_WRITE_AUDIT_GATE_BLOCK, None,
        "%d unresolved root write audit violation(s)" % len(unresolved),
        session_id, is_subagent,
    )
    bus_emit("write-audit-gate-block", {
        "outcome": "block",
        "rule_key": RULE_KEY_WRITE_AUDIT_GATE_BLOCK,
        "paths": list(rel_paths),
        "latch": "latched",
    })
    display = [str(path).replace("\\", "/") for path in unresolved]
    return {
        "action": "block",
        "message": _audit_gate_block_message(display, is_subagent),
    }


# Session-cumulative continuation-nudge cap (hardcoded, no config key):
# at most 3 nudges per session lifetime; the host's per-turn verify-nudge
# budget remains the outer bound.
PRE_VERIFY_NUDGE_CAP = 3


def pre_verify_nudge(session_id=None, changed_paths=None, **kwargs):
    """pre_verify continuation fallback decision.

    Nudge ({"action": "continue", ...}) only when the host reports file
    mutations this turn (changed_paths non-empty) AND this session still
    has unresolved pending violations; any other case returns None so the
    turn finishes naturally. Subagent sessions no-op (remediation is the
    parent's job). Session-cumulative cap: at most PRE_VERIFY_NUDGE_CAP
    nudges per session lifetime (counter in state.audit.nudge_counts,
    reset at session start); after the cap the hook returns None. The
    host `attempt` kwarg is ignored. Fail-open: any exception -> None.
    """
    try:
        if not changed_paths:
            return None
        if session_id and subagents._is_subagent_session(session_id):
            return None
        unresolved = pending_violation_paths(session_id)
        if not unresolved:
            return None
        with state.audit.lock:
            count = state.audit.nudge_counts.get(session_id, 0)
            if count >= PRE_VERIFY_NUDGE_CAP:
                return None
            state.audit.nudge_counts[session_id] = count + 1
        # Observability row for the actual nudge fire -- allow/verify,
        # target=None (the paths are already carried by the violation
        # events; privacy does not repeat them); reason carries the 1-based
        # session-cumulative attempt ordinal (counter value AFTER
        # increment). Allow outcome -> no bus fanout (emit surface = 7).
        emit(
            "allow", "verify", RULE_KEY_PRE_VERIFY_NUDGE, None,
            "continuation nudge issued (attempt %d)" % (count + 1),
            session_id, False,
        )
        display = [str(path).replace("\\", "/") for path in unresolved]
        # The resolution choice is presented to the USER; the keep-at-root
        # command carries the REAL absolute forward-slash path(s)
        # (copy-paste runnable; /dir-whip allow accepts batches).
        keep_command = "/dir-whip allow %s" % " ".join(display)
        return {
            "action": "continue",
            "message": (
                NUDGE_MESSAGE_TEMPLATE
                % (len(display), _settle_instruction(display),
                   keep_command)
            ),
        }
    except Exception as exc:
        logger.debug("dir-whip: pre_verify nudge error (fail-open): %s", exc)
        return None


def on_session_start(session_id):
    """Top-level session start: clear this session's pending violations
    and leftover pre snapshots, reset the one-time cap warning and the
    continuation-nudge cap counter, and record the current top-level
    session (child-inheritance fallback)."""
    try:
        pending_violation_clear(session_id)
        with state.audit.lock:
            stale = [k for k in state.audit.pre_snapshots if k[0] == session_id]
            for k in stale:
                state.audit.pre_snapshots.pop(k, None)
            # The nudge cap counter resets at session start (same place
            # session-start clears pending).
            state.audit.nudge_counts.pop(session_id, None)
            # Lock note: cap_warned stays under the pending lock;
            # top_session lives in state.session (plain write via
            # record_top_session).
            record_top_session(session_id)
            state.audit.cap_warned = False
    except Exception as exc:
        logger.debug("dir-whip: audit session start error: %s", exc)


# Declared conversation-side surface: hook entry + L3 gate pair +
# continuation nudge + session-start reset.
__all__ = [
    "transform_tool_result",
    "gate_unresolved",
    "gate_block",
    "PRE_VERIFY_NUDGE_CAP",
    "pre_verify_nudge",
    "on_session_start",
]
