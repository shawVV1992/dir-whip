#!/usr/bin/env python3
"""S5: Initialize a profile workspace.

Creates <parent>/<sanitized_name>/ (mkdir + sanitize only; no memo write,
no template file). Prints the absolute path of the created directory
(forward slashes), followed by a registration next-step when the plugin
is detected (standalone mode skips it).

Boundary validation: EXEMPT (this script creates new workspaces).

Exit codes:
  0 = initialized successfully
  1 = execution error (e.g. parent directory does not exist, permission denied)
  2 = parameter error (empty name after sanitization) or target exists
"""

import argparse
import os
import re
import sys

try:
    import workspace_resolver
except ImportError:
    # Dual import mode (mirrors the plugin guard): when run from outside the
    # scripts directory, make the shared module importable from this dir.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import workspace_resolver

MAX_NAME_LEN = 80
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_name(name):
    """Replace Windows-illegal filename chars with underscore, truncate to 80."""
    name = ILLEGAL_CHARS.sub("_", name)
    return name[:MAX_NAME_LEN]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Initialize a profile workspace (mkdir + sanitize only)."
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

    target = os.path.join(parent, name)
    if os.path.exists(target):
        sys.stdout.write(target.replace(os.sep, "/") + "\n")
        return 2

    try:
        os.makedirs(target)
    except OSError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1

    sys.stdout.write(target.replace(os.sep, "/") + "\n")
    if workspace_resolver.plugin_trace():
        # HERMES-SPECIFIC: registration is a plugin-owned write (active-profile
        # only); the script only prints the next step for the agent to run.
        sys.stdout.write(
            "Register it: call the plugin tool "
            "`workspace_guard_register_workspace('%s', '%s')` to add the "
            "workspace to the memo.\n" % (name, target.replace(os.sep, "/"))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
