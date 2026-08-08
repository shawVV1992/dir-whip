# workspace-guard

workspace-guard enforces file discipline for Hermes agent workspaces. Hermes agents do not reliably follow file placement rules: deliverables scatter across the workspace root, outputs from specific conversations become unfindable, and intermediate files accumulate without a home. workspace-guard fixes this with two complementary layers - a skill that teaches agents the rules, and a plugin that blocks violations before they happen.

## How It Works

The two layers have zero runtime coupling:

- **Skill (teach)**: The `workspace-organization` skill instructs the agent to place every file inside a Session Directory named `YYYYMMDD_HHMMSS_TaskName/`, which contains exactly two subdirectories: `Outputs/` for formal deliverables and `.tmp/` for intermediate files. Session Directories are created lazily at the first file write, so conversations that produce no files create no directories. Destructive operations (delete, overwrite, move) follow a two-step confirmation protocol: instruction is not confirmation.
- **Plugin (enforce)**: The `workspace-guard` plugin registers a `pre_tool_call` hook that intercepts `write_file`, `patch`, and `terminal` operations. A write inside the Default Working Directory is allowed only if the target path is inside a valid Session Directory, is exempted, or is `AGENTS.md` itself. Anything else is blocked with instructions on how to fix it. Terminal commands are classified by a lightweight tokenizer into three tiers: writes to the workspace root are blocked, lower-confidence writes request human approval, and everything else passes. The guard is fail-open: if the working directory cannot be resolved, it disables itself and warns the user once instead of breaking the agent.

Neither layer depends on the other at runtime. The skill works alone as a teaching layer; the plugin works alone as an enforcement layer. Together they ensure the agent both knows the rules and cannot bypass them.

## Installation

### Skill

```bash
hermes skills install <repo-url>/src/workspace-organization
```

The skill's scripts validate workspaces against the profile workspace memo
(`profile-workspaces.json`), not a rules-file presence check.

### Plugin

```bash
hermes plugins install <repo-url>#src/workspace-guard --enable
```

The guard becomes active after the next Hermes restart.

### Quick commands

Once the plugin is installed, these in-session commands are available:

- `/workspace-guard workspace_status` — show the profile workspace memo
  (profiles + workspaces + status + changed_at)
- `/workspace-guard workspace_update` — rebuild the memo manually

The `workspace_guard_auto_update_workspace` tool auto-syncs the same memo.

### Verify

Start a new Hermes session and try writing a file to the workspace root. It should be blocked with a helpful message explaining how to create a Session Directory first.

## Configuration

The plugin reads `guard-config.yaml` from `~/.hermes/workspace-guard/` (shared
with the memo; outside the plugin dir so forced reinstalls do not wipe it).
The file is user-managed and optional; the plugin works out of the box.

```yaml
# workspace-guard configuration
# Paths listed here are exempt from session directory enforcement.
# Use absolute paths with forward slashes.

exempt_paths: []
  # - <WORKSPACE_PATH>/projects/my-project

# Optional fallback: working_dir_root (used ONLY if auto-detection from the
# profile config or TERMINAL_CWD fails; never overrides auto-detection)
# working_dir_root: <WORKSPACE_PATH>
```

- `exempt_paths`: A whitelist of paths inside the Default Working Directory that bypass guard enforcement, e.g. project directories that live inside the workspace. Matching is a prefix match after normalizing to forward slashes.
- `working_dir_root`: A manual fallback for the Default Working Directory. Only used when auto-detection from the Hermes profile configuration or the `TERMINAL_CWD` environment variable fails. It never overrides a resolvable profile workspace.

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

The bundled scripts are self-contained CLI tools. Each one validates that its
target matches a profile workspace recorded in the workspace memo
(`profile-workspaces.json`) before operating on it (except `init_workspace.py`,
which creates and registers new workspaces).

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `create_session_dir.py` | Create a Session Directory `YYYYMMDD_HHMMSS[_TaskName]/` with `Outputs/` and `.tmp/`, print its absolute path | `--workspace <path>` |
| `audit_workspace.py` | Read-only compliance audit against the workspace rules; exit code 1 when violations are found | `--workspace`, `--json`, `--gate` |
| `clean_tmp.py` | Delete expired files inside session `.tmp/` directories (dry-run by default) | `--days N`, `--workspace`, `--confirm` |
| `init_workspace.py` | Create a new workspace directory and register its profile in the memo | `--workspace` |

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
single optional rules file may exist but is not required, is not written by the
tool, and does not affect workspace validation (SCR-011: validation uses the
profile workspace memo).

## Development

- Python 3.11 (command: `python`)
- Tests: pytest, run from the project root with the virtual environment active
- Test layout: plugin tests `tests/test_config.py`, `tests/test_guard.py`; script tests `tests/test_create_session_dir.py`, `tests/test_audit_workspace.py`, `tests/test_clean_tmp.py`, `tests/test_init_workspace.py`

```bash
python -m pytest
```

Scripts live in `src/workspace-organization/scripts/`. All skill content changes go in `src/workspace-organization/`; plugin code goes in `src/workspace-guard/`.

## License

MIT
