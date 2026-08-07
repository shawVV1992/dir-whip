# workspace-guard

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

workspace-guard 为 Hermes 智能体的工作目录强制执行文件纪律。Hermes 智能体并不总能可靠地遵守文件放置规则：交付物散落在工作目录根部，特定对话的输出难以查找，中间文件越积越多、无处安放。workspace-guard 通过两层互补机制解决这个问题。技能负责教导规则，插件负责在违规发生之前将其拦截。

## 工作原理

这两层机制在运行时完全解耦：

- **技能（教导）**：`workspace-organization` 技能指导智能体把每个文件放进名为 `YYYYMMDD_HHMMSS_TaskName/` 的会话目录，其中恰好包含两个子目录：`Outputs/` 存放正式交付物，`.tmp/` 存放中间文件。会话目录在首次写入文件时才惰性创建，因此不产生文件的对话不会留下任何目录。删除、覆盖、移动等破坏性操作遵循两步确认协议：指令本身不等于确认。
- **插件（强制）**：`workspace-guard` 插件注册了一个 `pre_tool_call` 钩子，拦截 `write_file`、`patch` 和 `terminal` 操作。默认工作目录内的写入只有在目标路径位于有效会话目录中、属于豁免路径、或就是 `AGENTS.md` 本身时才被放行；其余一律拦截，并附带修复指引。终端命令由一个轻量分词器分级判定：写入工作目录根部的命令被拦截，置信度较低的命令请求人工确认，其余放行。守卫采用失败放行（fail-open）策略：无法解析工作目录时自动禁用自身并给出一次性警告，而不是让智能体崩溃。

两层机制互不依赖。技能单独使用时是一层教导；插件单独使用时是一层强制。两者配合，确保智能体既知道规则，又无法绕过规则。

## 安装

### 插件

```bash
hermes plugins install <repo-url>#src/workspace-guard --enable
```

重启 Hermes 后守卫即开始生效。

### 技能（手动复制）

技能位于 `src/workspace-organization/`，需要复制到 Hermes 的 skills 目录。

Windows:

```powershell
Copy-Item -Recurse "src\workspace-organization" "$env:LOCALAPPDATA\hermes\skills\workspace-organization"
```

Linux/macOS:

```bash
cp -r src/workspace-organization ~/.hermes/skills/workspace-organization
```

### 验证

新建一个 Hermes 会话，尝试向工作目录根部写入文件。写入应当被拦截，并收到一条说明如何先创建会话目录的提示。

## 配置

插件从自身目录读取 `guard-config.yaml`。该文件由用户维护、完全可选，插件默认即可使用。

```yaml
# workspace-guard configuration
# Paths listed here are exempt from session directory enforcement.
# Use absolute paths with forward slashes.

exempt_paths: []
  # - <WORKSPACE_PATH>/projects/my-project

# 可选兜底：working_dir_root（仅在 profile 配置或 TERMINAL_CWD 自动检测失败时使用；绝不覆盖自动检测）
# working_dir_root: <WORKSPACE_PATH>
```

- `exempt_paths`（豁免路径）：默认工作目录内不受守卫约束的路径白名单，例如位于工作目录内的项目目录。匹配采用前缀匹配（路径先规范化为正斜杠再比较）。
- `working_dir_root`：手动指定的默认工作目录。仅在无法从 Hermes profile 配置或 `TERMINAL_CWD` 环境变量自动检测时使用，绝不覆盖可解析的 profile 工作区。

默认情况下，插件会自动从当前 Hermes profile 的 `terminal.cwd` 解析 `working_dir_root`。如果完全无法解析工作目录，守卫会禁用自身（失败放行）并给出一次性警告——配置损坏绝不会悄无声息地关闭保护。

## 强制细节

### 跨档案写入

当智能体写入另一个档案的工作目录时（例如会话属于 `default` 档案却写入 `job-hunt` 档案），插件会弹出一个人工审批门，而不是静默放行或直接拦截。审批按目标档案细分（`cross-profile-write:<档案名>`），放行一个档案不会预先放行另一个档案。选项包括：运行一次、本对话允许、始终允许（持久化到 Hermes 的 `command_allowlist`）和拒绝。

### `workspace_guard_allow_path` 工具

当用户在对话中明确指定一个路径时，智能体可以调用 `workspace_guard_allow_path` 工具，在当前会话内豁免该路径及其下所有内容。豁免是会话级的：每次新会话开始时被清空，且从不写入磁盘。

### 失败放行

如果守卫无法解析工作目录，它会禁用自身而不是让智能体崩溃，并注入一次可见警告。守卫保持禁用期间，每次新会话开始都会重新给出警告。

### 路径归一化

在 Windows 上，MSYS 风格路径（`/e/...`、`//e/...`、`/cygdrive/e/...`）会在分类前被归一化为盘符形式，豁免/放行匹配不区分大小写（与大小写不敏感的文件系统一致）。POSIX 系统保持精确匹配。

## 脚本

随插件捆绑的四个脚本是各自独立的命令行工具。除 `init_workspace.py`（它本身用于创建工作目录）外，每个脚本在操作前都会校验目标目录中包含 `AGENTS.md`。

| 脚本 | 用途 | 关键参数 |
|------|------|----------|
| `create_session_dir.py` | 创建会话目录 `YYYYMMDD_HHMMSS[_TaskName]/`（含 `Outputs/` 和 `.tmp/`），并输出其绝对路径 | `--workspace <path>` |
| `audit_workspace.py` | 只读的结构合规审计；发现违规时退出码为 1 | `--workspace`, `--json`, `--gate` |
| `clean_tmp.py` | 清理各会话 `.tmp/` 目录中过期的文件（默认 dry-run） | `--days N`, `--workspace`, `--confirm` |
| `init_workspace.py` | 初始化一个新的默认工作目录，写入 `AGENTS.md` 规则文件 | `--workspace`, `--template <file>` |

示例：

```bash
python create_session_dir.py my-task --workspace <WORKSPACE_PATH>
python audit_workspace.py --workspace <WORKSPACE_PATH> --json
python clean_tmp.py --workspace <WORKSPACE_PATH> --days 30 --confirm
python init_workspace.py learn --workspace <WORKSPACE_PATH>
```

## 会话目录结构

每个产生文件的 Hermes 对话都会得到一个会话目录。目录名编码了开始时间与可选的 task 名称；创建是惰性的（首次写入文件时创建，而非对话开始时）。

```
<WORKSPACE_PATH>/
├── AGENTS.md
├── 20260802_143000_my-task/
│   ├── Outputs/    <- 正式交付物
│   └── .tmp/       <- 中间文件，可安全清理
├── 20260802_153012_another-task/
│   ├── Outputs/
│   └── .tmp/
└── .hermes/        <- Hermes 内部使用，已加入白名单
```

工作目录根部只允许存在 `AGENTS.md` 和 `.hermes/`；其余任何根级条目都必须是会话目录。

## 开发

- Python 3.11（命令：`python`）
- 测试框架：pytest，在项目根目录、激活虚拟环境后运行
- 测试结构：插件测试 `tests/test_config.py`、`tests/test_guard.py`；脚本测试 `tests/test_create_session_dir.py`、`tests/test_audit_workspace.py`、`tests/test_clean_tmp.py`、`tests/test_init_workspace.py`

```bash
python -m pytest
```

脚本位于 `src/workspace-organization/scripts/`。所有技能内容的修改都在 `src/workspace-organization/` 内进行；插件代码在 `src/workspace-guard/`。

## License

MIT
