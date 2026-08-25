# Engineering Constraints (v0.3.0 basis, carried forward to v0.4.0)

Mandatory engineering constraints for dir-whip (in force for the v0.4.0
line, SCR-035). Constraint set
carried forward unchanged from v0.2.0 (archived in
`archive/v0.2.0/engineering-constraints.md`);
install.sh-specific constraints (echo/printf bytes, CR stripping, TTY
branches, `-p` always-passing, stale .archive cleanup) are obsolete — the
installer is removed. v0.4.0 addition (SCR-035, ADR-0007): core modules
import no host APIs; host capabilities enter only via the `__init__.py`
adapter injection.

## SKILL.md frontmatter

- First line `---` (no BOM, no leading blank line)
- `name`: lowercase + hyphens, <= 64 chars
- `description`: <= 1024 chars, trigger words within the first 57 chars;
  wording avoids "organize/clean up sessions" (session-librarian separation)
- Total length <= 100,000 chars
- `version` field REMOVED (plugin.yaml is the sole version)

## Discipline prompt

- <= 500 字, four elements (write classification / session-dir placement /
  root forbid / interception response), minimal — billed every round

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
  --workspace, allowlist, session_dir, rule_key) — allowlist entries are discriminated `file:` / `prefix:` (v2.6 B2)

## Release hygiene (SCR-037)

- Shipped plugin tree (`dir-whip/` subtree) must not contain agent-config filename literals `AGENTS.md` / `CLAUDE.md` / `.cursorrules` / `.clinerules` in any text file (`.yaml/.yml/.json/.toml/.md/.py`). The host's `agent_config_mod` rule (`skills_guard.py:462`) treats any occurrence as CRITICAL persistence → dangerous verdict → community source install blocked (`--force` does not override). Keep defaults/comments generic (e.g. “workspace rules file”) and cover with `TestReleaseHygiene`.
- Shipped `dir-whip-config.yaml` default for `allowlist` is `[]` (strict empty, spec 5.6 D1 v2.6, discriminated `file:` | `prefix:` — old keys `exempt_paths` / `allowed_root_files` deleted 2026-08-25 B2 clean break, no backward compat). The previous `["AGENTS.md"]` default hit the scanner (2026-08-25 `hermes plugins install` block, `dir-whip-config.yaml:19`). Shipped template must use single `allowlist: []`, not dual keys.

## Compatibility

- Python 3.11 (command: `python`)
- Windows 10+, Linux, WSL, macOS (scripts portable; plugin path handling
  branches per platform — SCR-006 rules)
- Runs on local Hermes v0.20.0 (88ab589f6); event bus activates only on a
  bus-enabled Hermes and degrades silently otherwise

Authoritative details: spec v2.0 sections 8 (constraints) and 9 (upstream
dependencies).
