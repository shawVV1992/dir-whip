"""Allowlist config writer (SCR-037, ADR-0008 D3, v2.6 B2).

Row-level YAML edit preserving comments, strict allowlist validation,
and narrow cache refresh. Pure stdlib + pyyaml, no host imports
(ADR-0007). Follows report.py precedent (line scan + regex).

Single unified key ``allowlist: []`` discriminated ``file:<basename>``
| ``prefix:<abs-path>`` (B2 clean break, old keys exempt_paths /
allowed_root_files deleted, no backward compat, strict empty fallback).

Discriminated entries:
- file:<basename> -> root file allowed at Working Directory root.
  Validation: basename only (no "/" or "\\" or "..", etc).
- prefix:<abs-path> -> exempt prefix (absolute path, forward slashes
  normalized, trailing slash stripped, no ".." component).

Bare entries without a tag (generator compat, not a config key):
  no slash -> file:, contains slash/colon -> prefix:. Invalid entries
  are rejected ( ValueError on add/remove) via normalize_allowlist_entry.

Path resolution: HERMES_HOME/dir-whip/dir-whip-config.yaml with
_profile_home awareness (stats._stats_jsonl_path pattern). When
HERMES_HOME is a profile dir (parent == "profiles") the file is
HERMES_HOME/dir-whip/...; when HERMES_HOME is root and a session
profile is set, resolve via _profile_home (per-profile config).
Tests monkeypatch HERMES_HOME to tmp/hermes with default profile,
so the path is tmp/hermes/dir-whip/dir-whip-config.yaml.
"""

import json
import os
import re
from pathlib import Path

import yaml

try:
    from .paths import _get_hermes_home, _profile_home
except ImportError:
    from paths import _get_hermes_home, _profile_home  # type: ignore

try:
    from . import state
except ImportError:
    import state  # type: ignore

# Unified allowlist core (B2) — parse/format/validate/normalize.
# Guarded import to survive test venv without the module (fallback stubs).
try:
    from .allowlist import (
        parse_allowlist as _allowlist_parse,
        format_allowlist as _allowlist_format,
        normalize_allowlist_entry as _allowlist_normalize,
        validate_file_entry as _allowlist_validate_file,
        validate_prefix_entry as _allowlist_validate_prefix,
    )
    try:
        from .allowlist import _normalize_prefix as _allowlist_norm_prefix
    except ImportError:
        _allowlist_norm_prefix = None  # type: ignore
except ImportError:
    try:
        from allowlist import (  # type: ignore
            parse_allowlist as _allowlist_parse,
            format_allowlist as _allowlist_format,
            normalize_allowlist_entry as _allowlist_normalize,
            validate_file_entry as _allowlist_validate_file,
            validate_prefix_entry as _allowlist_validate_prefix,
        )
        try:
            from allowlist import _normalize_prefix as _allowlist_norm_prefix  # type: ignore
        except ImportError:
            _allowlist_norm_prefix = None  # type: ignore
    except ImportError:
        _allowlist_parse = None  # type: ignore
        _allowlist_format = None  # type: ignore
        _allowlist_normalize = None  # type: ignore
        _allowlist_validate_file = None  # type: ignore
        _allowlist_validate_prefix = None  # type: ignore
        _allowlist_norm_prefix = None  # type: ignore

# ---------------------------------------------------------------- Constants

MAX_ENTRIES = 100
MAX_FILENAME_LEN = 255
MAX_PREFIX_LEN = 4096


# ---------------------------------------------------------------- Helpers — allowlist delegation


def _parse_allowlist(raw):
    """Parse raw list via allowlist module (fallback: empty)."""
    if _allowlist_parse is not None:
        try:
            return _allowlist_parse(raw)
        except Exception:
            pass
    # Fallback: minimal strict filter (file: / prefix: tags only, no bare compat)
    empty = {"files": set(), "prefixes": set()}
    if not isinstance(raw, list):
        return {"files": set(), "prefixes": set()}
    files = set()
    prefixes = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped:
            continue
        if stripped.startswith("file:"):
            part = stripped[5:].strip()
            if part and "/" not in part and "\\" not in part and ".." not in part:
                files.add(part)
        elif stripped.startswith("prefix:"):
            part = stripped[7:].strip()
            if part:
                prefixes.add(part.replace("\\", "/").rstrip("/"))
    return {"files": files, "prefixes": prefixes}


def _format_allowlist(parsed):
    """Format parsed dict via allowlist module (fallback: sorted tags)."""
    if _allowlist_format is not None:
        try:
            return _allowlist_format(parsed)
        except Exception:
            pass
    files = sorted((parsed or {}).get("files") or [])
    prefixes = sorted((parsed or {}).get("prefixes") or [])
    out = []
    for f in files:
        out.append("file:%s" % f)
    for p in prefixes:
        # minimal normalize
        norm = str(p).replace("\\", "/").rstrip("/")
        if norm.endswith(":"):
            norm = norm + "/"
        out.append("prefix:%s" % norm)
    return out


def _normalize_entry(entry):
    """Normalize a single entry (tagged or bare) to tagged form or None.

    Uses allowlist.normalize_allowlist_entry when available, otherwise
    minimal bare-compat logic (no slash -> file, slash/colon -> prefix).
    """
    if _allowlist_normalize is not None:
        try:
            return _allowlist_normalize(entry)
        except Exception:
            return None
    # Fallback minimal
    if not isinstance(entry, str):
        return None
    stripped = entry.strip()
    if not stripped:
        return None
    if stripped.startswith("file:"):
        part = stripped[5:].strip()
        if not part or "/" in part or "\\" in part or ".." in part:
            return None
        return "file:%s" % part
    if stripped.startswith("prefix:"):
        part = stripped[7:].strip()
        if not part:
            return None
        # minimal absolute check: contains ":" or starts with "/"
        if "/" not in part and "\\" not in part and ":" not in part:
            return None
        norm = part.replace("\\", "/").rstrip("/")
        return "prefix:%s" % norm
    has_slash = "/" in stripped or "\\" in stripped
    has_colon = ":" in stripped
    if has_slash or has_colon:
        # prefix attempt - require absolute-ish
        if not stripped:
            return None
        norm = stripped.replace("\\", "/").rstrip("/")
        return "prefix:%s" % norm
    else:
        if not stripped or "/" in stripped or "\\" in stripped:
            return None
        return "file:%s" % stripped


def _normalize_prefix_for_compare(p):
    """Normalize prefix for dedup/comparison (trailing slash stripped)."""
    if _allowlist_norm_prefix is not None:
        try:
            return _allowlist_norm_prefix(p)
        except Exception:
            pass
    s = str(p).replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    if s != "/" and not re.match(r"^[A-Za-z]:/$", s) and s.endswith("/"):
        s = s.rstrip("/")
    return s


# Backwards-compat validation helpers (now delegated to allowlist)

def validate_filename(name):
    """Strict file basename checks (delegated to allowlist)."""
    if _allowlist_validate_file is not None:
        try:
            return _allowlist_validate_file(name)
        except Exception:
            pass
    # Fallback old logic
    if not isinstance(name, str):
        return False, "filename must be a string"
    stripped = name.strip()
    if not stripped:
        return False, "filename must not be empty"
    if len(stripped) > MAX_FILENAME_LEN:
        return False, "filename too long (max %d)" % MAX_FILENAME_LEN
    if "/" in stripped or "\\" in stripped:
        return False, "filename must not contain path separators"
    if stripped in (".", ".."):
        return False, "filename must not be '.' or '..'"
    if ".." in stripped:
        return False, "filename must not contain '..'"
    if ":" in stripped:
        return False, "filename must not contain ':'"
    if os.path.basename(stripped) != stripped:
        return False, "filename must be basename only"
    return True, ""


def validate_prefix(path):
    """Strict prefix checks (delegated to allowlist)."""
    if _allowlist_validate_prefix is not None:
        try:
            return _allowlist_validate_prefix(path)
        except Exception:
            pass
    if not isinstance(path, str):
        return False, "prefix must be a string"
    stripped = path.strip()
    if not stripped:
        return False, "prefix must not be empty"
    if len(stripped) > MAX_PREFIX_LEN:
        return False, "prefix too long (max %d)" % MAX_PREFIX_LEN
    # minimal absolute check
    if not (os.path.isabs(stripped) or re.match(r"^[A-Za-z]:[\\/]", stripped) or stripped.startswith("\\")):
        return False, "prefix must be absolute"
    normalized_slashes = stripped.replace("\\", "/")
    parts = normalized_slashes.split("/")
    if ".." in parts:
        return False, "prefix must not contain '..'"
    return True, ""


def validate(entry):
    """Validate a tagged or bare entry (file: / prefix: / bare)."""
    norm = _normalize_entry(entry)
    if norm is None:
        return False, "invalid allowlist entry: %r" % entry
    return True, ""


# ---------------------------------------------------------------- Path resolution

def _get_config_path():
    """Locate dir-whip-config.yaml, profile-aware.

    Mirrors stats._stats_jsonl_path pattern: HERMES_HOME is layout-aware.
    When state.session.session_profile is set, resolve via _profile_home;
    otherwise fallback to report's captured ctx profile or registered_ctx.
    For tests (HERMES_HOME=tmp/hermes, profile default) this returns
    tmp/hermes/dir-whip/dir-whip-config.yaml.
    """
    home = _get_hermes_home()
    profile = None
    try:
        if getattr(state.session, "session_profile", None):
            profile = state.session.session_profile
    except Exception:
        pass
    if not profile:
        try:
            from . import report as _report
            ctx = _report._get_cmd_ctx()
            if ctx is not None and getattr(ctx, "profile_name", None):
                profile = ctx.profile_name  # type: ignore
        except Exception:
            pass
    if not profile:
        try:
            ctx2 = getattr(state.session, "registered_ctx", None)
            if ctx2 is not None and getattr(ctx2, "profile_name", None):
                profile = ctx2.profile_name  # type: ignore
        except Exception:
            pass
    if profile:
        try:
            home = _profile_home(home, profile)
        except Exception:
            pass
    return Path(home) / "dir-whip" / "dir-whip-config.yaml"


def _format_allowlist_flow(names):
    """Flow list line for allowlist (single unified key)."""
    if not names:
        return "allowlist: []"
    flow = json.dumps(list(names), ensure_ascii=False)
    return "allowlist: %s" % flow


# ---------------------------------------------------------------- Load

def load_allowlist():
    """Read current allowlist (yaml safe_load, strict fallback []).

    Returns a single list of tagged strings (file:<...> | prefix:<...>)
    via allowlist parse/format (validated, normalized, deduped, sorted).
    Missing key / non-list / unreadable file -> [] (strict empty, B2).
    Old keys exempt_paths / allowed_root_files are ignored (no compat).
    """
    path = _get_config_path()
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            raw = data.get("allowlist")
            if isinstance(raw, list):
                parsed = _parse_allowlist(raw)
                return _format_allowlist(parsed)
            # Strict fallback: key missing or not a list -> []
            return []
    except Exception:
        pass
    return []


# ---------------------------------------------------------------- Row-level write (preserves comments)

def write_allowlist(names):
    """Row-level edit of allowlist, preserving other lines/comments.

    - If the key exists, rewrite that line as flow list (handling block-form
      continuation lines for allowlist).
    - If not found, append the key at end with newline.
    - Strips legacy keys exempt_paths / allowed_root_files (both inline flow
      and block - list forms), preserving comments elsewhere.
    - Creates parent dir if missing, utf-8.
    - Assumes names already validated, deduped, normalized (via allowlist
      format). Handles trailing-slash normalization via allowlist.
    """
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Normalize via allowlist to ensure trailing-slash etc. are consistent
    # Caller should have already done this, but re-normalize for safety if
    # names is not already sorted tagged list.
    try:
        if isinstance(names, list):
            # If names are already tagged strings, parse+format normalizes them
            parsed_tmp = _parse_allowlist(names)
            names = _format_allowlist(parsed_tmp)
    except Exception:
        pass
    new_line = _format_allowlist_flow(names)
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
    pat_allow = re.compile(r"^\s*allowlist\s*:")
    pat_legacy_exempt = re.compile(r"^\s*exempt_paths\s*:")
    pat_legacy_allowed = re.compile(r"^\s*allowed_root_files\s*:")
    block_allow_pat = re.compile(r"^\s*allowlist\s*:\s*(?:#.*)?\s*$")
    block_legacy_pat = re.compile(r"^\s*(?:exempt_paths|allowed_root_files)\s*:\s*(?:#.*)?\s*$")
    item_pat = re.compile(r"^\s*-\s*.*$")
    idx = None
    for i, line in enumerate(lines):
        if pat_allow.search(line):
            idx = i
            break
    if idx is not None:
        # Detect block form for existing allowlist
        if block_allow_pat.match(lines[idx]):
            j = idx + 1
            while j < len(lines) and item_pat.match(lines[j]):
                j += 1
            new_lines = lines[:idx] + [new_line] + lines[j:]
            new_allow_idx = idx
        else:
            new_lines = lines[:idx] + [new_line] + lines[idx + 1 :]
            new_allow_idx = idx
        # Strip legacy keys elsewhere (preserve new allowlist line)
        filtered = []
        skip_block = False
        for k, ln in enumerate(new_lines):
            if k == new_allow_idx:
                filtered.append(ln)
                # If we are skipping a legacy block, this line is not legacy
                continue
            if pat_legacy_exempt.search(ln) or pat_legacy_allowed.search(ln):
                if block_legacy_pat.match(ln):
                    skip_block = True
                    continue
                else:
                    continue
            if skip_block:
                if item_pat.match(ln):
                    continue
                else:
                    skip_block = False
                    filtered.append(ln)
            else:
                filtered.append(ln)
        new_lines = filtered
        new_text = "\n".join(new_lines)
        if text.endswith("\n"):
            new_text += "\n"
        path.write_text(new_text, encoding="utf-8")
    else:
        # No allowlist key yet: strip legacy keys/blocks, then append
        cleaned = []
        skip_block = False
        for ln in lines:
            if pat_legacy_exempt.search(ln) or pat_legacy_allowed.search(ln):
                if block_legacy_pat.match(ln):
                    skip_block = True
                    continue
                else:
                    continue
            if skip_block:
                if item_pat.match(ln):
                    continue
                else:
                    skip_block = False
                    cleaned.append(ln)
            else:
                cleaned.append(ln)
        new_lines = cleaned + [new_line]
        new_text = "\n".join(new_lines)
        # Preserve final newline semantics: if original text had no trailing newline,
        # we ensure we still end with newline (cleaned join may not have it)
        # For append case, old logic ensured newline.
        if text and not text.endswith("\n"):
            # cleaned already joined without trailing newline, new_text will have newline via new_line addition
            # Ensure new_text ends with newline
            if not new_text.endswith("\n"):
                new_text += "\n"
        else:
            if not new_text.endswith("\n"):
                new_text += "\n"
            # If original ended with newline and we already have one, keep single
        # Simpler: ensure ends with newline
        if not new_text.endswith("\n"):
            new_text += "\n"
        # But to mimic old append: if text ends with newline, cleaned join + new_line\n is correct
        path.write_text(new_text, encoding="utf-8")
    _refresh_cache()


def _refresh_cache():
    """Narrow cache refresh so next verdict.classify_target sees new allowlist.

    The allowlist is read via load_guard_config() each time (no cache in
    config_writer itself, but config.py caches via get_cached_config), so
    a narrow refresh is required. This hook calls config._refresh_allowlist_cache.
    """
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


# ---------------------------------------------------------------- Public mutators for report.py

def add_allowlist(names):
    """Add entries to allowlist (idempotent, dedup, normalized).

    Intelligent discrimination via allowlist.normalize_allowlist_entry:
      no slash -> file:<basename>, slash/prefix: -> prefix:<abs-path>
    Handles trailing-slash normalization, bare-entry compat, absolute
    prefix validation, 100 cap. Writes via row-level edit and narrow
    refresh. Returns (new_list, added).
    Raises ValueError on invalid input or cap exceeded.
    """
    if not isinstance(names, list):
        raise ValueError("names must be a list")
    # Normalize each input via allowlist (handles bare compat)
    normalized = []
    for n in names:
        if not isinstance(n, str):
            raise ValueError("Invalid entry %r: must be a string" % n)
        norm = _normalize_entry(n)
        if norm is None:
            stripped = n.strip() if isinstance(n, str) else str(n)
            if stripped.startswith("file:"):
                raise ValueError("Invalid filename '%s': must be basename only" % n)
            has_slash = "/" in stripped or "\\" in stripped
            has_colon = ":" in stripped
            if stripped.startswith("prefix:") or has_slash or has_colon:
                raise ValueError("Invalid prefix '%s': must be absolute path" % n)
            raise ValueError("Invalid entry '%s'" % n)
        normalized.append(norm)
    # Dedup preserve order for input
    seen_input = set()
    uniq_normalized = []
    for n in normalized:
        if n not in seen_input:
            seen_input.add(n)
            uniq_normalized.append(n)
    current = load_allowlist()
    current_parsed = _parse_allowlist(current)
    # For cap/merge we need sets
    new_files = set(current_parsed.get("files") or [])
    new_prefixes = set(current_parsed.get("prefixes") or [])
    # Determine added preserving input order (uniq_normalized order)
    added = []
    # We need to compare each uniq_normalized entry against current sets
    # Use case-insensitive handling for dedup as allowlist does
    for entry in uniq_normalized:
        if entry.startswith("file:"):
            part = entry[5:]
            # Check existence (case-insensitive on Windows)
            exists = False
            if part in new_files:
                exists = True
            elif os.name == "nt":
                exists = any(existing.casefold() == part.casefold() for existing in new_files)
            else:
                # On POSIX, files are case-sensitive, exact
                exists = part in new_files
            if not exists:
                new_files.add(part)
                added.append(entry)
        elif entry.startswith("prefix:"):
            part = entry[7:]
            # part already normalized via _normalize_entry (trailing slash stripped)
            # Ensure we use normalized form for comparison
            part_norm = _normalize_prefix_for_compare(part)
            exists = False
            # need to check if any existing prefix equals part_norm (case-insensitive logic)
            for existing in new_prefixes:
                existing_norm = _normalize_prefix_for_compare(existing)
                if os.name == "nt":
                    if existing_norm.casefold() == part_norm.casefold():
                        exists = True
                        break
                else:
                    drive_re = re.compile(r"^[A-Za-z]:/")
                    if drive_re.match(existing_norm) and drive_re.match(part_norm):
                        if existing_norm.casefold() == part_norm.casefold():
                            exists = True
                            break
                    else:
                        if existing_norm == part_norm:
                            exists = True
                            break
            if not exists:
                # Add normalized form (not original part which may have different slash)
                new_prefixes.add(part_norm)
                added.append("prefix:%s" % part_norm)
    total = len(new_files) + len(new_prefixes)
    if total > MAX_ENTRIES:
        raise ValueError("Too many entries: max %d allowlisted items" % MAX_ENTRIES)
    merged = _format_allowlist({"files": new_files, "prefixes": new_prefixes})
    if added:
        write_allowlist(merged)
    return merged, added


def remove_allowlist(names):
    """Remove entries from allowlist. names may be str or list.

    Intelligent discrimination via allowlist.normalize_allowlist_entry.
    Writes and refreshes if changed. Returns (new_list, removed).
    Raises ValueError on invalid input.
    """
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        raise ValueError("names must be a string or list")
    normalized = []
    for n in names:
        if not isinstance(n, str):
            raise ValueError("Invalid entry %r: must be a string" % n)
        norm = _normalize_entry(n)
        if norm is None:
            stripped = n.strip() if isinstance(n, str) else str(n)
            if stripped.startswith("file:"):
                raise ValueError("Invalid filename '%s': must be basename only" % n)
            has_slash = "/" in stripped or "\\" in stripped
            has_colon = ":" in stripped
            if stripped.startswith("prefix:") or has_slash or has_colon:
                raise ValueError("Invalid prefix '%s': must be absolute path" % n)
            raise ValueError("Invalid entry '%s'" % n)
        normalized.append(norm)
    # Dedup preserve order
    seen = set()
    uniq_rem = []
    for n in normalized:
        if n not in seen:
            seen.add(n)
            uniq_rem.append(n)
    current = load_allowlist()
    current_parsed = _parse_allowlist(current)
    current_files = set(current_parsed.get("files") or [])
    current_prefixes = set(current_parsed.get("prefixes") or [])
    # Build removal sets
    rem_files = set()
    rem_prefixes = set()
    for entry in uniq_rem:
        if entry.startswith("file:"):
            rem_files.add(entry[5:])
        elif entry.startswith("prefix:"):
            rem_prefixes.add(entry[7:])
    new_files = set(current_files)
    new_prefixes = set(current_prefixes)
    removed = []
    # Files removal (case-insensitive on Windows)
    for f in list(new_files):
        for rf in rem_files:
            match = False
            if os.name == "nt":
                if f.casefold() == rf.casefold():
                    match = True
            else:
                if f == rf:
                    match = True
            if match:
                new_files.remove(f)
                removed.append("file:%s" % f)
                break
    # Prefixes removal (normalized, case-insensitive handling)
    for p in list(new_prefixes):
        p_norm = _normalize_prefix_for_compare(p)
        for rp in rem_prefixes:
            rp_norm = _normalize_prefix_for_compare(rp)
            match = False
            if os.name == "nt":
                if p_norm.casefold() == rp_norm.casefold():
                    match = True
            else:
                drive_re = re.compile(r"^[A-Za-z]:/")
                if drive_re.match(p_norm) and drive_re.match(rp_norm):
                    if p_norm.casefold() == rp_norm.casefold():
                        match = True
                else:
                    if p_norm == rp_norm:
                        match = True
            if match:
                new_prefixes.remove(p)
                # Use the stored normalized form for message (consistent)
                removed.append("prefix:%s" % p_norm)
                break
    if removed:
        merged = _format_allowlist({"files": new_files, "prefixes": new_prefixes})
        write_allowlist(merged)
        return merged, removed
    # No change
    merged = _format_allowlist({"files": new_files, "prefixes": new_prefixes})
    return merged, removed


# Backwards-compat aliases
load = load_allowlist
write = write_allowlist
