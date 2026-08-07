#!/usr/bin/env python3
"""S2: Audit a workspace for structural compliance violations.

Scans a workspace root against 6 compliance checks and reports violations.
READ-ONLY: never creates, moves, deletes or modifies any file or directory.

Checks:
  1. Root level may only contain AGENTS.md as a file.
  2. No Outputs/ directory directly at workspace root.
  3. Root directories must be session dirs (YYYYMMDD_HHMMSS[_TaskName])
     or the whitelisted .hermes/ directory.
  4. Each valid session dir must contain both Outputs/ and .tmp/.
  5. Outputs/ must not contain build artifacts (__pycache__, *.pyc,
     node_modules, .DS_Store, Thumbs.db) at its immediate level.
  6. Script files (.py, .sh, .bat, .ps1) directly inside a session dir
     belong in .tmp/ instead.

Output:
  Plain text: one block per violation (check number, name, path,
  suggestion), or a single "OK" line when compliant.
  --json: a JSON array of violation objects, or [] when compliant.

Exit codes:
  0 = compliant (no violations)
  1 = violations found
  2 = parameter/path error (e.g. target directory does not exist)
"""

import argparse
import datetime
import json
import os
import re
import sys

ALLOWED_ROOT_FILES = ("AGENTS.md",)
WHITELISTED_ROOT_DIRS = (".hermes",)
SESSION_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")
OUTPUTS_DIR = "Outputs"
TMP_DIR = ".tmp"
SCRIPT_EXTENSIONS = (".py", ".sh", ".bat", ".ps1")
BLACKLIST_NAMES = {
    "__pycache__": "directory",
    "node_modules": "directory",
    ".DS_Store": "file",
    "Thumbs.db": "file",
}


def to_fwd(path):
    """Convert a path to forward slashes for stable output."""
    return path.replace(os.sep, "/")


def is_session_name(name):
    """True if name is YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_TaskName with a real timestamp."""
    if not SESSION_NAME_RE.match(name):
        return False
    try:
        datetime.datetime.strptime(name[:15].replace("_", ""), "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def check_root_files(root, violations):
    for entry in os.scandir(root):
        if entry.is_file() and entry.name not in ALLOWED_ROOT_FILES:
            violations.append({
                "check": 1,
                "name": "Root-level files",
                "path": to_fwd(entry.path),
                "suggestion": "Only AGENTS.md is allowed at the workspace root; move other files into a session dir.",
            })


def check_root_outputs(root, violations):
    path = os.path.join(root, OUTPUTS_DIR)
    if os.path.isdir(path):
        violations.append({
            "check": 2,
            "name": "Root-level Outputs directory",
            "path": to_fwd(path),
            "suggestion": "Deliverables belong inside a session dir's Outputs/; move this directory into a session.",
        })


def check_root_session_format(root, violations):
    for entry in os.scandir(root):
        if not entry.is_dir() or entry.name in WHITELISTED_ROOT_DIRS:
            continue
        if not is_session_name(entry.name):
            violations.append({
                "check": 3,
                "name": "Session directory format",
                "path": to_fwd(entry.path),
                "suggestion": "Rename to YYYYMMDD_HHMMSS_TaskName or YYYYMMDD_HHMMSS.",
            })


def check_session_structure(session, violations):
    for sub in (OUTPUTS_DIR, TMP_DIR):
        if not os.path.isdir(os.path.join(session, sub)):
            violations.append({
                "check": 4,
                "name": "Session subdirectories",
                "path": to_fwd(session),
                "suggestion": "Session dir must contain both Outputs/ and .tmp/; create the missing %s/." % sub,
            })


def check_outputs_content(outputs, violations):
    for entry in os.scandir(outputs):
        kind = "directory" if entry.is_dir() else "file"
        if entry.name.lower() == "__pycache__" or entry.name.lower() == "node_modules":
            if kind != "directory":
                continue
        if entry.name in BLACKLIST_NAMES or (
            kind == "file" and entry.name.endswith(".pyc")
        ):
            violations.append({
                "check": 5,
                "name": "Outputs content",
                "path": to_fwd(entry.path),
                "suggestion": "Build artifacts do not belong in Outputs/; move %s into .tmp/ or remove it." % entry.name,
            })


def check_session_scripts(session, violations):
    for entry in os.scandir(session):
        if entry.is_file() and entry.name.lower().endswith(SCRIPT_EXTENSIONS):
            violations.append({
                "check": 6,
                "name": "Misplaced script files",
                "path": to_fwd(entry.path),
                "suggestion": "Intermediate scripts belong in the session's .tmp/ directory.",
            })


def audit(root):
    """Run all checks; return a list of violation dicts."""
    violations = []
    check_root_files(root, violations)
    check_root_outputs(root, violations)
    check_root_session_format(root, violations)

    for entry in os.scandir(root):
        if not entry.is_dir() or not is_session_name(entry.name):
            continue
        check_session_structure(entry.path, violations)
        check_session_scripts(entry.path, violations)
        outputs = os.path.join(entry.path, OUTPUTS_DIR)
        if os.path.isdir(outputs):
            check_outputs_content(outputs, violations)
    return violations


def print_plain(violations):
    if not violations:
        sys.stdout.write("OK\n")
        return
    for v in violations:
        sys.stdout.write("check %d: %s\n" % (v["check"], v["name"]))
        sys.stdout.write("path: %s\n" % v["path"])
        sys.stdout.write("suggestion: %s\n" % v["suggestion"])
        sys.stdout.write("---\n")


def print_json(violations):
    sys.stdout.write(json.dumps(violations) + "\n")


def validate_working_directory(path):
    """Check that path is a valid Default Working Directory."""
    if not os.path.isdir(path):
        return False, "directory does not exist"
    if not os.path.isfile(os.path.join(path, "AGENTS.md")):
        return False, "not a valid Default Working Directory (missing AGENTS.md)"
    return True, None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit a workspace root against structural compliance checks (read-only)."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Workspace root directory to audit (alias for --workspace).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Default Working Directory to audit (default: current working directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output violations as a JSON array instead of plain text.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Append wakeAgent JSON line for cron integration.",
    )
    args = parser.parse_args(argv)

    target = args.workspace if args.workspace is not None else args.root
    if target is None:
        target = os.getcwd()
    root = os.path.abspath(target)

    if not os.path.isdir(root):
        sys.stderr.write("error: target directory does not exist: %s\n" % to_fwd(root))
        return 2

    valid, msg = validate_working_directory(root)
    if not valid:
        sys.stderr.write("error: %s\n" % msg)
        return 2

    violations = audit(root)
    if args.json:
        print_json(violations)
    else:
        print_plain(violations)

    if args.gate:
        if violations:
            sys.stdout.write(json.dumps({"wakeAgent": True, "violations": len(violations)}) + "\n")
        else:
            sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
