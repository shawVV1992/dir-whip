"""Configuration loading, working_dir_root resolution and statistics for
dir-whip (v0.3.0).

Inverted resolution chain (spec 5.5): dir-whip-config.yaml working_dir_root
override (authoritative) -> current profile's terminal.cwd -> fail-open
(guard disabled). The v0.1.0 memo chain is removed (spec 1.3/B4); the sole
surviving tool is dir_whip_allow_path (spec 5.7). hermes_home honors
the HERMES_HOME env override before the platform default (D5).
"""

import copy
import datetime
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger("dir-whip")

SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

# plugins.plugin_utils is a Hermes runtime package; absent in the test venv.
# Guarded module-level import so config.py never crashes when unavailable
# (fail-open: get_cached_config degrades to a local lock-guarded cache).
try:
    from plugins.plugin_utils import lazy_singleton
except Exception:
    lazy_singleton = None

_cache_lock = threading.Lock()
_cached_result = None
_cache_initialized = False

_runtime_allowlist = set()
_runtime_allowlist_lock = threading.Lock()

# Register-time resolution context (spec 5.5: resolved ONCE at register()).
_register_ctx = None
_register_config_path = None

# SCR-027 session-scoped resolution: a desktop process registers under the
# ACTIVE profile but later sessions can be a DIFFERENT profile, so the
# working_dir_root is re-resolved per top-level session at on_session_start
# (single-threaded session loop assumption, same as stats). _session_root
# starts as the register-time value and is REPLACED by refresh_resolution
# (including None on fail-open — a stale value is never kept).
_session_root = None
_session_root_initialized = False

# The session's profile (set at on_session_start); stats.jsonl is written
# into that profile's home so a default-profile session never lands in the
# active profile's home (SCR-027).
_session_profile = None


def _get_hermes_home():
    """Return the Hermes home directory path (D5).

    HERMES_HOME environment override FIRST, then the platform default:
    Windows LOCALAPPDATA/hermes, POSIX ~/.hermes.
    """
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
    return Path.home() / ".hermes"


def _get_plugin_dir():
    """Return the plugin directory (SCR-013: no longer the config source).

    Kept for the plugin's own sibling resources; the runtime config now
    lives at HERMES_HOME/dir-whip/dir-whip-config.yaml.
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


def _parse_write_audit_value(value):
    """Parse a write_audit config value (spec 5.18); default enabled (a
    missing/unreadable value keeps the audit on)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("disabled", "false", "0", "off")
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
        import yaml
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
    except ImportError:
        result = _load_guard_config_fallback(config_path)
    except Exception as exc:
        logger.debug("dir-whip: failed to load dir-whip-config.yaml: %s", exc)

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
    """Minimal parser for dir-whip-config.yaml when PyYAML is unavailable."""
    result = {
        "exempt_paths": [],
        "terminal_guard": True,
        "allowed_root_files": [],
        "write_audit": True,
        "write_audit_entry_cap": 2000,
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
            if stripped.startswith("write_audit:"):
                value = stripped[len("write_audit:"):].strip().strip("'\"")
                result["write_audit"] = _parse_write_audit_value(value)
                continue
            if stripped.startswith("write_audit_entry_cap:"):
                value = stripped[len("write_audit_entry_cap:"):].strip().strip("'\"")
                result["write_audit_entry_cap"] = _parse_entry_cap_value(value)
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
    _session_root = <root> (same INFO source log as the chain); on
    fail-open _session_root = None + WARNING — a stale value from a
    previous session is NEVER kept. Returns _session_root.
    """
    global _session_root, _session_root_initialized
    _session_root = resolve_working_dir_root(ctx)
    _session_root_initialized = True
    return _session_root


def get_session_root():
    """The session-scoped working_dir_root (None = guard disabled)."""
    return _session_root


def _effective_root(ctx):
    """The session root, resolving lazily before any on_session_start ran.

    Consumers (the /dir-whip report) read the session value; before the
    first on_start the register-time resolution is the initial value, so a
    lazy refresh here keeps them correct in tests and pre-session contexts.
    """
    if not _session_root_initialized:
        refresh_resolution(ctx)
    return _session_root


def set_session_profile(profile):
    """Record the session's profile (SCR-027 stats placement).

    stats.jsonl for the session is written into THIS profile's home (via
    _profile_home), not the register-time active profile's.
    """
    global _session_profile
    _session_profile = profile


def _profile_home(hermes_home, profile):
    """The profile's home directory, aware of both layouts (SCR-026/027).

    profile default: home-shaped (parent named "profiles", i.e. HERMES_HOME
    IS a named profile's dir) -> hermes_home.parent.parent (the default
    home is two levels up); otherwise hermes_home. profile named: home IS
    the profile dir -> hermes_home; otherwise hermes_home/profiles/<name>.
    """
    hermes_home = Path(hermes_home)
    if not profile or profile == "default":
        if hermes_home.parent.name == "profiles":
            return hermes_home.parent.parent
        return hermes_home
    if hermes_home.name == profile and hermes_home.parent.name == "profiles":
        return hermes_home
    return hermes_home / "profiles" / profile


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
    plugin's ONLY tool. Wiring into ctx.register_tool happens in dir_whip.py.
    """
    path = args.get("path") if isinstance(args, dict) else args
    return runtime_allowlist_add(path)


# ---------------------------------------------------------------- Statistics (spec 5.13)

STATS_ROLLOVER_BYTES = 5 * 1024 * 1024
STATS_JSONL_NAME = "stats.jsonl"
STATS_ARCHIVE_NAME = "stats.jsonl.1"

_stats_lock = threading.Lock()
# outcome x tool x rule_key x is_subagent -> count
_stats_counters = {}
_stats_session = {
    "profile": None,
    "session_id": None,
    "is_subagent": False,
    "started_at": None,
}


def stats_reset():
    """Clear in-memory stats (counters + session context).

    Called at register/re-register so no counters or session fields leak
    into the next session (5.13 D2).
    """
    with _stats_lock:
        _stats_counters.clear()
        _reset_stats_session_locked()


def _reset_stats_session_locked():
    """Reset the stats session fields; callers must hold _stats_lock."""
    _stats_session["profile"] = None
    _stats_session["session_id"] = None
    _stats_session["is_subagent"] = False
    _stats_session["started_at"] = None


def stats_end_session():
    """Close the stats session context (counters kept).

    Clears the session fields (profile / session_id / is_subagent /
    started_at) so a closed child session's context never leaks into
    later events; in-memory counters are untouched (5.13 D2/D3).
    """
    with _stats_lock:
        _reset_stats_session_locked()


def stats_set_session(profile=None, session_id=None, is_subagent=None, started_at=None):
    """Attach session context to persisted stats events (5.13 session fields).

    Only the provided fields are updated (None leaves a field unchanged);
    the full reset is stats_reset().
    """
    with _stats_lock:
        if profile is not None:
            _stats_session["profile"] = str(profile)
        if session_id is not None:
            _stats_session["session_id"] = str(session_id)
        if is_subagent is not None:
            _stats_session["is_subagent"] = bool(is_subagent)
        if started_at is not None:
            _stats_session["started_at"] = str(started_at)


def stats_snapshot():
    """Return a deep copy of the counters (outcome x tool x rule_key x is_subagent)."""
    with _stats_lock:
        return copy.deepcopy(_stats_counters)


def _now_iso():
    """Local time as an ISO-8601 string (seconds precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _hash_prefix(value):
    """Deterministic privacy-preserving prefix for external paths (5.13)."""
    return "h:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _relativize_target(target, working_dir_root):
    """Privacy: target relative to working_dir_root; external -> hash prefix.

    None target stays None (omitted). External paths (outside the root,
    different drive, or unrelatable) become a 'h:<sha256-prefix>' hash so
    no absolute external path ever lands in stats.jsonl (5.13 privacy).
    """
    if target is None:
        return None
    target = str(target)
    if working_dir_root is None:
        return _hash_prefix(target)
    try:
        rel = os.path.relpath(target, str(working_dir_root))
    except ValueError:  # different drive on Windows -> cannot relate
        return _hash_prefix(target)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(".." + os.sep):
        return _hash_prefix(target)
    return rel.replace("\\", "/")


def _stats_jsonl_path():
    """stats.jsonl location: the session profile's home dir-whip dir.

    SCR-027: the path follows the SESSION profile (set at on_session_start),
    so a default-profile session's events land in the ROOT home's
    dir-whip dir, not the register-time active profile's. When no
    session profile is set yet, use HERMES_HOME directly (register-time
    behavior).
    """
    home = _get_hermes_home()
    if _session_profile:
        home = _profile_home(home, _session_profile)
    return home / "dir-whip" / STATS_JSONL_NAME


def _append_stats_event(event):
    """Append one JSON line to stats.jsonl (O_APPEND, rollover at 5MB).

    Single-process assumption: appends are atomic via os.open O_APPEND; the
    rollover rename tolerates a missing source (another process already
    rolled). Raises on failure; callers swallow and log (fail-open).
    """
    path = _stats_jsonl_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # surfaced by the os.open failure below
    try:
        if path.is_file() and path.stat().st_size > STATS_ROLLOVER_BYTES:
            try:
                os.replace(path, path.with_name(STATS_ARCHIVE_NAME))
            except FileNotFoundError:
                pass  # another process already rolled
    except Exception:
        pass  # rollover is best-effort; the append below still runs
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.write(fd, (json.dumps(event) + "\n").encode("utf-8"))
    finally:
        if fd is not None:
            os.close(fd)


def stats_record(outcome, tool, rule_key, target=None, reason=None,
                 is_subagent=None, working_dir_root=None):
    """Record one guard verdict: bump counters + append one stats.jsonl line.

    outcome x tool x rule_key counters are split by is_subagent (5.13 D2);
    each event persists session + event fields (D3). Never raises: a failed
    stats write is logged and does NOT affect the verdict (5.8 fail-open
    logging).
    """
    if is_subagent is None:
        is_subagent = _stats_session.get("is_subagent", False)
    is_subagent = bool(is_subagent)
    with _stats_lock:
        by_outcome = _stats_counters.setdefault(outcome, {})
        by_tool = by_outcome.setdefault(tool, {})
        by_rule = by_tool.setdefault(rule_key, {})
        by_rule[is_subagent] = by_rule.get(is_subagent, 0) + 1
        try:
            _append_stats_event({
                "profile": _stats_session.get("profile"),
                "session_id": _stats_session.get("session_id"),
                "is_subagent": is_subagent,
                "started_at": _stats_session.get("started_at"),
                "ts": _now_iso(),
                "outcome": outcome,
                "reason": reason,
                "tool": tool,
                "rule_key": rule_key,
                "target": _relativize_target(target, working_dir_root),
            })
        except Exception as exc:
            logger.debug("dir-whip: stats write failed (ignored): %s", exc)


# ---------------------------------------------------------------- Config cache (spec 5.5/5.8)

def _resolve_config(ctx, config_path=None):
    """Resolve working_dir_root + exempt_paths."""
    working_dir_root = resolve_working_dir_root(ctx, config_path)
    cfg = load_guard_config(config_path)
    exempt_paths = cfg.get("exempt_paths", [])
    return (working_dir_root, exempt_paths)


def _resolve_registered_config():
    """Zero-arg factory for lazy_singleton (register-time resolution)."""
    return _resolve_config(_register_ctx, _register_config_path)


if lazy_singleton is not None:
    _registered_config_accessor = lazy_singleton(_resolve_registered_config)
else:
    _registered_config_accessor = None


def get_cached_config(ctx, config_path=None):
    """Get or create cached configuration (thread-safe singleton).

    Returns (working_dir_root, exempt_paths) tuple. working_dir_root may
    be None (guard disabled). The root slot is SESSION-SCOPED (SCR-027):
    the first resolution (register-time) seeds _session_root, and every
    top-level on_session_start refreshes it via refresh_resolution(ctx);
    consumers therefore read the session root, never a stale
    register-time value. Backed by plugins.plugin_utils.lazy_singleton
    when the Hermes runtime provides it (spec 5.8), otherwise a local
    lock-guarded cache. reset_cache() clears either.
    """
    global _cached_result, _cache_initialized, _register_ctx, _register_config_path
    global _session_root, _session_root_initialized
    if _registered_config_accessor is not None:
        if _register_ctx is None:
            # First caller is register(); capture its ctx for the factory.
            _register_ctx = ctx
            _register_config_path = config_path
        result = _registered_config_accessor()
    else:
        if not _cache_initialized:
            with _cache_lock:
                if not _cache_initialized:
                    _cached_result = _resolve_config(ctx, config_path)
                    _cache_initialized = True
        result = _cached_result
    if not _session_root_initialized:
        # Initial value of the session root = the register-time resolution.
        _session_root = result[0]
        _session_root_initialized = True
    return (get_session_root(), result[1])


def reset_cache():
    """Reset config cache, stats and runtime allowlist (register/re-register)."""
    global _cached_result, _cache_initialized, _session_root, _session_root_initialized
    global _session_profile
    with _cache_lock:
        _cached_result = None
        _cache_initialized = False
    if _registered_config_accessor is not None:
        _registered_config_accessor.reset()
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()
    # Session-scoped state: re-seeded at register (get_cached_config) and at
    # the next top-level on_session_start.
    _session_root = None
    _session_root_initialized = False
    _session_profile = None
    stats_reset()


# ---------------------------------------------------------------- Commands & diagnostics (spec 5.7)

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


def _paths_equal(a, b):
    """Forward-slash path equality; case-insensitive on Windows."""
    a = str(a).replace("\\", "/")
    b = str(b).replace("\\", "/")
    if os.name == "nt":
        return a.casefold() == b.casefold()
    return a == b


def _guard_config_key_present(key):
    """True when the key appears in dir-whip-config.yaml (raw line scan)."""
    try:
        path = _get_hermes_home() / "dir-whip" / "dir-whip-config.yaml"
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
    never raises.
    """
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
    """Render the merged /dir-whip report (SCR-029 Plan A; spec 5.7).

    Fixed field order: version, State, Working Directory + source,
    Terminal Guard, Exempt Paths, Root Allowlist, Health (+ one
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

        # Line 5: exempt paths.
        exempts = cfg.get("exempt_paths", [])
        lines.append(
            "Exempt Paths: %s" % (", ".join(exempts) if exempts else "(none)")
        )

        # Line 6: root allowlist (allowed_root_files). Missing key =
        # fail-closed hint (doctor semantics); present-but-empty keeps the
        # status "(none)" semantics; otherwise comma-joined.
        allowed = cfg.get("allowed_root_files", [])
        if not _guard_config_key_present("allowed_root_files"):
            lines.append("Root Allowlist: (strict empty allowlist)")
        elif allowed:
            lines.append("Root Allowlist: %s" % ", ".join(allowed))
        else:
            lines.append("Root Allowlist: (none)")

        # Line 7: health (one line per problem when PROBLEM).
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

        # Line 8 (anomaly only): Q6 footgun — explicit override differs
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


def _dir_whip_cmd(raw_args):
    """/dir-whip dispatcher (spec 5.7, SCR-029): no subcommands.

    The host invokes the handler as fn(raw_args) with everything after the
    first token. Bare /dir-whip renders the merged report; ANY argument
    renders exactly one Usage line (the status/stats/doctor subcommands
    are removed). Never raises (errors become the message).
    """
    try:
        if (raw_args or "").strip():
            return "Usage: /dir-whip"
        return _dir_whip_report()
    except Exception as exc:
        return "[dir-whip] command failed: %s" % exc


def register_dir_whip_commands(ctx):
    """Register the /dir-whip slash command (spec 5.7).

    Exactly ONE command named "dir-whip": Hermes dispatches slash commands
    on the FIRST token only (cli.py: base_cmd = split()[0]), so every
    argument reaches the same handler, which renders the one-line Usage
    (SCR-029: status/stats/doctor subcommands removed). Guarded: a ctx
    without register_command still registers. allow_path is a TOOL and is
    NOT registered here (dir_whip.py registers it).
    """
    global _cmd_ctx
    _cmd_ctx = ctx
    if not hasattr(ctx, "register_command"):
        return
    try:
        ctx.register_command(
            "dir-whip", _dir_whip_cmd,
            description="dir-whip: Working Directory guard report",
            args_hint="",
        )
    except Exception as exc:
        logger.warning("dir-whip: register_command failed: %s", exc)
