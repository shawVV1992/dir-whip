"""Command lexer + device/session-script predicates -- the zero-import lexical leaf (spec 4.1, spec 5.10, spec 5.19).

Single home for the shell tokenizer (quote/escape aware, chain boundaries
as standalone tokens), the segment splitter and the three predicates that
depend on them: device-path exemption, session-dir creation script, and
the mv/cp source lookup. Pure functions over stdlib only: this leaf has
ZERO intra-package imports, so every consumer (terminal / classify /
session_dirs) may import it at module level with no cycle.

Layer: core leaf
Refs: spec 4.1, spec 5.10, spec 5.19
Key exports:
  - tokenize_command -- split a shell command into tokens (lenient POSIX-ish lexer; never raises).
  - chain_segments -- split tokens into command segments at chain boundaries (5.10).
  - REDIRECT_TOKENS / OPERATOR_TOKENS / NON_LITERAL_RE -- token-class constants shared by the consumers.
  - is_device_path -- exempt device-path predicate (4.3).
  - is_session_dir_script / terminal_cp_mv_src -- session-dir creation-script predicate + mv/cp source lookup.
"""

import re

# Terminal coarse tiers (spec 5.10): redirect operators are standalone
# tokens; block-tier targets are exact membership + next plain token.
# Everything else with write intent is ALLOW + LOG, never approved.
REDIRECT_TOKENS = frozenset((">", ">>", "1>", "2>", "1>>", "2>>", "&>"))
OPERATOR_TOKENS = frozenset(("|", "&")) | REDIRECT_TOKENS
NON_LITERAL_RE = re.compile(r"[$`]")

# 4.1 chain boundaries emitted by tokenize_command: `&&` is two `&`
# tokens (both boundaries); `&>` stays one redirect token, NOT a
# boundary; newlines are "\n" marker tokens.
_CHAIN_BOUNDARY_TOKENS = frozenset((";", "|", "&", "\n"))


def tokenize_command(command):
    """Split a shell command into tokens (lightweight, POSIX-ish).

    Respects single quotes (fully literal), double quotes (backslash only
    escapes " \\ $ ` inside), and backslash escaping outside quotes;
    unquoted whitespace separates tokens. Redirect operators (>, >>, 2>,
    &>, 1>, 1>>, 2>>), pipes, background ampersands, semicolons and
    newlines are emitted as standalone tokens (semicolons/newlines are
    chain boundaries). Lenient by design: unclosed quotes and malformed
    input never raise (the remainder is absorbed into the current token).
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
    """Standalone operator token at command[i], or (None, i) when a word
    starts there.

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


# 4.3 device paths are exempt BEFORE normalization -- they never enter
# the classification chain and produce no verdict/stats event (no
# drive-inherited E:\dev\null fabrication on Windows).
_DEVICE_PATHS = frozenset(("/dev/null", "/dev/stdout", "/dev/stderr"))

# Inputs of the is_session_dir_script predicate (spec 5.19).
_SESSION_SCRIPT_INTERPRETERS = frozenset(("python", "python3", "py"))
_SESSION_SCRIPT_NAME = "create_session_dir.py"


def _script_basename(tok):
    """Final path component of `tok` (quotes stripped; forward and
    backslash separators both count)."""
    return re.split(r"[/\\]", tok.strip("\"'"))[-1]


def is_session_dir_script(tokens):
    """Does the command invoke the session-dir creation script under a
    Python interpreter? (spec 5.19)

    Per-segment judgment: a segment triggers True only if it contains an
    interpreter token (python / python3 / py) AND a token whose final
    path component is create_session_dir.py (relative or absolute,
    quoted or not). Quoted nested-shell bodies stay inside one token, so
    every token is also split on whitespace (bash -c "python ...
    create_session_dir.py ..." -> True). Interpreter and script name in
    DIFFERENT segments -> False; `cat` / `echo` mentions without an
    interpreter, `python -V`, and the module form (basename without .py)
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
    """The literal source token of the mv/cp segment whose destination
    equals `dst` (spec 5.19).

    A segment qualifies when its command token is mv/cp and its LAST
    literal arg equals `dst` exactly (same filtering as target
    extraction: operators, flags, redirect slots and non-literal
    residues skipped). Returns the literal arg immediately before that
    destination, or None. The guard's session-dir creation gate consults
    this to distinguish a rename OF the bound directory (claim transfer)
    from a second creation via mv.
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
    """True when the token is an exempt device path (4.3).

    Public predicate over the frozen _DEVICE_PATHS set (hide the data,
    expose the judgment; the classify consumer must not reach the
    private set).
    """
    return target in _DEVICE_PATHS


__all__ = [
    "tokenize_command",
    "chain_segments",
    "NON_LITERAL_RE",
    "OPERATOR_TOKENS",
    "REDIRECT_TOKENS",
    "is_device_path",
    "is_session_dir_script",
    "terminal_cp_mv_src",
]
