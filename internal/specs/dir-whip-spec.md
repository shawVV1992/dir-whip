# dir-whip — Complete Specification

- Version: 2.11
- Date: 2026-08-30 (SCR-043 amendment v2.11 — classification chain T0-T4 reorder + runtime-allowlist value-domain gating + .hermes quarantine relocation + gate zero-auto-delete; previous freeze v2.10 2026-08-29)
- Status: FROZEN (v2.11, activated 2026-08-30, re-frozen 2026-08-30 at the implementation-complete state 43.9) — SCR-043 amendment v2.11 (scr-043-plan.md, feedback/14 design finalization 2026-08-30; version ruling v0.6.3 patch): (R1) classification chain T0-T4 — SCOPE FIRST: outside-root (excluding the root itself) is ALWAYS `external-write` before any exemption is consulted, so the outside-root signal never depends on gating correctness; under-root priority = runtime allowlist (T1) > config allowlist (T2; dual rule_keys `tier0-allowlist` / `allowed-file` kept distinct) > session dir (T3) > BLOCK (T4, including the root itself — `rel == "."` blocks as root-file without consulting the `files` list); (R2) external-write log/event routing sinks to the emit layer as a geometric test (normalize_target + within_working_dir; the outcome string stays as fallback) so any future allow tier cannot bypass the outside-root signal; (R3) registration gating completes the registrable value domain to the strict root subtree — outside-root rejection (R2c, self-explaining verbatim + stats rule_key `allow-path-external-rejected`, bus-skipped; 7 emits unchanged), empty-path rejection at the add layer (a normalized empty string prefix-matched EVERYTHING), and `runtime_allowlist_add` gains an optional `working_dir_root` assertion injected by the caller (config.py never imports verdict, ADR-0007); (R4) runtime allowlist matching becomes SEGMENT-BOUNDARY (`target == entry` or `target.startswith(entry.rstrip("/") + "/")` — `docs` no longer exempts `docs_secret`; pathlib is_relative_to rejected to keep one lexical basis); (R5) the audit quarantine relocates out of the workspace root to `<dir-whip home>/audit-quarantine/<ts>/` (profile-aware, beside dir-whip-config.yaml/stats.jsonl); the L1/nudge remediation message paths updated; the audit `.hermes` root-dir exemption is removed (the guard adds none — three-way consistency); `/dir-whip list` gains a quarantine-path line; (R6) `--gate` returns to PURE audit + wake: the `.tmp` deletion execution and `delete=args.gate` wiring are removed, the wakeAgent payload drops to two keys always ({wakeAgent, violations} — removed/failed retired, breaking for strict cron consumers), the SCR-042 H1 unresolved-root exit 2 stays as cron failure VISIBILITY, the R1 symlink scan boundary stays as inventory correctness, and the interactive proposal becomes a read-only inventory. 3.4/4.2/5.3/5.11/5.18/7.2/7.3/8.1 updated. All 7 feedback/14 items disposed (no backlog). Version target v0.6.3 (patch per user ruling 2026-08-30 — behavior changes flagged in the release notes; hooks 9 / emits 7 unchanged). Historical: SCR-042 v2.10 (activated 2026-08-29, re-frozen 2026-08-29 at implementation-complete state; suite 687 passed / 5 skipped / 0 failed) — SCR-042 amendment v2.10 (scr-042-plan.md, scope ruling 2026-08-29): skill-scripts security hardening per feedback/13 (9 items, 5 requirement groups): (R1) deletion surface — --gate mode treats a Working Directory resolution failure as a gate failure (exit 2, reason on stderr, NO wakeAgent line, NOTHING deleted; an unresolved root never becomes a deletion target via the fail-open CWD fallback; interactive fail-open unchanged), and the .tmp cleanup scan boundary excludes symlinked session dirs and symlinked .tmp directories (two layers); (R2) import hardening — both scripts load the bundled workspace_resolver via importlib from the __file__-derived absolute path with sys.modules registration (CWD/PYTHONPATH shadowing can no longer divert the shared module); (R3) case parity — audit root-file allowlist matching reuses the resolver's Windows-casefold matcher (parity with allowlist.py by construction, ADR-0006), Outputs blacklist comparisons case-insensitive, .hermes root-dir exemption casefolded on Windows (mirrors report.py _case_eq); (R4) gate output contract — the wakeAgent line carries four keys always ({wakeAgent, violations, removed, failed}; cleanup failures visible to cron) and gate+json stdout is line-by-line JSON; (R5) robustness smalls — stdout/stderr reconfigure(errors=replace) in all three scripts (cp936 non-ASCII paths no longer crash), create_session_dir abspaths the resolved root (absolute-path contract), hermes home falls back to the user home when LOCALAPPDATA is unset/empty (never CWD-relative). Version target v0.6.2 (patch — skill-scripts security hardening; no config surface change; hooks 9 / emits 7 unchanged; the --gate resolution-failure behavior change is noted in the release notes). Historical: SCR-041 v2.9 (activated 2026-08-28, re-frozen 2026-08-29 at implementation-complete state; suite 664 passed / 5 skipped / 0 failed) — SCR-041 amendment (scr-041-plan.md, user decisions 2026-08-28): (1) re-scan semantics tightened (R1) — the L3 settlement re-scan classifies pending paths by the CONFIG allowlist only (allowlist files/dirs + session-dir); a runtime-allowlist entry no longer settles a recorded violation (runtime exemption = prospective-only: future writes pass Tier 0, past violations stay pending); the pre-write sanction flow is unaffected (the audit diff classifies at write time, so a path exempted BEFORE the write never creates a violation); (2) allow_path entry gating (R2) — subagent calls rejected with a parent-guidance variant + stats rule_key allow-path-subagent-rejected (bus-skipped), and the Working Directory root itself is rejected as a path argument (workspace-wide exemptions belong in config allowlist dirs authored by the user); (3) two-step user-confirmation protocol (R3) — dir_whip_allow_path gains an optional confirm parameter: the first call (no confirm) returns a confirmation payload (risk + removal method + relay instruction) WITHOUT adding, and confirm=true is honored only for a path whose confirmation payload was already issued this session (mandatory two-step, session-memory state); (4) L1/L3 message rewording (R4) — the config-allowlist remediation option is re-attributed to the USER (ask the user to add) with the latch-period freeze made explicit (all writes frozen incl. config edits), removing the false affordance that cost two gate-blocked turns in realhost. Version target v0.6.1 (patch — enforcement hardening: no config surface change, confirm is a new optional parameter). Historical: SCR-040 v2.8 (activated 2026-08-27: continuation-nudge rework + observability pack + config/report surface rework incl. three-key de-configuration BREAKING; re-frozen 2026-08-28 after the implementation batch). Historical: SCR-039 v2.7 (activated 2026-08-26: prompt-channel rework + same-turn self-heal + structured allowlist; re-frozen 2026-08-27 at implementation-complete state). Earlier activations: 2026-08-22 — SCR-033 terminal false-positive fix (v2.1),
  SCR-034 root write audit feature (v2.2), SCR-034 acceptance clauses 7.6 (v2.3), all
  re-frozen after completion. Changes go through the SCR process again (spec-changes.md).
  (SCR-024 root), superseding the v1.4 baseline. v0.2.0 implemented and
  verified 2026-08-14 (acceptance matrix all Done, testing-standards.md;
  live phases 27.1-27.6 with WSL POSIX coverage; SCR-025 registered — native
  install pending an upstream hermes update; SCR-032 verification-scope
  decision applied). Earlier activation passes: 2026-08-22 (SCR-033 terminal
  false-positive fix, v2.1; SCR-034 root write audit new feature + 7.6
  acceptance criteria, v2.2/v2.3), each re-frozen on completion. Changes
  re-enter the SCR process (spec-changes.md).
- Change policy: Living document (approach C). Changes recorded via git commit messages.

---

## 1. Overview

### 1.1 Problem

Hermes agents do not reliably follow file discipline rules. Files scatter across
the Working Directory root, deliverables become unfindable, and the user cannot
locate outputs from specific conversations. Root causes:

1. Skill not triggered (agent never loads workspace-organization)
2. Agent self-exempts ("this conversation doesn't need a session dir")
3. Attention dilution in long contexts (forgets the rule)
4. Model hallucination (misinterprets the task)

### 1.2 Solution

Two complementary layers, distributed as ONE artifact (the plugin package):

| Layer | Role | Form |
|-------|------|------|
| Skill | Teach the agent what to do | Bundled SKILL.md registered via `ctx.register_skill()` (opt-in load) + always-on discipline prompt (≤400 chars, English) |
| Plugin | Prevent the agent from not doing it | Hermes plugin (pre_tool_call guard + observation hooks) |

The skill ships inside the plugin package: installing the plugin installs the
skill, the scripts, and the config template. The layers keep zero runtime
coupling (the guard never calls the skill; the skill never calls the guard).

### 1.3 Scope (v0.2.0)

- **Single-profile simplification**: cross-profile write interception removed;
  memo chain (sync / registration / incremental check) removed; Shared Space
  concept removed; Standalone Mode concept removed.
- **Terminal write interception simplified**: redirects (`>` `>>` `1>` `2>`),
  `touch`, `cp`/`mv` destination only; deep intent parsing (python / node / sed /
  tee / curl / wget / dd) removed; uncertain write intent -> allow + log (no
  approval gate).
- **Config**: dir-whip-config.yaml is the sole config source; `working_dir_root`
  semantics inverted (explicit value wins, fallback = current profile
  `terminal.cwd`); `TERMINAL_CWD` chain removed.
- **Skill bundling**: `workspace-organization` moves into the plugin package
  (`dir-whip/skills/workspace-organization/`), registered via
  `ctx.register_skill()`; always-on discipline prompt (≤200 chars) injected via
  `register_system_prompt_section()`.
- **Root write discipline**: the Working Directory root allows exactly the
  whitelist files + session-format directories + `.hermes/`; other root writes
  are blocked with replacement commands; creation workflow examples and a
  standard interception response template added to the skill.
- **Scripts**: `create_session_dir.py` + `audit_workspace.py` retained
  (audit keeps `--gate` / cron); `clean_tmp.py` + `init_workspace.py` removed;
  `--workspace` equality validation vs the resolved Working Directory;
  fail-open degradation.
- **Cron governance retained**: audit `--gate` / wakeAgent / `[SILENT]` and the
  skill cron chapter stay (reversal of draft item A4).
- **Observability**: structured single-line event logging; interception
  statistics (outcome × tool × rule_key, subagent split); stats.jsonl
  persistence (5MB rollover, privacy-trimmed); the `/dir-whip`
  merged report (no subcommands, SCR-029); `dir-whip:*` events on the
  inter-plugin event bus (silent degradation when the bus is absent);
  `pre_command` observation hook (observer-only).
- **Subagents**: prompt-layer discipline (write to parent session `.tmp/`, no
  self-created session dirs) + observation dimensions (subagent_start record,
  stats split, diagnostics filter).
- **Distribution**: native install only (`hermes plugins install
  shawVV1992/dir-whip/dir-whip --enable`); multi-profile installer removed;
  plugin manifest v2; no plugin pack; community plugin index BLOCKED (pending
  the official upstream repo).
- **Terminology**: "Default Working Directory" -> "Working Directory" on all
  user-visible surfaces + glossary + spec; code identifiers unchanged.
- **Cross-platform**: Windows 10+, Linux, WSL, macOS — installable and
  runnable on all four; scripts tested on the full platform matrix (WSL
  treated as standard Linux; Windows-specific code paths — MSYS mapping /
  casefold / ntpath — already branch on platform, SCR-006).
- **Event bus**: first-class capability; `dir-whip:*` events activate
  automatically on a Hermes version that ships the inter-plugin event bus;
  silent degradation on versions without it (no reconfiguration needed).

### 1.4 Out of Scope (Phase 2)

- Pi Coding Agent extension adapter
- Universal workspace management plugin (separate project)
- Community plugin index submission (BLOCKED — the official
  hermes-plugin-index repository does not exist yet; re-evaluate when it lands)
- Blocking `pre_command` interception (upstream middleware #64204/#64231;
  Phase 2 reservation — the v0.2.0 hook is observer-only)

---

## 2. Domain Model

### 2.1 Terminology

| Term | Definition |
|------|-----------|
| Working Directory | The profile-level working directory configured via `terminal.cwd` in Hermes config. Formerly "Default Working Directory" (renamed v0.2.0 to match Hermes desktop settings). This is the root that dir-whip protects. |
| Session Directory | A directory on disk named `YYYYMMDD_HHMMSS[_TaskName]/` produced by one Hermes session. Contains exactly `Outputs/` and `.tmp/`. Derived from Hermes "session" concept. |
| Outputs/ | Subdirectory of a Session Directory holding formal deliverables. |
| .tmp/ | Subdirectory of a Session Directory holding intermediate files. Eligible for age-based cleanup (audit cron mode). |
| Confirmation Protocol | Two-step rule: instruction is not confirmation. Destructive operations (delete, overwrite, move) require explicit user confirmation after the agent reports what it will do. |
| Governance Mode | Workflow triggered by user request ("tidy workspace") or cron job. Audits, classifies, proposes corrective actions, and executes with user confirmation (cron mode auto-cleans .tmp). Automated by `audit_workspace.py --gate`. |
| File Operation Guard | The plugin's pre_tool_call hook that classifies write targets (write_file / patch / terminal) and blocks operations targeting non-whitelist paths at the Working Directory root outside valid Session Directories. External paths are allowed and logged. |
| Allowlisted Dirs | Parsed `dirs` subset of `allowlist` (relative paths under working_dir_root) — directory subtrees inside the Working Directory that are not subject to guard enforcement; recursive. Not a separate config key (the single key is `allowlist`). Legacy v2.6 `prefix:` entries are ignored (clean break). |
| Runtime Allowlist | Session-scoped in-memory set of user-specified paths (injected via the `dir_whip_allow_path` tool), cleared at every session start; merged with allowlist `dirs` entries at T1 of the guard chain (v2.11). Entries are exempt for the current session only. |
| Subagent | A child AIAgent spawned by the parent agent (delegate_task). Runs in the same process and inherits the parent's toolset, so pre_tool_call covers its writes. By discipline its files land in the parent session's Session Directory `.tmp/`. |
| Discipline Block | The session-start discipline message injected once per top-level Working-Directory session via `ctx.inject_message()` (v2.7; replaces the removed always-on Discipline Prompt). Carries the core discipline; the full interception response template is delivered by the guard's block message. |

### 2.2 Hermes Terminology Mapping

| Our term | Hermes equivalent | Notes |
|----------|-------------------|-------|
| Working Directory | `terminal.cwd` / "Working dir:" (CLI) / "Working Directory" (desktop settings) | Hermes "workspace" means kanban task workspace — avoid |
| Session Directory | (no direct equivalent) | Disk directory derived from a Hermes session. NOT the same as Hermes session records — session-librarian manages those (no functional overlap) |
| file path | `path` (write_file/patch parameter) | Not `file_path` |
| profile | `ctx.profile_name` | Plugin API |
| Hermes session | `session_id` / `task_id` | A conversation, not a directory |

### 2.3 Relationships

```
Hermes Profile (1) ─── terminal.cwd ───> Working Directory (1)
Hermes Session (1) ─── produces ───────> Session Directory (1)
Session Directory (1) ─── contains ────> Outputs/ (1) + .tmp/ (1)
File Operation Guard ─── enforces ─────> Working Directory root + Session Directory boundary
Subagent (n) ─── writes into ──────────> parent Session Directory .tmp/ (discipline)
Discipline Prompt ─── guides ──────────> write classification before every create
Confirmation Protocol ─── governs ─────> delete / overwrite / move operations
```

---

## 3. Skill Specification

### 3.1 Trigger and Loading (C1)

The skill is bundled inside the plugin package
(`dir-whip/skills/workspace-organization/`) and registered at plugin
`register()` via `ctx.register_skill()`.

- Installed with the plugin — no separate `hermes skills install` step.
- **Opt-in loading** (upstream semantics): the skill is NOT listed in
  `<available_skills>`; the agent loads it explicitly by its QUALIFIED name
  `dir-whip:workspace-organization` (resolvable via `skill_view()`,
  which routes `plugin:name` names to the plugin skill registry) when deep
  reference is needed.
- The Discipline Prompt (3.7) carries the day-to-day discipline; the skill is
  the deep reference (workflows, examples, audit checklist).
- SKILL.md `description` constraints unchanged: ≤1024 characters, trigger
  words within the first 57 characters. Wording must avoid "organize/clean up
  sessions" phrasing (session-librarian separation, F4).
- The SKILL.md frontmatter `version` field is REMOVED: plugin.yaml `version`
  is the sole version for the whole artifact (bundled content follows the
  plugin version; nothing compares a separate skill version anymore).

Proposed description (first 57 chars bolded):

> **Use when creating, saving, writing, moving, or deleting files,** organizing deliverables, designing workspace layout, or auditing workspace compliance. Enforces session directory discipline and two-step confirmation for destructive operations.

### 3.2 Behavior Layers (Q5)

The skill operates in three layers, evaluated in order:

#### Layer 0: Scope Check (short-circuit)

```
IF project_list tool available AND active_id not null AND CWD under project folders
  -> PROJECT MODE. Skill exits. Stop.
IF CWD not under the current profile's Working Directory
  -> PROJECT MODE. Skill exits. Stop.
OTHERWISE
  -> DEFAULT MODE. Proceed.
```

#### Layer 1: Instant Discipline (every file operation)

Triggered by: any file write, create, save, delete, or move.

**Step 1 — classify the target before writing (C3).** The agent MUST state the
target class explicitly before every create/write. The classification is a
behavioral requirement (the guard cannot mechanically verify the statement —
it enforces by path location):

| Target classification | Guard behavior |
|---|---|
| Inside a Session Directory (`working_dir_root/YYYYMMDD_HHMMSS.../...`) | Allow |
| Root allowlisted file (`allowlist` `files` entry) | Allow |
| External path (outside the Working Directory, incl. sibling profile dirs) | Allow + logged (fail-open) |
| Working Directory root, non-allowlist (anything else at root) | Block |

**Step 2 — session directory discipline:**

```
1. Check: am I inside a valid Session Directory?
   - YES -> proceed with operation
   - NO  -> create Session Directory first (lazy creation)
            run: python scripts/create_session_dir.py <task_name> --workspace <working_dir>
            then write to Outputs/ or .tmp/ within it

2. If operation is delete / overwrite / move:
   -> Apply Confirmation Protocol (section 3.3)

3. Execute operation
```

Key rule: Session Directory is created lazily at first file write, NOT at
conversation start. Conversations that produce no files create no directories.

**Step 3 — root forbid (C4).** The Working Directory root allows EXACTLY:
`allowlist` `files` entries (root files), allowlisted `dirs` subtrees,
session-format directories
(`YYYYMMDD_HHMMSS_TaskName/`) and their contents, and `.hermes/`. Every other
creation at the root is strongly forbidden — use a Session Directory instead.

#### Layer 2: Governance Mode (on request or cron)

Triggered by:
- User says "tidy workspace" / "organize files" / equivalent
- Cron job with attached skill

```
1. Run audit: python scripts/audit_workspace.py --workspace <working_dir>
2. If violations found:
   - Classify each violation (misplaced deliverable / temp file / unknown)
   - Propose action (move to session dir / move to .tmp / leave)
   - Execute with user confirmation (or auto in cron mode for .tmp cleanup)
3. If no violations: report "OK" (or [SILENT] in cron mode)
```

### 3.3 Confirmation Protocol

Applies to: delete, overwrite, move operations.

Rule: **Instruction is not confirmation.**

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

### 3.4 Cron Governance Mode (retained, Q4)

Designed for Hermes cron with hybrid pattern (unchanged from v1.4):

```
Cron job configuration:
  script: scripts/audit_workspace.py --gate (pre-run gate, zero tokens)
  skill: dir-whip:workspace-organization   # qualified plugin-skill name;
                                                  # resolved via skill_view()
  prompt: "If audit found violations, classify and archive misplaced files.
           If no violations, respond with [SILENT]."

Flow:
  1. script= runs audit_workspace.py --gate
  2. stdout "OK" -> {"wakeAgent": false} -> silent tick, no delivery
  3. stdout violations -> {"wakeAgent": true} -> agent wakes
  4. Agent classifies violations, moves files to appropriate session dirs
  5. Agent reports summary (delivered to configured platform)
```

Gate contract (v2.11, SCR-043 R6): `--gate` is a PURE audit + wake — the
embedded `.tmp` auto-cleanup is removed and the plugin has NO automatic
deletion path anywhere. The last stdout line is the two-key wakeAgent
payload `{"wakeAgent": false|"true", "violations": N}` (4.2). Gate failure:
when the `--workspace` mismatch check fails, OR when the Working Directory
resolution fails in gate mode (SCR-042 H1; kept in v0.6.3 as cron failure
VISIBILITY — with deletion gone it no longer serves deletion safety), the
audit exits 2 with the reason on stderr and emits NO wakeAgent line — the
cron tick fails visibly and the agent is NOT woken. Interactive (non-gate)
runs keep the fail-open chain (4.4): fallback to CWD with one stderr
WARNING and proceed; expired `.tmp` entries are listed as a READ-ONLY
proposal (cleanup is agent-decided and confirmation-gated — "the agent
organizes, the plugin guards").

### 3.5 Terminal Write Discipline (simplified, A1/A2)

Layer 1 applies equally to writes performed through the `terminal` tool. The
guard only coarsely intercepts: redirects (`>` `>>` `1>` `2>`), `touch`, and
`cp`/`mv` destinations. Deep intent parsing (python / node / sed / tee /
curl / wget / dd) is removed; uncertain write intent is allowed and logged (no
approval gate). The agent must:

1. Prefer Session Directories for all file writes, including via terminal.
2. When the user explicitly specifies a target path in the conversation
   (e.g. "write to C:/Users/me/Reports/R1.md"), call the
   `dir_whip_allow_path(path)` tool to register that path before writing,
   so the guard's exemption tiers (T1/T2) allow it.
3. When a write is blocked by the guard, create a Session Directory
   (`python scripts/create_session_dir.py <task_name> --workspace <working_dir>`)
   and re-target — never bypass the guard.

### 3.6 Guarded Path Classification (C3/Q7)

The agent should understand the plugin's classification so it can respond to
block / allow outcomes correctly and state the classification truthfully:

- Paths inside a Session Directory or matching allowlist `dirs` / runtime-allowlisted
  paths: allowed.
- Root allowlist files (`allowlist` `file:` entries): allowed.
- Paths at the Working Directory root that are not allowlist file entries: blocked —
  create a Session Directory.
- Paths outside the Working Directory (including sibling profile
  directories): allowed + logged (external; no cross-profile interception).
- Uncertain terminal write intent: allowed + logged.

### 3.7 Session-start Discipline Block (v2.7 — replaces the Always-on Discipline Prompt)

The Always-on Discipline Prompt (`register_system_prompt_section`, billed every
round) is REMOVED in v2.7. Teaching is carried by two channels instead:

1. **Session-start discipline block** — injected ONCE per top-level session via
   `ctx.inject_message()`, CONDITIONALLY (only when the session lives inside
   the Working Directory; mechanics and fail-open matrix in 5.4). Locked text
   (verbatim; `len <= 280` chars, ~78 tokens, test-locked by character count):

```
[dir-whip] Active. WD writes need a session dir first: python scripts/create_session_dir.py <task> --workspace <root> (write the deliverable to Outputs/<filename>, or scratch to .tmp/<filename>). Root forbidden. User path -> dir_whip_allow_path first.
```

   Elements dropped vs the old prompt, each with a deterministic downstream
   teacher: classify-before-write framing (SKILL.md on load + block messages),
   external classification (guard auto allow+log), `.hermes/` root exception
   (block message), `[Reason]/[Next]` template pointer (block message carries
   the full template), timestamp format (create_session_dir.py enforces it).
2. **Block-message completion** — the guard's block message now carries the
   placement-intent rule (deliverable to Outputs/<filename>, scratch to
   .tmp/<filename>) and the
   `dir_whip_allow_path` hint (5.3), so every interception is a complete
   teaching point.

No always-on pointer micro-prompt is used (user decision 2026-08-26): the
guard's block message is the deterministic re-teaching point.

The full C6 template (3.9) is delivered by the plugin's block message, not by
the block.

### 3.8 Creation Workflow Examples (C5)

Negative example (wrong):
- User asks "save the report"; the agent writes directly to
  `working_dir_root/report.md` — root write, not whitelisted -> blocked.

Positive example (correct):
- The agent classifies the target as a session-dir write; runs
  `create_session_dir.py`; writes the deliverable to `Outputs/report.md` (or
  scratch to `.tmp/`).

### 3.9 Interception Response Template (C6)

When a write is blocked, the agent replies with this template, aligned with the
guard's block message:

```
[Reason] The target <path> is not allowed: <rule reason>.
[Next] I will create a Session Directory and write there:
  python scripts/create_session_dir.py <task_name> --workspace <working_dir>
  then write to its Outputs/ or .tmp/ subdirectory.
```

Subagent variant (Q1): a blocked subagent replies "[Reason] ... [Next] I will
write to the target directory passed by the parent agent" and reports the
block back to the parent (it never creates a session directory itself).

### 3.10 Removed Content (C7)

Pruned from the skill: multi-profile teaching, memo concepts, cross-profile
classification. KEPT: the cron governance chapter, confirmation protocol,
audit chapter. The skill teaches nothing about memo or cross-profile
workflows.

---

## 4. Script Specifications

Retained scripts: `create_session_dir.py`, `audit_workspace.py`, and the shared
`workspace_resolver.py` (cross-import exception, unchanged role — now the
shared READ-ONLY Working Directory resolution module). Removed:
`clean_tmp.py` (.tmp age-based cleanup now runs inside audit cron mode) and
`init_workspace.py` (a profile's Working Directory is configured by the user —
Hermes desktop settings / config.yaml `terminal.cwd`; no creation or
registration flow exists anymore).

Scripts remain self-contained (high cohesion, low coupling). No shared Python
module — EXCEPT `workspace_resolver.py`. Each script independently validates
its inputs.

### 4.1 create_session_dir.py

Boundary validation (B3): the `--workspace` target must EXACTLY EQUAL the
resolved Working Directory (see 4.4: dir-whip-config override ->
HERMES_SESSION_PROFILE -> profile enumeration + TERMINAL_CWD candidate root ->
fail-open). When `--workspace` is omitted, the script defaults to CWD and
applies the 4.4 containment matching (CWD under a candidate root -> that
root). Resolution failure is fail-open: the script falls back to the provided
`--workspace` (or CWD default) and emits ONE concise stderr WARNING, then
proceeds.

Interface:
```
python create_session_dir.py [task_name] --workspace <path>

Exit codes:
  0 = created successfully
  1 = parameter error (workspace directory does not exist; this check
      runs BEFORE boundary validation)
  2 = target already exists OR --workspace does not equal the resolved
      Working Directory
```

Output (stdout, R9): exactly two lines on success (exit 0) and on the
already-exists branch of exit 2 — line 1 is the absolute session-dir path
(forward slashes), line 2 is the placement hint:
`Write the deliverable to Outputs/<filename>, scratch to .tmp/<filename>.`
All other failure paths (exit 1; exit-2 boundary mismatch) stay silent on
stdout (errors go to stderr).

### 4.2 audit_workspace.py

Boundary validation: same equality/containment semantics vs the resolved
Working Directory (4.4); mismatch -> exit 2. Root-file allowlist stays
config-driven: `allowlist` `files` entries read from dir-whip-config.yaml
(default empty lists, shipped in the config template v2.7; missing key ->
same strict empty allowlist, over-reporting, fail-closed; the v2.6 flat
tagged format and the pre-v2.6 keys are removed, no backward compat).
Root-file allowlist matching uses the same Windows-casefold semantics as
the plugin guard — the audit reuses the resolver's allowlist matcher, so
guard and audit never disagree on case variants (ADR-0006 parity,
SCR-042 R3). The `.tmp` inventory scan boundary excludes symlinked session
dirs and symlinked `.tmp` directories (real directories only; the SCR-042
R1 deletion-surface hardening is retained as INVENTORY correctness —
nothing is deleted). The audit NEVER deletes (SCR-043 R6): expired `.tmp`
entries are listed as a read-only proposal in interactive runs.
`.hermes` is no longer exempt from the root-directory format check
(SCR-043 R5 — the audit quarantine lives outside the workspace root now,
so a root-level `.hermes/` is residue and is flagged like any
non-session directory).

`--gate` retained (Q4) for cron wakeAgent integration:
- Last stdout line is JSON with two keys always present:
  `{"wakeAgent": false, "violations": 0}` (no violations) or
  `{"wakeAgent": true, "violations": N}` (violations found); the
  `removed`/`failed` keys are REMOVED together with the deletion execution
  (SCR-043 R6 — breaking for strict cron consumers, release-note flagged)
- With `--json`, every stdout line is a valid JSON document (the violations
  array line + the wakeAgent line)
- Regular output (plain or --json) still printed before the gate line
- Gate error (boundary mismatch, or resolution failure in gate mode):
  exit 2, reason on stderr, NO wakeAgent line, nothing deleted

Interface:
```
python audit_workspace.py [--workspace <path>] [--json] [--gate]

Exit codes:
  0 = compliant
  1 = violations found
  2 = parameter/path error OR --workspace mismatch
```

### 4.3 Removed Scripts

- `clean_tmp.py` — removed. Age-based `.tmp` cleanup is embedded in the audit
  cron mode (Governance Mode step 2).
- `init_workspace.py` — removed. Workspaces are configured by the user via
  Hermes desktop settings / config.yaml `terminal.cwd`; there is no
  create-then-register flow anymore.

### 4.4 Common: Working Directory Resolution (workspace_resolver.py)

`workspace_resolver.py` is the shared READ-ONLY module (unchanged
cross-import exception role) that resolves the current profile's Working
Directory. Standalone scripts have NO Hermes runtime context
(`ctx.profile_name` is plugin-only), so the resolver locates the profile with
a layered chain. It cannot import hermes_cli (self-containment) and carries
its own minimal `terminal.cwd` parser for config.yaml (same fallback approach
as the plugin's `_parse_terminal_cwd_fallback`):

```
1. dir-whip-config.yaml working_dir_root (explicit) — authoritative when set
2. HERMES_SESSION_PROFILE env var (Hermes injects the session's profile name
   into terminal-subprocess environments) -> parse
   HERMES_HOME/(config.yaml for "default" | profiles/<name>/config.yaml)
   terminal.cwd -> that root
3. Profile enumeration + path matching (fallback when step 2 is unavailable,
   e.g. pure local CLI):
   candidate roots R = {every profile's terminal.cwd} + {TERMINAL_CWD if set}
   - TERMINAL_CWD is Hermes' runtime carrier for terminal.cwd (bridged at
     gateway/cron startup). It participates as a candidate root ONLY — it is
     not a config-source step (Q6/ADR-0003 keep it out of the plugin chain).
4. fail-open: None + one WARNING — callers fall back to CWD / provided
   --workspace
```

Hermes home resolution (`hermes_home`): the `HERMES_HOME` env var wins;
otherwise Windows `LOCALAPPDATA/hermes` with a user-home fallback when
LOCALAPPDATA is unset or empty (the home is never resolved relative to the
CWD), POSIX `~/.hermes` (SCR-042 N7).

Matching semantics:

```
Explicit --workspace: normalized equality against R — match -> proceed;
                      no match -> exit 2 (boundary failure)
Default (CWD):        CWD equals a candidate root            -> that root
                      CWD contained in exactly one root      -> that root
                      contained in several roots (nested)    -> longest match
                      no containment                         -> fail-open
                      (CWD + one concise stderr WARNING)
```

Validation flow per script:

```python
def validate_workspace(path):
    """Check that path equals the resolved Working Directory."""
    if not os.path.isdir(path):
        return False, "directory does not exist"
    root = resolve_working_dir_root()   # chain 1-4 above
    if root is None:
        warn("[dir-whip] Working Directory unresolved, using the "
             "provided --workspace")    # one concise stderr line
        return True, None               # fail-open fallback
    if normalize_path(path) == normalize_path(root):
        return True, None
    return False, "--workspace does not match the resolved Working Directory"
```

Exit-code mapping: callers check directory existence FIRST (parameter error,
exit 1 in create_session_dir) and boundary validation SECOND (exit 2).

### 4.5 Naming Alignment

v0.2.0 includes the SCR-030 project rename workspace-guard → dir-whip (code
identifiers updated accordingly: plugin package/key, slash command, tool,
event namespace, skill qualified name, module `guard.py` → `dir_whip.py`,
config `guard-config.yaml` → `dir-whip-config.yaml`, runtime dir, logger).
The `--workspace` flag remains unchanged. Terminology decision G still
governs display-layer terms. Intentional exclusions from the rename (do
not sweep): `stats.jsonl`, the `config.py` module name, the
`workspace-organization` skill name, guard-judgment identifiers (e.g.
`load_guard_config`), and historical surfaces (archive, feedback, change
registers). Forward note (SCR-035, v0.4.0): the
`dir_whip.py` module named here dissolves into the 11-module layout of
section 5.1; `config.py` persists as a module name.

---

## 5. Plugin Specification

### 5.1 Directory Structure

v0.4.0 layout (SCR-035; the two-file `dir_whip.py` + `config.py` layout of
v0.3.x is dissolved into 11 cohesive modules with one-way dependencies and
no cycles):

```
<repo-root>/
├── README.md / README-zh.md        # user-facing docs (EN/ZH)
├── LICENSE
├── dir-whip/                # plugin package (plugin.yaml at package root)
│   ├── plugin.yaml                 # manifest v2
│   ├── __init__.py                 # register(ctx) + hook adapters; the fail-open
│   │                               #   try/except converges HERE (single layer)
│   ├── verdict.py                   # guard chain: guard / classify_target / block message
│   ├── terminal.py                 # command lexer + coarse tiers (pure functions)
│   ├── paths.py                    # normalization / resolution / containment (pure functions)
│   ├── events.py                   # verdict emission deep module: emit(outcome, tool,
│   │                               #   rule_key, target, reason, session_id, is_subagent);
│   │                               #   root/profile from state; stats+log+bus fanout internal
│   ├── audit.py                    # write audit: snapshot/diff/pending/L1-L3 (state via state.py)
│   ├── sessions.py                 # child-session tracking + audit parent links
│   ├── state.py                    # ALL mutable runtime state: session/audit/stats containers,
│   │                               #   locks travel with their group, reset_all()
│   ├── config.py                   # config loading (single YAML parser) + working_dir_root
│   │                               #   resolution chain + runtime allowlist
│   ├── stats.py                    # counters + jsonl + rollover (state container lives in state.py)
│   ├── report.py                   # /dir-whip report rendering
│   ├── dir-whip-config.yaml        # shipped config template (runtime config lives
│   │                               #   in HERMES_HOME/dir-whip/, SCR-013)
│   ├── after-install.md            # post-install message
│   └── skills/workspace-organization/   # bundled skill (register_skill)
│       ├── SKILL.md
│       ├── references/
│       │   └── workspace-audit.md
│       └── scripts/
│           ├── workspace_resolver.py    # shared Working Directory resolution
│           ├── create_session_dir.py
│           └── audit_workspace.py
```

Dependency direction (one-way, no cycles): `__init__ → guard/audit/sessions/
events → terminal/paths → config/stats/state`. Core modules import no host
APIs — host capabilities enter only via `__init__.py` injection. The
resolution chain stays dual-implementation (plugin `config.py` in-process /
skill `workspace_resolver.py` subprocess), locked by parity contract tests.

Repository root has no installer script (removed, A6). The skill package is
not a standalone directory (bundled into the plugin, Q9).

### 5.2 plugin.yaml (plugin manifest, F3)

```yaml
name: dir-whip
version: <package version; sole version source is this file>
description: Enforce Working Directory file discipline in Hermes
api_version: <current Hermes plugin api_version>
author: shawVV1992
license: MIT
provides_hooks:
  - pre_tool_call
  - on_session_start
  - post_tool_call
  - transform_tool_result   # SCR-034 write-audit L1 fire-once notice
  - post_approval_response
  - subagent_start
  - subagent_stop
  - pre_command            # observer-only (v1); interception pending upstream
emits:
  - dir-whip:blocked
  - dir-whip:external-write
  - dir-whip:allowlisted
  - dir-whip:approval-requested
  - dir-whip:approval-resolved
  - dir-whip:write-audit-violation      # SCR-034
  - dir-whip:write-audit-gate-block     # SCR-034
```

`manifest_version` is deliberately OMITTED (e6e2148; user decision
2026-08-22 to keep it deleted, not restored): the local hermes CLI v0.20.1
installer gates `manifest_version <= 1` and rejects a declared v2, while the
runtime loader accepts the plugin without the field. Native
`hermes plugins install` distribution is tracked separately (SCR-025).

`api_version` follows the installed Hermes version and must be an INTEGER
(manifest parsing ignores non-integer values with a warning). No
`capabilities:` declaration is needed: register_skill / hooks / tools / emit
are NOT capability-gated (the capability registry covers only
override/platform-action surfaces), so the install consent flow does not
apply to dir-whip.

### 5.3 pre_tool_call Guard Logic

Callback signature:
```python
def guard(tool_name: str, args: dict, task_id: str, **kwargs):
```

Intercepted tools: `write_file`, `patch`, `terminal`
Passed through: all other tools (read-only tools unaffected)

Unified judgment chain (shared by all intercepted tools):

```
1. tool_name not in ("write_file", "patch", "terminal") -> return None (allow)

2. Guard-disabled shortcut: if working_dir_root is None,
   inject a one-time fail-open warning and return None (allow).
   This MUST run before path extraction/classification.

3. Extract target path(s):
   - write_file: args["path"]
   - patch (mode=replace): args["path"]
   - patch (mode=patch): parse file paths from args["patch"] content
     (V4A format: "*** Update File: <path>" lines)
   - terminal: parse args["command"] for write targets (see 5.10)

4. Resolve each target to absolute:
   - write_file/patch: relative to get_session_cwd(task_id) (the actual
     session CWD). If get_session_cwd returns None (unrecorded), fall back
     to working_dir_root (conservative) and log at DEBUG.
   - terminal: relative to args["workdir"], fallback get_session_cwd(task_id),
     then working_dir_root (never os.getcwd(), which is the plugin process CWD)

5. Normalize each target via normalize_target() (SCR-006 rules unchanged):
   - Windows: MSYS-style paths (/c/..., //c/..., /cygdrive/c/...) map to
     drive-letter paths; rooted-no-drive paths inherit the drive of
     working_dir_root; then normpath. UNC paths unaffected.
   - POSIX: normpath only (identity).
   - Paths still unclassifiable after normalization fail open (allow) with
     a warning log.

6. For each target, classify via classify_target() — the T0-T4 chain
   (v2.11, SCR-043 R1; scope is a PREMISE, not a rung: outside-root has
   exactly one exit — allow + log — regardless of any exemption; under-root
   priority is session-authorization T1 > persistent config T2 >
   session structure T3):

   T0  target outside working_dir_root (the root itself is NOT outside;
       incl. sibling profile directories)                -> ALLOW + LOG
                                                          (external-write)
   T1  runtime allowlist hit (value domain = strict subtree of the root,
       5.11 v2.11 gating)                                 -> ALLOW
                                                          (runtime-allowlist)
   T2  config allowlist hit — allowlist `dirs` subtree OR root-level
       `files` entry (rule_keys tier0-allowlist / allowed-file kept
       distinct)                                          -> ALLOW
   T3  inside a valid Session Directory                   -> ALLOW
                                                          (session-dir)
   T4  everything else, INCLUDING the root itself (rel == "." blocks as
       root-file without consulting the `files` list)     -> BLOCK + fix
       guidance (root-file / non-session-dir)

   The external-write log/event routing is computed in the emit layer as a
   geometric test (normalize_target + within_working_dir; the outcome
   string remains the fallback) so any FUTURE allow tier cannot bypass the
   outside-root signal (SCR-043 R2).

7. Aggregate multi-target result (strictest wins):
   - any BLOCK            -> return block
   - else                 -> return None (allow)
```

There is NO approve tier anymore (Q1: cross-profile interception removed;
A2: uncertain terminal intent is allowed + logged). Every verdict emits a
structured single-line log event (5.13) and updates the statistics counters.

Block result (v2.7: placement-intent rule + allow_path hint added, R3;
project-dir hint switched to relative `dirs` syntax):
```python
{
  "action": "block",
  "message": (
    "BLOCKED: File writes in the Working Directory require a Session "
    "Directory or an allowed root file.\n"
    f"Target: {target_path}\n"
    "Fix: Create a session directory first:\n"
    "  python <scripts_path>/create_session_dir.py <task_name> "
    "--workspace <working_dir_root>\n"
    "Then write the deliverable to Outputs/<filename> (or scratch to .tmp/<filename>).\n"
    "User-specified path -> dir_whip_allow_path first.\n"
    "If this is a project directory, add it to the allowlist dirs in "
    "HERMES_HOME/dir-whip/dir-whip-config.yaml (relative to the Working "
    "Directory root, e.g. projects/foo)\n"
    "Reply using the [Reason]/[Next] template."
  )
}
```

Subagent variant (Q1): when the call originates from a child session
(session_id in the `child_session_ids` set, 5.4), the fix instruction is
replaced by "write to the target directory passed by the parent agent" — no
create_session_dir guidance (subagents never create session directories).
The subagent variant does NOT gain the placement-intent / allow_path lines
(subagents make no placement decisions; protocol is write-where-parent-says).

### 5.4 on_session_start Hook

Callback signature:
```python
def on_start(session_id: str, model: str, platform: str, **kwargs):
```

Behavior: clear the runtime allowlist (session-scoped, SCR-010 behavior
retained), reset the fail-open warning flag, re-resolve working_dir_root from
this session's profile (SCR-027), then CONDITIONALLY inject the discipline
block via `ctx.inject_message()`:

```
[dir-whip] Active. WD writes need a session dir first: python scripts/create_session_dir.py <task> --workspace <root> (write the deliverable to Outputs/<filename>, or scratch to .tmp/<filename>). Root forbidden. User path -> dir_whip_allow_path first.
```

**Conditional injection (v2.7).** The block is injected only when the session
lives inside the Working Directory:

- Session CWD source: the host's agent-CWD accessor, wired at register()
  through the `agent_cwd_fn` injection slot (ADR-0007 pattern; host source =
  `agent.runtime_cwd.resolve_agent_cwd()`: session contextvar override ->
  TERMINAL_CWD bridge -> process CWD).
- Predicate: `verdict.discipline_applies(cwd, working_dir_root)` — a pure,
  None-safe function returning True unless positively known otherwise; inside
  it reuses `paths.within_working_dir` (equality = inside; Windows drive-rooted
  casefold rules on any host, SCR-006; different drive = outside).
- Injection matrix: cwd inside root -> inject; cwd outside root / different
  drive -> skip (debug log); cwd unavailable or predicate error -> inject
  (fail-open = current behavior); working_dir_root unresolved -> inject (the
  one-time fail-open warning path at first guarded write is unchanged, 5.12);
  inject_message unavailable -> debug log skip (CLI/TUI, unchanged).
- **Project-mode exemption (v2.7 R7).** BEFORE the predicate above, an ACTIVE
  host project exempts the session entirely: the plugin probes the host's
  projects.db via the `project_active_fn` injection slot (assembly-layer
  try-import of `hermes_cli.projects_db`; `connect_closing()` ->
  `get_active_id(conn)` -> `project_folders` paths; any failure -> None =
  no exemption, fail-open). When an active project exists AND the agent CWD
  falls under any of its folders (same containment semantics as
  `discipline_applies`), the reminder is skipped with status
  `skipped-project` — this takes precedence over `skipped-outside` (project
  mode has its own layout; spec 3.2 Layer 0 mirrors it on the skill side).
- Outcome recorded in `state.session.reminder_status` (`injected` |
  `skipped-outside` | `skipped-child` | `skipped-project` | `unavailable`)
  and surfaced by the
  `/dir-whip` report (5.7). Semantics (2026-08-26 ruling): single
  process-shared field, LAST-TOP-LEVEL-WRITER wins — the report shows the
  most recent top-level session's outcome (correct by construction in
  single-session CLI; documented limitation in desktop multi-session
  processes; no per-session map).

The allowlist clear applies to TOP-LEVEL sessions only — VERIFIED against the
installed hermes-agent source: child sessions DO fire on_session_start (the
child runs the same conversation loop, which fires the hook unconditionally on
the child's first turn; subagent_start is fired BEFORE the child's loop
starts). Defense: the plugin keeps a `child_session_ids` set (populated by the
subagent_start hook, removed by subagent_stop); the on_session_start callback
skips the allowlist clear, the fail-open flag reset, AND the discipline-block
injection when session_id is in the set — all three are top-level-session
only (the runtime allowlist is process-shared with the parent).

`ctx.inject_message()` is only available in CLI mode; in gateway mode it
returns False and the plugin records a debug log line (acceptable — the guard
still enforces via pre_tool_call).

### 5.5 working_dir_root Resolution (inverted, B2/Q6)

```python
def resolve_working_dir_root(ctx) -> Optional[str]:
    # 1. dir-whip-config.yaml explicit value (authoritative when set)
    try:
        cfg = load_guard_config()
        if cfg.get("working_dir_root"):
            return cfg["working_dir_root"]
    except Exception:
        pass

    # 2. current profile's terminal.cwd (fallback)
    try:
        profile = ctx.profile_name  # "default" / "learn" / "job-hunt"
        if profile == "default":
            config_path = HERMES_HOME / "config.yaml"
        else:
            config_path = HERMES_HOME / "profiles" / profile / "config.yaml"
        cwd = parse_terminal_cwd(config_path)
        if cwd:
            return cwd
    except Exception:
        pass

    # 3. Fail-open: guard disabled
    logger.warning("dir-whip: cannot resolve working_dir_root, guard disabled")
    return None
```

- The `TERMINAL_CWD` environment-variable chain is REMOVED.
- Priority semantics (inverted vs v1.4): the dir-whip-config.yaml
  `working_dir_root` value is authoritative when set; the profile's
  `terminal.cwd` is the fallback. The desktop-settings edit is reflected only
  when no override is configured — `/dir-whip` shows the
  effective value AND its source in its report so a stale override is
  visible (Q6).
- Resolution happens once at register() time, cached via `lazy_singleton`.
  If None, all guard checks return None (allow everything) AND a one-time
  fail-open warning is injected into the session (5.12).
- Visibility: on successful resolution the plugin logs at INFO level which
  source resolved the value — `dir-whip-config` or `profile-config` (format:
  "dir-whip: working_dir_root resolved from <source>: <value>").
  The failure WARNING is retained.

### 5.6 Configuration (dir-whip-config.yaml — the sole config source, B2)

File: `<HERMES_HOME>/dir-whip/dir-whip-config.yaml` (same location as
v1.4, SCR-013: outside the plugin dir so forced reinstalls do not wipe it)

```yaml
# dir-whip configuration (user-managed, lives outside the plugin dir)
# Structured allowlist (v2.7, BREAKING): entries are RELATIVE to
# working_dir_root; nothing outside the root (or the root itself) can be
# allowlisted.

allowlist:
  files: []   # root-level file basenames, e.g. ["README.md", "notes.txt"]
  dirs: []    # relative dir paths under the root, recursive subtree
              # exemption, e.g. ["projects/foo"] -- multi-level allowed;
              # no "..", no absolute/drive forms, not "." (the root itself)

# Optional override: working_dir_root (authoritative when set; fallback =
# current profile's terminal.cwd)
# working_dir_root: E:/HermesWorkspace/learn
```

**v2.8 BREAKING — three-key de-configuration.** `terminal_guard` /
`write_audit` / `write_audit_entry_cap` are no longer user configuration keys
(supported through v2.7): the parser stops reading them and behavior becomes
internally constant — terminal write interception always on (5.10), the root
write audit always on (5.18), the audit entry guardrail an internal constant
of 2000. The `write_audit_autofix` reservation key (L4 auto-move) is removed
too (5.18 L4 becomes a keyless reserved direction). Leftover occurrences of
these keys in runtime configs are **completely ignored** (no hint line, no
log entry). Upgrade notes in deployment.md.

Matching (v2.7 structured mapping): `files` entries are basenames allowed at
the Working Directory root (exact basename match, case-insensitive on Windows).
`dirs` entries are paths RELATIVE to working_dir_root; a target is exempt when
it equals or is under `<working_dir_root>/<dirs entry>` — a recursive subtree
exemption (forward-slash normalized; case-insensitive on Windows via casefold,
SCR-006). Multi-level relative paths are allowed (`projects/foo`). Storage is
ALWAYS root-relative; absolute input is input-layer tolerance only.

**Input normalization and add-time layering (2026-08-26 ruling — allow input
layer v2.1).** `/dir-whip allow` accepts index numbers, relative paths, or
absolute paths (backslash -> forward slash, MSYS/Cygwin forms mapped via the
shared normalizer, SCR-006; comparisons casefolded on Windows):

1. Absolute input is relativized against working_dir_root; a result that
   resolves OUTSIDE the root (rel starts with `..`, equals `.` — root itself
   or an ancestor) is rejected with the guided message
   `[dir-whip] Invalid path: choose a file or folder inside the Working
   Directory (<root>).` plus a reason clause (`'<input>' resolves outside
   it`).
2. Existing paths (casefold-aware) classify by disk state: directory ->
   `dirs` entry, file -> `files` entry (disk-aware; a bare name naming an
   existing directory still goes to `dirs`).
3. NON-EXISTENT paths follow the symmetric confirm-create protocol (same rule
   for bare names and dirs, 2026-08-26):
   - without `--create`: guided message with the exact follow-up command —
     `'<input>' does not exist -- run: /dir-whip allow <input> --create`;
   - with `--create`, the created artifact is decided by form: trailing
     slash -> `os.makedirs(<root>/<rel>, parents=True)` + `dirs` entry; bare
     name -> empty file created at the root + `files` entry (unlocks
     agent root-file writes the user deems root-appropriate); nested path
     without trailing slash -> directory tree (nested files cannot be `files`
     entries — basename-only storage — so nested implies directory intent).
     `--create` on an existing path is a no-op (existence decides first).
4. Validation at load time: `..` segments, absolute/drive forms, empty
   values, and `.` (root itself) in stored entries are ignored (hand-edited
   configs fail-closed, guard and audit agree); an entry resolving outside
   the root is ignored at load time.

Total entries across both keys <= 100 — the cap is
enforced at ADD time only (hand-edited configs beyond the cap are trusted
at load; 2026-08-26 ruling). config_writer edits each key as a single
flow-style line (`files: ["a", "b"]`, whole-line replacement, key comments
preserved) — block-style `- item` lists are not produced. Edge case
(documentation only): a `files` entry naming an existing DIRECTORY on disk is
harmless (basename match applies to files only; no extra validation).
Symlinks/junctions are matched literally (no realpath resolution) — consistent
with the guard's classify behavior.

**Clean break (v2.7)**: the flat tagged-list format of v2.6 (`file:<name>` /
`prefix:<abs-path>` strings) is REMOVED with no backward compat (user decision
2026-08-26, following the B2 precedent). A legacy flat value under `allowlist`
is ignored fail-closed (guard and audit agree) and `/dir-whip list` reports it
as ignored legacy entries so the migration is visible. Missing `allowlist`
key -> strict empty allowlist (fail-closed, guard and audit agree).

The runtime allowlist (5.11) is merged with the parsed `dirs` subset in
the classification chain (T1; v2.11 — after the T0 scope premise).

### 5.7 Commands and Tools (Q12) — SCR-029 / SCR-037 v2.6 / SCR-039 v2.7

Registered at `register()`:

- `/dir-whip` — merged report + allowlist management. SCR-029 removed the
  `status`/`stats`/`doctor` subcommands (statistics backend 5.13 unchanged);
  v2.7 unifies `allow`/`remove`/`list` on ONE presentation: a Files/Ddirs
  two-section numbered listing (config_writer, row-level YAML edit preserving
  comments). Bare `/dir-whip` renders the report below.
  - `/dir-whip allow` lists numbered candidates from the Working Directory
    root — `Files:` section (top-level files, excluding already-allowlisted
    files / prefix-covered subtrees / session dirs) then `Dirs:` section
    (top-level directories, excluding session-format dirs and subtrees already
    covered by a `dirs` entry) — one continuous numbering across both
    sections.
  - `/dir-whip allow <args>` adds entries; comma/whitespace batch (`1,3`)
    supported; numbers map into the candidate list (file number -> `files`
    entry, dir number -> `dirs` entry). Name arguments accept relative or
    ABSOLUTE paths (input-layer tolerance; normalized + relativized, 5.6):
    existing path -> disk-aware (`dirs` for a directory, `files` for a
    file); non-existent path -> confirm-create protocol (R3 below).
  - `/dir-whip remove` lists numbered CURRENT entries in the same two-section
    format (`Files:` then `Dirs:`); numbers map to removal. Name arguments
    accept relative or absolute paths (normalized; matched against both
    sets, 5.6 rules).
  - `/dir-whip list` renders the current allowlist in the same two-section
    numbered format (numbers align with `remove`), plus an ignored-legacy
    hint line when a legacy flat value was ignored (v2.6 format, clean break),
    plus a quarantine-path line showing the current audit quarantine
    directory (v2.11, SCR-043 R5 — discoverability after the relocation out
    of the workspace root).
  - Any other argument renders `Usage: /dir-whip [allow|remove|list]`.

**Interaction flow (v2.7, 2026-08-26 ruling — R1-R8):**

- **R1 Numbering**: `Files:` and `Dirs:` sections share ONE continuous integer
  sequence (section headers are visual grouping only; digit tokens never need
  section context).
- **R2 Bare `allow` (candidate enumeration)**:
  ```
  Candidates in <root>:
  Files:
    1: notes.txt
  Dirs:
    2: projects
  Add: /dir-whip allow <number|name>
  ```
  Files section = top-level files minus already-listed `files` entries; Dirs
  section = top-level directories minus session-format directories
  (`YYYYMMDD_HHMMSS.../`), minus `.hermes/`, minus subtrees already covered by
  a `dirs` entry; empty sections render `(none)`.
- **R3 `allow <args>`**: digit tokens map into the candidate list (file
  number -> `files`, dir number -> `dirs`); comma/whitespace batch with
  all-or-nothing semantics (first invalid token rejects the whole call).
  Path tokens (relative or absolute, 5.6 input layering):
  - existing path -> disk-aware classification (`dirs` / `files`);
  - NON-EXISTENT path -> the symmetric confirm-create protocol (same rule
    for bare names and dirs, 2026-08-26): without `--create` the call
    returns the guided message `'<input>' does not exist -- run: /dir-whip
    allow <input> --create` (nothing added); with `--create` the artifact is
    decided by form — trailing slash -> `makedirs` + `dirs` entry, bare name
    -> empty root file + `files` entry, nested no-slash -> directory tree +
    `dirs` entry; `--create` on an existing path is a no-op;
  - outside-root / root-itself / ancestor -> guided rejection
    `[dir-whip] Invalid path: choose a file or folder inside the Working
    Directory (<root>).` + reason clause.
  Feedback names the target set per entry
  (`Added to files: X` / `Added to dirs: X`); duplicates idempotent
  (`Already in files: X`); a final two-section current-state line follows.
- **R4 Bare `remove`** (upgraded from the v0.4.1 Usage hint): enumerates the
  CURRENT entries in the same two-section numbered format plus
  `Remove: /dir-whip remove <number|name>`; `(strict empty allowlist)` kept
  when the key is missing.
- **R5 `remove <args>` name matching**: accepts relative or absolute input
  (normalized, 5.6); matches by NAME against BOTH sets
  (casefold on Windows); a hit removes from whichever set holds it — a
  hand-edited entry present in both is removed from both. Disk-aware
  discrimination is an ALLOW-time concern only (remove deletes an entry, not
  a path).
- **R6 `list` rendering**: same multi-line format as R4 (numbers align with
  `remove` so a listed number can be copied directly); empty state
  `Files: (none)  Dirs: (none)`; ignored legacy flat values append
  `[!] ignored legacy entries: N -- re-add via /dir-whip allow`.
- **R7 Storage normalization**: `dirs` entries stored and displayed WITHOUT a
  trailing slash; nested relative paths (`proj/sub`) go to `dirs` whether or
  not they exist on disk yet.
- **R8 Unchanged**: no confirmation step (user-authoritative direct
  mutation); narrow cache refresh takes effect immediately; runtime
  allowlist tool unaffected; digit-only filenames (e.g. `123`) are always
  parsed as indices (documented).

- Tool `dir_whip_allow_path(path)` — runtime allowlist (T1);
  session-scoped, cleared at session start (5.4).
- Tool `dir_whip_settle(paths)` (NEW v2.7, LAZILY REGISTERED) — same-turn
  self-heal for write-audit violations (5.18): hard-constrained to paths
  currently in this session's unresolved pending set (zero arbitrary
  filesystem capability); moves each accepted path via shutil.move into
  `<dir-whip home>/audit-quarantine/<timestamp>/` (v2.11, SCR-043 R5 —
  outside the workspace root; audit-safe:
  snapshot scans top-level entries only and directory entries never violate);
  returns the moved list; subagents rejected (remediation is the parent's
  job); fail-open on error (error dict, latch stays). Registered on first L1
  notice fire so sessions with no violations pay zero standing schema cost;
  fallback if mid-session registration proves unavailable on a surface:
  constant registration.

**Report layout (exact field order, one field per line):**

```
[dir-whip] v<version>
State: enabled|disabled
Working Directory: <value>  (source: <source>)
Allowlist:                              # multi-line block (when any entry exists, see below)
  Files: (none)|<comma-joined file basenames>
  Dirs: (none)|<comma-joined relative dir paths>
  [!] ignored legacy entries: N -- re-add via /dir-whip allow   # legacy values only
WARNING: ...                            # only when override != terminal.cwd
Stats File: <absolute stats.jsonl path>
Debug Log: <absolute dir-whip.log path> [(no records yet)|(unavailable)]
Health: Good                            # last line; single Good when clean
# With problems, Health becomes a brief problem list:
# Health: N issue(s)
#   - resolution: FAIL-OPEN
#   - stats.jsonl: NOT WRITABLE (<err>)
```

Field rules (SCR-029 Plan A; labels redesigned per SCR-031 / B2; v2.8 rework):
- Line 1 `[dir-whip] v<version>`: version read from the package-root
  plugin.yaml (the single version source; simple text parse, no PyYAML).
  Any failure (missing/unreadable file, no match) -> `unknown`; never
  raises.
- Line 2 `State`: `enabled` (working_dir_root resolved), or `disabled` when
  the resolution chain (5.5) returned no root (fail-open semantics
  unchanged, 5.12). v2.8 renames the values from ACTIVE/FAIL-OPEN to
  enabled/disabled.
- Line 3 `Working Directory`: the resolved value, two spaces, then the
  resolving source — `guard-config` (dir-whip-config.yaml override) /
  `profile-config` (profile terminal.cwd) / `fail-open`, matching the 5.5
  chain steps; no value -> `Working Directory: (unresolved)`. Display term
  is "Working Directory" (G1); code identifier working_dir_root unchanged.
- **Allowlist multi-line block (v2.8)**: the former single line
  `Allowlist: Files: X  Dirs: Y` becomes a block — an `Allowlist:` header
  line plus one line each for `Files:` / `Dirs:`, valued sections indented
  2 spaces; with NO entries at all (no files/dirs/legacy) the existing
  single line `Allowlist: (strict empty allowlist)` is kept (also when the
  key is missing, fail-closed hint); an ignored legacy flat value adds an
  indented block line `[!] ignored legacy entries: N -- re-add via
  /dir-whip allow`. Old keys `exempt_paths` / `allowed_root_files` are not
  displayed (removed, B2).
- **Removed lines (v2.8)**: `Terminal Guard:` (meaningless after the
  three-key de-configuration, R7) and `Reminder:` (five-state observability
  moves to the 5.13 `session-reminder` stats record; the internal
  `state.session.reminder_status` field stays for the stats reason).
- **Debug Log line (v2.8, new)**: second-to-last line (before Health); the
  absolute path of the dedicated diagnostic log dir-whip.log (5.13,
  profile-aware); suffixed `(no records yet)` when the file does not exist
  yet, `(unavailable)` when log setup failed.
- **Health moved last (v2.8)**: a single `Health: Good` line when clean
  (value vocabulary changes from OK to Good); with problems it becomes a
  brief problem list (`Health: N issue(s)` + indented problem lines):
  `- resolution: FAIL-OPEN` and/or `- stats.jsonl: NOT WRITABLE (<err>)`.
  A missing dir-whip-config.yaml is the design default, NOT a problem.
- Anomaly-only WARNING line: emitted when an explicit `working_dir_root`
  override differs from the current profile's `terminal.cwd` (the Q6
  footgun: the desktop-settings edit is masked by the override).
- `Stats File`: the absolute stats.jsonl path of the session profile's home
  (5.13, SCR-027), placed before Debug Log.

Removed earlier (memo gone, B4): `/dir-whip workspace_status` and
`/dir-whip workspace_update` commands; tools
`dir_whip_auto_update_workspace` and
`dir_whip_register_workspace`.

### 5.8 Error Handling and Thread Safety

Rules (v1.4 retained):
- Hook callbacks NEVER raise. All exceptions caught, logged, return None.
- Accept `**kwargs` in all callbacks (forward compatibility).
- Cache resolved config via `from plugins.plugin_utils import lazy_singleton`.
- If register() crashes, plugin is disabled, Hermes continues normally.
- Log at WARNING level for guard blocks, DEBUG for allows.

v0.2.0 additions:
- Stats writes are append-only single-line JSON under a lock; a failed stats
  write is logged and NEVER affects the guard verdict (fail-open logging).

### 5.9 Session Directory Detection

A path is "inside a valid Session Directory" if the first path component
directly under working_dir_root matches the pattern (Session Directories
exist only at the Working Directory root):

```python
SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

def is_inside_session_dir(path, working_dir_root):
    """Check if path is under working_dir_root/<session_dir>/..."""
    rel = os.path.relpath(path, working_dir_root)
    parts = rel.replace("\\", "/").split("/")
    if parts and SESSION_DIR_RE.match(parts[0]):
        # Validate it's a real timestamp
        try:
            datetime.strptime(parts[0][:15].replace("_", ""), "%Y%m%d%H%M%S")
            return True
        except ValueError:
            return False
    return False
```

### 5.10 Terminal Write Interception (simplified, A1/A2)

The `terminal` tool is intercepted. `args["command"]` is parsed by a lightweight
shell tokenizer (respecting quoting/escaping) that extracts candidate write
targets. Detection is COARSE by design:

- **Block tier** (high-confidence target + root violation): redirects
  (`>` `>>` `1>` `2>`), `touch <path>`, `cp`/`mv` destination argument.
- **Allow-and-log tier** (write intent, target uncertain): everything else
  with possible write intent — nested shells (`bash -c` / `sh -c` /
  `powershell -Command`), python / node / sed / tee / curl / wget / dd,
  dynamic or non-literal paths, and commands containing a heredoc (`<<`).
  Allowed; a structured log event is recorded (rule_key
  `terminal-write-uncertain`). NO approval gate (A2).
- **Allow tier** (fail-open): read-only commands and commands with no
  parseable write target.

**Chain-aware target extraction (SCR-033).** The tokenizer emits `;` as a
standalone separator token; `&&` (two `&` tokens), `;`, `|` and newlines are
chain boundaries. Block-tier targets are extracted per command segment — the
redirect / touch / cp-mv target is looked up only inside the segment that
contains the write command, never across a chain boundary. Consequence:
writes chained after another command (e.g. `echo hi && touch <file>`) are now
detected (previously missed). A redirect target starting with `=` (residue of
an unquoted `>=` comparison split across a `>` redirect) is not a valid
target.

**Device-path exemption (SCR-033).** `/dev/null`, `/dev/stdout` and
`/dev/stderr` are exempt BEFORE normalization: they never enter the
classification chain and produce no verdict or stats event. (On Windows they
must not be drive-inherited into a fabricated path such as `E:\dev\null`.)

Relative targets resolve against `args["workdir"]`, falling back to
`get_session_cwd(task_id)`, then `working_dir_root` if the session CWD is
unrecorded (never `os.getcwd()`). Multi-target commands use strictest-wins
precedence (any block -> block; else allow). Always on (as of v2.8 the
`terminal_guard` config key is removed; no switch).

rule_keys for statistics: `terminal-redirect`, `terminal-touch`,
`terminal-cp-mv`, `terminal-write-uncertain`.

### 5.11 Runtime Allowlist (retained, Q2; v2.9 entry gating + user confirmation; v2.11 value-domain gating + segment-boundary matching)

The plugin registers the tool `dir_whip_allow_path(path, confirm=false)` at
`register()` (via `ctx.register_tool`) -- this is the plugin's eager,
resident tool; the `dir_whip_settle` self-heal tool (5.18) is lazily
registered on the first L1 audit notice instead. Its handler adds `path`
to a session-scoped in-memory allowlist, merged with allowlist `dirs`
entries in the classification chain (T1, step 6 of 5.3 — consulted only
AFTER the T0 scope premise; v2.11). The allowlist is cleared at every session start
(`on_session_start`, 5.4); `reset_cache()` (invoked at plugin
register/re-register) also clears it, so entries never leak into the next
session. The tool's description tells the agent the entry is "exempt for this
session".

v2.9 semantics (SCR-041 R1): a runtime entry is PROSPECTIVE-ONLY — future
writes under the path pass the chain (T1), but a runtime entry never settles a
recorded root-write violation: the L3 settlement re-scan (5.18) classifies
pending paths by the CONFIG allowlist only (allowlist `files`/`dirs`
entries + session directories). The pre-write sanction flow is unaffected:
a path exempted BEFORE the write never creates a violation (the audit diff
classifies at write time through the same chain).

Matching semantics (v2.11, SCR-043 R4): entries match by SEGMENT BOUNDARY —
forward-slash normalized and casefolded on Windows; `target == entry` OR
`target` starts with `entry.rstrip("/") + "/"` — a directory entry exempts
its entire subtree, a trailing slash on the entry is compatible, and the
former bare `startswith` (which let `docs` exempt `docs_secret`) is gone.
`pathlib.is_relative_to` was rejected to keep ONE lexical basis with
`_paths_equal` / `within_working_dir`. (Runtime entries stay absolute-path
based — they are user-specified per session, unlike the persistent relative
`allowlist`.)

Tool registration: the schema follows the OpenAI function format
(name/description/parameters) with parameters `path` (string, required)
and `confirm` (boolean, optional, default false); the handler signature is
`(args, **kwargs)` and reads `path`/`confirm` from the args dict, remaining
compatible with callers that pass a bare string. The schema `description`
instructs the agent on the two-step flow (call without `confirm` to obtain
the briefing, relay it to the user, re-call with `confirm=true` only after
explicit user approval).

v2.9 entry gating (SCR-041 R2), checked in order before any state change:

- Subagent calls are REJECTED — the tool is a user-sanction surface and the
  sanction flows top-down only (user -> main agent -> delegated scope); a
  subagent that needs an exemption fails and reports back so the parent can
  ask the user. Response (parent-guidance variant, mirroring the 5.3
  subagent block variant):

```
[dir-whip] BLOCKED: dir_whip_allow_path is not available to subagents.
Exemptions are granted by the user via the main agent. Write to the target
directory passed by the parent agent, or report back so the parent can ask
the user.
```

- The Working Directory root itself is REJECTED as a path argument (one
  call must not exempt everything; workspace-wide exemptions belong in
  config allowlist `dirs` authored by the user):

```
[dir-whip] BLOCKED: the Working Directory root itself cannot be allowlisted.
Allow a specific file or subdirectory path instead; workspace-wide
exemptions belong in dir-whip-config.yaml (allowlist dirs) authored by the user.
```

- A path OUTSIDE the Working Directory is REJECTED (v2.11 R2c, SCR-043 R3):
  outside-root writes need no registration — they are allowed and logged as
  external-write (T0, 5.3). The rejection is self-explaining to prevent
  agent retry loops (feedback/14 E5 R2); the judgment reuses the chain's own
  basis (normalize_target + within_working_dir — never a hand-rolled prefix
  comparison, guarding against Windows drive-case / backslash / `..` drift):

```
[dir-whip] BLOCKED: the path is outside the Working Directory; no allowlist
entry is needed. Writes there are allowed and logged (external-write).
Retry the write directly at the requested path.
```

- A rejected call records a stats row `block / allow-path / <rule_key>` —
  bus-skipped (no generic blocked fanout), mirroring the write-audit-violation
  precedent (5.13/5.14): subagent calls -> `allow-path-subagent-rejected`;
  root-target calls -> `allow-path-root-rejected`; outside-target calls ->
  `allow-path-external-rejected` (v2.11).

v2.11 add-layer assertion + empty-value rejection (SCR-043 R3):
`runtime_allowlist_add(path, working_dir_root=None)` asserts the value
domain when the caller injects the resolved root — an outside-root path is
rejected from the store (the handler always passes the root; direct callers
without a root keep working unchanged). config.py NEVER imports verdict
(verdict depends on config, ADR-0007) — the assertion uses
`paths.within_working_dir`, the same implementation the chain uses. Empty
or None paths are rejected outright: a normalized empty string
prefix-matched EVERYTHING — one empty entry meant a full-session exemption
(feedback/14 finding). Together with the root rejection (R2b) and the
outside rejection (R2c) the registrable value domain is the strict subtree
of the Working Directory — value hygiene, not the signal guard (the T0
scope premise protects external-write unconditionally).

v2.9 two-step user confirmation (SCR-041 R3): adding an entry REQUIRES
explicit user approval via a mandatory two-step protocol.

- First call (`confirm` absent or false): NOTHING is added; the handler
  returns the confirmation payload (verbatim below) and records the path in
  a session-memory `confirmation-issued` set. The payload issuance and an
  unbriefed `confirm=true` re-issue are recorded in the diagnostic log
  (dir-whip.log, DEBUG) only — no stats row, no bus event (user ruling
  2026-08-28).
- Second call (`confirm=true`): honored ONLY for a path already in the
  `confirmation-issued` set; a `confirm=true` without a prior issued payload
  adds NOTHING and RE-ISSUES the confirmation payload, marking the path as
  briefed (equivalent to a first call — `confirm=true` never adds on its
  own, so the two-step cannot be skipped; 2026-08-28 second-review ruling,
  replacing the earlier rejection design to avoid a new verbatim). On
  success the entry is added and the regular `runtime-allowlist-add` stats
  row / bus event fire (5.13/5.14).
- Payload (verbatim):

```
[dir-whip] CONFIRMATION REQUIRED: adding "<path>" to the runtime allowlist
exempts ALL file operations under it from the guard for the rest of this
session. Previously recorded root writes under it are NOT remediated by
this exemption (they stay pending until settled). The entry expires
automatically when the session ends; persistent exemptions belong in
dir-whip-config.yaml
(allowlist files/dirs, removable via /dir-whip remove).
Present this to the user and ask for explicit approval. Re-call
dir_whip_allow_path(path=..., confirm=true) ONLY after the user approves.
```

- Latch-context conditional line (2026-08-28 second review; third-review
  upgrade to choice presentation): when the pending set is non-empty (latch
  active), the payload gains one trailing line — `NOTE: a settlement block
  is currently active — present the resolution choice to the user: move the
  file(s) (settle), or keep them at the root (give the user the exact
  command: /dir-whip allow <path>). Writes stay frozen until then.` —
  preventing the wasted approval round-trip (the latch freezes ALL
  write-class calls; the exemption only passes the exemption tier (T1) and
  does not open the
  gate).

- Removal method (documented only — no revoke capability, user ruling
  2026-08-28): runtime entries expire automatically at session end
  (`on_session_start` clear + `reset_cache`); persistent exemptions belong
  in dir-whip-config.yaml allowlist (`files`/`dirs`), removable via
  `/dir-whip remove`.

The SKILL.md instructs the agent to call this tool when the user explicitly
specifies a target path in the conversation, so the user's intent is honored
without weakening the guard for other paths; under the v2.9 protocol the
agent relays the confirmation payload to the user and re-calls with
`confirm=true` only after explicit approval. The plugin's own tool call is
never intercepted.

### 5.12 Fail-Open Warning (retained, SCR-004 mechanics)

When `working_dir_root` cannot be resolved, the guard injects a warning via
`ctx.inject_message()` on the first tool call of each session, then allows
(fail-open). A module flag de-duplicates within a session and is reset at
`on_session_start`, so a disabled guard re-warns each new session until fixed.
Gateway mode degrades to a log line (`inject_message` unavailable). The
warning is term-updated:

```
[dir-whip] WARNING: The guard is DISABLED because the Working Directory
could not be resolved. File writes are NOT being enforced.
Check dir-whip-config.yaml (working_dir_root) or your profile's config.yaml
(terminal.cwd) and restart the session.
```

### 5.13 Structured Logging and Statistics (D1/D2/D3)

**Structured logging (D1).** Unified `logging.getLogger("dir-whip")`.
Every guard verdict emits ONE single-line structured event:
`outcome` (block / allow / external-write / fail-open), `reason`, `tool`,
`target` (relative to working_dir_root; external targets omitted or
hash-prefixed), `rule_key`, `is_subagent`, `session_id`, `timestamp`.
- Fail-open and config anomalies: WARNING level.
- Block: WARNING. External-write: INFO. Other allows: DEBUG.

**Statistics (D2).** In-memory counters aggregated by outcome × tool ×
rule_key, split by `is_subagent`:
- `post_tool_call` observation: records the completion (and result state) of
  write-class tool calls — the hook sees the call finished, not the bytes on
  disk (rule_key `landed:<tool>`).
- `post_approval_response` observation: records the approval/rejection
  distribution of host approval prompts (rule_key `approval:granted` /
  `approval:denied`).
- `transform_tool_result` / write-audit observation (5.18): records root
  write audit violations and latch blocks (rule_key `write-audit-violation`
  / `write-audit-gate-block`); the L1 notice itself is NOT an event.
- Continuation-nudge observation (5.18, v2.8): records nudge firings
  (rule_key `pre-verify-nudge`, reason carries the attempt ordinal;
  target=None — paths are already carried by the violation events).
- `allow_path` tool-call observation (v2.8): records runtime exemption adds
  (rule_key `runtime-allowlist-add`, target relativized; symmetric with the
   `dir-whip:allowlisted` bus event).
- Entry-gating observation (5.11, v2.9): records allow_path entry-gating
   rejections (rule_keys `allow-path-subagent-rejected` for subagent calls /
   `allow-path-root-rejected` for root-target calls; reason = category code
   subagent-rejected | root-target, no raw paths; bus-skipped — no generic
   blocked fanout, symmetric with the write-audit violations).
- Session-start observation (5.4, v2.8): records the discipline-block
  injection five states (rule_key `session-reminder`, reason = the state
  literal injected | skipped-outside | skipped-child | skipped-project |
  unavailable; one line per top-level session).
- Settle-rejection observation (5.18 R4, v2.8): records settlement
  rejection/failure categories (rule_key `write-audit-settle-rejected`,
  reason = category code subagent-rejected / invalid-paths /
  not-in-pending / move-failed, no raw paths).

**Persistence (D3).** Append one JSON line per event to
`HERMES_HOME/dir-whip/stats.jsonl`:
- Session fields: `profile`, `session_id`, `is_subagent`, `started_at`
- Event fields: `ts`, `outcome`, `reason`, `tool`, `rule_key`, `target`
  (relative to working_dir_root; external paths -> hash prefix / omitted)
- Privacy: no file contents, no absolute paths, no prompt text.
- Rollover: before appending, if stats.jsonl exceeds 5MB, rename it to
  stats.jsonl.1 (overwrite the previous archive) and start a new file.
- Failure isolation: stats write errors are logged and never affect verdicts
  (5.8).

The `/dir-whip` command does not display statistics (SCR-029); the recording
backend is unchanged — totals are inspectable via the Stats File path shown
by `/dir-whip`.

**Diagnostic log file (v2.8 R5).** At register() the plugin attaches a
dedicated DEBUG-full log `<HERMES_HOME>/dir-whip/dir-whip.log`
(profile-aware placement mirroring the stats path pattern) via the new
logsetup.py module: it captures every `dir-whip` logger line — including the
DEBUG breadcrumbs below the host agent.log INFO+ threshold (allow-class
verdicts, fail-open handling paths, etc.). Three-tier fail-open degradation:
`concurrent_log_handler.ConcurrentRotatingFileHandler` (cross-process-safe
rotation, installed in the host venv; a third-party import does not violate
ADR-0007) → stdlib `RotatingFileHandler` → console only. Parameters:
maxBytes=5 MiB, backupCount=3, delay=True (file created on first write),
encoding=utf-8. Privacy policy: absolute paths ARE allowed (a local
diagnostic file; diagnostic value first; no secret-class content in dir-whip
messages). Known limitations: with one desktop process serving multiple
profiles the log lands in the registering profile's directory and lines from
other profiles interleave (verdict JSONs carry session_id for attribution);
the stdlib fallback path has the known Windows multi-process rotation
WinError 32 risk.

### 5.14 Event Bus Events (D5)

The inter-plugin event bus is a first-class v0.2.0 capability (1.3). Bus
availability is version-dependent and detected at runtime: the local v0.20.0
install already provides `ctx.emit()`/`subscribe()` (verified against the
installed hermes-agent source); older versions degrade silently — no
reconfiguration needed on upgrade. The plugin emits `dir-whip:*`
events (full names); the implementation MUST call `ctx.emit("<bare-name>",
payload)` — the API accepts BARE event names only and FORCES the
`dir-whip:` namespace (passing a namespaced name raises ValueError,
fail-closed):

- `dir-whip:blocked` — a write was blocked (outcome, rule_key, target)
- `dir-whip:external-write` — an external path was allowed + logged
- `dir-whip:allowlisted` — `dir_whip_allow_path` added an entry
- `dir-whip:approval-requested` / `dir-whip:approval-resolved` —
  observed host approval flow (post_approval_response)
- `dir-whip:write-audit-violation` — root write audit (5.18): a new or
  modified root-level file outside the allowlist (files/dirs) / session scope
  (path, session scope flag, first-seen)
- `dir-whip:write-audit-gate-block` — the L3 pending-violation latch
  blocked a write-class tool until remediation (path, latch status)

Event payloads follow the same privacy rules as 5.13: `target` values are
relative to working_dir_root; external paths are hash-prefixed or omitted.

Silent degradation: bus absence is detected via capability check
(hasattr / try-except) at register(); no event is emitted, no error is raised,
one DEBUG log line records the skipped emission.

### 5.15 pre_command Observation (D6)

The plugin registers the `pre_command` hook (observer-only in current Hermes):
slash-command invocations are recorded — `surface` ("cli" | "gateway"),
`command` (canonical name), `alias_used` (the exact token the user typed),
plus `args_raw` / `session_key` / `platform` when present — into the log and
statistics (rule_key `pre-command:<command>`). Blocking / interception of
commands is an UPSTREAM DEPENDENCY (official middleware #64204/#64231); until
it lands, the hook only observes.

### 5.16 Subagent Observation (E1/E2)

- `subagent_start` hook: records `child_session_id`, `child_role`,
  `child_goal` (plus `parent_session_id` / `parent_turn_id` /
  `parent_subagent_id` / `child_subagent_id` when useful) into the log and
  stats session fields, and adds `child_session_id` to the plugin's
  `child_session_ids` set (the 5.4 top-level-session gate).
- `subagent_stop` hook: records the child's completion
  (`child_session_id` / `child_subagent_id`), removes the id from
  `child_session_ids`, and closes the child's stats session context
  (symmetric bookkeeping; no verdict impact).
- Guard verdicts apply identically to subagent writes: the child inherits the
  parent's toolset, so the same pre_tool_call path covers its writes — no
  special verdict branch (Q13).
- Statistics are split by `is_subagent` (recorded in stats.jsonl; the
  /dir-whip report does not display them).
- Discipline (prompt layer, E1): the parent ensures the target directory
  exists BEFORE delegating (creating the parent Session Directory first if
  needed — lazy creation stays the parent's job). Subagents write to the
  target directory passed by the parent — default: the parent session's
  `.tmp/`; the parent may explicitly pass an `Outputs/` path for formal
  deliverables, or a per-subagent subdirectory (e.g. `.tmp/<task>/`) to avoid
  concurrent name clashes (no mandatory subdir isolation — conflicts are
  arbitrated by the parent). Subagents do NOT create their own session
  directories and do NOT promote their own outputs (`.tmp/` -> `Outputs/`
  promotion is the parent's review step). When the target directory is
  missing or a write is blocked, the subagent reports back to the parent
  instead of creating a session directory itself.

### 5.17 Skill Registration and Teaching Channels (C1/C2)

- `register_skill()`: registers the bundled skill from
  `dir-whip/skills/workspace-organization/` (SKILL.md + references +
  scripts). Opt-in loading per upstream semantics (3.1).
- `register_system_prompt_section()`: REMOVED in v2.7 (the Always-on
  Discipline Prompt channel is gone; teaching = session-start discipline
  block (5.4) + block-message completion (5.3) + SKILL.md opt-in load).

### 5.18 Root Write Audit (SCR-034; v2.7 same-turn self-heal; v2.9 re-scan semantics; v2.11 quarantine relocation)

The ROOT WRITE AUDIT is the second detection backbone for terminal
discipline: it OBSERVES what the filesystem actually changed instead of
inferring intent from the command string. Command-parse interception (5.10)
remains the cheap pre-check; the audit is the reliable safety net — no
parsing, catches every syntax (shutil, heredoc, tee/dd, future forms).

**Mechanism (post-hoc, diff-based).**
- Triggered ONLY by `terminal` tool calls. `pre_tool_call` snapshots the
  top-level entries of the Working Directory root (`os.scandir`, recording
  `name` / `st_size` / `st_mtime_ns` / `is_dir`); `post_tool_call`
  re-scans and diffs. Commands blocked at pre (did not run) get no post
  snapshot.
- Only FILE entries are judged (`is_dir == False`). Directory mtime changes
  (session directories, `.git/`, `.hermes/` content) are ignored.
- The delta set is classified through the shared chain
  (`normalize_target` + `classify_target` with allowlist `dirs` entries,
  `is_subagent`). A VIOLATION is: a new or modified root-level file that is
  NOT in `allowlist` `files` entries (5.6), NOT under an `allowlist` `dirs`
  entry, and NOT inside any session directory. Deletions are
  recorded but are report-only — never violations (5.8 delete principle).

**Handling ladder (post-hoc accountability loop).**
- **L1 teach — fire-once notice**: via the `transform_tool_result` hook
  (Hermes first-party precedent: the security-guidance plugin appends a
  warning to the tool result; returning a string replaces the result the
  model sees). When the diff FIRST finds a violation, the terminal result
  gets one appended notice naming the path and the remediation. v2.9 text
  (shares the `_remediation_instruction` helper with the continuation nudge —
  single source of truth; v2.9 R4 rewording re-attributes the
  config-allowlist option to the USER and makes the latch-period freeze
  explicit; third-review 2026-08-28 adds the exact-command instruction):
  "Remediate now: call dir_whip_settle(paths=[...]) to move the file(s) into
  quarantine (<dir-whip home>/audit-quarantine/), or move them manually into
  a Session Directory (YYYYMMDD_HHMMSS_TaskName/Outputs|.tmp/). To keep the
  file(s) at the root, ask the user to add them to the allowlist files
  entries in dir-whip-config.yaml (files: [notes.txt]) — give them the
  exact command to run: /dir-whip allow <path> — while the block is active
  all writes are frozen (config edits included). Further writes to the
  Working Directory are blocked until then." HARD CONSTRAINT: one
  notice per violation, never re-appended on later results (context
  hygiene); error results are not decorated. ORDERING (live-verified
  2026-08-22, 30.12): Hermes fires `transform_tool_result` BEFORE
  `post_tool_call` for the terminal tool, so the audit re-scan runs inside
  `transform_tool_result` (before the notice reads the pending set); the
  `post_tool_call` re-scan stays as an order-agnostic no-op fallback (the
  pre snapshot is consumed exactly once regardless of hook order).
- **L2 record**: verdict events `write-audit-violation` /
  `write-audit-gate-block` flow through the 5.13 statistics and the 5.14
  event bus (`dir-whip:write-audit:*`, see below). Stats.jsonl privacy rules
  unchanged.
- **L3 gate — pending-violation latch**: an unresolved violation latches
  per session. On the NEXT write-class tool call (write_file / patch /
  terminal), `pre_tool_call` re-scans the root; the file still present →
  BLOCK the write via the standard block channel (message lists the
  unresolved paths and the remediation). The latch opens when a re-scan
  finds the path gone, moved outside the root, or legalized under the CONFIG
  allowlist (allowlist `files`/`dirs` entries or a session directory).
  v2.9 (SCR-041 R1): the settlement re-scan classifies by the CONFIG
  allowlist only — a runtime-allowlist entry (5.11) does NOT settle a
  recorded violation (runtime exemption is prospective-only: future writes
  pass the chain (T1), past violations stay pending until physical remediation or a
  user-authored config entry). First write cannot be
  prevented; all FURTHER writes are frozen until remediation. The latch is
  session-scoped and cleared at session start; subagent sessions inherit
  the parent's latch (child_session_ids gate, 5.4/5.16). NOTE (v2.7): while
  latched, EVERY write-class call is blocked — including a remediation
  `mv`/`rm` — which is why agent-side self-heal goes through the
  `dir_whip_settle` tool (below), not through terminal commands.
- **L4 auto-move**: reserved direction, NO config key (as of v2.8 the
  `write_audit_autofix` reservation key is removed — an extension of the
  de-configuration ruling; any future activation goes through its own SCR).
  The envisioned behavior moves the offending file into the session's
  `.tmp/`. Held back because auto-moving conflicts with the "delete =
  report-only" safety principle and can break the agent's later references
  to the path.

**Same-turn self-heal (v2.7, R4/R5).** Goal: the detect -> notify -> settle
loop completes within the user turn that triggered the violation.

- `dir_whip_settle(paths)` tool (5.7): the ONLY agent-side remediation
  channel. Hard-constrained to paths currently in this session's
  unresolved pending set; moves them via shutil.move into
  `<dir-whip home>/audit-quarantine/<timestamp>/` (v2.11, SCR-043 R5 —
  relocated OUT of the workspace root; dir-whip home = `paths._profile_home()/dir-whip/`,
  profile-aware, beside dir-whip-config.yaml / stats.jsonl; timestamp
  batches keep the all-or-nothing rollback semantics; audit-safe by
  5.6/5.18 semantics: top-level snapshot only, directory entries never
  judged; reversible (no deletion); subagents rejected; fail-open on error
  (error dict, latch stays). After a successful settle the next latch
  re-scan finds the original paths gone -> settled -> gate opens.
  Contract (2026-08-26 ruling, plugin-skill consistency): `paths` args are
  ABSOLUTE paths (forward-slash normalized) as the canonical form — the same
  form every agent-facing surface already uses (verdict block message, L1
  notice, L3 gate message, skill audit_workspace.py output); relative args
  are tolerated and resolved against working_dir_root before the pending-set
  check. Idempotent: a path that no longer exists is a successful
  no-op settlement (matches the latch's lexists semantics). Returns
  `{"settled": [<root-relative paths>]}` on success (relative for privacy)
  or `{"error": "<reason>"}` on rejection/failure, rendered as a JSON
  string. Records stats `allow/settle/write-audit-settle` only — NO bus
  event (7 emits unchanged).
- The L3 gate block message (both variants base) appends
  `"Remediate now: call dir_whip_settle(paths=[...])"` (2026-08-26 ruling —
  the gate blocks remediation mv/rm, so the message must name the tool
  channel; subagent variant stays report-to-parent only). v2.9 (R4): the
  main-agent Fix sentence re-attributes the config-allowlist option to the
  USER — "or ask the user to add them to the allowlist files entries in
  dir-whip-config.yaml (files: [notes.txt]) — give them the exact command:
  /dir-whip allow <path> — while the block is active all writes are frozen
  (config edits included)" — removing the false
  affordance of agent-side config edits during the latch (realhost: two
  gate-blocked turns wasted); the subagent variant and the settle line are
  unchanged.
- **Continuation nudge (v2.8 R1/R2 rewrite; host hook identifier `pre_verify`,
  external-facing term 「dir-whip 续推兜底」/"dir-whip continuation nudge")**:
  the plugin registers Hermes' `pre_verify` hook; when this session has
  unresolved pending violations AND the host reports file mutations this turn
  (`changed_paths` non-empty), the hook returns a continue directive so the
  host keeps the turn going; any other return lets the turn finish. Subagent
  sessions no-op (remediation is the parent's job).
  - **Message (verbatim lock, shares the L1 remediation sentence helper;
    v2.9 third-review tail adds the resolution-choice presentation)**:
    `[dir-whip] {N} unresolved root write(s) remain at the Working Directory root. Remediate now: call dir_whip_settle(paths=["<p1>", ...]) to move the file(s) into quarantine (<dir-whip home>/audit-quarantine/), or move them manually into a Session Directory. Present the resolution choice to the user: move the file(s) (settle), or keep them at the root — for the keep-at-root choice, give the user the exact command to run: /dir-whip allow <path>. Finish only after settlement or the user's decision.`
    Paths are absolute forward-slash form (matching the settle canonical
    input contract); the runtime exemption TOOL `dir_whip_allow_path` is
    never mentioned (no exemption option offered, no negative call-out —
    v2.9 third-review precision: the USER config command `/dir-whip allow`
    IS explicitly mentioned, pointing at the user-authored settlement
    channel; user ruling 2026-08-28: the user chooses move-vs-keep and gets
    the exact copy-paste command; `--create` verified non-contradictory —
    the violating file always exists, so the plain form is correct).
  - **Session-cumulative cap (v2.8, overturning the v2.7 "throttling relies
    on the host budget, the hook adds none" clause)**: plugin-side constant
    `PRE_VERIFY_NUDGE_CAP = 3` (hardcoded) — at most 3 continuation nudges
    per session lifetime (the counter resets in `_audit_session_start`);
    after the cap the hook returns None and the turn finishes naturally.
    The host's per-turn verify-nudge budget (`max_verify_nudges()`) remains
    as the outer bound.
  - Each firing records stats (rule_key `pre-verify-nudge`, reason carries
    the attempt ordinal, 5.13).
  - v2.9 (SCR-041 R1): a runtime-allowlist entry added after a nudge does
    NOT clear the pending set — the nudge keeps firing until physical
    settlement (the allow_path escape observed in v0.6.0 realhost is
    closed; the cap then bounds the loop as designed).
  KNOWN LIMIT (accepted): pure-terminal violation turns never reach the
  continuation nudge (the host's `_turn_file_mutation_paths` only records
  write_file / patch landings) — there the settle tool's discoverability in
  the L1 notice carries the loop; an upstream suggestion to count terminal
  writes into the mutation ledger is registered (9 / feedback/10 #6).

**Context hygiene (hard constraints).** The audit diff runs entirely in the
plugin process — zero context cost. Only the single L1 notice enters the
conversation. Unresolved violations are silent in session state (no
periodic re-notification); they surface only via the L3 gate or the
`/dir-whip` report, not by repetition.

**Performance (measured, Windows 10 / NTFS / Python 3.11).** One snapshot:
0.04ms at 15 entries (typical root), 0.15ms at 95, 6.3ms at ~4900
(pathological). One audit round (pre + post + diff): ~0.1-0.3ms typical,
<1% of command execution time. Guardrails: an internal audit entry constant
of 2000 (not configurable as of v2.8) skips the audit with a one-time
WARNING when the root entry count exceeds the cap; scan OSError → silent
skip (fail-open). Acceptance: p95 < 10ms at ≤500 entries.

**Known limitations.** Background processes (`cmd &`) that write after the
post hook fall outside the audit window — covered by the cron
audit_workspace.py net (same diff logic reusable). Network-mounted roots
may stat slowly — as of v2.8 the `write_audit` config escape is removed
(the audit is always on); affected users can only disable the plugin for
that profile. Parallel terminal calls in one task
are last-snapshot-wins (Hermes executes tools sequentially per turn;
acceptable). Deep subdirectory content is NOT audited (root-level
discipline is the scope, aligning with the root-file semantics of 5.10).

**Relationship to 5.10.** The audit provides the backstop that makes the
5.10 heuristics safe: heredoc (`<<`) demotion and `=`-residue filtering
(SCR-033) may let a root write past the pre-check, but the post-hoc diff
catches the actual file and enters the L1-L3 ladder. The pre-check and the
audit use the same single `allowlist` key (structured `files` / `dirs`),
so they never disagree. rule_keys are namespaced separately (`terminal-*` vs
`write-audit-*`).

---

## 6. Deployment Specification

### 6.1 Installation (F1)

```bash
hermes plugins install shawVV1992/dir-whip/dir-whip --enable
```

Note: the repository default branch is `main` (switched back 2026-08-14;
`main` was fast-forwarded to the v0.2.0 line at 57f64bf), so unpinned
installs clone the v0.2.0 line.

Note: the local hermes CLI v0.20.1 installer rejects manifest v2 plugins
(manifest_version <= 1 gate, plugins_cmd.py:737-750; the runtime loader
accepts v2). Until `hermes update`, install by copying the plugin package
manually (SCR-025).

Installs the plugin AND the bundled skill + scripts + config template — a
single native command. No installer script, no separate skill install
(installer removed, A6; skill bundling Q9).

Runtime configuration stays at `HERMES_HOME/dir-whip/dir-whip-config.yaml`
(SCR-013 location: outside the plugin dir, survives reinstalls).

### 6.2 after-install.md Content

```markdown
## dir-whip installed

**Plugin guard**: Active after next Hermes restart. Blocks file writes to the
Working Directory root outside Session Directories (allowlist files exempt).
External paths are allowed and logged.

**Bundled skill**: the workspace-organization skill ships with the plugin.
Load it explicitly when you need the full discipline reference; a short
always-on discipline prompt covers day-to-day behavior.

**Quick command**:
    /dir-whip   # merged report: version, state, Working Directory +
                # source, config detail, health, stats file path

**Tool**: dir_whip_allow_path — allow a user-specified path for this
session.

**Configure** (optional): edit HERMES_HOME/dir-whip/dir-whip-config.yaml
(structured allowlist files/dirs relative to the Working Directory root,
working_dir_root override; as of v2.8 the terminal_guard/write_audit/
write_audit_entry_cap keys are removed).

**Verify**: Start a new Hermes session. Try writing a file to the Working
Directory root — it should be blocked with a helpful message.
```

### 6.3 Uninstallation and Upgrade (Q14)

```bash
hermes plugins remove dir-whip                          # uninstall
hermes plugins install shawVV1992/dir-whip/dir-whip --force      # update
```

Upgrade from v0.1.x: reinstall with `--force` (the plugin directory has no
.git after subdir installation, so `--force` reinstall is the update path).
`dir-whip-config.yaml` is preserved across reinstalls.

### 6.4 Distribution (F1/F2)

- Native install via the GitHub repository URL only.
- Community plugin index: BLOCKED — the official hermes-plugin-index
  repository does not exist yet; re-evaluate when it lands.
- No plugin pack (single plugin; the skill is bundled inside it).
- GitHub Release whole-repo archives remain distribution-only (the Hermes CLI
  cannot install from archives).

---

## 7. Acceptance Criteria

### 7.1 Skill

- [ ] Bundled: `dir-whip/skills/workspace-organization/` registered via
      `register_skill()`; loads explicitly by name (opt-in)
- [ ] SKILL.md first line is `---` (no BOM, no leading blank line)
- [ ] `name` field: lowercase + hyphens, <= 64 chars
- [ ] `description` <= 1024 chars, first 57 chars contain trigger words;
      wording avoids "organize/clean up sessions"
- [ ] SKILL.md total length <= 100,000 chars
- [ ] SKILL.md frontmatter `version` field REMOVED (plugin version is the sole
      version)
- [ ] Behavior layers documented: scope check / instant discipline / governance
- [ ] Write classification (3-way) documented; root-forbid triple documented
- [ ] Confirmation protocol documented with examples
- [ ] Cron governance mode documented
- [ ] Creation workflow examples (positive/negative) included
- [ ] Interception response template ([Reason]/[Next]) documented
- [ ] Subagent file protocol documented (parent ensures target dir; default
      .tmp/; parent may pass Outputs/; promotion by parent; blocked subagent
      reports back)
- [ ] No memo / multi-profile / cross-profile content
- [ ] ASCII straight quotes, no emoji, forward slashes

### 7.2 Scripts

- [ ] Both retained scripts: `--help` outputs correct usage; `--workspace`
      accepted
- [ ] `--workspace` equality validation: equal to resolved Working Directory ->
      proceed; mismatch -> exit 2 (create_session_dir and audit_workspace)
- [ ] Resolution failure -> fail-open: fallback + one concise stderr WARNING,
      non-fatal (interactive mode); in --gate mode resolution failure ->
      exit 2 + no wakeAgent line + nothing deleted (SCR-042; kept in v0.6.3
      as cron failure visibility, SCR-043 R6)
- [ ] create_session_dir exit codes: 0 / 1 / 2 per 4.1
- [ ] audit_workspace exit codes: 0 / 1 / 2 per 4.2; `--json` output
- [ ] audit_workspace `--gate`: wakeAgent JSON on last line with EXACTLY the
      two keys {wakeAgent, violations} (SCR-043 R6 — removed/failed retired
      with the deletion execution); boundary mismatch or gate-mode
      resolution failure -> exit 2 + no wakeAgent line (agent not woken);
      --json + --gate stdout parses line-by-line as JSON
- [ ] audit NEVER deletes (SCR-043 R6): `--gate` is pure audit + wake (zero
      deletion); interactive mode lists expired `.tmp` entries as a
      read-only proposal (`--days` thresholds the inventory; cleanup is
      agent-decided and confirmation-gated)
- [ ] audit root allowlist from dir-whip-config `allowlist` `files`/`dirs`
      entries; missing key -> strict empty allowlist
- [ ] workspace_resolver.py resolves dir-whip-config -> terminal.cwd -> fail-open
      (no memo, no standalone branches)
- [ ] Removed scripts (clean_tmp.py, init_workspace.py) and installer absent
      from the repo
- [ ] Cross-platform: both scripts run on Windows 10+ / Linux / WSL / macOS
      (portable path handling; platform-matrix tests pass)

### 7.3 Plugin

- [ ] plugin.yaml manifest valid (api_version integer; `manifest_version`
      omitted for v0.20.1 installer compat, e6e2148; provides_hooks incl.
      post_tool_call / transform_tool_result / post_approval_response /
      subagent_start / subagent_stop / pre_command; emits declared incl.
      write-audit-violation / write-audit-gate-block)
- [ ] register(ctx) completes; bundled skill registered; discipline prompt
      injected (≤200 字)
- [ ] pre_tool_call blocks root non-allowlist writes (write_file / patch /
      terminal block tier)
- [ ] pre_tool_call classifies by the T0-T4 chain (SCR-043 R1): outside-root
      -> external-write allow+log EVEN when an exemption matches; the root
      itself -> block (root-file); allows allowlist `files` entries,
      session-dir writes, `dirs` subtrees, runtime-allowlisted paths
- [ ] Runtime allowlist segment-boundary matching: `docs` does NOT exempt
      `docs_secret` (SCR-043 R4)
- [ ] `dir_whip_allow_path` rejects outside-root paths (self-explaining
      message, stats `allow-path-external-rejected` bus-skipped) and empty
      paths; the add-layer assertion keeps the registrable value domain to
      the strict root subtree (SCR-043 R3)
- [ ] `dir_whip_settle` moves into `<dir-whip home>/audit-quarantine/<ts>/`
      (outside the workspace root); `/dir-whip list` shows the quarantine
      path; the guard still blocks tool writes to `.hermes/**`; the audit no
      longer exempts a root-level `.hermes/` (SCR-043 R5)
- [ ] pre_tool_call allows + logs external paths (incl. sibling profile dirs);
      NO cross-profile approve exists
- [ ] Terminal uncertain write intent -> allow + log (no approval gate)
- [ ] Fail-open: unresolvable working_dir_root -> one-time warning + allow
- [ ] Resolution source logged at INFO (dir-whip-config / profile-config)
- [ ] Structured single-line event per verdict; fail-open/config anomalies at
      WARNING
- [ ] Stats counters (outcome × tool × rule_key, subagent split);
      post_tool_call landed observation; post_approval_response
      approval/rejection distribution
- [ ] stats.jsonl appended with session fields; targets relative to
      working_dir_root; 5MB rollover to `.1`; write errors never affect
      verdicts
- [ ] `/dir-whip` merged report implemented (allow|remove|list subcommands with unified
      Files/Dirs two-section numbered presentation; any other argument
      -> `Usage: /dir-whip [allow|remove|list]`); report Allowlist multi-line block (header line + Files/Dirs one line each, valued sections indented 2 spaces; strict-empty single line)
- [ ] `dir_whip_allow_path` retained (session-scoped, cleared at session
      start); `dir_whip_settle` added (lazily registered, pending-set
      constrained, quarantine move)
- [ ] Removed: workspace_status / workspace_update commands;
      auto_update / register tools
- [ ] Event bus: emits dir-whip:* events when available; silent
      degradation otherwise (one DEBUG line)
- [ ] pre_command observer records surface/command/alias; interception marked
      upstream-dependent
- [ ] subagent_start records child_session_id/child_role/child_goal (adds to
      child_session_ids); subagent_stop removes it; stats filterable by
      subagent; verdicts identical for subagent writes
- [ ] Child-session on_session_start skips allowlist clear / fail-open reset /
      reminder injection (child_session_ids gate, 5.4)
- [ ] Blocked subagent gets the parent-target fix variant (no
      create_session_dir guidance, 5.3)
- [ ] No exceptions escape hook callbacks; thread-safe config cache; stats
      writes under lock
- [ ] Block message contains fix instructions and the [Reason]/[Next] cue

### 7.4 Domain Model

- [ ] CONTEXT.md: "Default Working Directory" -> "Working Directory" (= profile
      terminal.cwd; Hermes desktop-settings wording)
- [ ] CONTEXT.md: Profile Workspace Memo / Shared Space / Standalone Mode /
      Workspace Initialization / HERMES_WORKSPACE_ROOT entries removed
- [ ] CONTEXT.md: Subagent and Discipline Prompt defined; guard flow simplified
      (no cross-profile / approve tiers); working_dir_root semantics inverted
- [ ] CONTEXT.md: Session Directory vs Hermes session distinction kept
      (session-librarian note)

### 7.5 Integration

- [ ] Plugin installed -> bundled skill available (opt-in load) + discipline
      prompt active + guard enforced
- [ ] Agent write to the root -> blocked; agent replies with the
      [Reason]/[Next] template
- [ ] Cron tick with `--gate` works (wakeAgent JSON); boundary mismatch -> no
      wake
- [ ] Local Hermes v0.20.0: plugin installs and runs; the event bus is
      detected and emits (pre_command interception stays observer-only — out
      of v0.2.0 scope); older bus-less versions degrade silently
- [ ] Cross-platform: install + run verified on Windows and WSL (POSIX
      coverage via WSL, SCR-028); full pytest matrix passes on Windows + WSL;
      Linux/macOS stay supported per 8.2 (no live verification, SCR-032)
- [ ] Event bus (bus-enabled Hermes only): dir-whip:* events emitted on
      block / external-write / allowlist / approval observations
- [ ] Full pytest suite passes (updated for removed scripts, memo chain, and
      tools)

### 7.6 Terminal Write Discipline (SCR-033 consolidated into SCR-034, unified)

Prefixed `[A#]` = the unified acceptance id in
`docs/archive/v0.3.0/scr-033-034-plan.md` section 9 (test classes in
testing-standards.md v0.3.0; v2.6 allowlist key = single `allowlist`, A7 wording updated).

- [ ] [A1] feedback/06 archived commands no longer block: the four 2026-08-21
      cases (`up` tail-word, `bak_cdp` grep arg, echo tail-word, heredoc `=`
      residue) verdict allow / external-write; no block-tier false positive
- [ ] [A2] `grep pkg>=1.0` (unquoted): no block; the `=`-residue redirect
      target routes to a `terminal-write-uncertain` event (audit trail kept)
- [ ] [A3] Multi-line heredoc containing bare `>=` produces no pseudo-target;
      any REAL root-level write the command performs is caught by the audit
- [ ] [A4] Chain-aware extraction: `echo hi && touch <root-file>` is now
      detected (block tier); `cp a b && echo backed up` extracts `b` inside
      its own segment only
- [ ] [A5] Device paths exempt before normalization: `/dev/null`,
      `/dev/stdout`, `/dev/stderr` produce no verdict/stats event; no
      drive-inherited `E:\dev\null` fabrication; `2>/dev/null;` leaves no
      `;`-stuck target
- [ ] [A6] Audit kernel: snapshot/diff detects exactly four states — new
      file, modified (mtime/size), deleted (record-only), unrelated change
      (ignored); directory mtime changes are ignored
- [ ] [A7] Violation classification: a new/modified root-level file NOT in
      the allowlist (files / dirs entries) / any session directory is a
      violation; allowlisted files / dirs subtrees / session-dir / `.git/`
      content writes are not; deletions are record-only
- [ ] [A8] L1 fire-once: the `transform_tool_result` notice appears exactly
      once per violation; later tool results carry no re-append; error
      results are not decorated; non-string results untouched
- [ ] [A9] L3 settlement gate: an unresolved violation blocks the next
      write-class tool (write_file / patch / terminal) via the standard
      block channel; the gate re-opens once a re-scan finds the path gone or
      in an allowed location
- [ ] [A10] Cross-layer: `cat > <root-file> <<EOF` is not front-blocked
      (blanket heredoc demotion) but the audit catches the real write and
      enters the L1-L3 ladder
- [ ] [A11] Session scoping: the violation latch is session-scoped, cleared
      at top-level session start; subagent root writes resolve into the
      parent's latch (child_session_ids gate); parent-delegated target dirs
      stay exempt
- [ ] [A12] Performance: audit round p95 < 10 ms on roots up to 500 entries;
      root entry count above the internal audit entry constant 2000 (not
      configurable as of v2.8) skips the audit with a one-time WARNING
- [ ] [A13] Config wiring (v2.8 rewrite): the three keys terminal_guard /
      write_audit / write_audit_entry_cap are removed — the parser stops
      reading them, leftover keys are completely ignored (no hint, no log),
      interception and audit always on; scan OSError fails open silently
- [ ] [A14] Stats/events: verdict events `write-audit-violation` /
      `write-audit-gate-block` in the 5.13 recording and the 5.14 event bus
      (`dir-whip:write-audit-*`), privacy rules unchanged; L1 notice itself
      is not an event
- [ ] [A15] Regression + live: full pytest suite green with the new classes;
      stats.jsonl replay shows external-write noise back to the real level
      and `write-audit-*` counts matching real root writes (live phase)

---

## 8. Constraints and Red Lines

### 8.1 Safety

- No `rm -rf`, `del /S/Q`, bulk rename, recursive delete
- Script deletion requires `--confirm` (default: dry-run); the plugin has NO
  automatic deletion path ANYWHERE (v2.11 red line, SCR-043 R6 — the
  v0.6.2 cron-mode `.tmp` auto-cleanup is removed): audit only proposes /
  inventories; cleanup is agent-decided and confirmation-gated
- No secrets/credentials in any file; stats.jsonl never records file contents
  or absolute external paths
- Plugin guard is fail-open (never crashes the agent)

### 8.2 Compatibility

- Python 3.11 (command: `python`)
- Platforms: Windows 10+, Linux, WSL, macOS — installable and runnable on all;
  scripts use os.path (portable) and are tested on the platform matrix;
  plugin path handling branches per platform (Windows MSYS mapping / casefold,
  POSIX normpath identity — SCR-006)
- Hermes plugin API (register(ctx), hooks, ctx.profile_name,
  register_skill, register_system_prompt_section)
- MUST run on local Hermes v0.20.0: the event bus is detected at runtime (the
  local v0.20.0 install already ships it); versions without it degrade
  silently — see section 9
- Agent Skills standard (SKILL.md frontmatter; `version` field removed, not
  required)

### 8.3 Code Style

- ASCII straight quotes only
- No emoji
- Forward slashes in paths (output and documentation)
- No comments in code unless explaining non-obvious logic
- Scripts: self-contained, no cross-imports (EXCEPTION:
  `workspace_resolver.py` shared Working Directory resolution module; see
  section 4 and engineering-constraints)
- Plugin: follows Hermes standard patterns (register(ctx), **kwargs, JSON
  returns)
- Discipline block: session-start one-shot, <=280 chars (v2.7; the
  always-on per-round-billed prompt channel is removed)

### 8.4 Architecture

- Skill and Plugin zero runtime coupling (co-packaged only)
- Scripts are agent-agnostic (any agent that can run Python can use them)
- No memo / cross-profile / multi-profile machinery
- Code identifiers unchanged (working_dir_root, --workspace, session_dir, rule_key;
  config key `allowlist` restructured in v2.7 to a `{files: [], dirs: []}`
  mapping of root-relative paths — the v2.6 flat `file:` / `prefix:` tagged
  list is removed clean-break;
  the dir-whip brand itself was renamed by SCR-030, see 4.5)
- Hermes-specific concepts explicitly marked for future Pi adaptation
- No hardcoded absolute paths in logic (config/templates use placeholders)

---

## 9. Upstream Dependencies

| Capability | Status on local Hermes v0.20.0 | v0.2.0 behavior |
|------------|-------------------------------|-----------------|
| `register_skill()` (opt-in loading) | available (unused in v0.1.0) | used; skill loaded explicitly by name |
| `register_system_prompt_section()` (≤4000 chars) | available | used; ≤200 字 |
| New hooks: post_tool_call / post_approval_response / subagent_start / subagent_stop | available | used (observation) |
| pre_command hook | available (observer-only v1) | used for observation only; blocking interception is a Phase 2 reservation |
| Manifest v2 fields (manifest_version, api_version, provides_hooks, emits) | available | declared |
| `hermes plugins install` (manifest v2) | local v0.20.1 installer gate: manifest_version <= 1 (plugins_cmd.py:737-750); runtime loader accepts v2 | native install verified after `hermes update`; manual copy meanwhile (SCR-025) |
| Inter-plugin event bus (`ctx.emit`/`subscribe`) | present in local v0.20.0 (version-dependent) | in v0.2.0 scope: runtime capability detection; emit active when available, silent degradation otherwise |
| pre_command blocking interception | upstream middleware #64204/#64231 | OUT OF v0.2.0 SCOPE (Phase 2 reservation) |
| Community plugin index (hermes-plugin-index repo) | NOT created (upstream) | BLOCKED; native URL install only |
| Capability declarations + consent flow | available (registry covers only override/platform-action surfaces) | NOT declared: register_skill / hooks / tools / emit are not capability-gated; no consent screen |

---

## 10. Phase 2 Reservations

Not implemented in v0.2.0. Design decisions should not block these:

| Item | Notes |
|------|-------|
| Community plugin index | re-evaluate when the official repo exists |
| pre_command blocking interception | enable when upstream middleware #64204/#64231 lands |
| Event-bus consumers | build on the bus once a bus-enabled Hermes is in use |
| Pi extension | skill is agent-agnostic; scripts are CLI tools; only the guard layer needs an adapter |

---

## Changelog

| Date | Change | Reason |
|------|--------|--------|
| 2026-08-02 | Initial draft | Grilling session decisions |
| 2026-08-06 | v1.1 — Unified guard chain (SCR-001/002/004) | Joint design review + gap fixes |
| 2026-08-06 | v1.1 errata — consistency fixes (7 items) | Doc review findings |
| 2026-08-06 | SCR-005 — working_dir_root config source clarification | docs/spec-change-005 |
| 2026-08-06 | SCR-005 addendum — resolving-source INFO log | User decision 2026-08-06 |
| 2026-08-06 | SCR-005 task 13.6 executed — source INFO log implemented | Task 13.6 |
| 2026-08-06 | SCR-006 — path normalization step 0 (MSYS mapping + drive inheritance) | docs/spec-change-006 |
| 2026-08-09 | v1.2 — SCR-011 public installation (memo-based validation, native install channel, quick commands) | docs/spec-change-011 |
| 2026-08-09 | SCR-013 — dir-whip-config.yaml migrates to HERMES_HOME/dir-whip/ | docs/spec-change-013 |
| 2026-08-09 | v1.2 errata — consistency fixes (8 items) | Doc review findings |
| 2026-08-09 | Root-file whitelist unified across layers (guard + audit read allowed_root_files) | User decision 2026-08-09 |
| 2026-08-09 | v1.3 — Phase 8 implemented (SCR-011/013); 435 tests passed; skills_guard verdict = safe | Phase 8 completion |
| 2026-08-09 | Memo model revision (skill read-only; plugin sole writer) | User decisions 2026-08-09 |
| 2026-08-09 | Registration durability (config-first via set_config_value) | User decision 2026-08-09 |
| 2026-08-09 | v1.2 consistency revision — 9 fixes (session-scoped allowlist, gate fail-closed, curl -O approve tier, etc.) | Doc review findings |
| 2026-08-10 | v1.4 — Deployment revision (SCR-015/017): flat repo layout, install.sh, distribution section | SCR-015/017 design docs |
| 2026-08-13 | v2.0 FROZEN — user approved the spec after three review rounds (scope revision / upstream-source audit / subagent strategy). Status: authoritative v0.2.0 baseline; SCR-024 in implementation phase (testing-standards.md v0.2.0 rewritten; tasks.md Phase 2) | User decision 2026-08-13 |
| 2026-08-13 | v2.0 subagent errata (user-confirmed, source-verified): child sessions DO fire on_session_start (same conversation loop, no subagent guard; subagent_start precedes the child's first turn) — defense = child_session_ids gate (5.4/5.16: skip allowlist clear / fail-open reset / reminder injection for child sessions). Subagent file protocol detailed (Q1-Q3): parent ensures the target dir before delegating; subagent writes to the parent-passed dir (default .tmp/, Outputs/ or per-subagent subdir when explicitly passed); no self-created session dirs, no self-promotion; blocked subagent gets the parent-target fix variant and reports back (3.9/5.3/5.16/7.1/7.3) | 2026-08-13 subagent strategy confirmation |
| 2026-08-13 | v2.0 audit errata (source-verified against the installed hermes-agent): event bus present in local v0.20.0 (runtime detection, was listed as upstream-only); emit() takes bare names (namespace forced, namespaced name raises); pre_command field is `alias_used`; subagent_start fields `child_role`/`child_goal`; api_version is an integer; skill load path = qualified name `dir-whip:workspace-organization` (skill_view); capabilities/consent not applicable (no declaration, no consent screen); install --force does tmp-replace (no stale .archive residue); script profile resolution designed (dir-whip-config -> HERMES_SESSION_PROFILE -> profile enumeration + TERMINAL_CWD candidate root -> fail-open; explicit --workspace equality / CWD containment, longest match); cron uses the qualified skill name; 8.1 audit-deletes-only-in-cron clarified; 7.2 gains embedded .tmp cleanup criterion; doctor warns on override != terminal.cwd; event payloads privacy-aligned; subagent_stop defined; allowlist clear top-level-session only; 4.4/5.2/5.4/5.7/5.13/5.14/5.15/5.16/6.3/7.5/8.1/8.2/9/10 updated | 2026-08-13 audit vs upstream source (hermes-agent @ 9460cc11d) |
| 2026-08-13 | v2.0 errata — scope revision: cross-platform (Windows 10+ / Linux / WSL / macOS) and the inter-plugin event bus moved INTO the v0.2.0 scope (bus: conditional activation on a bus-enabled Hermes, silent degradation otherwise); pre_command blocking interception stays OUT of scope (observer-only; Phase 2 reservation). 1.3/1.4/5.14/8.2/9/10/7.2/7.5 updated | User decision 2026-08-13 |
| 2026-08-13 | v2.0 — v0.2.0 rewrite (feedback/04 grilling, Q1-Q15): single-profile (cross-profile/memo/Shared Space/Standalone Mode removed); terminal guard simplified (coarse tiers, uncertain -> allow + log); dir-whip-config sole config source with working_dir_root inversion (TERMINAL_CWD removed); skill bundled into the plugin (register_skill, opt-in; SKILL.md version field removed) + discipline prompt ≤500 字; root-forbid triple + classification + [Reason]/[Next] template; scripts reduced to create_session_dir/audit_workspace (+workspace_resolver), --workspace equality validation, fail-open degradation; cron governance retained (A4 reversed); structured logging + stats.jsonl (5MB rollover, privacy) + status/stats/doctor + dir-whip:* events (silent degrade) + pre_command observation + subagent observation; installer removed, native install only, manifest v2, no pack, community index BLOCKED; terminology "Default Working Directory" -> "Working Directory". Status: DRAFT | 2026-08-13 grilling session (internal/feedback/04) |
| 2026-08-13 | SCR-026 profile-home layout (live finding): at runtime Hermes sets HERMES_HOME to the PROFILE DIRECTORY itself for non-default profiles (e.g. HERMES_HOME=.../profiles/learn; proven by stats.jsonl landing in the profile home), so the profile config is HERMES_HOME/config.yaml there — the 4.4/5.5 step-2 path is layout-aware (root-home layout: HERMES_HOME/profiles/<name>/config.yaml; profile-home layout: HERMES_HOME/config.yaml). Before the fix, learn/job-hunt profiles failed resolution and the guard was silently disabled (fail-open) | 2026-08-13 live test (learn profile root write not blocked) |
| 2026-08-13 | SCR-027 session-scoped resolution (live finding): a desktop agent process registers the plugin under the ACTIVE profile and the per-process cache served later sessions of a DIFFERENT profile (session 20260813_223050_dbe71b: default-profile session showed learn's root; its stats landed in learn's home). 5.8 "resolution ONCE at register()" is revised: the Working Directory is RE-RESOLVED per top-level session at on_session_start from ctx.profile_name (session-scoped cache; child sessions inherit the parent value; fail-open never keeps a stale value); 5.5 step-2 additionally handles a "default" session while HERMES_HOME is a named profile's dir (default home = two levels up); stats.jsonl placement follows the session profile's home (5.13) | 2026-08-13 live test (desktop default-profile session status showed learn) |
| 2026-08-13 | SCR-028 cross-platform path handling (WSL live finding): Windows-style targets (drive-rooted, MSYS/Cygwin, backslash-rooted) follow WINDOWS normalization and case-insensitive containment on ANY host (8.2/SCR-006 clarification — a WSL/Git-Bash session can carry Windows-style roots); targets rooted in any of those forms never get joined onto the base (posixpath.isabs is insufficient on POSIX hosts) | 2026-08-13 live test (27.6 WSL POSIX suite, 19 failures fixed) |
| 2026-08-14 | SCR-030 project rename workspace-guard → dir-whip (full brand/code/config rename; stats.jsonl, config.py, skill name workspace-organization and guard-judgment identifiers unchanged; historical surfaces preserved) | docs/scr-029-030-plan.md |
| 2026-08-14 | SCR-029 command simplification — single subcommand-less /dir-whip merged report (Plan A); status/stats/doctor subcommands removed; stats display code deleted, backend unchanged; version display added (plugin.yaml source, unknown fallback) | feedback/05 (Q1-Q4) |
| 2026-08-14 | SCR-031 report field label redesign (5.7): `Guard` -> `State`; `terminal_guard` -> `Terminal Guard`; `exempt_paths` -> `Exempt Paths`; `allowed_root_files` -> `Root Allowlist` (incl. `(strict empty whitelist)` -> `(strict empty allowlist)`); `self-check` -> `Health`; `stats file` -> `Stats File`. Config keys and values unchanged; `Working Directory` and `WARNING` labels unchanged | User request 2026-08-14 (README revision round) |
| 2026-08-14 | v2.0 final update pass: spec activated (ACTIVE) then re-frozen (FROZEN) at the implementation-complete state. SCR-025 registered — native distribution via `hermes plugins install shawVV1992/dir-whip/dir-whip --enable`; the local CLI v0.20.1 installer gates manifest_version <= 1, so native install is verified after `hermes update` and manual copy is used meanwhile (6.1/9 updated); SCR-032 verification-scope decision: 7.5 criterion 5 revised (macOS live verification removed — user decision; Linux/macOS remain supported per 8.2); ADR 0004 default-branch wording corrected (6.1 already current) | User decisions 2026-08-14 (activation + verification scope + distribution) |
| 2026-08-22 | v2.1 — SCR-033 terminal false-positive fix (feedback/06, design reviewed): chain-aware target extraction (`;` emitted as a separator; `&&` / `;` / `|` / newlines are chain boundaries; per-segment target extraction; chained writes now detected); redirect targets may not start with `=` (`>=` residue); `/dev/null` / `/dev/stdout` / `/dev/stderr` exempt before normalization (no verdict/stats event, no drive inheritance); commands containing `<<` demoted to the uncertain tier (allow + log). Windows drive inheritance otherwise unchanged (SCR-006). python/shutil uncertain-tier write detection declared a non-goal. Spec activated (ACTIVE) then re-frozen (FROZEN) | User decision 2026-08-22 (feedback/06 SCR review) |
| 2026-08-22 | v2.2 — SCR-034 root write audit (new feature, design reviewed): second terminal-discipline backbone observing filesystem deltas instead of parsing command intent. New 5.18: snapshot/diff mechanism (terminal-only, file entries only, shared classification chain), L1 fire-once notice via transform_tool_result (context hygiene hard constraint), L2 write-audit-* statistics + events, L3 pending-violation latch (write-class tools blocked until remediation), L4 auto-move (default off). Config keys write_audit / write_audit_entry_cap / write_audit_autofix-reserved (5.6); rule_keys write-audit-violation / write-audit-gate-block (5.13); events dir-whip:write-audit-* (5.14); measured performance ~0.1-0.3ms per audit round, entry cap 2000. Backstops SCR-033 heredoc/`=` pre-check gaps. Spec activated (ACTIVE) then re-frozen (FROZEN) | User decision 2026-08-22 (SCR-034 design review) |
| 2026-08-22 | SCR-034/033 consolidated (process note, no spec text change): SCR-033 (5.10 false-positive fix) marked as consolidated into SCR-034 — both solve the same problem's two faces (pre-check over-blocking vs post-hoc under-catching). Unified dual-layer design in docs/scr-033-034-plan.md: front layer = the four SCR-033 pre-check repairs + `=` residue routes to terminal-write-uncertain log; audit layer = 5.18 ladder (which makes the front layer safe to be permissive: heredoc stays blanket-demoted, no body parsing). Spec content unchanged — 5.10 and 5.18 already hold both halves; this changelog line records only the registry/unity decision | User decision 2026-08-22 (consolidation) |
| 2026-08-22 | v2.3 — SCR-034 acceptance criteria added: section 7.6 "Terminal Write Discipline" (15 criteria, A1-A15 mapped to the unified proposal) completes the acceptance chain spec §7 → testing-standards matrix → test classes; version 2.2 → 2.3 (activated, edited, re-frozen) | User decision 2026-08-22 (design acceptance criteria for the unified solution) |
| 2026-08-22 | SCR-034 30.12 live finding (spec 5.18 L1 text clarified, no version bump): Hermes fires `transform_tool_result` BEFORE `post_tool_call` for the terminal tool, so the L1 notice read an empty pending set and never fired (the audit re-scan was post-only). Fix (code + regression test): the audit re-scan runs inside `transform_tool_result` before the notice reads the pending set; the `post_tool_call` re-scan stays as an order-agnostic no-op fallback (the pre snapshot is consumed exactly once regardless of hook order). Regression test `TestWriteAuditNotice::test_transform_runs_audit_before_post_notice_appears`; full suite 493 passed / 5 skipped; live re-verified (notice appended on the real machine) | 2026-08-22 live verification (30.12) |
| 2026-08-22 | Discipline Prompt constraint tightened (spec 3.7/5.17 + acceptance): ≤500 → ≤200 chars. The shipped prompt is 181 chars — fits the new bound. Spec activated (ACTIVE) then re-frozen (FROZEN) | User decision 2026-08-22 |
| 2026-08-24 | scr-033-034-plan.md archived to docs/archive/v0.3.0/ (SCR-035 initiation housekeeping); the 7.6 acceptance-id path reference swept accordingly. Process-only edit, no spec clause changed; version stays v2.3 FROZEN | SCR-035 archive sweep (2026-08-24) |
| 2026-08-24 | v2.4 — SCR-035 structural revision (spec ACTIVATED early per user decision; re-freeze at SCR-035 completion): 5.1 directory figure rewritten to the 11-module layout (register/hooks in `__init__.py`, guard/terminal/paths/events/audit/sessions/state/config/stats/report) with the one-way dependency note and dual-resolution parity note; 4.5 gains a forward note (dir_whip.py dissolves, config.py name persists); 5.7 report line-1 wording now "package-root plugin.yaml" (was "next to config.py"); 5.2 manifest example version -> placeholder. Behavior clauses UNCHANGED from frozen v2.3 — structural/documentation only | User decision 2026-08-24 (activate spec + reorganize per SCR-035) |
| 2026-08-24 | v2.4 FROZEN — re-frozen after SCR-035 structural revision pass. No behavior changes from v2.3 | User decision 2026-08-24 (freeze) |
| 2026-08-25 | v2.5 ACTIVE — SCR-037 v0.4.1: 5.6 D1 template default `["AGENTS.md"]`→`[]` (host `agent_config_mod` scanner `skills_guard.py:462`→dangerous block, shipped tree now zero agent-config literals, engineering-constraints red line), 5.7 `/dir-whip allow|remove|list` subcommands (reverses SCR-029 single-command, `args_hint` for gateway menus, `config_writer` row-level edit), 4.2 audit whitelist wording. Spec activated 2026-08-25, re-freeze pending 37.7 verification | User decision 2026-08-25 |
| 2026-08-25 | v2.6 ACTIVE — SCR-037 amendment v2.6 B2 single-key allowlist (BREAKING): `exempt_paths` + `allowed_root_files` removed as config keys, no backward compat (user 2026-08-25 B2 decision, feedback/09); single unified `allowlist: []` with discriminated `file:<basename>` | `prefix:<abs-path>` (prefix may end with /). 5.6 template replaced, 5.3 Tier0 = allowlist prefix OR runtime-allowlist / root-file = allowlist file, 5.7 `/dir-whip allow <file|prefix:PATH|PATH/>` intelligently discriminates (no slash→file, slash/prefix:→prefix), report single `Allowlist: Files: ...  Prefixes: ...` line, 5.18 audit aligned to single key, 5.11/5.13/5.14/6.2/7.x/8.4 swept. Old keys deleted. Spec activated 2026-08-25, re-freeze pending | User decision 2026-08-25 (B2 clean break) |
| 2026-08-26 | v2.7 ACTIVE — SCR-039 v0.5.0 (feedback/10): (1) prompt-channel rework — Always-on Discipline Prompt removed (3.7/5.17), conditional session-start discipline block added (5.4: `discipline_applies` predicate + `agent_cwd_fn` slot wired to host `resolve_agent_cwd`; <=280 chars lock; report Reminder status line injected/skipped-outside/skipped-child/unavailable); (2) same-turn self-heal — `dir_whip_settle` tool (lazily registered, pending-set constrained, `.hermes/audit-quarantine/<ts>/` move, subagent-rejected), L1 notice upgraded with settle instruction, L3 latch note (remediation mv/rm blocked while latched -> tool channel), `pre_verify` continuation fallback (mixed-turn hard guarantee; pure-terminal turns = accepted limit + upstream suggestion registered in 9); (3) structured allowlist (BREAKING) — `allowlist` becomes `{files: [...], dirs: [...]}` mapping of working_dir_root-relative paths (dirs multi-level recursive subtree exemption; root itself and outside-root entries rejected; v2.6 flat `file:`/`prefix:` tagged list removed clean-break, legacy ignored fail-closed + `/dir-whip list` hint); `/dir-whip allow|remove|list` unified Files/Dirs two-section numbered presentation (bare remove enumerates current entries; disk-aware bare-name discrimination); block message gains placement-intent rule + allow_path hint. 2/3/4/5/6/7/8 swept. Re-freeze on completion | User decisions 2026-08-26 (feedback/10) |
| 2026-08-27 | v2.8 ACTIVE — SCR-040 v0.6.0 (scr-040-plan.md / feedback/11, two ruling rounds after the v0.5.0 release): (1) continuation-nudge rework — nudge message rewritten settle-first (shares the L1 remediation sentence helper, exact `dir_whip_settle(paths=[...])` call form, allow_path never mentioned) + plugin-side session-cumulative cap=3 (`PRE_VERIFY_NUDGE_CAP` hardcoded, reset at session start, host per-turn budget kept as outer bound — overturns the v2.7 "hook adds no throttling" clause); grounded in realhost experiment A (one-nudge conversion hard evidence + the agent's allow_path detour); (2) observability pack — 5.13 adds four stats rule_keys (`pre-verify-nudge` / `runtime-allowlist-add` / `session-reminder` / `write-audit-settle-rejected`; emits stay at 7) + a diagnostic log file dir-whip.log subsection (new module logsetup.py, profile-aware, DEBUG-full, CLH→stdlib→console three-tier degradation, 5MiB×3+delay+utf-8, absolute-path policy); (3) report surface rework (5.7 layout rewrite) — State values become enabled/disabled, Terminal Guard and Reminder lines removed, Allowlist multi-lined (header line + Files/Dirs one line each, valued sections indented 2 spaces, strict-empty keeps the single line), Health moved last with Good/brief-problem-list two states, a Debug Log line added near the end; (4) three-key de-configuration (BREAKING, 5.6) — terminal_guard/write_audit/write_audit_entry_cap stop being user config, internal constants (interception/audit always on, cap=2000), leftover keys completely ignored, and the `write_audit_autofix` reservation key is removed too (L4 becomes a keyless reserved direction); (5) shipped template comments corrected to the v2.7 structured tutorial. Terminology unified: external-facing 「dir-whip 续推兜底」/"dir-whip continuation nudge", host hook identifier pre_verify only in implementation contexts. (6) placement-wording de-ambiguation (R9, folded in 2026-08-27; realhost incident session 20260827_222411_245249: the block message arrow shorthand `deliverable -> Outputs/` was misread as a literal path template and the file landed at <session>/deliverable/Outputs/) — block message line reworded to `Then write the deliverable to Outputs/<filename> (or scratch to .tmp/<filename>).` (5.3), session-start reminder parenthetical reworded accordingly (3.7/5.4; 251 chars <= 280 lock), create_session_dir.py stdout gains a placement hint line (4.1 output contract added); docs first, code/tests land with the SCR-040 implementation batch (40.R9.1). session_dir_pattern judged worth developing, registered in feedback/11 for a separate SCR. Implemented and re-frozen 2026-08-28 (implementation batch 40.x complete; suite 644 passed / 5 skipped / 0 failed) | User decisions 2026-08-27 (scr-040-plan.md rulings: Plan A four records / log absolute paths / report rework + Health last + Allowlist multi-line / three-key de-configuration + leftovers completely ignored / version target 0.6.0) |
| 2026-08-28 | v2.9 ACTIVE — SCR-041 v0.6.1 (scr-041-plan.md, user decisions 2026-08-28 after the v0.6.0 realhost six-scenario verification): (1) re-scan semantics tightened (R1, 5.18) — the L3 settlement re-scan classifies pending paths by the CONFIG allowlist only (allowlist files/dirs + session-dir); a runtime-allowlist entry no longer settles a recorded violation (runtime exemption = prospective-only: future writes pass Tier 0, past violations stay pending); grounded in the v0.6.0 realhost escape (sessions 20260828_135518_31b5d6 / 20260828_135546_abff8d: the agent self-served allow_path after the nudge, pending cleared, cap=3 unreachable); (2) allow_path entry gating (R2, 5.11) — subagent calls rejected with a parent-guidance variant + stats rule_key allow-path-subagent-rejected (bus-skipped; sanction flows top-down only, user ruling), and the Working Directory root itself rejected as a path argument; (3) two-step user-confirmation protocol (R3, 5.11) — dir_whip_allow_path gains an optional confirm parameter: the first call returns a confirmation payload (risk + removal method + relay instruction) WITHOUT adding and records the path in a session-memory confirmation-issued set; confirm=true is honored only for an already-briefed path (mandatory two-step); removal method documented only (session-end auto-expiry; persistent exemptions via config, /dir-whip remove) — no revoke capability per user ruling; (4) L1/L3 message rewording (R4, 5.18) — the config-allowlist remediation option re-attributed to the USER (ask the user to add) with the latch-period freeze made explicit (all writes frozen incl. config edits), removing the false affordance that cost two gate-blocked turns in realhost. Version target v0.6.1 (patch — enforcement hardening). Re-frozen 2026-08-29 (implementation batch complete, suite 664 passed / 5 skipped / 0 failed) | User decisions 2026-08-28 (Plan A + subagent policy A + documented-removal-only + v0.6.1 patch) |
| 2026-08-29 | v2.10 ACTIVE — SCR-042 v0.6.2 (scr-042-plan.md, scope ruling 2026-08-29 after the feedback/13 skill-scripts security review): skill-scripts security hardening, 9 items in 5 requirement groups — (R1) deletion surface: --gate mode treats a Working Directory resolution failure as a gate failure (exit 2, reason on stderr, NO wakeAgent line, NOTHING deleted — an unresolved root never becomes a deletion target via the fail-open CWD fallback; interactive fail-open unchanged) and the .tmp cleanup scan boundary excludes symlinked session dirs and symlinked .tmp directories (two layers); (R2) import hardening: both scripts load the bundled workspace_resolver via importlib from the __file__-derived absolute path with sys.modules registration (CWD/PYTHONPATH shadowing can no longer divert the shared module); (R3) case parity: audit root-file allowlist matching reuses the resolver's Windows-casefold matcher (parity with allowlist.py by construction, ADR-0006), Outputs blacklist comparisons case-insensitive, .hermes root-dir exemption casefolded on Windows (mirrors report.py _case_eq); (R4) gate output contract: the wakeAgent line carries four keys always ({wakeAgent, violations, removed, failed} — cleanup failures visible to cron, N8 closed with M2) and gate+json stdout is line-by-line JSON (plain-text removal report dropped in json mode, failure details on stderr); (R5) robustness smalls: stdout/stderr reconfigure(errors=replace) in all three scripts (cp936 non-ASCII paths no longer crash), create_session_dir abspaths the resolved root (absolute-path contract, N2), hermes home falls back to the user home when LOCALAPPDATA is unset/empty (never CWD-relative, N7). 3.4/4.2/4.4/7.2 updated. Remaining 7 low-risk findings (L1/L2/N4/N5/N6/L3+N3/L4) registered, not disposed. Version target v0.6.2 (patch — no config surface change; hooks 9 / emits 7 unchanged). Re-frozen 2026-08-29 (implementation batch complete: commits cdeb3d6/dbd2baf/0d68c37/ac58340/cb8929e, suite 687 passed / 5 skipped / 0 failed) | User scope ruling 2026-08-29 (9 items: 2 must-fix + 5 recommended + 2 free-riders) |
| 2026-08-30 | v2.11 ACTIVE — SCR-043 v0.6.3 (scr-043-plan.md, feedback/14 classify-chain & governance design finalization 2026-08-30): (R1) classification chain T0-T4 (5.3) — scope first: outside-root (excluding the root itself) always external-write before any exemption; under-root priority runtime (T1) > config (T2, dual rule_keys kept) > session dir (T3); T4 blocks including the root itself (rel == "." -> root-file); (R2) external-write log/event routing sinks to the emit layer as a geometric test (normalize_target + within_working_dir, outcome string as fallback); (R3) allow_path registration gating (5.11) — outside-root rejection R2c (self-explaining verbatim + stats rule_key allow-path-external-rejected, bus-skipped, 7 emits unchanged), empty-path rejection (an empty entry prefix-matched everything), add-layer value-domain assertion via an optional working_dir_root parameter injected by the caller (config never imports verdict, ADR-0007); (R4) runtime allowlist segment-boundary matching — docs no longer exempts docs_secret (pathlib is_relative_to rejected: one lexical basis); (R5) audit quarantine relocates to <dir-whip home>/audit-quarantine/<ts>/ (profile-aware, out of the workspace root); L1/nudge message paths updated; audit .hermes root-dir exemption removed (guard adds none); /dir-whip list gains a quarantine-path line; (R6) gate = pure audit + wake — .tmp deletion execution and delete=args.gate removed, wakeAgent payload two keys always {wakeAgent, violations} (removed/failed retired — breaking for strict cron consumers), H1 unresolved-root exit 2 kept as cron failure visibility, R1 symlink scan boundary kept as inventory correctness, interactive proposal becomes a read-only inventory. All 7 feedback/14 items disposed (C1-C4 + 2 new findings + G1; no backlog). 3.4/4.2/5.3/5.11/5.18/7.2/7.3/8.1 updated. Version target v0.6.3 (patch per user ruling 2026-08-30 — behavior changes flagged in release notes; hooks 9 / emits 7 unchanged). Re-frozen 2026-08-30 (implementation batch complete: commits 67b8409/39ca5b3, suite 710 passed / 5 skipped / 0 failed) | User version ruling 2026-08-30 (v0.6.3 patch line, SCR-042 precedent; feedback/14 design finalization) |
