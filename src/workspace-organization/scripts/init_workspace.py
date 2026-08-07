#!/usr/bin/env python3
"""S5: Initialize a profile workspace.

Creates <workspace>/<sanitized_name>/ and writes an AGENTS.md rules file
(built-in template or the content of --template). Prints the absolute
path of the workspace directory (forward slashes) as a single stdout line.

Boundary validation: EXEMPT (this script creates new workspaces).

Exit codes:
  0 = initialized successfully
  1 = execution error (e.g. --template file missing, permission denied)
  2 = parameter error (empty name after sanitization) or target exists
"""

import argparse
import os
import re
import sys

MAX_NAME_LEN = 80
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')

DEFAULT_TEMPLATE = """# Workspace Rules

## Session Directory Structure

Every conversation creates a session directory:

    YYYYMMDD_HHMMSS_TaskName/
    \u251c\u2500\u2500 Outputs/    <- formal deliverables only
    \u2514\u2500\u2500 .tmp/       <- intermediate files, safe to clean

## File Placement

- All deliverables go in a session dir's Outputs/
- Never save files to workspace root directly
- Shared space (<SHARED_SPACE_PATH>) requires explicit user confirmation
  # Configure your shared space path here

## Prohibitions

- No rm -rf, del /S/Q, bulk rename, or recursive delete
- No overwriting existing files without reading first
- No secrets or credentials in any file

## Conventions

- Absolute paths with forward slashes
- ASCII straight quotes only
- No emoji
"""


def sanitize_name(name):
    """Replace Windows-illegal filename chars with underscore, truncate to 80."""
    name = ILLEGAL_CHARS.sub("_", name)
    return name[:MAX_NAME_LEN]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Initialize a profile workspace with an AGENTS.md rules file."
    )
    parser.add_argument("name", help="Workspace name (= profile name).")
    parser.add_argument(
        "--workspace",
        default=None,
        help="Parent directory for the workspace (default: $HERMES_WORKSPACE_ROOT or CWD).",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Path to a custom AGENTS.md template file.",
    )
    args = parser.parse_args(argv)

    name = sanitize_name(args.name)
    if not name:
        sys.stderr.write("error: workspace name is empty after sanitization\n")
        return 2

    parent_arg = args.parent if args.parent is not None else args.workspace
    if parent_arg is None:
        # HERMES-SPECIFIC: HERMES_WORKSPACE_ROOT environment variable
        root = os.environ.get("HERMES_WORKSPACE_ROOT")
        parent_arg = root if root else os.getcwd()
    parent = os.path.abspath(parent_arg)
    if not os.path.isdir(parent):
        sys.stderr.write("error: parent directory does not exist: %s\n" % parent)
        return 1

    content = DEFAULT_TEMPLATE
    if args.template:
        template_path = os.path.abspath(args.template)
        if not os.path.isfile(template_path):
            sys.stderr.write("error: template file does not exist: %s\n" % template_path)
            return 1
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()

    target = os.path.join(parent, name)
    if os.path.exists(target):
        sys.stdout.write(target.replace(os.sep, "/") + "\n")
        return 2

    try:
        os.makedirs(target)
        # HERMES-SPECIFIC: .hermes/ directory whitelist
        with open(os.path.join(target, "AGENTS.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1

    sys.stdout.write(target.replace(os.sep, "/") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
