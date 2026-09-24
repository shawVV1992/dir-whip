"""/dir-whip slash command surface: allow|remove|list persistent-allowlist management (spec 5.7, SCR-037; split out of report.py at SCR-055 R3).

Exactly ONE command "dir-whip": bare /dir-whip renders the merged report (report.render); allow|remove|list manage the structured allowlist via row-level edits preserving comments (single-key model per spec v2.6 B2; command shape per SCR-037, spec v2.5; input layer v2.1 confirm-create protocol). The captured command ctx lives in report.py (render resolves profiles through the same slot; get_cmd_ctx/set_cmd_ctx are the read/write contract, this module is the only writer). Depends on report/config/allowlist/allowlist_writer/paths/state; zero host imports, pure relative imports, no report->commands back edge.

Layer: core+registration-helper
Refs: spec 5.5, spec 5.6, spec 5.7, spec v2.5, spec v2.6 B2, spec v2.7 R9, spec v2.8 R6, SCR-029, SCR-035, SCR-037, SCR-046 R1, SCR-055 R3
Key exports:
  - register_dir_whip_commands -- register the single "dir-whip" slash command; captures ctx; no-op when the host lacks register_command.
  - _dir_whip_cmd -- the registered dispatcher (report + allow|remove|list); never raises.
"""

import logging
import os
import re

from . import allowlist_writer, state

from .allowlist import validate_dir_entry
from .config import SESSION_DIR_RE, effective_working_dir_root
from .paths import dirwhip_home, is_absolute_any, paths_equal
from .report import (
    get_cmd_ctx, load_allowlist_state, relativize_input, render,
    render_current_state, render_two_sections, set_cmd_ctx,
)

logger = logging.getLogger("dir-whip")


def _list_candidates():
    """Scan working_dir_root for allow candidates (R2).

    Returns ((file_candidates, dir_candidates), error_string). Files =
    top-level files minus already-listed files entries; Dirs = top-level
    directories minus session-format dirs and subtrees already
    covered by a dirs entry (a leftover .hermes/ is enumerated like any
    other non-session dir, SCR-046 R1). Sorted for determinism.
    """
    ctx = get_cmd_ctx()
    working_dir_root = effective_working_dir_root(ctx)
    if not working_dir_root:
        return None, "[dir-whip] Working Directory unresolved: cannot list candidates"
    state_map, _legacy = load_allowlist_state()
    listed_files = state_map["files"]
    dir_first_segments = [d.split("/")[0] for d in state_map["dirs"]]
    file_cands = []
    dir_cands = []
    try:
        with os.scandir(working_dir_root) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        if any(paths_equal(entry.name, f) for f in listed_files):
                            continue
                        file_cands.append(entry.name)
                    elif entry.is_dir():
                        name = entry.name
                        if SESSION_DIR_RE.match(name):
                            continue
                        # SCR-046 R1 (v2.14): the .hermes skip is removed --
                        # a leftover root .hermes/ (pre-v0.6.3 quarantine
                        # residue) is enumerated like any other non-session
                        # directory (the SCR-043 R5 four-way consistency).
                        if any(paths_equal(name, seg) for seg in dir_first_segments):
                            continue
                        dir_cands.append(name)
                except Exception:
                    continue
    except Exception as exc:
        return None, "[dir-whip] failed to list candidates: %s" % exc
    file_cands.sort()
    dir_cands.sort()
    return (file_cands, dir_cands), None


_ALLOW_GUIDED_REJECTION = (
    "[dir-whip] Invalid path: choose a file or folder inside the "
    "Working Directory (%s)."
)


def _allow_mark(seen, kind, value):
    """Dedup guard for one accepted token (casefold on Windows)."""
    key = (kind, value.casefold() if os.name == "nt" else value)
    if key in seen:
        return False
    seen.add(key)
    return True


def _allow_parse_token(tok, fc, dc, numbered, working_dir_root,
                       working_dir_root_fwd, create, seen):
    """Classify ONE allow token -> (error, kind, value).

    kind "f"/"d" = entry to add, None = duplicate skip; error = the
    user-facing rejection message.
    """
    if tok.isdigit():
        idx = int(tok)
        if not numbered or not 1 <= idx <= len(numbered):
            return "[dir-whip] Invalid index '%s': valid 1-%d" % (
                tok, max(len(numbered), 1),
            ), None, None
        name = numbered[idx - 1]
        if idx <= len(fc):
            if not _allow_mark(seen, "f", name):
                return None, None, None
            return None, "f", name
        if not _allow_mark(seen, "d", name):
            return None, None, None
        return None, "d", name
    # Path token: ABSOLUTE input is relativized against the root
    # (input tolerance); a relative token is taken as-is.
    tok_fwd = tok.replace("\\", "/")
    if is_absolute_any(tok_fwd) or tok_fwd.startswith("/"):
        rel_raw, reason = relativize_input(tok, working_dir_root)
        if rel_raw is None:
            return "%s\n%s" % (
                _ALLOW_GUIDED_REJECTION % working_dir_root_fwd, reason,
            ), None, None
    else:
        rel_raw = tok_fwd
    had_trailing_slash = rel_raw.endswith("/")
    rel = rel_raw.rstrip("/")
    ok, vreason = validate_dir_entry(rel)
    if not ok:
        return "%s\n'%s' %s" % (
            _ALLOW_GUIDED_REJECTION % working_dir_root_fwd, tok, vreason,
        ), None, None
    if not _allow_mark(seen, "p", rel):
        return None, None, None
    full = os.path.join(str(working_dir_root), *rel.split("/"))
    if os.path.lexists(full):
        # Existence decides first (--create on existing = plain add).
        if os.path.isdir(full):
            return None, "d", rel
        if "/" in rel:
            return (
                "[dir-whip] Invalid path: '%s' is an existing file in a "
                "subdirectory; only root-level files can be files entries."
                % tok
            ), None, None
        return None, "f", rel
    if not create:
        return "'%s' does not exist -- run: /dir-whip allow %s --create" % (
            tok, tok,
        ), None, None
    # Form decides the created artifact (input layer v2.1).
    if had_trailing_slash or "/" in rel:
        try:
            os.makedirs(full, exist_ok=True)
        except OSError as exc:
            return "[dir-whip] cannot create '%s': %s" % (rel, exc), None, None
        return None, "d", rel
    try:
        with open(full, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        return "[dir-whip] cannot create '%s': %s" % (rel, exc), None, None
    return None, "f", rel


def _allow_commit(adds_files, adds_dirs):
    """Merge idempotently, cap-check, persist, append the current-state block."""
    state_map, _legacy = load_allowlist_state()
    new_files = list(state_map["files"])
    new_dirs = list(state_map["dirs"])
    feedback = []
    for f in adds_files:
        if any(paths_equal(f, x) for x in new_files):
            feedback.append("Already in files: %s" % f)
        else:
            new_files.append(f)
            feedback.append("Added to files: %s" % f)
    for d in adds_dirs:
        if any(paths_equal(d, x) for x in new_dirs):
            feedback.append("Already in dirs: %s" % d)
        else:
            new_dirs.append(d)
            feedback.append("Added to dirs: %s" % d)
    if len(new_files) + len(new_dirs) > allowlist_writer.MAX_ENTRIES:
        return "[dir-whip] Too many entries: max %d allowlisted items" % (
            allowlist_writer.MAX_ENTRIES,
        )
    if any(line.startswith("Added to") for line in feedback):
        allowlist_writer.write_config(
            {"files": sorted(new_files), "dirs": sorted(new_dirs)}
        )
    return "\n".join(feedback) + "\n\n" + render_current_state()


def _handle_allow(rest):
    """/dir-whip allow (v2.7 R2/R3 + input layer v2.1).

    Bare -> candidate enumeration; args -> digit/path tokens per
    _allow_parse_token, all-or-nothing (first invalid token rejects).
    """
    rest = (rest or "").strip()
    create = False
    m = re.search(r"(?:^|\s)--create\b", rest)
    if m:
        create = True
        rest = (rest[:m.start()] + " " + rest[m.end():]).strip()
    ctx = get_cmd_ctx()
    working_dir_root = effective_working_dir_root(ctx)
    working_dir_root_fwd = (
        str(working_dir_root).replace("\\", "/") if working_dir_root else ""
    )
    if not rest:
        if not working_dir_root:
            return "[dir-whip] Working Directory unresolved: cannot list candidates"
        cands, err = _list_candidates()
        if err:
            return err
        fc, dc = cands
        return render_two_sections(
            fc, dc,
            header="Candidates in %s:" % working_dir_root_fwd,
            tail="Add: /dir-whip allow <number|name>",
        )
    if not working_dir_root:
        return "[dir-whip] Working Directory unresolved: cannot allow"
    tokens = [t for t in re.split(r"[,\s]+", rest) if t]
    if not tokens:
        return "[dir-whip] Invalid argument: empty filename"
    cands, err = _list_candidates()
    if err:
        return err
    fc, dc = cands
    numbered = list(fc) + list(dc)
    seen = set()
    adds_files = []
    adds_dirs = []
    for tok in tokens:
        err, kind, value = _allow_parse_token(
            tok, fc, dc, numbered, working_dir_root,
            working_dir_root_fwd, create, seen,
        )
        if err:
            return err
        if kind == "f":
            adds_files.append(value)
        elif kind == "d":
            adds_dirs.append(value)
    return _allow_commit(adds_files, adds_dirs)


def _handle_remove(rest):
    """/dir-whip remove (v2.7 R4/R5).

    Bare -> enumerate CURRENT entries (two-section numbered + Remove
    hint); strict-empty hint when nothing is listed. Args -> digit
    tokens map into the current-entry numbering; name tokens accept
    relative/absolute input and match BOTH sets (casefold on Windows;
    a hand-edited double entry is removed from both). Disk-awareness is
    an ALLOW-time concern only (remove deletes an entry, not a path).
    """
    rest = (rest or "").strip()
    state_map, _legacy = load_allowlist_state()
    files = state_map["files"]
    dirs = state_map["dirs"]
    if not rest:
        if not files and not dirs:
            return "Allowlist: (strict empty allowlist)"
        return render_two_sections(
            files, dirs, tail="Remove: /dir-whip remove <number|name>",
        )
    ctx = get_cmd_ctx()
    working_dir_root = effective_working_dir_root(ctx)
    tokens = [t for t in re.split(r"[,\s]+", rest) if t]
    if not tokens:
        return "Usage: /dir-whip [allow|remove|list]"
    numbered = list(files) + list(dirs)
    rem_names = []
    seen = set()
    for tok in tokens:
        if tok.isdigit():
            idx = int(tok)
            if not numbered or not 1 <= idx <= len(numbered):
                return "[dir-whip] Invalid index '%s': valid 1-%d" % (
                    tok, max(len(numbered), 1),
                )
            name = numbered[idx - 1]
        else:
            # Name token: relative or absolute (normalized, 5.6); matched
            # by NAME against both sets -- no disk-aware discrimination.
            tok_fwd = tok.replace("\\", "/")
            rel = None
            if is_absolute_any(tok_fwd) or tok_fwd.startswith("/"):
                if working_dir_root:
                    rel, _reason = relativize_input(tok, working_dir_root)
            if rel is None:
                rel = tok_fwd.strip().rstrip("/")
            if not rel or rel in (".", ".."):
                return "[dir-whip] Invalid entry '%s'" % tok
            name = rel
        if name not in seen:
            seen.add(name)
            rem_names.append(name)
    removed_lines = []
    new_files = list(files)
    new_dirs = list(dirs)

    def _drop(entries, name, label):
        kept = []
        for x in entries:
            if paths_equal(x, name):
                removed_lines.append("Removed from %s: %s" % (label, x))
            else:
                kept.append(x)
        return kept

    for name in rem_names:
        new_files = _drop(new_files, name, "files")
        new_dirs = _drop(new_dirs, name, "dirs")
    if not removed_lines:
        return "Not in allowlist: %s\n\n%s" % (
            ", ".join(rem_names), render_current_state(),
        )
    allowlist_writer.write_config(
        {"files": sorted(new_files), "dirs": sorted(new_dirs)}
    )
    return "\n".join(removed_lines) + "\n\n" + render_current_state()


def _handle_list(rest):
    """/dir-whip list (v2.7 R6): the same two-section numbered format as
    remove (numbers align so a listed number can be copied directly),
    plus the ignored-legacy hint when a flat value was ignored. SCR-043
    R5: appends the current audit-quarantine path line (discoverability
    after the relocation out of the workspace root)."""
    if (rest or "").strip():
        return "Usage: /dir-whip [allow|remove|list]"
    state_map, legacy = load_allowlist_state()
    out = render_two_sections(state_map["files"], state_map["dirs"])
    if legacy:
        out += "\n[!] ignored legacy entries: %d -- re-add via /dir-whip allow" % legacy
    home = dirwhip_home(state.session.session_profile)
    out += "\nQuarantine: %s" % (home / "audit-quarantine")
    return out


def _dir_whip_cmd(raw_args):
    """/dir-whip dispatcher (spec 5.7, SCR-037 B2): report + allowlist management.

    Bare /dir-whip renders the merged report; allow|remove|list manage the
    persistent allowlist via row-level edit preserving
    comments. Unknown subcommand renders the Usage line. Never raises.
    """
    try:
        arg = (raw_args or "").strip()
        if not arg:
            return render()
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if sub == "allow":
            return _handle_allow(rest)
        elif sub == "remove":
            return _handle_remove(rest)
        elif sub == "list":
            return _handle_list(rest)
        else:
            return "Usage: /dir-whip [allow|remove|list]"
    except Exception as exc:
        return "[dir-whip] command failed: %s" % exc


def register_dir_whip_commands(ctx):
    """Register the /dir-whip slash command (spec 5.7).

    Exactly ONE command named "dir-whip": the host dispatches slash commands
    on the FIRST token only (host commands.py: base_cmd = split()[0]), so
    every argument reaches the same handler, which manages subcommands
    internally. Guarded: a ctx without register_command still captures.
    allow_path is a TOOL and is NOT registered here (__init__.py registers
    it). args_hint surfaces in Discord/Telegram menus.
    """
    set_cmd_ctx(ctx)
    if not hasattr(ctx, "register_command"):
        return
    try:
        ctx.register_command(
            "dir-whip", _dir_whip_cmd,
            description="dir-whip: Working Directory guard report",
            args_hint=" [allow|remove|list]",
        )
    except Exception as exc:
        logger.warning("dir-whip: register_command failed: %s", exc)


# Declared command-side surface (SCR-055 R3): only entry-level names.
__all__ = ["register_dir_whip_commands", "_dir_whip_cmd"]
