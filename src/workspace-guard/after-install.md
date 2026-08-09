# workspace-guard installed

**Plugin guard**: Active after next Hermes restart.
Blocks write_file/patch/terminal writes outside Session Directories and
escalates cross-profile writes for approval.

**Skill**: installed via `hermes skills install` into the skills directory;
the scripts validate workspaces against the profile workspace memo. A full
memo sync runs automatically on the plugin's first load after restart.

**Quick commands**:
- `/workspace-guard workspace_status` — show the memo: profiles + workspaces
  + status + changed_at
- `/workspace-guard workspace_update` — rebuild the memo manually

**Tools**:
- `workspace_guard_auto_update_workspace` — auto-syncs the memo
  (agent-triggered when the memo is stale)
- `workspace_guard_register_workspace(profile, workspace)` — registers a new
  workspace for the current profile (two-step init flow: run
  `init_workspace.py` first to create the directory, then call this tool in
  the target profile's own session). It sets the profile's `terminal.cwd`
  (durable) and writes the memo entry.

**Configure exempt paths** (optional):
Edit `HERMES_HOME/workspace-guard/guard-config.yaml` (Windows:
`%LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml`; POSIX:
`~/.hermes/workspace-guard/guard-config.yaml`) to add project directories
that live inside your Default Working Directory. The copy inside the plugin
directory is a shipped template only, not the runtime config source; the
runtime config survives forced plugin reinstalls.

**Verify**: Start a new Hermes session. Try writing a file to the
workspace root — it should be blocked with a helpful message.
