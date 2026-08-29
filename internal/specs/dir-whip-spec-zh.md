# dir-whip — 完整规范

- 版本：2.10
- 日期：2026-08-29（SCR-042 修正 v2.10 — skill 脚本安全加固；上次冻结 v2.9 2026-08-29）
- 状态：冻结（FROZEN，v2.10，2026-08-29 激活，2026-08-29 实施批完成 re-freeze；套件 687 passed / 5 skipped / 0 failed） — SCR-042 修正 v2.10（scr-042-plan.md，2026-08-29 范围裁决）：skill 脚本安全加固（feedback/13，9 项 5 需求组）：（R1）删除面——`--gate` 模式将 Working Directory 解析失败升格为门失败（退出码 2、stderr 原因、无 wakeAgent 行、**零删除**；未解析根永不经 fail-open CWD 兜底成为删除目标；交互模式 fail-open 不变），`.tmp` 清理扫描边界排除 symlink 会话目录与 symlink `.tmp` 目录（双层）；（R2）导入加固——两脚本经 importlib 按 `__file__` 绝对路径加载捆绑 workspace_resolver（注册 sys.modules；CWD/PYTHONPATH 阴影无法转移共享模块）；（R3）大小写 parity——审计根文件白名单匹配复用 resolver 的 Windows casefold 语义（与 allowlist.py 由构造保证 parity，ADR-0006）、Outputs 黑名单判断大小写不敏感、`.hermes` 豁免 Windows casefold（镜像 report.py `_case_eq` 先例）；（R4）gate 输出契约——wakeAgent 行恒四键 {wakeAgent, violations, removed, failed}（清理失败对 cron 可见）、gate+json 下 stdout 逐行合法 JSON；（R5）健壮性小项——三脚本 stdout/stderr reconfigure(errors=replace)（cp936 非 ASCII 路径不再崩溃）、create 解析根补 abspath（绝对路径契约落地）、LOCALAPPDATA 缺失/空回退用户主目录（home 永不相对 CWD）。版本目标 v0.6.2（patch——skill 脚本安全加固；无配置面变化；hooks 9 / emits 7 不变；--gate 解析失败行为变更见发布说明）。历史：SCR-041 v2.9（2026-08-28 激活，2026-08-29 实施批完成 re-freeze；套件 664 passed / 5 skipped / 0 failed） — SCR-041 修正（scr-041-plan.md，用户 2026-08-28 决策）：(1) 重扫语义收紧（R1） — L3 清偿重扫按 config 口径分类 pending 路径（allowlist files/dirs + session-dir）；runtime 豁免条目不再销案已记录违规（runtime 豁免 = 前瞻放行：未来写入过 Tier 0，历史违规保持 pending）；预授权流程不受影响（审计 diff 在写入时分类，写入前豁免的路径不产生违规）；(2) allow_path 入口门禁（R2） — 子代理调用拒绝（父代指引变体 + stats rule_key allow-path-subagent-rejected，bus-skip；制裁只能自上而下流动），working_dir_root 自身拒绝作为 path 参数（全工作区豁免属 config allowlist dirs，用户亲笔）；(3) 两步用户确认协议（R3） — dir_whip_allow_path 增加可选 confirm 参数：首调（无 confirm）返回确认载荷（风险 + 去除方法 + 转述指令）且不添加，confirm=true 仅在该路径本会话已签发确认载荷后被接受（强制两步，会话内存态）；(4) L1/L3 文案改述（R4） — config 白名单清偿选项改归属用户（ask the user to add），闩锁期写类全冻结显式化（含 config 编辑），消除真机白耗两轮的假可供性；版本目标 v0.6.1（patch — 执法加固：无配置面变化， confirm 为新增可选参数）。历史：SCR-040 v2.8（2026-08-27 激活：续推兜底优化 + 可观测性包 + 配置面/报告面重构含三键去配置化 BREAKING；2026-08-28 实施批完成 re-freeze）。历史：SCR-039 v2.7（2026-08-26 激活：提示通道重排 + 同轮自愈 + 结构化 allowlist；2026-08-27 实施完毕 re-freeze）。此前各轮激活：2026-08-22——SCR-033 terminal 误报修复（v2.1）、
  SCR-034 根目录写入审计新特性（v2.2）、SCR-034 验收条款 7.6（v2.3），均
  完成后再冻结。变更重新走 SCR 流程（spec-changes.md）。
- 变更策略：活文档（方案 C）。变更通过 git commit message 记录。

---

## 1. 概述

### 1.1 问题

Hermes agent 不能可靠地遵守文件纪律规则。文件散落在 Working Directory 根目录，
交付物无法查找，用户无法定位特定对话的产出。根本原因：

1. Skill 未触发（agent 从未加载 workspace-organization）
2. Agent 自行豁免（"这次对话不需要建目录"）
3. 长上下文中注意力稀释（忘记规则）
4. 模型幻觉（错误理解任务）

### 1.2 解决方案

两个互补层，以**单一产物**（插件包）分发：

| 层 | 职责 | 形态 |
|----|------|------|
| Skill | 教 agent 怎么做 | 打包的 SKILL.md，经 `ctx.register_skill()` 注册（opt-in 加载）+ 常驻纪律提示（≤400 字符，英文） |
| Plugin | 不让 agent 不这么做 | Hermes plugin（pre_tool_call 守卫 + 观察类 hooks） |

skill 随插件包分发：安装插件即获得 skill、脚本与配置模板。两层保持零运行时耦合
（守卫不调用 skill；skill 不调用守卫）。

### 1.3 范围（v0.2.0）

- **单档案化精简**：跨档案写拦截删除；memo 全链（同步/登记/增量检查）删除；
  Shared Space 概念删除；Standalone Mode 概念删除。
- **Terminal 写拦截精简**：仅保留重定向（`>` `>>` `1>` `2>`）、`touch`、`cp`/`mv`
  目标粗拦截；深度意图解析（python / node / sed / tee / curl / wget / dd）删除；
  不确定写意图 -> 放行 + 日志（不弹审批）。
- **配置**：dir-whip-config.yaml 为唯一配置源；`working_dir_root` 语义反转
  （显式值优先，缺省回退当前档案 `terminal.cwd`）；`TERMINAL_CWD` 链删除。
- **Skill 打包**：`workspace-organization` 移入插件包
  （`dir-whip/skills/workspace-organization/`），经 `ctx.register_skill()`
  注册；经 `register_system_prompt_section()` 注入常驻纪律提示（≤200 字）。
- **根目录禁写强化**：Working Directory 根目录只允许白名单文件 + 会话格式目录 +
  `.hermes/`；其余根写入 block 并给替代命令；skill 增加创建流程正反例与标准拦截
  响应模板。
- **脚本**：保留 `create_session_dir.py` + `audit_workspace.py`（audit 保留
  `--gate` / cron）；删除 `clean_tmp.py` + `init_workspace.py`；`--workspace`
  与解析出的 Working Directory 做等值校验；fail-open 降级。
- **Cron 治理保留**：audit `--gate` / wakeAgent / `[SILENT]` 与 skill cron 章节
  全部保留（撤销草案 A4）。
- **可观测性**：结构化单行事件日志；拦截统计（outcome × tool × rule_key，按子代理
  切分）；stats.jsonl 持久化（5MB 滚动、隐私裁剪）；`/dir-whip`
  合并报告（无子命令，SCR-029）；`dir-whip:*` 事件总线事件（总线缺失时静默降级）；
  `pre_command` 观察 hook（observer-only）。
- **子代理**：提示层纪律（写入父会话 `.tmp/`、不自建会话目录）+ 观察维度
  （subagent_start 记录、统计切分、诊断筛选）。
- **分发**：纯原生安装（`hermes plugins install shawVV1992/dir-whip/dir-whip
  --enable`）；多档案安装器删除；插件 manifest v2；不引入 plugin pack；
  社区插件索引 BLOCK（待官方上游仓库落地）。
- **术语**："Default Working Directory" -> "Working Directory"（全部用户可见面 +
  术语表 + spec）；代码标识符不改。
- **跨平台**：Windows 10+ / Linux / WSL / macOS——四平台均可安装可运行；
  脚本在完整平台矩阵上测试（WSL 视为标准 Linux；Windows 特有代码路径——
  MSYS 映射 / casefold / ntpath——已按平台分支，SCR-006）。
- **事件总线**：一级能力；`dir-whip:*` 事件在 Hermes 版本含插件间
  事件总线后自动生效；不含总线的版本静默降级（无需重新配置）。

### 1.4 不在范围内（二期）

- Pi Coding Agent extension 适配器
- 通用工作区管理插件（另起项目）
- 社区插件索引提交（BLOCK——官方 hermes-plugin-index 仓库尚不存在；
  待其落地后重新评估）
- `pre_command` 拦截能力（上游 middleware #64204/#64231；
  二期预留——v0.2.0 的 hook 仅观察）

---

## 2. 领域模型

### 2.1 术语定义

| 术语 | 定义 |
|------|------|
| Working Directory | 通过 Hermes config 中 `terminal.cwd` 配置的 profile 级工作目录。原名 "Default Working Directory"（v0.2.0 更名为与 Hermes 桌面版设置一致）。这是 dir-whip 保护之根。 |
| Session Directory | 磁盘上名为 `YYYYMMDD_HHMMSS[_TaskName]/` 的目录，由一次 Hermes session 产生。包含且仅包含 `Outputs/` 和 `.tmp/`。派生自 Hermes "session" 概念。 |
| Outputs/ | Session Directory 的子目录，存放正式交付物。 |
| .tmp/ | Session Directory 的子目录，存放中间文件。可按时间清理（audit cron 模式）。 |
| Confirmation Protocol | 两步规则：指令不等于确认。破坏性操作（删除、覆盖、移动）需要 agent 先报告操作内容，再由用户明确确认后才执行。 |
| Governance Mode | 由用户请求（"整理工作区"）或 cron 任务触发的工作流。审计、分类、提议修正动作，经用户确认后执行（cron 模式自动清理 .tmp）。由 `audit_workspace.py --gate` 自动化。 |
| File Operation Guard | Plugin 的 pre_tool_call hook，对写入目标（write_file / patch / terminal）分类，并拦截那些位于 Working Directory 根目录、合法 Session Directory 之外且非白名单的写入。外部路径放行并记日志。 |
| Allowlisted Dirs | `allowlist` 的 `dirs` 解析子集（working_dir_root 下的相对路径）——位于 Working Directory 内但不受 guard 管辖的目录子树；递归。非独立配置键（单键为 `allowlist`）。v2.6 遗留 `prefix:` 条目被忽略（clean break）。 |
| Runtime Allowlist | 会话级内存路径集合（通过 `dir_whip_allow_path` 工具注入），每个会话起始清空；与 allowlist `dirs` 条目在 guard 的 Tier 0 合并。条目仅当前会话豁免。 |
| Subagent | 父代理（delegate_task）派生的 child AIAgent。同进程运行并继承父工具集，因此 pre_tool_call 覆盖其写入。按纪律其文件落入父会话 Session Directory 的 `.tmp/`。 |
| Discipline Block | 插件在每个顶层 Working-Directory 会话开始时经 `ctx.inject_message()` 注入一次的纪律消息（v2.7；取代已移除的常驻 Discipline Prompt）。承载核心纪律；完整拦截响应模板由守卫的 block 消息携带。 |

### 2.2 Hermes 术语映射

| 我们的术语 | Hermes 对应 | 备注 |
|-----------|-------------|------|
| Working Directory | `terminal.cwd` / "Working dir:"（CLI）/ "Working Directory"（桌面版设置） | Hermes 的 "workspace" 指 kanban 任务工作区——避免使用 |
| Session Directory | （无直接对应） | 派生自 Hermes session 的磁盘目录。不同于 Hermes 会话记录——session-librarian 管理后者（无功能重叠） |
| 文件路径 | `path`（write_file/patch 参数） | 不是 `file_path` |
| profile | `ctx.profile_name` | Plugin API |
| Hermes session | `session_id` / `task_id` | 一次对话，不是目录 |

### 2.3 关系图

```
Hermes Profile (1) ─── terminal.cwd ───> Working Directory (1)
Hermes Session (1) ─── 产生 ───────────> Session Directory (1)
Session Directory (1) ─── 包含 ────────> Outputs/ (1) + .tmp/ (1)
File Operation Guard ─── 强制 ─────────> Working Directory 根目录 + Session Directory 边界
Subagent (n) ─── 写入 ────────────────> 父会话 Session Directory 的 .tmp/（纪律）
Discipline Prompt ─── 引导 ────────────> 每次创建前的写前分类
Confirmation Protocol ─── 管辖 ────────> 删除 / 覆盖 / 移动操作
```

---

## 3. Skill 规范

### 3.1 触发与加载（C1）

skill 打包在插件包内（`dir-whip/skills/workspace-organization/`），在插件
`register()` 时经 `ctx.register_skill()` 注册。

- 随插件安装——无独立 `hermes skills install` 步骤。
- **opt-in 加载**（上游语义）：skill 不进 `<available_skills>` 索引；agent 在需要
  深度参考时按**限定名** `dir-whip:workspace-organization` 显式加载
  （`skill_view()` 支持——`plugin:name` 形式路由到插件 skill 注册表）。
- Discipline Prompt（3.7）承担日常纪律；skill 是深度参考（工作流、示例、审计清单）。
- SKILL.md `description` 约束不变：≤1024 字符，前 57 字符含触发词。措辞必须避免
  "organize/clean up sessions" 表述（与 session-librarian 区分，F4）。
- SKILL.md frontmatter 的 `version` 字段**删除**：plugin.yaml `version` 是整个
  产物的唯一版本（打包内容随插件版本走；已无任何东西比对独立 skill 版本）。

建议的 description（前 57 字符加粗）：

> **Use when creating, saving, writing, moving, or deleting files,** organizing deliverables, designing workspace layout, or auditing workspace compliance. Enforces session directory discipline and two-step confirmation for destructive operations.

### 3.2 行为分层（Q5）

Skill 按三层运作，依次评估：

#### 第 0 层：Scope Check（短路）

```
如果 project_list 工具可用 且 active_id 非空 且 CWD 在项目目录下
  -> 项目模式。Skill 退出。停止。
如果 CWD 不在当前档案的 Working Directory 下
  -> 项目模式。Skill 退出。停止。
否则
  -> 默认模式。继续。
```

#### 第 1 层：即时纪律（每次文件操作）

触发条件：任何文件写入、创建、保存、删除、移动。

**第 1 步——写前分类（C3）。** agent 必须在每次创建/写入前显式陈述目标类别。
分类表态是行为要求（守卫无法机械验证陈述——它按路径位置机械裁决）：

| 目标分类 | Guard 行为 |
|---|---|
| 位于 Session Directory 内（`working_dir_root/YYYYMMDD_HHMMSS.../...`） | 放行 |
| 根白名单文件（`allowlist` `files` 条目） | 放行 |
| 外部路径（Working Directory 之外，含兄弟档案目录） | 放行 + 日志（fail-open） |
| Working Directory 根目录、非白名单（根下其他任何内容） | Block |

**第 2 步——会话目录纪律：**

```
1. 检查：我是否在合法 Session Directory 内？
   - 是 -> 执行操作
   - 否 -> 先创建 Session Directory（懒创建）
           运行：python scripts/create_session_dir.py <task_name> --workspace <working_dir>
           然后写入其 Outputs/ 或 .tmp/

2. 如果操作是 删除 / 覆盖 / 移动：
   -> 执行 Confirmation Protocol（见 3.3 节）

3. 执行操作
```

关键规则：Session Directory 在首次文件写入时懒创建，不在对话开始时创建。
不产出文件的对话不创建目录。

**第 3 步——根目录禁写（C4）。** Working Directory 根目录只允许：
`allowlist` `files` 条目（根文件）、`dirs` 白名单子树、会话格式目录
（`YYYYMMDD_HHMMSS_TaskName/`）
及其内容、`.hermes/`。根下其他一切创建动作**强禁止**——改用 Session Directory。

#### 第 2 层：治理模式（按需或 cron）

触发条件：
- 用户说"整理工作区" / "组织文件" / 等价表述
- 附加了 skill 的 cron 任务

```
1. 运行审计：python scripts/audit_workspace.py --workspace <working_dir>
2. 如果发现违规：
   - 分类每个违规（错位的交付物 / 临时文件 / 未知）
   - 建议操作（移入 session dir / 移入 .tmp / 保留）
   - 经用户确认后执行（cron 模式下 .tmp 清理可自动）
3. 如果无违规：报告 "OK"（cron 模式下为 [SILENT]）
```

### 3.3 确认协议

适用于：删除、覆盖、移动操作。

规则：**指令不等于确认。**

```
第 1 步：Agent 报告操作内容
  "我将[删除/覆盖/移动]以下文件：
   - /path/to/file1
   - /path/to/file2
   确认？(yes/no)"

第 2 步：用户回复明确确认
  "yes" / "确认" / "执行" -> 执行
  其他任何回复 -> 中止
```

用户的初始指令（"删除 X"）触发第 1 步。它永远不被视为确认本身。

### 3.4 Cron 治理模式（保留，Q4）

为 Hermes cron 设计的混合模式（与 v1.4 一致）：

```
Cron 任务配置：
  script: scripts/audit_workspace.py --gate（前置门控，零 token）
  skill: dir-whip:workspace-organization   # 打包 skill 限定名；
                                                  # 经 skill_view() 解析
  prompt: "如果审计发现违规，分类并归档错位文件。
           如果无违规，回复 [SILENT]。"

流程：
  1. script= 运行 audit_workspace.py --gate
  2. stdout "OK" -> {"wakeAgent": false} -> 静默，不投递
  3. stdout 有违规 -> {"wakeAgent": true} -> agent 唤醒
  4. Agent 分类违规，将文件移入对应 session dir
  5. Agent 报告摘要（投递到配置的平台）
```

门失败：`--workspace` 等值校验失败时，audit 退出码 2 + stderr 写明原因，
**不输出 wakeAgent 行**——cron tick 响亮失败且不唤醒 agent（边界配置错误是
系统问题，不是治理场景）。`--gate` 模式下 Working Directory 解析失败**同为
门失败**（SCR-042 H1）：退出码 2 + stderr 原因、不输出 wakeAgent 行、**零删除**
——未解析的根永不经 fail-open CWD 兜底成为删除目标。交互（非 gate）模式维持
fail-open 链（4.4）：回退 CWD + 一条 stderr WARNING 后继续。

### 3.5 Terminal 写入纪律（精简，A1/A2）

第 1 层同样适用于通过 `terminal` 工具执行的写入。守卫只粗拦截：重定向
（`>` `>>` `1>` `2>`）、`touch`、`cp`/`mv` 目标。深度意图解析（python / node /
sed / tee / curl / wget / dd）已删除；不确定写意图放行 + 日志（无审批门）。
agent 必须：

1. 所有文件写入优先使用 Session Directory，包括通过 terminal 的写入。
2. 当用户在对话中显式指定目标路径时（如"写入 C:/Users/me/Reports/R1.md"），
   先调用 `dir_whip_allow_path(path)` 工具登记该路径再写入，
   使 guard 的 Tier 0 放行。
3. 当写入被 guard 拦截时，创建 Session Directory
   （`python scripts/create_session_dir.py <task_name> --workspace <working_dir>`）
   并重新定向——绝不绕过 guard。

### 3.6 Guard 路径分类（C3/Q7）

agent 应理解 plugin 的分类，以便正确应对 block / allow 结果并如实陈述分类：

- 位于 Session Directory 内或命中 allowlist `dirs` / 运行时豁免路径：放行。
- 根白名单文件（`allowlist` `files` 条目）：放行。
- 位于 Working Directory 根目录、非白名单文件：拦截——需创建 Session Directory。
- 位于 Working Directory 之外（含兄弟档案目录）：放行 + 日志
  （外部；无跨档案拦截）。
- 不确定的 terminal 写意图：放行 + 日志。

### 3.7 会话开始纪律块（v2.7——取代常驻纪律提示）

常驻纪律提示（`register_system_prompt_section`，每轮计费）在 v2.7 移除。
教导职责由两条通道承担：

1. **会话开始纪律块** —— 每个顶层会话经 `ctx.inject_message()` 注入一次，
   且为条件化注入（仅当会话位于 Working Directory 内；机制与 fail-open
   矩阵见 5.4）。锁定文本（逐字；`len ≤ 280` 字符，约 78 token，测试按
   字符数锁定，与 tokenizer 无关）：

```
[dir-whip] Active. WD writes need a session dir first: python scripts/create_session_dir.py <task> --workspace <root> (write the deliverable to Outputs/<filename>, or scratch to .tmp/<filename>). Root forbidden. User path -> dir_whip_allow_path first.
```

   相对旧提示删减的要素及各自兜底：写前分类框架句（SKILL.md 加载时 +
   block 消息）、external 分类（守卫自动 allow+log）、`.hermes/` 根例外
   （block 消息）、`[Reason]/[Next]` 模板指引（block 消息自带完整模板）、
   时间戳格式（create_session_dir.py 强制校验）。
2. **block 消息补全** —— 守卫 block 消息现携带放置意图规则
   （交付物写 Outputs/<文件名>，其余写 .tmp/<文件名>）与
   `dir_whip_allow_path` 指引（5.3），
   使每次拦截都是完整教学点。

不使用常驻指针微提示（用户决策 2026-08-26）：守卫 block 消息即确定性
再教学点。

完整 C6 模板（3.9）由插件 block 消息携带，不由纪律块承载。

### 3.8 创建流程示例（C5）

反例（错误）：
- 用户要求"保存报告"；agent 直接写 `working_dir_root/report.md`
  ——根写入、非白名单 -> 被拦截。

正例（正确）：
- agent 将目标分类为会话目录写入；运行 `create_session_dir.py`；
  交付物写入 `Outputs/report.md`（草稿写入 `.tmp/`）。

### 3.9 拦截响应模板（C6）

写入被拦截时，agent 按此模板回复（与守卫 block 消息对齐）：

```
[Reason] 目标 <path> 不被允许：<规则原因>。
[Next] 我将创建 Session Directory 并在其中写入：
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  然后写入其 Outputs/ 或 .tmp/ 子目录。
```

子会话变体（Q1）：被拦截的子代理回复"[Reason] …… [Next] 我将写入父代理传递
的目标目录"并向父代上报拦截（永不自行创建会话目录）。

### 3.10 已删除内容（C7）

skill 精简掉：多档案教学、memo 概念、跨档案分类。保留：cron 治理章节、
确认协议、审计章节。skill 不再教授任何 memo 或跨档案工作流。

---

## 4. 脚本规范

保留脚本：`create_session_dir.py`、`audit_workspace.py` 与共享的
`workspace_resolver.py`（cross-import 例外，职责不变——现为共享**只读**
Working Directory 解析模块）。删除：`clean_tmp.py`（.tmp 按时间清理现内嵌
audit cron 模式）与 `init_workspace.py`（档案的 Working Directory 由用户配置
——Hermes 桌面版设置 / config.yaml `terminal.cwd`；不再存在"创建后登记"流程）。

脚本保持自包含（高内聚、低耦合）。无共享 Python 模块——例外：
`workspace_resolver.py`。每个脚本独立校验输入。

### 4.1 create_session_dir.py

边界校验（B3）：`--workspace` 目标必须**精确等于**解析出的 Working Directory
（见 4.4：dir-whip-config 覆盖 -> HERMES_SESSION_PROFILE -> 档案枚举 +
TERMINAL_CWD 候选根 -> fail-open）。省略 `--workspace` 时以 CWD 为缺省，按
4.4 包含匹配（CWD 在某候选根之下 -> 该根）。解析失败时 fail-open：脚本回退
使用提供的 `--workspace`（或 CWD 缺省），打一条简短 stderr WARNING 后继续。

接口：
```
python create_session_dir.py [task_name] --workspace <path>

退出码：
  0 = 创建成功
  1 = 参数错误（workspace 目录不存在；此检查先于边界校验执行）
  2 = 目标已存在 或 --workspace 与解析出的 Working Directory 不相等
```

输出（stdout，R9）：exit 0 与 exit 2 的"目标已存在"分支均输出恰好两行——
第 1 行为会话目录绝对路径（正斜杠），第 2 行为放置提示：
`Write the deliverable to Outputs/<filename>, scratch to .tmp/<filename>.`
其余失败路径（exit 1；exit-2 边界不匹配）stdout 保持静默（错误走 stderr）。

### 4.2 audit_workspace.py

边界校验：与解析出的 Working Directory 做等值/包含匹配（4.4）；不匹配 ->
退出码 2。根文件白名单保持配置驱动：`allowlist` 的 `files`/`dirs` 条目从
dir-whip-config.yaml 读取（默认空列表，随配置模板 v2.7 下发；键缺失 -> 同样
严格空白名单，宁可多报，fail-closed；v2.6 平铺标签格式与更早的旧键均已删除，
不向后兼容）。根文件白名单匹配与插件守卫同 Windows casefold 语义（审计复用
resolver 的 allowlist 匹配器——guard 与 audit 对大小写变体永不分歧，ADR-0006
parity，SCR-042 R3）。`.tmp` 清理扫描边界排除 symlink 会话目录与 symlink
`.tmp` 目录（仅真目录；SCR-042 R1）。

`--gate` 保留（Q4）用于 cron wakeAgent 集成：
- stdout 最后一行为 JSON，四键恒在：无违规
  `{"wakeAgent": false, "violations": 0, "removed": K, "failed": F}`；
  有违规 `{"wakeAgent": true, "violations": N, "removed": K, "failed": F}`；
  `removed`/`failed` 分别为清理删除数与清理失败数（清理部分失败对 cron 可见，
  SCR-042 R4）
- `--json` 下 stdout 每行均为合法 JSON 文档（violations 数组行 + wakeAgent 行；
  json 模式不再输出明文删除报告——失败明细走 stderr）
- 常规输出（plain 或 --json）仍在 gate 行之前打印
- 门失败（边界不匹配，或 gate 模式解析失败）：退出码 2、stderr 写明原因、
  **无 wakeAgent 行**、零删除

接口：
```
python audit_workspace.py [--workspace <path>] [--json] [--gate]

退出码：
  0 = 合规
  1 = 存在违规
  2 = 参数/路径错误 或 --workspace 不匹配
```

### 4.3 已删除脚本

- `clean_tmp.py` —— 删除。`.tmp` 按时间清理内嵌于 audit cron 模式
  （治理模式第 2 步）。
- `init_workspace.py` —— 删除。工作区由用户经 Hermes 桌面版设置 /
  config.yaml `terminal.cwd` 配置；不再存在创建-登记流程。

### 4.4 通用：Working Directory 解析（workspace_resolver.py）

`workspace_resolver.py` 是共享**只读**模块（cross-import 例外角色不变），
解析当前档案的 Working Directory。独立脚本**没有 Hermes 运行时上下文**
（`ctx.profile_name` 仅插件可用），因此解析器用分层链定位档案。它不能
import hermes_cli（自包含约束），自带最小 `terminal.cwd` 解析器读
config.yaml（与插件 `_parse_terminal_cwd_fallback` 同思路）：

```
1. dir-whip-config.yaml working_dir_root（显式）——设置时具权威性
2. HERMES_SESSION_PROFILE 环境变量（Hermes 将会话档案名注入终端子进程
   环境）-> 解析 HERMES_HOME/(config.yaml 若为 "default" |
   profiles/<name>/config.yaml) 的 terminal.cwd -> 该根
3. 档案枚举 + 路径匹配（步骤 2 不可用时的兜底，如纯本地 CLI）：
   候选根集合 R = { 全部档案的 terminal.cwd } + { TERMINAL_CWD 若设置 }
   - TERMINAL_CWD 是 Hermes 的 terminal.cwd 运行时载体（gateway/cron 启动时
     桥接）。它只作为**候选根**参与匹配——不是配置源步骤
     （Q6/ADR-0003 将其排除在插件链之外）
4. fail-open：None + 一条 WARNING —— 调用方回退 CWD / 提供的 --workspace
```

Hermes home 解析（`hermes_home`）：`HERMES_HOME` 环境变量优先；否则 Windows
为 `LOCALAPPDATA/hermes`、LOCALAPPDATA 缺失/空时回退用户主目录（home 永不
相对 CWD 解析），POSIX 为 `~/.hermes`（SCR-042 N7）。

匹配语义：

```
显式 --workspace：与 R 归一化等值——命中 -> 继续；未命中 -> 退出码 2（边界失败）
缺省（CWD）：    CWD 等于某候选根            -> 该根
                 CWD 恰好包含于某一根         -> 该根
                 包含于多根（嵌套）           -> 最长匹配
                 无包含关系                   -> fail-open
                                             （CWD + 一条简短 stderr WARNING）
```

各脚本校验流程：

```python
def validate_workspace(path):
    """检查 path 是否等于解析出的 Working Directory。"""
    if not os.path.isdir(path):
        return False, "directory does not exist"
    root = resolve_working_dir_root()   # 上述链 1-4
    if root is None:
        warn("[dir-whip] Working Directory unresolved, using the "
             "provided --workspace")    # 一条简短 stderr 警告
        return True, None               # fail-open 兜底
    if normalize_path(path) == normalize_path(root):
        return True, None
    return False, "--workspace does not match the resolved Working Directory"
```

退出码映射：调用方先检查目录是否存在（不存在属参数错误，create_session_dir
中为退出码 1），再检查边界校验（退出码 2）。

### 4.5 命名对齐

v0.2.0 包含 SCR-030 项目改名 workspace-guard → dir-whip（代码标识符随之更新：
插件包/key、斜杠命令、工具、事件命名空间、skill 限定名、模块
`guard.py` → `dir_whip.py`、配置 `guard-config.yaml` → `dir-whip-config.yaml`、
运行时目录、logger）。`--workspace` 参数保持不变。术语决策 G 仍管辖展示层
术语。改名的不改项（勿扫改）：`stats.jsonl`、`config.py` 模块名、
`workspace-organization` 技能名、guard 判定标识符（如
`load_guard_config`），以及历史面（archive、feedback、变更登记表）。
前向注记（SCR-035，v0.4.0）：此处所指 `dir_whip.py` 模块在 5.1 的
11 模块布局中消解；`config.py` 作为模块名保留。

---

## 5. Plugin 规范

### 5.1 目录结构

v0.4.0 布局（SCR-035；v0.3.x 的 `dir_whip.py` + `config.py` 两文件布局
消解为 11 个内聚模块，单向依赖、无环）：

```
<repo-root>/
├── README.md / README-zh.md        # 面向用户的文档（EN/ZH）
├── LICENSE
├── dir-whip/                # 插件包（plugin.yaml 位于包根）
│   ├── plugin.yaml                 # manifest v2
│   ├── __init__.py                 # register(ctx) + 钩子适配器；fail-open
│   │                               #   try/except 收敛于此一层
│   ├── verdict.py                   # 守卫链：guard / classify_target / block 消息
│   ├── terminal.py                 # 命令词法分析 + 粗粒度分层（纯函数）
│   ├── paths.py                    # 规范化 / 解析 / 包含判定（纯函数）
│   ├── events.py                   # 判定发射深模块：emit(outcome, tool,
│   │                               #   rule_key, target, reason, session_id, is_subagent)；
│   │                               #   root/profile 经 state；stats+日志+总线扇出内藏
│   ├── audit.py                    # 写审计：快照/diff/挂起/L1-L3（状态走 state.py）
│   ├── sessions.py                 # 子会话跟踪 + 审计父链接
│   ├── state.py                    # 全部可变运行时状态：会话/审计/stats 三容器，
│   │                               #   锁随组走，reset_all()
│   ├── config.py                   # 配置加载（单一 YAML 解析器）+ working_dir_root
│   │                               #   解析链 + 运行时白名单
│   ├── stats.py                    # 计数器 + jsonl + 滚动（状态容器在 state.py）
│   ├── report.py                   # /dir-whip 报告渲染
│   ├── dir-whip-config.yaml        # 随包配置模板（运行时配置位于
│   │                               #   HERMES_HOME/dir-whip/，SCR-013）
│   ├── after-install.md            # 安装后说明
│   └── skills/workspace-organization/   # 打包 skill（register_skill）
│       ├── SKILL.md
│       ├── references/
│       │   └── workspace-audit.md
│       └── scripts/
│           ├── workspace_resolver.py    # 共享 Working Directory 解析
│           ├── create_session_dir.py
│           └── audit_workspace.py
```

依赖方向（单向、无环）：`__init__ → guard/audit/sessions/events →
terminal/paths → config/stats/state`。core 模块不 import 宿主 API——宿主
能力仅经 `__init__.py` 注入进入。解析链保持双实现（插件 `config.py` 进程内 /
skill `workspace_resolver.py` 子进程），由 parity 契约测试锁定。

仓库根不再有安装器脚本（删除，A6）。skill 包不再是独立目录（移入插件，Q9）。

### 5.2 plugin.yaml（插件 manifest，F3）

```yaml
name: dir-whip
version: <包版本；唯一版本源即本文件>
description: Enforce Working Directory file discipline in Hermes
api_version: <当前 Hermes 插件 api_version>
author: shawVV1992
license: MIT
provides_hooks:
  - pre_tool_call
  - on_session_start
  - post_tool_call
  - transform_tool_result   # SCR-034 写入审计 L1 fire-once 通告
  - post_approval_response
  - subagent_start
  - subagent_stop
  - pre_command            # observer-only（v1）；拦截待上游落地
emits:
  - dir-whip:blocked
  - dir-whip:external-write
  - dir-whip:allowlisted
  - dir-whip:approval-requested
  - dir-whip:approval-resolved
  - dir-whip:write-audit-violation      # SCR-034
  - dir-whip:write-audit-gate-block     # SCR-034
```

`manifest_version` 刻意**省略**（e6e2148；用户决策 2026-08-22 保留删除、不恢复）：
本机 hermes CLI v0.20.1 安装器门限 `manifest_version <= 1`、拒绝显式声明 v2，
而运行时加载器接受无此字段的插件。原生 `hermes plugins install` 分发另行
跟踪（SCR-025）。

`api_version` 跟随所装 Hermes 版本，且必须是**整数**（manifest 解析忽略非整数值
并告警）。无需声明 `capabilities:`：register_skill / hooks / tools / emit 均**不受
能力门控**（能力注册表仅覆盖 override/平台动作类表面），因此安装 consent 流程
不适用于 dir-whip。

### 5.3 pre_tool_call Guard 逻辑

回调签名：
```python
def guard(tool_name: str, args: dict, task_id: str, **kwargs):
```

拦截的工具：`write_file`、`patch`、`terminal`
放行的工具：所有其他工具（只读工具不受影响）

统一判定链（所有被拦截工具共用）：

```
1. tool_name 不在 ("write_file", "patch", "terminal") 中 -> return None（放行）

2. 守卫禁用短路：working_dir_root 为 None 时，
   注入一次性 fail-open 告警并 return None（放行）。
   此步必须在路径提取/分类之前执行。

3. 提取目标路径：
   - write_file: args["path"]
   - patch (mode=replace): args["path"]
   - patch (mode=patch): 从 args["patch"] 内容解析文件路径
     （V4A 格式："*** Update File: <path>" 行）
   - terminal: 从 args["command"] 解析写入目标（见 5.10）

4. 将每个目标解析为绝对路径：
   - write_file/patch: 相对于 get_session_cwd(task_id)（实际会话 CWD）。
     若 get_session_cwd 返回 None（未记录），兜底以 working_dir_root
     为基准（保守处理），并记 DEBUG 日志。
   - terminal: 相对于 args["workdir"]，缺省 get_session_cwd(task_id)，
     再缺省 working_dir_root（绝不用 os.getcwd()——那是插件进程 CWD）

5. 每个目标经 normalize_target() 归一化（SCR-006 规则不变）：
   - Windows：MSYS 风格路径（/c/...、//c/...、/cygdrive/c/...）映射为
     盘符路径；无盘符根路径继承 working_dir_root 的盘符；再做 normpath。
     UNC 路径不受影响。
   - POSIX：仅 normpath（恒等）。
   - 归一化后仍无法分类的路径 fail-open（放行）并记告警日志。

6. 每个目标经 classify_target() 分类：

   - 命中 allowlist `dirs` 条目或运行时豁免表（Tier 0）      -> 放行

      目标在 working_dir_root 下：
        - 命中 allowlist `files` 条目在根部               -> 放行
        - 在合法 Session Directory 内              -> 放行
        - 其余（根非白名单 / 非会话目录）           -> 拦截

     目标在 working_dir_root 外
       （含兄弟档案目录）                          -> 放行 + 日志
                                                    （external-write 事件）

7. 多目标聚合（最严者生效）：
   - 任一拦截      -> 返回 block
   - 否则         -> return None（放行）
```

**不再有 approve 档**（Q1：跨档案拦截删除；A2：不确定 terminal 意图放行+日志）。
每个判定发出结构化单行日志事件（5.13）并更新统计计数。

拦截结果（v2.7：新增放置意图规则 + allow_path 指引，R3；项目目录提示改为相对
`dirs` 语法）：
```python
{
  "action": "block",
  "message": (
    "BLOCKED: File writes in the Working Directory require a Session "
    "Directory or an allowed root file.\n"
    f"Target: {target_path}\n"
    "Fix: Create a session directory first:\n"
    "  python <scripts_path>/create_session_dir.py <task_name> "
    "--workspace <working_dir_root>\n"
    "Then write the deliverable to Outputs/<filename> (or scratch to .tmp/<filename>).\n"
    "User-specified path -> dir_whip_allow_path first.\n"
    "If this is a project directory, add it to the allowlist dirs in "
    "HERMES_HOME/dir-whip/dir-whip-config.yaml (relative to the Working "
    "Directory root, e.g. projects/foo)\n"
    "Reply using the [Reason]/[Next] template."
  )
}
```

子会话变体（Q1）：当调用来自子会话（session_id 命中 `child_session_ids`
集合，5.4）时，修复指引替换为"写入父代理传递的目标目录"——不给
create_session_dir 指引（子代理永不自建会话目录）。子代理变体**不**追加
放置意图 / allow_path 两行（子代理无放置决策权；协议=写父代指定目录）。

### 5.4 on_session_start Hook

回调签名：
```python
def on_start(session_id: str, model: str, platform: str, **kwargs):
```

行为：清空运行时豁免表（会话级，SCR-010 行为保留）、重置 fail-open 告警标志、
按本会话档案重解析 working_dir_root（SCR-027），然后**条件化**注入纪律块
（`ctx.inject_message()`）：

```
[dir-whip] Active. WD writes need a session dir first: python scripts/create_session_dir.py <task> --workspace <root> (write the deliverable to Outputs/<filename>, or scratch to .tmp/<filename>). Root forbidden. User path -> dir_whip_allow_path first.
```

**条件化注入（v2.7）。** 仅当会话位于 Working Directory 内时注入：

- 会话 CWD 来源：宿主 agent-CWD 访问器，register() 时经 `agent_cwd_fn`
  注入槽装填（ADR-0007 模式；宿主源 = `agent.runtime_cwd.resolve_agent_cwd()`：
  会话 contextvar 覆盖 -> TERMINAL_CWD 桥接 -> 进程 CWD 三级链）。
- 判定谓词：`verdict.discipline_applies(cwd, working_dir_root)` —— 纯函数、
  None-safe，除非明确判定否则返回 True；内部复用 `paths.within_working_dir`
  （相等=在内；Windows 盘符大小写规则任意宿主一致 SCR-006；异盘=在外）。
- 注入矩阵：cwd 在根内 -> 注入；cwd 在根外/异盘 -> 跳过（debug log）；
  cwd 取不到或谓词异常 -> 照注（fail-open=现状）；working_dir_root 未解析
  -> 照注（首写时的一次性 fail-open 警告路径不变，5.12）；inject_message
  不可用 -> debug 跳过（CLI/TUI，不变）。
- **项目模式豁免（v2.7 R7）。** 在上述谓词之前，活跃宿主项目整体豁免本会话：
  插件经 `project_active_fn` 注入槽探测宿主 projects.db（装配层 try-import
  `hermes_cli.projects_db`；`connect_closing()` -> `get_active_id(conn)` ->
  `project_folders` 路径集；任何失败 -> None = 不豁免，fail-open）。当存在
  活跃项目且 agent CWD 落在其任一 folder 之下（包含语义与
  `discipline_applies` 一致）时，提醒跳过并记状态 `skipped-project`——优先级
  高于 `skipped-outside`（项目模式有自己的布局；skill 侧对应 3.2 Layer 0）。
- 结果记入 `state.session.reminder_status`（`injected` | `skipped-outside` |
  `skipped-child` | `skipped-project` | `unavailable`），由 `/dir-whip` 报告呈现（5.7）。
  语义（2026-08-26 裁决）：单进程共享字段、**顶层会话最后写入者**为准——
  报告显示最近一次顶层会话的注入结果（CLI 单会话天然正确；桌面多会话进程
  下为文档化限制；不引入 per-session 字典）。

豁免表清空仅适用于**顶层会话**——已对安装的 hermes-agent 源码验证：子会话**会**
触发 on_session_start（child 跑同一 conversation loop，该 hook 在 child 首轮
无条件触发；subagent_start 先于 child 的 loop 触发）。防御：插件维护
`child_session_ids` 集合（subagent_start hook 加入、subagent_stop 移除）；
on_session_start 回调在 session_id 命中集合时跳过豁免表清空、fail-open 标志
重置与纪律块注入——三者均仅顶层会话生效（运行时豁免表与父进程共享）。

`ctx.inject_message()` 仅在 CLI 模式可用；Gateway 模式返回 False，plugin 记录
一条 debug 跳过日志（可接受——guard 经 pre_tool_call 仍然强制生效）。

### 5.5 working_dir_root 解析（反转，B2/Q6）

```python
def resolve_working_dir_root(ctx) -> Optional[str]:
    # 1. dir-whip-config.yaml 显式值（设置时具权威性）
    try:
        cfg = load_guard_config()
        if cfg.get("working_dir_root"):
            return cfg["working_dir_root"]
    except Exception:
        pass

    # 2. 当前档案的 terminal.cwd（兜底）
    try:
        profile = ctx.profile_name  # "default" / "learn" / "job-hunt"
        if profile == "default":
            config_path = HERMES_HOME / "config.yaml"
        else:
            config_path = HERMES_HOME / "profiles" / profile / "config.yaml"
        cwd = parse_terminal_cwd(config_path)
        if cwd:
            return cwd
    except Exception:
        pass

    # 3. Fail-open：guard 禁用
    logger.warning("dir-whip: cannot resolve working_dir_root, guard disabled")
    return None
```

- `TERMINAL_CWD` 环境变量链**删除**。
- 优先级语义（相对 v1.4 反转）：dir-whip-config.yaml 的 `working_dir_root`
  显式值时具权威性；档案的 `terminal.cwd` 是兜底。桌面版设置仅在无覆盖配置时
  生效——`/dir-whip` 报告展示生效值与来源，使过期的覆盖配置可见（Q6）。
- 解析在 register() 时执行一次，经 `lazy_singleton` 缓存。
  如果返回 None，所有 guard 检查返回 None（放行一切），并注入一次性
  fail-open 告警（5.12）。
- 可见性：解析成功时，plugin 以 INFO 级别记录命中的配置源——`dir-whip-config`
  或 `profile-config`（格式："dir-whip: working_dir_root resolved from
  <source>: <value>"）。失败 WARNING 保留。

### 5.6 配置（dir-whip-config.yaml——唯一配置源，B2）

文件：`<HERMES_HOME>/dir-whip/dir-whip-config.yaml`（与 v1.4 同位置，
SCR-013：位于插件目录之外，强制重装不丢）

```yaml
# dir-whip 配置（用户管理，位于插件目录之外）
# 结构化 allowlist（v2.7，BREAKING）：条目相对 working_dir_root；
# 根目录之外（含根自身）的文件或文件夹不可添加。

allowlist:
  files: []   # 根目录顶层文件 basename，如 ["README.md", "notes.txt"]
  dirs: []    # 根下相对路径，递归豁免该目录及子目录，
              # 如 ["projects/foo"]——允许多级；禁 ".."、绝对/盘符形态、
              # "."（根自身）

# 可选覆盖：working_dir_root（设置时具权威性；缺省回退当前档案 terminal.cwd）
# working_dir_root: E:/HermesWorkspace/learn
```

**v2.8 BREAKING——三键去配置化。** `terminal_guard` / `write_audit` /
`write_audit_entry_cap` 不再是用户配置键（v2.7 及之前版本曾支持）：解析层
停读，行为内部常量化——terminal 写入拦截恒启用（5.10）、写入审计恒启用
（5.18）、审计条目护栏为内部常量 2000。预留键 `write_audit_autofix`
（L4 自动搬移）一并删除（5.18 L4 转为无键预留方向）。runtime 配置中
残留的这些键被**完全无视**（无提示行、无 hint、无日志）。升级说明见
deployment.md。

匹配规则（v2.7 结构化映射）：`files` 条目为 Working Directory 根目录允许存在的
根文件（basename 精确匹配，Windows 下不区分大小写）。`dirs` 条目为**相对**
working_dir_root 的路径；目标等于或位于 `<working_dir_root>/<dirs 条目>` 之下即
豁免——递归子树豁免（统一正斜杠比较；Windows 下经 casefold 不区分大小写，
SCR-006）。允许多级相对路径（如 `projects/foo`）。存储恒为相对条目；绝对路径
只是输入层宽容。

**输入归一化与 add 时分层（2026-08-26 裁决——allow 输入层 v2.1）。**
`/dir-whip allow` 接受索引编号、相对路径或绝对路径（反斜杠→正斜杠、
MSYS/Cygwin 形态经共享归一器映射 SCR-006；Windows 下 casefold 比较）：

1. 绝对输入对 working_dir_root 相对化；解析落**根外**（rel 以 `..` 开头、
   等于 `.`——根自身或祖先）时拒绝，引导消息
   `[dir-whip] Invalid path: choose a file or folder inside the Working
   Directory (<root>).` 附原因副句（`'<input>' resolves outside it`）。
2. **现存路径**（casefold 感知）按磁盘状态分类：目录 -> `dirs` 条目、
   文件 -> `files` 条目（磁盘感知；裸名指向现存目录仍入 `dirs`）。
3. **不存在的路径**走对称确认-创建协议（裸名与 dirs 同规则，2026-08-26）：
   - 无 `--create`：引导消息附精确后续命令——`'<input>' does not exist --
     run: /dir-whip allow <input> --create`；
   - 带 `--create`：创建物由形态决定——尾斜杠 -> `os.makedirs(<root>/<rel>,
     parents=True)` + `dirs` 条目；裸名 -> 根下建空文件 + `files` 条目
     （解锁用户裁定属于根的 agent 根文件写入）；嵌套无尾斜杠 -> 目录树
     （嵌套文件违反 files 仅 basename 存储，故嵌套即目录意图）。
     `--create` 作用于现存路径为 no-op（存在性判定优先）。
4. **加载时校验**：存储条目中的 `..` 段、绝对/盘符形态、空值与 `.`（根自身）
   被忽略（手编配置 fail-closed，guard 与 audit 一致）；解析后落在根外的
   条目加载时忽略。

两键合计条目数 ≤ 100——**cap 仅 add 时硬拒**（超 100 拒绝本次写入；手编配置
超限在加载时全量信任，2026-08-26 裁决）。config_writer 对两键各保持**单行
flow 风格**（`files: ["a", "b"]`，整行替换、键上方注释保留）——不产出块式
`- item` 列表。手编边角（仅文档说明）：`files` 条目命名了磁盘上现存目录时
按 basename 匹配文件、无害，不做额外校验。符号链接/junction 按字面匹配
（不做 realpath 解析）——与守卫 classify 行为一致。

**Clean break（v2.7）**：v2.6 的平铺标签列表格式（`file:<name>` /
`prefix:<abs-path>` 字符串）彻底删除、不向后兼容（用户决策 2026-08-26，沿 B2
先例）。`allowlist` 下的遗留平铺值被 fail-closed 忽略（guard 与 audit 一致），
`/dir-whip list` 将其报告为已忽略的遗留条目以保证迁移可感知。`allowlist`
键缺失 -> 严格空白名单（fail-closed，guard 与 audit 一致）。

运行时豁免表（5.11）在 Tier 0 与解析出的 `dirs` 子集合并。

### 5.7 命令与工具（Q12）——SCR-029 / SCR-037 v2.6

在 `register()` 时注册：

- `/dir-whip` —— 合并报告 + 白名单管理。SCR-029 删除了 `status`/`stats`/`doctor`
  子命令（统计后端 5.13 不变）；v2.7 将 `allow`/`remove`/`list` 统一在同一呈现上：
  Files/Dirs 两段式编号列表（config_writer 行级编辑保注释）。裸 `/dir-whip`
  输出下方报告。
  - `/dir-whip allow` 列出 Working Directory 根下的带编号候选——`Files:` 段
    （顶层文件，排除已列 files / 已被 dirs 覆盖的子树 / 会话目录）与 `Dirs:` 段
    （顶层目录，排除会话格式目录与已被 `dirs` 条目覆盖的子树）——两段共用一套
    连续编号。
  - `/dir-whip allow <args>` 添加条目；逗号/空白批量（`1,3`）；数字映射进候选
    列表（文件号 -> `files` 条目，目录号 -> `dirs` 条目）。名称参数接受
    **相对或绝对**路径（输入层宽容；归一 + 相对化，5.6）：现存路径 -> 磁盘
    感知（目录入 `dirs`、文件入 `files`）；不存在路径 -> 确认-创建协议
    （R3）。
  - `/dir-whip remove` 以同一两段式列出**当前条目**的编号清单（`Files:` 与
    `Dirs:`）；数字映射为移除。名称参数接受相对或绝对路径（归一后按 5.6
    规则对两集合匹配）。
  - `/dir-whip list` 以同一两段式编号格式渲染当前 allowlist（编号与 `remove`
    对齐），遗留平铺值被忽略时附提示行（v2.6 格式，clean break）。
  - 其它参数输出 `Usage: /dir-whip [allow|remove|list]`。

**交互流程（v2.7，2026-08-26 裁决——R1-R8）：**
- **R1 编号**：Files:/Dirs: 两段共用一套连续整数序列（段标题仅视觉分组，
  数字 token 无需段上下文）。
- **R2 `allow` 裸命令（候选枚举）**：
  ```
  Candidates in <root>:
  Files:
    1: notes.txt
  Dirs:
    2: projects
  Add: /dir-whip allow <number|name>
  ```
  Files 段 = 根顶层文件 − 已列 files 条目；Dirs 段 = 根顶层目录 − 会话格式
  目录（`YYYYMMDD_HHMMSS.../`）− `.hermes/` − 已被 dirs 条目覆盖的子树；
  空段渲染 `(none)`。
- **R3 `allow <args>`**：数字 token 映射进候选列表（文件号→files、目录号→
  dirs）；逗号/空白批量，**全有或全无**（首个非法 token 整体拒绝）。路径
  token（相对或绝对，5.6 输入分层）：
  - 现存路径 -> 磁盘感知分类（`dirs` / `files`）；
  - **不存在路径 -> 对称确认-创建协议**（裸名与 dirs 同规则，
    2026-08-26）：无 `--create` 时返回引导消息 `'<input>' does not exist
    -- run: /dir-whip allow <input> --create`（不添加任何条目）；带
    `--create` 时创建物由形态决定——尾斜杠 -> `makedirs` + `dirs` 条目、
    裸名 -> 根下建空文件 + `files` 条目、嵌套无尾斜杠 -> 目录树 + `dirs`
    条目；`--create` 作用于现存路径为 no-op；
  - 根外 / 根自身 / 祖先 -> 引导拒绝
    `[dir-whip] Invalid path: choose a file or folder inside the Working
    Directory (<root>).` + 原因副句。
  反馈逐条标注去向（`Added to files: X` / `Added to dirs: X`）；
  重复幂等（`Already in files: X`）；尾部附两段式当前状态行。
- **R4 `remove` 裸命令**（自 v0.4.1 的 Usage 提示升级）：以同一两段式编号
  格式枚举**当前条目** + `Remove: /dir-whip remove <number|name>` 尾行；
  键缺失保留 `(strict empty allowlist)`。
- **R5 `remove <args>` 名称匹配**：接受相对或绝对输入（归一后按 5.6）；
  按名称对**两个集合同时匹配**
  （Windows casefold），命中即从所在集合删除——手编双集合同名条目一并
  删除。磁盘感知只属于 allow 时判别（remove 删除的是条目不是路径）。
- **R6 `list` 渲染**：与 R4 同构多行格式（编号与 remove 对齐，可复制直接
  执行）；空态 `Files: (none)  Dirs: (none)`；遗留平铺值被忽略时追加
  `[!] ignored legacy entries: N -- re-add via /dir-whip allow`。
- **R7 存储规范化**：dirs 条目去尾斜杠存储与展示；嵌套相对路径（`proj/sub`）
  经存在性判定与确认-创建协议后入 dirs。
- **R8 不变项**：无确认步骤（用户权威直改；`--create` 即显式同意形态）；
  窄缓存刷新即时生效；runtime
  工具不受影响；纯数字文件名（如 `123`）恒按索引解析（文档注明）。
- 工具 `dir_whip_allow_path(path)` —— 运行时豁免表（Tier 0）；
  会话级，会话起始清空（5.4）。
- 工具 `dir_whip_settle(paths)`（v2.7 新增，**懒注册**）—— 写审计违规的
  同轮自愈（5.18）：硬约束只接受当前会话未决 pending 集合内的路径（零任意
  文件系统能力）；将每个接受的路径经 shutil.move 移入
  `<working_dir_root>/.hermes/audit-quarantine/<时间戳>/`（审计安全：快照只看
  根顶层且目录项永不违规）；返回迁移清单；子代理拒绝（清偿是父代职责）；
  fail-open 出错返回 error dict、闩锁保持。首次 L1 通告触发时才注册，
  无违规会话零常驻 schema 成本；若某表面验证 mid-session 注册不可用，
  降级=常驻注册。

**报告布局（字段顺序固定，每行一个字段）：**

```
[dir-whip] v<version>
State: enabled|disabled
Working Directory: <value>  (source: <source>)
Allowlist:                              # 多行块（有任一条目时，见下）
  Files: (none)|<comma-joined file basenames>
  Dirs: (none)|<comma-joined relative dir paths>
  [!] ignored legacy entries: N -- re-add via /dir-whip allow   # 仅遗留值时
WARNING: ...                            # 仅 override != terminal.cwd 时出现
Stats File: <absolute stats.jsonl path>
Debug Log: <absolute dir-whip.log path> [(no records yet)|(unavailable)]
Health: Good                            # 置于末尾；无问题时单行 Good
# 有问题时 Health 改为简要问题列表：
# Health: N issue(s)
#   - resolution: FAIL-OPEN
#   - stats.jsonl: NOT WRITABLE (<err>)
```

字段规则（SCR-029 方案 A；标签按 SCR-031 / B2 重设计；v2.8 重构）：
- 首行 `[dir-whip] v<version>`：版本取自包根的 plugin.yaml
  （唯一版本源；简单文本解析，无 PyYAML）。任何失败（文件缺失/不可读/
  无匹配）-> `unknown`；绝不抛出。
- 第 2 行 `State`：`enabled`（working_dir_root 解析成功），或解析链（5.5）
  未解析出根时的 `disabled`（fail-open 语义不变，5.12）。v2.8 起值词汇
  由 ACTIVE/FAIL-OPEN 改为 enabled/disabled。
- 第 3 行 `Working Directory`：解析值 + 两个空格 + 解析来源——
  `guard-config`（dir-whip-config.yaml 覆盖）/ `profile-config`
  （档案 terminal.cwd）/ `fail-open`，与 5.5 链三步对应；无值时输出
  `Working Directory: (unresolved)`。显示层术语用 "Working Directory"
  （G1）；代码标识符 working_dir_root 不改。
- **Allowlist 多行块（v2.8）**：原单行 `Allowlist: Files: X  Dirs: Y` 改为
  块状——`Allowlist:` 头行 + `Files:` / `Dirs:` 各占一行、段首缩进 2 空格；
  完全无条目（无 files/dirs/legacy）时保留既有单行
  `Allowlist: (strict empty allowlist)`（键缺失同样如此，fail-closed 提示）；
  遗留平铺值被忽略时块内附加缩进行
  `[!] ignored legacy entries: N -- re-add via /dir-whip allow`。
  旧键 `exempt_paths` / `allowed_root_files` 不再显示（已删除，B2）。
- **已删除行（v2.8）**：`Terminal Guard:`（随三键去配置化失去意义，R7）、
  `Reminder:`（五态观测职责移交 5.13 的 `session-reminder` stats 记录；
  `state.session.reminder_status` 内部状态保留供 stats reason 使用）。
- **Debug Log 行（v2.8 新增）**：倒数第二行（Health 之前）；专用诊断日志
  dir-whip.log 的绝对路径（5.13，profile-aware）；文件尚不存在附
  `(no records yet)`；日志装配失败附 `(unavailable)`。
- **Health 置于末尾（v2.8）**：无问题时单行 `Health: Good`（值词汇由 OK
  改为 Good）；有问题时改为简要问题列表（`Health: N issue(s)` + 缩进问题
  行）：`- resolution: FAIL-OPEN` 和/或 `- stats.jsonl: NOT WRITABLE (<err>)`。
  dir-whip-config.yaml 缺失属设计内默认值，**不算问题**。
- 仅异常时出现的 WARNING 行：显式 `working_dir_root` 覆盖值与当前档案
  `terminal.cwd` 不一致时输出（Q6 footgun：桌面版设置被覆盖值遮蔽）。
- `Stats File`：会话档案 home 的 stats.jsonl 绝对路径（5.13，SCR-027），
  位于 Debug Log 之前。

已删除（memo 已删，B4）：`/dir-whip workspace_status` 与
`/dir-whip workspace_update` 命令；工具
`dir_whip_auto_update_workspace` 与
`dir_whip_register_workspace`。

### 5.8 错误处理与线程安全

规则（v1.4 保留）：
- Hook 回调永远不 raise。所有异常捕获、记录日志、返回 None。
- 所有回调接受 `**kwargs`（前向兼容）。
- 通过 `from plugins.plugin_utils import lazy_singleton` 缓存配置。
- 如果 register() 崩溃，plugin 被禁用，Hermes 继续正常运行。
- 拦截时 WARNING 级别日志，放行时 DEBUG 级别。

v0.2.0 新增：
- 统计写入为加锁的逐行 JSON 追加；统计写失败只记日志，**绝不**影响守卫判定
  （fail-open 日志）。

### 5.9 Session Directory 检测

路径"在合法 Session Directory 内"的判定：其相对于 working_dir_root
的第一级目录名匹配以下模式（Session 目录只存在于 Working Directory 根级）：

```python
SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

def is_inside_session_dir(path, working_dir_root):
    """检查 path 是否在 working_dir_root/<session_dir>/... 下"""
    rel = os.path.relpath(path, working_dir_root)
    parts = rel.replace("\\", "/").split("/")
    if parts and SESSION_DIR_RE.match(parts[0]):
        # 校验真实时间戳
        try:
            datetime.strptime(parts[0][:15].replace("_", ""), "%Y%m%d%H%M%S")
            return True
        except ValueError:
            return False
    return False
```

### 5.10 Terminal 写入拦截（精简，A1/A2）

拦截 `terminal` 工具。`args["command"]` 由轻量 shell 切词器解析（处理引号/转义），
提取候选写入目标。检测**粗粒度**：

- **Block 档**（高置信目标 + 根违规）：重定向（`>` `>>` `1>` `2>`）、
  `touch <path>`、`cp`/`mv` 目标参数。
- **放行+日志档**（有写入意图，目标不确定）：其他一切可能写入的命令——嵌套
  shell（`bash -c` / `sh -c` / `powershell -Command`）、python / node / sed /
  tee / curl / wget / dd、动态或非字面量路径，以及含 heredoc（`<<`）的命令。
  放行；记录结构化日志事件（rule_key `terminal-write-uncertain`）。
  **无审批门**（A2）。
- **放行档**（fail-open）：只读命令与无解析出写入目标的命令。

**链感知目标提取（SCR-033）**。切词器将 `;` 作为独立分隔符词元输出；
`&&`（两个 `&` 词元）、`;`、`|` 与换行为链边界。Block 档目标**按命令段**提取
——redirect / touch / cp-mv 目标只在包含写入命令的段内查找，绝不跨链边界。
结果：链式写入（如 `echo hi && touch <file>`）此前漏检，现可被检出。以 `=`
开头的词元（无引号 `>=` 比较被 `>` 拆分的残留）不构成合法重定向目标。

**设备路径豁免（SCR-033）**。`/dev/null`、`/dev/stdout`、`/dev/stderr`
在**归一化之前**豁免：不进分类链，不产生 verdict 或 stats 事件。（Windows
上不得被盘符继承拼成 `E:\dev\null` 之类的虚构路径。）

相对目标以 `args["workdir"]` 为基准，缺省 `get_session_cwd(task_id)`，
会话 CWD 未记录时再缺省 `working_dir_root`（绝不用 `os.getcwd()`）。
多目标命令最严者生效（任一拦截 -> 拦截；否则放行）。恒启用（v2.8 起
`terminal_guard` 配置键已删除，无开关）。

统计 rule_key：`terminal-redirect`、`terminal-touch`、`terminal-cp-mv`、
`terminal-write-uncertain`。

### 5.11 运行时豁免表（保留，Q2；v2.9 入口门禁 + 用户确认）

plugin 在 `register()` 时注册工具 `dir_whip_allow_path(path, confirm=false)`
（通过 `ctx.register_tool`）——这是插件的常驻急切注册工具；`dir_whip_settle`
自愈工具（5.18）改为在首条 L1 审计通告时懒注册。其 handler 将 `path`
加入**会话级**的内存豁免表，在 Tier 0（5.3 步骤 6）与 allowlist `dirs` 条目合并。豁免表在每个会话起始清空
（`on_session_start`，5.4）；`reset_cache()`（插件注册/重注册时调用）也会清空
——条目不会泄漏到下一会话。工具描述向 agent 说明该条目"本会话豁免"。

v2.9 语义（SCR-041 R1）：runtime 条目是**前瞻性**的——未来对该路径的写入过
Tier 0，但 runtime 条目**不清偿**已记录的根目录写入违规：L3 清偿重扫（5.18）
按 **config 口径**分类 pending 路径（allowlist `files`/`dirs` 条目 + 会话目录）。
预授权流程不受影响：写入**之前**豁免的路径不产生违规（审计 diff 在写入时经
同一分类链判定）。

匹配语义：与 allowlist `dirs` 条目一致的前缀匹配——forward-slash 归一化 + Windows 下
casefold；目录条目豁免其整个子树。（运行时条目保持绝对路径——它们是用户逐会话
指定的，不同于持久化的相对 `allowlist`。）

工具注册：schema 采用 OpenAI function 格式（name/description/parameters），
参数 `path`（string，必填）与 `confirm`（boolean，可选，默认 false）；
handler 签名为 `(args, **kwargs)`，从 args dict 读取 `path`/`confirm`，兼容以裸字符串
调用的调用方。schema 的 `description` 向 agent 说明两步流程（先不带 `confirm`
首调取载荷、转述用户、仅在显式批准后带 `confirm=true` 重调）。

v2.9 入口门禁（SCR-041 R2），在任何状态变更前按序检查：

- **子代理调用拒绝**——本工具是用户制裁面，制裁只能自上而下流动
  （用户 → 主代理 → 委派范围）；需要豁免的子代理失败并上报父代，
  由父代询问用户。返回父代指引变体（与 5.3 子代理 block 变体同型）：

```
[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.
Exemptions are granted by the user via the main agent. Write to the target
directory passed by the parent agent, or report back so the parent can ask
the user.
```

- **working_dir_root 自身拒绝**作为 path 参数（一次调用不得豁免一切；
  全工作区豁免属 config allowlist `dirs`，由用户亲笔）：

```
[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.
Allow a specific file or subdirectory path instead; workspace-wide
exemptions belong in dir-whip-config.yaml (allowlist dirs) authored by the user.
```

- 拒绝调用记录 stats 行 `block / allow-path / <rule_key>`——bus-skip（无通用
  blocked 扇出），与 write-audit-violation 先例同型（5.13/5.14）：子代理调用
  → `allow-path-subagent-rejected`；整根调用 → `allow-path-root-rejected`。

v2.9 两步用户确认（SCR-041 R3）：添加条目**必须**经用户显式批准，
强制两步协议。

- 首调（`confirm` 缺省或 false）：**不添加任何条目**；handler 返回确认载荷
  （verbatim 见下）并将该路径记入会话内存 `confirmation-issued` 集合。
  载荷签发与未签发直接 `confirm=true` 的重发，仅记 dir-whip.log DEBUG 行——
  无 stats 行、无总线事件（用户裁决 2026-08-28）。
- 重调（`confirm=true`）：仅当该路径已在 `confirmation-issued` 集合时被接受；
  未签发载荷而直接 `confirm=true` → **不添加、重发确认载荷并记入
  confirmation-issued**（等效首调——`confirm=true` 永不独立添加，两步不可
  跳过；2026-08-28 二审裁决，替代原"拒绝"设计免设新 verbatim）。
  成功后正常添加条目，常规 `runtime-allowlist-add` stats 行 / 总线事件照发
  （5.13/5.14）。
- 确认载荷（verbatim）：

```
[dir-whip] CONFIRMATION REQUIRED: adding "<path>" to the runtime allowlist
exempts ALL file operations under it from the guard for the rest of this
session. Previously recorded root writes under it are NOT remediated by
this exemption (they stay pending until settled). The entry expires
automatically when the session ends; persistent exemptions belong in
dir-whip-config.yaml
(allowlist files/dirs, removable via /dir-whip remove).
Present this to the user and ask for explicit approval. Re-call
dir_whip_allow_path(path=..., confirm=true) ONLY after the user approves.
```

- 闩锁上下文条件行（2026-08-28 二审采纳；三审升级为选择呈现）：pending 非空
  （闩锁活跃）时，载荷末尾追加一行——`NOTE: a settlement block is currently
  active — present the resolution choice to the user: move the file(s)
  (settle), or keep them at the root (give the user the exact command:
  /dir-whip allow <path>). Writes stay frozen until then.`——避免"用户批准
  豁免后写入仍被拦"的无效往返（闩锁冻结一切写类调用；豁免只过 Tier 0，
  不开闸）。

- 去除方法（仅文档化——无撤销能力，用户裁决 2026-08-28）：runtime 条目在
  会话结束自动失效（`on_session_start` 清空 + `reset_cache`）；持久豁免属
  dir-whip-config.yaml allowlist（`files`/`dirs`），可经 `/dir-whip remove`
  移除。

SKILL.md 指示 agent：当用户在对话中显式指定目标路径时调用此工具登记，
从而尊重用户意图而不削弱对其他路径的约束；v2.9 协议下 agent 将确认载荷
转述给用户，仅在获得显式批准后带 `confirm=true` 重调。plugin 自身工具调用
永不被拦截。

### 5.12 fail-open 告警（保留，SCR-004 机制）

`working_dir_root` 无法解析时，guard 在每会话首次工具调用通过
`ctx.inject_message()` 注入告警，然后放行（fail-open）。模块级标志在会话内
去重，并在 `on_session_start` 重置，使失效的 guard 在每个新会话提醒一次，
直到修复。Gateway 模式降级为日志行（`inject_message` 不可用）。告警文案已
术语更新：

```
[dir-whip] WARNING: The guard is DISABLED because the Working Directory
could not be resolved. File writes are NOT being enforced.
Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml
(terminal.cwd) and restart the session.
```

### 5.13 结构化日志与统计（D1/D2/D3）

**结构化日志（D1）。** 统一 `logging.getLogger("dir-whip")`。
每个守卫判定发出**一条**单行结构化事件：`outcome`（block / allow /
external-write / fail-open）、`reason`、`tool`、`target`（相对 working_dir_root；
外部目标省略或哈希前缀）、`rule_key`、`is_subagent`、`session_id`、`timestamp`。
- fail-open 与配置异常：WARNING 级别。
- Block：WARNING。External-write：INFO。其他放行：DEBUG。

**统计（D2）。** 内存计数按 outcome × tool × rule_key 聚合，按 `is_subagent`
切分：
- `post_tool_call` 观察：记录写类工具调用的完成（与结果状态）——hook 只能看到
  调用结束，看不到磁盘上的字节（rule_key `landed:<tool>`）。
- `post_approval_response` 观察：记录宿主审批提示的批准/拒绝分布
  （rule_key `approval:granted` / `approval:denied`）。
- `transform_tool_result` / 写入审计观察（5.18）：记录根目录写入审计违规与
  清偿闸门拦截（rule_key `write-audit-violation` / `write-audit-gate-block`）；
  L1 通告本身不产生事件。
- 续推兜底观察（5.18，v2.8）：记录续推触发（rule_key `pre-verify-nudge`，
  reason 带 attempt 累计序号；target=None——路径已由 violation 事件承载）。
- `allow_path` 工具调用观察（v2.8）：记录运行期豁免添加（rule_key
  `runtime-allowlist-add`，target 相对化路径；与总线事件
  `dir-whip:allowlisted` 对称）。
- 入口门禁观察（5.11，v2.9）：记录 allow_path 入口门禁拒绝（rule_keys
  `allow-path-subagent-rejected`（子代理调用）/ `allow-path-root-rejected`
  （整根调用）；reason=类别码 subagent-rejected | root-target，无原始路径；
  bus-skip——无通用 blocked 扇出，与 write-audit 违规同型）。
- 会话开始观察（5.4，v2.8）：记录开场纪律块注入五态（rule_key
  `session-reminder`，reason=状态字面量 injected | skipped-outside |
  skipped-child | skipped-project | unavailable；每顶层会话一行）。
- settle 拒绝观察（5.18 R4，v2.8）：记录清偿拒绝/失败类别（rule_key
  `write-audit-settle-rejected`，reason=类别码 subagent-rejected /
  invalid-paths / not-in-pending / move-failed，不带原始路径）。

**持久化（D3）。** 每个事件向 `HERMES_HOME/dir-whip/stats.jsonl`
追加一行 JSON：
- 会话字段：`profile`、`session_id`、`is_subagent`、`started_at`
- 事件字段：`ts`、`outcome`、`reason`、`tool`、`rule_key`、`target`
  （相对 working_dir_root；外部路径 -> 哈希前缀 / 省略）
- 隐私：不记文件内容、不记绝对路径、不记提示文本。
- 滚动：追加前检查 stats.jsonl 是否超过 5MB——超限则改名为 stats.jsonl.1
  （覆盖上一份旧档）并新建文件。
- 故障隔离：统计写失败只记日志，绝不影响判定（5.8）。

`/dir-whip` 命令不再展示统计（SCR-029）；记录后端不变——累计数据可通过
`/dir-whip` 报告的 Stats File 路径自行查看。

**诊断日志文件（v2.8 R5）。** 插件在 register() 时经新模块 logsetup.py
装配专用 DEBUG 全量日志 `<HERMES_HOME>/dir-whip/dir-whip.log`
（profile-aware 落位，镜像 stats 路径模式）：捕获全部 `dir-whip` logger 行
——含宿主 agent.log（INFO+）阈值之下的 DEBUG breadcrumb（allow 类 verdict、
fail-open 处理路径等）。三级 fail-open 降级链：`concurrent_log_handler.
ConcurrentRotatingFileHandler`（跨进程安全轮转，宿主 venv 已装；第三方包
导入不违 ADR-0007）→ stdlib `RotatingFileHandler` → 仅 console。参数：
maxBytes=5 MiB、backupCount=3、delay=True（首条写入才建文件）、
encoding=utf-8。隐私口径：**允许绝对路径**（本机诊断文件，定位价值优先；
消息面无密钥类内容）。已知限制：桌面单进程服务多档案时 log 文件落
register 时档案目录、跨档案行混写（verdict JSON 自带 session_id 可归属）；
stdlib 兜底场景 Windows 多进程轮转存在 WinError 32 已知风险。

### 5.14 事件总线事件（D5）

插件间事件总线是 v0.2.0 的一级能力（1.3）。总线可用性**随版本而定、运行时探测**：
本地 v0.20.0 安装已具备 `ctx.emit()`/`subscribe()`（已对安装的 hermes-agent 源码
核验）；旧版本静默降级——升级后无需重新配置。plugin 发出 `dir-whip:*`
事件（全名）；实现**必须**调用 `ctx.emit("<裸名>", payload)`——API 只接受**裸
事件名**并强制 `dir-whip:` 命名空间（传带 `:` 的名字抛 ValueError，
fail-closed）：

- `dir-whip:blocked` —— 写入被拦截（outcome、rule_key、target）
- `dir-whip:external-write` —— 外部路径放行 + 日志
- `dir-whip:allowlisted` —— `dir_whip_allow_path` 添加条目
- `dir-whip:approval-requested` / `dir-whip:approval-resolved` ——
  观察到的宿主审批流程（post_approval_response）
- `dir-whip:write-audit-violation` —— 根目录写入审计（5.18）：allowlist（files/dirs）/会话范围之外的新增或修改根级文件（路径、会话范围标志、首次发现时间）
- `dir-whip:write-audit-gate-block` —— L3 清偿闸门拦截了写入类工具直至清偿
  （路径、闸门状态）

事件 payload 遵循与 5.13 相同的隐私规则：`target` 相对 working_dir_root；
外部路径哈希前缀或省略。

静默降级：register() 时经能力检查（hasattr / try-except）探测总线缺失；
不发事件、不报错，一条 DEBUG 日志记录跳过。

### 5.15 pre_command 观察（D6）

plugin 注册 `pre_command` hook（当前 Hermes 为 observer-only）：记录 slash 命令
调用——`surface`（"cli" | "gateway"）、`command`（规范名）、`alias_used`（用户
键入的原样 token），以及存在时的 `args_raw` / `session_key` / `platform`——入
日志与统计（rule_key `pre-command:<command>`）。命令的拦截能力为**上游依赖**
（官方 middleware #64204/#64231）；落地前仅观察。

### 5.16 子代理观察（E1/E2）

- `subagent_start` hook：记录 `child_session_id`、`child_role`、`child_goal`
  （以及有用时的 `parent_session_id` / `parent_turn_id` /
  `parent_subagent_id` / `child_subagent_id`）——日志 + 统计会话字段，并将
  `child_session_id` 加入插件的 `child_session_ids` 集合（5.4 顶层会话门）。
- `subagent_stop` hook：记录子代理完成（`child_session_id` /
  `child_subagent_id`），从 `child_session_ids` 移除该 id，并关闭其统计会话
  上下文（对称记账；不影响判定）。
- 守卫对子代理写入的判定与父代**完全一致**：child 继承父工具集，同一
  pre_tool_call 路径覆盖其写入——无特殊判定分支（Q13）。
- 统计按 `is_subagent` 切分（写入 stats.jsonl；/dir-whip 报告不展示
  统计）。
- 纪律（提示层，E1）：**父代在委托前确保目标目录存在**（必要时先创建父会话
  Session Directory——懒创建是父代的职责）。子代理写入父代传递的目标目录——
  缺省为父会话 `.tmp/`；父代可显式传递 `Outputs/` 路径（正式交付物），或为
  每个子代理指定独立子目录（如 `.tmp/<task>/`）规避并发同名冲突（不强制子目录
  隔离——冲突由父代仲裁）。子代理**不自建**会话目录、**不自晋升**产物
  （`.tmp/` -> `Outputs/` 晋升是父代的审阅步骤）。当目标目录缺失或写入被拦截
  时，子代理向父代上报，而非自行创建会话目录。

### 5.17 Skill 注册与教导通道（C1/C2）

- `register_skill()`：注册捆绑 skill（`dir-whip/skills/workspace-organization/`，
  SKILL.md + references + scripts）。按上游语义 opt-in 加载（3.1）。
- `register_system_prompt_section()`：v2.7 **移除**（常驻纪律提示通道取消；
  教导 = 会话开始纪律块（5.4）+ block 消息补全（5.3）+ SKILL.md opt-in 加载）。

### 5.18 根目录写入审计（SCR-034；v2.7 同轮自愈；v2.9 重扫语义）

根目录写入审计是 terminal 纪律的**第二条检测主干**：观察文件系统**实际
发生了什么**，而非从命令字符串推断意图。命令解析拦截（5.10）保留为廉价事前
检查；审计是可靠安全网——零解析，任何写法（shutil、heredoc、tee/dd、未来
新形态）一律可捕获。

**机制（事后 diff）。**
- **仅** `terminal` 工具调用触发。`pre_tool_call` 快照工作目录根顶层条目
  （`os.scandir`，记录 `name` / `st_size` / `st_mtime_ns` / `is_dir`）；
  `post_tool_call` 重扫并比对。pre 已判 block 的命令（未执行）不做 post 快照。
- **只判断文件条目**（`is_dir == False`）。目录 mtime 变化（会话目录、`.git/`、
  `.hermes/` 内容变动）一律忽略。
- 差异集经共享分类链（`normalize_target` + `classify_target`，带
  allowlist `dirs` 条目、`is_subagent`）判定。**违规** = 新增或修改的根级文件，且
  不在 `allowlist` `files` 条目（5.6）、不在 `allowlist` `dirs` 条目之下，
  且不在任何会话目录内。删除仅记账，永不判违规（5.8 删除原则，report-only）。

**处置阶梯（事后追责闭环）。**
- **L1 教学——fire-once 单次通告**：经 `transform_tool_result` 钩子（Hermes
  官方先例：security-guidance 插件向工具结果追加警告；返回字符串即替换模型
  所见结果）。diff **首次**发现违规时，terminal 结果末尾追加一次通告，写明
  路径与处置。v2.9 文案（与续推兜底共享 `_remediation_instruction` helper——
   单一事实源；v2.9 R4 改述把 config 白名单选项改归属**用户**并显式化闩锁期
   冻结；三审 2026-08-28 增加完整命令指令）："Remediate now: call
   dir_whip_settle(paths=[...]) to move the file(s) into quarantine
   (<root>/.hermes/audit-quarantine/), or move them manually into a Session
   Directory (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). To keep the file(s)
   at the root, ask the user to add them to the allowlist files entries in
   dir-whip-config.yaml (files: [notes.txt]) — give them the exact command
   to run: /dir-whip allow <path> — while the block is active all writes
   are frozen (config edits included). Further writes to the Working
   Directory are blocked until then." **硬约束**：同一违规只通告一次，
  永不随后续结果重复追加（上下文卫生）；错误结果不加装饰。**时序（2026-08-22
  真机实测，30.12）**：Hermes 对 terminal 工具先触发 `transform_tool_result`
  后触发 `post_tool_call`，故审计重扫在 `transform_tool_result` 内先行（通告
  读取 pending 之前）；`post_tool_call` 的重扫保留为顺序无关的 no-op 兜底
  （pre 快照只消费一次，与钩子先后无关）。
- **L2 记账**：verdict 事件 `write-audit-violation` / `write-audit-gate-block`
  流入 5.13 统计与 5.14 事件总线（`dir-whip:write-audit:*`，见下）。
  stats.jsonl 隐私规则不变。
- **L3 清偿闸门（关键）**：未清偿违规按会话**锁存**。下一次**写入类工具**
  （write_file / patch / terminal）调用时，`pre_tool_call` 重扫根目录；文件仍在
  → 经标准 block 通道**拦截**该次写入（消息列举未清偿路径与处置指引）。重扫
   发现路径已消失、已移出根目录、或已按 **config 口径**合法化（allowlist
   `files`/`dirs` 条目或会话目录）→ 闸门打开。v2.9（SCR-041 R1）：清偿重扫
   按 **config 口径**分类——runtime 豁免条目（5.11）**不清偿**已记录违规
   （runtime 豁免=前瞻放行：未来写入过 Tier 0，历史违规保持 pending，直至
   物理清偿或用户亲笔 config 条目）。第一笔写入拦不住；**清偿之前
  后续写入全部冻结**。锁存会话级、会话起始清空；子会话继承父会话锁存
  （child_session_ids 门，5.4/5.16）。注（v2.7）：锁存期间**一切**写入类调用
  被拦——包括清偿性的 `mv`/`rm`——因此 agent 自愈走 `dir_whip_settle`
  工具通道（见下），不走终端命令。
- **L4 自动搬移**：预留方向，**无配置键**（v2.8 起 `write_audit_autofix`
  预留键已删除——配置面去配置化裁决的延伸；未来若立项经独立 SCR 重议）。
  设想为插件将违规文件移入会话 `.tmp/`。搁置原因：自动搬移与"删除=仅报告"
  安全原则相抵，且可能割裂 agent 后续对该路径的引用。

**同轮自愈（v2.7，R4/R5）。** 目标：检测 -> 通告 -> 清偿闭环在触发违规的
用户一轮对话内完成。

- `dir_whip_settle(paths)` 工具（5.7）：唯一的 agent 侧清偿通道。硬约束只接受
  当前会话未决 pending 集合内的路径；经 shutil.move 移入
  `<working_dir_root>/.hermes/audit-quarantine/<时间戳>/`（依 5.6/5.18 语义
  审计安全：快照只看根顶层、目录项永不判违规）；可逆（不删除）；子代理拒绝；
  fail-open 出错返回 error dict、闩锁保持。settle 成功后下一次闸门重扫发现
  原路径已消失 -> 结算 -> 开闸。
  契约（2026-08-26 裁决，插件↔技能一致性）：`paths` 参数以**绝对路径**
  （正斜杠归一）为规范形态——与全部既有 agent 可见面一致（verdict block、
  L1 通告、L3 闸门消息、skill audit_workspace.py 输出）；相对参数宽容接受，
  对 working_dir_root 解析后再比对 pending 集合。**幂等**：路径已不存在
  （用户手动删）视为成功结算 no-op（与闸门 lexists 语义一致）。返回成功
  `{"settled": [<root 相对路径>]}`（相对=隐私合规）或失败 `{"error": "<原因>"}`，
  以 JSON 字符串呈现。统计仅 `allow/settle/write-audit-settle` 入
  stats/log——**不发 bus 事件**（emits 保持 7 不变）。
- **L3 闸门消息（深化裁决 2026-08-26，修订原"消息不动"）**：追加
   `"Remediate now: call dir_whip_settle(paths=[...])"` 行（闸门拦截清偿性
   mv/rm，消息必须指明工具通道；子代理变体保持仅上报父代）。v2.9（R4）：
   主代理 Fix 句把 config 白名单选项改归属**用户**——"or ask the user to add
   them to the allowlist files entries in dir-whip-config.yaml (files:
   [notes.txt]) — give them the exact command: /dir-whip allow <path> —
   while the block is active all writes are frozen (config
   edits included)"——消除"agent 闩锁期自己改 config 清偿"的假可供性
   （真机：两轮被闸门白拦）；子代理变体与 settle 行不变。
- **续推兜底（v2.8 R1/R2 重写；宿主钩子标识 `pre_verify`，对外术语
  「dir-whip 续推兜底」/"dir-whip continuation nudge"）**：插件注册 Hermes 的
  `pre_verify` 钩子；当本会话存在未清偿 pending 违规且宿主报告本回合有文件
  改动（`changed_paths` 非空）时，钩子返回 continue 指令使宿主维持回合继续；
  其他返回值放行回合结束。子代理会话 no-op（清偿是父代职责）。
  - **文案（verbatim 锁，与 L1 共享 remediation 句式 helper；v2.9 三审尾句
    增加选择呈现）**：
    `[dir-whip] {N} unresolved root write(s) remain at the Working Directory root. Remediate now: call dir_whip_settle(paths=["<p1>", ...]) to move the file(s) into quarantine (<root>/.hermes/audit-quarantine/), or move them manually into a Session Directory. Present the resolution choice to the user: move the file(s) (settle), or keep them at the root — for the keep-at-root choice, give the user the exact command to run: /dir-whip allow <path>. Finish only after settlement or the user's decision.`
    路径为绝对正斜杠形态（与 settle 参数规范输入契约一致）；全文不提**运行时
    豁免工具** `dir_whip_allow_path`（不提供豁免选项、不负面点名——v2.9 三审
    精确化：用户 config 命令 `/dir-whip allow` 明确提及，指向用户亲笔销案
    通道；用户裁决 2026-08-28：清偿由用户选择搬走或加白名单，加白名单显示
    可直接复制的完整命令；`--create` 协议核实不矛盾——违规文件必然存在，
    plain 形式正确）。
  - **会话累计上限（v2.8，推翻 v2.7"限流依赖宿主预算、钩子不自加"句）**：
    插件侧常量 `PRE_VERIFY_NUDGE_CAP = 3`（硬编码）——同一会话生命周期内
    至多续推 3 次（计数随 `_audit_session_start` 重置），达到上限后返回
    None 放行回合自然收尾；宿主每轮 verify-nudge 预算
    （`max_verify_nudges()`）保留为外层边界。
  - 触发即记 stats（rule_key `pre-verify-nudge`，reason 带 attempt 累计
     序号，5.13）。
   - v2.9（SCR-041 R1）：nudge 之后添加的 runtime 豁免条目**不清空** pending
     集——续推持续到物理清偿为止（v0.6.0 真机观测到的 allow_path 逃逸就此
     关闭；cap 随后按设计约束循环）。
  已知限制（明示接受）：纯终端违规轮永远不会到达续推兜底（宿主
  `_turn_file_mutation_paths` 只记 write_file / patch 落地）——该场景由 L1
  通告中 settle 工具的可发现性承载闭环；"terminal 写入计入 mutation ledger"
  的 upstream 建议已登记（9 / feedback/10 #6）。

**上下文卫生（硬约束）。** 审计 diff 全程在插件进程内——零上下文成本。
只有单次 L1 通告进对话。未清偿违规在会话状态中静默存在（不周期性重复通知）；
只在 L3 闸门或 `/dir-whip` 报告中出现，绝不靠重复刷屏。

**性能（实测，Windows 10 / NTFS / Python 3.11）。** 单次快照：15 条目（典型
工作区根）0.04ms、95 条目 0.15ms、~4900 条目（病态）6.3ms。一轮审计
（pre+post+diff）典型 ~0.1-0.3ms，低于命令执行时间的 1%。护栏：审计条目
内部常量 2000（v2.8 起不可配置）——根条目超限时跳过审计并一次性 WARNING；
扫描 OSError → 静默跳过（fail-open）。验收：
≤500 条目 p95 < 10ms。

**已知限制。** 后台进程（`cmd &`）在 post 钩子之后落盘 → 落在审计窗口外——
   由 cron audit_workspace.py 兜底（同一 diff 逻辑可复用）。网络盘 stat 可能
   偏慢 → v2.8 起 `write_audit` 配置出口已删除（审计恒开）；受影响用户只能
   经宿主 `plugins.enabled` 白名单停用该档案的插件。同任务并行 terminal 为 last-snapshot-wins（Hermes 每轮
顺序执行工具，风险可接受）。深层子目录内容不审（根级纪律是范围，与 5.10
root-file 语义对齐）。

**与 5.10 的关系。** 审计提供兜底，使 5.10 的启发式是安全的：heredoc（`<<`）
降档与 `=` 残留过滤（SCR-033）可让根级写入溜过前置层，但事后 diff 会捕获
实际落盘文件并进入 L1-L3 阶梯。前置层与审计读同一单一 `allowlist` 键（结构化
`files` / `dirs`），永不打架。rule_key 命名空间隔离（`terminal-*` 与
`write-audit-*`）。

---

## 6. 部署规范

### 6.1 安装（F1）

```bash
hermes plugins install shawVV1992/dir-whip/dir-whip --enable
```

注：仓库默认分支为 `main`（2026-08-14 切回；main 已快进至 v0.2.0 线
57f64bf），不带 pin 的安装即克隆 v0.2.0 线。

注：本机 hermes CLI v0.20.1 安装器拒绝 manifest v2 插件（manifest_version ≤ 1
门，plugins_cmd.py:737-750；运行时加载器接受 v2）。`hermes update` 之前请
手动复制插件包安装（SCR-025）。

一条原生命令安装插件 + 打包 skill + 脚本 + 配置模板。无安装器脚本、无独立
skill 安装（安装器删除，A6；skill 打包 Q9）。

运行时配置仍在 `HERMES_HOME/dir-whip/dir-whip-config.yaml`
（SCR-013 位置：插件目录之外，重装不丢）。

### 6.2 after-install.md 内容

```markdown
## dir-whip 已安装

**Plugin guard**：下次重启 Hermes 后生效。拦截 Working Directory 根目录
Session Directory 之外的写入（白名单文件豁免）。外部路径放行并记录。

**打包 skill**：workspace-organization skill 随插件分发。需要完整纪律参考时
按名显式加载；简短常驻提示覆盖日常行为。

**快捷命令**：
    /dir-whip   # 合并报告：版本、状态、Working Directory + 来源、
                # 配置明细、health、stats 文件路径

**工具**：dir_whip_allow_path —— 本会话放行用户指定路径。

**配置**（可选）：编辑 HERMES_HOME/dir-whip/dir-whip-config.yaml
（allowlist 结构化 files/dirs 相对 Working Directory 根、working_dir_root 覆盖；
v2.8 起 terminal_guard/write_audit/write_audit_entry_cap 三键已删除）。

**验证**：启动新的 Hermes 会话。尝试向 Working Directory 根目录写文件
——应该被拦截并显示修复指引。
```

### 6.3 卸载与升级（Q14）

```bash
hermes plugins remove dir-whip                          # 卸载
hermes plugins install shawVV1992/dir-whip/dir-whip --force      # 更新
```

从 v0.1.x 升级：以 `--force` 重装（subdir 安装的插件目录无 .git，
`--force` 重装即更新路径）。`dir-whip-config.yaml` 重装不丢。

### 6.4 分发（F1/F2）

- 仅经 GitHub 仓库 URL 原生安装。
- 社区插件索引：BLOCK——官方 hermes-plugin-index 仓库尚不存在；
  待其落地后重新评估。
- 不引入 plugin pack（单插件；skill 已打包在内）。
- GitHub Release 整仓归档仅供分发（Hermes CLI 无法从归档安装）。

---

## 7. 验收标准

### 7.1 Skill

- [ ] 打包：`dir-whip/skills/workspace-organization/` 经
      `register_skill()` 注册；可按名显式加载（opt-in）
- [ ] SKILL.md 首行为 `---`（无 BOM、无前导空行）
- [ ] `name` 字段：小写 + 连字符，<= 64 字符
- [ ] `description` <= 1024 字符，前 57 字符含触发词；
      措辞避开 "organize/clean up sessions"
- [ ] SKILL.md 总长 <= 100,000 字符
- [ ] SKILL.md frontmatter `version` 字段已删除（唯一版本 = plugin.yaml）
- [ ] 行为分层已文档化：scope check / 即时纪律 / 治理
- [ ] 写前分类（三分类）已文档化；根禁写三元组已文档化
- [ ] 确认协议已文档化，含示例
- [ ] Cron 治理模式已文档化
- [ ] 创建流程正/反例已包含
- [ ] 拦截响应模板（[Reason]/[Next]）已文档化
- [ ] 子代理文件协议已文档化（父代确保目标目录；缺省 .tmp/；父代可传
      Outputs/；晋升归父代；被拦截子代理上报）
- [ ] 无 memo / 多档案 / 跨档案内容
- [ ] ASCII 直引号、无 emoji、正斜杠

### 7.2 脚本

- [ ] 两个保留脚本：`--help` 输出正确用法；`--workspace` 被接受
- [ ] `--workspace` 等值校验：等于解析出的 Working Directory -> 继续；
      不匹配 -> 退出码 2（create_session_dir 与 audit_workspace）
- [ ] 解析失败 -> fail-open：回退 + 一条简短 stderr WARNING，非致命（交互模式）；
      --gate 模式解析失败 -> 退出码 2 + 无 wakeAgent 行 + 零删除（SCR-042）
- [ ] create_session_dir 退出码：0 / 1 / 2 符合 4.1
- [ ] audit_workspace 退出码：0 / 1 / 2 符合 4.2；`--json` 输出
- [ ] audit_workspace `--gate`：最后一行 wakeAgent JSON（violations/removed/failed
      键恒在）；边界不匹配或 gate 模式解析失败 -> 退出码 2 + 无 wakeAgent 行
      （不唤醒）；--json + --gate 下 stdout 逐行合法 JSON
- [ ] audit `--gate` cron 模式自动清理 `.tmp`（清理内嵌于 audit，见 3.4；
      交互模式保持 --confirm 语义）
- [ ] audit 根白名单读 dir-whip-config `allowlist` `file:` 条目；键缺失
      -> 严格空白名单
- [ ] workspace_resolver.py 解析 dir-whip-config -> terminal.cwd -> fail-open
      （无 memo、无 standalone 分支）
- [ ] 已删除脚本（clean_tmp.py、init_workspace.py）与安装器不在仓库中
- [ ] 跨平台：两个脚本在 Windows 10+ / Linux / WSL / macOS 上可运行
      （可移植路径处理；平台矩阵测试通过）

### 7.3 Plugin

- [ ] plugin.yaml manifest 有效（api_version 为整数；`manifest_version` 因
      v0.20.1 安装器兼容省略，e6e2148；provides_hooks 含 post_tool_call /
      transform_tool_result / post_approval_response / subagent_start /
      subagent_stop / pre_command；emits 已声明，含 write-audit-violation /
      write-audit-gate-block）
- [ ] register(ctx) 无错误完成；打包 skill 已注册；纪律提示已注入（≤200 字）
- [ ] pre_tool_call 拦截根非白名单写入（write_file / patch / terminal block 档）
- [ ] pre_tool_call 放行 allowlist file 条目、会话目录内写入、allowlist
      prefix 条目、运行时豁免表路径
- [ ] pre_tool_call 放行 + 日志外部路径（含兄弟档案目录）；不存在
      跨档案 approve
- [ ] Terminal 不确定写意图 -> 放行 + 日志（无审批门）
- [ ] Fail-open：working_dir_root 无法解析 -> 一次性告警 + 放行
- [ ] 解析成功时以 INFO 日志记录命中配置源（dir-whip-config / profile-config）
- [ ] 每个判定一条结构化单行事件；fail-open/配置异常为 WARNING
- [ ] 统计计数（outcome × tool × rule_key，子代理切分）；post_tool_call
      落盘观察；post_approval_response 批准/拒绝分布
- [ ] stats.jsonl 追加含会话字段；target 相对 working_dir_root；5MB 滚动
      到 `.1`；写失败不影响判定
- [ ] `/dir-whip` 合并报告已实现（allow|remove|list 子命令，统一 Files/Dirs 两段式编号呈现；其它参数 -> `Usage: /dir-whip [allow|remove|list]`）；报告 Allowlist 多行块（头行 + Files/Dirs 各一行、有值段首缩进 2 空格；严格空单行）
- [ ] `dir_whip_allow_path` 保留（会话级，会话起始清空）；
      `dir_whip_settle` 新增（懒注册、pending 集合约束、隔离区搬移）
- [ ] 已删除：workspace_status / workspace_update 命令；
      auto_update / register 工具
- [ ] 事件总线：总线可用时发 dir-whip:* 事件；否则静默降级
      （一条 DEBUG 日志）
- [ ] pre_command 观察记录 surface/command/alias；拦截标记为上游依赖
- [ ] subagent_start 记录 child_session_id/child_role/child_goal（加入
      child_session_ids）；subagent_stop 移除；统计可按子代理筛选；
      子代理写入判定与父代一致
- [ ] 子会话 on_session_start 跳过豁免表清空 / fail-open 重置 / 提醒注入
      （child_session_ids 门，5.4）
- [ ] 被拦截的子代理收到父目标指引变体（无 create_session_dir 指引，5.3）
- [ ] 无异常逃逸 hook 回调；线程安全配置缓存；统计写入加锁
- [ ] Block 消息含修复指引与 [Reason]/[Next] 提示

### 7.4 领域模型

- [ ] CONTEXT.md："Default Working Directory" -> "Working Directory"
      （= profile terminal.cwd；与 Hermes 桌面版设置措辞一致）
- [ ] CONTEXT.md：Profile Workspace Memo / Shared Space / Standalone Mode /
      Workspace Initialization / HERMES_WORKSPACE_ROOT 条目已删除
- [ ] CONTEXT.md：Subagent 与 Discipline Prompt 已定义；guard 判定流精简
      （无跨档案/approve 档）；working_dir_root 语义反转
- [ ] CONTEXT.md：Session Directory 与 Hermes session 区分保留
      （session-librarian 备注）

### 7.5 集成

- [ ] 插件已装 -> 打包 skill 可用（opt-in 加载）+ 纪律提示生效 + 守卫强制
- [ ] agent 写根目录 -> 被拦截；agent 以 [Reason]/[Next] 模板回复
- [ ] cron tick 带 `--gate` 正常（wakeAgent JSON）；边界不匹配 -> 不唤醒
- [ ] 本地 Hermes v0.20.0：插件可安装运行；事件总线被探测并 emit
      （pre_command 拦截保持 observer-only——v0.2.0 范围外）；无总线旧版静默降级
- [ ] 跨平台：Windows 与 WSL 上安装 + 运行验证通过（POSIX 覆盖经 WSL，
      SCR-028）；全量 pytest 矩阵在 Windows + WSL 通过；Linux/macOS 按 8.2
      保持支持（无真机验证，SCR-032）
- [ ] 事件总线（仅含总线的 Hermes）：block / external-write / allowlist /
      审批观察时发出 dir-whip:* 事件
- [ ] 全量 pytest 通过（适配已删脚本、memo 链与工具）

### 7.6 Terminal 写入纪律（SCR-033 并入 SCR-034，统一）

`[A#]` 前缀 = `docs/archive/v0.3.0/scr-033-034-plan.md` 第九节统一验收编号（测试类见
testing-standards.md v0.3.0；v2.6 allowlist 单键，A7 描述已更新）。

- [ ] [A1] feedback/06 存档命令不再误报：2026-08-21 四案（`up` 尾词、
      `bak_cdp` grep 参数、echo 尾词、heredoc `=` 残留）判定 allow /
      external-write；无 block 档误报
- [ ] [A2] `grep pkg>=1.0`（无引号）：不拦；`=` 残留重定向目标路由至
      `terminal-write-uncertain` 事件（保留审计轨迹）
- [ ] [A3] 含裸 `>=` 的多行 heredoc 不产生伪目标；命令产生的**真实**根级写入
      由审计捕获
- [ ] [A4] 链感知提取：`echo hi && touch <根文件>` 现可检出（block 档）；
      `cp a b && echo backed up` 只在自己的段内提取 `b`
- [ ] [A5] 设备路径归一化前豁免：`/dev/null`、`/dev/stdout`、`/dev/stderr`
      不产生 verdict/stats 事件；无 `E:\dev\null` 盘符继承虚构；`2>/dev/null;`
      无粘连目标
- [ ] [A6] 审计内核：快照/diff 恰好检出四态——新增、修改（mtime/size）、
      删除（仅记账）、无关变更（忽略）；目录 mtime 变化忽略
- [ ] [A7] 违规判定：不在 allowlist（files / dirs 条目）/ 任何会话目录内的
      新增或修改根级文件判违规；allowlist file / prefix / 会话目录 / `.git/` 内容写入不算；
      删除仅记账
- [ ] [A8] L1 fire-once：`transform_tool_result` 通告对同一违规恰好出现一次；
      后续工具结果不重复追加；错误结果不加装饰；非字符串结果不动
- [ ] [A9] L3 清偿闸门：未清偿违规拦截下一次写入类工具（write_file / patch /
      terminal）——标准 block 通道；重扫发现路径已消失或已合法化则闸门重开
- [ ] [A10] 跨层：`cat > <根文件> <<EOF` 前置不拦（heredoc 整体降档）但审计
      捕获真实写入并进入 L1-L3 阶梯
- [ ] [A11] 会话隔离：违规锁存会话级、顶层会话起始清空；子代理根级写入归入
      父会话锁存（child_session_ids 门）；父代委托的目标目录保持豁免
- [ ] [A12] 性能：≤500 条目根目录审计单轮 p95 < 10ms；根条目数超过
      审计条目内部常量 2000（v2.8 起不可配置）跳过审计并一次性 WARNING
- [ ] [A13] 配置接线（v2.8 重写）：三键 terminal_guard / write_audit /
      write_audit_entry_cap 已删除——解析层停读、遗留键完全无视（无提示/
      无日志）、拦截与审计恒开；扫描 OSError 静默 fail-open
- [ ] [A14] 统计/事件：verdict 事件 `write-audit-violation` /
      `write-audit-gate-block` 流入 5.13 记录与 5.14 事件总线
      （`dir-whip:write-audit-*`），隐私规则不变；L1 通告本身不产生事件
- [ ] [A15] 回归 + 真机：含新类全量 pytest 绿；stats.jsonl 回放显示
      external-write 噪音回落至真实水平、`write-audit-*` 计数与真实根级写入
      一致（真机阶段）

---

## 8. 约束与红线

### 8.1 安全

- 禁止 `rm -rf`、`del /S/Q`、批量重命名、递归删除
- 脚本删除需 `--confirm`（默认 dry-run）；audit 仅在 cron 模式删除
  （.tmp 自动清理，见 3.4）——其余情形只提议动作
- 任何文件不写入密钥/凭证；stats.jsonl 永不记录文件内容或外部绝对路径
- Plugin guard 为 fail-open（永远不崩溃 agent）

### 8.2 兼容性

- Python 3.11（命令：`python`）
- 平台：Windows 10+ / Linux / WSL / macOS——均可安装可运行；
  脚本用 os.path（可移植）并在平台矩阵上测试；插件路径处理按平台分支
  （Windows MSYS 映射 / casefold、POSIX normpath 恒等——SCR-006）
- Hermes plugin API（register(ctx)、hooks、ctx.profile_name、
  register_skill、register_system_prompt_section）
- 必须在本地 Hermes v0.20.0 上可运行：事件总线运行时探测（本地 v0.20.0 安装
  已具备）；不含总线的版本静默降级——见第 9 节
- Agent Skills 标准（SKILL.md frontmatter；`version` 字段已删，非必需）

### 8.3 代码风格

- 仅 ASCII 直引号
- 无 emoji
- 路径使用正斜杠（输出和文档）
- 代码中不加注释，除非解释非显而易见的逻辑
- 脚本：自包含，无交叉 import（例外：`workspace_resolver.py` 共享 Working
  Directory 解析模块；见第 4 节与 engineering-constraints）
- Plugin：遵循 Hermes 标准范式（register(ctx)、**kwargs、JSON 返回）
- 纪律提示：≤200 字、四要素、最小化（每轮计费）

### 8.4 架构

- Skill 和 Plugin 零运行时耦合（仅同包分发）
- 脚本 agent-agnostic（任何能跑 Python 的 agent 可用）
- 无 memo / 跨档案 / 多档案机制
- 代码标识符不变（working_dir_root、--workspace、session_dir、rule_key；
  配置键 `allowlist` 在 v2.7 重构为 `{files: [], dirs: []}` 映射（相对
  working_dir_root 的路径）——v2.6 平铺 `file:` / `prefix:` 标签列表删除
  clean-break；dir-whip 品牌本身经 SCR-030
  改名，见 4.5）
- Hermes 特有概念明确标注，便于未来 Pi 适配
- 逻辑中不硬编码绝对路径（配置/模板使用占位符）

---

## 9. 上游依赖

| 能力 | 本地 Hermes v0.20.0 状态 | v0.2.0 行为 |
|------|--------------------------|-------------|
| `register_skill()`（opt-in 加载） | 可用（v0.1.0 未用） | 使用；skill 按名显式加载 |
| `register_system_prompt_section()`（≤4000 字符） | 可用 | 使用；≤200 字 |
| 新 hooks：post_tool_call / post_approval_response / subagent_start / subagent_stop | 可用 | 使用（观察） |
| pre_command hook | 可用（observer-only v1） | 仅观察用；拦截为二期预留 |
| Manifest v2 字段（manifest_version、api_version、provides_hooks、emits） | 可用 | 声明 |
| `hermes plugins install`（manifest v2） | 本机 v0.20.1 安装器门：manifest_version ≤ 1（plugins_cmd.py:737-750）；运行时加载器接受 v2 | `hermes update` 后验证原生安装；此前手动复制（SCR-025） |
| 插件间事件总线（`ctx.emit`/`subscribe`） | 本地 v0.20.0 已具备（随版本而定） | v0.2.0 范围内：运行时能力探测；可用时 emit 生效，否则静默降级 |
| pre_command 拦截 | 上游 middleware #64204/#64231 | **v0.2.0 范围外**（二期预留） |
| 社区插件索引（hermes-plugin-index 仓库） | 未创建（上游） | BLOCK；仅原生 URL 安装 |
| capabilities 声明 + consent 流程 | 可用（注册表仅覆盖 override/平台动作类表面） | 不声明：register_skill / hooks / tools / emit 不受能力门控；无 consent 屏幕 |

---

## 10. 二期预留

v0.2.0 不实现。设计决策不应阻塞这些方向：

| 项目 | 备注 |
|------|------|
| 社区插件索引 | 官方仓库落地后重新评估 |
| pre_command 拦截 | 上游 middleware #64204/#64231 落地后启用 |
| 事件总线消费者 | 在用含总线的 Hermes 之后构建 |
| Pi extension | Skill agent-agnostic；脚本为 CLI 工具；仅 guard 层需适配器 |

---

## 变更日志

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-08-02 | 初始草稿 | Grilling session 决策 |
| 2026-08-06 | v1.1 — 统一 guard 链（SCR-001/002/004） | 联合设计评审 + 查漏修复 |
| 2026-08-06 | v1.1 勘误 — 一致性修复（7 项） | 文档评审发现 |
| 2026-08-06 | SCR-005 — working_dir_root 配置源澄清 | docs/spec-change-005 |
| 2026-08-06 | SCR-005 补遗 — 命中源 INFO 日志 | 用户决策 2026-08-06 |
| 2026-08-06 | SCR-005 任务 13.6 执行 — 命中源 INFO 日志实施 | 任务 13.6 |
| 2026-08-06 | SCR-006 — 路径归一化步骤 0（MSYS 映射 + 盘符继承） | docs/spec-change-006 |
| 2026-08-09 | v1.2 — SCR-011 公开安装（memo 化校验、原生安装通道、快捷命令） | docs/spec-change-011 |
| 2026-08-09 | SCR-013 — dir-whip-config.yaml 迁移至 HERMES_HOME/dir-whip/ | docs/spec-change-013 |
| 2026-08-09 | v1.2 勘误 — 一致性修复（8 项） | 文档评审发现 |
| 2026-08-09 | 根部文件白名单跨层统一（guard 与 audit 读同一 allowed_root_files） | 用户决策 2026-08-09 |
| 2026-08-09 | v1.3 — Phase 8 实施完成（SCR-011/013）；435 测试通过；skills_guard verdict = safe | Phase 8 完成 |
| 2026-08-09 | memo 模型修订（skill 只读；plugin 唯一写者） | 用户决策 2026-08-09 |
| 2026-08-09 | 登记持久化（config-first 经 set_config_value） | 用户决策 2026-08-09 |
| 2026-08-09 | v1.2 一致性修订 — 9 处修复（会话级豁免表、gate fail-closed、curl -O approve 档等） | 文档评审发现 |
| 2026-08-10 | v1.4 — 部署修订（SCR-015/017）：扁平仓库布局、install.sh、分发章节 | SCR-015/017 设计文档 |
| 2026-08-13 | v2.0 冻结 — 用户经三轮评审（范围修订 / 上游源码校审 / 子代理策略）批准规范。状态：v0.2.0 权威基线；SCR-024 进入实施阶段（testing-standards.md v0.2.0 已重建；tasks.md Phase 2） | 用户决策 2026-08-13 |
| 2026-08-13 | v2.0 子代理勘误（用户确认 + 源码核验）：子会话**会**触发 on_session_start（同一 conversation loop、无子代理守卫；subagent_start 先于 child 首轮）——防御 = child_session_ids 门（5.4/5.16：子会话跳过豁免表清空 / fail-open 重置 / 提醒注入）。子代理文件协议细化（Q1-Q3）：父代委托前确保目标目录；子代理写父代传递目录（缺省 .tmp/，显式传递时可为 Outputs/ 或独立子目录）；不自建会话目录、不自晋升；被拦截子代理收父目标指引变体并上报（3.9/5.3/5.16/7.1/7.3） | 2026-08-13 子代理策略确认 |
| 2026-08-13 | v2.0 校审勘误（对安装的 hermes-agent 源码核验）：事件总线在本地 v0.20.0 已具备（运行时探测；原列为上游缺失）；emit() 只收裸名（命名空间强制，带 `:` 抛错）；pre_command 字段为 `alias_used`；subagent_start 字段为 `child_role`/`child_goal`；api_version 为整数；skill 加载路径 = 限定名 `dir-whip:workspace-organization`（skill_view）；capabilities/consent 不适用（不声明、无 consent 屏）；install --force 为 tmp-replace（无 stale .archive 残留）；脚本档案定位方案（dir-whip-config -> HERMES_SESSION_PROFILE -> 档案枚举 + TERMINAL_CWD 候选根 -> fail-open；显式 --workspace 等值 / CWD 包含匹配最长者）；cron 用限定名；8.1 澄清 audit 仅 cron 模式删除；7.2 补内嵌 .tmp 清理验收项；doctor 对覆盖值 ≠ terminal.cwd 告警；事件 payload 隐私对齐；subagent_stop 已定义；豁免表清空仅顶层会话；同步更新 4.1/4.2/4.4/5.2/5.4/5.7/5.13/5.14/5.15/5.16/6.3/7.5/8.1/8.2/9/10 | 2026-08-13 校审（hermes-agent @ 9460cc11d） |
| 2026-08-13 | v2.0 勘误 — 范围修订：跨平台（Windows 10+ / Linux / WSL / macOS）与插件间事件总线移入 v0.2.0 范围（总线：含总线 Hermes 上条件生效，否则静默降级）；pre_command 拦截保持范围外（observer-only；二期预留）。同步更新 1.3/1.4/5.14/8.2/9/10/7.2/7.5 | 用户决策 2026-08-13 |
| 2026-08-13 | v2.0 — v0.2.0 重写（feedback/04 grilling，Q1-Q15）：单档案化（跨档案/memo/Shared Space/Standalone Mode 删除）；terminal 守卫精简（粗拦截档，不确定 -> 放行+日志）；dir-whip-config 唯一配置源 + working_dir_root 反转（TERMINAL_CWD 删除）；skill 打包进插件（register_skill，opt-in；SKILL.md version 字段删除）+ 常驻提示 ≤500 字；根禁写三元组 + 写前分类 + [Reason]/[Next] 模板；脚本精简为 create_session_dir/audit_workspace（+workspace_resolver），--workspace 等值校验，fail-open 降级；cron 治理保留（A4 撤销）；结构化日志 + stats.jsonl（5MB 滚动、隐私）+ status/stats/doctor + dir-whip:* 事件（静默降级）+ pre_command 观察 + 子代理观察；安装器删除、纯原生安装、manifest v2、无 pack、社区索引 BLOCK；术语 "Default Working Directory" -> "Working Directory"。状态：草案 | 2026-08-13 grilling 会议（internal/feedback/04） |
| 2026-08-13 | SCR-026 档案 HOME 布局（真机发现）：运行时 Hermes 对非默认档案把 HERMES_HOME 设为档案目录本身（如 HERMES_HOME=.../profiles/learn；stats.jsonl 落在档案 home 佐证），因此该布局下档案配置即 HERMES_HOME/config.yaml——4.4/5.5 步骤 2 路径按布局识别（根 home 布局：HERMES_HOME/profiles/<名>/config.yaml；档案 home 布局：HERMES_HOME/config.yaml）。修复前 learn/job-hunt 档案解析失败、守卫静默禁用（fail-open） | 2026-08-13 真机实测（learn 档案根写未被拦截） |
| 2026-08-13 | SCR-027 解析会话级化（真机发现）：桌面 agent 进程在激活档案下 register，进程级缓存导致同进程后续不同档案会话读到错根（会话 20260813_223050_dbe71b：default 档案会话显示 learn 的根；其 stats 落 learn 的 home）。5.8「解析仅 register 一次」修订：Working Directory 于 on_session_start 按会话 ctx.profile_name 重解析（会话级缓存；子会话继承父值；fail-open 不保留陈旧值）；5.5 步骤 2 增补「default 会话而 HERMES_HOME 为命名档案目录」情形（default home = 上两级）；stats.jsonl 落点按会话档案 home（5.13） | 2026-08-13 真机实测（桌面端 default 档案会话 status 显示 learn） |
| 2026-08-13 | SCR-028 跨平台路径处理（WSL 真机发现）：Windows 风格目标（盘符根、MSYS/Cygwin、反斜杠根）在任意宿主遵循 Windows 归一化与大小写不敏感包含判定（8.2/SCR-006 澄清——WSL/Git-Bash 会话可携带 Windows 风格根）；此类根形式目标绝不与 base 拼接（posixpath.isabs 在 POSIX 宿主判定不足） | 2026-08-13 真机实测（27.6 WSL POSIX 套件 19 失败修复） |
| 2026-08-14 | SCR-030 项目改名 workspace-guard → dir-whip（全量品牌/代码/配置改名；stats.jsonl、config.py、技能名 workspace-organization 与 guard 判定标识符不变；历史面保持原样） | docs/scr-029-030-plan.md |
| 2026-08-14 | SCR-029 命令精简——/dir-whip 单命令无子命令合并报告（方案 A）；status/stats/doctor 子命令删除；统计展示代码删除、记录后端不变；新增版本行（plugin.yaml 单一版本源，失败回退 unknown） | feedback/05（Q1-Q4） |
| 2026-08-14 | SCR-031 报告字段标签重设计（5.7）：`Guard` -> `State`；`terminal_guard` -> `Terminal Guard`；`exempt_paths` -> `Exempt Paths`；`allowed_root_files` -> `Root Allowlist`（含 `(strict empty whitelist)` -> `(strict empty allowlist)`）；`self-check` -> `Health`；`stats file` -> `Stats File`。配置键与取值不变；`Working Directory` 与 `WARNING` 标签不变 | 用户需求 2026-08-14（README 修订轮） |
| 2026-08-14 | v2.0 末轮更新：规范临时激活（ACTIVE）后再次冻结（FROZEN）至实施完毕状态。SCR-025 登记——原生分发经 `hermes plugins install shawVV1992/dir-whip/dir-whip --enable`；本机 CLI v0.20.1 安装器门限 manifest_version ≤ 1，原生安装待 `hermes update` 后验证，此前手动复制（6.1/9 已更新）；SCR-032 验证范围决策：7.5 验收 5 修订（macOS 真机验证删除——用户决策；Linux/macOS 按 8.2 保持支持）；ADR 0004 默认分支措辞修正（6.1 已就位） | 用户决策 2026-08-14（激活 + 验证范围 + 分发） |
| 2026-08-22 | v2.1 — SCR-033 terminal 误报修复（feedback/06，需求评审后）：链感知目标提取（`;` 作为分隔符输出；`&&` / `;` / `|` / 换行为链边界；按段提取目标；链式写入现可检出）；重定向目标不得以 `=` 开头（`>=` 残留）；`/dev/null` / `/dev/stdout` / `/dev/stderr` 归一化前豁免（无 verdict/stats 事件、无盘符继承）；含 `<<` 的命令整体降 uncertain 档（放行+日志）。Windows 盘符继承其余语义不变（SCR-006）。python/shutil 等 uncertain 档写入检测升级列非目标。规范临时激活（ACTIVE）后再次冻结（FROZEN） | 用户决策 2026-08-22（feedback/06 SCR 评审） |
| 2026-08-22 | v2.2 — SCR-034 根目录写入审计（新特性，设计评审后）：terminal 纪律第二条检测主干——观察文件系统 delta 取代解析命令意图。新增 5.18：快照/diff 机制（仅 terminal、仅文件条目、共享分类链），L1 fire-once 单次通告（transform_tool_result，上下文卫生硬约束），L2 write-audit-* 统计与事件，L3 清偿闸门（未清偿冻结写入类工具），L4 自动搬移（默认关）。配置键 write_audit / write_audit_entry_cap / write_audit_autofix 预留（5.6）；rule_key write-audit-violation / write-audit-gate-block（5.13）；事件 dir-whip:write-audit-*（5.14）；实测性能单轮 ~0.1-0.3ms、entry_cap 2000。为 SCR-033 的 heredoc/`=` 前置漏检提供兜底。规范临时激活（ACTIVE）后再次冻结（FROZEN） | 用户决策 2026-08-22（SCR-034 设计评审） |
| 2026-08-22 | SCR-034/033 合并（流程记录，spec 正文无改动）：SCR-033（5.10 误报修复）标记并入 SCR-034——两者解决同一问题的两面（前置拦错 vs 事后漏拦）。统一双层设计见 docs/scr-033-034-plan.md：前置层 = SCR-033 四项修法 + `=` 残留改路由 terminal-write-uncertain 日志；审计层 = 5.18 阶梯（使前置层可以放心宽容：heredoc 维持整体降档、不解析正文）。spec 内容不变——5.10 与 5.18 已分别承载两半；本行仅记录登记册合并决策 | 用户决策 2026-08-22（合并统一方案） |
| 2026-08-22 | v2.3 — SCR-034 验收条款补全：新增 7.6「Terminal 写入纪律」（15 条，A1-A15 映射统一提案）完成验收链 spec 7 → testing-standards 矩阵 → 测试类；版本 2.2 → 2.3（激活、修订、再冻结） | 用户决策 2026-08-22（设计统一方案的验收标准与测试用例） |
| 2026-08-22 | SCR-034 30.12 真机发现（spec 5.18 L1 文字澄清，不升版本）：Hermes 对 terminal 工具先触发 `transform_tool_result` 后触发 `post_tool_call`，L1 通告读取的 pending 恒为空（审计重扫原仅在 post）→ 通告永不出现。修复（代码 + 回归测试）：审计重扫移入 `transform_tool_result` 内部、先于通告读取 pending；`post_tool_call` 的重扫保留为顺序无关 no-op 兜底（pre 快照无论钩子先后只消费一次）。回归测试 `TestWriteAuditNotice::test_transform_runs_audit_before_post_notice_appears`；全量 493 passed / 5 skipped；真机复验通告已附加 | 2026-08-22 真机验证（30.12） |
| 2026-08-22 | 常驻纪律提示约束收紧（spec 3.7/5.17 + 验收）：≤500 → ≤200 字。实际提示 181 字，符合新上限。规范临时激活（ACTIVE）后再次冻结（FROZEN） | 用户决策 2026-08-22 |
| 2026-08-24 | scr-033-034-plan.md 归档至 docs/archive/v0.3.0/（SCR-035 立项归档动作），7.6 验收编号引用路径同步扫改；流程性修订，spec 正文条款无改动，版本维持 v2.3 FROZEN | SCR-035 归档整理（2026-08-24） |
| 2026-08-24 | v2.4 — SCR-035 结构性修订（规范按用户决策提前激活）：5.1 目录图重写为 11 模块布局（register/hooks 入 `__init__.py`，guard/terminal/paths/events/audit/sessions/state/config/stats/report）附单向依赖说明与双实现 parity 说明；4.5 增前向注记（dir_whip.py 消解、config.py 名保留）；5.7 报告首行措辞改「包根 plugin.yaml」（原「config.py 旁」）；5.2 manifest 示例版本改占位符。行为条款相对已冻结 v2.3 零变化——仅结构/文档性编辑 | 用户决策 2026-08-24（激活 spec 并按 SCR-035 重新整理） |
| 2026-08-24 | v2.4 再冻结——SCR-035 结构性修订后重新冻结。行为条款无变化 | 用户决策 2026-08-24（冻结） |
| 2026-08-25 | v2.5 已激活——SCR-037 v0.4.1：5.6 D1 模板默认值 `["AGENTS.md"]`→`[]`（宿主 agent_config_mod 扫描器 skills_guard.py:462→dangerous 拦截，发布物零字面量红线）、5.7 `/dir-whip allow|remove|list` 子命令（回退 SCR-029 单命令）、4.2 audit 白名单措辞。规范 2026-08-25 激活，待 37.7 验证后再次冻结 | 用户决策 2026-08-25 |
| 2026-08-25 | v2.6 已激活——SCR-037 修正 v2.6 B2 单键 allowlist（BREAKING）：`exempt_paths` + `allowed_root_files` 作为配置键已删除，不向后兼容（用户 2026-08-25 B2 决策，feedback/09）；单一统一 `allowlist: []` 且条目区分为 `file:<basename>` | `prefix:<abs-path>`（prefix 可带末尾 /）。5.6 模板替换，5.3 Tier0 = allowlist prefix 或 runtime-allowlist / 根文件 = allowlist file，5.7 `/dir-whip allow <file|prefix:PATH|PATH/>` 智能区分（无斜杠→file，含斜杠/prefix:→prefix），报告单行 `Allowlist: Files: ...  Prefixes: ...`，5.18 审计对齐单键，5.11/5.13/5.14/6.2/7.x/8.4 已扫改。旧键已删除。规范 2026-08-25 激活，待冻结 | 用户决策 2026-08-25（B2 彻底切换） |
| 2026-08-26 | v2.7 已激活——SCR-039 v0.5.0（feedback/10）：(1) 提示通道重排——常驻纪律提示移除（3.7/5.17），新增条件化会话开始纪律块（5.4：`discipline_applies` 谓词 + `agent_cwd_fn` 注入槽接宿主 `resolve_agent_cwd`；≤280 chars 锁定；报告 Reminder 状态行 injected/skipped-outside/skipped-child/unavailable）；(2) 同轮自愈——`dir_whip_settle` 工具（懒注册、pending 集合硬约束、`.hermes/audit-quarantine/<ts>/` 搬移、子代理拒绝），L1 通告升级附 settle 指引，L3 锁存注记（锁存期间清偿 mv/rm 亦被拦 -> 走工具通道），注册 `pre_verify` 续轮兜底（混合轮次硬保证；纯终端轮=明示接受限制 + upstream 建议登记于 9）；(3) 结构化 allowlist（BREAKING）——`allowlist` 改为 `{files: [...], dirs: [...]}` 映射、条目相对 working_dir_root（dirs 多级递归子树豁免；根自身与根外条目拒绝；v2.6 平铺 `file:`/`prefix:` 标签列表删除 clean-break，遗留 fail-closed 忽略 + `/dir-whip list` 提示）；`/dir-whip allow|remove|list` 统一 Files/Dirs 两段式编号呈现（bare remove 枚举当前条目；裸名磁盘感知判别）；block 消息新增放置意图规则 + allow_path 指引。2/3/4/5/6/7/8 全扫描。完成后 re-freeze | 用户决策 2026-08-26（feedback/10） |
| 2026-08-27 | v2.8 已激活——SCR-040 v0.6.0（scr-040-plan.md / feedback/11，发布后讨论两轮裁决）：(1) 续推兜底优化——nudge 文案 settle-first 重写（与 L1 共享 remediation 句式 helper、精确 `dir_whip_settle(paths=[...])` 调用形态、全文不提 allow_path）+ 插件侧会话累计 cap=3（`PRE_VERIFY_NUDGE_CAP` 硬编码、会话起始重置、宿主每轮预算保留为外层边界——推翻 v2.7"钩子不自加"句）；依据真机实验 A（nudge 一次转化硬证据 + agent 绕 allow_path 偏差）；(2) 可观测性包——5.13 新增四条 stats rule_key（`pre-verify-nudge` / `runtime-allowlist-add` / `session-reminder` / `write-audit-settle-rejected`；emits 保持 7）+ 诊断日志文件 dir-whip.log 小节（新模块 logsetup.py、profile-aware、DEBUG 全量、CLH→stdlib→console 三级降级、5MiB×3+delay+utf-8、绝对路径口径）；(3) 报告面重构（5.15→5.7 布局重写）——State 值改 enabled/disabled、删 Terminal Guard 与 Reminder 行、Allowlist 多行化（头行+Files/Dirs 各一行、有值段首缩进 2 空格、严格空保留单行）、Health 置末尾 Good/简要问题两态、末段新增 Debug Log 行；(4) 三键去配置化（BREAKING，5.6）——terminal_guard/write_audit/write_audit_entry_cap 停止用户配置、内部常量化（拦截/审计恒开、cap=2000）、遗留键完全无视，`write_audit_autofix` 预留键一并删除（L4 转为无键预留方向）；(5) shipped 模板注释修正为 v2.7 结构化教程。术语统一：对外「dir-whip 续推兜底」/"dir-whip continuation nudge"，宿主钩子标识 pre_verify 仅存实现语境。(6) 放置措辞去歧义（R9，2026-08-27 并入；真机事故会话 20260827_222411_245249：block 消息箭头简写 `deliverable -> Outputs/` 被误读为字面路径模板，文件落 <session>/deliverable/Outputs/）——block 消息行改述为 `Then write the deliverable to Outputs/<filename> (or scratch to .tmp/<filename>).`（5.3），会话开始纪律块括号句同步改述（3.7/5.4；251 字符 ≤ 280 锁定），create_session_dir.py stdout 增加放置提示行（4.1 输出契约新增）；文档先行，代码/测试随 SCR-040 实施批落地（40.R9.1）。session_dir_pattern 值得开发登记 feedback/11 待独立 SCR。2026-08-28 实施批完成 re-freeze（套件 644 passed / 5 skipped / 0 failed） | 用户决策 2026-08-27（scr-040-plan.md 两轮裁决：方案 A 四条记录 / 日志绝对路径 / 报告重构+Health 置末尾+Allowlist 多行 / 三键去配置化+遗留完全无视 / 版本目标 0.6.0） |
| 2026-08-28 | v2.9 激活 — SCR-041 v0.6.1（scr-041-plan.md，用户 2026-08-28 决策，v0.6.0 真机六场景验证后立项）：(1) 重扫语义收紧（R1，5.18）——L3 清偿重扫按 config 口径分类 pending 路径（allowlist files/dirs + session-dir）；runtime 豁免条目不再销案已记录违规（runtime 豁免=前瞻放行：未来写入过 Tier 0，历史违规保持 pending）；立项证据=v0.6.0 真机逃逸（会话 20260828_135518_31b5d6 / 20260828_135546_abff8d：agent 在 nudge 后自助 allow_path，pending 清空，cap=3 无法自然到达）；(2) allow_path 入口门禁（R2，5.11）——子代理调用拒绝（父代指引变体 + stats rule_key allow-path-subagent-rejected，bus-skip；制裁只能自上而下流动，用户裁决），working_dir_root 自身拒绝作为 path 参数；(3) 两步用户确认协议（R3，5.11）——dir_whip_allow_path 增加可选 confirm 参数：首调返回确认载荷（风险 + 去除方法 + 转述指令）且不添加、路径记入会话内存 confirmation-issued 集合；confirm=true 仅对已签发路径接受（强制两步）；去除方法仅文档化（会话结束自动失效；持久豁免走 config，/dir-whip remove 可移除）——无撤销能力（用户裁决）；(4) L1/L3 文案改述（R4，5.18）——config 白名单清偿选项改归属用户（ask the user to add），闩锁期写类全冻结显式化（含 config 编辑），消除真机白耗两轮的假可供性。版本目标 v0.6.1（patch——执法加固）。2026-08-29 实施批完成 re-freeze（套件 664 passed / 5 skipped / 0 failed） | 用户决策 2026-08-28（方案A + 子代理策略A + 仅文档化去除 + v0.6.1 patch） |
| 2026-08-29 | v2.10 激活 — SCR-042 v0.6.2（scr-042-plan.md，2026-08-29 范围裁决，feedback/13 skill 脚本安全评审后立项）：skill 脚本安全加固，9 项 5 需求组——（R1）删除面：--gate 模式将 Working Directory 解析失败升格为门失败（退出码 2、stderr 原因、无 wakeAgent 行、零删除——未解析根永不经 fail-open CWD 兜底成为删除目标；交互 fail-open 不变），.tmp 清理扫描边界排除 symlink 会话目录与 symlink .tmp 目录（双层）；（R2）导入加固：两脚本经 importlib 按 __file__ 绝对路径加载捆绑 workspace_resolver 并注册 sys.modules（CWD/PYTHONPATH 阴影无法转移共享模块）；（R3）大小写 parity：审计根文件白名单匹配复用 resolver 的 Windows casefold 匹配器（与 allowlist.py 由构造保证 parity，ADR-0006）、Outputs 黑名单判断大小写不敏感、.hermes 豁免 Windows casefold（镜像 report.py _case_eq）；（R4）gate 输出契约：wakeAgent 行恒四键 {wakeAgent, violations, removed, failed}（清理失败对 cron 可见，N8 随 M2 闭合）、gate+json 下 stdout 逐行合法 JSON（json 模式删明文删除报告，失败明细走 stderr）；（R5）健壮性小项：三脚本 stdout/stderr reconfigure(errors=replace)（cp936 非 ASCII 路径不再崩溃）、create_session_dir 解析根补 abspath（绝对路径契约，N2）、hermes home 在 LOCALAPPDATA 缺失/空时回退用户主目录（永不相对 CWD，N7）。3.4/4.2/4.4/7.2 已更新。其余 7 项低危（L1/L2/N4/N5/N6/L3+N3/L4）登记不处置。版本目标 v0.6.2（patch——无配置面变化；hooks 9 / emits 7 不变）。2026-08-29 实施批完成 re-freeze（commits cdeb3d6/dbd2baf/0d68c37/ac58340/cb8929e，套件 687 passed / 5 skipped / 0 failed） | 用户范围裁决 2026-08-29（9 项：必修 2 + 建议 5 + 顺带 2） |
