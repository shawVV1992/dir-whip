# dir-whip v0.4.1 变更方案（SCR-037）

- 状态：待设计评审（2026-08-25：用户已确认去 Dashboard 面板，0.4.1 仅 slash 命令；分支 0.4.1_dev 自 main@8dc3549）
- 性质：**缺陷修复 + 小特性**——① 发布物可安装性（模板命中扫描器，v0.4.0 不可安装）② 跨档案部署纪律（白名单缺失真实根因）③ 白名单管理命令面（slash 批量增删）④ skill 预检可观测性；行为基线 = spec v2.4 FROZEN
- 来源：feedback/09 跨档案失效（2026-08-25 核实）+ 2026-08-25 安装扫描拦截实测 + feedback/07 需求7（白名单手动指令，2026-08-22）
- 关联反馈：`feedback/09_v0.4.0跨档案运行反馈-注册缺失与配置解析回退失效.md`（已勘误）、`feedback/07_v0.3.0新功能需求反馈-命令面扩展与工作区管理.md` 需求7
- 版本：v0.4.1（patch，模板默认值 `[]` 属 fresh-install 行为修正，存量运行时配置不受影响 SCR-013）、分支 `0.4.1_dev`
- 前置决策：Dashboard 文件树面板**不入 0.4.1**（用户裁决 2026-08-25），延至 SCR-038；agent 工具通道不做（安全立场）；多档案面板选择器随 Dashboard 一并延后

---

## 一、背景与问题

### 1.1 现象（feedback/09，PM 档案会话 20260825_134404_2a33ff）

PM 档案 `profiles/pm/plugins/dir-whip/` 已存在但守卫未生效——子代理三个报告文件落在 `E:\HermesWorkspace\pm\` 根目录，绕过会话目录纪律；主 agent 自建 `Outputs/` 却合规（skill 纪律，非插件）。

### 1.2 真实根因（2026-08-25 三方核验：代码 + 磁盘 + 日志）

| 反馈原根因 | 裁定 | 地面真值 |
|------------|------|----------|
| 2.1 `profile-workspaces.json` 活动文件丢失 | **red herring** | dir-whip v0.4.0 代码零引用（grep 证实），纯宿主侧产物，与解析链无关 |
| 2.2 `load_guard_config()` 始终读全局路径，per-profile 修补无效 | 条件成立但非本次主因 | `load_guard_config(None)` 确读 `HERMES_HOME/dir-whip/dir-whip-config.yaml`（`config.py`），但 `HERMES_HOME=profiles/pm` 时该路径恰为手工补丁位置；本次事故未走到此步 |
| 2.3 `resolve_working_dir_root()` Step2 路径歧义→fail-open | **证伪** | `_profile_config_path` 对命名档案两种布局均收敛到 `profiles/<name>/config.yaml`（`config.py:292-311`），PM 的 `cwd: E:\HermesWorkspace\pm` 配置正确，一旦插件加载 Step2 必然成功 |
| “主代理合规=插件已加载” | 误判 | 双源独立架构（#42）——skill 独立安装，合规来自教学 |
| “`stats.jsonl` 0 事件查全局” | 位置误导 | PM 事件应落 `profiles/pm/dir-whip/stats.jsonl`（`stats.py:92-104`），该文件不存在碰巧结论正确；全局 `stats.jsonl` 15:19 仍有 default 事件 |
| “加载痕迹 `resolved from profile-config: E:\HermesWorkspace\default`” | **幻觉证据** | 全量日志检索无此行，仅存于 PM 助手自写报告草稿 |

**真实根因唯一**：`profiles/pm/config.yaml` 无 `plugins:` 段→ `plugins.enabled` 缺失→宿主严格 opt-in 白名单语义（`hermes_cli/plugins.py:587-614` 返回 None=什么都不加载）→插件在 PM 进程从未注册。佐证：`skill_view('dir-whip:workspace-organization') not found`（14:02:57, `agent.log`）、`profiles/pm/plugins/dir-whip/` 缺 `.install-metadata.json`（手工拷贝特征）、同档案 `profiles/pm/dir-whip/stats.jsonl` 不存在、learn/job-hunt 对照均有白名单与事件。

### 1.3 安装性缺陷（2026-08-25 实测）

正规重装 `hermes plugins install shawVV1992/dir-whip/dir-whip` 被拦截：

```
Scan: dir-whip Verdict: DANGEROUS  CRITICAL persistence  dir-whip-config.yaml:19 "allowed_root_files: [\"AGENTS.md\"]"
Decision: BLOCKED — Blocked (community source + dangerous verdict, 1 findings). --force does not override
```

规则 `agent_config_mod`（`skills_guard.py:462`）：被扫描文本文件含字面量 `AGENTS.md|CLAUDE.md|.cursorrules|.clinerules` 即 CRITICAL persistence→dangerous 无条件拦截。全插件子树仅此一处命中（`dir-whip-config.yaml:19`）。此前手动复制部署（SCR-025 manifest 门）从未触发插件侧扫描，宿主更新后地雷暴露。**v0.4.0 在当前 Hermes 上不可安装**。

---

## 二、目标与非目标

### 目标

1. 解堵发布物：模板去字面量后 `hermes plugins install` 扫描通过，PM 档案可正规重装（带 metadata）
2. 白名单管理闭环：TUI/GUI chat 全表面可用 slash 命令批量增删 `allowed_root_files`，无需手改 YAML
3. 可观测性：`audit_workspace.py` 对插件未启用给出明确告警

### 非目标

- `exempt_paths` 的同款命令、Dashboard 文件树面板（延 SCR-038）、agent 工具通道（安全立场不做）、feedback/07 其余 6 项需求

---

## 三、定稿设计

### 3.1 模板净化（R1）

`dir-whip/dir-whip-config.yaml:19`：`allowed_root_files: ["AGENTS.md"]` → `allowed_root_files: []`，注释用泛称“workspace rules file”等，杜绝四字面量。语义：strict empty（`spec 5.6`）不变；fresh-install 根部文件全部拦截，规则文件写入由宿主级保护兜底（#53①）；运行时副本可手工加条目。

### 3.2 发布物卫生测试（R2）

新增 `tests/test_release_hygiene.py::TestReleaseHygiene::test_plugin_tree_has_no_agent_config_literals`——扫描 `dir-whip/` 子树全部文本文件（`.yaml/.yml/.json/.toml/.md/.py`）断言四字面量零命中，防退化。存量测试 fixture 自写配置，不受影响。

### 3.3 Spec 修订（R3）

`specs/dir-whip-spec.md:420-421` / `specs/dir-whip-spec-zh.md:367-368`：`allowed_root_files` 模板默认值 `["AGENTS.md"]` → `[]`；示例块 `spec:793/811` 同步；`changelog` 登记；版本 v2.4→v2.5 激活→冻结。

### 3.4 部署文档（R4）

`dir-whip/after-install.md` 与 `internal/docs/deployment.md:106`：补 per-profile 白名单核对步骤（`plugins.enabled` opt-in，`hermes_cli/plugins_cmd.py:1327` 的 `_save_enabled_set`）、手工拷贝不支持声明、原生路径 `shawVV1992/dir-whip/dir-whip` 确认（SCR-025 门已解除）、新增“如何手工加条目”指引。

`README.md:110,207` / `README-zh.md:103,190`：`"(default)"` 措辞与示例注记同步。

### 3.5 共享写入核心

模块 `dir-whip/config_writer.py`（或 `config.py` 内函数，实施期定，避免循环依赖）：行级 YAML 编辑保注释（沿用 `report.py:83-93` 行扫描先例），`allowed_root_files` 键存在重写该行为 flow list，不存在追加；严格文件名校验（仅 basename、禁 `/\..`、≤255 字符、条目上限 100）；写后窄缓存刷新（`state.py`/`config.py` 的 `reset_cache` 过宽，新增窄刷新，与 `verdict.py:_allowed_root_files` 读取路径对齐）。

路径定位：`_profile_config_path` 形状感知（`config.py:292-311`）+ `_profile_home`（`paths.py`）复用。

### 3.6 Phase A — slash 命令（本期唯一新增命令面）

扩展 `report.py:230 _dir_whip_cmd(raw_args)`：空参仍渲染报告；`allow`/`allow 1,3,5`/`allow NAME`/`remove`/`list` 进入管理分支，`Usage: /dir-whip [allow|remove|list]` 统一出口。逆转 SCR-029「单命令无子命令」决策（注释同步），`/dir-whip` 仍为唯一注册名（`ctx.register_command("dir-whip", ...)` 首 token 分发，`plugins.py:2122`），`args_hint=" [allow|remove|list]"` 使 Discord/Telegram 菜单自动浮现（`commands.py:640`）。无参 `allow` 列编号清单（扫描 `working_dir_root` 根级文件，标记已在白名单/豁免/会话目录）。

### 3.7 skill 启用预检

`skills/workspace-organization/scripts/audit_workspace.py` 新增段：布局感知内联（不扩 `workspace_resolver.py` 共享例外，#55 边界），三态 `enabled / not-enabled(告警+指引 hermes plugins enable dir-whip) / disabled`，仅观察性输出，不改 exit code。

### 3.8 Amendment v2.6 -- 单键 allowlist B2 彻底切换（2026-08-25）

用户确认 2026-08-25："不用考虑兼容旧键，之前的旧键直接删除" + B2 全面切换（clean break，无兼容）。

**决策**：`exempt_paths` + `allowed_root_files` 作为配置键**删除**，不保留兼容/迁移/别名；单键 `allowlist: []` 取代，条目区分为 `file:<basename>`（根文件）与 `prefix:<abs-path>`（豁免前缀，绝对路径，可带末尾 `/`），模板注释 `allowlist: []  # file:<name> | prefix:<abs-path>`。

**B2 全面切换**：配置模板仅保留 `allowlist`；旧键出现时忽略/删除；报告行合并为单行 `Allowlist: Files: ...  Prefixes: ...`（或 `Allowlist: (strict empty allowlist)` 当键缺失）；`/dir-whip allow <file|prefix:PATH|PATH/>` 智能判别——无斜杠->`file:`，含斜杠或 `prefix:` 前缀->`prefix:`；`remove`/`list` 同理；分类链 5.3 更新为 Tier0 = allowlist prefix OR runtime-allowlist -> ALLOW，根文件 = allowlist file -> ALLOW；5.18 审计读单键；spec 头版本 v2.5->v2.6 ACTIVE 2026-08-25，changelog 记 BREAKING。

**Spec 对齐**：已同步至 spec EN/ZH v2.6（5.6/5.3/5.7/5.18/5.11/6.2/7.x/8.4），spec-changes SCR-037 行已更新至 v2.6。实施层（config_writer / report / verdict / audit）待后续代码面落地，本 spec 修正为先行文档依据（B2）。

---

## 四、任务拆解（37.x）— v0.4.2 B2 单键 `allowlist` 修订

初始 37.1-37.8 为 v0.4.1 双键 `allowed_root_files` 版；B2 彻底切换后任务细化为 37.9-37.12，旧键删除无兼容，spec v2.5->v2.6 ACTIVE 2026-08-25。详表见 `docs/tasks.md` Phase 7 (v0.4.2)。

| # | 任务 | 依赖 | KPI |
|---|------|------|-----|
| 37.1 | 分支 `0.4.1_dev` 自 `main@8dc3549`；登记行 + 本计划 + `feedback/09` 勘误 + `engineering-constraints` 红线 | - | 文档批 Done |
| 37.2 | spec EN/ZH 5.6 D1 + 示例 + changelog v2.5 激活→冻结; **amendment v2.6 B2** `allowlist: []` (`file:`|`prefix:`) 单键, 旧键 `exempt_paths`+`allowed_root_files` 删除 | 37.1 | spec v2.6 ACTIVE |
| 37.3 | 模板净化 + `TestReleaseHygiene` TDD（`allowlist: []` , 旧键删除） | 37.2 | 卫生测试绿 |
| 37.4 | `after-install.md`/`deployment.md`/`README*.md` 同步 (已为 `allowlist`) | 37.3 | 文档绿 |
| 37.5 | 共享 `config_writer` + slash 命令 (B2 单键 `allowlist`, `file:` vs `prefix:` 智能判别, `list` Files/Prefixes, 窄缓存刷新, 旧键删除) | 37.3 | `TestAllowlistUnified` 绿 |
| 37.6 | `audit_workspace.py` 预检 + 单测 `TestPluginEnablementPrecheck` (读 `allowlist` `file:` 子集) | 37.5 (现 37.11) | skill 绿 |
| 37.7 | 全量回归 + bump `plugin.yaml:0.4.2` (B2 后重测) | 37.3-37.6 (现 37.9-37.12) | `pytest 504 passed/5 skipped` 基线（#53） |
| 37.8 | tag `v0.4.2` + GitHub Release；PM 档案 `hermes plugins install --force` 真机验证 | 37.7 | 发布 Done |
| 37.9 | `allowlist.py` 抽取 — 纯函数 `parse_allowlist`/`is_allowlist_file`/`is_allowlist_prefix`/`normalize_allowlist_entry` (`file:`/`prefix:` 判别, 尾 `/` 归一, 零旧键兼容) | 37.2 | 模块存在, 解析单测绿 |
| 37.10 | cache/verdict/report/audit 统一 — 均改读 `allowlist` `file:`|`prefix:` 子集, `report` 单行 `Allowlist: Files:/Prefixes:`, 窄缓存刷新 | 37.9 | `TestAllowlistUnified` + `TestWriteAuditKernel` file/prefix 绿 |
| 37.11 | config_writer B2 行级编辑 — `allowlist:` 单键 flow list, 智能判别, 非绝对/ `..` /空/ file 含 slash 拒绝, 评论保留, 缺键追加, 去重幂等, 旧键删除 | 37.10 | 文件/前缀 判别, `list` Files/Prefixes, 非法前缀, mutation 刷新 绿 |
| 37.12 | SKILL prompt 硬化 (占位) — `SKILL.md`/prompt 加 allowlist 前缀指引 | 37.11 | Prompt 绿 |

---

## 五、验收标准

- A1 安装性：`hermes plugins install shawVV1992/dir-whip/dir-whip` 扫描 `verdict≠dangerous`，`profiles/pm/plugins/dir-whip/.install-metadata.json` 存在且 `profiles/pm/config.yaml:plugins.enabled` 含 `dir-whip`
- A2 卫生：`TestReleaseHygiene` 四字面量零命中
- A3 命令：`/dir-whip allow` 列表与批量增删在 TUI/桌面 chat 均生效，`allowlist` (`file:`|`prefix:`) 落盘且下次 `classify`/`audit_classify` 生效 (B2 单键, 旧键 `allowed_root_files`/`exempt_paths` 已删除)
- A4 预检：`audit_workspace.py` 在未启用档案输出 `[WARN] plugin not enabled` 行
- A5 全量 `pytest` 绿；A6 feedback/09 勘误与约束红线已落盘

---

## 六、风险与对照

- 风险：模板默认 `[]` 后 fresh-install 根部规则文件不再模板豁免——由宿主级保护兜底（#53①），文档补指引
- 对照：Dashboard 面板（`dashboard/manifest.json`+`api.py`，`web_server.py:18370` 发现链与 `window.__HERMES_PLUGINS__.register`）延至 SCR-038；agent 工具通道不做（自授权风险）
- 约束：`tests/` 不落 git（#60），阶段标记用 `--allow-empty`

---

## 七、变更记录

| Date | Change |
|------|--------|
| 2026-08-25 | 初稿：合并 feedback/09 真实根因（白名单缺失）+ 扫描拦截（模板字面量）+ 双通道（slash+Dashboard）+ skill 预检；裁决 0.4.1 仅 slash + 档案选择器 + agent 不做 |
| 2026-08-25 | 修订：用户裁决 **0.4.1 去 Dashboard 面板**，文件树延 SCR-038；本版定稿 |
| 2026-08-25 | B2 修订：单键 `allowlist: []` (`file:<name>`|`prefix:<abs-path>`, prefix 可带 `/`) 彻底切换, `exempt_paths`+`allowed_root_files` 删除无兼容, 任务细化 37.9 (`allowlist.py` 抽取) /37.10 (cache/verdict/report/audit 统一) /37.11 (config_writer B2) /37.12 (SKILL prompt 硬化占位), spec v2.5->v2.6 ACTIVE, testing-standards v0.4.1->v0.4.2 |
