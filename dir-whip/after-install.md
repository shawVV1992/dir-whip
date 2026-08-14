# dir-whip installed

**Plugin guard**: Active after next Hermes restart. Blocks file writes to the
Working Directory root outside Session Directories (whitelist files exempt).
External paths are allowed and logged.

**Bundled skill**: the workspace-organization skill ships with the plugin.
Load it explicitly when you need the full discipline reference; a short
always-on discipline prompt covers day-to-day behavior.

**Quick commands**:
    /dir-whip status   # effective config + source
    /dir-whip stats    # this session's interception statistics
    /dir-whip doctor   # configuration self-check

**Tool**: dir_whip_allow_path — allow a user-specified path for this
session.

**Configure** (optional): edit HERMES_HOME/dir-whip/dir-whip-config.yaml
(exempt_paths, allowed_root_files, working_dir_root override, terminal_guard).

**Verify**: Start a new Hermes session. Try writing a file to the Working
Directory root — it should be blocked with a helpful message.
