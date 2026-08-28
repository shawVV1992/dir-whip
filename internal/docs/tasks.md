# Tasks (v0.4.2)

Task tracker for the v0.4.2 line. Supersedes the v0.4.0 line (Phase R
31.x complete 2026-08-24, released as v0.4.0 @ 8dc3549; SCR-035 refactor
complete, 504 passed). Source of truth: spec v2.6 ACTIVE
(`internal/specs/dir-whip-spec.md`; behavior basis = frozen v2.4 clauses +
5.6 D1 `allowlist: []` (`file:<name>` | `prefix:<abs-path>`, B2 clean break —
`exempt_paths` + `allowed_root_files` deleted, no compat) + 5.7 `/dir-whip
allow|remove|list` intelligently discriminated + 5.3/5.18 single-key) +
engineering-constraints.md (release-hygiene red line, SCR-037 B2) + SCR-037
plan `docs/scr-037-plan.md` (§3.8 B2) + testing-standards.md v0.4.2 (7.8 B2).
Change management: spec-changes.md (register). Task numbering continues
the v0.4.0 series (last: 31.x).

## Phase R — SCR-035 v0.4.0 architecture refactor (31.x)

Registered 2026-08-24 (design confirmed, branch 0.4.0_dev created). Task
breakdown in `docs/scr-035-impl-plan.md` (authoritative for steps/symbol
inventories); design baseline `docs/scr-035-plan.md`. Each phase = full
suite green + one commit (`refactor(p<N>): ...`).
Spec basis: behavior = frozen v2.3 clauses; spec itself v2.4 ACTIVE
(5.1 directory figure revised to the target layout on 2026-08-24; re-freeze
at task 31.15).

| # | Task | Phase | KPI | Status |
|---|------|-------|-----|--------|
| 31.1 | venv install pyyaml==6.0.3, expose parser drift | 1 | drift findings logged as `parser-drift:` fixes | Done (2026-08-24: 1 drift fixed, commit 9fc97d8) |
| 31.2 | conftest real-package loading (alias `dirwhip`) + test import migration | 1 | full suite green, single module instance | Done (2026-08-24: conftest alias dirwhip; 2 files migrated, 4 skills-subtree untouched) |
| 31.3 | baseline re-measure + testing-standards sync | 1 | new baseline recorded: 493 passed / 5 skipped (2026-08-24) | Done |
| 31.4 | delete hand-written YAML fallback parser | 2 | dual-parser gone; fail-open path intact | Done (2026-08-24: commit c6bd1e6) |
| 31.5 | extract terminal.py lexer/tiering | 3 | full suite green | Done (2026-08-24: commit 4f20133) |
| 31.6 | extract paths.py normalization/resolution | 3 | full suite green | Done (2026-08-24: commit 8b882b4) |
| 31.7 | extract stats.py counters/jsonl/rollover | 4 | full suite green | Done (2026-08-24: commits 88623b3 + 6acbf52) |
| 31.8 | extract report.py command surface | 4 | full suite green | Done (2026-08-24: commit 0d5ccc5) |
| 31.9 | state.py three containers + reset_all() + anti-degradation test | 5 | ~10 hand-cleared fixtures -> one call; TestStateContainer green | Done (2026-08-24: commit fe1b053) |
| 31.10 | events.py deep module, five-param emit | 6 | 14 call sites migrated | Done (2026-08-24: commits 759b2e4 + a45fc18) |
| 31.11 | sessions.py child tracking + audit parent links | 6 | full suite green | Done (2026-08-24: commits b33055f + 30fdd66) |
| 31.12 | audit.py write-audit layer, classifier injection | 6 | no audit->guard import; cycle-free | Done (2026-08-24: commit 8bf146a) |
| 31.13 | verdict.py + __init__ assembly, dissolve dir_whip.py, P6 four-site fix | 6 | fail-open single layer; zero `__file__` in message paths; verbatim text diff clean | Done (2026-08-24: commit 788f066) |
| 31.14 | parity contract tests (test_parity_resolution.py) | 6.5 | shared vectors lock dual implementations | Done (2026-08-24: parity 8 tests / 504 passed, commit 0a6cbc5) |
| 31.15 | spec re-freeze pre-done (v2.4 FROZEN 2026-08-24) + README positioning + bump 0.4.0 + docs sync | 7 | acceptance criteria scr-035-plan §十 1-5 all pass | Done (2026-08-24: plugin.yaml 0.4.0, README EN/ZH positioning + badge, AGENTS.md/testing-standards.md sync, stale docstring cleanups; full suite 504 passed / 5 skipped) |

---

## Phase 6 — SCR-034 unified terminal write discipline (30.x) — COMPLETE

TDD per task: write the mapped tests FIRST (red), then implement (green).
Acceptance = mapped testing-standards v0.3.0 classes all green + KPI column +
spec 7.6 [A#] criterion. SCR-033 is consolidated into SCR-034 — the front
layer below IS the SCR-033 component; the audit layer is the SCR-034 backbone.

### Dependency graph

```
FRONT LAYER (SCR-033 component)              AUDIT LAYER (SCR-034 backbone)
─────────────────────────────                ────────────────────────────────
30.1 tokenizer + chain split                 30.4 snapshot + diff kernel
        |                                            |
30.2 = / device / heredoc                    30.5 classify + session state
        |                                            |
30.3 front regression [A1/A2/A4/A5]                 |
        |                                            |
        +----------------------+---------------------+
                               |
                    30.6 hook wiring (pre/post + session lifecycle + fail-open)
                               |
              +----------------+----------------+
       30.7 L1 notice    30.8 L2 + L3 gate   30.9 config/guardrails
          [A8]              [A9/A14]            [A12/A13]
              +----------------+----------------+
                               |
                    30.10 full regression [A13/A15]
                               |
              +----------------+----------------+
       30.11 matrix       30.12 live        30.13 version bump
        A1-A15 Done      (user-gated)        0.2.0 -> 0.3.0
```

Parallelizable: 30.1 and 30.4 are independent roots; the front track
(30.1-30.3) and audit track (30.4-30.5) proceed in parallel until they merge
at 30.6. Within a track, rows are sequential.

### Tasks

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 30.1 | **前置层·切词器与链切分**：`;` 独立分隔符词元；`&&`/`;`/`\|`/换行/裸 `&` 为链边界（引号感知，裸字符串切分禁止）；`_terminal_block_targets` 改按段提取（redirect/touch/cp-mv 只在含写入命令的段内查找，绝不跨链边界） | - | TestTerminalFront：`test_semicolon_emits_separator_token` / `test_targets_never_extracted_across_chain_boundaries` / `test_quoted_and_not_a_chain_boundary` 红→绿；链式 `cp a b && echo hi` 只取 `b` | Done (2026-08-22) |
| 30.2 | **前置层·`=`/设备/heredoc**：`=` 前缀重定向残留 → 降 `terminal-write-uncertain` 事件（不静默丢弃）；`/dev/null`+`/dev/stdout`+`/dev/stderr` 归一化前豁免（无 verdict/stats 事件、无 `E:\dev\null` 盘符继承、无 `;` 粘连目标）；含 `<<` 命令整体降 uncertain 档（不解析正文） | 30.1 | TestTerminalFront：`test_equals_residue_routes_to_uncertain_event` / `test_dev_null_exempt_before_normalization_no_event` / `test_dev_null_stuck_semicolon_has_no_target` / `test_heredoc_blanket_demotion_no_body_parse` 红→绿 | Done (2026-08-22) |
| 30.3 | **前置层·回归用例**：feedback/06 四条存档命令 block→allow/external-write（`up`/`bak_cdp`/echo 尾词/heredoc `=` 伪目标全消失）；链式真阳性 `echo hi && touch 根文件` 拦截；`grep pkg>=1.0` 零 block | 30.1, 30.2 | TestTerminalFront [A1/A2/A4/A5 + A10 前置侧] 全绿；feedback/06 四案逐条断言 | Done (2026-08-22) |
| 30.4 | **审计内核·快照与 diff**：`snapshot(root)`（os.scandir，记录 name/size/mtime_ns/is_dir）；`diff_snapshots` 恰好四态（新增/修改/删除/无关）；**仅文件条目**判违规（目录 mtime 变化忽略）；entry_cap 计数 | - | TestWriteAuditKernel：`test_audit_diff_detects_new_modified_deleted_unrelated` / `test_directory_mtime_change_ignored` 红→绿；os.utime 控制确定性（零 sleep） | Done (2026-08-22) |
| 30.5 | **审计内核·判定与会话状态**：差异集经共享分类链（`normalize_target`+`classify_target`）；违规 = 白名单/exempt/会话目录外的新增或修改根级文件；删除仅记账；会话级快照状态（pre/post 配对、pre 已 block 则不做 post 快照） | 30.4 | TestWriteAuditKernel：`test_new_root_file_outside_allowlist_violation` / `test_session_dir_content_write_not_violation` 红→绿；`.git/`/白名单/exempt 不判违规 | Done (2026-08-22) |
| 30.6 | **钩子接线**：pre_tool_call(terminal) 前置检查通过后快照；post_tool_call(terminal) 重扫+diff+判定+违规集；会话状态生命周期（顶层会话起始清空、child_session_ids 门、子会话继承父锁存）；扫描 OSError 静默 fail-open | 30.2, 30.5 | TestWriteAuditSession + TestWriteAuditKernel 接线用例红→绿；无异常逃逸钩子（TestRegression） | Done (2026-08-22) |
| 30.7 | **L1 fire-once 通告**：注册 `transform_tool_result` 钩子；违规首次发现时向 terminal 结果追加一次通告（路径+处置指引）；同一违规不重复追加；错误结果不加装饰；非字符串结果不动 | 30.6 | TestWriteAuditNotice：`test_notice_appended_exactly_once_fire_once` / `test_error_result_not_decorated` 红→绿 [A8] | Done (2026-08-22) |
| 30.8 | **L2 记账 + L3 清偿闸门**：verdict 事件 `write-audit-violation`/`write-audit-gate-block` 流入 5.13 统计 + 5.14 事件总线（`dir-whip:write-audit:*`，隐私相对路径）；未清偿违规时下一次写入类工具（write_file/patch/terminal）pre 拦截（标准 block 通道，消息列路径+处置）；重扫发现已消失/合法化则闸门重开 | 30.6 | TestWriteAuditGate + TestWriteAuditEvents：`test_gate_blocks_next_write_class_tool` / `test_gate_reopens_after_file_removed` / `test_write_audit_event_privacy_relative_target` 红→绿 [A9/A14]；L1 通告本身不产生事件 | Done (2026-08-22) |
| 30.9 | **配置与护栏**：`write_audit` 开关（关闭即无快照/diff/事件/闸门）；`write_audit_entry_cap`（默认 2000，可配置）；根条目超限跳过审计 + 一次性 WARNING；性能预算测试（≤500 条目 p95 < 10ms，宽松上限断言） | 30.6 | TestWriteAuditConfig + TestRegression：`test_write_audit_disabled_no_events_no_gate` / `test_entry_cap_exceeded_skips_audit_one_warning` / `test_audit_scan_oserror_fails_open_silent` 红→绿 [A12/A13] | Done (2026-08-22) |
| 30.10 | **全量回归**：pytest 全绿（本机 Windows）；TestStatsJsonl 扩展（write-audit-* rule_key 入 schema、is_subagent 切分）；TestRenameRegression 不退化；测试类表零 Pending | 30.1-30.9 | 全量 pytest 通过数与清单一致 [A13/A15]；stats 事件结构（terminal-*/write-audit-* 命名空间）测试更新 | Done (2026-08-22; 492 passed / 5 skipped) |
| 30.11 | **验收矩阵 A1-A15 → Done**：testing-standards.md v0.3.0 矩阵 7.6 行 1-15 逐条勾 Done（附测试类与日期）；spec 7.6 对应勾选 | 30.10 | testing-standards 验收矩阵 7.6 全勾；7.1-7.5 v0.2.0 基线保持 Done | Done (2026-08-22) |
| 30.12 | **真机验证（user-gated）**：L1/L3 端到端（`-z` 会话 heredoc 根写 → fire-once 通告 → 移走文件闸门重开）；feedback/06 19:37/23:04 两现场重放；675 行 stats.jsonl 回放（external-write 183→~11、write-audit-* 与真实根写一致）；transform_tool_result 真机通道核验 | 30.10 | 真机阶段（testing-standards §6 v0.3.0）[A15]；用户确认后执行 | Done (2026-08-22: L1/L3 端到端 + feedback/06 两现场重放（cp 链 external-write、heredoc 降档）+ /dev/null 零事件 + stats 回放全通过；**发现并修复 L1 钩子时序 bug**（transform 先于 post，审计重扫移入 transform_tool_result，回归测试 + 全量 493 passed / 5 skipped；spec 5.18 EN/ZH + changelog 已同步） |
| 30.13 | **版本提升与发布**：plugin.yaml 0.2.0 → 0.3.0；README EN/ZH 版本串/徽章同步；after-install.md 版本；提交（git）；通知用户触发 Hermes 同步 | 30.11（+30.12 建议先行） | 版本号全仓一致 0.3.0；git 提交；用户收到同步通知 | Planned |

---

## Phase 7 — SCR-037 v0.4.2 single-key allowlist B2 (37.x)

TDD per task: write the mapped tests FIRST (red), then implement (green).
Acceptance = mapped testing-standards v0.4.2 classes all green + KPI column +
spec 7.8 [A#] criterion (v2.6 B2 single-key `allowlist`). Active branch:
`0.4.1_dev` (continues for 0.4.2, from `main@8dc3549`).

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 37.1 | **文档批**：分支 `0.4.1_dev` + 登记行（spec-changes.md）+ `scr-037-plan.md` + `feedback/09` 勘误 + `engineering-constraints.md` 红线 | - | Docs batch committed `ccb9e9d` | Done (2026-08-25) |
| 37.2 | **spec v2.5 激活 + v2.6 B2 修正**：`internal/specs/dir-whip-spec.md:420` / `specs/dir-whip-spec-zh.md:367` D1 `["AGENTS.md"]`->`[]` + 5.6 例块 + 5.7 `/dir-whip allow|remove|list`（回退 SCR-029，`report.py:230`）+ changelog v2.5 ACTIVE; **amendment v2.6 B2** 单键 `allowlist: []` (`file:<basename>` | `prefix:<abs-path>` , prefix 可带 `/`) clean break — `exempt_paths` + `allowed_root_files` 删除, 无兼容, 5.6/5.3/5.7/5.18/报告/审计对齐, spec v2.5->v2.6 ACTIVE | 37.1 | Spec v2.6 ACTIVE (re-freeze at 37.12) | Done (2026-08-25: EN 4 edits, ZH 4 edits; v2.6 amendment committed, spec-changes updated) |
| 37.3 | **模板净化 + 卫生测试**：`dir-whip/dir-whip-config.yaml:19` -> `allowlist: []` (discriminated `file:` | `prefix:` , old keys `exempt_paths` / `allowed_root_files` 删除, 无兼容) 使 `TestReleaseHygiene` 转绿 | 37.2 | `tests/test_release_hygiene.py:58` 2 tests green (`TestReleaseHygiene` strict empty `allowlist`) | Todo |
| 37.4 | **部署/README 文档**：`after-install.md`/`deployment.md:106`/`README*.md:110,207` 白名单核对 + 手工拷贝不支持 + 模板 `[]` 说明 (已为 `allowlist`) | 37.3 | Docs green | Todo |
| 37.5 | **共享 `config_writer` + slash 命令 (B2 单键)**：行级 YAML 编辑保注释 + 窄缓存刷新 + `/dir-whip allow|remove|list` 单键 `allowlist` (`file:` vs `prefix:` 智能判别, `prefix:` 尾 `/` 归一, `list` 显示 Files/Prefixes) (`config_writer.py` + `report.py:230` 分支, `args_hint` 菜单; old keys 删除) | 37.3 | `TestAllowlistUnified` green, `TestReleaseHygiene` stays green | Todo |
| 37.6 | **`audit_workspace.py` 启用预检**：布局感知内联 + 三态 `enabled/not-enabled/disabled` 告警 (读取 `allowlist` `file:` 子集) | 37.5 (now 37.11) | `TestPluginEnablementPrecheck` green | Todo |
| 37.7 | **全量回归 + bump**：`plugin.yaml:0.4.2` + `pytest 504 passed/5 skipped` 基线 (B2 后重测) | 37.3-37.6 (now 37.9-37.12) | Full suite green | Todo |
| 37.8 | **发布 + 真机**：tag `v0.4.2` + GitHub Release；PM `hermes plugins install --force` 正规重装验证（A1） | 37.7 | Release Done | Todo |
| 37.9 | **allowlist.py 抽取**：新建 `dir-whip/allowlist.py` 纯函数模块 (`parse_allowlist`, `is_allowlist_file`, `is_allowlist_prefix`, `normalize_allowlist_entry`) — `file:` 仅 basename 校验, `prefix:` 绝对路径 + casefold + 尾 `/` 归一, 裸条目兼容 (无 slash -> `file:`, 有 slash -> `prefix:`); 零旧键兼容, 旧键出现即忽略/删除 | 37.2 | `allowlist.py` exists, `TestAllowlistUnified` parsing helpers green (or new `TestAllowlist` unit) | Pending |
| 37.10 | **cache/verdict/report/audit 统一**：`config.py` 缓存/解析、`verdict.py` guard Tier0 `prefix:` OR runtime-allowlist / root file `file:`、`report.py` `Allowlist: Files: ...  Prefixes: ...` 行、`audit.py` `audit_classify` 均改读 `allowlist` `file:` | `prefix:` 子集; 旧键 `exempt_paths`/`allowed_root_files` 删除路径验证; 窄缓存刷新 (`reset_cache` 不再过宽, 仅刷 allowlist 读取路径) | 37.9 | `TestAllowlistUnified` verdict/report/audit tiers green; `TestWriteAuditKernel` file/prefix split green | Pending |
| 37.11 | **config_writer B2 行级编辑**：`config_writer.py` 改写 `allowlist:` 单键 flow list, `file:`/`prefix:` 智能判别 (无 slash -> `file:`, slash/`prefix:` -> `prefix:`), 非绝对 prefix / `..` / 空 / file 含 slash 拒绝, 评论保留, 缺键追加, 去重幂等, 旧键删除 | 37.10 | `TestAllowlistUnified` file/prefix discrimination, `list` Files/Prefixes, invalid file/prefix, mutation refresh (both tiers) green | Pending |
| 37.12 | **SKILL prompt 硬化 (占位)**：`SKILL.md` / discipline prompt 加 allowlist 前缀指引 (文件 vs 目录) + 报告行示例; 单独任务, 不阻塞 37.9-37.11 | 37.11 (or 37.10) | Prompt docs green | Pending |

ADR & testing-standards supplement (37.1-continued, committed `5373656` + B2 amendment 2026-08-25):
`adr/0008` (D1-D4 + B2 amendment), `testing-standards.md` v0.4.2 (TestReleaseHygiene/TestAllowlistUnified/TestPluginEnablementPrecheck + 7.8 B2) + TDD samples (`test_release_hygiene.py` red, `test_allowlist_and_precheck_sample.py` red until 37.3/37.11; B2 discriminated samples `file:`/`prefix:` pending 37.9-37.11).

---

## Phase 8 — SCR-039 v0.5.0 提示通道重排 + 同轮自愈 + 结构化 allowlist (39.x)

TDD per task: write the mapped tests FIRST (red), then implement (green).
Acceptance = mapped testing-standards v0.5.0 classes all green + KPI column +
spec 7.9.x criteria (v2.7). Active branch: `0.5.0_dev` (from `main@22cf0e5`).
Design source: feedback/10 + `docs/scr-039-plan.md`（R1-R9 定稿设计）。

**需求拆分组织（2026-08-26）**：任务按 REQ 分组（39.R1-R5），与
testing-standards 测试文件（test_teaching_channel / test_settle_selfheal /
test_report_reminder / test_allowlist_commands）及 scr-039-plan 五节一一对应；
收尾任务 39.9/39.10；R8 登记行保持 39.x。

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.1 | **文档批**：spec-changes 登记 + `scr-039-plan.md` + spec EN/ZH v2.6->v2.7 ACTIVE（3.7/5.3/5.4/5.6/5.7/5.11/5.17/5.18 + 2/4/6/7/8 扫描）+ changelog + feedback/10 同步 + 需求拆分重构（测试/标准/tasks 三对齐） | - | Docs batch done | Done (2026-08-26) |

### REQ-1 教导通道重排（R1-R3，test_teaching_channel.py，spec 3.7/5.3/5.4/5.17）

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.R1.1 | **red**：TestReminderMessageV27（候选 A verbatim + `len<=280` + register 无 prompt-section + DISCIPLINE_PROMPT 移除）/ TestConditionalInjection（谓词单测 + on_start 注入矩阵 + reminder_status 四态）/ TestBlockMessageV27（两要素 + 相对 dirs 提示 + 子代理不含新行）；test_dir_whip.py 旧锁同步（reminder 文本 / TestDisciplinePrompt 通道移除 / block 全文） | 39.1 | 红灯确认（REQ-1 组） | Done (2026-08-26) |
| 39.R1.2 | **green**：verdict.py（删 DISCIPLINE_PROMPT、REMINDER 换候选 A、discipline_applies 谓词、block 补全两要素）、state.py（agent_cwd_fn + reminder_status）、__init__.py（移除 prompt-section 注册、装填 agent_cwd_fn、on_start 条件注入三步） | 39.R1.1 | REQ-1 全绿 | Done (2026-08-26, 95d7731) |

### REQ-2 同轮自愈（R4-R5，test_settle_selfheal.py，spec 5.18）

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.R2.1 | **red**：TestSettleTool（pending 约束/隔离区搬移结算/子代理拒绝/fail-open/相对路径/幂等/闸门 settle 行/stats rule_key/懒注册）+ TestPreVerifyHook（nudge 条件矩阵/register 接线） | 39.1 | 红灯确认（REQ-2 组） | Done (2026-08-26) |
| 39.R2.2 | **green**：audit.py（settle_paths + L1 文案升级 + L3 消息补 settle 行 + write-audit-settle stats + 懒注册挂接）、__init__.py（settle schema+handler、pre_verify 适配器 + register_hook） | 39.R2.1 | REQ-2 全绿 | Done (2026-08-27, 5aee036) |

### REQ-3 报告可观测（R6，test_report_reminder.py，spec 5.7）

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.R3.1 | **red + green**（小项合一）：TestReportReminderLine 四态（injected/skipped-outside/skipped-child/unavailable）+ report.py 渲染 Reminder 行 | 39.R1.1 | REQ-3 全绿 | Done (2026-08-27, 21fc63c) |

### REQ-4 项目模式豁免（R7，spike 门控，spec 5.4）

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.R4.0 | **spike**：宿主源码核实 `pdb.get_active_id(conn)` 的 conn 来源与项目路径字段（project_tools.py + 项目存储，30 分钟窗口） | 39.1 | pdb 签名钉死 or 降级裁定 | Done (2026-08-27, spike 通过：projects_db.py:656 get_active_id(conn) / connect_closing() per-profile projects.db) |
| 39.R4.1 | （spike 通过）red + green：项目活动态注入槽 + CWD∈项目路径跳过注入（skipped-project）+ 测试文件按需建；不可行则降级登记（维持 CWD∈root 判定） | 39.R4.0 | REQ-4 闭合（或降级登记） | Done (2026-08-27, 32e71be) |

### REQ-5 结构化 allowlist 与命令统一（R9，test_allowlist_commands.py，spec 5.6/5.7）

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.R5.1 | **red**：TestAllowlistStructured（映射解析/多级递归匹配/校验拒绝/遗留忽略/format 往返）+ TestCommandsUnified（两段式连续编号/候选枚举排除规则/数字与名称判别/**绝对路径归一/根外与祖先拒绝消息/不存在确认提示/--create 建目录与建文件/幂等**/裸 remove 枚举/双集合名称删除/list 对齐+遗留提示/空态） | 39.1 | 红灯确认（REQ-5 组） | Done (2026-08-26) |
| 39.R5.2 | **green**：allowlist.py 结构化重构（parse/format/match 需拼根，约 4 调用点）、config_writer.py（映射双子列表 flow 风格编辑保注释）、report.py（_list_candidates 扩目录 + 三命令 Files/Dirs 统一渲染 + **输入归一化与 --create 确认-创建协议** + R1-R8 流程） | 39.R5.1 | REQ-5 全绿 | Done (2026-08-27, 7e0c0d2) |
| 39.R5.3 | **迁移**：audit_workspace.py allowlist 解析适配映射形态、workspace_resolver.py parity 向量扩展（allowed-root-file 形态）；三处旧测试迁移（TestReleaseHygiene 模板默认值 / test_config.py TestAllowlistUnified 平铺用例 / test_dir_whip.py `_configure` helper legacy 翻译废弃） | 39.R5.2 | 全量绿（含迁移后） | Done (2026-08-27, 397e694) |

### 收尾

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 39.9 | **回归 + bump**：pytest 全量基线对比、plugin.yaml 0.5.0 + provides_hooks +pre_verify、README EN/ZH（提示通道/allowlist 结构/命令面）、after-install.md、SKILL.md allowlist 表述核对 | 39.R1.2/39.R2.2/39.R3.1/39.R4.1/39.R5.3 | 基线全绿 | Done (2026-08-27) |
| 39.10 | **发布 + 真机**：tag `v0.5.0` + GitHub Release；八场景验证（工作区开场提醒含 CWD 时序 / 非工作区零注入 / heredoc 根写->L1->settle->开闸同轮闭环 / 混合轮次 pre_verify 续轮 / 报告 Reminder 四态 / resolve_agent_cwd 导入路径+懒注册可见性 / allowlist 结构化全链路 / 三命令交互流 R1-R8 逐条） | 39.9 | Release Done（tag+Release 已推送）；真机整体 PASS 无 FAIL（报告=realhost-verification-report.md；场景①⑧ SKIP-with-reason=CLI oneshot 通道限制，判序经进程级探针实证） | Done (2026-08-27) |
| 39.x | **R8 upstream 登记项（外部依赖，非代码）**：建议 hermes 将 terminal 落地写入计入 `_turn_file_mutation_paths`（使 verify-on-stop/pre_verify 覆盖纯终端违规轮）；tasks 挂账跟踪直至 upstream 采纳或否决 | - | 登记于 spec 9 / feedback/10 #6 | Open |

---

## Phase 9 — SCR-040 v0.6.0 续推兜底重构 + 可观测性包 + 配置/报告面重构 (40.x)

TDD per task: write the mapped tests FIRST (red), then implement (green).
Acceptance = mapped testing-standards §7.10 matrix (v2.8) all green + KPI
column. Active branch: `0.6.0_dev` (from `main@ad3fc73`).
Design source: feedback/11 + `docs/scr-040-plan.md`（R1-R9 定稿设计；
R9 放置措辞去歧义 2026-08-27 并入，文档先行、代码随实施批）。

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 40.1 | **文档批**：spec-changes 登记更新 + `scr-040-plan.md` + spec EN/ZH v2.8 ACTIVE + changelog + CONTEXT.md 词条 + AGENTS.md 模块清单（+logsetup.py）+ **shipped 模板修正（R8）** | - | Docs batch done | Done (2026-08-28) |
| 40.R1.1 | **red**：TestPreVerifyHook 扩矩阵（verbatim 文案锁 / call 形态含路径 / 无 allow_path 断言 / cap=3 序列第 4 次 None / 会话重启重置 / child·disabled·no-pending 仍 None） | 40.1 | 红灯确认 | Done (2026-08-28) |
| 40.R1.2 | **green**：audit.py（`_remediation_instruction` helper + nudge 文案 + cap 计数）+ state.py（nudge_counts） | 40.R1.1 | REQ 全绿 | Done (2026-08-28) |
| 40.R2.1 | **red+green**：四条 stats 记录（nudge 行含 attempt 序号 / allow-path 行相对化 target / reminder 五态行 / settle-rejected 类别码无路径） | 40.R1.2 | 全绿 | Done (2026-08-28) |
| 40.R3.1 | **red+green**：logsetup（attach 幂等 / 三级降级注入模拟 / profile-aware 路径两形态 / CLH 优先选择 / delay+utf-8+轮转参数断言） | 40.1 | 全绿 | Done (2026-08-28) |
| 40.R4.1 | **red+green**：报告面重构（State enabled/disabled 两态 / Terminal Guard 与 Reminder 行消失断言 / Allowlist 多行块与严格空单行两形态 / Health 置末尾 Good 与问题列表两态 / Debug Log 行三态：正常+no records yet+unavailable） | 40.1 | 全绿 | Done (2026-08-28) |
| 40.R5.1 | **red+green**：三键去配置化（loader 停读断言 / 内部常量语义 / 遗留键完全无视 / TestWriteAuditConfig 迁移删除 / TestReleaseHygiene 模板默认断言迁移至映射形态 / verdict terminal_guard 分支删除） | 40.1 | 全绿 | Done (2026-08-28) |
| 40.R6.1 | 术语扫描：README EN/ZH + spec 正文 + testing-standards/tasks 措辞统一（pre_verify 收敛到实现语境）+ after-install.md 清扫（三键移除 + Debug Log 说明） | 40.R1.2 | 扫描完成 | Done (2026-08-28) |
| 40.R9.1 | **red+green**：放置措辞去歧义（5.3 block 行 + 3.7/5.4 纪律块括号句 verbatim 锁更新 / 子代理变体负断言迁移至新放置短语 / `len<=280` 保持 / create_session_dir 两行输出契约：路径行+提示行、失败路径 stdout 静默） | 40.1 | 全绿 | Done (2026-08-28) |
| 40.9 | 回归 + bump：pytest 全量基线对比、plugin.yaml 0.6.0、发布说明（三键行为变更提示） | 40.R1.2/40.R3.1/40.R4.1/40.R5.1/40.R9.1 | 基线全绿 | Done (2026-08-28: plugin.yaml 0.6.0 hooks 9/emits 7 不变；spec EN/ZH v2.8 re-frozen + changelog/AGENTS/testing-standards 同步；全量 644 passed / 5 skipped / 0 failed；版本断言 test_version_is_050→060 迁移) |
| 40.10 | 发布 + 真机抽验：tag v0.6.0 + GitHub Release；六场景（见 scr-040-plan.md 六） | 40.9 | Done (2026-08-28: Release Done（main=0d3dcfe + tag v0.6.0 + GitHub Release Latest 已推送，0.6.0_dev 已删）；真机六场景整体 PASS 无 FAIL（报告=realhost-verification-report.md）：场景 2/3/4/5/6 全 PASS，场景 1 机制链 PASS + 转化偏差归因记录（allow_path 逃逸系用户意图冲突所致，文案假设证伪，登记观察项待用户裁决后续 SCR）；config 还原哈希一致、根测试文件已清) |

## Phase 10 — SCR-041 v0.6.1 审计逃逸闭合 + allow_path 用户确认制 (41.x)

Acceptance = mapped testing-standards §7.11 matrix (v2.9) all green + KPI
column. Active branch: `0.6.1_dev` (from `main@0d3dcfe` v0.6.0).
Design source: v0.6.0 真机六场景验证（场景 1/4 逃逸实证 + cap 未达）+ 观察项
①②③ 立项裁决 2026-08-28 + `docs/scr-041-plan.md`——R1-R4 定稿设计
（方案A 重扫语义收紧 + 子代理策略A 禁止调用 + 去除方法仅文档化 + v0.6.1
patch，用户逐项拍板）。

| # | Task | Depends on | Key results (KPI) | Status |
|---|------|-----------|-------------------|--------|
| 41.1 | **文档批**：`scr-041-plan.md` + spec EN/ZH v2.9 ACTIVE（5.11 工具契约重写：入口门禁+确认协议 verbatim / 5.18 重扫语义 + L1/L3 文案 verbatim / changelog v2.9）+ tasks Phase 10 + testing-standards 7.11 + AGENTS.md 同步 | - | 文档落盘 | Done (2026-08-28) |
| 41.R1.1 | **red**：重扫不因 runtime 豁免销案（豁免后 pending 保留 / nudge attempt 序号持续断言） | 41.1 | red 确认 | Pending |
| 41.R1.2 | **green**：verdict.py `classify_target` 增 `honor_runtime_allowlist` 参数 + audit.py 重扫接线（config 口径分类） | 41.R1.1 | 全绿 | Pending |
| 41.R2.1 | **red+green**：allow_path 入口门禁（子代理拒绝 + 父代指引 verbatim / 整根拒绝 verbatim / stats `allow-path-subagent-rejected` 行 + bus-skip） | 41.1 | 全绿 | Pending |
| 41.R3.1 | **red+green**：两步确认协议（schema confirm 参数 / 首调确认载荷 verbatim 且不添加 / confirmation-issued 强制两步 / 未签发 confirm 拒绝 / 成功后常规 stats+bus / SKILL.md 措辞同步） | 41.1 | 全绿 | Pending |
| 41.R4.1 | **red+green**：L1/L3 文案 verbatim 锁更新（config 选项改归属用户 + 闩锁期冻结显式句 / 子代理变体与 settle 行不变） | 41.1 | 全绿 | Pending |
| 41.9 | 回归 + bump：pytest 全量基线对比、plugin.yaml 0.6.1、spec re-freeze（v2.9 FROZEN）、发布说明（重扫语义行为变更提示） | 41.R1.2/41.R2.1/41.R3.1/41.R4.1 | 基线全绿 | Pending |
| 41.10 | 真机复验：六检查点（逃逸闭合 / 子代理拒绝 / 整根拒绝 / 确认流 / 预授权回归 / 新文案渲染，见 scr-041-plan.md 六） | 41.9 | 报告落盘 | Pending |

---

_Note: testing-standards.md v0.3.0 rebuild (listed in the earlier backlog) is
already DONE (2026-08-22) — it is the acceptance source for this phase, not a
pending task. Version bump (30.13) was deferred per user until SCR review
completed; it now closes the phase._