"""All mutable plugin runtime state in three cohesive containers (SCR-035).

Session state (registration context, session root/profile, fail-open latch,
emit switch, injected host callable, child-session set), audit state
(pre-snapshots, pending violations, parent links, cap flag), stats state
(lock, counters, session fields). Locks travel with their group; cross-group
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
        self.child_session_ids = set()   # 受 self.lock 保护


class _AuditState:
    def __init__(self):
        self.lock = threading.Lock()     # 与 top_session 共锁（跨全局不变量）
        self.reset()

    def reset(self):
        self.pre_snapshots = {}          # key=(session_id, task_id)
        self.pending = {}                # owner-session -> {normpath: {...}}
        self.session_parents = {}
        self.top_session = None          # 与 pending 同锁（修复现状双锁竞态）
        self.cap_warned = False


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
stats = _StatsState()


def reset_all():
    """Single test-cleanup entry point replacing ~10 hand-cleared globals."""
    session.reset()
    audit.reset()
    stats.reset()