"""All mutable plugin runtime state in five cohesive containers: session / audit / session_dirs / stats / config.

session: registration-context slot, working_dir_root/profile + resolution source, fail-open
latch, emit switch, injected host callables, child-session set, parent
links, top-session fallback, runtime allowlist set + lock. audit:
pre-snapshots, pending violations, cap/nudge counters, injected classify
chain. session_dirs: per-session unique Session Directory claims,
in-flight script-creation markers, persistence sidecar meta, injected
classify chain. stats: counters + session fields. config: the config
cache group (lock / cached result / initialized bit / host lazy
accessor). Lock-per-group discipline: locks travel with their group;
cross-group invariants share one lock. Container-only access -- never
re-export individual fields.

Layer: core
Refs: spec 5.19
Key exports:
  - session -- container: registration ctx + working_dir_root/profile + resolution source + switches + injected host callables + runtime allowlist.
  - audit -- container: pending violations + pre-snapshots + cap/nudge counters + classify chain slot.
  - session_dirs -- container: per-session claims + pending markers + claim sidecar meta + classify chain slot.
  - stats -- container: outcome counters + session fields.
  - config -- container: config-cache lock/result/initialized bit + host lazy accessor.
  - reset_all -- test-cleanup entry: resets the four mutable containers (in-memory only; the persistent claims file is cleared by the test-support isolation entry, tests/support.py).
"""
import threading
from collections.abc import Callable


class _SessionState:
    """Registration + per-top-level-session resolution & switches."""

    def __init__(self):
        self.lock = threading.Lock()
        # Session-scoped runtime exemption set + its lock: injected at
        # session start, cleared by runtime_allowlist_clear (NOT by
        # reset_all -- clear semantics stay explicit).
        self.runtime_allowlist = set()
        self.runtime_allowlist_lock = threading.Lock()
        self.reset()

    def reset(self):
        self.registered_ctx = None       # single registration-context slot
        self.register_config_path = None
        self.working_dir_root: str | None = None     # None = unresolved/fail-open (never keeps a stale value)
        self.working_dir_root_initialized = False
        self.working_dir_source: str | None = None   # chain step: dir-whip-config / profile-config / fail-open
        self.session_profile = None
        self.fail_open_warned = False
        self.emit_enabled = False
        self.session_cwd_fn = None       # host API injection slot (filled at register)
        self.agent_cwd_fn = None         # host API injection slot (conditional agent-CWD injection)
        self.project_active_fn: Callable[[], tuple[str, list[str]] | None] | None = None    # host API injection slot (project-exemption probe, called at on_start)
        self.reminder_status: str | None = None      # injected|skipped-outside|skipped-child|unavailable
        self.reminder_pending_fallback = False  # unavailable reminder -> one-shot transform_tool_result fallback armed
        self.orphan_pending_fallback = False    # suppressed orphan notice -> same pending-notes fallback armed
        self.orphan_notice_text: str | None = None   # cached orphan notice text for the fallback tail
        self.log_handler_installed = False  # dir-whip.log attach idempotence flag
        self.confirmation_issued = set()  # allow_path two-step confirmation issued set (guarded by self.lock)
        self.unseen_tools = set()        # unseen:<tool> probe throttle, (session_id, tool_name) keys (self.lock)
        self.child_session_ids = set()   # guarded by self.lock
        # Session-topology pair; same container and lock discipline as
        # child_session_ids above.
        self.session_parents = {}        # child_session_id -> parent_session_id (self.lock)
        self.top_session = None          # latest top-level session (child-inheritance fallback)
        # Precomputed plugin paths/version: filled once at register();
        # None until then (direct-call fallbacks keep __file__ derivation).
        self.plugin_dir: str | None = None
        self.script_resolver_path: str | None = None
        self.skill_md_path: str | None = None
        self.plugin_version: str | None = None


class _AuditState:
    def __init__(self):
        self.lock = threading.Lock()     # group lock: pending / pre_snapshots invariants
        # Classification chain slot, wired at register; it
        # survives reset_all (re-wired at every register).
        self.classify_fn = None
        self.reset()

    def reset(self):
        self.pre_snapshots = {}          # key=(session_id, task_id)
        self.pending_violations = {}     # owner-session -> {normpath: {...}}
        self.cap_warned = False
        self.nudge_counts = {}           # continuation-nudge session-cumulative counts (owner session_id, cap=3)


class _SessionDirState:
    """Per-session unique Session Directory slot (spec 5.19).

    claims: owner_session -> bound dir name (root-relative first segment,
    Windows-casefold compared); pending_create marks a script creation in
    flight; claim_meta carries the persistence sidecar per claim
    (root/dir/profile/ts + internal restored flag) so the write-through
    store rebuilds correct entries. Owner resolution goes through
    subagents.owner_session. Cleared at every top-level session start
    (resume exception: a restored claim whose dir is still on disk is
    kept) and by reset_all (in-memory container only; the persistent
    claims file is cleared by the test-support isolation entry); the
    store is restored at register().
    """

    def __init__(self):
        self.lock = threading.Lock()
        # Orphan-scan classification chain slot, wired at register
        # It survives reset_all.
        self.classify_fn = None
        self.reset()

    def reset(self):
        self.claims = {}          # owner_session -> dir name (root-relative first segment)
        self.pending_create = {}  # owner_session -> True (script creation in flight)
        self.claim_meta = {}      # owner_session -> {root, dir, profile, ts, restored}


class _StatsState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.counters = {}
        self.session = {"profile": None, "session_id": None,
                        "is_subagent": False, "started_at": None}  # is_subagent domain = {False, True}


class _ConfigState:
    """Config-cache group: local lock-guarded cache + host lazy accessor.

    The cache is managed explicitly by config.reset_cache /
    invalidate_config_cache; reset_all does not touch it.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.cached_result = None
        self.cache_initialized = False
        self.lazy_accessor = None        # host lazy_singleton accessor (None without the host package)


session = _SessionState()
audit = _AuditState()
session_dirs = _SessionDirState()
stats = _StatsState()
config = _ConfigState()


def reset_all():
    """Test-cleanup entry: resets the four mutable containers (in-memory
    only). The config cache stays under config.reset_cache(); the
    persistent claims file is cleared by the test-support isolation entry
    (tests/support.py), not here."""
    session.reset()
    audit.reset()
    session_dirs.reset()
    stats.reset()
