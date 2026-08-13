# workspace-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[中文版](./README-zh.md) | [English](./README.md)

workspace-guard enforces Working Directory file discipline for Hermes agent
workspaces with two complementary layers: the bundled `workspace-organization`
skill teaches the rules, and the plugin blocks violations before they happen.
This repository is version 0.2.0.

## Install

```bash
hermes plugins install shawVV1992/workspace-guard --enable
```

One native command installs the plugin plus the bundled skill, scripts, and
config template. The guard becomes active after the next Hermes restart. No
installer script and no separate skill install are needed.

## Update

```bash
hermes plugins install shawVV1992/workspace-guard --force
```

`guard-config.yaml` is preserved across reinstalls.

## Uninstall

```bash
hermes plugins remove workspace-guard
```

## What it does

Every Hermes conversation that produces files gets one Session Directory at
the Working Directory root, named `YYYYMMDD_HHMMSS_TaskName/` with `Outputs/`
for formal deliverables and `.tmp/` for intermediate files. The guard blocks
file writes to the Working Directory root outside valid Session Directories;
external paths are allowed and logged.

## Configuration

Optional and user-managed:
`HERMES_HOME/workspace-guard/guard-config.yaml` (Windows:
`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`; POSIX:
`~/.hermes/workspace-guard/guard-config.yaml`). Keys: `exempt_paths`,
`allowed_root_files`, `working_dir_root`, `terminal_guard`.

## Commands

- `/workspace-guard status` - show the effective configuration and its source
- `/workspace-guard stats` - this session's interception statistics
- `/workspace-guard doctor` - configuration self-check

## License

MIT
