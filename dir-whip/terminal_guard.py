"""Terminal write interception: coarse-tier target extraction over the shared evaluation chain + the terminal decision predicate family (spec 5.10, spec v2.6 B2; split out of the guard module at SCR-055 R6; predicates moved from terminal.py at SCR-055 R7).

The terminal front-layer loop: heredoc blanket demotion (no body parsing), the session-dir script gate BEFORE the demotion, per-segment block targets (redirect / touch / cp-mv) evaluated through classify.evaluate_target, and the uncertain tier (allow + log, no approval gate). Always on (v2.8 R7: no config switch). SCR-055 R7 layering: terminal.py = pure lexical extraction, this module = terminal judgment predicates (is_terminal_uncertain / is_device_path / is_session_dir_script / terminal_cp_mv_src) + the chain evaluation loop; the shared extraction surface (tokenize_command / terminal_block_targets / chain_segments + token-filtering constants) is imported from terminal. Pure decision layer: classify / events / session_dirs / terminal + stdlib; no host imports; never imports the guard module (guard imports this one).

Layer: core
Refs: spec 5.10, spec 5.19, spec v2.6 B2, spec v2.8 R7, SCR-044 R5, SCR-052, SCR-055 R6, SCR-055 R7, ADR-0007
Key exports:
  - guard_terminal -- terminal write interception loop (guard() dispatch; None = allow).
  - is_terminal_uncertain -- uncertain write-intent detection -> allow + log tier.
  - is_device_path -- exempt device-path predicate (4.3; SCR-050 v3 R6.1 public).
  - is_session_dir_script -- does a chain segment invoke create_session_dir.py under a Python interpreter?
  - terminal_cp_mv_src -- literal source token of the mv/cp segment whose destination equals ``dst``.
"""

import logging
import re

from .classify import evaluate_target, session_cwd

from .events import RULE_KEY_TERMINAL_WRITE_UNCERTAIN, emit

from . import session_dirs

# SCR-050 v3 R6.1 (spec 5.1 v2.19 seam discipline): the lexer surface is
# consumed via its declared public names (SCR-055 R7: predicates moved
# here; the shared constants/helpers stay public in terminal.py).
from .terminal import (
    NON_LITERAL_RE,
    OPERATOR_TOKENS,
    REDIRECT_TOKENS,
    chain_segments,
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


# ---------------------------------------------------------------- Terminal decision predicates (SCR-055 R7: moved from terminal.py)

# Uncertain-tier inputs (spec 5.10): nested shells, scripting interpreters /
# download tools, dynamic ($ / `) paths, `=`-residue tokens -> ALLOW + LOG.
_NESTED_SHELLS = frozenset(("bash", "sh", "powershell", "pwsh"))
_UNCERTAIN_COMMANDS = frozenset(
    ("python", "python3", "py", "node", "sed", "tee", "curl", "wget", "dd")
)

# 4.3 (SCR-033): device paths exempt BEFORE normalization -- they never
# enter the classification chain and produce no verdict/stats event (no
# drive-inherited E:\dev\null fabrication on Windows).
_DEVICE_PATHS = frozenset(("/dev/null", "/dev/stdout", "/dev/stderr"))

# SCR-044 R1 (5.19): inputs of the is_session_dir_script predicate.
_SESSION_SCRIPT_INTERPRETERS = frozenset(("python", "python3", "py"))
_SESSION_SCRIPT_NAME = "create_session_dir.py"


def is_terminal_uncertain(tokens):
    """Uncertain write-intent detection (5.10 allow-and-log tier).

    Any chain segment whose first token is python/node/sed/tee/curl/wget/
    dd, any nested-shell invocation (bash -c / sh -c / powershell
    -Command), any non-literal ($ or `) token, or any token starting with
    "=" (residue of an unquoted `>=` comparison split by a > redirect,
    spec 5.10 / 4.2) -> True.
    """
    if not tokens:
        return False
    for seg in chain_segments(tokens):
        if not seg:
            continue
        first = seg[0]
        if first in _UNCERTAIN_COMMANDS:
            return True
        if first in _NESTED_SHELLS and any(
            t == "-c" or t.lower() == "-command" for t in seg
        ):
            return True
    return any(NON_LITERAL_RE.search(t) for t in tokens) or any(
        t.startswith("=") for t in tokens
    )


def _script_basename(tok):
    """Final path component of `tok` (quotes stripped; forward and
    backslash separators both count)."""
    return re.split(r"[/\\]", tok.strip("\"'"))[-1]


def is_session_dir_script(tokens):
    """Pure predicate (SCR-044 R1, spec 5.19): does the command invoke
    the session-dir creation script under a Python interpreter?

    Per-segment judgment: a chain segment triggers True only if it
    simultaneously contains an interpreter token (python / python3 / py)
    AND a token whose final path component is create_session_dir.py
    (relative or absolute, quoted or not, forward or backslash paths).
    Quoted nested-shell bodies stay inside a single token, so every token
    is also split on whitespace to keep the inner command visible
    (bash -c "python ... create_session_dir.py ..." -> True). An
    interpreter and the script name in DIFFERENT segments -> False;
    `cat` / `echo` mentions without an interpreter, `python -V` and the
    module form `python -m create_session_dir` (basename without .py)
    -> False.
    """
    if not tokens:
        return False
    for seg in chain_segments(tokens):
        words = []
        for tok in seg:
            words.extend(tok.split())
        if not any(w in _SESSION_SCRIPT_INTERPRETERS for w in words):
            continue
        if any(_script_basename(w) == _SESSION_SCRIPT_NAME for w in words):
            return True
    return False


def terminal_cp_mv_src(tokens, dst):
    """Pure helper (SCR-044 R5 form b, spec 5.19): the literal source
    token of the mv/cp command segment whose destination is `dst`.

    Scans chain segments; a segment qualifies when its command token is
    mv/cp and its LAST literal arg (the _last_literal_arg destination
    shape, same filtering as target extraction: operator tokens, flags,
    redirect target slots and non-literal residues skipped) equals `dst`
    exactly. Returns the literal arg immediately before that
    destination, or None. The guard's session-dir creation gate
    consults this to distinguish a rename OF the bound directory (claim
    transfer, MV-1) from a second creation via mv (BLK-5).
    """
    for seg in chain_segments(tokens):
        if len(seg) < 3 or seg[0] not in ("mv", "cp"):
            continue
        redirect_idx = {
            i + 1
            for i, tok in enumerate(seg)
            if tok in REDIRECT_TOKENS and i + 1 < len(seg)
        }
        literals = [
            tok
            for i, tok in enumerate(seg)
            if i
            and tok not in OPERATOR_TOKENS
            and i not in redirect_idx
            and not tok.startswith("-")
            and not NON_LITERAL_RE.search(tok)
        ]
        if len(literals) >= 2 and literals[-1] == dst:
            return literals[-2]
    return None


def is_device_path(target):
    """True when the token is an exempt device path (4.3, SCR-033).

    SCR-050 v3 R6.1: public predicate over the frozen _DEVICE_PATHS set
    (deep-module preference: hide the data, expose the judgment; the
    cross-module consumer (classify) must not reach the private set).
    SCR-055 R7: homed here with the predicate family.
    """
    return target in _DEVICE_PATHS


__all__ = [
    "guard_terminal",
    "is_terminal_uncertain",
    "is_device_path",
    "is_session_dir_script",
    "terminal_cp_mv_src",
]
