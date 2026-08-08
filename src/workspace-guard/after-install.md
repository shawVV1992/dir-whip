# workspace-guard installed

**Plugin guard**: Active after next Hermes restart.
Blocks write_file/patch/terminal writes outside Session Directories and
escalates cross-profile writes for approval.

**Skill**: installed via `hermes skills install` into the skills directory;
the scripts validate workspaces against the profile workspace memo.

**Quick commands**:
- `/workspace-guard workspace_status` — show the memo: profiles + workspaces
- `/workspace-guard workspace_update` — rebuild the memo manually

**Tool**: `workspace_guard_auto_update_workspace` auto-syncs the memo.

**Configure exempt paths** (optional):
Edit `~/.hermes/workspace-guard/guard-config.yaml` to add project
directories that live inside your Default Working Directory.

**Verify**: Start a new Hermes session. Try writing a file to the
workspace root — it should be blocked with a helpful message.