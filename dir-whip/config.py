"""Configuration loading, working_dir_root resolution and the config cache -- the dir-whip config layer (spec 5.5).

Inverted resolution chain: dir-whip-config.yaml working_dir_root override (authoritative) -> current profile terminal.cwd -> fail-open, guard disabled (v0.1.0 memo chain removed, spec 1.3/B4), with the HERMES_HOME env override ahead of the platform default (D5); the guarded plugins.plugin_utils.lazy_singleton import degrades to a local lock-guarded cache when absent. Single unified ``allowlist:`` key, strict empty fallback, no backward compat for deleted exempt_paths / allowed_root_files (spec v2.6 B2): the structured ``{files, dirs}`` mapping (RAW passthrough of legacy flat lists too) supersedes the v2.6 file:<basename> / prefix:<abs-path> tagged form. SCR-055 R7: the runtime-allowlist operation family moved to runtime_allowlist.py (its complete home, spec 5.7/5.11); the resolution chain, the raw-allowlist loader and the config cache stay here.

Layer: core+host-guarded
Refs: spec 1.3/B4, spec 5.5, spec 5.7, spec v2.6 B2, SCR-050, SCR-055 R7
Key exports:
  - get_cached_config -- cached (working_dir_root, allowlist); seeds the session root.
  - resolve_working_dir_root -- the inverted 3-step chain; None = guard disabled (fail-open).
  - refresh_resolution -- re-resolve for the session's profile at on_session_start.
  - load_guard_config -- load dir-whip-config.yaml (working_dir_root + raw allowlist).
  - invalidate_config_cache -- narrow cache invalidation for the runtime-allowlist refresh (SCR-055 R7).
  - ensure_session_root / reset_cache / set_session_profile -- cache+session seeding/lifecycle (SCR-045 R2 / SCR-027).
"""

import logging
import threading
from pathlib import Path

import yaml

logger = logging.getLogger("dir-whip")

# plugins.plugin_utils is a Hermes runtime package; absent in the test venv.
# Guarded module-level import so config.py never crashes when unavailable
# (fail-open: get_cached_config degrades to a local lock-guarded cache).
try:
    from plugins.plugin_utils import lazy_singleton
except ImportError:
    lazy_singleton = None

from . import state, stats

# SCR-055 R7: the raw-allowlist passthrough is homed in allowlist.py
# (allowlist semantics home).
from .allowlist import parse_allowlist_raw

# Message templates: centralized in the core leaf module messages.py
# (spec 5.20, SCR-047 R1, ADR-0014); these same-name aliases keep the
# historical config.* / test import surface (MS-2 pinned). The
# runtime-allowlist add layer now imports them from messages.py directly.
from .messages import (
    ALLOW_PATH_EMPTY_REJECTED_MESSAGE,
    ALLOW_PATH_EXTERNAL_REJECTED_MESSAGE,
    RUNTIME_ALLOWLIST_ADDED_TEMPLATE,
)

from .paths import config_file_path, get_hermes_home

_cache_lock = threading.Lock()
_cached_result = None
_cache_initialized = False

# SCR-027 session-scoped resolution: a desktop process registers under the
# ACTIVE profile but later sessions can be a DIFFERENT profile, so the
# working_dir_root is re-resolved per top-level session at on_session_start
# (single-threaded session loop assumption, same as stats); the register-time
# value is REPLACED, never kept stale. All of this lives in state.session.


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


def load_guard_config(config_path=None):
    """Load dir-whip-config.yaml exemptions and overrides.

    Returns a dict with at least 'allowlist' (RAW value: structured
    mapping dict, legacy flat list, or STRICT fallback [] when the key
    is absent or not a list/dict) and optionally 'working_dir_root'
    (str). v2.8 BREAKING (R7 three-key de-configuration): terminal_guard
    / write_audit / write_audit_entry_cap (and the reserved
    write_audit_autofix) are NO LONGER read -- behavior is internally
    constant (terminal interception and the write audit are always on;
    the entry guardrail is audit.WRITE_AUDIT_ENTRY_CAP) and leftover
    occurrences of these keys in runtime configs are COMPLETELY ignored
    (no hint, no log entry).
    Old keys exempt_paths / allowed_root_files stay removed (B2 clean
    break, no backward compat).
    """
    if config_path is None:
        # SCR-052 R1: the single config-path source is
        # paths.config_file_path (the former private
        # _get_guard_config_path is merged away).
        config_path = config_file_path()
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
            result["allowlist"] = parse_allowlist_raw(data.get("allowlist"))
    except Exception as exc:
        logger.debug("dir-whip: failed to load dir-whip-config.yaml: %s", exc)

    return result


def resolve_working_dir_root(ctx, config_path=None):
    """Resolve the Working Directory for the current profile (spec 5.5).

    Inverted 3-step chain (plugin side -- deliberately different from the
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
        working_dir_root = cfg.get("working_dir_root")
        if working_dir_root:
            logger.info(
                "dir-whip: working_dir_root resolved from dir-whip-config: %s",
                working_dir_root,
            )
            return working_dir_root
    except Exception:
        pass

    # 2. current profile's terminal.cwd (fallback)
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            hermes_home = get_hermes_home()
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
    state.session.working_dir_root = <root> (same INFO source log as the
    chain); on fail-open working_dir_root = None + WARNING — a stale value
    from a previous session is NEVER kept. Returns the session's
    working_dir_root.
    """
    state.session.working_dir_root = resolve_working_dir_root(ctx)
    state.session.working_dir_root_initialized = True
    return state.session.working_dir_root


def get_working_dir_root():
    """The session-scoped working_dir_root (None = guard disabled)."""
    return state.session.working_dir_root


def effective_working_dir_root(ctx):
    """The session working_dir_root, resolving lazily before any
    on_session_start ran (SCR-052 G2: renamed from effective_root -- the
    lazy-resolution variant keeps a distinguishable working_dir_root
    family name; the plain accessor is get_working_dir_root).

    Consumers (the /dir-whip report) read the session value; before the
    first on_start the register-time resolution is the initial value, so a
    lazy refresh here keeps them correct in tests and pre-session contexts.
    """
    if not state.session.working_dir_root_initialized:
        refresh_resolution(ctx)
    return state.session.working_dir_root


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
        hermes_home = get_hermes_home()
        cfg_path = _profile_config_path(hermes_home, profile)
        return parse_terminal_cwd(cfg_path)
    except Exception:
        return None


def set_session_profile(profile):
    """Record the session's profile (SCR-027 stats placement).

    stats.jsonl for the session is written into THIS profile's home (via
    paths.profile_home), not the register-time active profile's.
    """
    state.session.session_profile = profile


# Session-directory detection (spec 5.9) homed in paths.py (SCR-050 v3
# R6.3: pure pattern containment); same-name re-export keeps config.* /
# test import paths unchanged.
from .paths import SESSION_DIR_RE, is_inside_session_dir  # noqa: F401


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
    if not state.session.working_dir_root_initialized:
        # Initial value of the session working_dir_root = the register-time
        # resolution.
        state.session.working_dir_root = result[0]
        state.session.working_dir_root_initialized = True
    return (get_working_dir_root(), result[1])


def ensure_session_root():
    """Explicitly seed the config cache + session root (SCR-045 R2).

    The observation adapters (on_post_tool_call / on_pre_command) used
    to call guard.resolved_config() and discard the value; the actual
    purpose was get_cached_config's seeding side effect (cache warm-up,
    registered_ctx capture, session-root seed). Fail-open: any error ->
    None (never raises).
    """
    try:
        get_cached_config(state.session.registered_ctx)
    except Exception:
        return None


def invalidate_config_cache():
    """Narrow config-cache invalidation (SCR-055 R7; spec v2.6 B2 refresh).

    Clears both the local lock-guarded cache and the lazy_singleton
    accessor when present, so the next get_cached_config / classify
    re-reads dir-whip-config.yaml. Session root is re-seeded from the
    refreshed result via get_cached_config's session logic. Consumed by
    runtime_allowlist.refresh_allowlist_cache (allowlist-writer refresh
    hook); the cache globals stay in their home module.
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


def reset_cache():
    """Reset config cache, stats and runtime allowlist (register/re-register)."""
    global _cached_result, _cache_initialized
    with _cache_lock:
        _cached_result = None
        _cache_initialized = False
    if _registered_config_accessor is not None:
        _registered_config_accessor.reset()
    # SCR-055 R7: the runtime-allowlist state family moved to
    # runtime_allowlist.py; the function-local import is the documented
    # cycle-break idiom (that module's refresh hook reaches this one).
    from . import runtime_allowlist
    runtime_allowlist.runtime_allowlist_clear()
    # SCR-041 R3: the allow_path confirmation-issued set follows the
    # runtime allowlist lifecycle (register/re-register clears it too).
    with state.session.lock:
        state.session.confirmation_issued.clear()
    # Session-scoped state: re-seeded at register (get_cached_config) and at
    # the next top-level on_session_start.
    state.session.working_dir_root = None
    state.session.working_dir_root_initialized = False
    state.session.session_profile = None
    stats.stats_reset()


# SCR-052 G2: the report-facing lazy-resolution surface carries the
# distinguishable working_dir_root family name (former effective_root
# alias); the profile probe helpers keep their SCR-045 R6 names.
profile_terminal_cwd = _profile_terminal_cwd
profile_config_path = _profile_config_path

# SCR-050 v3 R6.1: declared public surface (AC-9, spec 5.1 v2.19).
# R6.3: is_inside_session_dir + SESSION_DIR_RE homed in paths.py; the
# same-name re-export entries below stay (consumer/test import paths).
__all__ = [
    "get_cached_config",
    "get_working_dir_root",
    "resolve_working_dir_root",
    "refresh_resolution",
    "load_guard_config",
    "parse_terminal_cwd",
    "ensure_session_root",
    "reset_cache",
    "set_session_profile",
    "invalidate_config_cache",
    "effective_working_dir_root",
    "profile_terminal_cwd",
    "profile_config_path",
    "is_inside_session_dir",
    "SESSION_DIR_RE",
]
