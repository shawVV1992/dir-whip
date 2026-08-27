# dir-whip installed

**Plugin guard**: Active after next Hermes restart. Terminal writes are
intercepted via chain-aware extraction (splits on `&&` / `;` / `|` /
newlines); root violations blocked with a fix-it message. Device paths
(`/dev/null`, etc.) are exempt; heredoc commands are demoted to allow+log.

**Write audit**: A post-hoc snapshot diff catches root writes the front
guard lets through. The L1 notice names the file and the remediation;
further writes are frozen until the file is moved or removed.

**Bundled skill**: the workspace-organization skill ships with the plugin.
Load it explicitly when you need the full discipline reference; a short
conditional session-start reminder (injected only when the agent CWD is
inside the Working Directory and no active project covers it) covers
day-to-day behavior.

**Quick command**:
    /dir-whip   # merged report: version, state, Working Directory +
                # source, config detail, reminder status, health, stats file

**Tools**: dir_whip_allow_path — allow a user-specified path for this
session. dir_whip_settle — lazily registered on the first write-audit
notice; moves flagged root files into the audit quarantine (same-turn
self-heal).

**Configure** (optional): edit HERMES_HOME/dir-whip/dir-whip-config.yaml
(structured allowlist mapping {files, dirs}, working_dir_root override,
terminal_guard, write_audit, write_audit_entry_cap).

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
