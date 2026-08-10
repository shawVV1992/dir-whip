# workspace-guard

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

workspace-guard 为 Hermes 智能体的工作目录强制执行文件纪律。Hermes 智能体并不总能可靠地遵守文件放置规则：交付物散落在工作目录根部，特定对话的输出难以查找，中间文件越积越多、无处安放。workspace-guard 通过两层互补机制解决这个问题。技能负责教导规则，插件负责在违规发生之前将其拦截。

## 工作原理

这两层机制在运行时完全解耦：

- **技能（教导）**：`workspace-organization` 技能指导智能体把每个文件放进名为 `YYYYMMDD_HHMMSS_TaskName/` 的会话目录，其中恰好包含两个子目录：`Outputs/` 存放正式交付物，`.tmp/` 存放中间文件。会话目录在首次写入文件时才惰性创建，因此不产生文件的对话不会留下任何目录。删除、覆盖、移动等破坏性操作遵循两步确认协议：指令本身不等于确认。
- **插件（强制）**：`workspace-guard` 插件注册了一个 `pre_tool_call` 钩子，拦截 `write_file`、`patch` 和 `terminal` 操作。默认工作目录内的写入只有在目标路径位于有效会话目录中、属于豁免路径、或在 `allowed_root_files` 白名单上（与审计读取同一键，守卫与审计对根部文件的判定永远一致）时才被放行；其余一律拦截，并附带修复指引。终端命令由一个轻量分词器分级判定：写入工作目录根部的命令被拦截，置信度较低的命令请求人工确认，其余放行。守卫采用失败放行（fail-open）策略：无法解析工作目录时自动禁用自身并给出一次性警告，而不是让智能体崩溃。

两层机制互不依赖。技能单独使用时是一层教导；插件单独使用时是一层强制。两者配合，确保智能体既知道规则，又无法绕过规则。

## 安装

### 技能

```bash
hermes skills install <repo-url>/workspace-organization
```

技能包内不含任何规则文件字面量，因此 skills_guard 扫描返回 `safe` 判定，无需 `--force`。技能的脚本以档案备忘录（`profile-workspaces.json`）校验工作区，而非规则文件存在性检查。

### 插件

```bash
hermes plugins install <repo-url>#workspace-guard --enable
```

重启 Hermes 后守卫即开始生效。

### 安装脚本

`install.sh` 将原生命令封装为单一按档案（per-profile）、幂等的流程（技能与插件始终一起安装）：

```bash
bash install.sh status          # 各档案已装版本
bash install.sh install --all-profiles          # 一次性安装/更新全部档案
bash install.sh install --profile default       # 单个档案
bash install.sh install --dry-run               # 只显示计划，不做任何更改
bash install.sh uninstall --all-profiles        # 卸载并删除配置
bash install.sh uninstall --profile learn --keep-config
```

**环境自动检测**：脚本自动检测 Hermes 所在环境并安装到对应位置：

- **Git Bash**（或任意 POSIX shell）：安装到 Windows 侧 Hermes（`%LOCALAPPDATA%\hermes`）。
- **PowerShell/CMD** 敲 `bash install.sh`：此处的 `bash` 是 WSL 启动器。若 WSL 内自有 Hermes（`~/.hermes` 且 PATH 中有 `hermes`），则安装到 WSL；否则脚本自动以 Git Bash 重新执行，安装到 Windows 侧 Hermes。
- **WSL**：若 WSL 内自有 Hermes，安装到 WSL 侧（不再回退检测 Windows）；否则自动以 Git Bash 重新执行。

不带参数运行即进入交互式菜单。更新 = 全量覆盖（`--force` 重装 + 配置模板 + 下次重启时重建备忘录）。所有参数见 `bash install.sh --help`。

终端仅显示简化状态行（`[1/2] 安装 skill 完成`）；完整命令输出（hermes 拉取/扫描/元数据）与配置细节写入 `<HERMES_HOME>/workspace-guard/install.log`（可用 `--log <path>` 覆盖）。

### 快捷命令与工具

插件安装后，会话内可用以下命令：

- `/workspace-guard workspace_status` — 查看档案备忘录（档案 + 工作区 + 状态 + 变更时间）
- `/workspace-guard workspace_update` — 手动重建备忘录

工具：

- `workspace_guard_auto_update_workspace` — 自动同步同一备忘录（备忘录过期时由智能体触发）
- `workspace_guard_register_workspace(profile, workspace)` — 为当前档案登记新工作区（两步 init 流程：先运行 `init_workspace.py` 创建目录，再在目标档案自己的会话内调用本工具）。它会先设置该档案的 `terminal.cwd`（config-first，持久化）再写备忘录条目；由于同步派生自 `terminal.cwd`，登记永不会被覆盖。

### 验证

新建一个 Hermes 会话，尝试向工作目录根部写入文件。写入应当被拦截，并收到一条说明如何先创建会话目录的提示。

## 配置

插件从 `HERMES_HOME/workspace-guard/guard-config.yaml` 读取配置（Windows：`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`；POSIX：`~/.hermes/workspace-guard/guard-config.yaml`）——与档案备忘录同目录、位于插件目录之外，强制重装不会丢失。插件目录内的副本只是随包模板，不是运行时配置源。该文件由用户维护、完全可选，插件默认即可使用。

```yaml
# workspace-guard configuration
# Runtime location: HERMES_HOME/workspace-guard/guard-config.yaml
# (Windows: %LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml;
#  POSIX: ~/.hermes/workspace-guard/guard-config.yaml)
# Paths listed here are exempt from session directory enforcement.
# Use absolute paths with forward slashes.

exempt_paths: []
  # - <WORKSPACE_PATH>/projects/my-project

# 可选兜底：working_dir_root（仅在 profile 配置或 TERMINAL_CWD 自动检测失败时使用；绝不覆盖自动检测）
# working_dir_root: <WORKSPACE_PATH>

# 允许位于默认工作目录根部的文件（可选的规则文件）。守卫与审计共用同一键。
# 键缺失 -> 严格兜底：空白名单（fail-closed）。
allowed_root_files: ["AGENTS.md"]
```

- `exempt_paths`（豁免路径）：默认工作目录内不受守卫约束的路径白名单，例如位于工作目录内的项目目录。匹配采用前缀匹配（路径先规范化为正斜杠再比较）。
- `working_dir_root`：手动指定的默认工作目录。仅在无法从 Hermes profile 配置或 `TERMINAL_CWD` 环境变量自动检测时使用，绝不覆盖可解析的 profile 工作区。
- `allowed_root_files`（根部白名单）：允许位于默认工作目录根部的文件名白名单（即可选的规则文件）。插件守卫的根部豁免与审计读取同一键，两者对哪些根部文件可放行的判定永远一致。若该键（或整个配置）缺失，严格兜底为空白名单：所有根部文件都被标记（fail-closed）。

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

随插件捆绑的四个脚本是各自独立的命令行工具。除 `init_workspace.py`（它只创建新工作区；登记是独立的插件工具步骤）外，每个脚本在操作前都会校验目标是否精确匹配档案备忘录中登记的档案工作区（路径先做分隔符与大小写归一化），不匹配则退出码 2 并附登记提示。备忘录缺失/损坏且未安装插件时，脚本降级为 standalone 模式（信任传入的 `--workspace`，并输出一条简短 stderr 警告）；插件已安装但备忘录损坏时，脚本 fail-closed（退出码 2）并提示运行 `/workspace-guard workspace_update`。

| 脚本 | 用途 | 关键参数 |
|------|------|----------|
| `create_session_dir.py` | 创建会话目录 `YYYYMMDD_HHMMSS[_TaskName]/`（含 `Outputs/` 和 `.tmp/`），并输出其绝对路径 | `--workspace <path>`, `--profile` |
| `audit_workspace.py` | 只读的结构合规审计；发现违规时退出码为 1 | `--workspace`, `--profile`, `--json`, `--gate` |
| `clean_tmp.py` | 清理各会话 `.tmp/` 目录中过期的文件（默认 dry-run） | `--days N`, `--workspace`, `--profile`, `--confirm` |
| `init_workspace.py` | 创建新的工作目录（只 mkdir + sanitize；不写备忘录、不写模板文件）；输出含登记下一步指引 | `--workspace` |

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
├── <rules file>          <- 可选的工作区规则文件（名称由用户自行决定）
├── 20260802_143000_my-task/
│   ├── Outputs/    <- 正式交付物
│   └── .tmp/       <- 中间文件，可安全清理
├── 20260802_153012_another-task/
│   ├── Outputs/
│   └── .tmp/
└── .hermes/        <- Hermes 内部使用，已加入白名单
```

工作目录根部只允许存在会话目录和 `.hermes/`；可存在一个可选的规则文件（列于 `allowed_root_files`），但并非必需、不由工具写入、也不影响工作区校验（SCR-011：校验使用档案备忘录）。

## License

MIT
