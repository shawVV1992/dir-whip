"""All mutable plugin runtime state in four cohesive containers: session / audit / session_dirs / stats.

session: registration-context slot, working_dir_root/profile, fail-open
latch, emit switch, injected host callables, child-session set, parent
links, top-session fallback. audit: pre-snapshots, pending violations,
cap/nudge counters. session_dirs: per-session unique Session Directory
claims, in-flight script-creation markers, persistence sidecar meta.
stats: counters + session fields. Lock-per-group discipline: locks
travel with their group; cross-group invariants share one lock.
Container-only access -- never re-export individual fields (ADR-0005).

Layer: core
Refs: spec 5.19, SCR-035, ADR-0005
Key exports:
  - session -- container: registration ctx + working_dir_root/profile + switches + injected host callables.
  - audit -- container: pending violations + pre-snapshots + cap/nudge counters.
  - session_dirs -- container: per-session claims + pending markers + claim sidecar meta.
  - stats -- container: outcome counters + session fields.
  - reset_all -- test-cleanup entry: resets all four containers + the persistent claims file.
"""
import threading


class _SessionState:
    """Registration + per-top-level-session resolution & switches."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.registered_ctx = None       # single registration-context slot
        self.register_config_path = None
        self.working_dir_root = None     # None = unresolved/fail-open (never keeps a stale value)
        self.working_dir_root_initialized = False
        self.session_profile = None
        self.fail_open_warned = False
        self.emit_enabled = False
        self.session_cwd_fn = None       # host API injection slot (ADR-0007; filled at register)
        self.agent_cwd_fn = None         # host API injection slot (ADR-0007; conditional agent-CWD injection)
        self.project_active_fn = None    # host API injection slot (ADR-0007; project-exemption probe, called at on_start)
        self.reminder_status = None      # injected|skipped-outside|skipped-child|unavailable
        self.reminder_pending_fallback = False  # unavailable reminder -> one-shot transform_tool_result fallback armed
        self.orphan_pending_fallback = False    # suppressed orphan notice -> same pending-notes fallback armed
        self.orphan_notice_text = None   # cached orphan notice text for the fallback tail
        self.log_handler_installed = False  # dir-whip.log attach idempotence flag
        self.confirmation_issued = set()  # allow_path two-step confirmation issued set (guarded by self.lock)
        self.child_session_ids = set()   # guarded by self.lock
        # Session-topology pair; same container and lock discipline as
        # child_session_ids above.
        self.session_parents = {}        # child_session_id -> parent_session_id (self.lock)
        self.top_session = None          # latest top-level session (child-inheritance fallback)
        # Precomputed plugin paths/version: filled once at register();
        # None until then (direct-call fallbacks keep __file__ derivation).
        self.plugin_dir = None
        self.script_resolver_path = None
        self.skill_md_path = None
        self.plugin_version = None


class _AuditState:
    def __init__(self):
        self.lock = threading.Lock()     # group lock: pending / pre_snapshots invariants
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
    kept) and by reset_all (also clears the persistent claims file); the
    store is restored at register().
    """

    def __init__(self):
        self.lock = threading.Lock()
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


session = _SessionState()
audit = _AuditState()
session_dirs = _SessionDirState()
stats = _StatsState()


def reset_all():
    """Test-cleanup entry: resets all four containers + the persistent
    claims file, so isolation extends beyond the in-memory state."""
    session.reset()
    audit.reset()
    session_dirs.reset()
    stats.reset()
    _clear_persistent_claims()


def _clear_persistent_claims():
    """Delete the persistent claims file (fail-open).

    Function-local import breaks the state -> claims module cycle; the
    cold path only runs on test reset / re-register.
    """
    try:
        from .claims import clear_claims_store
        clear_claims_store()
    except Exception:
        pass
