# workspace-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[中文版](./README-zh.md) | [English](./README.md)

workspace-guard enforces file discipline for Hermes agent workspaces with two
complementary layers - a skill that teaches agents the rules and a plugin that
blocks violations before they happen, plus four standalone scripts and a
one-shot installer.

Hermes agents do not reliably follow file placement rules: deliverables
scatter across the workspace root, outputs from specific conversations become
unfindable, and intermediate files accumulate without a home.

[Why](#why-workspace-guard) · [Features](#features) · [Session Directory Structure](#session-directory-structure) · [Installation & Quick Start](#installation--quick-start) · [Configuration](#configuration) · [Enforcement Details](#enforcement-details) · [License](#license)

## Why workspace-guard?

- **Teach and enforce** - the `workspace-organization` skill teaches the
  rules; the `workspace-guard` plugin blocks violations at write time
- **Fail-open safety** - an unresolvable working directory disables the guard
  with a visible warning instead of breaking the agent
- **One-shot installer** - `install.sh` installs skill + plugin together, per
  profile, idempotently, with dry-run previews
- **Open source** - MIT licensed, no accounts, no telemetry

## Features

| Layer | Capabilities |
|-------|--------------|
| Skill (`workspace-organization`) | Session Directory convention `YYYYMMDD_HHMMSS_TaskName/` with `Outputs/` and `.tmp/`; two-step confirmation protocol for destructive operations |
| Plugin (`workspace-guard`) | `pre_tool_call` hook over `write_file`, `patch`, and `terminal`; root-file whitelist shared with the audit; terminal writes classified into block / human-approval / pass; fail-open by design |

## Session Directory Structure

Every Hermes conversation that produces files gets one Session Directory. The
name encodes the start time and an optional task name; creation is lazy (first
file write, not conversation start).

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
validation (validation uses the profile workspace memo).

## Installation & Quick Start

### Requirements

- Hermes with the `hermes skills` and `hermes plugins` commands
- `bash` for `install.sh` (Git Bash on Windows, or WSL)
- Python 3 only if you run the bundled scripts directly

### Quick Start (Human Users)

Install skill and plugin manually with the native Hermes commands:

```bash
hermes skills install https://github.com/shawVV1992/workspace-guard/workspace-organization
hermes plugins install https://github.com/shawVV1992/workspace-guard#workspace-guard --enable
```

The guard becomes active after the next Hermes restart.

### Quick Start (AI Agent)

> If you are an AI agent helping the user install workspace-guard, use the
> installer's non-interactive subcommands below instead of the interactive
> menu: run `status` first, preview with `--dry-run`, then apply.

```bash
bash install.sh status                 # check installed versions first
bash install.sh install --dry-run      # preview the plan, change nothing
bash install.sh install --all-profiles # apply
```

### Install script

```bash
bash install.sh status                     # per-profile installed versions
bash install.sh install --all-profiles     # one-shot install/update
bash install.sh install --profile default  # one profile
bash install.sh install --dry-run          # show the plan, change nothing
bash install.sh uninstall --all-profiles   # uninstall + delete config
bash install.sh uninstall --profile default --keep-config
```

**Environment detection**: the script routes by where you launch it from and
configures only that environment's Hermes:

- **Windows terminal** (PowerShell/CMD/Windows Terminal, `bash install.sh` -
  `bash` is the WSL launcher there): detected automatically, and the script
  silently re-runs itself under Git Bash to configure the Windows Hermes
  (`%LOCALAPPDATA%\hermes`).
- **WSL / Linux / macOS** (a real WSL session, or a native Linux/macOS
  shell): installs into that environment's Hermes (`~/.hermes`) only. In WSL,
  if that side has no Hermes, the script fails with guidance - it never falls
  back to the Windows Hermes automatically.
- **Windows, not WSL** (Git Bash): installs into the Windows Hermes; the WSL
  Hermes is never searched.

Note:
(1) The WSL-side Hermes and the Windows-side Hermes are two independent
    installations that do not interfere. Use `WG_TARGET=wsl` (force the WSL
    side) or `WG_TARGET=windows` (force the Windows side) to override the
    routing. Run with no arguments for the interactive menu.
(2) Update = full overwrite (`--force` reinstall + config template + memo
    rebuild on next restart). See `bash install.sh --help` for all flags.

### Quick commands and tools

Once the plugin is installed, these in-session commands are available:

- `/workspace-guard workspace_status` - show the profile workspace memo
  (profiles + workspaces + status + changed_at)
- `/workspace-guard workspace_update` - rebuild the memo manually

Tools:

- `workspace_guard_auto_update_workspace` - auto-syncs the same memo
  (agent-triggered when the memo is stale)
- `workspace_guard_register_workspace(profile, workspace)` - registers a new
  workspace for the current profile. It sets the profile's `terminal.cwd`
  (durable, config-first) and writes the memo entry; because sync derives
  `terminal.cwd`, a registration is never clobbered.

### Scripts

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

### Verify

Start a new Hermes session and try writing a file to the workspace root. It
should be blocked with a helpful message explaining how to create a Session
Directory first.

## Configuration

The plugin reads `guard-config.yaml` from
`HERMES_HOME/workspace-guard/guard-config.yaml` (Windows:
`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`; POSIX:
`~/.hermes/workspace-guard/guard-config.yaml`) - the same directory as the
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

- `exempt_paths`: A whitelist of paths inside the Default Working Directory
  that bypass guard enforcement, e.g. project directories that live inside the
  workspace. Matching is a prefix match after normalizing to forward slashes.
- `working_dir_root`: A manual fallback for the Default Working Directory.
  Only used when auto-detection from the Hermes profile configuration or the
  `TERMINAL_CWD` environment variable fails. It never overrides a resolvable
  profile workspace.
- `allowed_root_files`: A whitelist of file names permitted at the Default
  Working Directory root (the optional workspace rules file). The plugin
  guard's root exemption and the audit read the SAME key, so they never
  disagree about which root files are allowed. If the key (or the whole
  config) is missing, the strict fallback is an empty whitelist: every root
  file is flagged (fail-closed).

By default the plugin resolves `working_dir_root` automatically from the
active Hermes profile's `terminal.cwd`. If it cannot resolve a working
directory at all, the guard disables itself (fail-open) and shows a one-time
warning, so a broken configuration never silently turns off the protection.

## Enforcement Details

### Cross-profile writes

When the agent writes into another profile's working directory (for example a
`job-hunt` profile while the session belongs to `default`), the plugin shows a
human-approval gate instead of silently allowing or blocking it. Approvals are
keyed per target profile (`cross-profile-write:<profile>`), so allowing one
profile does not pre-approve another. Choices include run-once,
allow-this-conversation, always (persisted to the Hermes `command_allowlist`),
and deny.

### The `workspace_guard_allow_path` tool

When the user explicitly names a path in conversation, the agent can call the
`workspace_guard_allow_path` tool to exempt that path (and everything under
it) for the current session. The exemption is session-scoped: it is cleared at
the start of each new session, and it never persists to disk.

### Fail-open

If the guard cannot resolve the working directory, it disables itself rather
than breaking the agent, and injects a one-time visible warning. The warning
re-appears at the start of a new session while the guard remains disabled.

### Path normalization

On Windows, MSYS-style paths (`/e/...`, `//e/...`, `/cygdrive/e/...`) are
normalized to their drive-letter form before classification, and
exempt/allowlist matching is case-insensitive (matching the case-insensitive
filesystem). POSIX systems keep exact matching.

## License

MIT
