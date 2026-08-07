#!/usr/bin/env python3
"""S1: Create a session directory.

Creates YYYYMMDD_HHMMSS[_TaskName]/ containing Outputs/ and .tmp/.
Prints the absolute path of the created directory (forward slashes) as
a single stdout line.

Exit codes:
  0 = created successfully
  1 = parameter error (workspace does not exist)
  2 = target already exists OR workspace boundary validation failed
"""

import argparse
import datetime
import os
import re
import sys

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
MAX_TASK_NAME_LEN = 80


def sanitize_task_name(name):
    """Replace Windows-illegal filename chars with underscore, truncate to 80."""
    name = ILLEGAL_CHARS.sub("_", name)
    return name[:MAX_TASK_NAME_LEN]


def validate_working_directory(path):
    """Check that path is a valid Default Working Directory."""
    if not os.path.isdir(path):
        return False, "directory does not exist"
    if not os.path.isfile(os.path.join(path, "AGENTS.md")):
        return False, "not a valid Default Working Directory (missing AGENTS.md)"
    return True, None


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
    args = parser.parse_args(argv)

    workspace = args.parent if args.parent is not None else args.workspace
    workspace = os.path.abspath(workspace)

    if not os.path.isdir(workspace):
        sys.stderr.write("error: workspace directory does not exist: %s\n" % workspace)
        return 1

    valid, msg = validate_working_directory(workspace)
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
