# workspace-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](./README.md) | [中文版](./README-zh.md)

本文档为英文 README 的中文翻译版本，如有歧义以英文版为准。

workspace-guard 为 Hermes 智能体的工作目录强制执行文件纪律，采用两层互补
机制：技能负责教导规则，插件负责在违规发生之前将其拦截，另附 4 个独立
脚本与一个一键安装器。

Hermes 智能体并不总能可靠地遵守文件放置规则：交付物散落在工作目录根部，
特定对话的输出难以查找，中间文件越积越多、无处安放。

[为什么选择](#为什么选择-workspace-guard) · [功能](#功能) · [会话目录结构](#会话目录结构) · [安装与快速上手](#安装与快速上手) · [配置](#配置) · [强制细节](#强制细节) · [License](#license)

## 为什么选择 workspace-guard

- **教导 + 强制**：`workspace-organization` 技能教导规则；`workspace-guard`
  插件在写入时拦截违规
- **失败放行安全**：工作目录无法解析时，守卫禁用自身并给出可见警告，
  而不是让智能体崩溃
- **一键安装器**：`workspace-guard_install.sh` 按档案同时安装技能与插件，幂等、支持
  dry-run 预览
- **开源**：MIT 许可，无账号、无遥测

## 功能

| 层 | 能力 |
|------|------|
| 技能（`workspace-organization`） | 会话目录约定 `YYYYMMDD_HHMMSS_TaskName/`（含 `Outputs/` 与 `.tmp/`）；破坏性操作两步确认协议 |
| 插件（`workspace-guard`） | `pre_tool_call` 钩子拦截 `write_file`、`patch`、`terminal`；根部白名单与审计共用；终端写入分为拦截 / 人工审批 / 放行三档；fail-open 设计 |

## 会话目录结构

每个产生文件的 Hermes 对话都会得到一个会话目录。目录名编码了开始时间与
可选的 task 名称；创建是惰性的（首次写入文件时创建，而非对话开始时）。

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

工作目录根部只允许存在会话目录和 `.hermes/`；可存在一个可选的规则文件
（列于 `allowed_root_files`），但并非必需、不由工具写入、也不影响工作区
校验（校验使用档案备忘录）。

## 安装与快速上手

### 前置要求

- Hermes，含 `hermes skills` 与 `hermes plugins` 命令
- 运行 `workspace-guard_install.sh` 需要 `bash`（Windows 用 Git Bash，或 WSL）
- 只有直接运行捆绑脚本时才需要 Python 3

### 快速上手（人类用户）

**Linux、macOS、WSL2**

下载安装器并运行（不带参数即进入交互式菜单）：

```bash
curl -fSLo workspace-guard_install.sh \
  https://raw.githubusercontent.com/shawVV1992/workspace-guard/main/workspace-guard_install.sh
bash workspace-guard_install.sh
```

**Windows（原生，PowerShell）**

提醒：PowerShell 中的 `bash` 是 WSL 启动器——安装器会自动检测并静默改以
Git Bash 重新运行，无需手动干预。若使用 WSL2，上面的 Linux 命令同样适用。

在 PowerShell 中运行：

```powershell
irm https://raw.githubusercontent.com/shawVV1992/workspace-guard/main/workspace-guard_install.sh -o workspace-guard_install.sh
bash workspace-guard_install.sh
```

重启 Hermes 后守卫即开始生效。

### 快速上手（AI 智能体）

> 如果你是为用户安装 workspace-guard 的 AI 智能体，请使用下面安装器的
> 非交互子命令，而不是交互菜单：先运行 `status`，用 `--dry-run` 预览，
> 再执行。

```bash
bash workspace-guard_install.sh status                 # 先检查各档案已装版本
bash workspace-guard_install.sh install --dry-run      # 预览计划，不做任何更改
bash workspace-guard_install.sh install --all-profiles # 执行
```

### 安装脚本

```bash
bash workspace-guard_install.sh status                     # 各档案已装版本
bash workspace-guard_install.sh install --all-profiles     # 一次性安装/更新全部档案
bash workspace-guard_install.sh install --profile default  # 单个档案
bash workspace-guard_install.sh install --dry-run          # 只显示计划，不做任何更改
bash workspace-guard_install.sh uninstall --all-profiles   # 卸载并删除配置
bash workspace-guard_install.sh uninstall --profile default --keep-config
```

**环境检测**：脚本按「运行入口」分流，只配置对应环境的 Hermes：

- **Windows 终端**（PowerShell/CMD/Windows Terminal 敲 `bash workspace-guard_install.sh`——
  此处的 `bash` 即 WSL 启动器）：自动识别，随后静默自动以 Git Bash 重新
  执行，配置 Windows 侧 Hermes（`%LOCALAPPDATA%\hermes`）。
- **WSL / Linux / macOS**（用户主动进入的 WSL 会话，或原生 Linux/macOS
  shell）：仅安装到本环境的 Hermes（`~/.hermes`）。在 WSL 中若该侧无
  Hermes，脚本报错并给出指引，绝不自动回退到 Windows 侧。

注意：
（1）WSL 侧的 Hermes 与 Windows 侧的 Hermes 是两个独立的安装，互不干扰。可用 `WG_TARGET=wsl`（强制 WSL 侧）或 `WG_TARGET=windows`（强制 Windows 侧）覆盖分流。不带参数运行即进入交互式菜单。
（2）更新 = 全量覆盖（`--force` 重装 + 配置模板 + 下次重启时重建备忘录）。所有参数见 `bash workspace-guard_install.sh --help`。

### 快捷命令与工具

插件安装后，会话内可用以下命令：

- `/workspace-guard workspace_status` — 查看档案备忘录（档案 + 工作区 +
  状态 + 变更时间）
- `/workspace-guard workspace_update` — 手动重建备忘录

工具：

- `workspace_guard_auto_update_workspace` — 自动同步同一备忘录（备忘录
  过期时由智能体触发）
- `workspace_guard_register_workspace(profile, workspace)` — 为当前档案登记新工作区
它会先设置该档案的  `terminal.cwd`（config-first，持久化）再写备忘录条目；由于同步派生自 `terminal.cwd`，登记永不会被覆盖。

### 脚本

随插件捆绑的四个脚本是各自独立的命令行工具。除 `init_workspace.py`
（它只创建新工作区；登记是独立的插件工具步骤）外，每个脚本在操作前都会
校验目标是否精确匹配档案备忘录中登记的档案工作区（路径先做分隔符与大小写
归一化），不匹配则退出码 2 并附登记提示。备忘录缺失/损坏且未安装插件时，
脚本降级为 standalone 模式（信任传入的 `--workspace`，并输出一条简短
stderr 警告）；插件已安装但备忘录损坏时，脚本 fail-closed（退出码 2）并
提示运行 `/workspace-guard workspace_update`。

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

### 验证

新建一个 Hermes 会话，尝试向工作目录根部写入文件。写入应当被拦截，并
收到一条说明如何先创建会话目录的提示。

## 配置

插件从 `HERMES_HOME/workspace-guard/guard-config.yaml` 读取配置（Windows：
`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`；POSIX：
`~/.hermes/workspace-guard/guard-config.yaml`）——与档案备忘录同目录、
位于插件目录之外，强制重装不会丢失。插件目录内的副本只是随包模板，不是
运行时配置源。该文件由用户维护、完全可选，插件默认即可使用。

```yaml
# workspace-guard configuration
# Runtime location: HERMES_HOME/workspace-guard/guard-config.yaml
# (Windows: %LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml;
#  POSIX: ~/.hermes/workspace-guard/guard-config.yaml)
# Paths listed here are exempt from session directory enforcement.
# Use absolute paths with forward slashes.

exempt_paths: []
  # - <WORKSPACE_PATH>/projects/my-project

# 可选兜底：working_dir_root（仅在 profile 配置或 TERMINAL_CWD 自动检测
# 失败时使用；绝不覆盖自动检测）
# working_dir_root: <WORKSPACE_PATH>

# 允许位于默认工作目录根部的文件（可选的规则文件）。守卫与审计共用同一键。
# 键缺失 -> 严格兜底：空白名单（fail-closed）。
allowed_root_files: ["AGENTS.md"]
```

- `exempt_paths`（豁免路径）：默认工作目录内不受守卫约束的路径白名单，
  例如位于工作目录内的项目目录。匹配采用前缀匹配（路径先规范化为正斜杠
  再比较）。
- `working_dir_root`：手动指定的默认工作目录。仅在无法从 Hermes profile
  配置或 `TERMINAL_CWD` 环境变量自动检测时使用，绝不覆盖可解析的 profile
  工作区。
- `allowed_root_files`（根部白名单）：允许位于默认工作目录根部的文件名
  白名单（即可选的规则文件）。插件守卫的根部豁免与审计读取同一键，两者
  对哪些根部文件可放行的判定永远一致。若该键（或整个配置）缺失，严格
  兜底为空白名单：所有根部文件都被标记（fail-closed）。

默认情况下，插件会自动从当前 Hermes profile 的 `terminal.cwd` 解析
`working_dir_root`。如果完全无法解析工作目录，守卫会禁用自身（失败放行）
并给出一次性警告——配置损坏绝不会悄无声息地关闭保护。

## 强制细节

### 跨档案写入

当智能体写入另一个档案的工作目录时（例如会话属于 `default` 档案却写入
`job-hunt` 档案），插件会弹出一个人工审批门，而不是静默放行或直接拦截。
审批按目标档案细分（`cross-profile-write:<档案名>`），放行一个档案不会
预先放行另一个档案。选项包括：运行一次、本对话允许、始终允许（持久化到
Hermes 的 `command_allowlist`）和拒绝。

### `workspace_guard_allow_path` 工具

当用户在对话中明确指定一个路径时，智能体可以调用
`workspace_guard_allow_path` 工具，在当前会话内豁免该路径及其下所有内容。
豁免是会话级的：每次新会话开始时被清空，且从不写入磁盘。

### 失败放行

如果守卫无法解析工作目录，它会禁用自身而不是让智能体崩溃，并注入一次
可见警告。守卫保持禁用期间，每次新会话开始都会重新给出警告。

### 路径归一化

在 Windows 上，MSYS 风格路径（`/e/...`、`//e/...`、`/cygdrive/e/...`）
会在分类前被归一化为盘符形式，豁免/放行匹配不区分大小写（与大小写不敏感
的文件系统一致）。POSIX 系统保持精确匹配。

## License

MIT
