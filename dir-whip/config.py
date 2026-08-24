"""Configuration loading, working_dir_root resolution and statistics for
dir-whip (v0.3.1).

Inverted resolution chain (spec 5.5): dir-whip-config.yaml working_dir_root
override (authoritative) -> current profile's terminal.cwd -> fail-open
(guard disabled). The v0.1.0 memo chain is removed (spec 1.3/B4); the sole
surviving tool is dir_whip_allow_path (spec 5.7). hermes_home honors
the HERMES_HOME env override before the platform default (D5).
"""

import datetime
import json
import logging
import os
import re
import threading
from pathlib import Path

import yaml

logger = logging.getLogger("dir-whip")

SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

# plugins.plugin_utils is a Hermes runtime package; absent in the test venv.
# Guarded module-level import so config.py never crashes when unavailable
# (fail-open: get_cached_config degrades to a local lock-guarded cache).
try:
    from plugins.plugin_utils import lazy_singleton
except Exception:
    lazy_singleton = None

try:
    from . import state, stats
except ImportError:
    import state
    import stats

try:
    from .paths import (
        _get_hermes_home,
        _paths_equal,
        _profile_home,
        relativize_target,
    )
except ImportError:
    from paths import (
        _get_hermes_home,
        _paths_equal,
        _profile_home,
        relativize_target,
    )

_cache_lock = threading.Lock()
_cached_result = None
_cache_initialized = False

_runtime_allowlist = set()
_runtime_allowlist_lock = threading.Lock()

# SCR-027 session-scoped resolution: a desktop process registers under the
# ACTIVE profile but later sessions can be a DIFFERENT profile, so the
# working_dir_root is re-resolved per top-level session at on_session_start
# (single-threaded session loop assumption, same as stats). The session
# root starts as the register-time value and is REPLACED by
# refresh_resolution (including None on fail-open — a stale value is never
# kept). All of this lives in state.session (see state.py).


def parse_terminal_cwd(config_path):
    """Parse terminal.cwd from a Hermes config.yaml file.

    Returns the cwd string or None if not found/unparseable.
    """
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


def _parse_terminal_guard_value(value):
    """Parse a terminal_guard config value; default enabled (fail toward
    enforcement: a missing/unreadable value keeps interception on)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("disabled", "false", "0", "off")
    # PyYAML parses the YAML 1.1 integer forms 0/1 as int; treat numeric 0
    # as disabled per the switch semantics (spec 5.6).
    if isinstance(value, (int, float)):
        return value != 0
    return True


def terminal_guard_enabled(config_path=None):
    """Return True when terminal write interception is enabled (default enabled)."""
    try:
        cfg = load_guard_config(config_path)
        return bool(cfg.get("terminal_guard", True))
    except Exception:
        return True


def _parse_write_audit_value(value):
    """Parse a write_audit config value (spec 5.18); default enabled (a
    missing/unreadable value keeps the audit on)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("disabled", "false", "0", "off")
    # PyYAML parses the YAML 1.1 integer forms 0/1 as int; treat numeric 0
    # as disabled per the switch semantics (spec 5.18).
    if isinstance(value, (int, float)):
        return value != 0
    return True


def _parse_entry_cap_value(value):
    """Parse a write_audit_entry_cap config value; default 2000 on
    missing/unparseable/non-positive input (a bad value must not weaken
    the guardrail; spec 5.18)."""
    if isinstance(value, bool):
        return 2000
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 2000
    return parsed if parsed > 0 else 2000


def write_audit_enabled(config_path=None):
    """True when the root write audit is enabled (spec 5.18; default on)."""
    try:
        cfg = load_guard_config(config_path)
        return bool(cfg.get("write_audit", True))
    except Exception:
        return True


def write_audit_entry_cap(config_path=None):
    """The root-entry cap for the write audit (spec 5.18; default 2000)."""
    try:
        cfg = load_guard_config(config_path)
        return _parse_entry_cap_value(cfg.get("write_audit_entry_cap", 2000))
    except Exception:
        return 2000


def _parse_allowed_root_files(value):
    """Parse the allowed_root_files config value (spec 5.6, D1).

    Accepts a YAML list; only string elements are kept. STRICT fallback:
    None / non-list / empty -> [] (every root file blocks, fail-closed).
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def load_guard_config(config_path=None):
    """Load dir-whip-config.yaml exemptions and overrides.

    Returns a dict with at least 'exempt_paths' (list), 'terminal_guard'
    (bool, default True), 'allowed_root_files' (list; STRICT fallback []
    when the key is absent), 'write_audit' (bool, default True),
    'write_audit_entry_cap' (int, default 2000) and optionally
    'working_dir_root' (str).
    """
    if config_path is None:
        config_path = _get_hermes_home() / "dir-whip" / "dir-whip-config.yaml"
    config_path = Path(config_path)

    result = {
        "exempt_paths": [],
        "terminal_guard": True,
        "allowed_root_files": [],
        "write_audit": True,
        "write_audit_entry_cap": 2000,
    }

    if not config_path.is_file():
        return result

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            result["exempt_paths"] = data.get("exempt_paths") or []
            if data.get("working_dir_root"):
                result["working_dir_root"] = data["working_dir_root"]
            result["terminal_guard"] = _parse_terminal_guard_value(data.get("terminal_guard"))
            result["allowed_root_files"] = _parse_allowed_root_files(data.get("allowed_root_files"))
            result["write_audit"] = _parse_write_audit_value(data.get("write_audit"))
            result["write_audit_entry_cap"] = _parse_entry_cap_value(
                data.get("write_audit_entry_cap", 2000)
            )
    except Exception as exc:
        logger.debug("dir-whip: failed to load dir-whip-config.yaml: %s", exc)

    return result


def resolve_working_dir_root(ctx, config_path=None):
    """Resolve the Working Directory for the current profile (spec 5.5).

    Inverted 3-step chain (plugin side — deliberately different from the
    script-side 4-step chain in workspace_resolver.py):
    1. dir-whip-config.yaml explicit working_dir_root -> authoritative when set
    2. current profile terminal.cwd: HERMES_HOME/config.yaml for "default",
       else HERMES_HOME/profiles/<name>/config.yaml (ctx.profile_name)
    3. fail-open: WARNING + None (guard disabled)

    The TERMINAL_CWD / HERMES_SESSION_PROFILE env steps are REMOVED on the
    plugin side. Resolution happens ONCE at register() (cached via
    get_cached_config); None -> all guard checks allow.
    """
    # 1. dir-whip-config.yaml explicit value (authoritative when set)
    try:
        cfg = load_guard_config(config_path)
        root = cfg.get("working_dir_root")
        if root:
            logger.info(
                "dir-whip: working_dir_root resolved from dir-whip-config: %s", root
            )
            return root
    except Exception:
        pass

    # 2. current profile's terminal.cwd (fallback)
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            hermes_home = _get_hermes_home()
            cfg_path = _profile_config_path(hermes_home, profile)
            cwd = parse_terminal_cwd(cfg_path)
            if cwd:
                logger.info(
                    "dir-whip: working_dir_root resolved from profile-config: %s",
                    cwd,
                )
                return cwd
    except Exception:
        pass

    # 3. Fail-open: guard disabled
    logger.warning("dir-whip: cannot resolve working_dir_root, guard disabled")
    return None


def refresh_resolution(ctx):
    """Re-run the resolution chain for the SESSION's profile (SCR-027).

    Called at every top-level on_session_start: the same 3-step chain runs
    against the session's ctx.profile_name, so a desktop multi-profile
    process never keeps the register-time (other-profile) root. On success
    state.session.session_root = <root> (same INFO source log as the
    chain); on fail-open session_root = None + WARNING — a stale value
    from a previous session is NEVER kept. Returns the session root.
    """
    state.session.session_root = resolve_working_dir_root(ctx)
    state.session.session_root_initialized = True
    return state.session.session_root


def get_session_root():
    """The session-scoped working_dir_root (None = guard disabled)."""
    return state.session.session_root


def _effective_root(ctx):
    """The session root, resolving lazily before any on_session_start ran.

    Consumers (the /dir-whip report) read the session value; before the
    first on_start the register-time resolution is the initial value, so a
    lazy refresh here keeps them correct in tests and pre-session contexts.
    """
    if not state.session.session_root_initialized:
        refresh_resolution(ctx)
    return state.session.session_root


def _profile_config_path(hermes_home, profile):
    """Path to a profile's config.yaml, aware of both home layouts (SCR-026/027).

    At runtime Hermes sets HERMES_HOME to the PROFILE DIRECTORY itself for
    non-default profiles (e.g. HERMES_HOME=.../profiles/learn), while tests
    and some hosts keep HERMES_HOME at the root with named profiles under
    .../profiles/<name>/. Detect the layout by path shape: when hermes_home
    already IS the profile dir (name == profile, parent == "profiles"), the
    profile config is hermes_home/config.yaml. The reverse case (R2): a
    "default" session while hermes_home is a NAMED profile's dir -> the
    default home is TWO levels up (hermes_home.parent.parent/config.yaml).
    """
    hermes_home = Path(hermes_home)
    if not profile or profile == "default":
        if hermes_home.parent.name == "profiles":
            return hermes_home.parent.parent / "config.yaml"
        return hermes_home / "config.yaml"
    if hermes_home.name == profile and hermes_home.parent.name == "profiles":
        return hermes_home / "config.yaml"
    return hermes_home / "profiles" / profile / "config.yaml"


def _profile_terminal_cwd(ctx):
    """The current profile's terminal.cwd (None when unset/unparseable)."""
    try:
        profile = getattr(ctx, "profile_name", None)
        if not profile:
            return None
        hermes_home = _get_hermes_home()
        cfg_path = _profile_config_path(hermes_home, profile)
        return parse_terminal_cwd(cfg_path)
    except Exception:
        return None


def set_session_profile(profile):
    """Record the session's profile (SCR-027 stats placement).

    stats.jsonl for the session is written into THIS profile's home (via
    _profile_home), not the register-time active profile's.
    """
    state.session.session_profile = profile


def is_inside_session_dir(path, working_dir_root):
    """Check if path is under working_dir_root/<session_dir>/... (spec 5.9)."""
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
    """Check if target_path matches any exempt path (spec 5.6).

    Prefix match on forward-slash-normalized paths; case-insensitive on
    Windows via casefold (SCR-006).
    """
    normalized = str(target_path).replace("\\", "/")
    if os.name == "nt":
        normalized = normalized.casefold()
    for exempt in exempt_paths:
        exempt_normalized = str(exempt).replace("\\", "/")
        if os.name == "nt":
            exempt_normalized = exempt_normalized.casefold()
        if normalized.startswith(exempt_normalized):
            return True
    return False


# ---------------------------------------------------------------- Runtime allowlist (spec 5.11)

def _normalize_allowlist_path(path):
    """Normalize a path for allowlist comparison (forward slashes)."""
    if path is None:
        return ""
    return str(path).replace("\\", "/")


def runtime_allowlist_add(path):
    """Add a path to the runtime allowlist (process-lifetime).

    Returns a confirmation string for the dir_whip_allow_path tool.
    """
    normalized = _normalize_allowlist_path(path)
    with _runtime_allowlist_lock:
        _runtime_allowlist.add(normalized)
    logger.debug("dir-whip: runtime allowlist added: %s", normalized)
    return "[dir-whip] Added to runtime allowlist: %s" % normalized


def is_runtime_allowlisted(path):
    """Check a path against the runtime allowlist (normalized slashes).

    Prefix match (case-insensitive): allowing a directory also exempts
    operations under it, matching the dir_whip_allow_path tool intent
    ("file operations under that path are exempt") and exempt_paths semantics.
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

    The dir_whip_allow_path tool grants a session-scoped exemption
    ("exempt for this session"); the guard must not keep allowing a path
    across sessions in the same process. on_session_start calls this so
    each new session starts without leftover allowlist entries.
    """
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()


def dir_whip_allow_path(args, **kwargs):
    """Tool handler: add a path to the runtime allowlist (spec 5.7, 5.11).

    Accepts either the tool-handler contract (args dict + extra kwargs such
    as task_id, per Hermes registry dispatch) or a bare path string (direct
    helper/test callers). Returns a confirmation string. This is the
    plugin's ONLY tool. Wiring into ctx.register_tool happens in __init__.py
    (register).
    """
    path = args.get("path") if isinstance(args, dict) else args
    return runtime_allowlist_add(path)


# ---------------------------------------------------------------- Config cache (spec 5.5/5.8)

def _resolve_config(ctx, config_path=None):
    """Resolve working_dir_root + exempt_paths."""
    working_dir_root = resolve_working_dir_root(ctx, config_path)
    cfg = load_guard_config(config_path)
    exempt_paths = cfg.get("exempt_paths", [])
    return (working_dir_root, exempt_paths)


def _resolve_registered_config():
    """Zero-arg factory for lazy_singleton (register-time resolution)."""
    return _resolve_config(
        state.session.registered_ctx, state.session.register_config_path
    )


if lazy_singleton is not None:
    _registered_config_accessor = lazy_singleton(_resolve_registered_config)
else:
    _registered_config_accessor = None


def get_cached_config(ctx, config_path=None):
    """Get or create cached configuration (thread-safe singleton).

    Returns (working_dir_root, exempt_paths) tuple. working_dir_root may
    be None (guard disabled). The root slot is SESSION-SCOPED (SCR-027):
    the first resolution (register-time) seeds the session root, and every
    top-level on_session_start refreshes it via refresh_resolution(ctx);
    consumers therefore read the session root, never a stale
    register-time value. Backed by plugins.plugin_utils.lazy_singleton
    when the Hermes runtime provides it (spec 5.8), otherwise a local
    lock-guarded cache. reset_cache() clears either.
    """
    global _cached_result, _cache_initialized
    if _registered_config_accessor is not None:
        if state.session.registered_ctx is None:
            # First caller is register(); capture its ctx for the factory.
            state.session.registered_ctx = ctx
            state.session.register_config_path = config_path
        result = _registered_config_accessor()
    else:
        if not _cache_initialized:
            with _cache_lock:
                if not _cache_initialized:
                    _cached_result = _resolve_config(ctx, config_path)
                    _cache_initialized = True
        result = _cached_result
    if not state.session.session_root_initialized:
        # Initial value of the session root = the register-time resolution.
        state.session.session_root = result[0]
        state.session.session_root_initialized = True
    return (get_session_root(), result[1])


def reset_cache():
    """Reset config cache, stats and runtime allowlist (register/re-register)."""
    global _cached_result, _cache_initialized
    with _cache_lock:
        _cached_result = None
        _cache_initialized = False
    if _registered_config_accessor is not None:
        _registered_config_accessor.reset()
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()
    # Session-scoped state: re-seeded at register (get_cached_config) and at
    # the next top-level on_session_start.
    state.session.session_root = None
    state.session.session_root_initialized = False
    state.session.session_profile = None
    stats.reset()


