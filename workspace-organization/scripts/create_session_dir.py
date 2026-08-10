#!/usr/bin/env python3
"""S1: Create a session directory.

Creates YYYYMMDD_HHMMSS[_TaskName]/ containing Outputs/ and .tmp/.
Prints the absolute path of the created directory (forward slashes) as
a single stdout line.

Workspace validation (SCR-011): the target must be a registered profile
workspace in the profile workspace memo; when the memo is unavailable and
no plugin is present, standalone mode trusts the provided --workspace
(with a stderr warning). A missing directory is a parameter error.

Exit codes:
  0 = created successfully
  1 = parameter error (workspace does not exist)
  2 = target already exists OR workspace validation failed
"""

import argparse
import datetime
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

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
MAX_TASK_NAME_LEN = 80


def sanitize_task_name(name):
    """Replace Windows-illegal filename chars with underscore, truncate to 80."""
    name = ILLEGAL_CHARS.sub("_", name)
    return name[:MAX_TASK_NAME_LEN]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a session directory YYYYMMDD_HHMMSS[_TaskName] with Outputs/ and .tmp/ subdirectories."
    )
    parser.add_argument(
        "task_name",
        nargs="?",
        default="",
        help="Optional task name; sanitized and truncated to 80 chars.",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Default Working Directory for the session dir (default: current working directory).",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help=argparse.SUPPRESS,
    )
    workspace_resolver.add_profile_arg(parser)
    args = parser.parse_args(argv)

    workspace = args.parent if args.parent is not None else args.workspace
    workspace = os.path.abspath(workspace)

    if not os.path.isdir(workspace):
        sys.stderr.write("error: workspace directory does not exist: %s\n" % workspace)
        return 1

    valid, msg = workspace_resolver.validate_workspace(workspace, profile=args.profile)
    if not valid:
        sys.stderr.write("error: %s\n" % msg)
        return 2

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    task_name = sanitize_task_name(args.task_name)
    dir_name = timestamp if not task_name else "%s_%s" % (timestamp, task_name)

    target = os.path.join(workspace, dir_name)
    if os.path.exists(target):
        sys.stdout.write(target.replace(os.sep, "/") + "\n")
        return 2

    os.makedirs(os.path.join(target, "Outputs"))
    os.makedirs(os.path.join(target, ".tmp"))

    sys.stdout.write(target.replace(os.sep, "/") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
