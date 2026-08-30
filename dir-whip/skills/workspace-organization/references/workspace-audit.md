# Workspace Compliance Audit

Procedure for scanning a workspace against the compliance checklist and fixing violations.

## When to Run

- User asks to "tidy" or "organize" a workspace
- After a period of heavy use where files may have accumulated outside session dirs
- Cron governance job (with --gate)

## Workspace Legitimacy

Before auditing, confirm the target is a legitimate Working Directory:

- **Resolution** -- the audit resolves the Working Directory via the layered
  chain (dir-whip-config.yaml `working_dir_root` override -> current profile's
  `terminal.cwd` -> profile enumeration + TERMINAL_CWD candidate root ->
  fail-open). An explicit `--workspace` must equal the resolved root (exit 2
  on mismatch); when resolution fails, the audit fails open to CWD with one
  concise stderr warning and proceeds.
- **Whitelist** -- the root rules file must be covered by an `allowlist`
  `files` entry (config-driven,
  `HERMES_HOME/dir-whip/dir-whip-config.yaml`). The guard and the audit
  read the same key, so they never disagree about which root files are
  permitted. Missing key -> strict empty whitelist (fail-closed); legacy
  flat-format entries are ignored with a warning.

## Audit Checklist

| # | Check | Violation Example |
|:-:|-------|-----------------|
| 1 | **Root-level entries** -- only allowlist `files` entries, allowlist `dirs` subtrees, and session-format dirs (`YYYYMMDD_HHMMSS_TaskName/`) should exist at the workspace root; a leftover `.hermes/` directory is flagged too (the audit quarantine moved to the dir-whip home) | Scripts, images, notes dumped in root |
| 2 | **Root-level Outputs/** -- deliverables must live inside a session dir's Outputs/ | `Outputs/script.sh` at top level |
| 3 | **Session dir format** -- must be `YYYYMMDD_HHMMSS_TaskName` | Plain-named folders, missing timestamp |
| 4 | **Session subdirs** -- each session dir must have both `Outputs/` and `.tmp/` | Missing `.tmp/` or missing `Outputs/` |
| 5 | **Outputs content** -- only user-facing deliverables, no build artifacts | `__pycache__/`, `.pyc`, node_modules |
| 6 | **.tmp/ content** -- intermediate/scratch files go here, not in root or Outputs | Debug scripts left in workspace root |
| 7 | **Destructive commands** -- never use `rm -rf`, `del /S/Q`, bulk rename. This is a behavioral rule, not an automated audit check. | Use `rmdir` for empty dirs, `mv` for files |

## Procedure

### Step 1: Read the Workspace Rules File

The rules file is the root file covered by an `allowlist` `files` entry
(config-driven, shared with the guard). Read it by its actual name (the
placeholder below stands for the whitelisted name):

```bash
cat "<WORKSPACE_PATH>/<rules-file>"
```

Extract the specific rules about directory structure, file placement, and
prohibitions. If no rules file exists yet, skip this step and apply the
checklist defaults below.

### Step 2: Inventory the Workspace

```bash
ls -laR "<WORKSPACE_PATH>/"
```

Capture the full tree - root, session dirs, their contents.

### Step 3: Identify Violations

Compare each item against the checklist above. Common patterns:

- **Orphaned file at root** -- script sitting beside the workspace rules file instead of inside a session dir
- **Missing .tmp/** -- session dir created with only Outputs/
- **Build artifacts in Outputs** -- `__pycache__/` from Python scripts landed in the deliverables folder
- **Root Outputs/** -- files saved to the workspace root `Outputs/` instead of `YYYYMMDD_HHMMSS_TaskName/Outputs/`

### Step 4: Fix - One Violation at a Time

Use targeted operations only - no bulk moves, no recursive deletes on user files.

| Operation | Command | Use Case |
|-----------|---------|----------|
| Create dir | `mkdir -p` | Missing `.tmp/`, new session dir |
| Move file | `mv src dest` | Orphaned file -> session dir |
| Remove empty dir | `rmdir` | Empty root Outputs/ after moving contents |
| Remove artifact | per-file delete (Python) | `__pycache__/` in Outputs |

Destructive operations follow the skill's Confirmation Protocol: report the
exact file list and wait for explicit `yes` / `confirm` / `go ahead`.

### Step 5: Verify Final State

```bash
ls -la "<WORKSPACE_PATH>/"
ls -laR "<WORKSPACE_PATH>/" --ignore='.tmp' | head -40
```

Confirm:
- No root-level deliverables remain
- Every session dir has Outputs/ + .tmp/
- Outputs/ contains only user-facing files
- The workspace rules file is untouched

## Cron Mode

With `--gate` the audit runs a pure audit and emits a single JSON wakeAgent
line (`{"wakeAgent": false, "violations": 0}` on compliance,
`{"wakeAgent": true, "violations": N}` otherwise -- exactly two keys); an
unresolved Working Directory is a gate failure (exit 2, no wakeAgent line).
ZERO auto-delete: the plugin never deletes anything. The interactive audit
lists expired `.tmp/` entries as a read-only inventory proposal
("Expired .tmp entries (proposal only; cleanup needs your confirmation):");
the agent (or the user) decides what to clean up.
