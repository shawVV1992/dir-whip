# workspace-guard

workspace-guard enforces file discipline for Hermes agent workspaces. Hermes agents do not reliably follow file placement rules: deliverables scatter across the workspace root, outputs from specific conversations become unfindable, and intermediate files accumulate without a home. workspace-guard fixes this with two complementary layers - a skill that teaches agents the rules, and a plugin that blocks violations before they happen.

## How It Works

The two layers have zero runtime coupling:

- **Skill (teach)**: The `workspace-organization` skill instructs the agent to place every file inside a Session Directory named `YYYYMMDD_HHMMSS_TaskName/`, which contains exactly two subdirectories: `Outputs/` for formal deliverables and `.tmp/` for intermediate files. Session Directories are created lazily at the first file write, so conversations that produce no files create no directories. Destructive operations (delete, overwrite, move) follow a two-step confirmation protocol: instruction is not confirmation.
- **Plugin (enforce)**: The `workspace-guard` plugin registers a `pre_tool_call` hook that intercepts `write_file`, `patch`, and `terminal` operations. A write inside the Default Working Directory is allowed only if the target path is inside a valid Session Directory, is exempted, or is a root file on the `allowed_root_files` whitelist (the same key the audit reads, so guard and audit always agree on which root files are permitted). Anything else is blocked with instructions on how to fix it. Terminal commands are classified by a lightweight tokenizer into three tiers: writes to the workspace root are blocked, lower-confidence writes request human approval, and everything else passes. The guard is fail-open: if the working directory cannot be resolved, it disables itself and warns the user once instead of breaking the agent.

Neither layer depends on the other at runtime. The skill works alone as a teaching layer; the plugin works alone as an enforcement layer. Together they ensure the agent both knows the rules and cannot bypass them.

## Installation

### Skill

```bash
hermes skills install <repo-url>/workspace-organization
```

The skill package contains no rules-file literal, so the skills_guard scan
returns a `safe` verdict and `--force` is not needed. The skill's scripts
validate workspaces against the profile workspace memo
(`profile-workspaces.json`), not a rules-file presence check.

### Plugin

```bash
hermes plugins install <repo-url>#workspace-guard --enable
```

The guard becomes active after the next Hermes restart.

### Install script

`install.sh` wraps the native commands in a single per-profile,
idempotent flow (always installs skill + plugin together):

```bash
bash install.sh status          # per-profile installed versions
bash install.sh install --all-profiles          # one-shot install/update
bash install.sh install --profile default       # one profile
bash install.sh install --dry-run               # show the plan, change nothing
bash install.sh uninstall --all-profiles        # uninstall + delete config
bash install.sh uninstall --profile learn --keep-config
```

**Environment detection**: the script configures only the Hermes of the
environment it runs in — it never crosses the Windows/WSL boundary:

- **Windows, not WSL** (Git Bash): installs into the Windows Hermes
  (`%LOCALAPPDATA%\hermes`); the WSL Hermes is never searched.
- **WSL** (`bash install.sh` from PowerShell/CMD resolves to the WSL
  launcher; a plain WSL shell works the same): installs into the WSL Hermes
  (`~/.hermes`) only. If the WSL side has no Hermes, the script fails with
  guidance — it never falls back to the Windows Hermes automatically (run
  `install.sh` from Git Bash instead to configure the Windows side).
- **Linux/macOS**: installs into that environment's Hermes (`~/.hermes`).

Run with no arguments for the interactive menu. Update = full overwrite
(`--force` reinstall + config template + memo rebuild on next restart).
Run `bash install.sh --help` for all flags.

**Network**: the repo skill/plugin versions shown by `status` are fetched from
the GitHub remote (`raw.githubusercontent.com`) — the script works standalone,
no repo checkout needed next to it. `install` also needs the remote (version
comparison + config template download): offline it fails with a
"connect to the network and retry" hint instead of silently skipping updates;
`status` then shows `-` for the repo versions (installed versions still shown);
`uninstall` never needs the network.

The terminal shows only simplified status lines (`[1/2] 安装 skill 完成`);
full command output (hermes fetch/scan/metadata) and configuration details are
written to `<HERMES_HOME>/workspace-guard/install.log` (override with
`--log <path>`).

### Quick commands and tools

Once the plugin is installed, these in-session commands are available:

- `/workspace-guard workspace_status` — show the profile workspace memo
  (profiles + workspaces + status + changed_at)
- `/workspace-guard workspace_update` — rebuild the memo manually

Tools:

- `workspace_guard_auto_update_workspace` — auto-syncs the same memo
  (agent-triggered when the memo is stale)
- `workspace_guard_register_workspace(profile, workspace)` — registers a new
  workspace for the current profile (two-step init flow: run
  `init_workspace.py` to create the directory, then call this tool in the
  target profile's own session). It sets the profile's `terminal.cwd`
  (durable, config-first) and writes the memo entry; because sync derives
  `terminal.cwd`, a registration is never clobbered.

### Verify

Start a new Hermes session and try writing a file to the workspace root. It should be blocked with a helpful message explaining how to create a Session Directory first.

## Configuration

The plugin reads `guard-config.yaml` from
`HERMES_HOME/workspace-guard/guard-config.yaml` (Windows:
`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`; POSIX:
`~/.hermes/workspace-guard/guard-config.yaml`) — the same directory as the
profile workspace memo, outside the plugin directory so forced reinstalls
never wipe it. The copy inside the plugin directory is a shipped template
only, not the runtime config source. The file is user-managed and optional;
the plugin works out of the box.

```yaml
# workspace-guard configuration
# Runtime location: HERMES_HOME/workspace-guard/guard-config.yaml
# (Windows: %LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml;
#  POSIX: ~/.hermes/workspace-guard/guard-config.yaml)
# Paths listed here are exempt from session directory enforcement.
# Use absolute paths with forward slashes.

exempt_paths: []
  # - <WORKSPACE_PATH>/projects/my-project

# Optional fallback: working_dir_root (used ONLY if auto-detection from the
# profile config or TERMINAL_CWD fails; never overrides auto-detection)
# working_dir_root: <WORKSPACE_PATH>

# Root files allowed at the Default Working Directory root (the optional
# workspace rules file). Shared by the plugin guard and the audit.
# Missing key -> strict fallback: empty whitelist (fail-closed).
allowed_root_files: ["AGENTS.md"]
```

- `exempt_paths`: A whitelist of paths inside the Default Working Directory that bypass guard enforcement, e.g. project directories that live inside the workspace. Matching is a prefix match after normalizing to forward slashes.
- `working_dir_root`: A manual fallback for the Default Working Directory. Only used when auto-detection from the Hermes profile configuration or the `TERMINAL_CWD` environment variable fails. It never overrides a resolvable profile workspace.
- `allowed_root_files`: A whitelist of file names permitted at the Default Working Directory root (the optional workspace rules file). The plugin guard's root exemption and the audit read the SAME key, so they never disagree about which root files are allowed. If the key (or the whole config) is missing, the strict fallback is an empty whitelist: every root file is flagged (fail-closed).

By default the plugin resolves `working_dir_root` automatically from the active Hermes profile's `terminal.cwd`. If it cannot resolve a working directory at all, the guard disables itself (fail-open) and shows a one-time warning, so a broken configuration never silently turns off the protection.

## Enforcement Details

### Cross-profile writes

When the agent writes into another profile's working directory (for example a `job-hunt` profile while the session belongs to `default`), the plugin shows a human-approval gate instead of silently allowing or blocking it. Approvals are keyed per target profile (`cross-profile-write:<profile>`), so allowing one profile does not pre-approve another. Choices include run-once, allow-this-conversation, always (persisted to the Hermes `command_allowlist`), and deny.

### The `workspace_guard_allow_path` tool

When the user explicitly names a path in conversation, the agent can call the `workspace_guard_allow_path` tool to exempt that path (and everything under it) for the current session. The exemption is session-scoped: it is cleared at the start of each new session, and it never persists to disk.

### Fail-open

If the guard cannot resolve the working directory, it disables itself rather than breaking the agent, and injects a one-time visible warning. The warning re-appears at the start of a new session while the guard remains disabled.

### Path normalization

On Windows, MSYS-style paths (`/e/...`, `//e/...`, `/cygdrive/e/...`) are normalized to their drive-letter form before classification, and exempt/allowlist matching is case-insensitive (matching the case-insensitive filesystem). POSIX systems keep exact matching.

## Scripts

The bundled scripts are self-contained CLI tools. Each one validates its
target against the profile workspace memo (`profile-workspaces.json`): the
target must exactly match a profile's recorded workspace (separator- and
case-normalized) or the script exits 2 with a registration prompt. When the
memo is missing/corrupt and no plugin is installed, the scripts fall back to a
standalone mode (the provided `--workspace` is trusted, with one concise
stderr warning); when the plugin is installed but the memo is broken, they
fail closed with a prompt to run `/workspace-guard workspace_update`.
`init_workspace.py` is exempt from memo validation (it creates new workspaces;
registration is a separate plugin-tool step).

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `create_session_dir.py` | Create a Session Directory `YYYYMMDD_HHMMSS[_TaskName]/` with `Outputs/` and `.tmp/`, print its absolute path | `--workspace <path>`, `--profile` |
| `audit_workspace.py` | Read-only compliance audit against the workspace rules; exit code 1 when violations are found | `--workspace`, `--profile`, `--json`, `--gate` |
| `clean_tmp.py` | Delete expired files inside session `.tmp/` directories (dry-run by default) | `--days N`, `--workspace`, `--profile`, `--confirm` |
| `init_workspace.py` | Create a new workspace directory (mkdir + sanitize only; no memo write, no template file); output includes the registration next-step | `--workspace` |

Example:

```bash
python create_session_dir.py my-task --workspace <WORKSPACE_PATH>
python audit_workspace.py --workspace <WORKSPACE_PATH> --json
python clean_tmp.py --workspace <WORKSPACE_PATH> --days 30 --confirm
python init_workspace.py learn --workspace <WORKSPACE_PATH>
```

## Session Directory Structure

Every Hermes conversation that produces files gets one Session Directory. The name encodes the start time and an optional task name; creation is lazy (first file write, not conversation start).

```
<WORKSPACE_PATH>/
├── <rules file>          <- optional workspace rules (name is user-chosen)
├── 20260802_143000_my-task/
│   ├── Outputs/    <- formal deliverables
│   └── .tmp/       <- intermediate files, safe to clean
├── 20260802_153012_another-task/
│   ├── Outputs/
│   └── .tmp/
└── .hermes/        <- Hermes-internal, whitelisted
```

Only session directories and `.hermes/` are allowed at the workspace root; a
single optional rules file (listed in `allowed_root_files`) may exist but is
not required, is not written by the tool, and does not affect workspace
validation (SCR-011: validation uses the profile workspace memo).

## License

MIT
