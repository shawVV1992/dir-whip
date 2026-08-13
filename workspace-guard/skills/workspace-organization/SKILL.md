---
name: workspace-organization
description: Use when creating, saving, writing, moving, or deleting files, organizing deliverables, designing workspace layout, or auditing workspace compliance. Enforces session directory discipline and two-step confirmation for destructive operations.
---

# Workspace Organization

File discipline for Hermes agent workspaces. Teaches session directory
structure, file placement rules, the root-forbid rule, and governance
workflows. The skill is bundled inside the workspace-guard plugin and loads
explicitly by its qualified name `workspace-guard:workspace-organization`
when deep reference is needed; a short always-on discipline prompt covers
day-to-day behavior.

## Behavior Layers

Evaluate in order. Each layer builds on the previous.

### Layer 0: Scope Check (short-circuit)

Determine whether this skill applies BEFORE doing anything else.

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Stop. This skill does not apply.
IF CWD not under the current profile's Working Directory
  -> PROJECT MODE. Stop. This skill does not apply.
OTHERWISE
  -> DEFAULT MODE. Proceed to Layer 1.
```

Evaluate in order. First match wins. Do NOT evaluate subsequent conditions.

### Layer 1: Instant Discipline (every file operation)

Triggered by: any file write, create, save, delete, or move.

**Step 1 - classify the target before writing.** State the target class
explicitly before every create/write:

| Target classification | Guard behavior |
|---|---|
| Inside a Session Directory (`working_dir_root/YYYYMMDD_HHMMSS_TaskName/...`) | Allow |
| Root whitelist file (`allowed_root_files`) | Allow |
| External path (outside the Working Directory) | Allow + logged (fail-open) |
| Working Directory root, non-whitelist (anything else at root) | Block |

**Step 2 - session directory discipline:**

```
1. Am I inside a valid Session Directory?
   - YES -> proceed with operation
   - NO  -> create a Session Directory first (lazy creation):
            python scripts/create_session_dir.py <task_name> --workspace <working_dir>
            then write to Outputs/ or .tmp/ within it

2. Is this a delete / overwrite / move?
   -> Apply the Confirmation Protocol (below)

3. Execute operation
```

Key rule: Session Directory is created LAZILY at first file write, NOT at
conversation start. Conversations that produce no files create no
directories.

**Step 3 - root forbid.** The Working Directory root allows EXACTLY:
`allowed_root_files` whitelist files, session-format directories
(`YYYYMMDD_HHMMSS_TaskName/`) and their contents, and `.hermes/`. Every
other creation at the root is strongly forbidden - use a Session Directory
instead.

File placement:
- Formal deliverables -> `Outputs/`
- Intermediate scripts, debug files, scratch work -> `.tmp/`
- NEVER save to the Working Directory root directly

### Layer 2: Governance Mode (on request or cron)

Triggered by:
- User says "tidy workspace" / "organize files" / equivalent
- Cron job with the attached skill

```
1. Run audit: python scripts/audit_workspace.py --workspace <working_dir>
2. If violations found:
   - Classify each violation (misplaced deliverable / temp file / unknown)
   - Propose action (move to session dir / move to .tmp / leave)
   - Execute with user confirmation (or auto in cron mode for .tmp cleanup)
3. If no violations: report "OK" (or [SILENT] in cron mode)
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

Rules:
- Root allows ONLY: `allowed_root_files` whitelist files, `.hermes/`, and
  session-format directories (`YYYYMMDD_HHMMSS_TaskName/`) with their contents
- Every session dir MUST contain both Outputs/ and .tmp/
- Outputs/ blacklist: `__pycache__/`, `*.pyc`, `node_modules/`, `.DS_Store`, `Thumbs.db`
- .tmp/ eligible for age-based cleanup (default: 30 days)

## Confirmation Protocol

Applies to: delete, overwrite, move operations.

**Rule: Instruction is not confirmation.**

```
Step 1: Agent reports the operation
  "I will [delete/overwrite/move] the following file(s):
   - /path/to/file1
   - /path/to/file2
   Confirm? (yes/no)"

Step 2: User replies with explicit confirmation
  "yes" / "confirm" / "go ahead" -> execute
  Anything else -> abort
```

The user's initial instruction ("delete X") triggers Step 1. It is NEVER
treated as confirmation itself.

Anti-patterns (NEVER do these):
- User says "delete old files" -> agent deletes without listing them first
- Agent interprets silence as confirmation

## Cron Governance Mode

Designed for Hermes cron with the hybrid pattern:

```
Cron job configuration:
  script: scripts/audit_workspace.py --gate (pre-run gate, zero tokens)
  skill: workspace-guard:workspace-organization   # qualified plugin-skill name
  prompt: "If audit found violations, classify and archive misplaced files.
           If no violations, respond with [SILENT]."

Flow:
  1. script= runs audit_workspace.py --gate
  2. stdout "OK" -> {"wakeAgent": false} -> silent tick, no delivery
  3. stdout violations -> {"wakeAgent": true} -> agent wakes
  4. Agent classifies violations and moves files to appropriate session dirs
  5. Agent reports summary (delivered to the configured platform)
```

Gate failure: when the `--workspace` mismatch check fails, the audit exits 2
with the reason on stderr and emits NO wakeAgent line - the cron tick fails
visibly and the agent is NOT woken. On Working Directory resolution failure
the audit follows the fail-open chain: it falls back to CWD with one stderr
warning and proceeds.

## Scripts

All scripts: Python 3.11, `--help` support, forward-slash output paths.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| create_session_dir.py | Create session dir with Outputs/ + .tmp/ | `--workspace` |
| audit_workspace.py | Compliance audit with gate + cron .tmp cleanup | `--workspace`, `--json`, `--gate`, `--days` |

Boundary validation: scripts validate the target against the resolved
Working Directory (layered chain: guard-config override -> profile
terminal.cwd -> candidate roots -> fail-open). An explicit `--workspace`
must match the resolved root (exit 2 on mismatch); a resolution failure
fails open to CWD with one warning.

## Creation Workflow Examples

### Negative example (wrong)

- User asks "save the report"; the agent writes directly to
  `<working_dir>/report.md` - a root write, not whitelisted -> blocked by
  the guard with fix instructions.

### Positive example (correct)

- The agent classifies the target as a session-dir write; runs
  `python scripts/create_session_dir.py <task_name> --workspace <working_dir>`;
  writes the deliverable to `Outputs/report.md` (or scratch to `.tmp/`).

## Interception Response Template

When a write is blocked by the guard, reply with this template (aligned
with the guard's block message):

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will create a Session Directory and write there:
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/ or .tmp/ subdirectory.
```

### Subagent variant

A blocked subagent replies:

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will write to the target directory passed by the parent agent.
```

and reports the block back to the parent. Subagents never create session
directories themselves.

## Subagent File Protocol

- The PARENT ensures the target directory EXISTS before delegating (creating
  the parent Session Directory first if needed - lazy creation stays the
  parent's job).
- Subagents write to the target directory passed by the parent. Default: the
  parent session's `.tmp/`. The parent may explicitly pass an `Outputs/` path
  for formal deliverables, or a per-subagent subdirectory (e.g. `.tmp/<task>/`)
  to avoid concurrent name clashes.
- Subagents do NOT create their own session directories and do NOT promote
  their own outputs (`.tmp/` -> `Outputs/` promotion is the parent's review
  step).
- When the target directory is missing or a write is blocked, the subagent
  reports back to the parent instead of creating a session directory itself.

## Terminal Write Discipline

Layer 1 applies equally to writes made through the `terminal` tool. The
guard coarsely intercepts: redirects (`>` `>>` `1>` `2>`), `touch`, and
`cp`/`mv` destinations. Deep intent parsing is removed; uncertain write
intent is allowed and logged (no approval gate).

Rules:
1. Prefer Session Directories for all file writes, including via terminal.
2. When the USER explicitly specifies a target path in the conversation
   (e.g. "write to C:/Users/me/Reports/R1.md"), call the
   `workspace_guard_allow_path(path)` tool to register that path BEFORE
   writing, so the guard's Tier 0 allows it.
3. When a write is blocked by the guard, create a Session Directory
   (`python scripts/create_session_dir.py <task_name> --workspace <working_dir>`)
   and re-target the write there. Never bypass the guard.

## Guarded Path Classification

The plugin guard classifies every file write. Know these outcomes so a
block message never surprises you:

- Paths inside a Session Directory or matching exempt/runtime-allowlisted
  paths: allowed.
- Root whitelist files (`allowed_root_files`): allowed.
- Paths at the Working Directory root that are not whitelist files: blocked -
  create a Session Directory.
- Paths outside the Working Directory: allowed + logged (external).
- Uncertain terminal write intent: allowed + logged.

## Compliance Audit

To audit and fix violations:

1. Run: `python scripts/audit_workspace.py --workspace <dir>` (add `--json`
   for machine-readable output)
2. See `references/workspace-audit.md` for the checklist
3. In cron mode the audit auto-cleans expired `.tmp/` contents (age-based,
   default 30 days); interactive runs only propose and never delete

## Pitfalls

- Session directory creation is lazy - do NOT create one at conversation start
- In project mode this skill does not apply at all - defer to project conventions
- Confirmation protocol: listing files is NOT the same as confirming deletion
- When existing code repos are outside the workspace root, point at them via
  the workspace rules file instead of relocating them
- Don't assume project file rules from one context apply to general workspace design
