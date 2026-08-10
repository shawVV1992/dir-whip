"""Configuration loading and working_dir_root resolution for workspace-guard."""

import datetime
import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path

logger = logging.getLogger("workspace-guard")

SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

# hermes_cli is a Hermes runtime package; absent in the test venv. Guarded
# module-level import so config.py never crashes when it is unavailable
# (fail-open: callers treat a missing hermes_cli as "no profiles to sync").
# set_config_value backs the SCR-011 registration tool's config-first step;
# when hermes_cli is absent the tool rejects (never writes the memo).
try:
    from hermes_cli.profiles import list_profiles
    from hermes_cli.config import read_user_config_raw
    from hermes_cli.config import set_config_value
except Exception:
    list_profiles = None
    read_user_config_raw = None
    set_config_value = None

_cache_lock = threading.Lock()
_cached_result = None
_cache_initialized = False

_runtime_allowlist = set()
_runtime_allowlist_lock = threading.Lock()


def _get_hermes_home():
    """Return the Hermes home directory path."""
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
    return Path.home() / ".hermes"


def _get_plugin_dir():
    """Return the plugin directory (SCR-013: no longer the config source).

    Kept for the plugin's own sibling resources; the runtime config now
    lives at HERMES_HOME/workspace-guard/guard-config.yaml.
    """
    return Path(__file__).parent


def parse_terminal_cwd(config_path):
    """Parse terminal.cwd from a Hermes config.yaml file.

    Returns the cwd string or None if not found/unparseable.
    """
    try:
        import yaml
    except ImportError:
        return _parse_terminal_cwd_fallback(config_path)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            terminal = data.get("terminal", {})
            if isinstance(terminal, dict):
                return terminal.get("cwd")
    except Exception:
        pass
    return None


def _parse_terminal_cwd_fallback(config_path):
    """Minimal YAML parser for terminal.cwd when PyYAML is unavailable."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        in_terminal = False
        for line in lines:
            stripped = line.strip()
            if stripped == "terminal:":
                in_terminal = True
                continue
            if in_terminal:
                if stripped.startswith("cwd:"):
                    value = stripped[4:].strip().strip("'\"")
                    return value if value else None
                if not line.startswith(" ") and not line.startswith("\t"):
                    in_terminal = False
    except Exception:
        pass
    return None


def _parse_terminal_guard_value(value):
    """Parse a terminal_guard config value; default enabled (fail toward
    enforcement: a missing/unreadable value keeps interception on)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("disabled", "false", "0", "off")
    return True


def terminal_guard_enabled(config_path=None):
    """Return True when terminal write interception is enabled (default enabled)."""
    try:
        cfg = load_guard_config(config_path)
        return bool(cfg.get("terminal_guard", True))
    except Exception:
        return True


def _parse_allowed_root_files(value):
    """Parse the allowed_root_files config value (SCR-011, D1).

    Accepts a YAML list; only string elements are kept. STRICT fallback:
    None / non-list / empty -> [] (every root file blocks, fail-closed).
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def load_guard_config(config_path=None):
    """Load guard-config.yaml exemptions and overrides.

    Returns a dict with at least 'exempt_paths' (list), 'terminal_guard'
    (bool, default True), 'allowed_root_files' (list; STRICT fallback []
    when the key is absent) and optionally 'working_dir_root' (str).
    """
    if config_path is None:
        config_path = _get_hermes_home() / "workspace-guard" / "guard-config.yaml"
    config_path = Path(config_path)

    result = {
        "exempt_paths": [],
        "terminal_guard": True,
        "allowed_root_files": [],
    }

    if not config_path.is_file():
        return result

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            result["exempt_paths"] = data.get("exempt_paths") or []
            if data.get("working_dir_root"):
                result["working_dir_root"] = data["working_dir_root"]
            result["terminal_guard"] = _parse_terminal_guard_value(data.get("terminal_guard"))
            result["allowed_root_files"] = _parse_allowed_root_files(data.get("allowed_root_files"))
    except ImportError:
        result = _load_guard_config_fallback(config_path)
    except Exception as exc:
        logger.debug("workspace-guard: failed to load guard-config.yaml: %s", exc)

    return result


def _parse_inline_list(text):
    """Parse an inline YAML list of quoted strings (e.g. '["AGENTS.md"]')."""
    items = []
    for part in re.split(r"[,\[\]]", text):
        part = part.strip().strip("'\"")
        if part:
            items.append(part)
    return items


def _load_guard_config_fallback(config_path):
    """Minimal parser for guard-config.yaml when PyYAML is unavailable."""
    result = {
        "exempt_paths": [],
        "terminal_guard": True,
        "allowed_root_files": [],
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        in_exempt = False
        in_allowed = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("exempt_paths:"):
                in_exempt = True
                in_allowed = False
                rest = stripped[len("exempt_paths:"):].strip()
                if rest and rest != "[]":
                    result["exempt_paths"].append(rest)
                continue
            if stripped.startswith("working_dir_root:"):
                value = stripped[len("working_dir_root:"):].strip().strip("'\"")
                if value:
                    result["working_dir_root"] = value
                in_exempt = False
                in_allowed = False
                continue
            if stripped.startswith("terminal_guard:"):
                value = stripped[len("terminal_guard:"):].strip().strip("'\"")
                result["terminal_guard"] = _parse_terminal_guard_value(value)
                continue
            if stripped.startswith("allowed_root_files:"):
                in_exempt = False
                in_allowed = True
                rest = stripped[len("allowed_root_files:"):].strip()
                if rest and rest != "[]":
                    result["allowed_root_files"].extend(_parse_inline_list(rest))
                continue
            if in_exempt and stripped.startswith("- "):
                value = stripped[2:].strip().strip("'\"")
                if value:
                    result["exempt_paths"].append(value)
                continue
            if in_allowed and stripped.startswith("- "):
                value = stripped[2:].strip().strip("'\"")
                if value:
                    result["allowed_root_files"].append(value)
                continue
            if in_exempt or in_allowed:
                in_exempt = False
                in_allowed = False
    except Exception:
        pass
    return result


def resolve_working_dir_root(ctx, config_path=None):
    """Resolve the Default Working Directory for the current profile.

    Degradation chain:
    1. ctx.profile_name -> profile config terminal.cwd
    2. TERMINAL_CWD environment variable
    3. guard-config.yaml working_dir_root
    4. fail-open (return None -> guard disabled)
    """
    hermes_home = _get_hermes_home()

    # 1. Profile config
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            if profile == "default":
                cfg_path = hermes_home / "config.yaml"
            else:
                cfg_path = hermes_home / "profiles" / profile / "config.yaml"
            cwd = parse_terminal_cwd(cfg_path)
            if cwd:
                logger.info(
                    "workspace-guard: working_dir_root resolved from "
                    "profile-config: %s", cwd
                )
                return cwd
    except Exception:
        pass

    # 2. Environment variable
    env_cwd = os.environ.get("TERMINAL_CWD")
    if env_cwd:
        logger.info(
            "workspace-guard: working_dir_root resolved from env: %s", env_cwd
        )
        return env_cwd

    # 3. Plugin guard-config.yaml
    try:
        cfg = load_guard_config(config_path)
        if cfg.get("working_dir_root"):
            logger.info(
                "workspace-guard: working_dir_root resolved from "
                "guard-config: %s", cfg["working_dir_root"]
            )
            return cfg["working_dir_root"]
    except Exception:
        pass

    # 4. Fail-open
    logger.warning("workspace-guard: cannot resolve working_dir_root, guard disabled")
    return None


def is_inside_session_dir(path, working_dir_root):
    """Check if path is under working_dir_root/<session_dir>/..."""
    try:
        rel = os.path.relpath(path, working_dir_root)
    except ValueError:
        return False
    parts = rel.replace("\\", "/").split("/")
    if parts and SESSION_DIR_RE.match(parts[0]):
        try:
            datetime.datetime.strptime(parts[0][:15].replace("_", ""), "%Y%m%d%H%M%S")
            return True
        except ValueError:
            return False
    return False


def is_exempt(target_path, exempt_paths):
    """Check if target_path matches any exempt path (prefix match, forward slashes)."""
    normalized = target_path.replace("\\", "/")
    for exempt in exempt_paths:
        exempt_normalized = exempt.replace("\\", "/")
        if normalized.startswith(exempt_normalized):
            return True
    return False


# ---------------------------------------------------------------- Profile workspace memo (SCR-001)

MEMO_SUBDIR = "workspace-guard"
MEMO_FILE = "profile-workspaces.json"


def _memo_path(hermes_home):
    """Memo location: <hermes_home>/workspace-guard/profile-workspaces.json."""
    return Path(hermes_home) / MEMO_SUBDIR / MEMO_FILE


def _memo_bak_path(hermes_home):
    """Backup copy of the memo for corruption recovery."""
    return Path(hermes_home) / MEMO_SUBDIR / (MEMO_FILE + ".bak")


def _empty_memo():
    """Fail-open memo shape: no synced_at, no profiles."""
    return {"synced_at": None, "profiles": {}}


def _read_memo_file(path):
    """Read and validate a memo JSON file; None on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        profiles = data.get("profiles")
        if profiles is None:
            data["profiles"] = {}
        elif not isinstance(profiles, dict):
            return None
        return data
    except Exception:
        return None


def load_memo(hermes_home=None):
    """Load the profile-workspaces memo (fail-open).

    Corrupt JSON falls back to the .bak copy; if both are unusable or
    missing, returns an empty memo so every profile is treated as external
    (allow). Schema: {"synced_at": iso, "profiles": {name: {"workspace",
    "status", "changed_at"}}}.
    """
    if hermes_home is None:
        hermes_home = _get_hermes_home()
    memo = _read_memo_file(_memo_path(hermes_home))
    if memo is None:
        memo = _read_memo_file(_memo_bak_path(hermes_home))
    if memo is None:
        logger.warning("workspace-guard: memo unreadable, fail-open empty memo")
        return _empty_memo()
    return memo


def save_memo(memo, hermes_home=None):
    """Persist the memo atomically (tmp file + os.replace), retaining .bak.

    Returns True on success, False on failure (callers fail open).
    """
    if hermes_home is None:
        hermes_home = _get_hermes_home()
    target = _memo_path(hermes_home)
    bak = _memo_bak_path(hermes_home)
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # back up the previous good version only (never a corrupt one)
        if target.is_file() and _read_memo_file(target) is not None:
            shutil.copyfile(target, bak)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(memo, f, indent=2)
        os.replace(tmp, target)
        return True
    except Exception as exc:
        logger.debug("workspace-guard: memo save failed: %s", exc)
        return False


def _now_iso():
    """Local time as an ISO-8601 string (seconds precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _list_profiles():
    """Enumerate (name, home) profiles via hermes_cli; fail-open to empty.

    Normalizes hermes_cli ProfileInfo objects (name + path) to (name, home)
    tuples so consumers can unpack uniformly (live-verification finding
    2026-08-07: the real list_profiles() returns ProfileInfo, not tuples).
    """
    if list_profiles is None:
        return []
    try:
        out = []
        for item in list_profiles():
            if hasattr(item, "name") and hasattr(item, "path"):
                out.append((item.name, str(item.path)))
            else:
                out.append(item)
        return out
    except Exception as exc:
        logger.debug("workspace-guard: list_profiles() failed (fail-open): %s", exc)
        return []


def _profile_workspace(home):
    """Resolve a profile's workspace (terminal.cwd); None on any failure.

    Prefers hermes_cli read_user_config_raw; falls back to the existing
    parse_terminal_cwd when hermes_cli is unavailable.
    """
    try:
        cfg_path = Path(home) / "config.yaml"
        if not cfg_path.is_file():
            return None
        if read_user_config_raw is not None:
            data = read_user_config_raw(cfg_path)
            if not isinstance(data, dict):
                return None
            terminal = data.get("terminal") or {}
            if not isinstance(terminal, dict):
                return None
            cwd = terminal.get("cwd")
            return cwd if isinstance(cwd, str) and cwd else None
        return parse_terminal_cwd(cfg_path)
    except Exception as exc:
        logger.debug("workspace-guard: profile workspace resolution failed: %s", exc)
        return None


def sync_memo(hermes_home=None):
    """Full profile-workspace memo sync (SCR-001 2.4).

    Enumerates profiles, resolves each workspace, marks valid/invalid,
    sets changed_at only when the workspace value changes, drops deleted
    profiles, and persists atomically. Returns the new memo dict.
    """
    if hermes_home is None:
        hermes_home = _get_hermes_home()
    old = load_memo(hermes_home)
    old_profiles = old.get("profiles", {})
    now = _now_iso()
    new_profiles = {}
    for name, home in _list_profiles():
        ws = _profile_workspace(home)
        if ws and os.path.isabs(ws) and os.path.isdir(ws):
            status = "valid"
        else:
            status = "invalid"
            ws = None
        old_entry = old_profiles.get(name) or {}
        if old_entry.get("workspace") == ws and old_entry.get("status") == status:
            changed_at = old_entry.get("changed_at")
        else:
            changed_at = now
        new_profiles[name] = {
            "workspace": ws,
            "status": status,
            "changed_at": changed_at,
        }
    memo = {"synced_at": now, "profiles": new_profiles}
    save_memo(memo, hermes_home)
    return memo


# ---------------------------------------------------------------- Runtime allowlist (SCR-002)

def _normalize_allowlist_path(path):
    """Normalize a path for allowlist comparison (forward slashes)."""
    if path is None:
        return ""
    return str(path).replace("\\", "/")


def runtime_allowlist_add(path):
    """Add a path to the runtime allowlist (process-lifetime).

    Returns a confirmation string for the workspace_guard_allow_path tool.
    """
    normalized = _normalize_allowlist_path(path)
    with _runtime_allowlist_lock:
        _runtime_allowlist.add(normalized)
    logger.debug("workspace-guard: runtime allowlist added: %s", normalized)
    return "[workspace-guard] Added to runtime allowlist: %s" % normalized


def is_runtime_allowlisted(path):
    """Check a path against the runtime allowlist (normalized slashes).

    Prefix match (case-insensitive): allowing a directory also exempts
    operations under it, matching the workspace_guard_allow_path tool intent
    ("file operations under that path are exempt") and exempt_paths semantics.
    Live-verification finding 2026-08-07 (F01 re-test): exact match left
    writes inside an allowed directory blocked.
    """
    normalized = _normalize_allowlist_path(path).casefold()
    with _runtime_allowlist_lock:
        return any(normalized.startswith(e.casefold()) for e in _runtime_allowlist)


def runtime_allowlist_snapshot():
    """Return a copy of the runtime allowlist (debug/testing)."""
    with _runtime_allowlist_lock:
        return set(_runtime_allowlist)


def runtime_allowlist_clear():
    """Clear the runtime allowlist (session-start scope reset).

    The workspace_guard_allow_path tool grants a session-scoped exemption
    ("exempt for this session"); the guard must not keep allowing a path
    across sessions in the same process. _session_start_hook calls this so
    each new session starts without leftover allowlist entries
    (live-verification finding 2026-08-07: E03 granularity test was masked
    by allowlist residue from a prior session).
    """
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()


def workspace_guard_allow_path(args, **kwargs):
    """Tool handler: add a path to the runtime allowlist.

    Accepts either the tool-handler contract (args dict + extra kwargs such
    as task_id, per Hermes registry dispatch) or a bare path string (direct
    helper/test callers). Returns a confirmation string. Wiring into
    ctx.register_tool happens in guard.py (task 10.1 hands off the store +
    handler only).
    """
    path = args.get("path") if isinstance(args, dict) else args
    return runtime_allowlist_add(path)


# ---------------------------------------------------------------- SCR-011: quick command + registration tool

# Schemas for the two SCR-011 tools, registered in guard.py register()
# (OpenAI function-call format required by Hermes tools.registry, SCR-008).
AUTO_UPDATE_TOOL_SCHEMA = {
    "name": "workspace_guard_auto_update_workspace",
    "description": (
        "Rebuild the workspace-guard profile workspace memo from the Hermes "
        "profile configurations (terminal.cwd). Use when profile workspaces "
        "may have changed and need re-validation before writing files."
    ),
    "parameters": {"type": "object", "properties": {}},
}

REGISTER_TOOL_SCHEMA = {
    "name": "workspace_guard_register_workspace",
    "description": (
        "Register a workspace as the Default Working Directory of the ACTIVE "
        "profile (two-step init flow): sets the profile's terminal.cwd in "
        "the Hermes config (durable) and records the workspace in the "
        "profile workspace memo (immediate). Active-profile-only: any "
        "profile other than the current session profile is rejected, and "
        "the workspace directory must already exist (create it with "
        "init_workspace.py first)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "Name of the profile to register (must be the active "
                    "profile)"
                ),
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path of the workspace directory (must exist)"
                ),
            },
        },
        "required": ["profile", "workspace"],
    },
}


def _format_memo(memo):
    """Format the profile workspace memo for display (SCR-011 2.6).

    Output shape (profile names aligned to the widest name):
      [workspace-guard] Profile workspace memo
      Synced: <synced_at or "n/a">
        <profile>: <workspace or "-"> [<status or "unknown">]  changed: <changed_at or "-">
    Graceful degradation: a missing/corrupt memo never raises (n/a / no
    profile lines).
    """
    try:
        synced = memo.get("synced_at") or "n/a"
        profiles = memo.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        lines = [
            "[workspace-guard] Profile workspace memo",
            "Synced: %s" % synced,
        ]
        if profiles:
            name_w = max(len(str(name)) for name in profiles)
            for name in sorted(profiles):
                entry = profiles.get(name)
                if not isinstance(entry, dict):
                    entry = {}
                ws = entry.get("workspace") or "-"
                status = entry.get("status") or "unknown"
                changed = entry.get("changed_at") or "-"
                lines.append(
                    "  %s: %s [%s]  changed: %s"
                    % (str(name).ljust(name_w), ws, status, changed)
                )
        return "\n".join(lines)
    except Exception:
        return "[workspace-guard] Profile workspace memo\nSynced: n/a"


def workspace_guard_auto_update_workspace(args, **kwargs):
    """Tool handler: full memo sync (SCR-011 2.2).

    Uses the same sync_memo() as the /workspace-guard workspace_update
    command. Returns a display string; never raises (errors become the
    message).
    """
    try:
        memo = sync_memo()
        return "[workspace-guard] Memo updated.\n" + _format_memo(memo)
    except Exception as exc:
        return "[workspace-guard] Memo update failed: %s" % exc


def workspace_guard_register_workspace(args, ctx=None, **kwargs):
    """Tool handler: register a profile workspace (SCR-011 2.2/2.6).

    Two-step init flow, ACTIVE-PROFILE-ONLY and CONFIG-FIRST:
    1. Rejects when profile != ctx.profile_name (ctx None -> reject,
       fail-safe).
    2. Requires workspace to be an existing directory.
    3. Config-first: sets the profile's terminal.cwd via Hermes
       set_config_value() (durable step). Unavailable or failing -> the
       WHOLE operation aborts with an error message, no memo write.
    4. Then writes/overwrites the memo entry {profile: {workspace,
       status: "valid", changed_at: now}} (immediacy for scripts).
    5. Because step 3 makes terminal.cwd == the registered workspace,
       sync_memo() derives the same value and can never clobber the
       registration.

    Returns a confirmation/rejection string; never raises (errors become
    the message, so the host process survives set_config_value's
    sys.exit-based failure paths).
    """
    try:
        if isinstance(args, dict):
            profile = args.get("profile")
            workspace = args.get("workspace")
        else:
            profile = args
            workspace = None
        if isinstance(profile, dict):
            profile = profile.get("name") or profile.get("id") or ""
        profile = str(profile or "").strip()
        workspace = str(workspace or "").strip()

        active = getattr(ctx, "profile_name", None) if ctx is not None else None
        if not profile or profile != active:
            return (
                "[workspace-guard] Registration rejected: profile %r is not "
                "the active profile (active: %r). Switch to the target "
                "profile first." % (profile, active)
            )
        if not workspace or not os.path.isdir(workspace):
            return (
                "[workspace-guard] Registration rejected: workspace %r is "
                "not an existing directory." % workspace
            )
        if set_config_value is None:
            return (
                "[workspace-guard] Registration aborted: Hermes "
                "set_config_value is unavailable (hermes_cli not present); "
                "no memo entry was written."
            )
        try:
            set_config_value("terminal.cwd", workspace)
        except BaseException as exc:
            # set_config_value fails via print + sys.exit(1) for managed
            # keys and unparseable configs (SystemExit) or by raising on
            # write errors; neither may kill the plugin host.
            return (
                "[workspace-guard] Registration aborted: failed to set "
                "terminal.cwd (%s); no memo entry was written." % exc
            )

        memo = load_memo()
        if not isinstance(memo, dict):
            memo = {"synced_at": None, "profiles": {}}
        profiles = memo.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            memo["profiles"] = profiles
        profiles[profile] = {
            "workspace": workspace,
            "status": "valid",
            "changed_at": _now_iso(),
        }
        if not save_memo(memo):
            return (
                "[workspace-guard] Registration aborted: memo write failed; "
                "terminal.cwd was set but the memo was not updated."
            )
        return (
            "[workspace-guard] Registered profile %r workspace: %s"
            % (profile, workspace)
        )
    except Exception as exc:
        return "[workspace-guard] Registration failed: %s" % exc


def _resolve_config(ctx, config_path=None):
    """Resolve working_dir_root + exempt_paths."""
    working_dir_root = resolve_working_dir_root(ctx, config_path)
    cfg = load_guard_config(config_path)
    exempt_paths = cfg.get("exempt_paths", [])
    return (working_dir_root, exempt_paths)


def get_cached_config(ctx, config_path=None):
    """Get or create cached configuration (thread-safe singleton).

    Returns (working_dir_root, exempt_paths) tuple.
    working_dir_root may be None (guard disabled).
    """
    global _cached_result, _cache_initialized
    if not _cache_initialized:
        with _cache_lock:
            if not _cache_initialized:
                _cached_result = _resolve_config(ctx, config_path)
                _cache_initialized = True
    return _cached_result


def reset_cache():
    """Reset the config cache and runtime allowlist (for testing)."""
    global _cached_result, _cache_initialized
    with _cache_lock:
        _cached_result = None
        _cache_initialized = False
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()
