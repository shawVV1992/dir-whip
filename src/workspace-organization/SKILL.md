---
name: workspace-organization
description: 'Use when creating, saving, writing, moving, or deleting files, organizing deliverables, designing workspace layout, or auditing workspace compliance. Enforces session directory discipline and two-step confirmation for destructive operations.'
category: productivity
tags: [hermes, workspace, profiles, directory-structure, file-organization, session-directory]
---

# Workspace Organization

File discipline for Hermes agent workspaces. Teaches session directory
structure, file placement rules, and governance workflows.

## When to Use

Any file write, create, save, delete, or move operation within a Default
Working Directory. Also: workspace layout design, compliance audit, temp
file cleanup, and new profile initialization.

---

## Behavior Layers

Evaluate in order. Each layer builds on the previous.

### Layer 0: Scope Check (short-circuit)

Determine whether this skill applies BEFORE doing anything else.

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Stop. This skill does not apply.
IF CWD not under any Default Working Directory
  -> PROJECT MODE. Stop. This skill does not apply.
OTHERWISE
  -> DEFAULT MODE. Proceed to Layer 1.
```

Evaluate in order. First match wins. Do NOT evaluate subsequent conditions.

Examples:
- CWD = `<workspace-root>/default/`, no active project -> default mode
- CWD = `<some-project>/`, active project -> project mode (skill steps back)
- CWD = `<some-project>/`, no active project -> project mode (skill steps back)

### Layer 1: Instant Discipline (every file operation)

Triggered by: any file write, create, save, delete, or move.

```
1. Am I inside a valid Session Directory?
   - YES -> proceed with operation
   - NO  -> create Session Directory first (lazy creation):
            python scripts/create_session_dir.py <task_name> --workspace <working_dir>
            then write to Outputs/ or .tmp/ within it

2. Is this a delete / overwrite / move?
   -> Apply Confirmation Protocol (see below)

3. Execute operation
```

Key rule: Session Directory is created LAZILY at first file write, NOT at
conversation start. Conversations that produce no files create no directories.

File placement:
- Formal deliverables -> `Outputs/`
- Intermediate scripts, debug files, scratch work -> `.tmp/`
- NEVER save to workspace root directly
- NEVER save to Desktop or arbitrary paths

### Layer 2: Governance Mode (on request or cron)

Triggered by:
- User says "tidy workspace" / "organize files" / "audit" / equivalent
- Cron job with attached skill

```
1. Run audit:
   python scripts/audit_workspace.py --workspace <working_dir>

2. If violations found:
   - Classify each (misplaced deliverable / temp file / unknown)
   - Propose action (move to session dir / move to .tmp / leave)
   - Execute with user confirmation
   - In cron mode: auto-clean .tmp/ only, skip ambiguous items

3. If no violations:
   - Interactive: report "OK"
   - Cron mode: output [SILENT] (suppress wakeAgent noise)
```

For cron integration, use `--gate` flag:
```
python scripts/audit_workspace.py --workspace <working_dir> --gate
```
Last stdout line is JSON: `{"wakeAgent": false}` or `{"wakeAgent": true, "violations": N}`.

---

## Confirmation Protocol

Applies to: delete, overwrite, move operations.

**Rule: Instruction is NOT confirmation.**

```
Step 1: Agent reports the operation
  "I will delete .tmp/debug.py (last modified 45 days ago). Proceed?"

Step 2: User explicitly confirms
  "Yes" / "Go ahead" / "Confirm"

Step 3: Agent executes
```

Anti-patterns (NEVER do these):
- User says "clean up old files" -> agent deletes without listing them first
- User says "move X to Y" -> agent moves without showing what will change
- Agent interprets silence as confirmation

For scripts: `clean_tmp.py` requires `--confirm` flag. Without it, only
reports what would be deleted (dry-run is the default).

---

## Session Directory Structure

```
<Default Working Directory>/
├── AGENTS.md                          <- workspace rules (root whitelist)
├── .hermes/                           <- optional Hermes internal
├── YYYYMMDD_HHMMSS_TaskName/          <- session directory
│   ├── Outputs/                       <- formal deliverables only
│   └── .tmp/                          <- intermediate files, safe to clean
├── YYYYMMDD_HHMMSS_AnotherTask/
│   ├── Outputs/
│   └── .tmp/
└── ...
```

Rules:
- Root allows ONLY: AGENTS.md (file) and .hermes/ (directory)
- Every other root entry must match `YYYYMMDD_HHMMSS[_TaskName]` format
- Each session dir MUST contain both Outputs/ and .tmp/
- Outputs/ blacklist: `__pycache__/`, `*.pyc`, `node_modules/`, `.DS_Store`, `Thumbs.db`
- .tmp/ eligible for age-based cleanup (default: 30 days)

---

## Scripts

All scripts: Python 3.11, `--help` support, forward-slash output paths.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| create_session_dir.py | Create session dir with Outputs/ + .tmp/ | `--workspace` |
| audit_workspace.py | 7-point compliance audit (read-only) | `--workspace`, `--json`, `--gate` |
| clean_tmp.py | Remove expired .tmp/ contents | `--workspace`, `--days N`, `--confirm` |
| init_workspace.py | Initialize new profile workspace | `--workspace`, `--template` |

Boundary validation: all scripts (except init_workspace.py) verify the target
directory contains AGENTS.md before proceeding. This prevents accidental
operations outside a valid Default Working Directory.

---

## Workspace Design Methodology

When a user asks to design or restructure a workspace:

### Phase 1: Propose Top-Level Structure

Start with just the top-level layout. Propose, don't decree.

```
<workspace-root>/
├── default/
├── learn/
└── shared/
```

### Phase 2: Handle Objections

| Objection | Likely cause | Pivot to |
|-----------|-------------|----------|
| "Too rigid" | Pre-categorized by type | Task/session-based model |
| "Hard to find" | Lifecycle model mixes tasks | Session-based (one dir per conversation) |
| "Not relevant" | Applied rules from another project | Strip context, start fresh |

### Phase 3: Settle on Session-Based Organization

One directory per conversation. Timestamp + task name. Outputs/ for
deliverables, .tmp/ for everything else.

### Phase 4: Shared Space

```
<workspace-root>/shared/    <- admission control: user confirms before placement
```

Agent may ask: "Should this go in shared/ as a cross-profile tool?"
Only place files after explicit user confirmation.

### Phase 5: Implement

1. Initialize: `python scripts/init_workspace.py <name> --workspace <parent>`
2. Create sessions: `python scripts/create_session_dir.py <task> --workspace <dir>`
3. Set terminal.cwd: `hermes config set terminal.cwd "<path>"`

---

## Multi-Profile Support

This skill is loaded by every profile. Your workspace is your `terminal.cwd`.
Never assume a hardcoded workspace path.

Key principles:
1. One subdirectory per profile under a shared root
2. AGENTS.md at each level (top-level for navigation, per-profile for rules)
3. No cross-profile file pollution
4. Security isolation (redact_pii/redact_secrets profiles stay in their own tree)
5. Existing repos don't move -- use AGENTS.md to point at existing paths

### Setting terminal.cwd

```bash
hermes config set terminal.cwd "<workspace-path>"
```

Pitfalls:
- `hermes config set cwd "path"` (without `terminal.` prefix) sets an
  unrecognized top-level key. Only `terminal.cwd` controls the working directory.
- Do not edit config.yaml directly -- Hermes blocks writes to config files.
- Changes take effect on the NEXT session, not the current one.

---

## Compliance Audit

To audit and fix violations:

1. Run: `python scripts/audit_workspace.py --workspace <dir>` (add `--json` for machine-readable)
2. See `references/workspace-audit.md` for the 7-point checklist
3. Clean expired temps: `python scripts/clean_tmp.py --workspace <dir>` (dry-run by default)

---

## Path Classification

The plugin guard classifies every file write into one of three outcomes.
Know these so a block message or an approval prompt never surprises you.

### External Path -> Allowed

The target is outside the Default Working Directory and not under any other
profile's workspace. The guard treats this as outside its jurisdiction and
lets the write through without a message.

### Cross-Profile Write -> Approval Prompt

The target is under ANOTHER profile's workspace (tracked in the profile
workspace memo), not the current one. The guard raises a human approval
prompt instead of silently allowing or blocking. This usually means the
Hermes Desktop app switched profiles without refreshing the workspace, so
the current conversation is writing into the wrong profile's tree.

When this prompt appears:
- Report it to the user. The prompt shows the target path, the target
  profile, and the active profile.
- Let the user decide. Choosing "always" only approves writes to that
  specific profile's workspace; other profiles still prompt.

### In-Workspace Write Outside a Session Directory -> Blocked

The target is inside the Default Working Directory but not inside a valid
Session Directory. The write is blocked with fix instructions: create a
Session Directory first, then re-target the write there.

---

## Terminal Write Discipline

The guard also intercepts the terminal tool. Writes made through shell
commands are classified exactly like write_file targets, so terminal
commands cannot bypass session directory discipline.

Rules:
1. All writes land in a Session Directory, including writes made via
   terminal. Redirects (`echo x > file`), `touch`, `cp`, `mv`, `curl -o`,
   `wget -O`, `tee`, and similar commands are all subject to the same rule.
2. When the USER explicitly specifies a path in conversation, call the
   `workspace_guard_allow_path` tool with that path FIRST, then perform
   the write.
3. When a write is blocked, create a Session Directory
   (`python scripts/create_session_dir.py <task_name> --workspace <working_dir>`)
   and re-target the write there. Do NOT try to bypass the guard.

---

## Pitfalls

- Don't assume project file rules from one context apply to general workspace design
- When existing code repos are outside the workspace root, add instructions in
  AGENTS.md instead of relocating them
- AGENTS.md rules for one profile should not leak into another profile's context
- Session directory creation is lazy -- do NOT create one at conversation start
- In project mode, this skill does not apply at all -- defer to project conventions
- Confirmation protocol: listing files is NOT the same as confirming deletion
