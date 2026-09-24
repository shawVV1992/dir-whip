"""/dir-whip merged report rendering + the read-side helpers consumed by commands.py (spec 5.7, spec v2.8 R6).

Renders the merged report in fixed field order -- version, State enabled/disabled, Working Directory + resolution source, Allowlist block, anomaly-only WARNING, Stats File, Debug Log, Health last -- and holds the two-section allowlist formatters plus the read-side single sources shared with the command family: the captured /dir-whip command ctx slot and load_allowlist_state (structured allowlist + ignored-legacy count, flat values fail-closed via parse_allowlist). The legacy `_get_cmd_ctx` alias is kept for the paths.config_file_path probe (same object as get_cmd_ctx). The mutable command surface (allow|remove|list) moved to commands.py at SCR-055 R3; dependency direction stays commands -> report (this module never imports commands). Depends on the config resolution/stats surface (report -> config direction per the plan dependency graph); extracted from config.py (task 31.8).

Layer: core
Refs: spec 5.5, spec 5.6, spec 5.7, spec v2.5, spec v2.6 B2, spec v2.7 R9, spec v2.8 R6, SCR-029, SCR-035, SCR-037, SCR-043 R5, SCR-045 R5, SCR-046 R1, SCR-050 v3 R6.1, SCR-055 R3
Key exports:
  - render -- render the merged /dir-whip report.
  - plugin_version -- plugin.yaml version probe (SCR-050 v3 R6.1 public; consumer: assembly register-time precompute).
  - load_allowlist_state -- current structured allowlist + ignored-legacy count (consumer: commands.py).
  - get_cmd_ctx / set_cmd_ctx -- the /dir-whip command ctx slot (render reads it; commands.register_dir_whip_commands is the only writer).
  - relativize_input / render_two_sections / render_current_state -- command-output helpers consumed by commands.py (SCR-055 R3).
"""

import os
import re
from pathlib import Path

from . import state

from .config import (
    effective_working_dir_root,
    load_guard_config,
    parse_terminal_cwd,
    profile_config_path,
    profile_terminal_cwd,
)

from .paths import get_hermes_home, is_absolute_any, paths_equal
from .stats import stats_jsonl_path

# Diagnostic log path (v2.8 R6): single source of truth from logsetup.
from . import logsetup

# Unified allowlist core (v2.7 R9 structured mapping)
from .allowlist import parse_allowlist

# The ctx captured by commands.register_dir_whip_commands (SCR-055 R3: the
# command family moved out; the capture stays HERE because render() reads it
# and paths.config_file_path probes the legacy alias).
_cmd_ctx = None


def get_cmd_ctx():
    """The ctx captured by commands.register_dir_whip_commands (None when
    unregistered)."""
    return _cmd_ctx


def set_cmd_ctx(ctx):
    """Capture the /dir-whip command ctx (only writer:
    commands.register_dir_whip_commands)."""
    global _cmd_ctx
    _cmd_ctx = ctx


# Legacy probe name (paths.config_file_path documented cycle-break idiom;
# code-design-standards 2.3 same-object alias).
_get_cmd_ctx = get_cmd_ctx


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
        ctx = get_cmd_ctx()
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
    for render() and commands.py (SCR-055 R3).
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


# Declared render-side surface (SCR-055 R3): render + version probe + the
# shared read-side accessors and command-output helpers consumed by
# commands.py (dependency direction commands -> report).
__all__ = [
    "get_cmd_ctx",
    "load_allowlist_state",
    "plugin_version",
    "relativize_input",
    "render",
    "render_current_state",
    "render_two_sections",
    "set_cmd_ctx",
]
