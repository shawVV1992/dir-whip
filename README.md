# workspace-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version: 0.2.0](https://img.shields.io/badge/version-0.2.0-blue.svg)](https://github.com/shawVV1992/workspace-guard)

[中文版](./README-zh.md) | [English](./README.md)

workspace-guard enforces Working Directory file discipline for Hermes agents.
A bundled `workspace-organization` skill teaches the rules; a plugin with 7
hooks blocks violations before they land. Version 0.2.0 — installable with 1
command on Windows, Linux, WSL, and macOS.

[Installation & Quick Start](#installation--quick-start) ·
[What It Guards](#what-it-guards) · [Commands](#commands) ·
[Configuration](#configuration) ·
[Advanced Usage](#advanced-usage) · [Security & Risk](#security--risk) ·
[Contributing](#contributing) · [License](#license)

## Why

- **Two layers, one package** — the skill teaches, the plugin enforces; 1
  native command installs both.
- **Enforcement before violation** — 7 hooks intercept file writes; root-level
  violations are blocked with a fix-it message.
- **Zero-config discipline** — an always-on prompt (≤500 chars) ships by
  default; misconfiguration fails open with a WARNING, never in silence.
- **Scheduled governance** — `audit_workspace.py --gate` powers zero-token
  cron pre-runs with the wakeAgent / [SILENT] pattern.
- **Observable by design** — 3 slash commands, 5 `workspace-guard:*` events,
  and privacy-trimmed stats.jsonl (5 MB rollover).
- **Cross-platform** — Windows 10+, Linux, WSL, and macOS.

## Features

| Area | Capability |
| ---- | ---------- |
| **Plugin guard** | 7 hooks block writes to the Working Directory root outside Session Directories; external paths are allowed and logged |
| **Bundled skill** | `workspace-organization` ships inside the plugin: full discipline reference + audit workflow |
| **Session Directories** | `YYYYMMDD_HHMMSS_TaskName/` with `Outputs/` and `.tmp/`, created by a bundled script |
| **Commands** | `/workspace-guard status`, `stats [--all] [--subagent]`, `doctor` |
| **Tool** | `workspace_guard_allow_path` — session-scoped path exemption, the plugin's only tool |
| **Configuration** | `guard-config.yaml` with 4 keys, outside the plugin dir, survives reinstalls |
| **Governance** | `audit_workspace.py --gate` with the cron wakeAgent / [SILENT] pattern |
| **Observability** | stats.jsonl (5 MB rollover, privacy-trimmed); 5 `workspace-guard:*` events |

## Installation & Quick Start

### Prerequisites

- Hermes CLI or desktop with plugin support (manifest v2).
- Network access to GitHub for the install command.

### Quick Start (Human Users)

```bash
# 1. Install the plugin plus the bundled skill, scripts, and config template
hermes plugins install shawVV1992/workspace-guard/workspace-guard --enable

# 2. Restart Hermes — the guard activates on the next session

# 3. Verify the effective configuration and its source
/workspace-guard status
```

The guard becomes active after the next Hermes restart. No installer script
and no separate skill install are needed.

### Quick Start (AI Agent)

> **Note for AI assistants:** If you are an AI agent asked to install or
> verify workspace-guard, run the commands below. The guard activates only
> after Hermes restarts — do not test file writes in the current session.

```bash
# 1. Install (user approval may be required)
hermes plugins install shawVV1992/workspace-guard/workspace-guard --enable
# 2. Report that a restart is required, then verify in a new session
/workspace-guard status
```

For the full discipline reference, load the bundled skill explicitly with
`workspace-guard:workspace-organization`.

### Update

```bash
hermes plugins install shawVV1992/workspace-guard/workspace-guard --force
```

`guard-config.yaml` is preserved across reinstalls.

### Uninstall

```bash
hermes plugins remove workspace-guard
```

## What It Guards

Every Hermes conversation that produces files gets one Session Directory at
the Working Directory root, named `YYYYMMDD_HHMMSS_TaskName/`, with `Outputs/`
for formal deliverables and `.tmp/` for intermediate files.

| Target | Verdict |
| ------ | ------- |
| Inside a Session Directory | Allowed |
| Exempt or runtime-allowlisted path | Allowed |
| Whitelist file at the root (`allowed_root_files`) | Allowed |
| Other writes at the Working Directory root | Blocked — create a Session Directory |
| Outside the Working Directory | Allowed + logged (external) |
| Uncertain terminal write intent | Allowed + logged |

Terminal writes are intercepted at the shell level: redirects (`>` `>>` `1>`
`2>`), `touch`, and `cp`/`mv` destinations. Complex pipelines are observed,
not parsed.

## Commands

| Command | Purpose |
| ------- | ------- |
| `/workspace-guard status` | Effective config + resolving source (working_dir_root, terminal_guard, exempt_paths, allowed_root_files) |
| `/workspace-guard stats [--all] [--subagent]` | This session's interception statistics; `--all` reads persisted totals |
| `/workspace-guard doctor` | Configuration self-check: parseability, keys, resolution chain, stats writability |

`workspace_guard_allow_path(path)` is the plugin's only tool: it registers a
user-specified path for the current session (see Advanced Usage).

## Configuration

Optional and user-managed, at `HERMES_HOME/workspace-guard/guard-config.yaml`
(Windows: `%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`; POSIX:
`~/.hermes/workspace-guard/guard-config.yaml`).

| Key | Meaning |
| --- | ------- |
| `exempt_paths` | Paths exempt from enforcement (prefix match, absolute, forward slashes) |
| `allowed_root_files` | Filenames allowed at the Working Directory root; strict empty-list fallback |
| `working_dir_root` | Explicit Working Directory override; fallback = profile `terminal.cwd` |
| `terminal_guard` | Enable/disable terminal write interception (default: enabled) |

```yaml
exempt_paths: []
allowed_root_files: ["AGENTS.md"]
# working_dir_root: E:/HermesWorkspace/default   # optional override
# terminal_guard: enabled                        # default when absent
```

## Advanced Usage

**Working Directory resolution.** Resolution follows three steps: explicit
`working_dir_root` in guard-config.yaml wins; otherwise the current profile's
`terminal.cwd` is used; if both fail, the guard falls back to the current
working directory with a WARNING. `/workspace-guard status` reports the value
and its source.

**Path exemption for a session.** When a user explicitly names a target path
in the conversation, call `workspace_guard_allow_path(path)` before writing.
The entry lasts for the current session only and merges with `exempt_paths`.

**Statistics.** Every verdict is appended as one JSON line to
`HERMES_HOME/workspace-guard/stats.jsonl` — no file contents, no absolute
paths, no prompt text. At 5 MB the file rolls over to `stats.jsonl.1`. Use
`/workspace-guard stats --all` for cross-session totals.

**Cron governance.** `audit_workspace.py --gate` is a zero-token pre-run gate
for Hermes cron jobs:

```bash
# Cron job: script= scripts/audit_workspace.py --gate
#           skill= workspace-guard:workspace-organization
#           prompt: "If audit found violations, classify and archive misplaced
#                    files. If no violations, respond with [SILENT]."
```

- stdout "OK" → `{"wakeAgent": false}` → silent tick, no delivery
- stdout lists violations → `{"wakeAgent": true}` → the agent wakes, classifies,
  and moves files into Session Directories
- `--workspace` mismatch → exit 2, no wakeAgent (a misconfigured boundary is a
  system problem, not a governance situation)

## Security & Risk

workspace-guard is a discipline aid, not a security boundary.

**What can go wrong.** An agent may be prompted to write anywhere; a prompt
injection can push writes to unexpected locations. Widening `exempt_paths` or
disabling the guard leaves the workspace unmanaged. Misconfiguration is not
always visible without a check.

**Built-in protections.** Enforcement happens in the `pre_tool_call` hook
before a write lands. Misconfiguration fails open with a WARNING, never in
silence. External writes are allowed but logged. Stats are privacy-trimmed.
The audit gate refuses to wake agents when `--workspace` mismatches, and
`/workspace-guard doctor` verifies config and stats health.

**If you disable it.** Disabling the plugin stops all enforcement; nothing is
reverted, and misplaced files are not cleaned up automatically. Disabling
`terminal_guard` leaves shell writes unmonitored.

**Enable / disable / restore default.**

```bash
# On — install with the plugin enabled
hermes plugins install shawVV1992/workspace-guard/workspace-guard --enable

# Off
hermes plugins disable workspace-guard

# Restore default — re-enable
hermes plugins enable workspace-guard
```

```yaml
# terminal_guard three states (config-level)
terminal_guard: enabled    # On (default)
terminal_guard: disabled   # Off
                           # Restore default: remove the line
```

**Recommended use.** Keep the defaults. Exempt only intentional project
directories. When a write is blocked, create a Session Directory and re-target
— never bypass the guard. Review `/workspace-guard stats` periodically.

**Responsibility.** Configuration is user-managed. The guard assists review;
it does not replace it.

## Contributing

Bug reports, feature requests, and pull requests are welcome:
[github.com/shawVV1992/workspace-guard](https://github.com/shawVV1992/workspace-guard).
Development is spec-driven; the authoritative spec lives in the repository.

## License

[MIT](./LICENSE) — see the [LICENSE](./LICENSE) file. No third-party
components are bundled.
