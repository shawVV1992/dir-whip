"""Terminal lexer and coarse tiering (spec 5.10) — pure functions.

Tokenizes shell commands and extracts block-tier write targets
(redirect / touch / cp-mv) and uncertain write-intent signals. Pure
functions only: no host imports, no state (SCR-035 core module
discipline, ADR-0007). Extracted from dir_whip.py (task 31.5).
"""

import re

# Terminal coarse tiers (spec 5.10). Redirect operators are emitted by
# _tokenize_command as standalone tokens; block-tier targets are exact
# membership + next plain token. Everything else with write intent is
# ALLOW + LOG (terminal-write-uncertain), never approved or blocked.
_REDIRECT_TOKENS = frozenset((">", ">>", "1>", "2>", "1>>", "2>>", "&>"))
_OPERATOR_TOKENS = frozenset(("|", "&")) | _REDIRECT_TOKENS
_NESTED_SHELLS = frozenset(("bash", "sh", "powershell", "pwsh"))
_UNCERTAIN_COMMANDS = frozenset(
    ("python", "python3", "py", "node", "sed", "tee", "curl", "wget", "dd")
)
_NON_LITERAL_RE = re.compile(r"[$`]")

# 4.3 (SCR-033): device paths exempt BEFORE normalization -- they never
# enter the classification chain and produce no verdict/stats event (no
# drive-inherited E:\dev\null fabrication on Windows).
_DEVICE_PATHS = frozenset(("/dev/null", "/dev/stdout", "/dev/stderr"))
# 4.1 (SCR-033): chain boundaries emitted by _tokenize_command. `&&` is
# two `&` tokens (both boundaries); `&>` stays a single redirect token and
# is NOT a boundary. Newlines are emitted as "\n" marker tokens.
_CHAIN_BOUNDARY_TOKENS = frozenset((";", "|", "&", "\n"))

# SCR-044 R1 (5.19): inputs of the is_session_dir_script predicate.
_SESSION_SCRIPT_INTERPRETERS = frozenset(("python", "python3", "py"))
_SESSION_SCRIPT_NAME = "create_session_dir.py"


def _tokenize_command(command):
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
        if c == "\n":
            tokens.append("\n")
            i += 1
            continue
        if c == "|":
            tokens.append("|")
            i += 1
            continue
        if c == "&":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append("&>")
                i += 2
            else:
                tokens.append("&")
                i += 1
            continue
        if c == ">":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append(">>")
                i += 2
            else:
                tokens.append(">")
                i += 1
            continue
        if c == ";":
            tokens.append(";")
            i += 1
            continue

        # Word start: handle quoting and escapes until an unquoted
        # whitespace or operator is reached.
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

        w = "".join(word)
        # Glued fd redirect: "2>" / "2>>" (also "1>", "1>>").
        if w in ("1", "2") and i < n and command[i] == ">":
            if i + 1 < n and command[i + 1] == ">":
                tokens.append(w + ">>")
                i += 2
            else:
                tokens.append(w + ">")
                i += 1
            continue
        tokens.append(w)

    return tokens


def _chain_segments(tokens):
    """Split tokens into command segments at chain boundaries (5.10).

    Boundaries: ";", "|", a lone "&" (background), and the "\n" marker
    token. "&&" surfaces as two "&" tokens, both boundaries, dropping the
    empty segment between them; "&>" stays a single redirect token and
    never splits. Quoted boundaries never reach here (the tokenizer keeps
    them inside words).
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
            tok in _OPERATOR_TOKENS
            or i in redirect_idx
            or tok.startswith("-")
            or _NON_LITERAL_RE.search(tok)
        ):
            continue
        out.append(tok)
    return out


def _last_literal_arg(seg, redirect_idx):
    """Last (rightmost) literal arg = destination (cp/mv shape)."""
    for i in range(len(seg) - 1, -1, -1):
        tok = seg[i]
        if tok in _OPERATOR_TOKENS or i in redirect_idx or tok.startswith("-"):
            continue
        if not _NON_LITERAL_RE.search(tok):
            return [tok]
        break
    return []


def _flag_value(*flags):
    """Shape factory: the literal value following one of `flags`.

    Exact flag-token match only (combined short flags and equals-attached
    forms never match). Infrastructure for R4 (curl -o / wget -O); no R1
    command registers this shape yet.
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
                nxt in _OPERATOR_TOKENS
                or j in redirect_idx
                or nxt.startswith("-")
                or _NON_LITERAL_RE.search(nxt)
            ):
                continue
            out.append(nxt)
        return out

    return extract


# Block-tier command specs (5.10): command -> (shape, rule_key). R1
# carries the three current commands only; R4 registers mkdir / curl /
# wget (terminal-mkdir / terminal-download).
_WRITE_SPECS = {
    "touch": (_all_literal_args, "terminal-touch"),
    "cp": (_last_literal_arg, "terminal-cp-mv"),
    "mv": (_last_literal_arg, "terminal-cp-mv"),
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
        if tok in _REDIRECT_TOKENS and i + 1 < n:
            nxt = seg[i + 1]
            if (
                nxt not in _OPERATOR_TOKENS
                and not _NON_LITERAL_RE.search(nxt)
                and not nxt.startswith("=")
            ):
                out.append((nxt, "terminal-redirect"))
                redirect_idx.add(i + 1)

    spec = _WRITE_SPECS.get(seg[0])
    if spec is not None:
        shape, rule_key = spec
        for tok in shape(seg, redirect_idx):
            out.append((tok, rule_key))

    return out


def _terminal_block_targets(tokens):
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
    for seg in _chain_segments(tokens):
        out.extend(_segment_block_targets(seg))
    return out


def _terminal_uncertain(tokens):
    """Uncertain write-intent detection (5.10 allow-and-log tier).

    Any chain segment whose first token is python/node/sed/tee/curl/wget/
    dd, any nested-shell invocation (bash -c / sh -c / powershell
    -Command), any non-literal ($ or `) token, or any token starting with
    "=" (residue of an unquoted `>=` comparison split by a > redirect,
    spec 5.10 / 4.2) -> True.
    """
    if not tokens:
        return False
    for seg in _chain_segments(tokens):
        if not seg:
            continue
        first = seg[0]
        if first in _UNCERTAIN_COMMANDS:
            return True
        if first in _NESTED_SHELLS and any(
            t == "-c" or t.lower() == "-command" for t in seg
        ):
            return True
    return any(_NON_LITERAL_RE.search(t) for t in tokens) or any(
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
    for seg in _chain_segments(tokens):
        words = []
        for tok in seg:
            words.extend(tok.split())
        if not any(w in _SESSION_SCRIPT_INTERPRETERS for w in words):
            continue
        if any(_script_basename(w) == _SESSION_SCRIPT_NAME for w in words):
            return True
    return False


# Public thin aliases (SCR-035 interface convergence point).
tokenize_command = _tokenize_command
terminal_block_targets = _terminal_block_targets
terminal_uncertain = _terminal_uncertain

__all__ = [
    "tokenize_command",
    "terminal_block_targets",
    "terminal_uncertain",
    "is_session_dir_script",
]
