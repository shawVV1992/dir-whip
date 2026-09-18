"""All mutable plugin runtime state in four cohesive containers: session / audit / session_dirs / stats (SCR-035).

Contents: session = registration-context slot, working_dir_root/profile, fail-open latch, emit switch, injected host callables, child-session set, parent links, top-session fallback; audit = pre-snapshots, pending violations, cap/nudge counters; session_dirs = per-session unique Session Directory claims, in-flight script-creation markers, persistence sidecar meta (SCR-044 R5, SCR-048 R1); stats = counters + session fields. Lock-per-group discipline: locks travel with their group and cross-group invariants share one lock; container-only access - never re-export the individual fields as module-level names (ADR-0005).

Layer: core
Refs: spec 5.19, SCR-035, SCR-044 R5, SCR-048 R1, ADR-0005, ADR-0007
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
        self.registered_ctx = None       # single registration-context slot (converges config._register_ctx and dir_whip._registered_ctx)
        self.register_config_path = None
        self.working_dir_root = None     # None = unresolved/fail-open (never keeps a stale value; SCR-052 G2: renamed from session_root)
        self.working_dir_root_initialized = False
        self.session_profile = None
        self.fail_open_warned = False
        self.emit_enabled = False
        self.session_cwd_fn = None       # host API injection slot (ADR-0007; filled at register)
        self.agent_cwd_fn = None         # host API injection slot (ADR-0007; SCR-039 R2 conditional agent-CWD injection)
        self.project_active_fn = None    # host API injection slot (ADR-0007; SCR-039 R7 project-exemption probe, called at on_start)
        self.reminder_status = None      # R2/R6: injected|skipped-outside|skipped-child|unavailable
        self.reminder_pending_fallback = False  # SCR-048 R4 (5.17): unavailable reminder -> one-shot transform_tool_result fallback armed
        self.log_handler_installed = False  # SCR-040 R5: dir-whip.log attach idempotence flag (logsetup.setup)
        self.confirmation_issued = set()  # SCR-041 R3: allow_path two-step confirmation issued set (session memory, guarded by self.lock)
        self.child_session_ids = set()   # guarded by self.lock
        # SCR-044 R3: session-topology pair, moved in from the audit
        # container (historical misplacement) -- same container and lock
        # discipline as child_session_ids above.
        self.session_parents = {}        # child_session_id -> parent_session_id (self.lock)
        self.top_session = None          # latest top-level session (child-inheritance fallback)
        # P6 precomputed plugin paths/version (31.13): filled once at
        # register(); None until then (direct-call fallbacks keep the
        # __file__-based derivation).
        self.plugin_dir = None
        self.script_resolver_path = None
        self.skill_md_path = None
        self.plugin_version = None


class _AuditState:
    def __init__(self):
        self.lock = threading.Lock()     # group lock: pending / pre_snapshots invariants (cross-global invariant)
        self.reset()

    def reset(self):
        self.pre_snapshots = {}          # key=(session_id, task_id)
        self.pending_violations = {}     # owner-session -> {normpath: {...}} (SCR-052 G3: renamed from self.pending)
        self.cap_warned = False
        self.nudge_counts = {}           # SCR-040 R2: continuation-nudge session-cumulative counts (keyed by owner session_id, cap=3)


class _SessionDirState:
    """Per-session unique Session Directory slot (SCR-044 R5, spec 5.19).

    claims maps owner_session -> bound dir name (root-relative first
    segment, Windows-casefold compared); pending_create marks a script
    creation in flight (owner_session -> True). claim_meta carries the
    persistence sidecar per claim (root / dir / profile / ts plus the
    internal restored-at-register flag, SCR-048 R1) so the write-through
    store rebuilds correct entries. Owner resolution goes through
    subagents.owner_session (subagent -> parent attribution, mirroring the
    audit pending propagation). Cleared at every top-level session start
    (CLR-1 resume exception: a restored claim whose dir is still on disk
    is kept) and by reset_all (CLR-2, which also clears the persistent
    claims file); the store is restored at register().
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
                        "is_subagent": False, "started_at": None}  # SCR-052 T52-11/V2: domain unified to {False, True}


session = _SessionState()
audit = _AuditState()
session_dirs = _SessionDirState()
stats = _StatsState()


def reset_all():
    """Single test-cleanup entry point replacing ~10 hand-cleared globals.

    CLR-2 revision (spec 5.19, SCR-048 R1): the isolation guarantee
    extends from the in-memory containers to the persistent claims file,
    which is cleared here too.
    """
    session.reset()
    audit.reset()
    session_dirs.reset()
    stats.reset()
    _clear_persistent_claims()


def _clear_persistent_claims():
    """Delete the persistent claims file (CLR-2; fail-open).

    Function-local import: session_dirs imports state at module load, so
    a module-level state -> session_dirs edge would be a cycle. The cold
    path only runs on test reset / re-register.
    """
    try:
        from .session_dirs import clear_claims_store
        clear_claims_store()
    except Exception:
        pass
