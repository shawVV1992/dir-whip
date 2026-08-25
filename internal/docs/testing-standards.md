# Testing Standards (v0.4.2 basis, in force for v0.4.2)

Authoritative reference for all testing in the dir-whip project.
Test cases derive from `specs/dir-whip-spec.md` — behavior basis is the
frozen v2.3 clause set (re-frozen 2026-08-22, section 7 acceptance criteria
incl. 7.6 Terminal Write Discipline); the spec is now v2.6 ACTIVE
(SCR-037 amendment v2.6 2026-08-25: single-key `allowlist: []` with
discriminated `file:<basename>` | `prefix:<abs-path>` B2 clean break —
`exempt_paths` + `allowed_root_files` removed as config keys, no backward
compat; 5.6/5.3/5.7/5.18 aligned; re-freeze pending 37.12 verification)
and unified SCR-034 proposal acceptance A1-A15
(`archive/v0.3.0/scr-033-034-plan.md` section 9). Supersedes
the v0.2.0 testing standards (archived at `archive/v0.2.0/testing-standards.md`);
the v0.2.0 criteria rows remain the frozen v0.2.0 basis (Done), this revision
ADDS the v0.3.0 terminal-discipline layer and the v0.4.2 single-key allowlist +
release-hygiene + allowlist-command layers. This document remains in force for
the v0.4.2 line (SCR-037 B2): single-key allowlist is the behavior delta vs
v2.5; the unified allowlist command and hygiene tests are the added regression
net (ADR-0008 + amendment 2026-08-25).

---

## 1. General Principles

- Spec is the single source of truth for test cases (spec §7; v0.3.0 rows
  in 7.6 carry the [A#] ids from the unified proposal).
- Every acceptance criterion in spec section 7 must map to at least one test
  (acceptance matrix, section 7).
- TDD red-green cycle: write failing test first, then implement, then refactor.
- Tests are version-controlled, self-contained, and reproducible.
- Each test file is independently runnable: `pytest tests/test_<name>.py`.
- No test depends on another test's side effects.
- Tests verify behavior, not implementation details.
- **Environment isolation**: all automated tests run fully isolated from any
  real Hermes environment — pytest tmp_path + subprocess + mocks only. Live
  Hermes verification is a separate, gated phase (section 6).
- **Write-audit isolation (v0.3.0)**: audit tests create and delete files
  ONLY inside tmp_path fake workspace roots. Snapshot determinism is
  achieved with controlled mtime (`os.utime`) and size — never with real
  sleeps. No test touches the real Working Directory or real HERMES_HOME.
- **Performance budget (v0.3.0)**: the audit performance test asserts a
  GENEROUS ceiling (p95 < 10ms at ≤500 entries is verified on a measured
  median, not an absolute wall-clock bound on CI); the deterministic part is
  the entry-cap skip behavior, not the timing.
- **Cross-platform**: the suite must pass on Windows 10+, Linux, WSL, and
  macOS. New v0.3.0 logic (chain split, device exemption, audit snapshot)
  is platform-neutral Python and is covered identically on all platforms via
  the matrix; Windows-specific branches keep the existing
  `@pytest.mark.skipif` gates.

---

## 2. Directory Structure

```
tests/
├── conftest.py                     # loads dir-whip/ as package alias `dirwhip` (v0.4.0, SCR-035)
├── test_workspace_resolver.py      # shared Working Directory resolution (4.4)
├── test_create_session_dir.py      # S1 script tests
├── test_audit_workspace.py         # S2 script tests (incl. --gate / cron cleanup + v0.4.2 enablement precheck)
├── test_dir_whip.py                # guard logic + observation hooks + events
│                                   #   (+ v0.3.0 terminal front layer + write audit)
├── test_rename_regression.py       # SCR-030 rename regression gate (legacy-token sweep)
├── test_config.py                  # config resolution, stats, commands, prompt (v0.4.2: allowlist command lives here as TestAllowlistUnified)
├── test_state_container.py         # (v0.4.0, task 31.9) state.py container anti-degradation
├── test_parity_resolution.py       # (v0.4.0, task 31.14) dual-implementation parity contract
└── test_release_hygiene.py         # (v0.4.2, SCR-037 B2) shipped-tree agent-config literal sweep + template default `allowlist: []`
```

Rules:
- One test file per module/script. **SCR-034 additions live inside
  test_dir_whip.py** — both the terminal front layer and the write audit are
  implemented in dir_whip.py, so no new test file is created (matches the
  one-file-per-module rule).
- **v0.4.0 (SCR-035)**: the plugin splits into 11 modules; new-module tests
  follow the one-file-per-module rule again — `test_state_container.py`
  (state.py) and `test_parity_resolution.py` (cross-implementation contract,
  owns no production module by design). Existing per-behavior classes stay in
  their files; only import paths change.
- **conftest.py (v0.4.0)**: exactly one — it registers the plugin directory
  as a real package under the alias `dirwhip` via importlib
  (`submodule_search_locations`), so relative imports inside the plugin work
  as in production and all tests share ONE module instance. Tests import via
  `from dirwhip import config` / `from dirwhip import dir_whip`; flat
  `sys.path.insert` blocks are removed. No other conftest machinery (no
  auto-fixtures — isolation stays explicit per test).
- No subdirectories; .venv/ not in git.
- Retired with v0.1.0: test_clean_tmp.py, test_init_workspace.py,
  test_install.py (still not resurrected).

---

## 3. Naming Conventions

### Files

- Pattern: `test_<module_name>.py`.

### Classes

Pattern: `Test<Category>`.

#### v0.2.0 classes retained unchanged (spec 7.1-7.5 basis)

| Class | Purpose (unchanged) |
|-------|---------------------|
| TestResolveChain / TestCandidateRoots / TestMinimalYamlParse / TestValidateWorkspace | Resolver chain (4.4) |
| TestCore / TestBoundary | Script happy-path and edges |
| TestGateFlag / TestCronTmpCleanup / TestAllowedRootFiles | audit --gate, cron cleanup, allowlist |
| TestClassifyTarget / TestSessionStartGate / TestAllowPath / TestFailOpen / TestNormalization | Guard verdicts, tiers, gate, normalization |
| TestStructuredEvents / TestEventBus / TestPreCommandObserve / TestSubagentHooks / TestPostToolCall / TestPostApprovalResponse | Observability |
| TestResolveWorkingDirRoot / TestGuardConfig / TestStats / TestStatsJsonl / TestCommands / TestDisciplinePrompt | Config, stats, /dir-whip, prompt |
| TestRegisterSkill / TestManifestV2 / TestRemovedSurfaces / TestCrossPlatform / TestRegression / TestRenameRegression | Registry, surface, meta |

#### v0.3.0 classes (spec 7.6, SCR-034 unified)

| Class | Purpose |
|-------|---------|
| TestTerminalFront | **Front layer (replaces/augments TestTerminalCoarse behavior)**: chain-aware target extraction (`;` standalone separator; `&&`/`;`/`|`/newline as chain boundaries; per-segment extraction of redirect/touch/cp-mv; quote-aware boundaries; lone `&` as a segment separator); `=`-residue redirect target -> terminal-write-uncertain event (NOT a silent drop); device-path exemption before normalization (`/dev/null` `/dev/stdout` `/dev/stderr` -> no verdict/stats event; no drive-inherited `E:\dev\null`; no `;`-stuck `/dev/null;` target); heredoc blanket demotion (command containing `<<` -> uncertain tier, no body parsing) |
| TestWriteAuditKernel | **Audit kernel**: snapshot/diff four states (new / modified / deleted / unrelated); file-entries-only rule (dir mtime changes ignored, incl. session dirs and `.git/`); violation classification through the shared chain (allowlist `file:` / `prefix:` / session dir -> not a violation; unprotected new root file -> violation; deletion -> record-only); session-scoped snapshot state (pre/post pairing, command-blocked-at-pre -> no post snapshot) |
| TestWriteAuditNotice | **L1 fire-once notice** (transform_tool_result): appends exactly once per violation; later results carry no re-append; error results not decorated; non-string results untouched; notice names the path and remediation |
| TestWriteAuditGate | **L3 settlement gate**: unresolved violation blocks the next write-class tool (write_file / patch / terminal) via the standard block channel (message lists paths + remediation); re-scan after file removal / move to allowed location re-opens the gate; latch action emits write-audit-gate-block |
| TestWriteAuditSession | **Session scoping**: latch session-scoped, cleared at top-level session start; child sessions inherit the parent latch (child_session_ids gate, all child session-start steps skipped); subagent root writes resolve into the parent latch; parent-delegated target dirs stay exempt |
| TestWriteAuditConfig | **Config wiring**: `write_audit: false` disables snapshot/diff/events/gate; `write_audit_entry_cap` default 2000 and configurable; entry count above cap -> audit skipped + one-time WARNING; scan OSError -> silent skip (fail-open) |
| TestWriteAuditEvents | **Stats/event integration**: verdict events `write-audit-violation` / `write-audit-gate-block` in the 5.13 recording and the 5.14 event bus (`dir-whip:write-audit-*` sidecar payload; bare-name emit contract); privacy rules (relative target or hash); L1 notice itself produces NO event |
| TestStatsJsonl (extended) | New rule_keys accepted in the append schema; split by is_subagent; rollover untouched |
| TestRegression (extended) | Perf budget (p95 < 10ms at ≤500 entries, generous ceiling); entry-cap skip; no exceptions escape the new hooks |

#### v0.4.0 classes (SCR-035 refactor; structural, behavior-frozen)

| Class | Purpose |
|-------|---------|
| TestStateContainer (test_state_container.py, task 31.9) | **state.py anti-degradation**: three containers exist (`session` / `audit` / `stats`); no flat re-export of field names at module level (a leaked name like `session_root` or `pending` fails); `reset_all()` clears all three groups in one call (fixture single-point cleanup, ADR-0005) |
| TestCoreImportSurface (lands in test_dir_whip.py, task 31.13) | **Core zero-host-import discipline** (ADR-0007): scan every core module (terminal/paths/verdict/events/audit/sessions/state/stats/config) import surface — any `hermes_cli` (or host API) import fails the test; host capabilities enter only via `__init__.py` injection (`_session_cwd_fn` slot in state.session) |
| TestParityResolution (test_parity_resolution.py, task 31.14) | **Dual-implementation parity contract** (ADR-0006): plugin `config.py` resolution chain and skill `scripts/workspace_resolver.py` produce identical outputs for identical inputs across shared vectors (profile home / root home / HERMES_SESSION_PROFILE / terminal.cwd / allowed-root-file shapes); normalization contract paths.normalize_target ↔ workspace_resolver.normalize_path (MSYS `/c/..`, Cygwin `/cygdrive/c/..`, case, slash direction, backslash roots). Any divergence turns red with the named vector id |

#### v0.4.2 classes (SCR-037 v0.4.2 B2; spec 5.6/5.3/5.7 single-key allowlist + hygiene)

| Class | Purpose |
|-------|---------|
| TestReleaseHygiene (test_release_hygiene.py, SCR-037 R2) | **Shipped-tree hygiene** (ADR-0008 D1 + B2): scan every text file under `dir-whip/` (`.yaml/.yml/.json/.toml/.md/.py`) for the four agent-config literals `AGENTS.md` / `CLAUDE.md` / `.cursorrules` / `.clinerules` — any hit fails; also asserts the shipped template default is `allowlist: []` (discriminated `file:` | `prefix:`, strict empty; old keys `exempt_paths` / `allowed_root_files` deleted, `allowed_root_files: []` no longer accepted) |
| TestAllowlistUnified (test_config.py, SCR-037 R3/R5 B2) | **`/dir-whip allow|remove|list` command — single-key `allowlist` (spec 5.7, ADR-0008 D2/D3 B2)**: bare `/dir-whip` still renders the merged report with `Allowlist: Files: ...  Prefixes: ...` (or `Allowlist: (strict empty allowlist)` when key missing); `allow` without args lists numbered root-file candidates (excludes session dirs / already-allowlisted `file:` entries; prefix candidates not listed); `allow <file|prefix:PATH|PATH/>` / `allow 1,3` / `remove <file|prefix:PATH|PATH/>` mutate the persistent `allowlist` via row-level edit (comments preserved, missing-key append, `file:` vs `prefix:` discrimination: no slash -> `file:<basename>`, slash or `prefix:` tag -> `prefix:<abs-path>`, trailing `/` normalized, duplicate add idempotent); `list` shows current allowlist split into Files / Prefixes; unknown subcommand -> `Usage: /dir-whip [allow|remove|list]`; invalid file names (slash, `..`, empty) and invalid prefixes (non-absolute, `..`, empty, `prefix:` with relative) rejected with no mutation; mutation narrowly refreshes the config cache so the next `classify` / `audit_classify` sees the new allowlist (both file and prefix tiers) |
| TestPluginEnablementPrecheck (test_audit_workspace.py, SCR-037 R7) | **Skill-side enablement precheck** (ADR-0008 D4): `audit_workspace.py` locates the current profile's `config.yaml` (inline layout-aware `_profile_config_path` shape test, no import of `workspace_resolver` per #55) and reports three states — `enabled` (quiet), `not-enabled` (WARN + `hermes plugins enable dir-whip` guidance), `disabled` (WARN, in `plugins.disabled`) — without changing the exit code |

Sample — state container anti-degradation (task 31.9):

```python
import dirwhip.state as state


def test_containers_exist():
    assert hasattr(state, "session") and hasattr(state, "audit") and hasattr(state, "stats")


def test_no_flat_reexport_of_field_names():
    field_names = {"session_root", "fail_open_warned", "pending", "pre_snapshots",
                   "counters", "child_session_ids", "top_session"}
    leaked = field_names & set(dir(state))
    assert not leaked, f"state.py must not re-export fields flatly: {leaked}"


def test_reset_all_clears_all_groups():
    state.session.fail_open_warned = True
    state.audit.top_session = "s1"
    state.stats.counters["x"] = 1
    state.reset_all()
    assert state.session.fail_open_warned is False
    assert state.audit.top_session is None
    assert state.stats.counters == {}
```

Sample — core import surface scan (task 31.13):

```python
CORE_MODULES = ["terminal", "paths", "guard", "events", "audit",
                "sessions", "state", "stats", "config"]


@pytest.mark.parametrize("mod", CORE_MODULES)
def test_core_module_imports_no_host(mod):
    import importlib
    m = importlib.import_module(f"dirwhip.{mod}")
    src = inspect.getsource(m)
    assert "hermes_cli" not in src, f"{mod} imports the host"
```

Sample — parity vector shape (task 31.14; expected values taken from BOTH
implementations agreeing on the same fake home, never one side hardcoded):

```python
VECTORS = [
    # (vector_id, env{HERMES_HOME, HERMES_SESSION_PROFILE}, terminal_cwd)
    ("profile-home",    {"HERMES_HOME": "<tmp>/profiles/learn"}, None),
    ("root-home",       {"HERMES_HOME": "<tmp>"},               None),
    ("session-profile", {"HERMES_HOME": "<tmp>", "HERMES_SESSION_PROFILE": "learn"}, None),
    ("terminal-cwd",    {"HERMES_HOME": "<tmp>/profiles/learn"}, "<tmp>/profiles/learn/ws/proj"),
]


@pytest.mark.parametrize("vid,env,cwd", VECTORS)
def test_resolution_parity(vid, env, cwd, tmp_path):
    assert resolve_plugin(env, cwd) == resolve_skill_script(env, cwd), vid
```

### Methods

Same pattern as v0.2.0: `test_<condition>_<expected_result>`. New examples
(for the red-green drive):

- `test_semicolon_emits_separator_token`
- `test_targets_never_extracted_across_chain_boundaries`
- `test_quoted_and_not_a_chain_boundary`
- `test_equals_residue_routes_to_uncertain_event`
- `test_dev_null_exempt_before_normalization_no_event`
- `test_dev_null_stuck_semicolon_has_no_target`
- `test_heredoc_blanket_demotion_no_body_parse`
- `test_chain_touch_root_file_now_blocked`
- `test_audit_diff_detects_new_modified_deleted_unrelated`
- `test_directory_mtime_change_ignored`
- `test_new_root_file_outside_allowlist_violation`
- `test_session_dir_content_write_not_violation`
- `test_notice_appended_exactly_once_fire_once`
- `test_error_result_not_decorated`
- `test_gate_blocks_next_write_class_tool`
- `test_gate_reopens_after_file_removed`
- `test_child_session_inherits_parent_latch`
- `test_write_audit_disabled_no_events_no_gate`
- `test_entry_cap_exceeded_skips_audit_one_warning`
- `test_audit_scan_oserror_fails_open_silent`
- `test_write_audit_event_privacy_relative_target`

v0.4.0 additions (SCR-035):

- `test_containers_exist`
- `test_no_flat_reexport_of_field_names`
- `test_reset_all_clears_all_groups`
- `test_core_module_imports_no_host`
- `test_resolution_parity` (parametrized by vector id)
- `test_normalization_parity_msys_drive`
- `test_normalization_parity_cygwin_drive`
- `test_normalization_parity_case_and_slash_direction`

v0.4.2 additions (SCR-037 B2 single-key `allowlist`):

- `test_shipped_tree_has_no_agent_config_literals`
- `test_template_default_is_strict_empty_allowlist` (asserts `allowlist: []`, old keys `allowed_root_files` / `exempt_paths` absent)
- `test_allow_without_args_lists_candidates` (excludes session dirs / already-allowlisted `file:` entries)
- `test_allow_file_by_name_mutates_config_row_level` (no slash -> `file:` discriminant)
- `test_allow_prefix_by_path_mutates_config` (slash or `prefix:` tag -> `prefix:` discriminant, trailing `/` normalized)
- `test_allow_prefix_explicit_tag`
- `test_allow_by_index_batch`
- `test_allow_duplicate_is_idempotent` (file and prefix)
- `test_allow_invalid_file_name_rejected` (slash, `..`, empty -> no mutation)
- `test_allow_invalid_prefix_rejected` (non-absolute, `..`, empty, `prefix:` with relative -> no mutation)
- `test_remove_file_by_name_mutates_config`
- `test_remove_prefix_by_path_mutates_config`
- `test_list_shows_files_and_prefixes`
- `test_list_strict_empty_allowlist_when_key_missing`
- `test_unknown_subcommand_shows_usage`
- `test_mutation_refreshes_cache_for_next_classify` (file and prefix tiers)
- `test_mutation_refreshes_cache_for_next_audit_classify`
- `test_precheck_reports_enabled`
- `test_precheck_reports_not_enabled_with_guidance`
- `test_precheck_reports_disabled`

---

## 4. Test Isolation Rules

**Isolation boundary (mandatory)**: every automated test runs WITHOUT any real
Hermes environment. Tests never touch a real Hermes installation, real profile
directories, the deployed plugin copy, or a real HERMES_HOME.

- `hermes_home()` honors the HERMES_HOME env override — every test sets
  `HERMES_HOME` to a pytest tmp_path fixture (fake home with
  `dir-whip/dir-whip-config.yaml`, `config.yaml`, `profiles/<name>/config.yaml`).
- **Session env control**: tests explicitly set / remove `HERMES_SESSION_PROFILE`
  and `TERMINAL_CWD` via the `env` parameter (they are the resolver inputs —
  never inherit from the real process env).
- `hermes_cli` is absent from the test venv: plugin-side guarded imports must
  degrade (fail-open), never crash.
- Subprocess-invoked scripts run under the same fake env (env= parameter).
- No test writes outside tmp_path; stats.jsonl writes go to the fake
  HERMES_HOME/dir-whip/.
- **Audit-specific (v0.3.0)**: the fake WORKSPACE ROOT lives under a second
  tmp_path fixture (never the same tree as the fake HERMES_HOME, mirroring
  the real separation). Snapshot timing uses `os.utime` bumps, never sleeps.
  Deletion/move of violation files in gate tests uses tmp_path paths only.
- **Real-data replay**: the stats.jsonl replay (A15) ingests a FIXTURE COPY
  of the feedback/06 dataset (checked into the test tree or generated
  by the fixture) — never the live HERMES_HOME/dir-whip/stats.jsonl.
- Real Hermes verification is gated behind section 6 (live phase) — planned
  only after the full automated suite passes.

---

## 5. Fixture Patterns (v0.3.0)

### Fake Hermes Home (unchanged from v0.2.0)

```python
def make_hermes_home(tmp_path, profiles=None, guard_config=None):
    hh = tmp_path / "hermes"
    (hh / "dir-whip").mkdir(parents=True)
    (hh / "profiles").mkdir()
    # default profile config at hh/config.yaml; named at hh/profiles/<n>/config.yaml
    # dir-whip-config at hh/dir-whip/dir-whip-config.yaml
    return hh
```

- Profiles fixture: `config.yaml` (default) + `profiles/{learn,job-hunt}/config.yaml`
  each with `terminal.cwd`.
- dir-whip-config fixture: `allowlist: []` (discriminated `file:<basename>` | `prefix:<abs-path>`, default strict empty, v2.6 B2; old keys `exempt_paths` / `allowed_root_files` deleted, no compat; bare no-slash entries treated as `file:`, bare with slash as `prefix:` — generator compat, not a config key), optional `working_dir_root`, optional `terminal_guard`, **optional `write_audit` / `write_audit_entry_cap` (v0.3.0)**.
- Session env fixture: `env = {"HERMES_HOME": str(hh),
  "HERMES_SESSION_PROFILE": "learn"}` (and pop TERMINAL_CWD for determinism).

### Fake Workspace Root (new, v0.3.0)

```python
def make_workspace_root(tmp_path, entries=None):
    """entries: list of names -> created as files (with content) or dirs (suffix '/')."""
    root = tmp_path / "ws"
    root.mkdir()
    for name in (entries or []):
        p = root / name
        p.mkdir() if name.endswith("/") else p.write_text("x", encoding="utf-8")
    return root
```

### Snapshot Determinism

- The plugin's snapshot is (name, size, mtime_ns, is_dir) per top-level entry.
- Tests control modification WITHOUT sleeps:
  - create -> new entry;
  - modify -> `p.write_text(...)` then `os.utime(p, ns=(t, t))` with a bumped
    timestamp;
  - delete -> `p.unlink()`;
  - unrelated -> touch an entry not in the snapshot keys.
- The kernel is exercised through the plugin's public functions
  (`snapshot(root)`, `diff_snapshots(a, b)`, `audit_classify(...)` per the
  implementation split), so no filesystem races are possible.

### State Reset (v0.4.0)

Before SCR-035 phase 5, tests hand-clear ~10 private module globals in
fixtures. From task 31.9 on, the single entry point replaces them all:

```python
@pytest.fixture(autouse=False)
def clean_state():
    from dirwhip import state
    state.reset_all()
    yield
    state.reset_all()
```

- `reset_cache()` (config parse cache) and `runtime_allowlist_clear()`
  remain their own public APIs — they are config-layer state and are NOT
  part of the three containers.
- Tests that need a pre-seeded session context set `state.session.*`
  fields directly (they are the documented container fields), never
  module-level globals.

### Resolver Test Matrix (unchanged)

| Scenario | Env / fixture | Expected root |
|----------|---------------|---------------|
| Explicit override | dir-whip-config working_dir_root = W1 | W1 (chain step 1) |
| Profile env | HERMES_SESSION_PROFILE=learn, no override | learn's terminal.cwd |
| Enumeration fallback | no HERMES_SESSION_PROFILE, explicit --workspace = a profile root | that root |
| TERMINAL_CWD candidate | no HERMES_SESSION_PROFILE, TERMINAL_CWD=W2 | W2 joins the candidate set |
| Fail-open | nothing resolvable | None + one WARNING |

### Front-Layer Scenario Matrix (spec 5.10 + 7.6 A1-A5, A10 front side)

| Scenario | Command shape | Expected |
|----------|---------------|----------|
| feedback/06 case 1 | `cp <ext> <ext>.bak_$(date +%Y%m%d_%H%M%S) && echo backed up` | allow / external-write; NO `up` block (A1) |
| feedback/06 case 2 | `cp <ext>.bak && echo backed up && grep bak_cdp` | NO `bak_cdp` block (A1) |
| feedback/06 case 3 | `cp <ext>.bak && echo backed up` | NO `up` block (A1) |
| feedback/06 case 4 | `var=1` newline `curl > <ext>` newline `python - <<'PYEOF'` + `>=` in body | NO `=` block; real ext write external-write (A1/A3) |
| `=` residue | `grep pkg>=1.0` | no block; terminal-write-uncertain event (A2) |
| chain true positive | `echo hi && touch notes.txt` (`notes.txt` unprotected root) | block terminal-touch (A4) |
| chain segment scoping | `cp a b && echo hi` | cp target = `b`, NOT `hi` (A1/A4) |
| device exemption | `ls 2>/dev/null`, `cmd>/dev/null` | no event at all (A5) |
| device stuck `;` | `cmd 2>/dev/null;` | no `/dev/null;` target; no `;` stuck (A5) |
| heredoc demotion | `cat > notes.txt <<EOF` + body | uncertain tier (front), no body parse (A10 front) |
| quoted boundary | `echo "a && b"` | no split, no targets (B2 guard) |
| lone `&` | `cmd1 & cmd2` | segment split at `&` (B1 guard) |

### Audit Scenario Matrix (spec 5.18 + 7.6 A6-A14)

| Scenario | Setup | Expected |
|----------|-------|----------|
| add | new root file `notes.md` (no whitelist/exempt/session) | violation -> L1 notice once (A6/A7/A8) |
| modify | existing unprotected root file re-written (bumped utime/size) | violation (A6/A7) |
| delete | root file removed | record-only, NOT a violation (A6/A7) |
| unrelated | file elsewhere / unchanged snapshot | no diff, no event (A6) |
| dir content change | file added inside `sess/Outputs/` (session dir) | dir entry mtime changes -> IGNORED, not a violation (A6/A7) |
| `.git/` content | file created under `.git/` | not a violation (A7) |
| allowlist file write | `README.md` with `file:README.md` entry rewritten | not a violation (A7) |
| allowlist prefix write | file under `prefix:E:/ws/projects/foo` entry | not a violation (A7) |
| fire-once | violation persists across two terminal calls | notice on first result only (A8) |
| error result | terminal returns an error dict | notice NOT appended (A8) |
| gate block | unresolved violation; next `write_file` | block with path + remediation (A9) |
| gate reopen | violation file moved to session dir | next write allowed (A9) |
| gate + subagent | child session calls terminal writing root | parent latch blocks next parent write (A11) |
| disabled | `write_audit: false` | no snapshot/diff/events/gate at all (A13) |
| entry cap | 2001+ synthetic entries, cap default 2000 | audit skipped + one-time WARNING (A12) |
| OSError | root deleted mid-scan (mock) | silent fail-open, no verdict (A13) |
| events | violation + gate block | `write-audit-violation` / `write-audit-gate-block` in stats + bus, relative target, L1 not an event (A14) |

---

## 6. Live Verification (gated)

After the full automated suite passes, real-machine checks are planned
separately (user-gated). v0.2.0 items retained:

- Install via `hermes plugins install shawVV1992/dir-whip/dir-whip --enable`;
  write to the root -> blocked; agent replies [Reason]/[Next].
- `/dir-whip` merged report in a live session.
- Cron tick with `--gate` (wakeAgent JSON; mismatch -> no wake).
- Event bus emit visible (local v0.20.0 has the bus).
- Subagent delegation: child writes land in the parent-passed dir; parent
  allowlist survives child sessions.

v0.3.0 additions:

- **L1 channel live**: a `-z` session runs a heredoc root write
  (`cat > notes.md <<EOF`); the result shows the fire-once audit notice; a
  second terminal call does NOT re-append; moving the file re-opens the gate.
- **L3 gate live**: unresolved violation -> next write_file/terminal returns
  the block message; after moving the file the same call succeeds (A9/A10).
- **feedback/06 replay**: 19:37 cp-chain cases run in the live session (no
  block, no noise); 23:04 heredoc case runs clean (A1-A3).
- **stats.jsonl replay (A15)**: a copy of the real 675-line stats.jsonl
  (logged 2026-08-13..22) is replayed through the updated recording; assert
  external-write drops from ~183 to near the real ~9-11 level, terminal
  events reflect the new rule_keys, and `write-audit-*` counts match the
  actual root writes in the replayed window.
- **transform_tool_result channel verified on the real runtime**: the
  returned string replaces the model-visible result (per the
  security-guidance precedent), not just unit-mocked.

---

## 7. Acceptance Matrix (spec v2.6 section 7)

Legend: Pending / In-progress / Done.

### 7.1 Skill — v0.2.0 basis (Done, archived matrix rows)

| # | Criterion | Test class | Status |
|---|-----------|-----------|--------|
| 1-14 | v0.2.0 skill criteria (bundling, frontmatter, docs, removed memo surfaces) | TestRegisterSkill / TestRemovedSurfaces / TestRegression | Done (v0.2.0, 2026-08-13) |

### 7.2 Scripts — v0.2.0 basis (Done, archived matrix rows)

| # | Criterion | Test class | Status |
|---|-----------|-----------|--------|
| 1-11 | v0.2.0 script criteria (--help/--workspace, exit codes, --gate, allowlist, resolver, removed scripts, matrix) | TestCore / TestBoundary / TestValidateWorkspace / TestGateFlag / TestCronTmpCleanup / TestAllowedRootFiles / TestResolveChain / TestCandidateRoots / TestCrossPlatform / TestRemovedSurfaces | Done (v0.2.0, 2026-08-13) |

### 7.3 Plugin — v0.2.0 basis (Done)

| # | Criterion | Test class | Status |
|---|-----------|-----------|--------|
| 1-20 | v0.2.0 plugin criteria (manifest, register, verdicts, tiers, fail-open, stats, allow_path, removed surfaces, events, subagents, session gate, block message) | v0.2.0 classes (unchanged) | Done (v0.2.0, 2026-08-13/14) |
| 21-23 | v0.2.0 rename / command / report-label criteria (SCR-029/030/031) | TestRenameRegression / TestCommands | Done (2026-08-14) |

### 7.4 Domain Model — v0.2.0 basis (Done)

| # | Criterion | Test class | Status |
|---|-----------|-----------|--------|
| 1-4 | CONTEXT.md terminology and removed entries | TestRegression / TestRemovedSurfaces | Done (v0.2.0, 2026-08-13) |

### 7.5 Integration — v0.2.0 basis (Done except noted)

| # | Criterion | Test class / phase | Status |
|---|-----------|--------------------|--------|
| 1-8 | v0.2.0 live rows (install, root block + [Reason]/[Next], cron --gate, bus, cross-platform, three-profile smoke) | Live phase (section 6) / TestCrossPlatform / TestGateFlag / TestEventBus | Done (v0.2.0, 2026-08-13/14; macOS live removed per SCR-032) |

### 7.6 Terminal Write Discipline — SCR-034 unified (A1-A14 Done; A15 live replay pending 30.12)

| # | [A#] | Criterion | Test class | Status |
|---|------|-----------|-----------|--------|
| 1 | A1 | feedback/06 four command cases: no block-tier false positive | TestTerminalFront | Done (2026-08-22) |
| 2 | A2 | `grep pkg>=1.0` unquoted: no block + `=` residue -> terminal-write-uncertain event | TestTerminalFront | Done (2026-08-22) |
| 3 | A3 | multi-line heredoc with bare `>=`: no pseudo-target; real writes caught by audit | TestTerminalFront + TestWriteAuditKernel | Done (2026-08-22) |
| 4 | A4 | chain-aware extraction (true positive `echo hi && touch`; segment scoping `cp a b && ...`) | TestTerminalFront | Done (2026-08-22) |
| 5 | A5 | device exemption before normalization, no events, no stuck `;` | TestTerminalFront | Done (2026-08-22) |
| 6 | A6 | audit kernel four states + dir-mtime ignore | TestWriteAuditKernel | Done (2026-08-22) |
| 7 | A7 | violation classification (allowlist/exempt/session/.git vs unprotected root; delete record-only) | TestWriteAuditKernel | Done (2026-08-22) |
| 8 | A8 | L1 fire-once notice (once per violation; error/non-string untouched) | TestWriteAuditNotice | Done (2026-08-22) |
| 9 | A9 | L3 settlement gate block + reopen | TestWriteAuditGate | Done (2026-08-22) |
| 10 | A10 | cross-layer heredoc: front no-block + audit catches | TestTerminalFront + TestWriteAuditKernel + TestWriteAuditGate | Done (2026-08-22) |
| 11 | A11 | session scoping + subagent inheritance + parent delegation exempt | TestWriteAuditSession | Done (2026-08-22) |
| 12 | A12 | performance p95 < 10ms (≤500); entry-cap skip + WARNING | TestRegression + TestWriteAuditConfig | Done (2026-08-22) |
| 13 | A13 | config wiring: disable, cap configurable, OSError fail-open | TestWriteAuditConfig | Done (2026-08-22) |
| 14 | A14 | stats/event integration + privacy; L1 not an event | TestWriteAuditEvents + TestStatsJsonl (extended) | Done (2026-08-22) |
| 15 | A15 | regression full suite + stats.jsonl replay (live) | All + Live phase (section 6) | Done (auto: 493 passed / 5 skipped, 2026-08-22; live 30.12: /dev/null 117-event noise eliminated, feedback/06 cp-chain + heredoc replay clean, L1 ordering fix re-verified) |

### 7.7 v0.4.0 Architecture Refactor — SCR-035 (all Done; behavior frozen, structural criteria)

Legend: task ids map to `docs/scr-035-impl-plan.md` / tasks.md Phase R.
Every row also requires the FULL suite green (baseline re-measured at 31.3)
and verbatim rule_key/message-text comparison against spec v2.3.

| # | Criterion | Test class / evidence | Task | Status |
|---|-----------|----------------------|------|--------|
| 1 | Parser drift exposed & resolved; test path = production path (pyyaml in venv) | full suite run log; `parser-drift:` fixes | 31.1 | Done (2026-08-24: 1 drift fixed, commit 9fc97d8) |
| 2 | Single module instance via conftest package loading; flat sys.path blocks gone | conftest.py + import sweep | 31.2-31.3 | Done (2026-08-24: conftest alias dirwhip, commit bd85dac) |
| 3 | Fallback parser removed; fail-open degradation path intact (parse failure -> None -> chain continues) | TestGuardConfig / resolver rows | 31.4 | Done (2026-08-24: commit c6bd1e6) |
| 4 | terminal.py / paths.py extracted as pure functions; zero behavior change | existing terminal/normalization classes green unchanged | 31.5-31.6 | Done (2026-08-24: commits 4f20133 + 8b882b4) |
| 5 | stats.py / report.py extracted; display separated from config | TestStats* / TestCommands green unchanged | 31.7-31.8 | Done (2026-08-24: commits 88623b3 + 0d5ccc5) |
| 6 | state.py three containers + reset_all(); ~10 hand-cleared fixtures replaced | TestStateContainer | 31.9 | Done (2026-08-24: commit fe1b053) |
| 7 | events.py five-param emit; 14 call sites migrated; stats+log+bus fanout internal | existing event classes green unchanged | 31.10 | Done (2026-08-24: commits 759b2e4 + a45fc18) |
| 8 | sessions.py owns child tracking + audit parent links; no cross-module global access | TestSubagentHooks / TestWriteAuditSession green | 31.11 | Done (2026-08-24: commits b33055f + 30fdd66) |
| 9 | audit.py extracted; classifier injected (`set_classifier`); no audit->guard import | TestWriteAudit* green; import scan | 31.12 | Done (2026-08-24: commit 8bf146a) |
| 10 | verdict.py + __init__ assembly; dir_whip.py dissolved; fail-open try/except only in __init__ | try/except sweep; all guard classes green | 31.13 | Done (2026-08-24: commit 788f066) |
| 11 | P6: zero `__file__` dependence in message/report paths (4 sites precomputed at register) | rg `__file__` sweep + patched-path tests updated | 31.13 | Done (2026-08-24: commit 788f066) |
| 12 | Core modules import no host APIs | TestCoreImportSurface | 31.13 | Done (2026-08-24: commit 788f066) |
| 13 | Parity contract: dual resolution implementations locked by shared vectors | TestParityResolution (8 tests = 5 resolution vectors + 3 normalization methods over 8 case rows) | 31.14 | Done (full suite 504 passed / 5 skipped, 2026-08-24; expected values measured on BOTH sides against identical fake homes) |
| 14 | spec 5.1 directory figure revised (v2.4 activate-edit-refreeze); README positioning; version bump 0.4.0 | spec changelog + plugin.yaml | 31.15 | Done (2026-08-24: plugin.yaml 0.4.0, README EN/ZH positioning + badge, docs sync; full suite 504 passed / 5 skipped) |

### 7.8 v0.4.2 — SCR-037 B2 (spec v2.6 single-key `allowlist` + hygiene)

Spec 5.6 D1 single-key `allowlist: []` (B2 clean break — `exempt_paths` + `allowed_root_files` deleted, no compat; discriminated `file:<basename>` | `prefix:<abs-path>`), 5.7 `/dir-whip allow|remove|list` intelligently discriminates file vs prefix (ADR-0008 B2), 5.3 Tier0 = allowlist `prefix:` OR runtime-allowlist, root file = allowlist `file:`, 5.18 audit reads `allowlist` `file:` | `prefix:` subsets. Dashboard file-tree deferred to SCR-038.

| # | Criterion | Test class / evidence | Task | Status |
|---|-----------|----------------------|------|--------|
| 1 | Shipped `dir-whip/` tree has zero agent-config literals (`AGENTS.md`/`CLAUDE.md`/`.cursorrules`/`.clinerules`); `hermes plugins install` scan passes (not dangerous) | TestReleaseHygiene | 37.3 | Pending |
| 2 | Shipped template default is `allowlist: []` (strict empty, discriminated `file:` | `prefix:`; old keys `exempt_paths` / `allowed_root_files` deleted) | TestReleaseHygiene | 37.3 | Pending |
| 3 | `/dir-whip` bare renders merged report with `Allowlist: Files: ...  Prefixes: ...` (or `Allowlist: (strict empty allowlist)` when key missing); `allow`/`remove`/`list` subcommands work, unknown -> `Usage: /dir-whip [allow|remove|list]` | TestAllowlistUnified | 37.11 (was 37.5) | Pending |
| 4 | `allow` without args lists numbered root-file candidates (excludes session dirs / already-allowlisted `file:` entries; prefix candidates not listed) | TestAllowlistUnified | 37.11 | Pending |
| 5 | `allow <file|prefix:PATH|PATH/>` / `allow 1,3` / `remove <file|prefix:PATH|PATH/>` mutate persistent `allowlist` via row-level edit (comments preserved, missing-key append, `file:` vs `prefix:` discrimination: no slash -> `file:<basename>`, slash or `prefix:` tag -> `prefix:<abs-path>`, trailing `/` normalized, duplicate idempotent) | TestAllowlistUnified | 37.11 | Pending |
| 6 | Invalid file names (slash, `..`, empty) and invalid prefixes (non-absolute, `..`, empty, `prefix:` with relative) rejected; no mutation | TestAllowlistUnified | 37.11 | Pending |
| 7 | Mutation narrowly refreshes cache so next `classify` / `audit_classify` sees new allowlist (both file and prefix tiers) | TestAllowlistUnified | 37.10 | Pending |
| 8 | `audit_workspace.py` precheck reports three states (`enabled` / `not-enabled` + guidance / `disabled`) without changing exit code | TestPluginEnablementPrecheck | 37.6 | Pending |
| 9 | Full suite green (baseline 504 passed / 5 skipped re-measured at 37.2; B2 re-measured after 37.10) | All | 37.12 | Pending |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-08-22 | v0.3.0 rebuild: spec source v2.0 -> v2.3 (SCR-033 + SCR-034 unified; section 7.6 added). New classes TestTerminalFront / TestWriteAuditKernel / TestWriteAuditNotice / TestWriteAuditGate / TestWriteAuditSession / TestWriteAuditConfig / TestWriteAuditEvents; TestStatsJsonl / TestRegression extended. New fixture: fake workspace root + snapshot determinism via os.utime (no sleeps). New scenario matrices (front layer A1-A5/A10-front/B-guards; audit A6-A14). Live phase additions (L1/L3 end-to-end, feedback/06 replay, 675-line stats.jsonl replay). Acceptance matrix 7.6 rows 1-15 all Pending. v0.2.0 matrix rows retained as Done basis |
| 2026-08-22 | spec v2.2/v2.3 sync: SCR-034 spec revision (5.18 + 5.6/5.13/5.14) recorded in spec changelog; 7.6 acceptance criteria added (A1-A15), version 2.2 -> 2.3 re-frozen |
| 2026-08-22 | SCR-034 implementation acceptance (tasks 30.1-30.10): matrix 7.6 rows 1-14 (A1-A14) all Done via TestTerminalFront (20) + TestWriteAuditKernel (15) / Session (5) / Config (4+9) / Notice (8) / Gate (10) / Events (6) + TestRegression perf; row 15 (A15) auto part Done — full suite 492 passed / 5 skipped (incl. manifest reconciliation: manifest_version kept deleted per user decision, plugin.yaml 8 hooks / 7 emits, TestManifestV2 + TestRenameRegression synced); live stats.jsonl replay remains pending 30.12 |
| 2026-08-22 | 30.12 live verification (SCR-034, user-gated phase): L1/L3 end-to-end (root write → audit → L1 notice → L3 gate); feedback/06 19:37 cp-chain replay (external-write allow, `up` false target gone) + 23:04 heredoc replay (blanket demotion, `=` false target gone); /dev/null zero events (baseline 117-event noise confirmed eliminated); stats.jsonl replay (baseline analysis confirmed noise distribution, live re-run clean); **L1 hook-ordering bug found + fixed**: Hermes fires transform_tool_result BEFORE post_tool_call for terminal, so the audit re-scan moved into transform_tool_result (post stays an order-agnostic no-op); regression test added `TestWriteAuditNotice::test_transform_runs_audit_before_post_notice_appears`; full suite 493 passed / 5 skipped; matrix row 15 (A15) live part Done |
| 2026-08-24 | v0.4.0 pre-registration (SCR-035, docs-only — no code/test files created yet): header marked "in force for v0.4.0"; §2 directory structure gains conftest.py (real-package alias `dirwhip`) + planned test_state_container.py / test_parity_resolution.py; new classes TestStateContainer / TestCoreImportSurface / TestParityResolution with code samples; state-reset fixture pattern (`state.reset_all()` replacing ~10 hand-cleared globals); method-name examples added; acceptance matrix §7.7 (14 rows, all Pending, mapped to tasks 31.1-31.15). Rows flip to Done as impl-plan tasks complete | SCR-035 (2026-08-24) |
| 2026-08-24 | Task 31.14 executed + record correction: TestParityResolution landed as 8 tests (test_resolution_parity parametrized over the five feedback/08 §9.3 vector shapes + three normalization methods covering MSYS / /c// //c/ / Cygwin / case / slash direction / absolute dot segments / backslash UNC root / foreign-drive rows); full suite 504 passed / 5 skipped. Matrix row 13 had been pre-flipped to Done citing "15 tests = ... / 511 passed" by a run that left NO test file on disk (two empty commits f24b48b and 672cd20 with the phase message remain in history); row corrected to measured values. Measured out-of-contract divergences documented in the test docstring, not asserted: paths.normalize_target drive-inherits from working_dir_root (also onto relative targets), workspace_resolver.normalize_path inherits from the process cwd (absolute rooted-no-drive only); on POSIX normalize_path is normpath identity so normalization rows are Windows-host guarded | SCR-035 task 31.14 (2026-08-24) |
| 2026-08-25 | SCR-037 v0.4.1 pre-registration (docs-only): `docs/scr-037-plan.md` (merged feedback/09 + scanner block + allowlist command, 0.4.1去Dashboard), spec 5.6/5.7 v2.5 ACTIVE, engineering-constraints red line (four literals), feedback/09 errata (二*), ADR-0008 (D1-D4). Testing-standards §2 gains `test_release_hygiene.py`, new classes TestReleaseHygiene / TestAllowlistCommand / TestPluginEnablementPrecheck with method-name examples, acceptance matrix §7.8 (9 rows, all Pending, mapped to tasks 37.3-37.7). Rows flip to Done as impl tasks complete | SCR-037 (2026-08-25) |
| 2026-08-25 | v0.4.2 B2 single-key allowlist (BREAKING, spec v2.6): `exempt_paths` + `allowed_root_files` removed as config keys (no backward compat, B2 clean break per user 2026-08-25); single key `allowlist: []` with discriminated `file:<basename>` | `prefix:<abs-path>` (prefix may end with `/`; bare no-slash -> `file:`, bare with slash -> `prefix:`). Testing-standards header v0.4.1->v0.4.2, spec source v2.5->v2.6; §2 test_config comment `TestAllowlistUnified`, §3 v0.4.1->v0.4.2 TestAllowlistUnified (file:/prefix: discrimination, Files/Prefixes list, invalid prefix handling, cache refresh for both tiers), fixture Guard config `allowlist`, §5 audit matrix `allowlist file` | `prefix:` split, §7.8 rows 1-9 revised to single allowlist (row 2: `allowlist: []`, rows 3-7: file/prefix discrimination, list Files/Prefixes, invalid prefix, mutation refresh) | SCR-037 amendment v2.6 B2 (2026-08-25 user decision) |

---

_Test-standards history: v0.1.0 archived at `archive/v0.1.0/` (test-evaluation-cases.md);
v0.2.0 archived at `archive/v0.2.0/testing-standards.md`._