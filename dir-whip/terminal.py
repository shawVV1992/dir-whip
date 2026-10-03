"""Terminal coarse-tier target extraction + the terminal decision layer -- pure functions.

Command-target extraction shapes for redirect / touch / cp-mv / mkdir /
downloads, the chain-aware block-target collector, and the terminal
interception loop (guard_terminal) plus its uncertain-tier predicate. The
shell lexer and the device/session-script predicates live in the
zero-import leaf command_lex.py (imported at module level). Pure
functions, no host imports, no state.

Layer: core
Refs: spec 4.1, spec 5.10
Key exports:
  - terminal_block_targets -- chain-aware block-tier write targets as (target, rule_key) pairs.
  - guard_terminal -- terminal write interception loop (guard() dispatch; None = allow).
  - is_terminal_uncertain -- uncertain write-intent detection -> allow + log tier.
"""

import logging

from .classify import evaluate_target, session_cwd

from .command_lex import (
    NON_LITERAL_RE,
    OPERATOR_TOKENS,
    REDIRECT_TOKENS,
    chain_segments,
    tokenize_command,
)

from .events import (
    RULE_KEY_TERMINAL_CP_MV,
    RULE_KEY_TERMINAL_DOWNLOAD,
    RULE_KEY_TERMINAL_MKDIR,
    RULE_KEY_TERMINAL_REDIRECT,
    RULE_KEY_TERMINAL_TOUCH,
    RULE_KEY_TERMINAL_WRITE_UNCERTAIN,
    emit,
)

from . import session_dirs

logger = logging.getLogger("dir-whip")


# --- Command-target extraction shapes (spec 5.10) -----------------------
# Declarative shapes consumed by _WRITE_SPECS. Each shape is a pure
# function (seg, redirect_idx) -> literal target tokens. All shapes share
# the same filtering: operator tokens, flag tokens (leading "-"), redirect
# target slots and non-literal ($ / `) tokens are never extracted -- the
# non-literal residue falls to the uncertain tier instead.


def _all_literal_args(seg, redirect_idx):
    """Every literal arg after the command token (touch shape)."""
    out = []
    for i, tok in enumerate(seg):
        if i == 0:
            continue
        if (
            tok in OPERATOR_TOKENS
            or i in redirect_idx
            or tok.startswith("-")
            or NON_LITERAL_RE.search(tok)
        ):
            continue
        out.append(tok)
    return out


def _last_literal_arg(seg, redirect_idx):
    """Last (rightmost) literal arg = destination (cp/mv shape)."""
    for i in range(len(seg) - 1, -1, -1):
        tok = seg[i]
        if tok in OPERATOR_TOKENS or i in redirect_idx or tok.startswith("-"):
            continue
        if not NON_LITERAL_RE.search(tok):
            return [tok]
        break
    return []


def _flag_value(*flags):
    """Shape factory: the literal value following one of `flags`.

    Exact flag-token match only (combined short flags and equals-attached
    forms never match). Registered for curl -o / wget -O.
    """
    wanted = frozenset(flags)

    def extract(seg, redirect_idx):
        out = []
        for i, tok in enumerate(seg):
            if tok not in wanted:
                continue
            j = i + 1
            if j >= len(seg):
                continue
            nxt = seg[j]
            if (
                nxt in OPERATOR_TOKENS
                or j in redirect_idx
                or nxt.startswith("-")
                or NON_LITERAL_RE.search(nxt)
            ):
                continue
            out.append(nxt)
        return out

    return extract


# Block-tier command specs (5.10): command -> (shape, rule_key). mkdir /
# curl / wget targets classify through the same T0-T4 chain as the write
# tools. Non-literal targets are never extracted and fall to the uncertain
# tier; the blanket uncertain signal for curl / wget stays untouched for
# non-extracted forms.
_WRITE_SPECS = {
    "touch": (_all_literal_args, RULE_KEY_TERMINAL_TOUCH),
    "cp": (_last_literal_arg, RULE_KEY_TERMINAL_CP_MV),
    "mv": (_last_literal_arg, RULE_KEY_TERMINAL_CP_MV),
    "mkdir": (_all_literal_args, RULE_KEY_TERMINAL_MKDIR),
    "curl": (_flag_value("-o", "--output"), RULE_KEY_TERMINAL_DOWNLOAD),
    "wget": (_flag_value("-O", "--output-document"), RULE_KEY_TERMINAL_DOWNLOAD),
}


def _segment_block_targets(seg):
    """Block-tier targets of ONE command segment (5.10).

    Redirect targets (token after a redirect operator, unless it is an
    operator, non-literal, or starts with "=" -- the residue of an
    unquoted `>=` comparison) plus the command targets declared in
    _WRITE_SPECS, within this segment only. Returns (target, rule_key)
    pairs.
    """
    out = []
    n = len(seg)
    redirect_idx = set()
    for i, tok in enumerate(seg):
        if tok in REDIRECT_TOKENS and i + 1 < n:
            nxt = seg[i + 1]
            if (
                nxt not in OPERATOR_TOKENS
                and not NON_LITERAL_RE.search(nxt)
                and not nxt.startswith("=")
            ):
                out.append((nxt, RULE_KEY_TERMINAL_REDIRECT))
                redirect_idx.add(i + 1)

    spec = _WRITE_SPECS.get(seg[0])
    if spec is not None:
        shape, rule_key = spec
        for tok in shape(seg, redirect_idx):
            out.append((tok, rule_key))

    return out


def terminal_block_targets(tokens):
    """Block-tier write targets (spec 5.10, chain-aware).

    Tokens are first split into command segments at chain boundaries
    (5.10: `&&` / `;` / `|` / newline / lone `&`); redirect targets and
    touch/cp-mv destinations are extracted ONLY inside the segment that
    contains the write command, never across a chain boundary. Returns a
    list of (target, rule_key) pairs.

    Non-literal targets (containing $ or `) are skipped -- they fall into
    the uncertain tier (allow + log) instead. Redirect targets starting
    with "=" (residue of an unquoted `>=` split) are never valid targets.
    """
    out = []
    for seg in chain_segments(tokens):
        out.extend(_segment_block_targets(seg))
    return out


# ---------------------------------------------------------------- Terminal decision layer


def _terminal_base(args, task_id, working_dir_root):
    """Resolve the terminal relative-target base (spec 5.3 step 4).

    Chain: args["workdir"] -> get_session_cwd(task_id) -> working_dir_root.
    Never os.getcwd(). The resolved base carries the working_dir_root name
    (the effective root for THIS terminal call).
    """
    working_dir_root = (
        (args.get("workdir") if isinstance(args, dict) else None)
        or session_cwd(task_id)
        or working_dir_root
    )
    return working_dir_root


def guard_terminal(args, task_id, working_dir_root, allowlist,
                   is_subagent=False, session_id=None):
    """Terminal write interception (spec 5.10 coarse tiers).

    Always on, no config switch. Heredoc (`<<`) -> the WHOLE command is
    uncertain (allow + log, no body parsing, no block extraction). Block
    tier: redirect / touch / cp-mv targets classify through the shared
    chain per segment; device paths are exempt BEFORE normalization and
    emit nothing. Uncertain tier: nested shells, python/node/sed/tee/
    curl/wget/dd, dynamic paths, `=`-residue -> ALLOW + LOG (rule_key
    terminal-write-uncertain), NO approval gate. Read-only / unparseable
    -> allow (no verdict event). Any exception -> None (fail-open).
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

        # Session-dir script gate BEFORE the heredoc blanket demotion --
        # a second create_session_dir.py attempt is blocked even in
        # heredoc form (BLK-3); a first attempt arms the pending_create
        # marker that the audit post-diff observer consumes (OB-1/OB-2).
        act = session_dirs.guard_script(
            tokens, working_dir_root, session_id, is_subagent,
        )
        if act:
            return act

        # Heredoc blanket demotion: never parse the body, never block.
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


# ---------------------------------------------------------------- Terminal decision predicates

# Uncertain-tier inputs (spec 5.10): nested shells, scripting interpreters /
# download tools, dynamic ($ / `) paths, `=`-residue tokens -> ALLOW + LOG.
_NESTED_SHELLS = frozenset(("bash", "sh", "powershell", "pwsh"))
_UNCERTAIN_COMMANDS = frozenset(
    ("python", "python3", "py", "node", "sed", "tee", "curl", "wget", "dd")
)


def is_terminal_uncertain(tokens):
    """Uncertain write-intent detection (5.10 allow-and-log tier).

    Any chain segment whose first token is python/node/sed/tee/curl/wget/
    dd, any nested-shell invocation (bash -c / sh -c / powershell
    -Command), any non-literal ($ or `) token, or any token starting with
    "=" (residue of an unquoted `>=` comparison split by a > redirect)
    -> True.
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


__all__ = [
    "terminal_block_targets",
    "guard_terminal",
    "is_terminal_uncertain",
]
