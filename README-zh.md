![banner](assert/image/banner.png)

# dir-whip

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version: 0.3.0](https://img.shields.io/badge/version-0.3.0-blue.svg)](https://github.com/shawVV1992/dir-whip)

[English](./README.md) | [中文版](./README-zh.md)

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

dir-whip 为 [Hermes-agent](https://github.com/NousResearch/hermes-agent) 工作目录（Initial Project Directory）提供三层文件纪律保障：技能教导规则、插件以 8 个钩子拦截违规、审计层捕获漏网。1 条命令即可安装，支持 Windows、Linux、WSL 与 macOS。

注意：dir-whip 权限范围仅限于工作目录（Initial Project Directory），工作目录之外的写入不受管控，新建的项目目录不受管控。

[核心能力](#核心能力) · [安装与快速上手](#安装与快速上手) ·
[设计架构与能力边界](#设计架构与能力边界) · [命令](#命令) ·
[高级用法](#高级用法) · [安全与风险](#安全与风险) ·
[贡献](#贡献) · [License](#license)

## 核心能力

1. **教罚结合：** skill 教纪律、plugin 强制执行，默认工作区管理纪律有效稳定，文件管理不再混乱。
2. **插件的双层检测：** 在插件中，前置层拦截根级违规写入并附修正指引；审计层以快照 diff 事后兜底。
3. **可观测：** 定义 7 类 `dir-whip:*` 事件保存至 stats.jsonl（5 MB 滚动），可观测溯源。
4. **定时治理：** 针对 cron 任务采用 wakeAgent / [SILENT] 模式，不打断 agent 执行。下次 cron tick 继续治理。
5. **子代理纪律：** 子代理写入父代指定目录，绝不自行创建会话目录。

## 安装与快速上手

### 前置要求

- 安装 Hermes 0.20.0 或更高版本。
- 安装命令需要可访问 GitHub。

### 快速上手

1. 安装插件及其捆绑的技能、脚本与配置模板

```bash
hermes plugins install shawVV1992/dir-whip/dir-whip --enable
```

2. 重启 Hermes —— 插件在下一次会话生效

3. 验证生效配置及其来源
```bash
/dir-whip
```

插件在重启 Hermes 后生效。无需安装脚本，也无需单独安装技能。

### 更新

```bash
hermes plugins install shawVV1992/dir-whip/dir-whip --force
```

重装不会清除 `dir-whip-config.yaml`。

### 卸载

```bash
hermes plugins remove dir-whip
```

### 开启 / 关闭

```bash
# 开启插件
hermes plugins enable dir-whip

# 关闭插件
hermes plugins disable dir-whip

```

## 设计架构与能力边界

### 设计思路

- **教罚分离** —— skill 与 plugin 零运行时耦合，只共享同一份配置与同一套
  判定规则。
- **允许误放、绝不误拦** —— 前置层fail-open策略，审计层可靠兜底。
- **观察事实，而非推断意图** —— 审计层 diff 实际落盘的文件，而非解析
  命令字符串。


### 功能架构

| 层 | 职责 | 形态 |
|----|------|------|
| **Skill（教导）** | 纪律参考 | 捆绑的 `workspace-organization` 技能（可选加载）+ 常驻提示（≤200 字） |
| **Plugin（强制）** | 拦截违规落地前 | 8 个钩子：`pre_tool_call` 拦截 + 写入审计 + 会话/子代理观察 |
| **Scripts（工具）** | Agent 和 cron 的 CLI 辅助 | `create_session_dir.py` / `audit_workspace.py` / `workspace_resolver.py` |
| **Config** | 唯一配置源 | `dir-whip-config.yaml` |
| **Observability** | 记录与报告 | stats.jsonl + `dir-whip:*` 事件 + `/dir-whip` |

每个产生文件的 Hermes 对话都会在工作目录根部得到一个会话目录：

```
<Working Directory>/
├── AGENTS.md                      # 根白名单文件（默认）
├── 20260822_143000_ReportTask/    # 会话目录（懒创建）
│   ├── Outputs/                   # 正式交付物
│   └── .tmp/                      # 中间文件（可按时间清理）
└── .hermes/                       # Hermes 自身目录
```

- 命名 `YYYYMMDD_HHMMSS_TaskName/`，时间戳必须真实（插件校验）。
- 懒创建：首次文件写入时才建，不产出文件的对话不建目录。
- 根目录只允许三样东西：白名单文件、会话格式目录、`.hermes/`。

### 执行策略

终端写入在 shell 层拦截：重定向（`>` `>>` `1>`
`2>`）、`touch`，以及 `cp`/`mv` 的目标路径。双层纪律执行：

![写入纪律流程（含终端写入观察路径）](assert/image/write-guard-flow.svg)

**前置层（宽容快速）** —— 链感知提取按 `&&` / `;` / `|` /
换行切分命令链，仅在每个命令段内提取写入目标。以 `=` 开头的重定向目标被排除。
设备路径（`/dev/null`、`/dev/stdout`、`/dev/stderr`）在归一化前豁免。含 `<<`
（heredoc）的命令整体降档至"放行并记入日志"，不解析正文。该层的设计原则是
**允许误放、绝不误拦**。

**审计层（可靠主干）** —— 对根目录文件条目做前后快照 diff，捕获前置层漏过的
任何文件。检测到违规时，L1 通告写明路径与处置（移入会话目录 / 加入根白名单）；
L3 闸门冻结后续所有写入类工具调用，直至文件被移除或移入合法位置。

### 能力边界

| 管 | 不管 |
|----|------|
| 根目录非白名单写入（`write_file` / `patch` / `terminal`） | 会话目录内的写入 |
| 根写入事后审计 + 清偿闸门 | 根白名单文件（`allowed_root_files`） |
| 会话目录结构合规（audit 脚本） | 豁免路径（`exempt_paths` + 运行时豁免） |
| | Working Directory 之外的一切（放行 + 日志） |
| | 只读工具与只读命令 |
| | 删除操作（仅记账，永不判违规） |

## 命令

`/dir-whip` 输出一份合并报告：

| 字段 | 含义 |
| ---- | ---- |
| `[dir-whip] v<version>` | 插件版本，取自插件的 plugin.yaml（读取失败显示 `unknown`） |
| `State` | `ACTIVE`，或 Working Directory 无法解析时的 `FAIL-OPEN` |
| `Working Directory` | 生效值 + 解析来源（见下一行） |
| source | `guard-config`（dir-whip-config.yaml）· `profile-config`（档案 `terminal.cwd`）· `fail-open` |
| `Terminal Guard` | `enabled` / `disabled`（`terminal_guard`） |
| `Exempt Paths` | 逗号分隔的豁免路径，或 `(none)`（`exempt_paths`） |
| `Root Allowlist` | 逗号分隔的根白名单，或键缺失时 `(strict empty allowlist)`（`allowed_root_files`） |
| `Health` | `OK`，或 `PROBLEM` 并逐行列问题（解析、stats.jsonl 可写性） |
| `Stats File` | stats.jsonl 的绝对路径 |

没有任何子命令：任何参数都会输出 `Usage: /dir-whip`。

`dir_whip_allow_path(path)` 是插件唯一的工具：当用户在对话中明确指定
目标路径时，写入前调用它以注册该路径。该记录仅对当前会话有效，并与
`exempt_paths` 合并。

## 高级用法

### 可选配置

可选、由用户维护，位于 `HERMES_HOME/dir-whip/dir-whip-config.yaml`
（Windows：`%LOCALAPPDATA%/hermes/dir-whip/dir-whip-config.yaml`；POSIX：
`~/.hermes/dir-whip/dir-whip-config.yaml`）。

| 键 | 含义 |
| --- | ---- |
| `exempt_paths` | 豁免于强制执行的路径（前缀匹配、绝对路径、正斜杠） |
| `allowed_root_files` | 允许位于工作目录根部的文件名；缺省时严格空列表 |
| `working_dir_root` | 显式工作目录覆盖；缺省回退 = 当前档案 `terminal.cwd` |
| `terminal_guard` | 开关终端写入拦截（默认：enabled） |
| `write_audit` | 开关根写入事后审计（默认：enabled） |
| `write_audit_entry_cap` | 根目录条目数超过此值时跳过审计轮（默认：2000） |

```yaml
exempt_paths: []
allowed_root_files: ["AGENTS.md"]
# working_dir_root: E:/HermesWorkspace/default   # 可选覆盖
# terminal_guard: enabled                        # 缺省即此值
# write_audit: enabled                           # 缺省即此值
# write_audit_entry_cap: 2000                    # 缺省即此值
```

> 配置仅支持手改 `dir-whip-config.yaml`，暂不支持快捷命令修改——`/dir-whip`
> 是只读报告。

**工作目录解析。** 解析分三步：dir-whip-config.yaml 中显式 `working_dir_root`
优先；否则取当前档案的 `terminal.cwd`；两者皆不可用时回退到当前工作目录并
发出 WARNING。`/dir-whip` 会报告取值及其来源。

### 定时治理模式

`audit_workspace.py --gate` 是 Hermes cron 任务的零 token 预检门：

```mermaid
flowchart TD
    A[cron tick] --> B[audit_workspace.py --gate]
    B -->|OK| C[wakeAgent: false - 静默跳过一次]
    B -->|列出违规| D[wakeAgent: true - agent 唤醒]
    D --> E[分类并归档错位文件]
    E --> F[报告摘要]
    B -->|--workspace 不匹配| G[退出码 2 - 不唤醒]
```

```bash
# Cron 任务：script= scripts/audit_workspace.py --gate
#            skill= dir-whip:workspace-organization
#            prompt: "If audit found violations, classify and archive misplaced
#                     files. If no violations, respond with [SILENT]."
```

- stdout 为 "OK" → `{"wakeAgent": false}` → 静默跳过一次，不投递
- stdout 列出违规 → `{"wakeAgent": true}` → agent 被唤醒、分类并把文件移入
  会话目录
- `--workspace` 不匹配 → 退出码 2、不发出 wakeAgent（边界配置错误属于系统
  问题，而非治理场景）

### 子代理模式

父代理把任务委托给子代理时，遵循以下文件协议：

```mermaid
flowchart TD
    A[父代委托任务] --> B[父代确保目标目录存在]
    B --> C[子代理写入父代传递的目录]
    C -->|缺省| D[父会话 .tmp/]
    C -->|显式传递| E[Outputs/ 或独立子目录]
    C -->|写入被拦截| F[向父代上报]
    C -->|完成| G[父代审阅并晋升 .tmp/ → Outputs/]
```

- 父代在委托前确保目标目录存在（必要时先创建会话目录）。
- 子代理写入父代传递的目标目录：缺省为父会话 `.tmp/`；父代可显式传递
  `Outputs/` 路径（正式交付物），或为每个子代理指定独立子目录
  （如 `.tmp/<task>/`）。
- 子代理不自建会话目录、不自晋升产物（`.tmp/` → `Outputs/` 晋升归父代
  审阅）。
- 目标目录缺失或写入被拦截时，子代理向父代上报，而非自行创建会话目录。
- 插件对子代理写入的判定与父代一致；统计按子代理切分记录。

### 统计

每次判定以一行 JSON 追加写入 `HERMES_HOME/dir-whip/stats.jsonl`。每行含
会话字段（`profile` / `session_id` / `is_subagent` / `started_at`）与事件
字段（`ts` / `outcome` / `reason` / `tool` / `rule_key` / `target`）。记录
范围：拦截判定、运行时豁免、审批观察，以及写入审计的违规与闸门拦截
（`write-audit-violation` / `write-audit-gate-block`），按子代理切分。
`target` 一律相对 Working Directory 记录，外部路径哈希前缀或省略——不含
文件内容、绝对路径或提示词文本。超过 5 MB 滚动为 `stats.jsonl.1`；跨会话
汇总经 `/dir-whip` 报告的 Stats File 路径查看。

## 安全与风险

dir-whip 是纪律辅助工具，不是安全边界。

**可能出什么问题。** Agent 可能被提示词引导写入任意位置；提示注入可能把写入
推送到意料之外的位置。扩大 `exempt_paths` 或关闭插件都会让工作区失去管理。
配置错误并非总能一眼可见。

**内置防护。** 强制发生在 `pre_tool_call` 钩子中，先于写入落地。前置层漏过的
根级写入由审计层事后捕获（快照 diff），清偿前冻结后续写入。配置异常时
fail-open 并发出 WARNING，绝不静默。外部写入放行但记入日志。统计经过隐私
裁剪。`--workspace` 不匹配时审计门拒绝唤醒 agent，`/dir-whip` 的
Health 可核验配置与统计健康。

## 贡献

欢迎提交缺陷报告、功能请求与拉取请求：
[github.com/shawVV1992/dir-whip](https://github.com/shawVV1992/dir-whip)。
本项目按规格驱动开发，权威规格位于仓库内。

## License

[MIT](./LICENSE) —— 见 [LICENSE](./LICENSE) 文件。不捆绑任何第三方组件。
