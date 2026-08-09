#!/usr/bin/env python3
"""S0: Shared READ-ONLY workspace resolver (SCR-011).

Shared memo/config access module imported by all four scripts
(create_session_dir.py, audit_workspace.py, clean_tmp.py, init_workspace.py).
Breaks the "no cross-imports between scripts" rule -- documented exception in
engineering-constraints.md section 4 (SCR-011, 2026-08-09).

READ-ONLY by design: this module never writes or rebuilds the profile
workspace memo (no save_memo, no rebuild, no hermes_cli). The plugin
(config.py) is the memo's SOLE writer (sync_memo() + the
workspace_guard_register_workspace tool). The skill consumes the memo
through this module only.

Memo schema (unchanged, SCR-001):
    {"synced_at": iso, "profiles": {name: {"workspace", "status",
    "changed_at"}}}

Functions:
    hermes_home()           -- Hermes home dir (HERMES_HOME env override
                               first; Windows LOCALAPPDATA/hermes, POSIX
                               ~/.hermes)
    read_memo(hh)           -- memo dict or None (missing/corrupt)
    plugin_trace(hh)        -- True iff <hh>/workspace-guard/ exists (plugin
                               installed/configured) -- gates the standalone
                               fallback
    profile_workspace(name, memo) -- one profile's workspace value
    list_workspaces(memo)   -- all profile workspace values
    normalize_path(path)    -- separator + case normalization (Windows
                               casefold, POSIX exact) for exact matching
    add_profile_arg(parser) -- shared --profile CLI argument
    allowed_root_files(hh)  -- root-file whitelist from guard-config.yaml
                               (allowed_root_files); STRICT fallback when the
                               config/key is absent: empty whitelist -> every
                               root file flagged (fail-closed, over-report)
    validate_workspace(path, hh) -- memo-based validation flow (SCR-011 2.4)
                               with plugin-trace detection and standalone
                               fallback
"""

import json
import os
import sys

MEMO_SUBDIR = "workspace-guard"
MEMO_FILE = "profile-workspaces.json"
CONFIG_FILE = "guard-config.yaml"

# Standalone warning: emitted to stderr on EVERY invocation (safety/audit
# net). Never pollutes stdout or --gate JSON. The user-facing explanation
# happens once per session via SKILL.md teaching.
STANDALONE_WARNING = (
    "warning: memo unavailable and no workspace-guard plugin detected; "
    "standalone mode (trusting the provided --workspace)\n"
)

# Fail-closed messages (exit code 2 paths).
MEMO_MISS_MESSAGE = (
    "not a registered profile workspace (if this is a new workspace, "
    "register it via the workspace_guard_register_workspace tool)"
)
PLUGIN_MEMO_MISSING_MESSAGE = (
    "plugin detected but memo unavailable; run /workspace-guard "
    "workspace_update to rebuild it"
)


def hermes_home():
    """Return the Hermes home directory path.

    HERMES_HOME environment variable wins when set (Hermes' canonical
    override, used by tests to isolate from any real installation);
    otherwise Windows: LOCALAPPDATA/hermes, POSIX: ~/.hermes.
    """
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return env_home
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _memo_path(hh):
    """Memo file location under a Hermes home."""
    return os.path.join(hh, MEMO_SUBDIR, MEMO_FILE)


def read_memo(hh=None):
    """Load the profile workspace memo; None on missing/corrupt.

    Read-only: never creates or repairs the file. Corrupt JSON returns
    None (the caller decides between standalone fallback and fail-closed
    via plugin_trace()). Unlike the plugin's fail-open load_memo, this
    module does NOT fall back to the .bak copy -- a corrupt memo is a
    plugin-side problem, and the scripts surface it (fail-closed with a
    workspace_update prompt) instead of silently using stale data.
    """
    if hh is None:
        hh = hermes_home()
    try:
        with open(_memo_path(hh), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return None
        return data
    except Exception:
        return None


def plugin_trace(hh=None):
    """True iff the plugin is installed/configured for this Hermes home.

    The plugin keeps its shared state (memo + runtime config) under
    <HERMES_HOME>/workspace-guard/; that directory existing is the
    plugin-presence heuristic that gates the standalone fallback
    (SCR-011 2.4).
    """
    if hh is None:
        hh = hermes_home()
    return os.path.isdir(os.path.join(hh, MEMO_SUBDIR))


def profile_workspace(name, memo=None):
    """Return one profile's workspace value; None when absent/invalid."""
    if memo is None:
        memo = read_memo()
    if not isinstance(memo, dict):
        return None
    entry = memo.get("profiles", {}).get(name)
    if not isinstance(entry, dict):
        return None
    ws = entry.get("workspace")
    return ws if isinstance(ws, str) and ws else None


def list_workspaces(memo=None):
    """All profile workspace values ([] when no memo).

    Includes every non-empty workspace string (sync_memo records None for
    invalid profiles, so this is effectively the valid set).
    """
    if memo is None:
        memo = read_memo()
    if not isinstance(memo, dict):
        return []
    out = []
    for entry in memo.get("profiles", {}).values():
        if not isinstance(entry, dict):
            continue
        ws = entry.get("workspace")
        if isinstance(ws, str) and ws:
            out.append(ws)
    return out


def normalize_path(path):
    """Normalize a path for exact matching (SCR-011 2.2).

    Absolute + normpath + forward slashes; Windows also casefolds
    (case-insensitive filesystem). POSIX keeps exact comparison.
    """
    norm = os.path.abspath(os.path.normpath(path)).replace("\\", "/")
    if os.name == "nt":
        norm = norm.casefold()
    return norm


def add_profile_arg(parser):
    """Add the shared --profile argument to a script's argparse parser.

    When provided, validation matches only that profile's workspace
    (narrow-match); without it, any registered profile workspace matches.
    """
    parser.add_argument(
        "--profile",
        default=None,
        help="Profile name for memo workspace matching (optional; default: any profile).",
    )


def _parse_allowed_root_files_yaml(data):
    """Extract allowed_root_files from a parsed YAML dict."""
    if not isinstance(data, dict):
        return []
    raw = data.get("allowed_root_files")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _parse_allowed_root_files_lines(path):
    """Line-based allowed_root_files parser (PyYAML unavailable).

    Handles the template shape:
        allowed_root_files:
          - <name>
    and the inline shape:  allowed_root_files: ["<name>"]
    """
    result = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("allowed_root_files:"):
            rest = stripped[len("allowed_root_files:"):].strip()
            in_list = True
            if rest:
                # Inline list: ["<name>"] or single quoted/unquoted value
                inner = rest.strip("[]").strip()
                for part in inner.split(","):
                    part = part.strip().strip("'\"")
                    if part:
                        result.append(part)
                in_list = False
            continue
        if in_list:
            if stripped.startswith("- "):
                value = stripped[2:].strip().strip("'\"")
                if value:
                    result.append(value)
            else:
                in_list = False
    return result


def allowed_root_files(hh=None):
    """Root-file whitelist from guard-config.yaml (audit side of D1).

    Reads the `allowed_root_files` key from
    <HERMES_HOME>/workspace-guard/guard-config.yaml. STRICT fallback: when
    the config file or the key is absent, returns an EMPTY list -> every
    root file is flagged (fail-closed, over-report), matching the plugin
    guard's D1 semantics so guard and audit never disagree about root
    files. No rules-file name is hardcoded here.
    """
    if hh is None:
        hh = hermes_home()
    path = os.path.join(hh, MEMO_SUBDIR, CONFIG_FILE)
    try:
        import yaml  # noqa: PLC0415 -- optional dependency, fallback below

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _parse_allowed_root_files_yaml(data)
    except ImportError:
        return _parse_allowed_root_files_lines(path)
    except Exception:
        return []


def validate_workspace(path, hh=None, profile=None):
    """Memo-based workspace validation flow (SCR-011 2.4).

    Assumes the caller already verified the target directory exists (exit
    code mapping for a missing directory differs per script: 1 in
    create_session_dir, 2 in audit_workspace / clean_tmp).

    Returns (True, None) when the target is usable:
      - memo readable and the normalized target exactly matches a profile
        workspace (the `profile` narrow-match when given, any profile
        otherwise); or
      - memo missing/corrupt AND no plugin trace -> standalone fallback
        (trust the provided path; emits ONE concise stderr warning).
    Returns (False, reason) when fail-closed:
      - memo readable but no exact match (message includes the
        registration-missing prompt); or
      - memo missing/corrupt but the plugin is present (prompt to run
        /workspace-guard workspace_update).
    """
    if hh is None:
        hh = hermes_home()
    memo = read_memo(hh)
    if memo is not None:
        target_norm = normalize_path(path)
        if profile:
            candidates = [profile_workspace(profile, memo)] if profile_workspace(profile, memo) else []
        else:
            candidates = list_workspaces(memo)
        for ws in candidates:
            if target_norm == normalize_path(ws):
                return True, None
        return False, MEMO_MISS_MESSAGE
    if plugin_trace(hh):
        return False, PLUGIN_MEMO_MISSING_MESSAGE
    sys.stderr.write(STANDALONE_WARNING)
    return True, None
