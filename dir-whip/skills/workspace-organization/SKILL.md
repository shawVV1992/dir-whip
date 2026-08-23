---
name: workspace-organization
description: "在 Hermes 工作区中创建、保存、写入、移动或删除文件，组织交付物，或审计工作区合规性时使用。"
author: dir-whip
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [files, workspace, session-directory, organization, audit, terminal]
    requires_toolsets: [terminal, file]
---

# 工作区组织

Hermes 代理工作区的文件放置纪律：会话目录结构、Outputs/.tmp 放置规则、根目录禁写规则与治理工作流。

## 何时使用

以下情况使用本技能：
- 在 Hermes 工作区中创建、保存、写入、移动或删除文件
- 组织交付物或设计工作区布局
- 审计工作区合规性（"整理工作区"、cron 治理）

以下情况不要使用：
- 项目模式激活（project_list 工具可用、active_id 非空、CWD 位于项目文件夹下）
- CWD 不在当前档案的 Working Directory 下

### 范围检查（Layer 0）

按顺序评估。首个匹配生效。不要评估后续条件。

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Stop. This skill does not apply.
IF CWD not under the current profile's Working Directory
  -> PROJECT MODE. Stop. This skill does not apply.
OTHERWISE
  -> DEFAULT MODE. Proceed to Layer 1.
```

## 快速参考

| 场景 | 动作 |
|------|------|
| 写入任何文件 | 先分类目标 → 会话目录 → `Outputs/` 或 `.tmp/` |
| 根目录写入被拦截 | 创建会话目录，重新定向 |
| 删除 / 覆盖 / 移动 | 确认协议（列出文件 → 等待明确确认） |
| 用户指定路径 | 写入前先调用 `dir_whip_allow_path(path)` |
| 子代理写入 | 写入父代理传递的目录，绝不自建会话目录 |

## 即时纪律（Layer 1）

触发条件：任何文件写入、创建、保存、删除或移动。

### 1. 分类目标（每次写入前）

| 目标 | 守卫行为 |
|------|----------|
| 会话目录内（`YYYYMMDD_HHMMSS_TaskName/...`） | 允许 |
| 根白名单文件（`allowed_root_files`） | 允许 |
| Working Directory 之外 | 允许 + 记录（fail-open） |
| Working Directory 根目录，非白名单 | 拦截 |

### 2. 会话目录纪律

- 会话目录在首次文件写入时懒创建，而非对话开始时
- 不在会话目录内？先创建：
  `python scripts/create_session_dir.py <task_name> --workspace <working_dir>`
- 每个会话目录包含 `Outputs/`（交付物）和 `.tmp/`（临时文件）
- 根目录只允许：白名单文件、会话格式目录、`.hermes/`
- `Outputs/` 黑名单：`__pycache__/`、`*.pyc`、`node_modules/`、`.DS_Store`、`Thumbs.db`

### 3. 文件放置决策（Outputs vs .tmp）

| 文件类型 | 目标位置 |
|----------|----------|
| 用户要求的交付物（报告、文档、分析结果） | `Outputs/` |
| 中间脚本、调试文件、探索性工作 | `.tmp/` |
| 不确定 | `.tmp/`（默认） |

### 4. 确认协议

适用于删除 / 覆盖 / 移动。**指令不等于确认。**

1. 代理列出确切文件并询问 "确认？(yes/no)"
2. 用户回复 "yes"/"confirm"/"go ahead" → 执行；其他任何回复 → 中止

### 5. 被拦截时

使用 [Reason]/[Next] 模板回复：

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will create a Session Directory and write there:
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/ or .tmp/ subdirectory.
```

子代理变体：将 "I will create..." 替换为 "I will write to the target directory passed by the parent agent."

### 6. 示例

- **错误：** 直接写入 `<working_dir>/report.md` → 被守卫拦截
- **正确：** `python scripts/create_session_dir.py report --workspace <working_dir>`，然后将交付物写入 `Outputs/report.md`（或临时文件写入 `.tmp/`）

## 子代理文件协议

- 父代理在委派前确保目标目录存在（懒创建是父代理的职责）
- 子代理写入父代理的 `.tmp/`（默认）或显式指定的 `Outputs/`/按任务划分的子目录
- 子代理绝不创建会话目录或晋升输出（`.tmp/` → `Outputs/` 晋升是父代理的审查步骤）；目标缺失或被拦截 → 回报父代理

## 终端写入纪律

Layer 1 同样适用于终端写入。守卫粗粒度拦截：重定向（`>` `>>`）、`touch`、`cp`/`mv` 目标；不确定意图允许 + 记录。

1. 所有写入优先使用会话目录
2. 用户指定路径 → 写入前先调用 `dir_whip_allow_path(path)`
3. 被拦截 → 创建会话目录并重新定向（绝不绕过守卫）

## 治理与定时任务

由"整理工作区"或 cron 任务触发：

1. 运行：`python scripts/audit_workspace.py --workspace <working_dir>`（加 `--json`）
2. 有违规？分类 → 提议 → 经确认后执行
3. 无违规？报告 "OK"（cron 模式为 `[SILENT]`）

Cron：`script: scripts/audit_workspace.py --gate` + skill `dir-whip:workspace-organization`。Gate 合规时发出 `{"wakeAgent": false}`，违规时 `{"wakeAgent": true}`；gate 失败以 exit 2 退出且无 wakeAgent；解析失败 fail-open 到 CWD。Cron 自动清理过期 `.tmp/` 内容（按年龄，默认 30 天）；交互运行只提议、绝不删除。完整检查清单见 `references/workspace-audit.md`。

## 脚本

所有脚本：Python 3.11、支持 `--help`、正斜杠输出路径。

| 脚本 | 用途 | 关键参数 |
|------|------|----------|
| create_session_dir.py | 创建含 Outputs/ + .tmp/ 的会话目录 | `--workspace` |
| audit_workspace.py | 合规审计 + gate + cron .tmp 清理 | `--workspace`、`--json`、`--gate`、`--days` |

边界：`--workspace` 必须匹配解析出的根目录（不匹配 exit 2）；解析失败 fail-open 到 CWD 并输出一条警告。

## 常见问题

| 问题 | 原因 | 修复 |
|------|------|------|
| 根目录写入被拦截 | 尚未创建会话目录 | 运行 create_session_dir.py，重新定向 |
| 交付物在 `.tmp/` | 未分类放置 | 用户要求的文件 → `Outputs/` |
| 对话开始就创建会话目录 | 误解懒创建 | 仅在首次文件写入时创建 |
| 未经确认就删除 | 把指令当作确认 | 列出文件，等待明确确认 |
| 工作区外的现有仓库 | 试图移动 | 通过 rules 文件指向，不要移动 |

## 验证

- 每次写入前是否已分类目标？
- 文件在会话目录内，且位于正确的 `Outputs/`/`.tmp/`？
- Working Directory 根目录无违规文件？
- 删除/覆盖/移动前是否已获得确认？

## 记住

写前分类 → 所有写入进会话目录 → 根目录禁写 → 被拦截时创建会话目录重试。