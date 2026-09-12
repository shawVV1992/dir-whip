---
name: workspace-organization
description: "Use when creating, saving, writing, moving, or deleting files, organizing deliverables, designing workspace layout, auditing workspace compliance, or locating and reusing files from past sessions. Enforces session directory discipline and two-step confirmation for destructive operations."
author: dir-whip
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [files, workspace, session-directory, organization, audit, terminal]
    requires_toolsets: [terminal, file]
---

# Workspace Organization

File placement discipline for Hermes agent workspaces: session directory structure, the Inputs/.tmp/Outputs placement pipeline, root-forbid rule, and governance workflows.

## When to Use

Use when:
- Before first file write in a Hermes workspace - Session Directory first: reuse the conversation's, or create one when none exists
- Creating, saving, writing, moving, or deleting files in a Hermes workspace
- Organizing deliverables or designing workspace layout
- Locating or reusing files from past sessions (cross-session metadata search)
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
IF a Session Directory exists for this conversation
  -> DEFAULT MODE. Reuse it: write to its Outputs/ (deliverables), .tmp/ (scratch) or Inputs/ (introduced files), then proceed to Layer 1.
OTHERWISE
  -> DEFAULT MODE. Create a Session Directory first: python scripts/create_session_dir.py <task> --workspace <root>, then proceed to Layer 1.
```

## Quick Reference

| Scenario | Action |
|----------|--------|
| Writing any file | Classify target -> reuse the conversation's Session Directory (create only when none exists) -> write to `Outputs/`, `.tmp/` or `Inputs/` per the placement decision |
| Reusing a file from a past session | Run `python scripts/search_workspace.py --task <substr> --name <glob> --workspace <root>`; copy the found file into this session's `Inputs/` |
| Root write blocked | Reuse the conversation's Session Directory, re-target there (create only when none exists) |
| Delete / overwrite / move | Confirmation Protocol (list files -> wait for explicit yes) |
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

One session directory per conversation: a conversation that already has a
Session Directory REUSES it; never create a second one for the same
conversation (the guard blocks a second creation attempt).

1. Am I inside a valid Session Directory? YES -> proceed with the operation.
2. NO -> does THIS CONVERSATION already have a Session Directory? Judge by
   conversation identity, NOT by the current task or the current location.
   - YES -> REUSE it: write into its `Outputs/`, `.tmp/` and `Inputs/`
   - NO -> create it first (lazy creation at first file write, NOT at
     conversation start):
     `python scripts/create_session_dir.py <task> --workspace <root>`
     Example: `python scripts/create_session_dir.py auth-refactor --workspace E:/ws` (replace <task> and <root> with your values)
     then write to `Outputs/`, `.tmp/` or `Inputs/` within it
3. Every session dir contains `Outputs/` (deliverables), `.tmp/` (scratch) and `Inputs/` (introduced files). A legacy session dir without `Inputs/` stays valid - create it on the first introduced file.
- Root allows ONLY: `allowlist` `files` entries, session-format dirs (`allowlist` `dirs` subtrees likewise exempt)
- Project directories inside the workspace can be exempted wholesale via an
  `allowlist` `dirs` entry (recursive subtree) - add via `/dir-whip allow <path>`
- `Outputs/` blacklist: `__pycache__/`, `*.pyc`, `node_modules/`, `.DS_Store`, `Thumbs.db`

### 3. File placement decision (Inputs, .tmp, Outputs)

Classify BEFORE writing, by pipeline stage. First match wins:

1. **Introduced file** -- anything entering from OUTSIDE this session:
   copied from a past session, downloaded, or user-provided. -> `Inputs/`
   (strict: land here FIRST even when consumed immediately; the original
   stays put; a processed result is a NEW file in `Outputs/`)
2. **User-requested deliverable** -- a file the user asked for and will take
   away: report, document, analysis result, chart, export. -> `Outputs/`
3. **Working artifact** -- anything needed only to produce the deliverable:
   scripts, intermediate data, debug output, drafts still being iterated. -> `.tmp/`
4. **Unsure?** -> `.tmp/` (default; promotion later is always possible, demotion pollutes the deliverable folder)

Anchor: placement follows the pipeline intake -> processing -> delivery
(`Inputs/` -> `.tmp/` -> `Outputs/`); classify by stage, never by file type.
Expired `.tmp/` entries (30-day default threshold) appear in the audit's
read-only inventory proposal - the plugin never auto-cleans.
If you would miss this file when the proposal lists it, it belongs in `Outputs/`.

Extension hints (intent wins over extension):
- Deliverable-like: `.md` report, `.pdf`, `.docx`, `.xlsx`, `.png`/`.svg` result
- Scratch-like: `.log`, `.pyc`, debug dumps, temp copies, intermediate `.csv`/`.json`

Subagent: no placement decision -- write to the parent-passed directory
(default `.tmp/`; `Outputs/` or `Inputs/` only when the parent passes it).
See the Subagent File Protocol below.

### 4. Reusing past artifacts

Locate -> Take -> Process -> Deliver:

1. **Locate**: `python scripts/search_workspace.py --task <substr> --name <glob> [--since YYYYMMDD] [--until YYYYMMDD] --workspace <root>`
   - metadata-only search across every session directory, newest first; results are absolute paths, ready to use
   - extract query terms from the user's words: time -> `--since`/`--until`, task -> `--task`, file feature -> `--name`
   - no results? widen the range or change keywords - do NOT hand-roll `ls`/`glob`
2. **Take**: copy the located file into THIS session's `Inputs/` (it is an
   introduced file now; the original stays in its own session)
3. **Process**: work in `.tmp/` as needed
4. **Deliver**: the result is a NEW file in this session's `Outputs/`

### 5. Confirmation Protocol

Applies to delete / overwrite / move. **Instruction is not confirmation.**

1. Agent lists the exact files and asks "Confirm? (yes/no)"
2. User replies "yes"/"confirm"/"go ahead" -> execute; anything else -> abort

### 6. When blocked

Reply with the [Reason]/[Next] template:

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will write into the conversation's Session Directory (reuse it if it already exists; create one only when none does):
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/, .tmp/ or Inputs/ subdirectory.
```

Subagent variant: replace "I will create..." with "I will write to the target directory passed by the parent agent."

### 7. Examples

- **Wrong:** writing `<working_dir>/report.md` directly -> blocked by the guard
- **Correct:** `python scripts/create_session_dir.py report --workspace <working_dir>`, then write the deliverable to `Outputs/report.md` (scratch to `.tmp/`, introduced files to `Inputs/`)

## Subagent File Protocol

- Parent ensures the target directory exists before delegating (lazy creation is the parent's job)
- Subagents write to the parent's `.tmp/` (default) or an explicit `Outputs/`, `Inputs/`/per-task subdirectory
- Subagents never create session directories or promote outputs (`.tmp/` -> `Outputs/` is the parent's review step); missing target or blocked write -> report back to the parent
- `dir_whip_allow_path` is not available to subagents; exemptions are granted by the user via the main agent -> report back so the parent can ask the user

## Terminal Write Discipline

Layer 1 applies to terminal writes. Guard intercepts redirects (`>` `>>`), `touch`, `cp`/`mv` destinations, and resolvable `mkdir` / `curl -o` / `wget -O` targets; uncertain intent is allowed + logged.

1. Prefer Session Directories for all writes
2. One session directory per conversation - a second creation attempt is blocked (session-dir limit)
3. User specifies a path -> call `dir_whip_allow_path(path)` first (two-step: briefing -> user approval -> `confirm=true`) BEFORE writing. Value domain: paths INSIDE the Working Directory only - paths outside need NO entry (writes there are allowed and logged)
4. Blocked -> re-target into the conversation's Session Directory (reuse it; create one only when none exists; never bypass the guard)

## Governance & Cron

Triggered by "tidy workspace" or cron job:

1. Run: `python scripts/audit_workspace.py --workspace <working_dir>` (add `--json`)
2. Violations? Classify -> propose -> execute with confirmation
3. No violations? Report "OK" (or `[SILENT]` in cron mode)

Cron: `script: scripts/audit_workspace.py --gate` + skill `dir-whip:workspace-organization`. Gate emits `{"wakeAgent": false, "violations": 0}` on compliance, `{"wakeAgent": true, "violations": N}` on violations (exactly two keys); gate failure exits 2 with no wakeAgent (an unresolved Working Directory is a gate failure); interactive resolution failure fails open to CWD. Zero auto-delete: the plugin never deletes; the interactive audit lists expired `.tmp/` entries as a read-only proposal ("Expired .tmp entries (proposal only; cleanup needs your confirmation):"). See `references/workspace-audit.md` for the full checklist.

## Scripts

All scripts: Python 3.11, `--help` support, forward-slash output paths.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| create_session_dir.py | Create session dir with Outputs/, .tmp/ + Inputs/ | `--workspace` |
| audit_workspace.py | Compliance audit + wakeAgent gate line (audit-only, zero delete) | `--workspace`, `--json`, `--gate`, `--days` |
| search_workspace.py | Cross-session metadata search over session dirs (newest first, stateless) | `--task`, `--name`, `--since`, `--until`, `--limit`, `--workspace`, `--json` |

Boundary: `--workspace` must match the resolved root (exit 2 on mismatch); resolution failure fails open to CWD with one warning.

## Pitfalls

| Problem | Cause | Fix |
|---------|-------|-----|
| Root write blocked | No session dir available | Reuse the conversation's Session Directory (create only when none exists), re-target |
| Deliverable in `.tmp/` | Placement not classified | User-requested files -> `Outputs/` |
| Session dir created at conversation start | Misunderstood lazy creation | Create at first file write only |
| Second Session Directory in one conversation | Reuse rule missed | Reuse the existing Session Directory; a second creation is blocked |
| Lazy creation misread as per-task | Task-domain judgment | Judge by conversation, not by task; a new task reuses the same dir |
| Existing Session Directory ignored | New task treated as a new conversation | Reuse it: write to its `Outputs/` or `.tmp/` |
| Deleted without confirmation | Instruction treated as confirmation | List files, wait for explicit yes |
| Existing repos outside workspace | Relocation attempted | Point via rules file, don't relocate |
| First write without session dir (missed trigger) | Skill not triggered before first write | Before first file write, reuse the conversation's Session Directory or create one: `python scripts/create_session_dir.py <task> --workspace <root>` |
| Hand-rolled ls/glob for past-session files | Search surface missed | Use `search_workspace.py` (metadata search, newest first) |
| Introduced file mixed into `.tmp/` or `Outputs/` | Placement not classified | Copied/downloaded/user-provided files -> `Inputs/` |

## Verification

- Classified the target before every write?
- Reused the conversation's existing Session Directory (no second dir created)?
- Judged reuse by conversation, not by the current task or location?
- File inside a session dir, in the correct `Outputs/`/`.tmp/`/`Inputs/`?
- Introduced files placed in `Inputs/` (not mixed with scratch or deliverables)?
- Past-session files located via `search_workspace.py` (not hand-rolled `ls`/`glob`)?
- No non-whitelist files at the Working Directory root? (root allows only
  `allowlist` `files` entries, session-format dirs, and `allowlist` `dirs`
  subtrees; a leftover `.hermes/` directory is flagged by the audit)
- Confirmation obtained before delete/overwrite/move?

## Remember

Classify before write (introduced -> `Inputs/`, scratch -> `.tmp/`, deliverable -> `Outputs/`) -> session dir for all writes -> root forbid -> when blocked, reuse the conversation's session dir (create one only when none exists) and retry.
