"""Terminal command lexer + coarse tiering (block-tier write targets) -- pure functions.

Tokenizes shell commands (quote/escape aware, chain-boundary markers as
standalone tokens) and extracts block-tier write targets (redirect /
touch / cp-mv / mkdir / downloads) per command segment. The terminal
DECISION predicates (is_terminal_uncertain / is_device_path /
is_session_dir_script / terminal_cp_mv_src) are homed in the
terminal_guard module (SCR-055 R7: lexical extraction here, judgment
there); chain_segments and the token-filtering constants are the shared
public extraction surface. Pure functions only: no host imports, no
state (SCR-035 core module discipline, ADR-0007); extracted from
dir_whip.py (task 31.5).

Layer: core
Refs: spec 4.1, spec 4.2, spec 4.3, spec 5.10, spec 5.19, SCR-033, SCR-035, SCR-044, SCR-055 R7, ADR-0007
Key exports:
  - tokenize_command -- split a shell command into tokens (lenient POSIX-ish lexer; never raises).
  - chain_segments -- split tokens into command segments at chain boundaries (5.10).
  - terminal_block_targets -- chain-aware block-tier write targets as (target, rule_key) pairs.
"""

import re

from .events import (
    RULE_KEY_TERMINAL_CP_MV,
    RULE_KEY_TERMINAL_DOWNLOAD,
    RULE_KEY_TERMINAL_MKDIR,
    RULE_KEY_TERMINAL_REDIRECT,
    RULE_KEY_TERMINAL_TOUCH,
)

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
    them inside words). SCR-055 R7 public (cross-module consumer: the
    terminal_guard predicate family).
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
# (terminal_guard.is_terminal_uncertain) stays untouched for
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


__all__ = [
    "tokenize_command",
    "chain_segments",
    "NON_LITERAL_RE",
    "OPERATOR_TOKENS",
    "REDIRECT_TOKENS",
    "terminal_block_targets",
]
