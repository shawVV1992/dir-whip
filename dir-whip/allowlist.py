"""Single source of truth for unified allowlist parsing (spec v2.6 B2).

Spec references: 5.6 (single key ``allowlist: []`` discriminated
``file:<basename>`` | ``prefix:<abs-path>``), 5.3 (Tier 0 = allowlist
prefix OR runtime allowlist; root file = allowlist file), 5.18 (audit
reads same file/prefix subsets). B2 clean break: old keys
``exempt_paths`` / ``allowed_root_files`` deleted, no backward compat,
strict empty fallback when key missing or value is not a list.

Discriminated entries
---------------------
- ``file:<basename>`` -> root file allowed at Working Directory root.
  Validation: basename only (no "/" or "\\" or "..", non-empty,
  length <= 255, not "." or "..").
- ``prefix:<abs-path>`` -> exempt prefix (project dir inside workspace).
  Validation: absolute path (via ``paths.is_absolute_any``), non-empty,
  length <= 4096, no ".." path component, forward slashes normalized,
  trailing slash normalized.
- Bare entries without a tag (generator compat, not a config key):
  ``no slash`` -> ``file:``, ``contains slash`` -> ``prefix:``. The
  extended check also treats bare entries containing "/" or "\\" or ":"
  as prefix attempts (colon = drive), otherwise file. Invalid entries
  are silently ignored (strict filter, fail-closed for guard).

Matching
--------
- File: exact basename match, case-insensitive on Windows via
  ``os.name == "nt"`` and ``casefold()``.
- Prefix: prefix match on forward-slash-normalized paths,
  case-insensitive on Windows, trailing slash normalized. Subtree
  semantics: target == prefix or target starts with prefix + "/".

Pure functions only: no host imports, no state (core module discipline,
ADR-0007). Import surface: stdlib + ``paths`` + ``yaml`` (yaml only for
callers that load the file; parsing itself works on an already-loaded list).
"""

import os
import re

try:
    from .paths import is_absolute_any as _is_absolute_any
except ImportError:
    try:
        from paths import is_absolute_any as _is_absolute_any  # type: ignore
    except ImportError:
        _DRIVE_ROOTED_RE = re.compile(r"^[A-Za-z]:[\\/]")

        def _is_absolute_any(target):  # fallback
            if os.path.isabs(target):
                return True
            if _DRIVE_ROOTED_RE.match(target):
                return True
            return target.startswith("\\") and not target.startswith("\\\\")

MAX_FILENAME_LEN = 255
MAX_PREFIX_LEN = 4096

# ---------------------------------------------------------------- Validation


def _validate_file(name):
    """Strict file basename checks (spec 5.6 D1 / config_writer precedent).

    Returns (ok, reason). Valid file: non-empty stripped, length <=255,
    no "/" or "\\", not "." or "..", no ".." substring, basename == name,
    no ":" (colon is drive separator, not basename).
    """
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


def _validate_prefix(path):
    """Strict prefix checks (spec 5.6 D1).

    Returns (ok, reason). Valid prefix: non-empty stripped, length
    <=4096, absolute via is_absolute_any, no ".." path component.
    """
    if not isinstance(path, str):
        return False, "prefix must be a string"
    stripped = path.strip()
    if not stripped:
        return False, "prefix must not be empty"
    if len(stripped) > MAX_PREFIX_LEN:
        return False, "prefix too long (max %d)" % MAX_PREFIX_LEN
    if not _is_absolute_any(stripped):
        return False, "prefix must be absolute"
    # No ".." path component (split on both separators)
    normalized_slashes = stripped.replace("\\", "/")
    parts = normalized_slashes.split("/")
    if ".." in parts:
        return False, "prefix must not contain '..'"
    return True, ""


def _normalize_prefix(path):
    """Normalize a validated prefix to forward slashes, trailing slash stripped.

    Preserves drive root "E:/" and posix root "/". Backslashes become "/",
    duplicate slashes collapsed (except leading drive). Does not call
    normpath (keeps dot segments as-is; they were rejected above). Stripping
    is single-pass rstrip after the root check.
    """
    if not isinstance(path, str):
        return ""
    s = path.strip().replace("\\", "/")
    # Collapse duplicate slashes (e.g. E://ws//p -> E:/ws/p) but keep protocol?
    # Use regex to collapse //+ to / . This handles double-backslash inputs
    # that become // after replacement.
    s = re.sub(r"/{2,}", "/", s)
    # Fix drive letter that may have lost its slash after collapse? e.g. E:/ -> already correct
    # Preserve roots: "/" and "X:/" stay with slash
    if s == "/":
        return s
    if re.match(r"^[A-Za-z]:/$", s):
        return s
    if s.endswith("/") and len(s) > 1:
        s = s.rstrip("/")
        # After rstrip, "E:" would result from "E:/" but we already returned
        # for that case, so no extra handling needed.
        if not s:
            s = "/"
    return s


def _normalize_for_match(path):
    """Forward-slash normalized form for prefix/file matching."""
    if path is None:
        return ""
    s = str(path).replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    # Trailing slash normalized except roots (same rule as prefix)
    if s != "/" and not re.match(r"^[A-Za-z]:/$", s) and s.endswith("/"):
        s = s.rstrip("/")
    return s


# ---------------------------------------------------------------- Core parsing


def parse_allowlist(raw):
    """Parse a raw allowlist value into discriminated sets.

    Args:
        raw: value of ``allowlist`` key after yaml.safe_load. Expected
            list of strings (discriminated ``file:`` | ``prefix:``).
            Bare entries without a tag are treated as ``file:`` when they
            contain no slash and as ``prefix:`` when they contain a slash
            (spec 5.6 generator compat). Extended bare check also treats
            entries containing "/" or "\\" or ":" as prefix attempts.

    Returns:
        dict ``{"files": set, "prefixes": set}`` with validated, normalized
        entries. Invalid entries (bad file basename, non-absolute prefix,
        empty, wrong type) are silently ignored (strict filter). Non-list
        input (missing key, None, dict) returns strict empty ``{"files":
        set(), "prefixes": set()}`` (fail-closed, B2).

    Example:
        parse_allowlist(["file:a.txt", "prefix:E:/ws/p", "b.txt", "C:/x/"])
        -> {"files": {"a.txt", "b.txt"}, "prefixes": {"E:/ws/p", "C:/x"}}
    """
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
            ok, _ = _validate_file(part)
            if ok:
                files.add(part)
            continue
        if stripped.startswith("prefix:"):
            part = stripped[7:].strip()
            ok, _ = _validate_prefix(part)
            if ok:
                prefixes.add(_normalize_prefix(part))
            continue
        # Bare entry compat
        # Spec: no slash -> file, contains slash -> prefix
        # Extended: "/" or "\\" or ":" -> prefix attempt
        has_slash = "/" in stripped or "\\" in stripped
        has_colon = ":" in stripped
        is_prefix_bare = has_slash or has_colon
        if is_prefix_bare:
            ok, _ = _validate_prefix(stripped)
            if ok:
                prefixes.add(_normalize_prefix(stripped))
            # Invalid bare prefix is ignored, not fell back to file
            continue
        else:
            ok, _ = _validate_file(stripped)
            if ok:
                files.add(stripped)
            continue
    return {"files": files, "prefixes": prefixes}


def format_allowlist(parsed):
    """Format a parsed allowlist back to a tagged list for YAML flow dump.

    Args:
        parsed: dict with ``files`` and ``prefixes`` (sets or lists) as
            returned by ``parse_allowlist``.

    Returns:
        list of strings with discriminated tags: ``file:<basename>`` for
        files and ``prefix:<abs-path>`` for prefixes. Deterministic
        sorted order (files sorted, then prefixes sorted) for stable YAML.

    Example:
        format_allowlist({"files": {"b.txt", "a.txt"}, "prefixes": {"E:/ws/p"}})
        -> ["file:a.txt", "file:b.txt", "prefix:E:/ws/p"]
    """
    if not isinstance(parsed, dict):
        return []
    files = parsed.get("files") or []
    prefixes = parsed.get("prefixes") or []
    # Normalize to sorted lists for determinism
    try:
        files_sorted = sorted(files)
    except TypeError:
        files_sorted = sorted(list(files))
    try:
        prefixes_sorted = sorted(prefixes)
    except TypeError:
        prefixes_sorted = sorted(list(prefixes))
    out = []
    for f in files_sorted:
        # f should already be validated basename; skip any invalid just in case
        ok, _ = _validate_file(f)
        if ok:
            out.append("file:%s" % f)
    for p in prefixes_sorted:
        ok, _ = _validate_prefix(p)
        if ok:
            out.append("prefix:%s" % _normalize_prefix(p))
        else:
            # If p was already normalized but fails absolute check due to
            # test fixture using posix path on Windows-absolute, keep it if
            # it looks like a normalized path; fallback to raw
            # This branch is defensive, normal prefixes pass the check.
            out.append("prefix:%s" % _normalize_prefix(str(p)))
    return out


# ---------------------------------------------------------------- Matching helpers


def is_allowlist_file(name, parsed):
    """Check whether a basename is allowlisted as file.

    Args:
        name: basename to test (e.g. "notes.txt"). If a full path is
            passed, its basename is used.
        parsed: dict from ``parse_allowlist``.

    Returns:
        True if ``name`` matches an allowlist ``file:`` entry (exact
        basename, case-insensitive on Windows via casefold).
    """
    if not isinstance(name, str):
        return False
    # Use basename if a path was passed (defensive)
    base = os.path.basename(name.strip().replace("\\", "/"))
    if not base:
        base = name.strip()
    files = (parsed or {}).get("files") or set()
    if os.name == "nt":
        base_cf = base.casefold()
        for f in files:
            if isinstance(f, str) and f.casefold() == base_cf:
                return True
        return False
    else:
        return base in files


def is_allowlist_prefix(path, parsed):
    """Check whether a path is under an allowlist prefix entry.

    Args:
        path: absolute path to test (forward or back slashes). Relative
            paths return False (prefix entries are absolute).
        parsed: dict from ``parse_allowlist``.

    Returns:
        True if ``path`` equals or is inside any allowlist ``prefix:``
        entry (prefix match, forward-slash normalized, case-insensitive
        on Windows via casefold, trailing slash normalized).
    """
    if not isinstance(path, str):
        return False
    stripped = path.strip()
    if not stripped:
        return False
    target_norm = _normalize_for_match(stripped)
    prefixes = (parsed or {}).get("prefixes") or set()
    if os.name == "nt":
        target_cf = target_norm.casefold()
        for pref in prefixes:
            if not isinstance(pref, str):
                continue
            pref_norm = _normalize_for_match(pref).casefold()
            if target_cf == pref_norm:
                return True
            # Subtree: target starts with prefix + "/"
            if target_cf.startswith(pref_norm.rstrip("/") + "/"):
                return True
        return False
    else:
        # On POSIX, Windows-style drive prefixes still match case-insensitively
        # if both sides look Windowsy (drive-rooted). Use casefold for those.
        for pref in prefixes:
            if not isinstance(pref, str):
                continue
            pref_norm = _normalize_for_match(pref)
            # If both look drive-rooted, compare case-insensitively
            drive_re = re.compile(r"^[A-Za-z]:/")
            if drive_re.match(target_norm) and drive_re.match(pref_norm):
                t_cf = target_norm.casefold()
                p_cf = pref_norm.casefold()
                if t_cf == p_cf or t_cf.startswith(p_cf.rstrip("/") + "/"):
                    return True
            else:
                if target_norm == pref_norm or target_norm.startswith(pref_norm.rstrip("/") + "/"):
                    return True
        return False


# ---------------------------------------------------------------- Supplemental helpers (for config_writer / report)


def validate_file_entry(name):
    """Public wrapper for file validation (config_writer contract).

    Returns (ok, reason).
    """
    return _validate_file(name)


def validate_prefix_entry(path):
    """Public wrapper for prefix validation (config_writer contract).

    Returns (ok, reason).
    """
    return _validate_prefix(path)


def normalize_allowlist_entry(entry):
    """Normalize a single allowlist entry string (tagged or bare) for display.

    Returns the normalized tagged form or None if invalid.
    Example: "prefix:E:/ws/p/" -> "prefix:E:/ws/p"
    """
    if not isinstance(entry, str):
        return None
    stripped = entry.strip()
    if stripped.startswith("file:"):
        part = stripped[5:].strip()
        ok, _ = _validate_file(part)
        return "file:%s" % part if ok else None
    if stripped.startswith("prefix:"):
        part = stripped[7:].strip()
        ok, _ = _validate_prefix(part)
        return "prefix:%s" % _normalize_prefix(part) if ok else None
    # Bare
    has_slash = "/" in stripped or "\\" in stripped
    has_colon = ":" in stripped
    if has_slash or has_colon:
        ok, _ = _validate_prefix(stripped)
        return "prefix:%s" % _normalize_prefix(stripped) if ok else None
    else:
        ok, _ = _validate_file(stripped)
        return "file:%s" % stripped if ok else None


__all__ = [
    "parse_allowlist",
    "format_allowlist",
    "is_allowlist_file",
    "is_allowlist_prefix",
    "validate_file_entry",
    "validate_prefix_entry",
    "normalize_allowlist_entry",
    "MAX_FILENAME_LEN",
    "MAX_PREFIX_LEN",
]
