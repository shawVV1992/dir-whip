"""All mutable plugin runtime state in four cohesive containers (SCR-035).

Session state (registration context, session root/profile, fail-open latch,
emit switch, injected host callable, child-session set, parent links,
top-session fallback), audit state (pre-snapshots, pending violations,
cap flag), session-dir state (SCR-044 R5: per-session unique Session
Directory claims + in-flight script-creation markers), stats state (lock,
counters, session fields). Locks travel with their group; cross-group
invariants share one lock. Anti-degradation rule: containers only - never
re-export the individual fields as module-level names (ADR-0005).
"""
import threading


class _SessionState:
    """Registration + per-top-level-session resolution & switches."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.registered_ctx = None       # 单一注册上下文槽（收敛 config._register_ctx 与 dir_whip._registered_ctx）
        self.register_config_path = None
        self.session_root = None         # None = 未解析/fail-open（不保留陈旧值）
        self.session_root_initialized = False
        self.session_profile = None
        self.fail_open_warned = False
        self.emit_enabled = False
        self.session_cwd_fn = None       # 宿主 API 注入槽（ADR-0007；register 时装填）
        self.agent_cwd_fn = None         # 宿主 API 注入槽（ADR-0007；R2 条件注入 agent CWD）
        self.project_active_fn = None    # 宿主 API 注入槽（ADR-0007；R7 项目豁免探针，on_start 时调用）
        self.reminder_status = None      # R2/R6: injected|skipped-outside|skipped-child|unavailable
        self.log_handler_installed = False  # SCR-040 R5: dir-whip.log attach 幂等标志（logsetup.setup）
        self.confirmation_issued = set()  # SCR-041 R3: allow_path 两步确认已签发集合（会话内存，受 self.lock 保护）
        self.child_session_ids = set()   # 受 self.lock 保护
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
        self.lock = threading.Lock()     # 组锁：pending / pre_snapshots 不变量（跨全局不变量）
        self.reset()

    def reset(self):
        self.pre_snapshots = {}          # key=(session_id, task_id)
        self.pending = {}                # owner-session -> {normpath: {...}}
        self.cap_warned = False
        self.nudge_counts = {}           # SCR-040 R2: 续推兜底会话累计计数（owner session_id 键控，cap=3）


class _SessionDirState:
    """Per-session unique Session Directory slot (SCR-044 R5, spec 5.19).

    claims maps owner_session -> bound dir name (root-relative first
    segment, Windows-casefold compared); pending_create marks a script
    creation in flight (owner_session -> True). Owner resolution goes
    through sessions.owner_session (subagent -> parent attribution,
    mirroring the audit pending propagation). Session-lifetime memory:
    cleared at every top-level session start (CLR-1) and by reset_all
    (CLR-2); a restart loses it (accepted, same class as the runtime
    allowlist).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.claims = {}          # owner_session -> dir name (root-relative first segment)
        self.pending_create = {}  # owner_session -> True (script creation in flight)


class _StatsState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.counters = {}
        self.session = {"profile": None, "session_id": None,
                        "is_subagent": None, "started_at": None}


session = _SessionState()
audit = _AuditState()
session_dirs = _SessionDirState()
stats = _StatsState()


def reset_all():
    """Single test-cleanup entry point replacing ~10 hand-cleared globals."""
    session.reset()
    audit.reset()
    session_dirs.reset()
    stats.reset()
