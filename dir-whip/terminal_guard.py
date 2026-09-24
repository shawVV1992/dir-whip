"""Terminal write interception: coarse-tier target extraction over the shared evaluation chain (spec 5.10, spec v2.6 B2; split out of the guard module at SCR-055 R6).

The terminal front-layer loop: heredoc blanket demotion (no body parsing), the session-dir script gate BEFORE the demotion, per-segment block targets (redirect / touch / cp-mv) evaluated through classify.evaluate_target, and the uncertain tier (allow + log, no approval gate). Always on (v2.8 R7: no config switch). Pure decision layer: classify / events / session_dirs / terminal + stdlib; no host imports; never imports the guard module (guard imports this one).

Layer: core
Refs: spec 5.10, spec 5.19, spec v2.6 B2, spec v2.8 R7, SCR-044 R5, SCR-052, SCR-055 R6, ADR-0007
Key exports:
  - guard_terminal -- terminal write interception loop (guard() dispatch; None = allow).
"""

import logging

from .classify import evaluate_target, session_cwd

from .events import RULE_KEY_TERMINAL_WRITE_UNCERTAIN, emit

from . import session_dirs

# SCR-050 v3 R6.1 (spec 5.1 v2.19 seam discipline): the lexer surface is
# consumed via its declared public names.
from .terminal import (
    is_terminal_uncertain,
    terminal_block_targets,
    tokenize_command,
)

logger = logging.getLogger("dir-whip")


def _terminal_base(args, task_id, working_dir_root):
    """Resolve the terminal relative-target base (spec 5.3 step 4).

    Chain: args["workdir"] -> get_session_cwd(task_id) -> working_dir_root.
    Never os.getcwd(). SCR-052 G2: the resolved base carries the
    working_dir_root name (the effective root for THIS terminal call).
    """
    working_dir_root = (
        (args.get("workdir") if isinstance(args, dict) else None)
        or session_cwd(task_id)
        or working_dir_root
    )
    return working_dir_root


def guard_terminal(args, task_id, working_dir_root, allowlist,
                   is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers, v2.6 B2).

    - Always on (5.10 v2.8 R7: the terminal_guard config key is removed;
      enforcement is unconditional, no switch).
    - Heredoc (`<<`) blanket demotion (4.4): the WHOLE command is judged
      uncertain (allow + log), no body parsing, no block extraction.
    - Block tier: redirect / touch / cp-mv targets classify through the
      shared chain, per command segment (4.1); a target that is a device
      path (4.3) is exempt BEFORE normalization and emits nothing.
    - Uncertain tier: nested shells, python/node/sed/tee/curl/wget/dd,
      dynamic paths, `=`-residue tokens -> ALLOW + LOG (rule_key
      terminal-write-uncertain), NO approval gate.
    - Read-only / unparseable -> allow (no verdict event).
    - Any exception -> None (fail-open).
    """
    try:
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str) or not command:
            return None

        tokens = tokenize_command(command)
        if not tokens:
            return None
        terminal_working_dir_root = _terminal_base(
            args, task_id, working_dir_root
        )

        # SCR-044 R5 (spec 5.19): session-dir script gate BEFORE the
        # heredoc blanket demotion -- a second create_session_dir.py
        # attempt is blocked even in heredoc form (BLK-3); a first
        # attempt arms the pending_create marker that the audit
        # post-diff observer consumes (OB-1/OB-2).
        act = session_dirs.guard_script(
            tokens, working_dir_root, session_id, is_subagent,
        )
        if act:
            return act

        # 4.4 heredoc blanket demotion: never parse the body, never block.
        if "<<" in command:
            emit(
                "allow", "terminal", RULE_KEY_TERMINAL_WRITE_UNCERTAIN, None,
                "heredoc detected, blanket demotion", session_id, is_subagent,
            )
            return None

        for target, rule_key in terminal_block_targets(tokens):
            act = evaluate_target(
                target, "terminal", working_dir_root, allowlist,
                is_subagent, session_id, is_terminal=True,
                terminal_working_dir_root=terminal_working_dir_root,
                rule_key=rule_key, tokens=tokens,
            )
            if act:
                return act

        if is_terminal_uncertain(tokens):
            emit(
                "allow", "terminal", RULE_KEY_TERMINAL_WRITE_UNCERTAIN, None,
                "write intent detected, target uncertain", session_id, is_subagent,
            )
            return None
        return None
    except Exception as exc:
        logger.debug("dir-whip: terminal guard error (fail-open): %s", exc)
        return None


__all__ = ["guard_terminal"]
