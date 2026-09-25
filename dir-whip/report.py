"""/dir-whip merged report rendering + the /dir-whip slash-command family in one module (spec 5.7, spec v2.8 R6; unified at SCR-056 R1a).

Renders the merged report in fixed field order -- version, State enabled/disabled, Working Directory + resolution source, Allowlist block, anomaly-only WARNING, Stats File, Debug Log, Health last -- and owns the whole /dir-whip command surface: the ctx slot captured by register_dir_whip_commands (render reads it; paths.config_file_path probes _get_cmd_ctx), allow|remove|list management of the structured allowlist via row-level edits preserving comments (single-key model per spec v2.6 B2; command shape per SCR-037, spec v2.5; input layer v2.1 confirm-create protocol), plus the two-section formatters and load_allowlist_state (flat values fail-closed via parse_allowlist). Depends on the config resolution/stats surface; extracted from config.py (task 31.8); commands.py merged back at SCR-056 R1a (the SCR-055 R3 line-cap split is reverted -- one feature, one module).

Layer: core+registration-helper
Refs: spec 5.5, spec 5.6, spec 5.7, spec v2.5, spec v2.6 B2, spec v2.7 R9, spec v2.8 R6, SCR-029, SCR-035, SCR-037, SCR-043 R5, SCR-045 R5, SCR-046 R1, SCR-050 v3 R6.1, SCR-055 R3, SCR-056 R1a
Key exports:
  - render -- render the merged /dir-whip report.
  - plugin_version -- plugin.yaml version probe (SCR-050 v3 R6.1 public; consumer: assembly register-time precompute).
  - load_allowlist_state -- current structured allowlist + ignored-legacy count.
  - register_dir_whip_commands -- register the single "dir-whip" slash command; captures ctx; no-op when the host lacks register_command.
  - _dir_whip_cmd -- the registered dispatcher (report + allow|remove|list); never raises.
  - relativize_input / render_two_sections / render_current_state -- command-output helpers (SCR-055 R3).
"""

import logging
import os
import re
from pathlib import Path

from . import allowlist_writer, state

from .config import (
    SESSION_DIR_RE,
    effective_working_dir_root,
    load_guard_config,
    parse_terminal_cwd,
    profile_config_path,
    profile_terminal_cwd,
)

from .paths import dirwhip_home, get_hermes_home, is_absolute_any, paths_equal
from .stats import stats_jsonl_path

# Diagnostic log path (v2.8 R6): single source of truth from logsetup.
from . import logsetup

# Unified allowlist core (v2.7 R9 structured mapping; validate_dir_entry
# backs the command-side input layer).
from .allowlist import parse_allowlist, validate_dir_entry

logger = logging.getLogger("dir-whip")

# The ctx captured by register_dir_whip_commands (SCR-056 R1a: module-
# internal now that the command family lives here; render() and the
# allow|remove|list handlers resolve profiles through this slot and
# register_dir_whip_commands is the only writer).
_cmd_ctx = None


def _get_cmd_ctx():
    """The ctx captured by register_dir_whip_commands (None when
    unregistered)."""
    return _cmd_ctx


def _set_cmd_ctx(ctx):
    """Capture the /dir-whip command ctx (only writer:
    register_dir_whip_commands)."""
    global _cmd_ctx
    _cmd_ctx = ctx


def _resolution_source(ctx):
    """The resolution-chain step that produces working_dir_root (5.5).

    Mirrors resolve_working_dir_root's order: dir-whip-config override ->
    profile terminal.cwd -> fail-open. Source strings match the chain's
    INFO log sources exactly.
    """
    try:
        if load_guard_config().get("working_dir_root"):
            return "dir-whip-config"
    except Exception:
        pass
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            hermes_home = get_hermes_home()
            # SCR-045 R5: reuse the layout-aware resolver (both home
            # layouts; the former hand-built profiles/<name>/ probe
            # missed the profile-dir layout and mislabeled fail-open).
            if parse_terminal_cwd(profile_config_path(hermes_home, profile)):
                return "profile-config"
    except Exception:
        pass
    return "fail-open"


def _stats_writable():
    """Check stats.jsonl writability (Health). Returns (ok, error)."""
    path = stats_jsonl_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, str(exc)
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


# Report display labels for the resolution-chain sources (SCR-029): the
# dir-whip-config source renders as "guard-config" per the report contract;
# profile-config / fail-open render as-is.
_SOURCE_LABELS = {"dir-whip-config": "guard-config"}


def plugin_version(path=None):
    """The plugin version from the sibling plugin.yaml (the single version
    source, SCR-029). Simple text parse, NO PyYAML: the first `version:`
    line. On ANY failure (missing/unreadable file, no match) -> 'unknown';
    never raises. P6 (31.13): the register-time precomputed value in
    state.session.plugin_version wins when present.
    """
    if path is None and state.session.plugin_version:
        return state.session.plugin_version
    if path is None:
        path = Path(__file__).resolve().parent / "plugin.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        match = re.search(r"^version:\s*(\S+)$", text, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "unknown"


def _render_working_dir_line(ctx, working_dir_root):
    """Line 3: Working Directory + resolving source (5.5 chain)."""
    if not working_dir_root:
        return "Working Directory: (unresolved)"
    source = _resolution_source(ctx)
    source = _SOURCE_LABELS.get(source, source)
    return "Working Directory: %s  (source: %s)" % (working_dir_root, source)


def _render_allowlist_lines(state_map, legacy_n):
    """Line 4 (v2.8): the allowlist multi-line block.

    Header + one line each for Files/Dirs (indented 2 spaces); with NO
    entries at all (no files/dirs/legacy) the strict-empty single line is
    kept; an ignored legacy flat value adds an indented block line.
    """
    if not state_map["files"] and not state_map["dirs"] and not legacy_n:
        return ["Allowlist: (strict empty allowlist)"]
    files_str = ", ".join(state_map["files"]) if state_map["files"] else "(none)"
    dirs_str = ", ".join(state_map["dirs"]) if state_map["dirs"] else "(none)"
    lines = ["Allowlist:", "  Files: %s" % files_str, "  Dirs: %s" % dirs_str]
    if legacy_n:
        lines.append(
            "  [!] ignored legacy entries: %d -- re-add via /dir-whip allow"
            % legacy_n
        )
    return lines


def _render_warning_line(cfg, ctx):
    """Anomaly-only WARNING line (Q6 footgun) or None.

    Explicit dir-whip-config override differs from the profile
    terminal.cwd (doctor logic retained).
    """
    override = cfg.get("working_dir_root")
    if override:
        profile_cwd = profile_terminal_cwd(ctx)
        if profile_cwd is not None and not paths_equal(override, profile_cwd):
            return (
                "WARNING: dir-whip-config working_dir_root (%s) differs from "
                "profile terminal.cwd (%s); the desktop-settings edit is "
                "masked by the override" % (override, profile_cwd)
            )
    return None


def _render_debug_log_line():
    """Debug Log line (v2.8): absolute path + suffix.

    (no records yet) when the file does not exist yet; (unavailable) when
    log setup failed (log_handler_installed False wins over a stale file).
    """
    log_path = logsetup.diagnostic_log_path()
    if not state.session.log_handler_installed:
        log_suffix = " (unavailable)"
    elif not log_path.exists():
        log_suffix = " (no records yet)"
    else:
        log_suffix = ""
    return "Debug Log: %s%s" % (log_path, log_suffix)


def _render_health_lines(working_dir_root):
    """Health lines (v2.8, LAST): single Good, or a brief issue list."""
    problems = []
    if not working_dir_root:
        problems.append("resolution: FAIL-OPEN")
    writable, error = _stats_writable()
    if not writable:
        problems.append("stats.jsonl: NOT WRITABLE (%s)" % error)
    if not problems:
        return ["Health: Good"]
    return ["Health: %d issue(s)" % len(problems)] + [
        "  - %s" % p for p in problems
    ]


def render():
    """Render the merged /dir-whip report (spec 5.7 v2.8 R6).

    Fixed field order: version, State (enabled/disabled), Working
    Directory + source, Allowlist (multi-line block; strict-empty keeps
    the single line), WARNING (anomaly-only), Stats File path, Debug Log
    path, Health (LAST; single Good when clean, else a brief issue
    list). A missing dir-whip-config.yaml is the design default, NOT a
    Health problem. Never raises.
    """
    try:
        ctx = _get_cmd_ctx()
        cfg = load_guard_config()
        working_dir_root = effective_working_dir_root(ctx)
        lines = []

        # Line 1: version (plugin.yaml, unknown fallback).
        lines.append("[dir-whip] v%s" % plugin_version())

        # Line 2: state (v2.8: ACTIVE/FAIL-OPEN -> enabled/disabled).
        lines.append("State: enabled" if working_dir_root else "State: disabled")

        # Line 3: Working Directory + resolving source (5.5 chain).
        lines.append(_render_working_dir_line(ctx, working_dir_root))

        # Line 4 (v2.8): allowlist multi-line block (formatter above).
        state_map, legacy_n = load_allowlist_state()
        lines.extend(_render_allowlist_lines(state_map, legacy_n))

        # Anomaly-only WARNING: Q6 footgun (formatter above).
        warning = _render_warning_line(cfg, ctx)
        if warning:
            lines.append(warning)

        # Stats File (always): stats.jsonl absolute path (session profile
        # home, 5.13/SCR-027), placed before Debug Log.
        lines.append("Stats File: %s" % stats_jsonl_path())

        # Debug Log (v2.8, second-to-last): absolute path from logsetup
        # (single source of truth).
        lines.append(_render_debug_log_line())

        # Health (v2.8, LAST): single Good when clean; with problems a
        # brief issue list (one indented line per problem).
        lines.extend(_render_health_lines(working_dir_root))

        return "\n".join(lines)
    except Exception as exc:
        return "[dir-whip] report failed: %s" % exc


# ---------------------------------------------------------------- Allowlist read state + two-section rendering (v2.7 R9)

def load_allowlist_state():
    """Current structured allowlist + ignored legacy count.

    Returns ({"files": [sorted...], "dirs": [sorted...]}, legacy_count).
    Legacy flat values are ignored fail-closed by parse_allowlist; the
    count surfaces them for the clean-break hint. Read-side single source
    for render() and the command family.
    """
    try:
        cfg = load_guard_config()
        raw = cfg.get("allowlist")
    except Exception:
        raw = None
    parsed = parse_allowlist(raw)
    legacy = 0
    if isinstance(raw, list):
        legacy = sum(1 for x in raw if isinstance(x, str) and x.strip())
    return {
        "files": sorted(parsed.get("files") or []),
        "dirs": sorted(parsed.get("dirs") or []),
    }, legacy


def relativize_input(token, working_dir_root):
    """Relativize an input token against working_dir_root (5.6 input layer).

    Returns (rel_or_None, reason_clause). rel keeps forward slashes and a
    possible trailing slash (the --create form signal); None means guided
    rejection (root itself / ancestor / outside root).
    """
    t = str(token).replace("\\", "/").strip()
    r = str(working_dir_root).replace("\\", "/").rstrip("/")
    cf = os.name == "nt" or (is_absolute_any(t) and is_absolute_any(r))
    t_cmp = t.casefold() if cf else t
    r_cmp = r.casefold() if cf else r
    if t_cmp == r_cmp:
        return None, "'%s' is the Working Directory itself" % token
    if t_cmp.startswith(r_cmp + "/"):
        return t[len(r) + 1:], None
    return None, "'%s' resolves outside it" % token


def render_two_sections(files, dirs, header=None, tail=None):
    """Files:/Dirs: two-section listing with ONE continuous numbering
    (R1); empty sections render (none); both-empty renders the compact
    single-line empty state (R6)."""
    files = list(files or [])
    dirs = list(dirs or [])
    if not files and not dirs:
        out = "Files: (none)  Dirs: (none)"
        if header:
            out = "%s\n%s" % (header, out)
        if tail:
            out = "%s\n%s" % (out, tail)
        return out
    lines = [header] if header else []
    lines.append("Files:")
    n = 0
    for f in files:
        n += 1
        lines.append("  %d: %s" % (n, f))
    if not files:
        lines.append("  (none)")
    lines.append("Dirs:")
    for d in dirs:
        n += 1
        lines.append("  %d: %s" % (n, d))
    if not dirs:
        lines.append("  (none)")
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def render_current_state():
    """The trailing two-section current-state block (R3/R5 feedback)."""
    state_map, _legacy = load_allowlist_state()
    return render_two_sections(state_map["files"], state_map["dirs"])


# SCR-052 R1: the former _case_eq thin delegate of paths.paths_equal
# (SCR-045 R7 single source) is deleted; all call sites use
# paths.paths_equal directly (imported above).


# ---------------------------------------------------------------- /dir-whip slash-command family
# (SCR-056 R1a: merged back from commands.py -- registration + dispatch +
# the allow|remove|list handlers; the /dir-whip family is one feature.)

def _list_candidates():
    """Scan working_dir_root for allow candidates (R2).

    Returns ((file_candidates, dir_candidates), error_string). Files =
    top-level files minus already-listed files entries; Dirs = top-level
    directories minus session-format dirs and subtrees already
    covered by a dirs entry (a leftover .hermes/ is enumerated like any
    other non-session dir, SCR-046 R1). Sorted for determinism.
    """
    ctx = _get_cmd_ctx()
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
    ctx = _get_cmd_ctx()
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
    ctx = _get_cmd_ctx()
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
    _set_cmd_ctx(ctx)
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


# Declared surface (SCR-056 R1a): render + version probe + the command
# family entry points + the shared read-side accessors and command-output
# helpers (commands.py's declared surface merged in).
__all__ = [
    "_dir_whip_cmd",
    "load_allowlist_state",
    "plugin_version",
    "register_dir_whip_commands",
    "relativize_input",
    "render",
    "render_current_state",
    "render_two_sections",
]
