# Engineering Constraints (v0.3.0 basis, carried forward to v0.5.0)

Mandatory engineering constraints for dir-whip (in force for the v0.5.0
line, SCR-039). Constraint set
carried forward unchanged from v0.2.0 (archived in
`archive/v0.2.0/engineering-constraints.md`);
install.sh-specific constraints (echo/printf bytes, CR stripping, TTY
branches, `-p` always-passing, stale .archive cleanup) are obsolete — the
installer is removed. v0.4.0 addition (SCR-035, ADR-0007): core modules
import no host APIs; host capabilities enter only via the `__init__.py`
adapter injection. v0.5.0 additions (SCR-039, ADR-0009): session-start
discipline block replaces the always-on prompt channel; `dir_whip_settle`
is the only agent-side remediation channel; `allowlist` is a structured
root-relative mapping.

## SKILL.md frontmatter

- First line `---` (no BOM, no leading blank line)
- `name`: lowercase + hyphens, <= 64 chars
- `description`: <= 1024 chars, trigger words within the first 57 chars;
  wording avoids "organize/clean up sessions" (session-librarian separation)
- Total length <= 100,000 chars
- `version` field REMOVED (plugin.yaml is the sole version)

## Discipline block (v2.7)

- The always-on per-round-billed prompt channel is REMOVED
  (`register_system_prompt_section` no longer called).
- Session-start discipline block: one-shot `ctx.inject_message`, conditional
  (session CWD inside working_dir_root; fail-open injects), locked text
  verbatim, `len <= 280` chars (~70 tokens), test-locked by character count.
- No pointer micro-prompt: the guard's block message is the deterministic
  re-teaching point.

## Scripts

- Self-contained, no cross-imports — EXCEPT `workspace_resolver.py` (shared
  Working Directory resolution, read-only)
- `--workspace` must equal the resolved Working Directory (mismatch -> exit 2)
- Resolution failure -> fail-open (fall back to CWD + one stderr WARNING)
- Hermes-specific concepts marked with comments

## Safety

- No `rm -rf` / `del /S/Q`; delete = report-only by default; audit never
  deletes by itself (proposes actions only)
- No secrets; stats.jsonl never records file contents or absolute external paths
- Plugin guard is fail-open (never crashes the agent); hook callbacks never raise

## Code style

- ASCII straight quotes, no emoji, forward slashes in paths
- No comments unless explaining non-obvious logic
- Code identifiers frozen by the terminology decision (working_dir_root,
  --workspace, allowlist, session_dir, rule_key) — `allowlist` is a structured
  mapping `{files: [...], dirs: [...]}` of paths RELATIVE to working_dir_root
  (v2.7; the v2.6 flat `file:` / `prefix:` tagged list is removed clean-break)

## Release hygiene (SCR-037)

- Shipped plugin tree (`dir-whip/` subtree) must not contain agent-config filename literals `AGENTS.md` / `CLAUDE.md` / `.cursorrules` / `.clinerules` in any text file (`.yaml/.yml/.json/.toml/.md/.py`). The host's `agent_config_mod` rule (`skills_guard.py:462`) treats any occurrence as CRITICAL persistence → dangerous verdict → community source install blocked (`--force` does not override). Keep defaults/comments generic (e.g. “workspace rules file”) and cover with `TestReleaseHygiene`.
- Shipped `dir-whip-config.yaml` default for `allowlist` is the strict empty
  structured mapping (`files: []` / `dirs: []`, spec 5.6 v2.7 — the v2.6 flat
  tagged list and the pre-v2.6 keys `exempt_paths` / `allowed_root_files` are
  deleted clean-break, no backward compat). The previous `["AGENTS.md"]`
  default hit the scanner (2026-08-25 `hermes plugins install` block,
  `dir-whip-config.yaml:19`). Shipped template must use the structured
  mapping, not legacy forms.
- Allowlist validation red line (v2.7): `files` entries are basenames only;
  `dirs` entries are relative multi-level paths under working_dir_root —
  `..` segments, absolute/drive forms, empty values, and `.` (the root
  itself) are rejected at add time and ignored at load time; nothing outside
  the root can ever be allowlisted. Legacy flat values are ignored
  fail-closed and reported by `/dir-whip list`.

## Compatibility

- Python 3.11 (command: `python`)
- Windows 10+, Linux, WSL, macOS (scripts portable; plugin path handling
  branches per platform — SCR-006 rules)
- Runs on local Hermes v0.20.0 (88ab589f6); event bus activates only on a
  bus-enabled Hermes and degrades silently otherwise

Authoritative details: spec v2.0 sections 8 (constraints) and 9 (upstream
dependencies).
