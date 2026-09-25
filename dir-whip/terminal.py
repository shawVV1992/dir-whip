"""Terminal command lexer + coarse-tier tiering and the terminal decision layer -- pure functions.

Tokenizes shell commands (quote/escape aware, chain boundaries as
standalone tokens), extracts block-tier write targets (redirect / touch /
cp-mv / mkdir / downloads) per segment, and owns the terminal interception
loop (guard_terminal: session-dir script gate, heredoc blanket demotion,
per-segment targets through classify.evaluate_target, uncertain allow+log)
plus its decision predicates (is_terminal_uncertain / is_device_path /
is_session_dir_script / terminal_cp_mv_src) -- lexical extraction and
judgment are one concept (SCR-056 R1b reverts the SCR-055 R6/R7 module
split). Pure functions, no host imports, no state (SCR-035, ADR-0007);
extracted from dir_whip.py (task 31.5).

Layer: core
Refs: spec 4.1, spec 4.2, spec 4.3, spec 5.10, spec 5.19, spec v2.6 B2, spec v2.8 R7, SCR-033, SCR-035, SCR-044, SCR-052, SCR-055 R6, SCR-055 R7, SCR-056 R1b, ADR-0007
Key exports:
  - tokenize_command -- split a shell command into tokens (lenient POSIX-ish lexer; never raises).
  - chain_segments -- split tokens into command segments at chain boundaries (5.10).
  - terminal_block_targets -- chain-aware block-tier write targets as (target, rule_key) pairs.
  - guard_terminal -- terminal write interception loop (guard() dispatch; None = allow).
  - is_terminal_uncertain -- uncertain write-intent detection -> allow + log tier.
  - is_device_path -- exempt device-path predicate (4.3; SCR-050 v3 R6.1 public).
  - is_session_dir_script -- does a chain segment invoke create_session_dir.py under a Python interpreter?
  - terminal_cp_mv_src -- literal source token of the mv/cp segment whose destination equals ``dst``.
"""

import logging
import re

from .classify import evaluate_target, session_cwd

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

# Terminal coarse tiers (spec 5.10). Redirect operators are emitted by
# tokenize_command as standalone tokens; block-tier targets are exact
# membership + next plain token. Everything else with write intent is
# ALLOW + LOG (terminal-write-uncertain), never approved or blocked.
REDIRECT_TOKENS = frozenset((">", ">>", "1>", "2>", "1>>", "2>>", "&>"))
OPERATOR_TOKENS = frozenset(("|", "&")) | REDIRECT_TOKENS
NON_LITERAL_RE = re.compile(r"[$`]")

# 4.1 (SCR-033): chain boundaries emitted by tokenize_command. `&&` is
# two `&` tokens (both boundaries); `&>` stays a single redirect token and
# is NOT a boundary. Newlines are emitted as "\n" marker tokens.
_CHAIN_BOUNDARY_TOKENS = frozenset((";", "|", "&", "\n"))


def tokenize_command(command):
    """Split a shell command into tokens (lightweight, POSIX-ish).

    Respects single quotes (fully literal), double quotes (backslash only
    escapes " \\ $ ` inside), and backslash escaping outside quotes.
    Unquoted whitespace separates tokens. Redirect operators (>, >>, 2>,
    &>, 1>, 1>>, 2>>), pipes, background ampersands, semicolons and
    newlines are emitted as standalone tokens (semicolons and newlines are
    chain-boundary markers, 5.10 "Chain-aware target extraction"). Lenient
    by design: unclosed quotes and malformed input never raise (the
    remainder is absorbed into the current token).
    """
    if not isinstance(command, str):
        return []
    tokens = []
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c in " \t\r":
            i += 1
            continue
        tok, next_i = _scan_operator(command, i, n)
        if tok is not None:
            tokens.append(tok)
            i = next_i
            continue
        word, i = _scan_word(command, i, n)
        # Glued fd redirect: "2>" / "2>>" (also "1>", "1>>").
        if word in ("1", "2") and i < n and command[i] == ">":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append(word + ">>")
                i += 2
            else:
                tokens.append(word + ">")
                i += 1
            continue
        tokens.append(word)

    return tokens


def _scan_operator(command, i, n):
    """Standalone operator token at command[i] (5.10 chain markers +
    redirect operators), or (None, i) when a word starts there.

    Newline / pipe / semicolon emit verbatim; a lone "&" is a chain
    boundary while "&>" stays one redirect token; ">" / ">>" are the
    plain redirect operators.
    """
    c = command[i]
    if c == "\n":
        return "\n", i + 1
    if c == "|":
        return "|", i + 1
    if c == "&":
        if i + 1 < n and command[i + 1] == ">":
            return "&>", i + 2
        return "&", i + 1
    if c == ">":
        if i + 1 < n and command[i + 1] == ">":
            return ">>", i + 2
        return ">", i + 1
    if c == ";":
        return ";", i + 1
    return None, i


def _scan_word(command, i, n):
    """Scan one word from command[i] -> (word, next_index).

    Single quotes are fully literal; double quotes honor backslash
    escapes of " \\ $ ` only; backslash escapes outside quotes; an
    unquoted whitespace or operator (|&>;) ends the word. Lenient:
    unclosed quotes absorb the remainder (never raises).
    """
    word = []
    in_single = False
    in_double = False
    while i < n:
        c = command[i]
        if in_single:
            if c == "'":
                in_single = False
            else:
                word.append(c)
            i += 1
            continue
        if in_double:
            if c == '"':
                in_double = False
                i += 1
                continue
            if c == "\\" and i + 1 < n and command[i + 1] in ('"', "\\", "$", "`"):
                word.append(command[i + 1])
                i += 2
                continue
            word.append(c)
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "\\":
            if i + 1 < n:
                word.append(command[i + 1])
                i += 2
            else:
                word.append("\\")
                i += 1
            continue
        if c in " \t\n\r" or c in "|&>;":
            break
        word.append(c)
        i += 1

    return "".join(word), i


def chain_segments(tokens):
    """Split tokens into command segments at chain boundaries (5.10).

    Boundaries: ";", "|", a lone "&" (background), and the "\n" marker
    token. "&&" surfaces as two "&" tokens, both boundaries, dropping the
    empty segment between them; "&>" stays a single redirect token and
    never splits. Quoted boundaries never reach here (the tokenizer keeps
    them inside words). SCR-055 R7 public (predicate-family consumer;
    SCR-056 R1b merged that family back into this module).
    """
    segments = []
    cur = []
    for tok in tokens:
        if tok in _CHAIN_BOUNDARY_TOKENS:
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        segments.append(cur)
    return segments


# --- Command-target extraction shapes (SCR-044 R1, spec 5.10) -----------
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
    forms never match). Registered for curl -o / wget -O (SCR-044 R4).
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


# Block-tier command specs (5.10): command -> (shape, rule_key). SCR-044
# R4 registered mkdir / curl / wget (terminal-mkdir / terminal-download);
# their extracted targets classify through the same T0-T4 chain as the
# write tools. Non-literal targets are never extracted and fall to the
# uncertain tier; the blanket uncertain signal for curl / wget
# (is_terminal_uncertain) stays untouched for
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
    """Block-tier write targets (spec 5.10), chain-aware (SCR-033).

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


# ---------------------------------------------------------------- Terminal decision layer (SCR-056 R1b: merged back; the SCR-055 R6/R7 split reverted)


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


# ---------------------------------------------------------------- Terminal decision predicates (SCR-055 R7; homed with the lexer again at SCR-056 R1b)

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
    "tokenize_command",
    "terminal_block_targets",
    "chain_segments",
    "NON_LITERAL_RE",
    "OPERATOR_TOKENS",
    "REDIRECT_TOKENS",
    "guard_terminal",
    "is_terminal_uncertain",
    "is_device_path",
    "is_session_dir_script",
    "terminal_cp_mv_src",
]
