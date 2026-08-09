# Workspace Compliance Audit

Procedure for scanning a workspace against its workspace rules file and fixing violations.

## When to Run

- User asks to "clean up" or "organize" a workspace
- After a period of heavy use where files may have accumulated outside session dirs
- When adding a new profile and validating its workspace structure
- User says "调整工作区内不符合规则的文件"

## Workspace Legitimacy

Before auditing, confirm the target is a legitimate workspace. Legitimacy is
judged by the memo and the whitelist together:

- **Memo** -- the target must be registered in the profile workspace memo
  (exact match of a profile's workspace value, i.e. that profile's
  `terminal.cwd`). Run `python scripts/audit_workspace.py --workspace <dir>`;
  a target that is not a registered profile workspace is rejected (exit 2).
- **Whitelist** -- the root workspace rules file must be listed in the
  `allowed_root_files` whitelist (config-driven,
  `HERMES_HOME/workspace-guard/guard-config.yaml`). The guard and the audit
  read the same key, so they never disagree about which root files are
  permitted.

## Audit Checklist

| # | Check | Violation Example |
|:-:|-------|-----------------|
| 1 | **Root-level files** — only the workspace rules file (name per the allowed_root_files whitelist) and optional .hermes/ should exist at workspace root | Scripts, images, notes dumped in root |
| 2 | **Root-level Outputs/** — deliverables must live inside a session dir's Outputs/ | `Outputs/script.sh` at top level |
| 3 | **Session dir format** — must be `YYYYMMDD_HHMMSS_TaskName` | Plain-named folders, missing timestamp |
| 4 | **Session subdirs** — each session dir must have both `Outputs/` and `.tmp/` | Missing `.tmp/` or missing `Outputs/` |
| 5 | **Outputs content** — only user-facing deliverables, no build artifacts | `__pycache__/`, `.pyc`, node_modules |
| 6 | **.tmp/ content** — intermediate/scratch files go here, not in root or Outputs | Debug scripts left in workspace root |
| 7 | **Destructive commands** — never use `rm -rf`, `del /S/Q`, bulk rename. This is a behavioral rule, not an automated audit check. | Use `rmdir` for empty dirs, `mv` for files |

## Procedure

### Step 1: Read the Workspace Rules File

The rules file is the root file named in the `allowed_root_files` whitelist
(config-driven, shared with the guard). Read it by its actual name (the
placeholder below stands for the whitelisted name):

```bash
cat "<WORKSPACE_PATH>/rules.md"
```

Extract the specific rules about directory structure, file placement, and
prohibitions. If no rules file exists yet (a freshly registered workspace),
skip this step and apply the checklist defaults below.

### Step 2: Inventory the Workspace

```bash
ls -laR "<WORKSPACE_PATH>/"
```

Capture the full tree — root, session dirs, their contents.

### Step 3: Identify Violations

Compare each item against the checklist above. Common patterns:

- **Orphaned file at root** — script sitting beside the workspace rules file instead of inside a session dir
- **Missing .tmp/** — session dir created with only Outputs/
- **Build artifacts in Outputs** — `__pycache__/` from Python scripts landed in the deliverables folder
- **Root Outputs/** — files saved to profile-root `Outputs/` instead of `YYYYMMDD_HHMMSS_TaskName/Outputs/`

### Step 4: Fix — One Violation at a Time

Use targeted operations only — no bulk moves, no recursive deletes on user files.

| Operation | Command | Use Case |
|-----------|---------|----------|
| Create dir | `mkdir -p` | Missing `.tmp/`, new session dir |
| Move file | `mv src dest` | Orphaned file → session dir |
| Remove empty dir | `rmdir` | Empty root Outputs/ after moving contents |
| Remove artifact | `shutil.rmtree` (Python) or per-file delete | `__pycache__/` in Outputs |

### Step 5: Verify Final State

```bash
ls -la "<WORKSPACE_PATH>/"
ls -laR "<WORKSPACE_PATH>/" --ignore='.tmp' | head -40
```

Confirm:
- No root-level deliverables remain
- Every session dir has Outputs/ + .tmp/
- Outputs/ contains only user-facing files
- The workspace rules file and .hermes/ are untouched
