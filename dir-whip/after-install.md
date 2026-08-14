# dir-whip installed

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
(exempt_paths, allowed_root_files, working_dir_root override, terminal_guard).

**Verify**: Start a new Hermes session. Try writing a file to the Working
Directory root — it should be blocked with a helpful message.
