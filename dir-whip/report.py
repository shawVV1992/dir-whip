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
        _effective_root,
        _get_hermes_home,
        _paths_equal,
        _profile_terminal_cwd,
        load_guard_config,
        parse_terminal_cwd,
    )
except ImportError:
    from config import (
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

# Unified allowlist core (B2)
try:
    from .allowlist import (
        format_allowlist,
        is_allowlist_file,
        is_allowlist_prefix,
        normalize_allowlist_entry,
        parse_allowlist,
    )
except ImportError:
    try:
        from allowlist import (
            format_allowlist,
            is_allowlist_file,
            is_allowlist_prefix,
            normalize_allowlist_entry,
            parse_allowlist,
        )  # type: ignore
    except ImportError:
        def parse_allowlist(raw):  # type: ignore
            return {"files": set(), "prefixes": set()}
        def format_allowlist(parsed):  # type: ignore
            return []
        def is_allowlist_file(name, parsed):  # type: ignore
            return False
        def is_allowlist_prefix(path, parsed):  # type: ignore
            return False
        def normalize_allowlist_entry(entry):  # type: ignore
            return None

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

        # Line 5: allowlist (single key B2).
        # Display: strict empty hint when key missing, otherwise Files/Prefixes.
        if not _guard_config_key_present("allowlist"):
            lines.append("Allowlist: (strict empty allowlist)")
        else:
            raw = cfg.get("allowlist") or []
            parsed = parse_allowlist(raw)
            files = sorted(parsed.get("files") or [])
            prefixes = sorted(parsed.get("prefixes") or [])
            files_str = ", ".join(files) if files else "(none)"
            prefixes_str = ", ".join(prefixes) if prefixes else "(none)"
            lines.append("Allowlist: Files: %s  Prefixes: %s" % (files_str, prefixes_str))

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


# ---------------------------------------------------------------- Helpers for allowlist persistence (B2 row-level edit)

def _load_allowlist_raw():
    """Load current allowlist raw list via load_guard_config."""
    try:
        cfg = load_guard_config()
        raw = cfg.get("allowlist") or []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, str)]
        return []
    except Exception:
        return []


def _write_allowlist_raw(tagged_list):
    """Row-level edit of allowlist key, preserving other lines/comments.

    - If the key exists, rewrite that line as flow list (handling block-form
      continuation lines for legacy allowed_root_files / exempt_paths).
    - If not found, append the key at end with newline.
    - Creates parent dir if missing, utf-8.
    - tagged_list is list of discriminated strings (file:<...> | prefix:<...>).
    """
    path = _guard_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Format as flow list: allowlist: ["file:a", "prefix:E:/p"]
    import json
    if not tagged_list:
        new_line = "allowlist: []"
    else:
        flow = json.dumps(list(tagged_list), ensure_ascii=False)
        new_line = "allowlist: %s" % flow
    if not path.is_file():
        path.write_text(new_line + "\n", encoding="utf-8")
        _refresh_cache()
        return
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        path.write_text(new_line + "\n", encoding="utf-8")
        _refresh_cache()
        return
    lines = text.splitlines()
    # Match allowlist key, or legacy keys for migration (exempt_paths / allowed_root_files)
    # For B2 clean break, we treat legacy keys as allowlist and replace them.
    pat_allow = re.compile(r"^\s*allowlist\s*:")
    # Also detect legacy keys to remove them when we write allowlist
    pat_legacy_exempt = re.compile(r"^\s*exempt_paths\s*:")
    pat_legacy_allowed = re.compile(r"^\s*allowed_root_files\s*:")
    idx = None
    legacy_indices = []
    for i, line in enumerate(lines):
        if pat_allow.search(line):
            idx = i
            break
    # Collect legacy indices for removal if we are adding allowlist anew
    if idx is None:
        for i, line in enumerate(lines):
            if pat_legacy_exempt.search(line) or pat_legacy_allowed.search(line):
                legacy_indices.append(i)
    if idx is not None:
        # Detect block form: allowlist: with optional comment/whitespace only
        block_pat = re.compile(r"^\s*allowlist\s*:\s*(?:#.*)?\s*$")
        if block_pat.match(lines[idx]):
            j = idx + 1
            item_pat = re.compile(r"^\s*-\s*.*$")
            while j < len(lines) and item_pat.match(lines[j]):
                j += 1
            new_lines = lines[:idx] + [new_line] + lines[j:]
        else:
            new_lines = lines[:idx] + [new_line] + lines[idx + 1 :]
        # Also strip any legacy keys that may remain elsewhere
        # (clean break: remove exempt_paths / allowed_root_files if present)
        filtered = []
        for k, ln in enumerate(new_lines):
            # Skip legacy keys except the new allowlist line we just inserted
            if k != idx and (pat_legacy_exempt.search(ln) or pat_legacy_allowed.search(ln)):
                # Need to also skip its block items
                # Check if next lines are list items, skip them as well via lookahead
                # But for simplicity, if line is legacy key with block form, we will skip following items in next iteration
                # Handle block removal: if legacy key is block-form, skip following "- " lines
                # Determine if this ln is legacy key block
                block_legacy_pat = re.compile(r"^\s*(?:exempt_paths|allowed_root_files)\s*:\s*(?:#.*)?\s*$")
                if block_legacy_pat.match(ln):
                    # Skip this line and following list items; we will handle by scanning?
                    continue
                else:
                    continue
            filtered.append(ln)
        # Second pass to remove orphaned list items that belonged to legacy keys we skipped
        # If we skipped a block legacy key, its following "- " items would still be in filtered if not removed above?
        # Our filtered loop above skips only the key line, not items. Need to handle properly:
        # Reconstruct by scanning original new_lines and excluding legacy blocks entirely.
        final_lines = []
        skip_block = False
        for ln in new_lines:
            if pat_legacy_exempt.search(ln) or pat_legacy_allowed.search(ln):
                # Start of legacy block
                block_legacy_pat = re.compile(r"^\s*(?:exempt_paths|allowed_root_files)\s*:\s*(?:#.*)?\s*$")
                if block_legacy_pat.match(ln):
                    skip_block = True
                    continue
                else:
                    # Inline form, just skip this line
                    continue
            if skip_block:
                if re.match(r"^\s*-\s*.*$", ln):
                    continue
                else:
                    skip_block = False
                    final_lines.append(ln)
            else:
                final_lines.append(ln)
        new_lines = final_lines
        new_text = "\n".join(new_lines)
        if text.endswith("\n"):
            new_text += "\n"
        path.write_text(new_text, encoding="utf-8")
    else:
        # No allowlist key yet: append, but first strip legacy keys if present
        # Build new content without legacy keys/blocks, then append allowlist
        cleaned = []
        skip_block = False
        for ln in lines:
            if pat_legacy_exempt.search(ln) or pat_legacy_allowed.search(ln):
                block_legacy_pat = re.compile(r"^\s*(?:exempt_paths|allowed_root_files)\s*:\s*(?:#.*)?\s*$")
                if block_legacy_pat.match(ln):
                    skip_block = True
                    continue
                else:
                    continue
            if skip_block:
                if re.match(r"^\s*-\s*.*$", ln):
                    continue
                else:
                    skip_block = False
                    cleaned.append(ln)
            else:
                cleaned.append(ln)
        text_cleaned = "\n".join(cleaned)
        if text_cleaned and not text_cleaned.endswith("\n"):
            text_cleaned += "\n"
        text_cleaned += new_line + "\n"
        path.write_text(text_cleaned, encoding="utf-8")
    _refresh_cache()


def _refresh_cache():
    """Narrow cache refresh so next verdict.classify_target sees new allowlist."""
    try:
        from . import config as _cfg
        if hasattr(_cfg, "_refresh_allowlist_cache"):
            _cfg._refresh_allowlist_cache()
        elif hasattr(_cfg, "refresh_allowlist_cache"):
            _cfg.refresh_allowlist_cache()
    except Exception:
        try:
            import config as _cfg2
            if hasattr(_cfg2, "_refresh_allowlist_cache"):
                _cfg2._refresh_allowlist_cache()
        except Exception:
            pass


# ---------------------------------------------------------------- Allowlist helpers (SCR-037 B2)

def _list_candidates():
    """Scan working_dir_root for root-file candidates.

    Returns (candidates_list, error_string). Candidates are top-level
    files excluding already-allowlisted files, allowlist prefixes (subtree),
    and session-dir entries. Sorted for determinism.
    """
    ctx = _get_cmd_ctx()
    root = _effective_root(ctx)
    if not root:
        return None, "[dir-whip] Working Directory unresolved: cannot list candidates"
    try:
        cfg = load_guard_config()
        raw = cfg.get("allowlist") or []
        parsed = parse_allowlist(raw)
    except Exception:
        parsed = {"files": set(), "prefixes": set()}
    candidates = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except Exception:
                    continue
                name = entry.name
                # Exclude allowlist files (exact basename, case-insensitive on Windows)
                if is_allowlist_file(name, parsed):
                    continue
                full = os.path.join(root, name)
                try:
                    # Exclude allowlist prefixes (subtree)
                    if is_allowlist_prefix(full, parsed):
                        continue
                    from .config import is_inside_session_dir
                except ImportError:
                    from config import is_inside_session_dir  # type: ignore
                try:
                    if is_inside_session_dir(full, root):
                        continue
                except Exception:
                    pass
                candidates.append(name)
    except Exception as exc:
        return None, "[dir-whip] failed to list candidates: %s" % exc
    candidates.sort()
    return candidates, None


def _handle_allow(rest):
    """Handle /dir-whip allow subcommand (B2 unified).

    Bare allow -> list candidates. With args, intelligently discriminate
    file vs prefix via allowlist.normalize_allowlist_entry: no slash
    (e.g. README.md) -> file:, slash or prefix: -> prefix:.
    Supports numeric indices for candidates, batch comma/whitespace separated.
    """
    # Bare allow -> list candidates
    if not rest.strip():
        cands, err = _list_candidates()
        if err:
            return err
        if not cands:
            return "No candidates: no root-level files to allowlist"
        lines = []
        for i, name in enumerate(cands, 1):
            lines.append("%d: %s" % (i, name))
        return "\n".join(lines)
    # Parse tokens (comma or whitespace separated, supports "1,3" and names with slashes/colons)
    # Use regex split on comma or whitespace, but preserve entries that contain colon? They have no spaces.
    tokens = [t for t in re.split(r"[,\s]+", rest.strip()) if t]
    if not tokens:
        return "[dir-whip] Invalid argument: empty filename"
    # Need candidates for numeric index resolution
    cands, err = _list_candidates()
    if cands is None:
        cands = []
    to_add_raw = []
    for tok in tokens:
        if tok.isdigit():
            if not cands:
                return "[dir-whip] Invalid index '%s': no candidates available" % tok
            idx = int(tok)
            if 1 <= idx <= len(cands):
                # Candidate files are bare basenames -> file: entry
                to_add_raw.append("file:%s" % cands[idx - 1])
            else:
                return "[dir-whip] Invalid index '%s': valid 1-%d" % (tok, len(cands))
        else:
            # Intelligent discrimination via allowlist normalize
            norm = normalize_allowlist_entry(tok)
            if norm is None:
                # Try to give precise error: validate as file or prefix
                # Fallback: if tok contains slash or colon, treat as prefix error, else file
                has_slash = "/" in tok or "\\" in tok
                has_colon = ":" in tok
                if tok.startswith("file:"):
                    return "[dir-whip] Invalid filename '%s': must be basename only" % tok
                if tok.startswith("prefix:") or has_slash or has_colon:
                    return "[dir-whip] Invalid prefix '%s': must be absolute path" % tok
                return "[dir-whip] Invalid entry '%s'" % tok
            to_add_raw.append(norm)
    # Dedup preserve order
    seen = set()
    uniq_add = []
    for n in to_add_raw:
        if n not in seen:
            seen.add(n)
            uniq_add.append(n)
    # Load current allowlist and merge
    current_raw = _load_allowlist_raw()
    current_parsed = parse_allowlist(current_raw)
    current_formatted = format_allowlist(current_parsed)
    # Determine which of uniq_add are already present
    # Use parsed sets for comparison (normalized)
    add_parsed = parse_allowlist(uniq_add)
    # Build new merged sets
    new_files = set(current_parsed.get("files") or [])
    new_prefixes = set(current_parsed.get("prefixes") or [])
    added = []
    for f in add_parsed.get("files") or []:
        if f not in new_files:
            new_files.add(f)
            added.append("file:%s" % f)
    for p in add_parsed.get("prefixes") or []:
        # Normalize prefix already done via parse
        if p not in new_prefixes:
            # But need to check case-insensitive on Windows? Use parsed set directly
            # For now exact check
            found = False
            for existing in new_prefixes:
                # casefold compare on Windows handled by is_allowlist_prefix but for dedup we use exact
                if os.name == "nt" and existing.casefold() == p.casefold():
                    found = True
                    break
                if existing == p:
                    found = True
                    break
            if not found:
                new_prefixes.add(p)
                added.append("prefix:%s" % p)
    if not added:
        # Idempotent: already present
        merged_formatted = format_allowlist({"files": new_files, "prefixes": new_prefixes})
        allow_str = ", ".join(merged_formatted) if merged_formatted else "(none)"
        # For strict empty vs none, we show current
        return "Already allowlisted: %s\nAllowlist: %s" % (", ".join(uniq_add), allow_str)
    # Validate total entries <=100 (file+prefix combined cap)
    total = len(new_files) + len(new_prefixes)
    if total > 100:
        return "[dir-whip] Too many entries: max 100 allowlisted items"
    merged_formatted = format_allowlist({"files": new_files, "prefixes": new_prefixes})
    _write_allowlist_raw(merged_formatted)
    allow_str = ", ".join(merged_formatted) if merged_formatted else "(none)"
    return "Added: %s\nAllowlist: %s" % (", ".join(added), allow_str)


def _handle_remove(rest):
    """Handle /dir-whip remove subcommand (B2 unified)."""
    if not rest.strip():
        return "Usage: /dir-whip [allow|remove|list]"
    tokens = [t for t in re.split(r"[,\s]+", rest.strip()) if t]
    if not tokens:
        return "Usage: /dir-whip [allow|remove|list]"
    current_raw = _load_allowlist_raw()
    current_parsed = parse_allowlist(current_raw)
    current_formatted = format_allowlist(current_parsed)
    if not current_formatted:
        # No entries, check if key missing -> strict empty
        if not _guard_config_key_present("allowlist"):
            return "Allowlist: (strict empty allowlist)"
        return "Allowlist: (none)"
    to_remove_raw = []
    for tok in tokens:
        if tok.isdigit():
            if not current_formatted:
                return "[dir-whip] Invalid index '%s': allowlist empty" % tok
            idx = int(tok)
            if 1 <= idx <= len(current_formatted):
                to_remove_raw.append(current_formatted[idx - 1])
            else:
                return "[dir-whip] Invalid index '%s': valid 1-%d" % (tok, len(current_formatted))
        else:
            norm = normalize_allowlist_entry(tok)
            if norm is None:
                # Also allow bare file name removal via file: inference?
                # Try to see if tok is a bare file that should be treated as file:
                # If tok has no slash/colon but fails validation (e.g. contains ..), error
                has_slash = "/" in tok or "\\" in tok
                has_colon = ":" in tok
                if tok.startswith("file:") or (not has_slash and not has_colon):
                    return "[dir-whip] Invalid filename '%s': must be basename only" % tok
                return "[dir-whip] Invalid prefix '%s': must be absolute path" % tok
            to_remove_raw.append(norm)
    # Dedup preserve order
    seen = set()
    uniq_rem = []
    for n in to_remove_raw:
        if n not in seen:
            seen.add(n)
            uniq_rem.append(n)
    # Parse uniq_rem to files/prefixes sets for removal
    rem_parsed = parse_allowlist(uniq_rem)
    rem_files = rem_parsed.get("files") or set()
    rem_prefixes = rem_parsed.get("prefixes") or set()
    # Normalize prefixes for comparison (case-insensitive on Windows)
    # Build new sets after removal
    new_files = set(current_parsed.get("files") or [])
    new_prefixes = set(current_parsed.get("prefixes") or [])
    removed = []
    for f in list(new_files):
        # Check if f matches any rem file (casefold on Windows)
        for rf in rem_files:
            if os.name == "nt":
                if f.casefold() == rf.casefold():
                    new_files.remove(f)
                    removed.append("file:%s" % f)
                    break
            else:
                if f == rf:
                    new_files.remove(f)
                    removed.append("file:%s" % f)
                    break
    # For prefixes, need to handle normalization
    for p in list(new_prefixes):
        for rp in rem_prefixes:
            # Compare normalized prefixes case-insensitively on Windows
            # Use helper to normalize both
            try:
                from .allowlist import _normalize_prefix as _norm  # type: ignore
            except Exception:
                def _norm(x):  # fallback
                    return x.replace("\\", "/").rstrip("/")
            p_norm = _norm(p)
            rp_norm = _norm(rp)
            if os.name == "nt":
                if p_norm.casefold() == rp_norm.casefold():
                    new_prefixes.remove(p)
                    removed.append("prefix:%s" % p_norm)
                    break
            else:
                # On POSIX, Windows drive prefixes still case-insensitive if both drive-rooted
                import re as _re
                drive_re = _re.compile(r"^[A-Za-z]:/")
                if drive_re.match(p_norm) and drive_re.match(rp_norm):
                    if p_norm.casefold() == rp_norm.casefold():
                        new_prefixes.remove(p)
                        removed.append("prefix:%s" % p_norm)
                        break
                else:
                    if p_norm == rp_norm:
                        new_prefixes.remove(p)
                        removed.append("prefix:%s" % p_norm)
                        break
    if not removed:
        merged_formatted = format_allowlist({"files": new_files, "prefixes": new_prefixes})
        allow_str = ", ".join(merged_formatted) if merged_formatted else "(none)"
        return "Not in allowlist: %s\nAllowlist: %s" % (", ".join(uniq_rem), allow_str)
    merged_formatted = format_allowlist({"files": new_files, "prefixes": new_prefixes})
    _write_allowlist_raw(merged_formatted)
    allow_str = ", ".join(merged_formatted) if merged_formatted else "(none)"
    return "Removed: %s\nAllowlist: %s" % (", ".join(removed), allow_str)


def _handle_list(rest):
    """Handle /dir-whip list subcommand (B2 unified)."""
    if rest.strip():
        return "Usage: /dir-whip [allow|remove|list]"
    try:
        if not _guard_config_key_present("allowlist"):
            return "Allowlist: (strict empty allowlist)"
        cfg = load_guard_config()
        raw = cfg.get("allowlist") or []
        parsed = parse_allowlist(raw)
        files = sorted(parsed.get("files") or [])
        prefixes = sorted(parsed.get("prefixes") or [])
        if not files and not prefixes:
            return "Allowlist: Files: (none)  Prefixes: (none)"
        files_str = ", ".join(files) if files else "(none)"
        prefixes_str = ", ".join(prefixes) if prefixes else "(none)"
        # Also show raw tagged list for clarity? Keep simple Files/Prefixes
        return "Allowlist: Files: %s  Prefixes: %s" % (files_str, prefixes_str)
    except Exception as exc:
        return "[dir-whip] command failed: %s" % exc


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
