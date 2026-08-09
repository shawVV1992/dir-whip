# workspace-guard 安装 / 更新 Prompt

将本文件的全部内容复制，粘贴到一个 Hermes 会话中，由智能体代为执行
workspace-guard 的安装、更新与配置迁移，并在关键节点征求用户确认。
本 Prompt 驱动的是仓库自带的 `tools/install/install.py`（仓库根目录下，
不在技能包内），它以 `--dry-run` / `--apply` 两种模式运行，全程无交互输入。

## 使用方式

1. 打开一个 Hermes 会话（建议使用 `default` 档案）。
2. 把本文件内容整体粘贴进会话。
3. 按提示回答确认问题（`--force` 覆盖、重启时机等）。
4. 安装完成后按文末「验证」一节检查结果。

---

## 一、前置检查（先做，再动手）

- 确认 `python` 为 3.11 或更高（`python --version`）。
- 确认 `hermes` CLI 可用（`hermes --version`），且仓库网络可达。
- 默认仓库地址：`https://github.com/shawVV1992/workspace-guard`。
  若需本地测试或内网镜像，可在命令后追加 `--url <仓库地址>`。
- 在仓库根目录下执行脚本（`tools/install/install.py` 的相对路径以仓库根为准）。

## 二、全新安装

1. 先以 dry-run 预览全部步骤，并向用户展示：

   ```bash
   python tools/install/install.py --dry-run
   ```

   预览内容应包含：配置写入目标路径、插件安装命令、技能安装命令、
   备忘录说明（install.py 不重建备忘录，重启后由插件 register() 同步）。
   向用户确认无误后再继续。

2. 执行安装：

   ```bash
   python tools/install/install.py --apply
   ```

   脚本按以下顺序执行（顺序不可颠倒）：

   - 第 1 步 配置：确保
     `HERMES_HOME/workspace-guard/guard-config.yaml` 存在。若新位置已有
     配置则原样保留；否则若插件目录内存在旧副本
     （`HERMES_HOME/plugins/workspace-guard/guard-config.yaml`），先复制
     到新位置再删除旧文件（仅单个文件，不递归删除）；两者都不存在则写入
     全新默认配置（含 `working_dir_root` 注释态兜底、`exempt_paths: []`、
     `terminal_guard: true`、`allowed_root_files: ["AGENTS.md"]`）。
   - 第 2 步 插件：`hermes plugins install <url>#src/workspace-guard --enable`。
   - 第 3 步 技能：`hermes skills install <url>/src/workspace-organization`
     （已安装则跳过）。
   - 第 4 步 收尾：打印总结与新配置位置，提示用户重启 Hermes。

3. 提示用户：**重启 Hermes** 后插件 register() 会自动同步备忘录；
   也可以随时在会话中运行 `/workspace-guard workspace_update` 手动同步。

## 三、更新（已安装过，版本不同）

- 脚本会自动读取已安装的
  `HERMES_HOME/plugins/workspace-guard/plugin.yaml` 的 version，与仓库
  `src/workspace-guard/plugin.yaml` 的 version 比较：
  - 同版本：提示「已是最新」，跳过插件安装（dry-run 同样会报告）。
  - 版本不同：插件安装命令追加 `--force` 覆盖更新。
- **需要用户确认的点**：执行 `--force` 覆盖前，必须向用户说明并征得
  同意。说明要点：配置已在插件目录之外（`HERMES_HOME/workspace-guard/`），
  且迁移步骤先于重装完成，因此覆盖不会丢失用户配置。
- 配置迁移同样先于插件重装执行（见下节），无论版本是否相同。

## 四、配置迁移（SCR-013）

- 旧版部署把配置放在插件目录内；迁移把它移到
  `HERMES_HOME/workspace-guard/guard-config.yaml`（与备忘录同目录）。
- 迁移优先级：
  1. 新位置已有配置 -> 不动（保留用户配置）；
  2. 否则插件目录内存在旧副本 -> 复制到新位置，再删除旧文件；
  3. 两者都不存在 -> 写入全新默认配置。
- 迁移只在 `--apply` 时真正执行；`--dry-run` 只打印源与目标路径。
- 向用户说明配置的新位置，以及配置键的含义
  （`exempt_paths` 豁免路径、`working_dir_root` 注释态兜底、
  `terminal_guard` 终端守卫开关、`allowed_root_files` 根部白名单）。

## 五、验证（安装 / 更新后）

1. 重启 Hermes，新建一个会话。
2. 尝试向工作目录根部写入文件 -> 应当被拦截，并收到一条说明如何先创建
   会话目录的提示。
3. 运行 `/workspace-guard workspace_status` -> 应显示档案备忘录
   （档案 + 工作区 + 状态 + 变更时间）。
4. 如需立即重建备忘录，运行 `/workspace-guard workspace_update`。

## 六、常见问题

- 备忘录缺失但插件已安装：脚本校验会失败关闭，运行
  `/workspace-guard workspace_update` 重建备忘录即可。
- `hermes` CLI 找不到：检查 PATH 是否包含 Hermes 可执行文件所在目录。
- 安装中途失败或中断：修复原因后重新运行
  `python tools/install/install.py --apply`（脚本幂等，重复执行无副作用）。
- 想跳过技能安装或只想预览：始终先 `--dry-run` 查看计划再 `--apply`。

---

## 给智能体的执行要求

- 严格按上文步骤执行，先 dry-run 展示计划并征求用户确认，再 apply。
- 涉及 `--force` 覆盖、重启 Hermes、修改用户配置的时机，必须先询问用户。
- 不要调用任何运行时工具（如 `workspace_guard_auto_update_workspace`）来
  重建备忘录：install.py 与智能体都不负责此事，重启后 register() 会自动
  同步；用户可自行运行 `workspace_update`。
- 不要修改 `src/` 下的任何文件；本 Prompt 只驱动安装脚本，不改代码。
