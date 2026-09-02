"""Allowlist config writer (SCR-039 R9, v2.7 structured mapping).

Row-level YAML edit preserving comments, strict allowlist validation,
and narrow cache refresh. Pure stdlib + pyyaml, no host imports
(ADR-0007). Follows report.py precedent (line scan + regex).

Structured mapping (v2.7 R9, BREAKING clean break of the v2.6 flat
tagged list)::

    allowlist:
      files: ["a.txt", "b.txt"]   # root-level file basenames
      dirs: ["proj", "proj/sub"]  # root-relative, recursive subtree

Storage/edit style (plan ruling): each key stays a SINGLE flow-style
line (``files: ["a", "b"]``); the whole ``allowlist`` block is replaced
line-level; comments ABOVE the key are preserved; block-style
``- item`` lists are never produced.

Path resolution: HERMES_HOME/dir-whip/dir-whip-config.yaml with
profile_home awareness (stats.stats_jsonl_path pattern).
"""

import json
import re
from pathlib import Path

import yaml

from .paths import get_hermes_home, profile_home

from . import state

from .allowlist import parse_allowlist as _allowlist_parse
from .allowlist import format_allowlist as _allowlist_format

# ---------------------------------------------------------------- Constants

MAX_ENTRIES = 100


# ---------------------------------------------------------------- Parse/format delegation


def _parse_mapping(raw):
    """Parse raw value via allowlist module (fallback: empty mapping)."""
    if _allowlist_parse is not None:
        try:
            return _allowlist_parse(raw)
        except Exception:
            pass
    if not isinstance(raw, dict):
        return {"files": set(), "dirs": set()}
    files = {f for f in (raw.get("files") or []) if isinstance(f, str) and f.strip()}
    dirs = {
        str(d).replace("\\", "/").strip().rstrip("/")
        for d in (raw.get("dirs") or [])
        if isinstance(d, str) and d.strip()
    }
    return {"files": files, "dirs": dirs}


def _format_mapping(parsed):
    """Format parsed sets into the canonical mapping of sorted lists."""
    if _allowlist_format is not None:
        try:
            return _allowlist_format(parsed)
        except Exception:
            pass
    return {
        "files": sorted(str(f) for f in (parsed or {}).get("files") or []),
        "dirs": sorted(str(d) for d in (parsed or {}).get("dirs") or []),
    }


# ---------------------------------------------------------------- Path resolution

def _get_config_path():
    """Locate dir-whip-config.yaml, profile-aware.

    Mirrors stats.stats_jsonl_path pattern: HERMES_HOME is layout-aware.
    When state.session.session_profile is set, resolve via profile_home;
    otherwise fallback to report's captured ctx profile or registered_ctx.
    For tests (HERMES_HOME=tmp/hermes, profile default) this returns
    tmp/hermes/dir-whip/dir-whip-config.yaml.
    """
    home = get_hermes_home()
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
            home = profile_home(home, profile)
        except Exception:
            pass
    return Path(home) / "dir-whip" / "dir-whip-config.yaml"


# ---------------------------------------------------------------- Load

def load_allowlist():
    """Read the current allowlist as the structured mapping.

    Returns {"files": [sorted...], "dirs": [sorted...]} — validated,
    normalized, deduped via allowlist parse/format. Missing key / legacy
    flat value / unreadable file -> empty mapping (fail-closed, clean
    break: legacy flat values are IGNORED, surfaced as legacy hints by
    the command layer).
    """
    path = _get_config_path()
    if not path.is_file():
        return {"files": [], "dirs": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        raw = data.get("allowlist") if isinstance(data, dict) else None
        return _format_mapping(_parse_mapping(raw))
    except Exception:
        pass
    return {"files": [], "dirs": []}


def load_allowlist_legacy_count():
    """Number of ignored legacy flat entries under the allowlist key.

    Non-zero only when the raw value is a LIST (v2.6 flat format) with
    string entries — the clean-break visibility signal for /dir-whip list.
    """
    path = _get_config_path()
    if not path.is_file():
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        raw = data.get("allowlist") if isinstance(data, dict) else None
        if isinstance(raw, list):
            return sum(1 for x in raw if isinstance(x, str) and x.strip())
    except Exception:
        pass
    return 0


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


def write_allowlist(mapping):
    """Row-level edit writing the two-key flow block, preserving comments.

    - If an ``allowlist`` key exists, replace it AND every following
      more-indented line (old flat ``- item`` continuations, old/new
      ``files:/dirs:`` sub-lines) with the canonical three-line block.
    - If absent, strip legacy exempt_paths/allowed_root_files keys then
      append the block at the end.
    - Creates parent dir if missing, utf-8. Refreshes the config cache
      narrowly so the next classify sees the new allowlist.
    """
    path = _get_config_path()
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
        pass


# Backwards-compat aliases
load = load_allowlist
write = write_allowlist
