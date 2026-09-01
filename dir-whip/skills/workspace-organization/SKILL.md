---
name: workspace-organization
description: "Use when creating session dirs - create session dirs in a Hermes workspace - Use when creating, saving, writing, moving, or deleting files in a Hermes workspace, organizing deliverables, or auditing workspace compliance."
author: dir-whip
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [files, workspace, session-directory, organization, audit, terminal]
    requires_toolsets: [terminal, file]
---

# Workspace Organization

File placement discipline for Hermes agent workspaces: session directory structure, Outputs/.tmp placement, root-forbid rule, and governance workflows.

## When to Use

Use when:
- Before first file write in a Hermes workspace - you MUST create session dir first
- Creating, saving, writing, moving, or deleting files in a Hermes workspace
- Organizing deliverables or designing workspace layout
- Auditing workspace compliance ("tidy workspace", cron governance)

Do NOT use when:
- Project mode is active (project_list tool available, active_id not null, CWD under project folders)
- CWD is not under the current profile's Working Directory

### Scope Check (Layer 0) - MANDATORY

You MUST evaluate this checklist in order. First match wins. Do NOT evaluate subsequent conditions.

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Stop. This skill does not apply.
IF CWD not under the current profile's Working Directory
  -> PROJECT MODE. Stop. This skill does not apply.
IF no session dir exists for this task
  -> DEFAULT MODE. Create session dir first: python scripts/create_session_dir.py <task> --workspace <root>, then proceed to Layer 1.
OTHERWISE
  -> DEFAULT MODE. Proceed to Layer 1.
```

## Quick Reference

| Scenario | Action |
|----------|--------|
| Writing any file | classify → create session dir → write: Classify target → create session dir if needed → `Outputs/` or `.tmp/` |
| Root write blocked | Create session dir, re-target there |
| Delete / overwrite / move | Confirmation Protocol (list files → wait for explicit yes) |
| User specifies a path | Call `dir_whip_allow_path(path)` (no confirm) to get the confirmation briefing, relay it to the user, then re-call with `confirm=true` only after explicit user approval. Value domain: paths INSIDE the Working Directory only - paths outside need NO entry (writes there are allowed and logged) |
| Subagent writing | Write to parent-passed dir, never create own session dir |

## Instant Discipline (Layer 1)

Triggered by: any file write, create, save, delete, or move.

### 1. Classify the target (before every write)

| Target | Guard behavior |
|--------|----------------|
| Inside a Session Directory (`YYYYMMDD_HHMMSS_TaskName/...`) | Allow |
| Root whitelist file (`allowlist` `files` entry) | Allow |
| Outside the Working Directory | Allow + logged (external) |
| Working Directory root, non-whitelist | Block |

### 2. Session directory discipline

- Session dirs are created LAZILY at first file write, not at conversation start - you MUST create session dir before first write
- Not inside a session dir? You MUST create one before first write:
  `python scripts/create_session_dir.py <task> --workspace <root>`
  Example: `python scripts/create_session_dir.py <task> --workspace <root>` - replace <task> and <root> with your values (legacy form `python scripts/create_session_dir.py <task_name> --workspace <working_dir>` also works)
- Every session dir contains `Outputs/` (deliverables) and `.tmp/` (scratch)
- Root allows ONLY: `allowlist` `files` entries, session-format dirs (`allowlist` `dirs` subtrees likewise exempt)
- Project directories inside the workspace can be exempted wholesale via an
  `allowlist` `dirs` entry (recursive subtree) — add via `/dir-whip allow <path>`
- `Outputs/` blacklist: `__pycache__/`, `*.pyc`, `node_modules/`, `.DS_Store`, `Thumbs.db`

### 3. File placement decision (Outputs vs .tmp)

Classify BEFORE writing, by intent. First match wins:

1. **User-requested deliverable** -- a file the user asked for and will take
   away: report, document, analysis result, chart, export. -> `Outputs/`
2. **Working artifact** -- anything needed only to produce the deliverable:
   scripts, intermediate data, debug output, drafts still being iterated. -> `.tmp/`
3. **Unsure?** -> `.tmp/` (default; promotion later is always possible, demotion pollutes the deliverable folder)

Anchor: expired `.tmp/` entries (30-day default threshold) appear in the
audit's read-only inventory proposal - the plugin never auto-cleans.
If you would miss this file when the proposal lists it, it belongs in `Outputs/`.

Extension hints (intent wins over extension):
- Deliverable-like: `.md` report, `.pdf`, `.docx`, `.xlsx`, `.png`/`.svg` result
- Scratch-like: `.log`, `.pyc`, debug dumps, temp copies, intermediate `.csv`/`.json`

Subagent: no placement decision -- write to the parent-passed directory
(default `.tmp/`; `Outputs/` only when the parent passes it). See the
Subagent File Protocol below.

### 4. Confirmation Protocol

Applies to delete / overwrite / move. **Instruction is not confirmation.**

1. Agent lists the exact files and asks "Confirm? (yes/no)"
2. User replies "yes"/"confirm"/"go ahead" → execute; anything else → abort

### 5. When blocked

Reply with the [Reason]/[Next] template:

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will create a Session Directory and write there:
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/ or .tmp/ subdirectory.
```

Subagent variant: replace "I will create..." with "I will write to the target directory passed by the parent agent."

### 6. Examples

- **Wrong:** writing `<working_dir>/report.md` directly → blocked by the guard
- **Correct:** `python scripts/create_session_dir.py report --workspace <working_dir>`, then write the deliverable to `Outputs/report.md` (or scratch to `.tmp/`)

## Subagent File Protocol

- Parent ensures the target directory exists before delegating (lazy creation is the parent's job)
- Subagents write to the parent's `.tmp/` (default) or an explicit `Outputs/`/per-task subdirectory
- Subagents never create session directories or promote outputs (`.tmp/` → `Outputs/` is the parent's review step); missing target or blocked write → report back to the parent
- `dir_whip_allow_path` is not available to subagents; exemptions are granted by the user via the main agent -> report back so the parent can ask the user

## Terminal Write Discipline

Layer 1 applies to terminal writes. Guard intercepts redirects (`>` `>>`), `touch`, `cp`/`mv` destinations, and resolvable `mkdir` / `curl -o` / `wget -O` targets; uncertain intent is allowed + logged.

1. Prefer Session Directories for all writes
2. One session directory per conversation - a second creation attempt is blocked (session-dir limit)
3. User specifies a path -> call `dir_whip_allow_path(path)` first (two-step: briefing -> user approval -> `confirm=true`) BEFORE writing. Value domain: paths INSIDE the Working Directory only - paths outside need NO entry (writes there are allowed and logged)
4. Blocked → create a Session Directory and re-target (never bypass the guard)

## Governance & Cron

Triggered by "tidy workspace" or cron job:

1. Run: `python scripts/audit_workspace.py --workspace <working_dir>` (add `--json`)
2. Violations? Classify → propose → execute with confirmation
3. No violations? Report "OK" (or `[SILENT]` in cron mode)

Cron: `script: scripts/audit_workspace.py --gate` + skill `dir-whip:workspace-organization`. Gate emits `{"wakeAgent": false, "violations": 0}` on compliance, `{"wakeAgent": true, "violations": N}` on violations (exactly two keys); gate failure exits 2 with no wakeAgent (an unresolved Working Directory is a gate failure); interactive resolution failure fails open to CWD. Zero auto-delete: the plugin never deletes; the interactive audit lists expired `.tmp/` entries as a read-only proposal ("Expired .tmp entries (proposal only; cleanup needs your confirmation):"). See `references/workspace-audit.md` for the full checklist.

## Scripts

All scripts: Python 3.11, `--help` support, forward-slash output paths.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| create_session_dir.py | Create session dir with Outputs/ + .tmp/ | `--workspace` |
| audit_workspace.py | Compliance audit + wakeAgent gate line (audit-only, zero delete) | `--workspace`, `--json`, `--gate`, `--days` |

Boundary: `--workspace` must match the resolved root (exit 2 on mismatch); resolution failure fails open to CWD with one warning.

## Pitfalls

| Problem | Cause | Fix |
|---------|-------|-----|
| Root write blocked | No session dir created yet | Run create_session_dir.py, re-target |
| Deliverable in `.tmp/` | Placement not classified | User-requested files → `Outputs/` |
| Session dir created at conversation start | Misunderstood lazy creation | Create at first file write only |
| Deleted without confirmation | Instruction treated as confirmation | List files, wait for explicit yes |
| Existing repos outside workspace | Relocation attempted | Point via rules file, don't relocate |
| First write without session dir (漏触发) | Skill not triggered before first write | Before first file write, you MUST create session dir: `python scripts/create_session_dir.py <task> --workspace <root>` |

## Verification

- Classified the target before every write?
- Created session dir before first write?
- File inside a session dir, in the correct `Outputs/`/`.tmp/`?
- No non-whitelist files at the Working Directory root? (root allows only
  `allowlist` `files` entries, session-format dirs, and `allowlist` `dirs`
  subtrees; a leftover `.hermes/` directory is flagged by the audit)
- Confirmation obtained before delete/overwrite/move?

## Remember

Classify before write → session dir for all writes → root forbid → when blocked, create a session dir and retry.
