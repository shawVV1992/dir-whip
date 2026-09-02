# dir-whip installed (v0.6.4)

**Plugin guard**: Active after next Hermes restart. Terminal writes are
intercepted via chain-aware extraction (splits on `&&` / `;` / `|` /
newlines); root violations blocked with a fix-it message. Device paths
(`/dev/null`, etc.) are exempt; heredoc commands are demoted to allow+log.
Resolvable `mkdir`, `curl -o`, and `wget -O` targets are also judged
(rule_keys `terminal-mkdir` / `terminal-download`).
Blocks now carry a uniqueness line (one Session Directory per conversation;
a second creation attempt is blocked) and, when a non-compliant directory
for the target already exists, a conditional orphan-relocation line.

**Write audit**: A post-hoc snapshot diff catches root writes the front
guard lets through. The L1 notice names the file and the remediation;
further writes are frozen until the file is moved or removed.

**Bundled skill**: the workspace-organization skill ships with the plugin.
Load it explicitly when you need the full discipline reference; a short
conditional session-start reminder (injected only when the agent CWD is
inside the Working Directory and no active project covers it) covers
day-to-day behavior.

**Session-start orphan scan**: at every top-level session start (after
the reminder injection) the plugin scans the Working Directory root for
entries left outside any Session Directory, judged by the same classify
chain (allowlisted and compliant Session Directories are auto-exempt).
Advise-only: it never blocks or deletes; orphans are reported once with
create-then-relocate guidance, and any scan error is silently skipped.

**Quick command**:
    /dir-whip   # merged report: version, State, Working Directory +
                # source, Allowlist block, WARNING, Stats File, Debug
                # Log, Health

**Debug log**: a dedicated diagnostic log (dir-whip.log) is written
under HERMES_HOME/dir-whip/ at DEBUG level; the /dir-whip report shows
the exact path.

**Tools**: dir_whip_allow_path — allow a user-specified path for this
session (two-step user confirmation: the first call returns a briefing
payload without adding; re-call with confirm=true after the user approves;
prospective-only — never clears a recorded violation; subagent calls,
empty paths, and registrations outside the Working Directory are rejected
with a self-explaining message — the value domain is paths INSIDE the
Working Directory; writes outside it need no entry, they are allowed and
logged). dir_whip_settle — lazily registered on the first write-audit
notice; moves flagged root files into the audit quarantine
(`<dir-whip home>/audit-quarantine/`; same-turn self-heal).

**Configure** (optional): edit HERMES_HOME/dir-whip/dir-whip-config.yaml
(structured allowlist mapping {files, dirs}, working_dir_root override).

**Allowlist**: template ships with a strict empty structured allowlist.
No workspace rules file is allowlisted by default; add entries via slash
command without hand-editing YAML:
    /dir-whip allow <number|name|path>  # add one entry (disk-aware: dir -> dirs, file -> files)
    /dir-whip allow 1,3           # add by numbered candidates
    /dir-whip list                # show current allowlist (Files: ... Dirs: ...)
    /dir-whip remove <number|name> # remove entry
Non-existent paths need --create (confirm-create protocol); outside-root /
root-itself inputs are rejected with guidance. See spec 5.7 for the full
slash surface (config_writer does row-level edits preserving comments).

**Profiles**: plugin is per-profile opt-in. After install, verify each
profile's config (default: HERMES_HOME/config.yaml; named:
HERMES_HOME/profiles/<name>/config.yaml) contains `dir-whip` in
`plugins.enabled` — the native installer writes it via
hermes_cli/plugins_cmd.py _save_enabled_set on `--enable`. If a profile
lacks the entry, run `hermes plugins enable dir-whip` or reinstall with
`hermes plugins install shawVV1992/dir-whip/dir-whip --enable` in that
profile context. Manual copy of dir-whip/ into plugins/dir-whip/ without
.install-metadata.json is unsupported (no metadata, no enabled row).

**Install source**: native path is `shawVV1992/dir-whip/dir-whip` (SDIR
install). The SCR-025 manifest gate is lifted on current Hermes — native
install is the primary path.

**Verify**: Start a new Hermes session. Try writing a file to the Working
Directory root — it should be blocked with a helpful message. If the write
lands via a command the front guard lets through (e.g. heredoc), the audit
catches it and you will see the L1 notice in the conversation.
