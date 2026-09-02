"""Configuration loading, working_dir_root resolution and statistics for
dir-whip (v0.4.0, spec v2.6 B2).

Inverted resolution chain (spec 5.5): dir-whip-config.yaml working_dir_root
override (authoritative) -> current profile's terminal.cwd -> fail-open
(guard disabled). The v0.1.0 memo chain is removed (spec 1.3/B4); the sole
surviving tool is dir_whip_allow_path (spec 5.7). hermes_home honors
the HERMES_HOME env override before the platform default (D5).

Spec v2.6 B2: single unified key allowlist: [] with discriminated
file:<basename> | prefix:<abs-path> (old keys exempt_paths / allowed_root_files
deleted, no backward compat, strict empty fallback).
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
except ImportError:
    lazy_singleton = None

from . import state, stats

from .paths import (
    _get_hermes_home,
    _paths_equal,
    _profile_home,
    normalize_target,
    relativize_target,
    within_working_dir,
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


def _parse_allowlist(value):
    """RAW passthrough of the allowlist config value (spec 5.6 v2.7 R9).

    Parsing/validation moved to allowlist.parse_allowlist at the
    consumption points (verdict/audit/report). Keeping the RAW value
    (structured mapping dict, legacy flat list, or []) preserves the
    loaded-value contract while legacy flat lists stay visible for the
    clean-break hint. Non-list/dict scalars -> [].
    """
    if isinstance(value, (list, dict)):
        return value
    return []


def _get_guard_config_path():
    """Profile-aware dir-whip-config.yaml location (stats._stats_jsonl_path pattern).

    HERMES_HOME may be a profile dir (parent == "profiles") -> use it directly;
    otherwise when a session profile is set, resolve via _profile_home so
    per-profile configs land in profiles/<name>/dir-whip/ (SCR-037 D3).
    Falls back to HERMES_HOME/dir-whip/... for default tests.
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
            ctx = getattr(state.session, "registered_ctx", None)
            if ctx is not None and getattr(ctx, "profile_name", None):
                profile = ctx.profile_name
        except Exception:
            pass
    if profile:
        try:
            home = _profile_home(home, profile)
        except Exception:
            pass
    return Path(home) / "dir-whip" / "dir-whip-config.yaml"


def load_guard_config(config_path=None):
    """Load dir-whip-config.yaml exemptions and overrides.

    Returns a dict with at least 'allowlist' (RAW value: structured
    mapping dict, legacy flat list, or STRICT fallback [] when the key
    is absent or not a list/dict) and optionally 'working_dir_root'
    (str). v2.8 BREAKING (R7 three-key de-configuration): terminal_guard
    / write_audit / write_audit_entry_cap (and the reserved
    write_audit_autofix) are NO LONGER read — behavior is internally
    constant (terminal interception and the write audit are always on;
    the entry guardrail is audit.WRITE_AUDIT_ENTRY_CAP) and leftover
    occurrences of these keys in runtime configs are COMPLETELY ignored
    (no hint, no log entry).
    Old keys exempt_paths / allowed_root_files stay removed (B2 clean
    break, no backward compat).
    """
    if config_path is None:
        config_path = _get_guard_config_path()
    config_path = Path(config_path)

    result = {
        "allowlist": [],
    }

    if not config_path.is_file():
        return result

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            if data.get("working_dir_root"):
                result["working_dir_root"] = data["working_dir_root"]
            result["allowlist"] = _parse_allowlist(data.get("allowlist"))
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


# ---------------------------------------------------------------- Runtime allowlist (spec 5.11)

# SCR-043 R3 (spec 5.11 v2.11): add-layer rejection messages. The
# outside-root text mirrors the handler-layer R2c verbatim constant in
# __init__.py (config.py must not import the assembly layer, ADR-0007
# dependency direction; the text is duplicated verbatim so both layers
# answer identically).
ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE = (
    "[dir-whip] BLOCKED: the path is outside the Working Directory; no allowlist\n"
    "entry is needed. Writes there are allowed and logged (external-write).\n"
    "Retry the write directly at the requested path."
)
ALLOW_PATH_EMPTY_REJECTED_MESSAGE = (
    "[dir-whip] Rejected: empty path. dir_whip_allow_path requires an explicit\n"
    "path inside the Working Directory."
)


def _normalize_allowlist_path(path):
    """Normalize a path for allowlist comparison (forward slashes)."""
    if path is None:
        return ""
    return str(path).replace("\\", "/")


def runtime_allowlist_add(path, working_dir_root=None):
    """Add a path to the runtime allowlist (process-lifetime).

    Returns a confirmation string for the dir_whip_allow_path tool.

    SCR-043 R3 (spec 5.11 v2.11) value-domain gating: empty/None paths
    are rejected (a normalized-empty entry would prefix-match every
    path). When working_dir_root is injected (non-None), the path is
    asserted to be inside the root via paths.within_working_dir (the
    same implementation as the classify chain; config never imports
    verdict, ADR-0007) -- an outside-root path is NOT stored and the
    rejection message is returned. working_dir_root=None (existing
    direct-call/test form) skips the assertion, behavior unchanged.
    """
    normalized = _normalize_allowlist_path(path)
    if not normalized:
        return ALLOW_PATH_EMPTY_REJECTED_MESSAGE
    if working_dir_root is not None and not within_working_dir(
        normalize_target(normalized, working_dir_root), working_dir_root
    ):
        return ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE
    with _runtime_allowlist_lock:
        _runtime_allowlist.add(normalized)
    logger.debug("dir-whip: runtime allowlist added: %s", normalized)
    return "[dir-whip] Added to runtime allowlist: %s" % normalized


def is_runtime_allowlisted(path):
    """Check a path against the runtime allowlist (normalized slashes).

    Segment-boundary match (SCR-043 R4, case-insensitive): an entry
    exempts ITSELF (file-level registration) and everything UNDER it
    (directory subtree, entry with or without a trailing slash). A bare
    string prefix no longer matches -- allowing "docs" does not exempt a
    same-prefix sibling like "docs_secret/x.txt". casefold (Windows
    caliber) and the forward-slash _normalize_allowlist_path lexical
    domain (same domain as the classify chain) are kept.
    """
    normalized = _normalize_allowlist_path(path).casefold()
    with _runtime_allowlist_lock:
        return any(
            normalized == ec or normalized.startswith(ec.rstrip("/") + "/")
            for ec in (e.casefold() for e in _runtime_allowlist)
        )


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


def dir_whip_allow_path(args, working_dir_root=None, **kwargs):
    """Tool handler: add a path to the runtime allowlist (spec 5.7, 5.11).

    Accepts either the tool-handler contract (args dict + extra kwargs such
    as task_id, per Hermes registry dispatch) or a bare path string (direct
    helper/test callers). Returns a confirmation string. This is the
    plugin's ONLY tool. Wiring into ctx.register_tool happens in __init__.py
    (register).

    SCR-043 R3: optional working_dir_root pass-through to the add layer's
    value-domain assertion (the handler injects the resolved root; the
    bare-path/rootless direct-call form keeps the assertion skipped).
    """
    path = args.get("path") if isinstance(args, dict) else args
    return runtime_allowlist_add(path, working_dir_root=working_dir_root)


# ---------------------------------------------------------------- Config cache (spec 5.5/5.8)

def _resolve_config(ctx, config_path=None):
    """Resolve working_dir_root + allowlist (single-key B2)."""
    working_dir_root = resolve_working_dir_root(ctx, config_path)
    cfg = load_guard_config(config_path)
    allowlist = cfg.get("allowlist", [])
    return (working_dir_root, allowlist)


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

    Returns (working_dir_root, allowlist) tuple. working_dir_root may
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


def ensure_session_root():
    """Explicitly seed the config cache + session root (SCR-045 R2).

    The observation adapters (on_post_tool_call / on_pre_command) used
    to call verdict._resolved_config() and discard the value; the actual
    purpose was get_cached_config's seeding side effect (cache warm-up,
    registered_ctx capture, session-root seed). Same semantics, explicit
    intent. Fail-open: any error -> None (never raises).
    """
    try:
        get_cached_config(state.session.registered_ctx)
    except Exception:
        return None


def _refresh_allowlist_cache():
    """Narrow cache refresh for unified allowlist (spec v2.6 B2).

    Invalidates the cached allowlist so the next get_cached_config /
    classify_target sees the updated file. Clears both the local lock-guarded
    cache and the lazy_singleton accessor when present. Session root is
    re-seeded from the refreshed result via get_cached_config's session logic.
    """
    global _cached_result, _cache_initialized
    with _cache_lock:
        _cached_result = None
        _cache_initialized = False
    if _registered_config_accessor is not None:
        try:
            _registered_config_accessor.reset()
        except Exception:
            pass
    return


def refresh_allowlist_cache():
    """Public alias for narrow allowlist cache refresh."""
    return _refresh_allowlist_cache()


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
    # SCR-041 R3: the allow_path confirmation-issued set follows the
    # runtime allowlist lifecycle (register/re-register clears it too).
    with state.session.lock:
        state.session.confirmation_issued.clear()
    # Session-scoped state: re-seeded at register (get_cached_config) and at
    # the next top-level on_session_start.
    state.session.session_root = None
    state.session.session_root_initialized = False
    state.session.session_profile = None
    stats.reset()
