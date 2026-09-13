#!/usr/bin/env python3
"""Create a Session Directory YYYYMMDD_HHMMSS[_TaskName] with the three-subdirectory skeleton Outputs/ + .tmp/ + Inputs/ (v2.18 SCR-049, spec 4.1).

stdout contract: absolute created path (line 1) + placement hint (line 2, spec 4.1 R9), emitted on success (exit 0) and on the exit-2 target-already-exists branch, silent on all other failure paths; boundary validation (SCR-011, spec 4.4) requires an explicit --workspace to EXACTLY EQUAL the resolved Working Directory (existence first, exit 1; mismatch exit 2), while the omitted form defaults to the CWD + 4.4 containment matching -- a resolution failure is fail-open with exactly ONE resolver stderr WARNING and the script proceeds with the CWD. Same-day advisory (SCR-048 R5; spec 4.1 v2.16/v2.17): ONE stderr note when same-day Session Directories already exist (newest first, capped at 3, "(+N more)" overflow); advisory only, creation is never blocked.

Layer: skill-subprocess
Refs: spec 4.1, spec 4.4, SCR-011, SCR-042, SCR-048, SCR-049
Key exports:
  - main -- CLI entry: create the three-subdirectory Session Directory under the validated workspace; exit 0 created / 1 parameter error / 2 target exists or boundary mismatch.
"""

import argparse
import datetime
import importlib.util
import os
import re
import sys

# SCR-042 H2: the bundled shared resolver is loaded from THIS script's own
# directory via an absolute path -- independent of sys.path / PYTHONPATH /
# CWD state, so a same-named workspace file can never hijack the module
# (python -m / PYTHONPATH shadow / embedded-import vectors). Registering
# sys.modules["workspace_resolver"] keeps direct-import and in-process
# callers on a single instance. No fallback: a missing bundled file is a
# broken script and must fail loudly.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_resolver_spec = importlib.util.spec_from_file_location(
    "workspace_resolver", os.path.join(_SCRIPT_DIR, "workspace_resolver.py")
)
workspace_resolver = importlib.util.module_from_spec(_resolver_spec)
sys.modules["workspace_resolver"] = workspace_resolver
_resolver_spec.loader.exec_module(workspace_resolver)

# SCR-042 M3: never crash on a non-UTF-8 console/pipe (e.g. cp936 with
# non-ASCII paths) -- encode errors degrade to replacement characters.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
MAX_TASK_NAME_LEN = 80

# SCR-048 R5 (spec 4.1): session-name detection is a script-local copy of
# the audit_workspace.py precedent (SESSION_NAME_RE + strptime) -- scripts
# stay stdlib-only with no package imports (the dual-implementation
# boundary is kept). v2.18 SCR-049: a THIRD script-local copy lives in
# search_workspace.py (registered in spec 4.6); the script-local-copy
# discipline is unchanged.
SESSION_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

EXIT_OK = 0
EXIT_PARAM_ERROR = 1
EXIT_BOUNDARY_ERROR = 2

# Placement hint (spec 4.1 R9 output contract; v2.18 SCR-049 three-layer
# wording): stdout line 2 on exit 0 and on the exit-2 "target already
# exists" branch. Other failure paths (exit 1; exit-2 boundary mismatch)
# stay silent on stdout.
PLACEMENT_HINT = (
    "Write deliverables to Outputs/<filename>, scratch to .tmp/<filename>, "
    "imported or reused files to Inputs/<filename>."
)

# Same-day advisory (spec 4.1 v2.16 SCR-048 R5; v2.17 amend — advisory
# precision): ONE stderr note line when the workspace already holds same-day
# Session Directories. Advisory only: creation is never blocked, exit codes
# and the two-line stdout contract are unchanged; when the note coexists with
# the fail-open resolution WARNING the warning comes first (the resolver wrote
# it earlier). v2.17: the wording is deliberately CONDITIONAL (this
# stdlib-only script cannot know conversation identity — it teaches the
# one-per-conversation rule instead of asserting reuse); the list is NEWEST
# first, capped at 3 entries, with the overflow shown as "(+N more)".
ADVISORY_NOTE_TEMPLATE = (
    "note: workspace already has Session Directory(s) today (newest first): %s; "
    "reuse the existing one if this is the same conversation; "
    "one Session Directory per conversation\n"
)


def sanitize_task_name(name):
    """Replace Windows-illegal filename chars with underscore, truncate to 80."""
    name = ILLEGAL_CHARS.sub("_", name)
    return name[:MAX_TASK_NAME_LEN]


def is_session_name(name):
    """True if name is YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_TaskName with a real timestamp."""
    if not SESSION_NAME_RE.match(name):
        return False
    try:
        datetime.datetime.strptime(name[:15].replace("_", ""), "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def same_day_session_dirs(workspace, today):
    """Newest-first names of same-day session-format DIRECTORIES under workspace."""
    names = []
    try:
        entries = os.listdir(workspace)
    except OSError:
        return names
    for name in sorted(entries, reverse=True):
        if name[:8] != today or not is_session_name(name):
            continue
        if os.path.isdir(os.path.join(workspace, name)):
            names.append(name)
    return names


def advisory_note(workspace):
    """Append ONE same-day reuse note to stderr (advisory only; spec 4.1).

    v2.17: names are newest-first, capped at 3 entries; the overflow is
    shown as "(+N more)".
    """
    today = datetime.datetime.now().strftime("%Y%m%d")
    names = same_day_session_dirs(workspace, today)
    if names:
        shown = list(names[:3])
        if len(names) > 3:
            shown.append("(+%d more)" % (len(names) - 3))
        sys.stderr.write(ADVISORY_NOTE_TEMPLATE % ", ".join(shown))


def main(argv=None):
    """CLI entry: create the three-subdirectory Session Directory under the validated workspace; exit 0 created / 1 parameter error / 2 target exists or boundary mismatch (spec 4.1, spec 4.4)."""
    parser = argparse.ArgumentParser(
        description="Create a session directory YYYYMMDD_HHMMSS[_TaskName] with Outputs/, .tmp/ and Inputs/ subdirectories."
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
        # SCR-042 N2: the printed path is ALWAYS absolute (spec 4.1) --
        # a relative working_dir_root config value is anchored to the CWD.
        workspace = os.path.abspath(resolved) if resolved is not None else os.getcwd()
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
        sys.stdout.write(PLACEMENT_HINT + "\n")
        return EXIT_BOUNDARY_ERROR

    # SCR-048 R5 (spec 4.1): same-day advisory AFTER validation and the
    # target-absent check, BEFORE creation. The exit-2 boundary-mismatch
    # path never reaches here; creation is not blocked, the exit code is
    # unchanged, and stdout stays exactly two lines.
    advisory_note(workspace)

    # v2.18 SCR-049 (spec 4.1): the skeleton is exactly three
    # subdirectories, created in this order: Outputs/, .tmp/, Inputs/.
    os.makedirs(os.path.join(target, "Outputs"))
    os.makedirs(os.path.join(target, ".tmp"))
    os.makedirs(os.path.join(target, "Inputs"))

    sys.stdout.write(target.replace(os.sep, "/") + "\n")
    sys.stdout.write(PLACEMENT_HINT + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
