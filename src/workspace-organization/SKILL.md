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
├── <rules-file>                       <- workspace rules file (root whitelist)
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
- Root allows ONLY: the workspace rules file (its name comes from the
  allowed_root_files whitelist) and .hermes/ (directory)
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
| init_workspace.py | Create new profile workspace dir (mkdir only; no rules file, no memo write; output ends with the registration next-step) | `<name>`, `--workspace` |

Boundary validation: all scripts (except init_workspace.py) validate the
target against the profile workspace memo (see "Profile Workspace Memo") --
exact match of a profile's workspace value. An unregistered target is rejected
(exit 2), preventing accidental operations outside a valid Default Working
Directory.

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

Two-step init flow (see "Profile Workspace Memo"):
1. Create the directory (mkdir only):
   `python scripts/init_workspace.py <name> --workspace <parent>`
2. Register it in the target profile's own session: call the tool
   `workspace_guard_register_workspace('<name>', '<created path>')` (sets that
   profile's `terminal.cwd` and writes the memo entry)
3. Create sessions: `python scripts/create_session_dir.py <task> --workspace <dir>`

---

## Multi-Profile Support

This skill is loaded by every profile. Your workspace is your `terminal.cwd`.
Never assume a hardcoded workspace path.

Key principles:
1. One subdirectory per profile under a shared root
2. A workspace rules file at each level (top-level for navigation, per-profile
   for rules); the root file name comes from the allowed_root_files whitelist
3. No cross-profile file pollution
4. Security isolation (redact_pii/redact_secrets profiles stay in their own tree)
5. Existing repos don't move -- use a workspace rules file to point at
   existing paths

### Setting terminal.cwd

```bash
hermes config set terminal.cwd "<workspace-path>"
```

Pitfalls:
- `hermes config set cwd "path"` (without `terminal.` prefix) sets an
  unrecognized top-level key. Only `terminal.cwd` controls the working directory.
- Do not edit config.yaml directly -- Hermes blocks writes to config files.
- Changes take effect on the NEXT session, not the current one.
- After changing `terminal.cwd`, run `/workspace-guard workspace_update`
  (plugin installed) so the profile workspace memo reflects the new value --
  scripts validate against the memo.

---

## Profile Workspace Memo

Scripts validate a working directory against the profile workspace memo
(`HERMES_HOME/workspace-guard/profile-workspaces.json`): a target is a valid
Default Working Directory iff it exactly matches a profile's recorded workspace
value -- that profile's `terminal.cwd`. The memo stores `synced_at` plus, per
profile, `workspace`, `status`, and `changed_at`.

Write ownership: the PLUGIN is the memo's only writer. The skill and its
scripts only READ the memo -- they never rebuild or edit it.

Quick commands:

```
/workspace-guard workspace_status
```

Read-only display of the memo: `synced_at` plus each profile's workspace,
status, and `changed_at`.

```
/workspace-guard workspace_update
```

Manual rebuild (user-triggered): re-derives the memo from each profile's
`terminal.cwd` and returns the full display. Use it after manually changing
`terminal.cwd`.

Tools:

- `workspace_guard_auto_update_workspace` -- automatic memo sync
  (tool/agent-triggered; the same rebuild as the update command).
- `workspace_guard_register_workspace(profile, workspace)` -- registration:
  sets the profile's `terminal.cwd` first (config-first, durable), then writes
  the memo entry. Active-profile-only: it rejects a profile other than the
  current session's profile.

Validation semantics:

- Memo present and the target exactly matches a profile workspace -> pass.
- Target exists but is not registered -> reject (exit 2, "not a registered
  profile workspace"); for a NEW workspace, register it via the
  `workspace_guard_register_workspace` tool.
- Memo missing/corrupt with the plugin installed -> reject (exit 2) and prompt
  to run `/workspace-guard workspace_update`.
- Memo missing/corrupt with NO plugin trace -> standalone mode.

### Standalone Mode

Without the plugin there is no memo to validate against. Every script
invocation then emits one concise stderr warning ("memo unavailable, standalone
mode") and trusts the provided `--workspace`. When you see that warning,
explain standalone mode to the user ONCE per session: the guard is absent, so
discipline is enforced by this skill's teaching only. Registration is skipped
in standalone mode (the tool is plugin-owned).

### Two-Step Init Flow

Creating a new profile workspace is two steps:

1. Create the directory (mkdir only -- no rules file, no memo write):
   `python scripts/init_workspace.py <name> --workspace <parent>`. If the
   directory already exists, the script exits 2 without creating or changing
   anything. The output ends with the registration next-step.
2. Register it in the TARGET profile's own session: call
   `workspace_guard_register_workspace('<name>', '<created path>')`.
   Registration is active-profile-only, so switch to the target profile
   first. The tool sets that profile's `terminal.cwd` (config-first) and
   writes the memo entry; the new workspace then passes script validation.

---

## Compliance Audit

To audit and fix violations:

1. Run: `python scripts/audit_workspace.py --workspace <dir>` (add `--json` for machine-readable)
2. See `references/workspace-audit.md` for the 7-point checklist
3. Clean expired temps: `python scripts/clean_tmp.py --workspace <dir>` (dry-run by default)

---

## Path Classification

The plugin guard classifies every file write into one of four outcomes.
Know these so a block message or an approval prompt never surprises you.

### Root Rules File -> Allowed

A write to the workspace rules file at the workspace root is exempted. The
permitted name is config-driven: the `allowed_root_files` whitelist in
`HERMES_HOME/workspace-guard/guard-config.yaml`, read by BOTH the guard and the
audit (same key), so the two never disagree about which root files are allowed.

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
  the workspace rules file instead of relocating them
- Rules from one profile's workspace rules file should not leak into another
  profile's context
- Session directory creation is lazy -- do NOT create one at conversation start
- In project mode, this skill does not apply at all -- defer to project conventions
- Confirmation protocol: listing files is NOT the same as confirming deletion
