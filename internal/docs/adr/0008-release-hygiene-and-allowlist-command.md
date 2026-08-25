# Release Hygiene and Allowlist Command Surface (2026-08-25)

SCR-037 v0.4.1 must fix two linked defects: the shipped `dir-whip-config.yaml`
template hits the host's `agent_config_mod` scanner (dir-whip — skills_guard.py:462, any
occurrence of `AGENTS.md` / `CLAUDE.md` / `.cursorrules` / `.clinerules` in a
scanned text file → CRITICAL persistence → dangerous verdict → community-source
install blocked, `--force` does not override), and per-profile installs were
hand-copied without writing `plugins.enabled`, so the guard never registered
(feedback/09). The same SCR adds the first persistent allowlist management
surface (feedback/07 demand 7).

## D1 — Template default `[]` + release-hygiene red line

**Decision.** Ship `allowlist: []` (strict empty, spec 5.6 D1 v2.6) with discriminated entries `file:<basename>` | `prefix:<abs-path>` (forward slashes, e.g. `file:README.md`, `prefix:E:/HermesWorkspace/learn/projects/foo`; bare no-slash is `file:`, bare with slash is `prefix:`). Old keys `exempt_paths` / `allowed_root_files` deleted per user 2026-08-25 B2 clean break, no backward compat. Forbid the four agent-config literals in any shipped text file under `dir-whip/` (`.yaml/.yml/.json/.toml/.md/.py`). Comments use generic phrasing ("workspace rules file") and a `TestReleaseHygiene` sweep fails the build if a literal appears.

**Considered options.** (1) Keep `["AGENTS.md"]` and ask the host to exempt dir-whip — rejected: scanner is host-owned, literal matching intentional, exemption fragile. (2) Move default into code fallback — rejected: template is the single source. (3) Rename literal — rejected: obfuscation. (4) Keep dual keys `exempt_paths` + `allowed_root_files` — rejected: B2 collapses to single `allowlist`, cleaner and explicitly breaking.

**Consequences.** Fresh installs get strict empty (every root file blocked except session dirs / `prefix:` entries); the workspace rules file is already protected by the host's own gate (#53), so visible change is near zero. Existing runtime configs are untouched (SCR-013: forced reinstalls never overwrite runtime copy) but old dual keys are now ignored (B2, no compat). `TestReleaseHygiene` covers the red line; `engineering-constraints.md` records it.

## D2 — Allowlist management via slash command, not agent tool or Dashboard

**Decision.** Add `/dir-whip allow|remove|list` (SCR-037) as the 0.4.1 surface: `allow` without args lists numbered root-file candidates, `allow <file|prefix:PATH|PATH/>` intelligently discriminates (no slash -> `file:` entry, slash or `prefix:` prefix -> `prefix:` entry, batch `<name|1,3>` supported), `remove <file|prefix:PATH|PATH/>` and `list` operate on the single `allowlist`. Mutations go via shared `config_writer` (row-level YAML edit preserving comments), `list` shows current allowlist (Files + Prefixes). Handler receives `raw_args` (plugins.py:2122) and host dispatches on first token (`report.py:230`). Gateway menus via `args_hint` (commands.py:640). No agent tool `dir_whip_add_root_file`; Dashboard file-tree deferred to SCR-038.

**Considered options.** (1) Agent tool — rejected: self-authorization vector (prompt injection -> allowlist poisoning); guard stays user-authoritative only. (2) Dashboard panel now (manifest.json + api.py, web_server.py:18370, `window.__HERMES_PLUGINS__.register`) — deferred: needs JS bundle and profile-selector (#40); would delay unblock. Slash covers TUI + GUI chat in ~1 day, correct baseline. (3) Dashboard only — rejected: no TUI coverage.

**Consequences.** All surfaces get a user-typed, approval-free path never via agent. Spec 5.7 revised; `TestAllowlistCommand` covers parsing, discriminated validation, row-level edit, and narrow cache refresh.

## D3 — Row-level YAML edit, not YAML round-trip

**Decision.** `config_writer` edits the file as text (regex for the `allowlist:` key, following `report.py:83` precedent), rewriting only that line as a flow list of discriminated entries `file:` / `prefix:` or appending the key. A full YAML load-then-dump would strip comments and reformat the user's file.

**Consequences.** Comments elsewhere preserved; writer tested against missing-key, present-key, and already-present-entry cases (including `prefix:` trailing-slash normalization, forward slashes, casefold on Windows).

## D4 — Skill-side enablement precheck

**Decision.** `audit_workspace.py` gains a precheck that locates the current profile's `config.yaml` (inline layout-aware `_profile_config_path` shape test, `config.py:292-311`, not importing `workspace_resolver.py` per #55 boundary), and reports three states: `enabled` / `not-enabled` (warn + `hermes plugins enable dir-whip` guidance) / `disabled`. Precheck is unchanged in mechanism but now targets the single `allowlist` key (v2.6 B2). Output only, no exit-code change.

**Consequences.** The "plugin not loaded, user sees nothing" gap from feedback/09 is closed without widening the cross-import exception.

## Amendment 2026-08-25 — v2.6 B2 single-key allowlist (BREAKING)

Date 2026-08-25, user decision B2 clean break: dual keys `exempt_paths` + `allowed_root_files` deleted, replaced by single unified `allowlist: []` with discriminated entries `file:<basename>` | `prefix:<abs-path>` (no backward compat). Spec sections updated: 5.6 (sole config source + discriminated matching), 5.3 (guard Tier 0 + file/prefix split), 5.7 (slash intelligent file-vs-prefix handling, batch, report `Allowlist: Files:/Prefixes:`), 5.18 (audit reads `allowlist` `file:` subset). Shipped template `dir-whip-config.yaml` default is `allowlist: []`; `engineering-constraints.md` Release hygiene red line updated accordingly.
