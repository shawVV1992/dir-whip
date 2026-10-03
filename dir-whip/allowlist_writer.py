"""Allowlist config writer: row-level YAML edit preserving comments -- structured ``{files, dirs}`` mapping (spec 5.6).

``files`` = root-level file basenames, ``dirs`` = root-relative recursive
subtree. Each key stays a single flow-style line (``files: ["a", "b"]``);
the ``allowlist`` block is replaced line-level with comments above the key
preserved -- block-style ``- item`` lists are never produced. Pure stdlib,
no host imports; path resolution is single-sourced in
paths.config_file_path.

Layer: core
Refs: spec 5.6
Key exports:
  - load_config -- current allowlist as structured {"files": [sorted], "dirs": [sorted]}.
  - load_allowlist_legacy_count -- count of ignored legacy flat entries (clean-break visibility signal).
  - write_config -- row-level edit writing the two-key flow block; comments preserved.
"""

import json
import re

from .paths import config_file_path

from .allowlist import load_allowlist_state
from .allowlist import format_allowlist as _allowlist_format

# ---------------------------------------------------------------- Constants

MAX_ENTRIES = 100


# ---------------------------------------------------------------- Parse/format delegation


def _format_mapping(parsed):
    """Format parsed sets into the canonical mapping of sorted lists."""
    try:
        return _allowlist_format(parsed)
    except Exception:
        return {
            "files": sorted(str(f) for f in (parsed or {}).get("files") or []),
            "dirs": sorted(str(d) for d in (parsed or {}).get("dirs") or []),
        }


# ---------------------------------------------------------------- Path resolution


# ---------------------------------------------------------------- Load

def load_config():
    """Read the current allowlist as the structured mapping.

    Thin delegate to allowlist.load_allowlist_state (ONE read-side
    source). Returns {"files": [sorted...], "dirs": [sorted...]} --
    validated, normalized, deduped; missing/unreadable file -> empty
    mapping (fail-closed).
    """
    return load_allowlist_state()[0]


def load_allowlist_legacy_count():
    """Number of ignored legacy flat entries under the allowlist key.

    Thin delegate to allowlist.load_allowlist_state (ONE read-side
    source); non-zero only when the raw value is a LIST with string
    entries -- the clean-break visibility signal for /dir-whip list.
    """
    return load_allowlist_state()[1]


# ---------------------------------------------------------------- Row-level write (preserves comments)

def _flow(values):
    """JSON flow list for one key line (ensure_ascii=False, stable)."""
    return json.dumps(list(values), ensure_ascii=False)


def _allowlist_block(mapping):
    """The three canonical lines replacing/creating the allowlist block."""
    return [
        "allowlist:",
        "  files: %s" % _flow(mapping.get("files") or []),
        "  dirs: %s" % _flow(mapping.get("dirs") or []),
    ]


_PAT_ALLOW = re.compile(r"^\s*allowlist\s*:")
_PAT_LEGACY = re.compile(r"^\s*(?:exempt_paths|allowed_root_files)\s*:")


def write_config(mapping):
    """Row-level edit writing the two-key flow block, preserving comments.

    - Existing ``allowlist`` key: replace it AND every following
      more-indented line (old flat ``- item`` continuations, old/new
      ``files:/dirs:`` sub-lines) with the canonical three-line block.
    - Absent: strip legacy exempt_paths/allowed_root_files keys, then
      append the block at the end.
    - Creates parent dir if missing, utf-8; refreshes the config cache
      narrowly so the next classify sees the new allowlist.
    """
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping = _format_mapping(mapping if isinstance(mapping, dict) else {})

    def _write_lines(final_lines, had_trailing_newline):
        new_text = "\n".join(final_lines)
        if had_trailing_newline or not final_lines:
            new_text += "\n"
        path.write_text(new_text, encoding="utf-8")

    if not path.is_file():
        _write_lines(_allowlist_block(mapping), True)
        _refresh_cache()
        return
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        _write_lines(_allowlist_block(mapping), True)
        _refresh_cache()
        return
    lines = text.splitlines()
    had_nl = text.endswith("\n")
    idx = None
    for i, line in enumerate(lines):
        if _PAT_ALLOW.match(line):
            idx = i
            break
    if idx is not None:
        # Consume the whole indented block below the key (flat items,
        # files/dirs sub-lines, indented comments).
        j = idx + 1
        while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
            j += 1
        new_lines = lines[:idx] + _allowlist_block(mapping) + lines[j:]
    else:
        # No allowlist key: drop legacy keys (+ their indented blocks), append.
        cleaned = []
        skip_block = False
        for ln in lines:
            if _PAT_LEGACY.match(ln):
                skip_block = True
                continue
            if skip_block:
                if ln.startswith(" ") or ln.startswith("\t"):
                    continue
                skip_block = False
            cleaned.append(ln)
        new_lines = cleaned + _allowlist_block(mapping)
    _write_lines(new_lines, had_nl)
    _refresh_cache()


def _refresh_cache():
    """Narrow cache refresh so the next classify sees the new allowlist.

    The allowlist is read via load_guard_config() each time, but config.py
    caches via get_cached_config, so a refresh is required; delegates to
    runtime_allowlist.refresh_allowlist_cache (fail-open).
    """
    try:
        from . import runtime_allowlist as _ra
        _ra.refresh_allowlist_cache()
    except Exception:
        pass
