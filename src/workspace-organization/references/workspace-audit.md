# Workspace Compliance Audit

Procedure for scanning a workspace against AGENTS.md rules and fixing violations.

## When to Run

- User asks to "clean up" or "organize" a workspace
- After a period of heavy use where files may have accumulated outside session dirs
- When adding a new profile and validating its workspace structure
- User says "调整工作区内不符合规则的文件"

## Audit Checklist

| # | Check | Violation Example |
|:-:|-------|-----------------|
| 1 | **Root-level files** — only AGENTS.md (and optional .hermes/) should exist at workspace root | Scripts, images, notes dumped in root |
| 2 | **Root-level Outputs/** — deliverables must live inside a session dir's Outputs/ | `Outputs/script.sh` at top level |
| 3 | **Session dir format** — must be `YYYYMMDD_HHMMSS_TaskName` | Plain-named folders, missing timestamp |
| 4 | **Session subdirs** — each session dir must have both `Outputs/` and `.tmp/` | Missing `.tmp/` or missing `Outputs/` |
| 5 | **Outputs content** — only user-facing deliverables, no build artifacts | `__pycache__/`, `.pyc`, node_modules |
| 6 | **.tmp/ content** — intermediate/scratch files go here, not in root or Outputs | Debug scripts left in workspace root |
| 7 | **Destructive commands** — never use `rm -rf`, `del /S/Q`, bulk rename. This is a behavioral rule for agents, not an automated audit check. | Use `rmdir` for empty dirs, `mv` for files |

## Procedure

### Step 1: Read AGENTS.md

```bash
cat "<WORKSPACE_PATH>/AGENTS.md"
```

Extract the specific rules about directory structure, file placement, and prohibitions.

### Step 2: Inventory the Workspace

```bash
ls -laR "<WORKSPACE_PATH>/"
```

Capture the full tree — root, session dirs, their contents.

### Step 3: Identify Violations

Compare each item against the checklist above. Common patterns:

- **Orphaned file at root** — script sitting beside AGENTS.md instead of inside a session dir
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
- AGENTS.md and .hermes/ are untouched
