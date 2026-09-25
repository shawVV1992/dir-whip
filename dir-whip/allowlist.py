"""Structured allowlist parsing / matching (``{files, dirs}`` mapping, root-relative) -- single source of truth.

Storage is relative to working_dir_root (absolute input is input-layer
tolerance only); legacy flat / non-dict input is ignored fail-closed
(empty sets), legacy shapes surfacing on the report/list surfaces.
``files`` = exact basename match (casefold on Windows), ``dirs`` =
recursive subtree exemption under <working_dir_root>/<entry>; the root
is never exempt. Pure functions, no host imports, no state.

Layer: core
Refs: spec 5.6, spec 5.3, ADR-0007
Key exports:
  - parse_allowlist -- raw ``allowlist`` config value -> validated ``{"files": set, "dirs": set}`` (fail-closed).
  - parse_allowlist_raw -- raw value passthrough (list/dict kept; scalars -> []).
  - format_allowlist -- parsed sets -> canonical sorted ``{"files": [...], "dirs": [...]}`` mapping.
  - is_allowlist_file -- root-file basename exemption (exact match, casefold on Windows).
  - is_allowlist_dir -- recursive subtree exemption under <working_dir_root>/<entry>; root never exempt.
  - validate_file_entry / validate_dir_entry / normalize_dir_entry -- entry validation wrappers (ok, reason) + stored-form dir normalization.
"""

import os
import re

from .paths import is_absolute_any as _is_absolute_any

MAX_FILENAME_LEN = 255
MAX_DIR_LEN = 4096

# ---------------------------------------------------------------- Validation


def _validate_file(name):
    """Strict file basename checks (spec 5.6).

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


def _validate_dir_rel(path):
    """Strict relative-dir checks (spec 5.6).

    Returns (ok, reason). Valid dir entry: non-empty stripped string,
    RELATIVE to working_dir_root (no drive/absolute forms, no ":"),
    no "." or ".." path segments, length <=4096. Multi-level allowed.
    Trailing slashes are a storage-normalization concern (stripped by
    _normalize_dir_rel), not a validation failure.
    """
    if not isinstance(path, str):
        return False, "dir entry must be a string"
    stripped = path.strip()
    if not stripped:
        return False, "dir entry must not be empty"
    if len(stripped) > MAX_DIR_LEN:
        return False, "dir entry too long (max %d)" % MAX_DIR_LEN
    if stripped in (".", ".."):
        return False, "dir entry must not be '.' or '..'"
    if _is_absolute_any(stripped):
        return False, "dir entry must be relative to the Working Directory root"
    if ":" in stripped:
        return False, "dir entry must not contain ':'"
    normalized = stripped.replace("\\", "/")
    if normalized.startswith("/"):
        return False, "dir entry must be relative to the Working Directory root"
    parts = normalized.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False, "dir entry must not contain '.', '..' or empty segments"
    return True, ""


def _normalize_dir_rel(path):
    """Normalize a validated dir entry: forward slashes, trailing slash
    stripped, duplicate slashes collapsed."""
    if not isinstance(path, str):
        return ""
    s = path.strip().replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    while s.endswith("/") and len(s) > 1:
        s = s.rstrip("/")
    return s.rstrip("/")


def _normalize_for_match(path):
    """Forward-slash normalized form for dir matching."""
    if path is None:
        return ""
    s = str(path).replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    # Trailing slash normalized except roots
    if s != "/" and not re.match(r"^[A-Za-z]:/$", s) and s.endswith("/"):
        s = s.rstrip("/")
    return s


# ---------------------------------------------------------------- Core parsing


def parse_allowlist(raw):
    """Parse the raw ``allowlist`` config value into structured sets.

    ``raw`` is the value of the ``allowlist`` key after yaml.safe_load;
    the expected mapping is ``{"files": [...], "dirs": [...]}`` with
    root-relative entries. A legacy flat list or any non-dict input is
    ignored fail-closed -> empty sets. Invalid entries are silently
    ignored (hand-edited configs fail-closed; guard and audit agree).
    ``parse_allowlist({"files": ["a.txt"], "dirs": ["proj/sub"]})`` ->
    ``{"files": {"a.txt"}, "dirs": {"proj/sub"}}``.
    """
    if not isinstance(raw, dict):
        # Legacy flat list / missing key / scalar -> fail-closed ignore.
        return {"files": set(), "dirs": set()}
    files = set()
    dirs = set()
    raw_files = raw.get("files")
    if isinstance(raw_files, (list, tuple, set)):
        for item in raw_files:
            ok, _ = _validate_file(item)
            if ok:
                files.add(item.strip())
    raw_dirs = raw.get("dirs")
    if isinstance(raw_dirs, (list, tuple, set)):
        for item in raw_dirs:
            ok, _ = _validate_dir_rel(item)
            if ok:
                dirs.add(_normalize_dir_rel(item))
    return {"files": files, "dirs": dirs}


def parse_allowlist_raw(value):
    """RAW passthrough of the allowlist config value.

    Parsing/validation is done by parse_allowlist at the consumption
    points (guard / audit / report). Keeping the raw value (structured
    dict, legacy flat list, or []) preserves the loaded-value contract
    while legacy flat lists stay visible for the clean-break hint.
    Non-list/dict scalars -> [].
    """
    if isinstance(value, (list, dict)):
        return value
    return []


def format_allowlist(parsed):
    """Format a parsed allowlist back to the structured mapping form.

    Input: dict with ``files`` and ``dirs`` (sets or lists) as returned
    by parse_allowlist. Output: ``{"files": [sorted...], "dirs":
    [sorted...]}`` -- the canonical YAML shape (deterministic sorted
    order for stable flow-style writing); invalid entries are dropped.
    """
    if not isinstance(parsed, dict):
        return {"files": [], "dirs": []}
    files_out = []
    for f in (parsed.get("files") or []):
        ok, _ = _validate_file(f)
        if ok:
            files_out.append(str(f).strip())
    dirs_out = []
    for d in (parsed.get("dirs") or []):
        ok, _ = _validate_dir_rel(d)
        if ok:
            dirs_out.append(_normalize_dir_rel(d))
    return {"files": sorted(files_out), "dirs": sorted(dirs_out)}


# ---------------------------------------------------------------- Matching helpers


def is_allowlist_file(name, parsed):
    """Check whether a basename is allowlisted as a root file.

    Uses the basename when a full path is passed; matching is exact and
    case-insensitive on Windows via casefold. ``parsed`` is the dict
    from parse_allowlist.
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


def is_allowlist_dir(path, working_dir_root, parsed):
    """Check whether a path is exempted by an allowlist ``dirs`` entry.

    True when ``path`` equals or is UNDER ``<root>/<entry>`` for any
    dirs entry (recursive subtree exemption, forward-slash normalized,
    case-insensitive on Windows and for drive-rooted pairs on any host).
    The root itself and anything outside it are never exempt.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    if not working_dir_root:
        return False
    t = _normalize_for_match(path)
    r = _normalize_for_match(working_dir_root)
    if not t or not r:
        return False
    cf = os.name == "nt" or (
        _is_absolute_any(t) and _is_absolute_any(r)
    )
    t_cmp = t.casefold() if cf else t
    r_cmp = r.casefold() if cf else r
    r_cmp = r_cmp.rstrip("/")
    if t_cmp == r_cmp:
        return False  # the root itself is never exempt
    prefix = r_cmp + "/"
    if not t_cmp.startswith(prefix):
        return False  # outside the root -> never exempt
    rel = t[len(r.rstrip("/")) + 1:]
    rel_cmp = rel.casefold() if cf else rel
    for d in (parsed or {}).get("dirs") or set():
        if not isinstance(d, str):
            continue
        d_norm = _normalize_for_match(d)
        d_cmp = d_norm.casefold() if cf else d_norm
        if rel_cmp == d_cmp or rel_cmp.startswith(d_cmp + "/"):
            return True
    return False


# ---------------------------------------------------------------- Supplemental helpers (for allowlist_writer / report)


def validate_file_entry(name):
    """Public wrapper for file validation; returns (ok, reason)."""
    return _validate_file(name)


def validate_dir_entry(rel):
    """Public wrapper for relative-dir validation; returns (ok, reason)."""
    return _validate_dir_rel(rel)


def normalize_dir_entry(rel):
    """Normalize a dir entry to stored form (fwd slashes, no trailing
    slash) or None when invalid."""
    ok, _ = _validate_dir_rel(rel)
    return _normalize_dir_rel(rel) if ok else None


__all__ = [
    "parse_allowlist",
    "parse_allowlist_raw",
    "format_allowlist",
    "is_allowlist_file",
    "is_allowlist_dir",
    "validate_file_entry",
    "validate_dir_entry",
    "normalize_dir_entry",
    "MAX_FILENAME_LEN",
    "MAX_DIR_LEN",
]
