"""The /dir-whip report command surface (spec 5.7, SCR-037, v2.6 B2).

Renders the merged report (version, State, Working Directory + source,
Terminal Guard, Allowlist, Health, WARNING, Stats File)
and registers the slash command. Depends on the config resolution/stats
surface (report -> config direction, per the plan's dependency graph).
Extracted from config.py (task 31.8). Allowlist management
(allow|remove|list) per SCR-037 v2.5, unified single-key B2 via allowlist.
"""

import logging
import os
import re
from pathlib import Path

try:
    from . import state
except ImportError:
    import state

try:
    from .config import (
        SESSION_DIR_RE,
        _effective_root,
        _get_hermes_home,
        _paths_equal,
        _profile_terminal_cwd,
        load_guard_config,
        parse_terminal_cwd,
    )
except ImportError:
    from config import (
        SESSION_DIR_RE,
        _effective_root,
        _get_hermes_home,
        _paths_equal,
        _profile_terminal_cwd,
        load_guard_config,
        parse_terminal_cwd,
    )

try:
    from .stats import _stats_jsonl_path
except ImportError:
    from stats import _stats_jsonl_path

# Unified allowlist core (v2.7 R9 structured mapping)
try:
    from .allowlist import (
        parse_allowlist,
        validate_dir_entry,
    )
except ImportError:
    try:
        from allowlist import (  # type: ignore
            parse_allowlist,
            validate_dir_entry,
        )
    except ImportError:
        def parse_allowlist(raw):  # type: ignore
            return {"files": set(), "dirs": set()}
        def validate_dir_entry(rel):  # type: ignore
            return False, "allowlist core unavailable"

# Structured allowlist persistence (v2.7 R9): row-level mapping writer.
try:
    from . import config_writer
except ImportError:
    import config_writer  # type: ignore

logger = logging.getLogger("dir-whip")

# The ctx captured by register_dir_whip_commands; command handlers
# read profile_name from it (the host invokes handlers as fn(raw_args)).
_cmd_ctx = None


def _get_cmd_ctx():
    """The ctx captured at command registration (None when unregistered)."""
    return _cmd_ctx


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
            hermes_home = _get_hermes_home()
            if profile == "default":
                cfg_path = hermes_home / "config.yaml"
            else:
                cfg_path = hermes_home / "profiles" / profile / "config.yaml"
            if parse_terminal_cwd(cfg_path):
                return "profile-config"
    except Exception:
        pass
    return "fail-open"


def _guard_config_path():
    """Profile-aware dir-whip-config.yaml path for key-presence checks.

    Mirrors config._get_guard_config_path / stats._stats_jsonl_path pattern
    so report, writer and config agree on the file location.
    """
    try:
        from .config import _get_guard_config_path as _cfg_path
        return _cfg_path()
    except Exception:
        pass
    try:
        # Fallback: profile-aware via state if config helper unavailable
        from .paths import _profile_home
        home = _get_hermes_home()
        profile = None
        try:
            if getattr(state.session, "session_profile", None):
                profile = state.session.session_profile
        except Exception:
            pass
        if not profile:
            try:
                ctx = _get_cmd_ctx()
                if ctx and getattr(ctx, "profile_name", None):
                    profile = ctx.profile_name
            except Exception:
                pass
        if profile:
            try:
                home = _profile_home(home, profile)
            except Exception:
                pass
        return Path(home) / "dir-whip" / "dir-whip-config.yaml"
    except Exception:
        return _get_hermes_home() / "dir-whip" / "dir-whip-config.yaml"


def _guard_config_key_present(key):
    """True when the key appears in dir-whip-config.yaml (raw line scan)."""
    try:
        path = _guard_config_path()
        if not path.is_file():
            return False
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return re.search(r"^\s*%s\s*:" % re.escape(key), text, re.MULTILINE) is not None
    except Exception:
        return False


def _stats_writable():
    """Check stats.jsonl writability (Health). Returns (ok, error)."""
    path = _stats_jsonl_path()
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


def _plugin_version(path=None):
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


def _dir_whip_report():
    """Render the merged /dir-whip report (SCR-029 Plan A; spec 5.7 v2.6 B2).

    Fixed field order: version, State, Working Directory + source,
    Terminal Guard, Allowlist, Health (+ one
    line per problem), WARNING (anomaly-only), Stats File path. A missing
    dir-whip-config.yaml is the design default, NOT a Health problem.
    Never raises.
    """
    try:
        ctx = _get_cmd_ctx()
        cfg = load_guard_config()
        root = _effective_root(ctx)
        lines = []

        # Line 1: version (plugin.yaml, unknown fallback).
        lines.append("[dir-whip] v%s" % _plugin_version())

        # Line 2: state.
        lines.append("State: ACTIVE" if root else "State: FAIL-OPEN")

        # Line 3: Working Directory + resolving source (5.5 chain).
        if root:
            source = _resolution_source(ctx)
            source = _SOURCE_LABELS.get(source, source)
            lines.append("Working Directory: %s  (source: %s)" % (root, source))
        else:
            lines.append("Working Directory: (unresolved)")

        # Line 4: terminal guard.
        lines.append(
            "Terminal Guard: %s"
            % ("enabled" if cfg.get("terminal_guard", True) else "disabled")
        )

        # Line 5: allowlist (structured mapping v2.7 R9).
        # Display: strict empty hint when key missing, otherwise Files/Dirs;
        # an ignored legacy flat value appends the clean-break hint.
        if not _guard_config_key_present("allowlist"):
            lines.append("Allowlist: (strict empty allowlist)")
        else:
            state_map, legacy_n = _load_allowlist_state()
            files_str = ", ".join(state_map["files"]) if state_map["files"] else "(none)"
            dirs_str = ", ".join(state_map["dirs"]) if state_map["dirs"] else "(none)"
            allow_line = "Allowlist: Files: %s  Dirs: %s" % (files_str, dirs_str)
            if legacy_n:
                allow_line += "  | ignored legacy entries: %d" % legacy_n
            lines.append(allow_line)

        # Line 5b (v2.7 R6): session-start discipline-block outcome (5.4).
        # reminder_status is set by on_start (injected | skipped-outside |
        # skipped-child | unavailable); None (on_start not yet run in this
        # process) renders a neutral placeholder -- never one of the four
        # spec states.
        reminder = getattr(state.session, "reminder_status", None)
        lines.append(
            "Reminder: %s" % (reminder if reminder else "(not recorded)")
        )

        # Line 6: health (one line per problem when PROBLEM).
        problems = []
        if not root:
            problems.append("resolution: FAIL-OPEN")
        writable, error = _stats_writable()
        if not writable:
            problems.append("stats.jsonl: NOT WRITABLE (%s)" % error)
        if problems:
            lines.append("Health: PROBLEM")
            lines.extend("- %s" % p for p in problems)
        else:
            lines.append("Health: OK")

        # Line 7 (anomaly only): Q6 footgun — explicit override differs
        # from the profile terminal.cwd (doctor logic retained).
        override = cfg.get("working_dir_root")
        if override:
            profile_cwd = _profile_terminal_cwd(ctx)
            if profile_cwd is not None and not _paths_equal(override, profile_cwd):
                lines.append(
                    "WARNING: dir-whip-config working_dir_root (%s) differs from "
                    "profile terminal.cwd (%s); the desktop-settings edit is "
                    "masked by the override" % (override, profile_cwd)
                )

        # Last line (always): stats.jsonl absolute path (session profile
        # home, 5.13/SCR-027).
        lines.append("Stats File: %s" % _stats_jsonl_path())
        return "\n".join(lines)
    except Exception as exc:
        return "[dir-whip] report failed: %s" % exc


# ---------------------------------------------------------------- Allowlist state + rendering (v2.7 R9)

def _load_allowlist_state():
    """Current structured allowlist + ignored legacy count.

    Returns ({"files": [sorted...], "dirs": [sorted...]}, legacy_count).
    Legacy flat values are ignored fail-closed by parse_allowlist; the
    count surfaces them for the clean-break hint.
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


def _render_two_sections(files, dirs, header=None, tail=None):
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


def _render_current_state():
    """The trailing two-section current-state block (R3/R5 feedback)."""
    state_map, _legacy = _load_allowlist_state()
    return _render_two_sections(state_map["files"], state_map["dirs"])


def _case_eq(a, b):
    """Casefold-aware equality on Windows (SCR-006)."""
    if os.name == "nt":
        return str(a).casefold() == str(b).casefold()
    return str(a) == str(b)


def _is_abs_any(path):
    """Absolute check incl. drive-rooted forms (paths.is_absolute_any proxy)."""
    try:
        from .paths import is_absolute_any
    except ImportError:
        try:
            from paths import is_absolute_any  # type: ignore
        except ImportError:
            return os.path.isabs(path)
    return is_absolute_any(path)


def _relativize_input(token, root):
    """Relativize an input token against working_dir_root (5.6 input layer).

    Returns (rel_or_None, reason_clause). rel keeps forward slashes and a
    possible trailing slash (the --create form signal); None means guided
    rejection (root itself / ancestor / outside root).
    """
    t = str(token).replace("\\", "/").strip()
    r = str(root).replace("\\", "/").rstrip("/")
    cf = os.name == "nt" or (_is_abs_any(t) and _is_abs_any(r))
    t_cmp = t.casefold() if cf else t
    r_cmp = r.casefold() if cf else r
    if t_cmp == r_cmp:
        return None, "'%s' is the Working Directory itself" % token
    if t_cmp.startswith(r_cmp + "/"):
        return t[len(r) + 1:], None
    return None, "'%s' resolves outside it" % token


def _list_candidates():
    """Scan working_dir_root for allow candidates (R2).

    Returns ((file_candidates, dir_candidates), error_string). Files =
    top-level files minus already-listed files entries; Dirs = top-level
    directories minus session-format dirs, .hermes/, and subtrees already
    covered by a dirs entry. Sorted for determinism.
    """
    ctx = _get_cmd_ctx()
    root = _effective_root(ctx)
    if not root:
        return None, "[dir-whip] Working Directory unresolved: cannot list candidates"
    state_map, _legacy = _load_allowlist_state()
    listed_files = state_map["files"]
    dir_first_segments = [d.split("/")[0] for d in state_map["dirs"]]
    file_cands = []
    dir_cands = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        if any(_case_eq(entry.name, f) for f in listed_files):
                            continue
                        file_cands.append(entry.name)
                    elif entry.is_dir():
                        name = entry.name
                        if SESSION_DIR_RE.match(name):
                            continue
                        if _case_eq(name, ".hermes"):
                            continue
                        if any(_case_eq(name, seg) for seg in dir_first_segments):
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


def _handle_allow(rest):
    """/dir-whip allow (v2.7 R2/R3 + input layer v2.1).

    Bare -> candidate enumeration (two-section numbered + Add hint).
    Args -> digit tokens map into the candidate list (file number ->
    files, dir number -> dirs); path tokens accept relative/absolute
    input (relativized against the root): existing -> disk-aware
    classification; non-existent -> confirm-create protocol (--create
    form decides: trailing slash -> makedirs + dirs, bare name -> empty
    root file + files, nested no-slash -> directory tree + dirs);
    outside-root/root-itself/ancestor -> guided rejection. Batch
    comma/whitespace, all-or-nothing (first invalid token rejects).
    """
    rest = (rest or "").strip()
    create = False
    m = re.search(r"(?:^|\s)--create\b", rest)
    if m:
        create = True
        rest = (rest[:m.start()] + " " + rest[m.end():]).strip()
    ctx = _get_cmd_ctx()
    root = _effective_root(ctx)
    root_fwd = str(root).replace("\\", "/") if root else ""
    if not rest:
        if not root:
            return "[dir-whip] Working Directory unresolved: cannot list candidates"
        cands, err = _list_candidates()
        if err:
            return err
        fc, dc = cands
        return _render_two_sections(
            fc, dc,
            header="Candidates in %s:" % root_fwd,
            tail="Add: /dir-whip allow <number|name>",
        )
    if not root:
        return "[dir-whip] Working Directory unresolved: cannot allow"
    tokens = [t for t in re.split(r"[,\s]+", rest) if t]
    if not tokens:
        return "[dir-whip] Invalid argument: empty filename"
    cands, err = _list_candidates()
    if err:
        return err
    fc, dc = cands
    numbered = list(fc) + list(dc)
    adds_files = []
    adds_dirs = []
    seen = set()

    def _mark(kind, value):
        key = (kind, value.casefold() if os.name == "nt" else value)
        if key in seen:
            return False
        seen.add(key)
        return True

    for tok in tokens:
        if tok.isdigit():
            idx = int(tok)
            if not numbered or not 1 <= idx <= len(numbered):
                return "[dir-whip] Invalid index '%s': valid 1-%d" % (
                    tok, max(len(numbered), 1),
                )
            name = numbered[idx - 1]
            if idx <= len(fc):
                if _mark("f", name):
                    adds_files.append(name)
            else:
                if _mark("d", name):
                    adds_dirs.append(name)
            continue
        # Path token: ABSOLUTE input is relativized against the root
        # (input tolerance); a relative token is taken as-is.
        tok_fwd = tok.replace("\\", "/")
        if _is_abs_any(tok_fwd) or tok_fwd.startswith("/"):
            rel_raw, reason = _relativize_input(tok, root)
            if rel_raw is None:
                return "%s\n%s" % (_ALLOW_GUIDED_REJECTION % root_fwd, reason)
        else:
            rel_raw = tok_fwd
        had_trailing_slash = rel_raw.endswith("/")
        rel = rel_raw.rstrip("/")
        ok, vreason = validate_dir_entry(rel)
        if not ok:
            return "%s\n'%s' %s" % (
                _ALLOW_GUIDED_REJECTION % root_fwd, tok, vreason,
            )
        if not _mark("p", rel):
            continue
        full = os.path.join(str(root), *rel.split("/"))
        if os.path.lexists(full):
            # Existence decides first (--create on existing = plain add).
            if os.path.isdir(full):
                adds_dirs.append(rel)
            elif "/" in rel:
                return (
                    "[dir-whip] Invalid path: '%s' is an existing file in a "
                    "subdirectory; only root-level files can be files entries."
                    % tok
                )
            else:
                adds_files.append(rel)
        else:
            if not create:
                return "'%s' does not exist -- run: /dir-whip allow %s --create" % (
                    tok, tok,
                )
            # Form decides the created artifact (input layer v2.1).
            if had_trailing_slash or "/" in rel:
                try:
                    os.makedirs(full, exist_ok=True)
                except OSError as exc:
                    return "[dir-whip] cannot create '%s': %s" % (rel, exc)
                adds_dirs.append(rel)
            else:
                try:
                    with open(full, "a", encoding="utf-8"):
                        pass
                except OSError as exc:
                    return "[dir-whip] cannot create '%s': %s" % (rel, exc)
                adds_files.append(rel)
    # Merge idempotently (Added to ... / Already in ...), cap, persist.
    state_map, _legacy = _load_allowlist_state()
    new_files = list(state_map["files"])
    new_dirs = list(state_map["dirs"])
    feedback = []
    for f in adds_files:
        if any(_case_eq(f, x) for x in new_files):
            feedback.append("Already in files: %s" % f)
        else:
            new_files.append(f)
            feedback.append("Added to files: %s" % f)
    for d in adds_dirs:
        if any(_case_eq(d, x) for x in new_dirs):
            feedback.append("Already in dirs: %s" % d)
        else:
            new_dirs.append(d)
            feedback.append("Added to dirs: %s" % d)
    if len(new_files) + len(new_dirs) > config_writer.MAX_ENTRIES:
        return "[dir-whip] Too many entries: max %d allowlisted items" % (
            config_writer.MAX_ENTRIES,
        )
    if any(line.startswith("Added to") for line in feedback):
        config_writer.write_allowlist(
            {"files": sorted(new_files), "dirs": sorted(new_dirs)}
        )
    return "\n".join(feedback) + "\n\n" + _render_current_state()


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
    state_map, _legacy = _load_allowlist_state()
    files = state_map["files"]
    dirs = state_map["dirs"]
    if not rest:
        if not files and not dirs:
            return "Allowlist: (strict empty allowlist)"
        return _render_two_sections(
            files, dirs, tail="Remove: /dir-whip remove <number|name>",
        )
    ctx = _get_cmd_ctx()
    root = _effective_root(ctx)
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
            if _is_abs_any(tok_fwd) or tok_fwd.startswith("/"):
                if root:
                    rel, _reason = _relativize_input(tok, root)
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
            if _case_eq(x, name):
                removed_lines.append("Removed from %s: %s" % (label, x))
            else:
                kept.append(x)
        return kept

    for name in rem_names:
        new_files = _drop(new_files, name, "files")
        new_dirs = _drop(new_dirs, name, "dirs")
    if not removed_lines:
        return "Not in allowlist: %s\n\n%s" % (
            ", ".join(rem_names), _render_current_state(),
        )
    config_writer.write_allowlist(
        {"files": sorted(new_files), "dirs": sorted(new_dirs)}
    )
    return "\n".join(removed_lines) + "\n\n" + _render_current_state()


def _handle_list(rest):
    """/dir-whip list (v2.7 R6): the same two-section numbered format as
    remove (numbers align so a listed number can be copied directly),
    plus the ignored-legacy hint when a flat value was ignored."""
    if (rest or "").strip():
        return "Usage: /dir-whip [allow|remove|list]"
    state_map, legacy = _load_allowlist_state()
    out = _render_two_sections(state_map["files"], state_map["dirs"])
    if legacy:
        out += "\n[!] ignored legacy entries: %d -- re-add via /dir-whip allow" % legacy
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
            return _dir_whip_report()
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

    Exactly ONE command named "dir-whip": Hermes dispatches slash commands
    on the FIRST token only (cli.py: base_cmd = split()[0]), so every
    argument reaches the same handler, which manages subcommands internally.
    Guarded: a ctx without register_command still registers. allow_path is a
    TOOL and is NOT registered here (__init__.py registers it).
    args_hint surfaces in Discord/Telegram menus (commands.py:640).
    """
    global _cmd_ctx
    _cmd_ctx = ctx
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


# Public thin aliases (SCR-035 interface convergence point).
register_commands = register_dir_whip_commands
render = _dir_whip_report

__all__ = ["register_commands", "render"]
