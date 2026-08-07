## workspace-guard installed

**Plugin guard**: Active after next Hermes restart.
Blocks write_file/patch operations outside Session Directories.

**Skill deployment** (required for full functionality):
Copy the skill to your skills directory. It ships in the repository at
`src/workspace-organization/`:

    # Windows
    Copy-Item -Recurse "src\workspace-organization" "%LOCALAPPDATA%\hermes\skills\workspace-organization"

    # Linux/macOS
    cp -r src/workspace-organization ~/.hermes/skills/workspace-organization

**Configure exempt paths** (optional):
Edit `~/.hermes/plugins/workspace-guard/guard-config.yaml` to add
project directories that live inside your Default Working Directory.

**Verify**: Start a new Hermes session. Try writing a file to the
workspace root — it should be blocked with a helpful message.
