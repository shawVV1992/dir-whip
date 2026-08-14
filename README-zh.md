# dir-whip

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version: 0.2.0](https://img.shields.io/badge/version-0.2.0-blue.svg)](https://github.com/shawVV1992/dir-whip)

[English](./README.md) | [中文版](./README-zh.md)

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

dir-whip 为 Hermes 智能体的工作目录（Working Directory）强制执行文件
纪律：内置的技能负责教导规则，同时插件负责在违规落地之前将其拦截。支持 Windows、Linux、WSL 与 macOS。

[安装与快速上手](#安装与快速上手) · [守护范围](#守护范围) · [命令](#命令) ·
[配置](#配置) ·
[高级用法](#高级用法) · [安全与风险](#安全与风险) ·
[贡献](#贡献) · [License](#license)

## 为什么选择它

- **双层一体** —— 技能负责教导、插件负责强制；1 条原生命令同时安装两者。
- **违规前拦截** —— 7 个钩子截获文件写入；指向根目录的违规写操作被拦截，
  并附修正指引。
- **零配置纪律** —— 默认注入常驻提示（≤200 字）；配置异常时 fail-open 并
  发出 WARNING。
- **支持定时治理** —— `audit_workspace.py --gate` 支撑零 token 的 cron 预检，
  采用 wakeAgent / [SILENT] 模式。
- **天生可观测** —— 3 个斜杠命令、5 个 `dir-whip:*` 事件，以及隐私
  裁剪的 stats.jsonl（5 MB 滚动）。
- **跨平台** —— Windows 10+、Linux、WSL、macOS。

## 功能特性

| 领域 | 能力 |
| ---- | ---- |
| **插件守卫** | 7 个钩子拦截指向工作目录根部（合法会话目录之外）的写入；外部路径放行并记入日志 |
| **捆绑技能** | `workspace-organization` 随插件分发：完整纪律参考 + 审计工作流 |
| **会话目录** | `YYYYMMDD_HHMMSS_TaskName/`，含 `Outputs/` 与 `.tmp/`，由捆绑脚本创建 |
| **命令** | `/dir-whip status`、`stats [--all] [--subagent]`、`doctor` |
| **工具** | `dir_whip_allow_path` —— 会话级路径豁免，插件唯一工具 |
| **配置** | `dir-whip-config.yaml` 共 4 个键，位于插件目录之外，重装不受影响 |
| **治理** | `audit_workspace.py --gate` 配合 cron 的 wakeAgent / [SILENT] 模式 |
| **可观测性** | stats.jsonl（5 MB 滚动、隐私裁剪）；5 个 `dir-whip:*` 事件 |

## 安装与快速上手

### 前置要求

- 支持插件（manifest v2）的 Hermes CLI 或桌面端。
- 安装命令需要可访问 GitHub。

### 快速上手（人类用户）

```bash
# 1. 安装插件及其捆绑的技能、脚本与配置模板
hermes plugins install shawVV1992/dir-whip/dir-whip --enable

# 2. 重启 Hermes —— 守卫在下一次会话生效

# 3. 验证生效配置及其来源
/dir-whip status
```

重启 Hermes 后守卫即开始生效。无需安装脚本，也无需单独安装技能。

### 快速上手（AI Agent）

> **面向 AI 助手的提示：** 如果你是受用户委托安装或验证 dir-whip 的
> AI Agent，请执行以下命令。守卫仅在 Hermes 重启后生效 —— 不要在当前会话
> 中测试文件写入。

```bash
# 1. 安装（可能需要用户批准）
hermes plugins install shawVV1992/dir-whip/dir-whip --enable
# 2. 告知用户需要重启，并在新会话中验证
/dir-whip status
```

如需完整的纪律参考，用 `dir-whip:workspace-organization` 显式加载
捆绑技能。

### 更新

```bash
hermes plugins install shawVV1992/dir-whip/dir-whip --force
```

重装不会清除 `dir-whip-config.yaml`。

### 卸载

```bash
hermes plugins remove dir-whip
```

## 守护范围

每个产生文件的 Hermes 对话都会在工作目录根部得到一个会话目录，命名为
`YYYYMMDD_HHMMSS_TaskName/`，其中 `Outputs/` 存放正式交付物、`.tmp/` 存放
中间文件。

| 目标 | 判定 |
| ---- | ---- |
| 会话目录之内 | 放行 |
| 豁免路径或运行时豁免 | 放行 |
| 根目录白名单文件（`allowed_root_files`） | 放行 |
| 工作目录根部的其他写入 | 拦截 —— 请创建会话目录 |
| 工作目录之外 | 放行 + 记日志（外部路径） |
| 无法判定的终端写入意图 | 放行 + 记日志 |

终端写入在 shell 层拦截：重定向（`>` `>>` `1>` `2>`）、`touch`，以及
`cp`/`mv` 的目标路径。复杂管道只观察、不解析。

## 命令

| 命令 | 用途 |
| ---- | ---- |
| `/dir-whip status` | 生效配置 + 解析来源（working_dir_root、terminal_guard、exempt_paths、allowed_root_files） |
| `/dir-whip stats [--all] [--subagent]` | 本会话的拦截统计；`--all` 读取持久化汇总 |
| `/dir-whip doctor` | 配置自检：可解析性、各键、解析链、统计可写性 |

`dir_whip_allow_path(path)` 是插件唯一的工具：为当前会话注册用户指定
的路径（见高级用法）。

## 配置

可选、由用户维护，位于 `HERMES_HOME/dir-whip/dir-whip-config.yaml`
（Windows：`%LOCALAPPDATA%/hermes/dir-whip/dir-whip-config.yaml`；POSIX：
`~/.hermes/dir-whip/dir-whip-config.yaml`）。

| 键 | 含义 |
| --- | ---- |
| `exempt_paths` | 豁免于强制执行的路径（前缀匹配、绝对路径、正斜杠） |
| `allowed_root_files` | 允许位于工作目录根部的文件名；缺省时严格空列表 |
| `working_dir_root` | 显式工作目录覆盖；缺省回退 = 当前档案 `terminal.cwd` |
| `terminal_guard` | 开关终端写入拦截（默认：enabled） |

```yaml
exempt_paths: []
allowed_root_files: ["AGENTS.md"]
# working_dir_root: E:/HermesWorkspace/default   # 可选覆盖
# terminal_guard: enabled                        # 缺省即此值
```

## 高级用法

**工作目录解析。** 解析分三步：dir-whip-config.yaml 中显式 `working_dir_root`
优先；否则取当前档案的 `terminal.cwd`；两者皆不可用时回退到当前工作目录并
发出 WARNING。`/dir-whip status` 会报告取值及其来源。

**会话级路径豁免。** 当用户在对话中明确指定目标路径时，写入前先调用
`dir_whip_allow_path(path)`。该记录仅对当前会话有效，并与
`exempt_paths` 合并。

**统计。** 每次守卫判定以一行 JSON 追加写入
`HERMES_HOME/dir-whip/stats.jsonl` —— 不含文件内容、绝对路径或提示词
文本。超过 5 MB 时滚动为 `stats.jsonl.1`。跨会话汇总用
`/dir-whip stats --all`。

**定时治理。** `audit_workspace.py --gate` 是 Hermes cron 任务的零 token
预检门：

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

## 安全与风险

dir-whip 是纪律辅助工具，不是安全边界。

**可能出什么问题。** Agent 可能被提示词引导写入任意位置；提示注入可能把写入
推送到意料之外的位置。扩大 `exempt_paths` 或关闭守卫都会让工作区失去管理。
配置错误并非总能一眼可见。

**内置防护。** 强制发生在 `pre_tool_call` 钩子中，先于写入落地。配置异常时
fail-open 并发出 WARNING，绝不静默。外部写入放行但记入日志。统计经过隐私
裁剪。`--workspace` 不匹配时审计门拒绝唤醒 agent，`/dir-whip doctor`
可核验配置与统计健康。

**如果关闭它。** 关闭插件即停止全部强制；已发生的事不会被回滚，错放的文件
也不会被自动清理。关闭 `terminal_guard` 后 shell 写入不再被监视。

**开启 / 关闭 / 恢复默认。**

```bash
# 开 —— 安装时启用插件
hermes plugins install shawVV1992/dir-whip/dir-whip --enable

# 关
hermes plugins disable dir-whip

# 恢复默认 —— 重新启用
hermes plugins enable dir-whip
```

```yaml
# terminal_guard 三态（配置级）
terminal_guard: enabled    # 开（默认）
terminal_guard: disabled   # 关
                           # 恢复默认：删除该行
```

**推荐用法。** 保持默认值。只豁免确属有意的项目目录。写入被拦截时创建会话
目录并重新定向 —— 绝不绕过守卫。定期查看 `/dir-whip stats`。

**责任归属。** 配置由用户维护。守卫辅助审查，但不取代审查。

## 贡献

欢迎提交缺陷报告、功能请求与拉取请求：
[github.com/shawVV1992/dir-whip](https://github.com/shawVV1992/dir-whip)。
本项目按规格驱动开发，权威规格位于仓库内。

## License

[MIT](./LICENSE) —— 见 [LICENSE](./LICENSE) 文件。不捆绑任何第三方组件。
