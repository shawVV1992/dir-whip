#!/usr/bin/env python3
"""S3: Clean expired files from .tmp/ directories inside session directories.

Scans a workspace root for session directories (YYYYMMDD_HHMMSS[_TaskName])
and lists/deletes files and subdirectories inside their .tmp/ directory that
have not been modified for N days.

DRY-RUN BY DEFAULT: without --confirm the script only reports what would be
deleted and never touches the filesystem. Only with --confirm are items
actually removed. Deletion is limited to the contents of .tmp/ directories;
the .tmp/ directory itself, session Outputs/ directories, other session
content and workspace root files are never touched.

Exit codes:
  0 = completed successfully (dry-run, or all items deleted)
  1 = execution error (some items could not be deleted)
  2 = parameter error (e.g. parent directory does not exist, invalid --days)
"""

import argparse
import datetime
import os
import re
import shutil
import sys
import time

SESSION_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")
TMP_DIR = ".tmp"


def to_fwd(path):
    """Convert a path to forward slashes for stable output."""
    return path.replace(os.sep, "/")


def _is_valid_session_name(name):
    """Check if name is a valid session directory name with a real timestamp."""
    if not SESSION_NAME_RE.match(name):
        return False
    try:
        datetime.datetime.strptime(name[:15].replace("_", ""), "%Y%m%d%H%M%S")
        return True
    except ValueError:
        return False


def find_tmp_entries(parent):
    """Return a sorted list of immediate entry paths inside each session .tmp/.

    Only scans parent/<session-dir>/.tmp/ directories where the session dir
    name is a valid session directory name: matches YYYYMMDD_HHMMSS[_TaskName]
    AND parses as a real timestamp via strptime. Never recurses deeper and
    never scans .tmp/ directories outside session dirs.
    """
    entries = []
    for entry in os.scandir(parent):
        if not entry.is_dir() or not _is_valid_session_name(entry.name):
            continue
        tmp_dir = os.path.join(entry.path, TMP_DIR)
        if not os.path.isdir(tmp_dir):
            continue
        for item in os.scandir(tmp_dir):
            entries.append(item.path)
    return sorted(entries)


def is_old(path, days):
    """True if the entry has not been modified for `days` days or longer."""
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return False
    return age >= days * 86400


def validate_working_directory(path):
    """Check that path is a valid Default Working Directory."""
    if not os.path.isdir(path):
        return False, "directory does not exist"
    if not os.path.isfile(os.path.join(path, "AGENTS.md")):
        return False, "not a valid Default Working Directory (missing AGENTS.md)"
    return True, None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Clean expired files from .tmp/ directories inside session "
            "directories (dry-run by default; --confirm required to delete)."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Age threshold in days (default: 30). Items not modified for N days are eligible.",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Default Working Directory to scan (default: current working directory).",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without deleting (this is the default behavior).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete eligible items instead of only reporting them.",
    )
    args = parser.parse_args(argv)

    if args.days < 0:
        sys.stderr.write("error: --days must be a non-negative integer\n")
        return 2

    workspace = args.parent if args.parent is not None else args.workspace
    workspace = os.path.abspath(workspace)

    if not os.path.isdir(workspace):
        sys.stderr.write("error: workspace directory does not exist: %s\n" % to_fwd(workspace))
        return 2

    valid, msg = validate_working_directory(workspace)
    if not valid:
        sys.stderr.write("error: %s\n" % msg)
        return 2

    items = [p for p in find_tmp_entries(workspace) if is_old(p, args.days)]

    if not items:
        sys.stdout.write("Nothing to clean.\n")
        return 0

    if not args.confirm:
        for path in items:
            sys.stdout.write(to_fwd(path) + "\n")
        sys.stdout.write("Dry run: %d item(s) would be removed.\n" % len(items))
        return 0

    failures = 0
    for path in items:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            sys.stdout.write(to_fwd(path) + "\n")
        except OSError as exc:
            failures += 1
            sys.stderr.write("error: could not delete %s: %s\n" % (to_fwd(path), exc))

    sys.stdout.write("Removed %d item(s).\n" % (len(items) - failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
