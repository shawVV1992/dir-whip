#!/usr/bin/env python3
"""S1: Create a session directory (v0.3.0, spec 4.1 + 4.4).

Creates YYYYMMDD_HHMMSS[_TaskName]/ containing exactly Outputs/ and .tmp/.
Prints the absolute path of the created directory (forward slashes) as
a single stdout line.

Boundary validation (SCR-011, spec 4.4): the --workspace target must EXACTLY
EQUAL the resolved Working Directory (dir-whip-config working_dir_root ->
HERMES_SESSION_PROFILE -> profile enumeration + TERMINAL_CWD candidate root ->
fail-open). The existence check runs FIRST (parameter error, exit 1); boundary
validation SECOND (exit 2). When --workspace is omitted, the script defaults
to the CWD and applies the 4.4 containment matching (equals / contained-in-one
/ nested longest-match); a resolution failure is fail-open -- the resolver
emits exactly ONE concise stderr WARNING and the script proceeds with the CWD.

Exit codes:
  0 = created successfully
  1 = parameter error (workspace directory does not exist)
  2 = target already exists OR --workspace does not match the resolved
      Working Directory
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

EXIT_OK = 0
EXIT_PARAM_ERROR = 1
EXIT_BOUNDARY_ERROR = 2


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
        default=None,
        help="Working Directory for the session dir; must equal the resolved Working Directory (default: current directory, containment-matched).",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    workspace_arg = args.parent if args.parent is not None else args.workspace
    if workspace_arg is None:
        # Omitted --workspace: default to CWD + 4.4 containment matching.
        # Resolve WITHOUT an explicit workspace so the CWD containment branch
        # (equals / contained-in-one / nested longest-match) applies. On
        # resolution failure the resolver emits exactly ONE stderr WARNING
        # (fail-open) and we fall back to the CWD -- no extra warnings here.
        resolved = workspace_resolver.resolve_working_dir_root()
        workspace = resolved if resolved is not None else os.getcwd()
    else:
        workspace = os.path.abspath(workspace_arg)

    # Existence check FIRST (spec 4.1): parameter error, before boundary
    # validation -- never emits the fail-open warning for a missing dir.
    if not os.path.isdir(workspace):
        sys.stderr.write("error: workspace directory does not exist: %s\n" % workspace)
        return EXIT_PARAM_ERROR

    # Boundary validation SECOND (spec 4.4): the explicit --workspace must
    # equal the resolved Working Directory; mismatch -> exit 2 (stderr stays
    # warning-free; the fail-open fallback already carried its ONE warning).
    if workspace_arg is not None:
        valid, reason = workspace_resolver.validate_workspace(workspace)
        if not valid:
            sys.stderr.write("error: %s\n" % reason)
            return EXIT_BOUNDARY_ERROR

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    task_name = sanitize_task_name(args.task_name)
    dir_name = timestamp if not task_name else "%s_%s" % (timestamp, task_name)

    target = os.path.join(workspace, dir_name)
    if os.path.exists(target):
        sys.stdout.write(target.replace(os.sep, "/") + "\n")
        return EXIT_BOUNDARY_ERROR

    os.makedirs(os.path.join(target, "Outputs"))
    os.makedirs(os.path.join(target, ".tmp"))

    sys.stdout.write(target.replace(os.sep, "/") + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
