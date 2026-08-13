"""Configuration loading, working_dir_root resolution and statistics for
workspace-guard (v0.2.0).

Inverted resolution chain (spec 5.5): guard-config.yaml working_dir_root
override (authoritative) -> current profile's terminal.cwd -> fail-open
(guard disabled). The v0.1.0 memo chain is removed (spec 1.3/B4); the sole
surviving tool is workspace_guard_allow_path (spec 5.7). hermes_home honors
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

logger = logging.getLogger("workspace-guard")

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
    """Parse the allowed_root_files config value (spec 5.6, D1).

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
    """Resolve the Working Directory for the current profile (spec 5.5).

    Inverted 3-step chain (plugin side — deliberately different from the
    script-side 4-step chain in workspace_resolver.py):
    1. guard-config.yaml explicit working_dir_root -> authoritative when set
    2. current profile terminal.cwd: HERMES_HOME/config.yaml for "default",
       else HERMES_HOME/profiles/<name>/config.yaml (ctx.profile_name)
    3. fail-open: WARNING + None (guard disabled)

    The TERMINAL_CWD / HERMES_SESSION_PROFILE env steps are REMOVED on the
    plugin side. Resolution happens ONCE at register() (cached via
    get_cached_config); None -> all guard checks allow.
    """
    # 1. guard-config.yaml explicit value (authoritative when set)
    try:
        cfg = load_guard_config(config_path)
        root = cfg.get("working_dir_root")
        if root:
            logger.info(
                "workspace-guard: working_dir_root resolved from guard-config: %s", root
            )
            return root
    except Exception:
        pass

    # 2. current profile's terminal.cwd (fallback)
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            hermes_home = _get_hermes_home()
            if profile == "default":
                cfg_path = hermes_home / "config.yaml"
            else:
                cfg_path = hermes_home / "profiles" / profile / "config.yaml"
            cwd = parse_terminal_cwd(cfg_path)
            if cwd:
                logger.info(
                    "workspace-guard: working_dir_root resolved from profile-config: %s",
                    cwd,
                )
                return cwd
    except Exception:
        pass

    # 3. Fail-open: guard disabled
    logger.warning("workspace-guard: cannot resolve working_dir_root, guard disabled")
    return None


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
    across sessions in the same process. on_session_start calls this so
    each new session starts without leftover allowlist entries.
    """
    with _runtime_allowlist_lock:
        _runtime_allowlist.clear()


def workspace_guard_allow_path(args, **kwargs):
    """Tool handler: add a path to the runtime allowlist (spec 5.7, 5.11).

    Accepts either the tool-handler contract (args dict + extra kwargs such
    as task_id, per Hermes registry dispatch) or a bare path string (direct
    helper/test callers). Returns a confirmation string. This is the
    plugin's ONLY tool. Wiring into ctx.register_tool happens in guard.py.
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
        _stats_session["profile"] = None
        _stats_session["session_id"] = None
        _stats_session["is_subagent"] = False
        _stats_session["started_at"] = None


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
    """stats.jsonl location: HERMES_HOME/workspace-guard/stats.jsonl."""
    return _get_hermes_home() / "workspace-guard" / STATS_JSONL_NAME


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
            logger.debug("workspace-guard: stats write failed (ignored): %s", exc)


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
    be None (guard disabled). Resolution happens ONCE at register()
    (spec 5.5): backed by plugins.plugin_utils.lazy_singleton when the
    Hermes runtime provides it (spec 5.8), otherwise (hermes_cli absent /
    test venv) a local lock-guarded cache. reset_cache() clears either.
    """
    global _cached_result, _cache_initialized, _register_ctx, _register_config_path
    if _registered_config_accessor is not None:
        if _register_ctx is None:
            # First caller is register(); capture its ctx for the factory.
            _register_ctx = ctx
            _register_config_path = config_path
        return _registered_config_accessor()
    if not _cache_initialized:
        with _cache_lock:
            if not _cache_initialized:
                _cached_result = _resolve_config(ctx, config_path)
                _cache_initialized = True
    return _cached_result


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
    stats_reset()
