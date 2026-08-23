---
name: workspace-organization
description: Use when creating, saving, writing, moving, or deleting files in a Hermes workspace, organizing deliverables, designing workspace layout, or auditing workspace compliance.
---

# Workspace Organization

File discipline for Hermes agent workspaces. Enforces session directory
structure, file placement rules, and root-forbid governance. Loaded via
`dir-whip:workspace-organization` when deep reference is needed.

## Scope Check (Layer 0)

Evaluate in order. First match wins. Do NOT evaluate subsequent conditions.

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Stop. This skill does not apply.
IF CWD not under the current profile's Working Directory
  -> PROJECT MODE. Stop. This skill does not apply.
OTHERWISE
  -> DEFAULT MODE. Proceed to Layer 1.
```

## Instant Discipline (Layer 1)

Triggered by: any file write, create, save, delete, or move.

**Target classification (state before every write):**

| Target classification | Guard behavior |
|---|---|
| Inside a Session Directory (`YYYYMMDD_HHMMSS_TaskName/...`) | Allow |
| Root whitelist file (`allowed_root_files`) | Allow |
| External path (outside Working Directory) | Allow + logged (fail-open) |
| Working Directory root, non-whitelist | Block |

**Session directory discipline:**
1. Inside valid Session Directory? → proceed
2. Not inside one? → create lazily: `python scripts/create_session_dir.py <task_name> --workspace <working_dir>`, then write to `Outputs/` or `.tmp/`
3. Delete/overwrite/move? → Apply Confirmation Protocol

**Root forbid:** Root allows ONLY: `allowed_root_files`, session-format directories, `.hermes/`. Everything else → use Session Directory.

**File placement:** Formal → `Outputs/`, scratch → `.tmp/`, NEVER root directly.

## Governance Mode (Layer 2)

Triggered by: user says "tidy workspace" or cron job.

```
1. Run: python scripts/audit_workspace.py --workspace <working_dir>
2. Violations found? Classify → propose → execute with confirmation
3. No violations? Report "OK" (or [SILENT] in cron mode)
```

## Session Directory Structure

```
<Working Directory>/
├── <rules-file>                      <- root whitelist file (allowed_root_files)
├── .hermes/                          <- optional Hermes internal
├── YYYYMMDD_HHMMSS_TaskName/         <- session directory
│   ├── Outputs/                      <- formal deliverables only
│   └── .tmp/                         <- intermediate files, safe to clean
└── ...
```

**Rules:** Root allows ONLY whitelist files + `.hermes/` + session dirs. Session dirs MUST contain `Outputs/` and `.tmp/`. `.tmp/` eligible for age-based cleanup (default: 30 days).

## Confirmation Protocol

Applies to: delete, overwrite, move operations. **Instruction is NOT confirmation.**

```
Step 1: Agent lists files and asks "Confirm? (yes/no)"
Step 2: User replies "yes"/"confirm"/"go ahead" → execute; anything else → abort
```

Anti-patterns: deleting without listing first, interpreting silence as confirmation.

## Cron Governance Mode

```
Cron config:
  script: scripts/audit_workspace.py --gate
  skill: dir-whip:workspace-organization
  prompt: "If violations, classify and archive. If none, [SILENT]."

Flow:
  1. script= runs --gate
  2. "OK" → {"wakeAgent": false} → silent tick
  3. violations → {"wakeAgent": true} → agent wakes
  4. Agent classifies and moves files
  5. Agent reports summary
```

Gate failure: exit 2 on stderr, no wakeAgent. Resolution failure: fail-open to CWD.

## Scripts

All scripts: Python 3.11, `--help` support, forward-slash output paths.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| create_session_dir.py | Create session dir with Outputs/ + .tmp/ | `--workspace` |
| audit_workspace.py | Compliance audit with gate + cron .tmp cleanup | `--workspace`, `--json`, `--gate`, `--days` |

Boundary: `--workspace` must match resolved root (exit 2 on mismatch). Resolution failure: fail-open to CWD with warning.

## Interception Response Template

When blocked, reply with:

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will create a Session Directory and write there:
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/ or .tmp/ subdirectory.
```

**Subagent variant:** Replace "I will create" with "I will write to the target directory passed by the parent agent."

## Subagent File Protocol

- Parent ensures target directory exists before delegating (lazy creation is parent's job)
- Subagents write to parent's `.tmp/` (default) or explicit `Outputs/`/per-task subdirectory
- Subagents do NOT create session directories or promote outputs (`.tmp/` → `Outputs/` is parent's review)
- Missing target or blocked write → report back to parent

## Terminal Write Discipline

Layer 1 applies to terminal writes. Guard intercepts: redirects (`>` `>>`), `touch`, `cp`/`mv` destinations. Uncertain intent allowed + logged.

Rules:
1. Prefer Session Directories for all writes
2. USER specifies path → call `dir_whip_allow_path(path)` BEFORE writing
3. Blocked → create Session Directory and re-target (never bypass guard)

## Guarded Path Classification

| Location | Behavior |
|----------|----------|
| Inside Session Directory or exempt path | Allow |
| Root whitelist file (`allowed_root_files`) | Allow |
| Root, non-whitelist | Block → create Session Directory |
| Outside Working Directory | Allow + logged (external) |
| Uncertain terminal intent | Allow + logged |

## Compliance Audit

1. Run: `python scripts/audit_workspace.py --workspace <dir>` (add `--json`)
2. See `references/workspace-audit.md` for checklist
3. Cron: auto-cleans `.tmp/` (age-based, default 30 days); interactive: propose only

## Pitfalls

- Session dir creation is lazy - NOT at conversation start
- Project mode: this skill does not apply
- Confirmation: listing ≠ confirming deletion
- Existing repos outside workspace → use rules file, don't relocate
- Don't assume project file rules from other contexts
