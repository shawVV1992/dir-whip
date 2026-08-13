#!/usr/bin/env python3
"""S2: Audit a workspace for structural compliance violations.

Scans the Working Directory root against 6 compliance checks and reports
violations. Boundary validation (spec 4.4): an explicit --workspace must
equal the resolved Working Directory; the default target is the resolved
Working Directory (guard-config override -> HERMES_SESSION_PROFILE ->
profile enumeration + TERMINAL_CWD candidate root), falling back to the
current directory with ONE concise stderr warning when the chain is
unresolvable (fail-open). A missing directory or a mismatch is a
parameter error.

Checks:
  1. Root level may only contain files on the workspace-guard allowed_root_files whitelist.
  2. No Outputs/ directory directly at workspace root.
  3. Root directories must be session dirs (YYYYMMDD_HHMMSS[_TaskName])
     or the whitelisted .hermes/ directory.
  4. Each valid session dir must contain both Outputs/ and .tmp/.
  5. Outputs/ must not contain build artifacts (__pycache__, *.pyc,
     node_modules, .DS_Store, Thumbs.db) at its immediate level.
  6. Script files (.py, .sh, .bat, .ps1) directly inside a session dir
     belong in .tmp/ instead.

Embedded .tmp cleanup (spec 3.4 / 8.1): age-based cleanup of session
.tmp/ directories runs inside the audit. Cron mode (--gate) deletes
expired entries automatically; interactive mode only proposes them
(never deletes without the user). The hidden --days flag sets the age
threshold (default 30). Deletion never touches Outputs/, session files
outside .tmp/, workspace root files, or the .tmp/ directory itself, and
never scans .tmp/ directories outside valid session dirs.

Output:
  Plain text: one block per violation (check number, name, path,
  suggestion), or a single "OK" line when compliant.
  --json: a JSON array of violation objects, or [] when compliant.
  --gate: regular output first, then the cleanup report, then a final
  JSON line {"wakeAgent": false} or {"wakeAgent": true, "violations": N}.
  Interactive --json keeps the plain JSON array (no cleanup proposal).

Exit codes:
  0 = compliant (no violations)
  1 = violations found
  2 = parameter/path error (missing directory, --workspace mismatch,
      invalid --days)
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import time

try:
    import workspace_resolver
except ImportError:
    # Dual import mode (mirrors the plugin guard): when run from outside the
    # scripts directory, make the shared module importable from this dir.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import workspace_resolver

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


def check_root_files(root, allowed, violations):
    for entry in os.scandir(root):
        if entry.is_file() and entry.name not in allowed:
            violations.append({
                "check": 1,
                "name": "Root-level files",
                "path": to_fwd(entry.path),
                "suggestion": "Only files listed in the workspace-guard allowed_root_files whitelist are allowed at the workspace root; move other files into a session dir.",
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


def audit(root, hh):
    """Run all checks; return a list of violation dicts."""
    allowed = workspace_resolver.allowed_root_files(hh)
    violations = []
    check_root_files(root, allowed, violations)
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


def find_tmp_entries(parent):
    """Sorted immediate entry paths inside each session .tmp/ directory.

    Only scans parent/<session-dir>/.tmp/ where the session dir name is a
    valid session name (real timestamp). Never recurses deeper and never
    scans .tmp/ directories outside session dirs.
    """
    entries = []
    for entry in os.scandir(parent):
        if not entry.is_dir() or not is_session_name(entry.name):
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


def cleanup_tmp(root, days, delete):
    """Find expired session .tmp/ entries; delete them when delete=True.

    Returns (items, failures). Deletion is limited to the contents of
    session .tmp/ directories: Outputs/, session files outside .tmp/,
    workspace root files and the .tmp/ directory itself are never
    touched.
    """
    items = [p for p in find_tmp_entries(root) if is_old(p, days)]
    failures = 0
    if delete:
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
    return items, failures


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit a workspace root against structural compliance checks (with embedded .tmp cleanup)."
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
        help="Working Directory to audit (default: resolved Working Directory, or the current directory on fail-open).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output violations as a JSON array instead of plain text.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Cron mode: auto-clean expired .tmp entries and append the wakeAgent JSON line.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    if args.days < 0:
        sys.stderr.write("error: --days must be a non-negative integer\n")
        return 2

    hh = workspace_resolver.hermes_home()
    target = args.workspace if args.workspace is not None else args.root
    if target is not None:
        root = os.path.abspath(target)
        if not os.path.isdir(root):
            sys.stderr.write("error: target directory does not exist: %s\n" % to_fwd(root))
            return 2
        valid, reason = workspace_resolver.validate_workspace(root, hh=hh)
        if not valid:
            sys.stderr.write("error: %s\n" % reason)
            return 2
    else:
        root = workspace_resolver.resolve_working_dir_root(hh=hh)
        if root is None:
            # Fail-open (spec 4.4 step 4): the resolver already emitted
            # exactly ONE concise stderr warning; fall back to CWD.
            root = os.getcwd()
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            sys.stderr.write("error: resolved Working Directory does not exist: %s\n" % to_fwd(root))
            return 2

    violations = audit(root, hh)

    if args.json:
        print_json(violations)
    else:
        print_plain(violations)

    items, failures = cleanup_tmp(root, args.days, delete=args.gate)
    if args.gate and items:
        if failures:
            sys.stderr.write(
                "error: %d item(s) could not be removed from .tmp directories\n" % failures
            )
        sys.stdout.write("Removed %d item(s) from .tmp directories.\n" % (len(items) - failures))
    elif items and not args.json:
        sys.stdout.write("Tmp cleanup proposal (cron mode auto-cleans):\n")
        for path in items:
            sys.stdout.write(to_fwd(path) + "\n")
        sys.stdout.write("Proposed %d item(s) for .tmp cleanup.\n" % len(items))

    if args.gate:
        if violations:
            sys.stdout.write(json.dumps({"wakeAgent": True, "violations": len(violations)}) + "\n")
        else:
            sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
