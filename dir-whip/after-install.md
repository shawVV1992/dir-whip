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
always-on discipline prompt covers day-to-day behavior.

**Quick command**:
    /dir-whip   # merged report: version, state, Working Directory +
                # source, config detail, health, stats file path

**Tool**: dir_whip_allow_path — allow a user-specified path for this
session.

**Configure** (optional): edit HERMES_HOME/dir-whip/dir-whip-config.yaml
(exempt_paths, allowed_root_files, working_dir_root override, terminal_guard,
write_audit, write_audit_entry_cap).

**Verify**: Start a new Hermes session. Try writing a file to the Working
Directory root — it should be blocked with a helpful message. If the write
lands via a command the front guard lets through (e.g. heredoc), the audit
catches it and you will see the L1 notice in the conversation.
