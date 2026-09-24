#!/usr/bin/env python3
"""Cross-session metadata search over Session Directories -- stateless single-pass os.scandir over every session-format directory and its full tree (spec 4.6).

Locates files without an index (metadata only: session name / rel_path / size / mtime; content search stays the job of grep); filters --task / --name / --since / --until are AND-combined, ordering is session name DESCENDING then (casefold(rel_path), rel_path) ascending, --limit (default 50) applies after ordering with total counted BEFORE truncation. Domain excludes symlinked/junction session dirs, directory reparse points (never traversed) and allowlist ``dirs`` subtrees (SCR-049); boundary validation mirrors create_session_dir.py (spec 4.4) -- omitted --workspace resolves with exactly ONE resolver stderr WARNING (this script adds none), explicit --workspace checks existence first (exit 1) then mismatch (exit 2), and scan errors are fail-open (exit 0).

Layer: skill-subprocess
Refs: spec 4.4, spec 4.6, SCR-042, SCR-049
Key exports:
  - main -- CLI entry: parse args, validate the boundary, scan + filter + order + truncate, emit plain or JSON; exit 0/1/2 (spec 4.6).
  - scan -- enumerate the search domain: real session-name dirs, per-file lstat records, fail-open {path, error} entries (spec 4.6).
"""

import argparse
import datetime
import fnmatch
import importlib.util
import json
import os
import re
import stat
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

# SCR-049 (spec 4.6): script-local SESSION_NAME_RE copy -- the THIRD copy
# alongside audit_workspace.py and create_session_dir.py (the stdlib-only /
# zero-package-import boundary is kept; the copies are registered in 4.6).
SESSION_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")

EXIT_OK = 0
EXIT_PARAM_ERROR = 1
EXIT_BOUNDARY_ERROR = 2

DEFAULT_LIMIT = 50

# Windows FILE_ATTRIBUTE_* bits (stat.FILE_ATTRIBUTE_* exists on Windows
# only; the literal values keep the module importable on POSIX).
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_DATE_RE = re.compile(r"^\d{8}$")


class _SearchArgumentParser(argparse.ArgumentParser):
    """Parser whose parameter errors exit 1 (argparse defaults to 2).

    The spec 4.6 exit-code family keeps parameter errors at exit 1 and
    stdout silent (the create_session_dir.py precedent); usage and the
    error message still go to stderr.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(EXIT_PARAM_ERROR)


def to_fwd(path):
    """Convert a path to forward slashes for stable output."""
    return str(path).replace(os.sep, "/")


def is_session_name(name):
    """True if name is YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_TaskName with a real timestamp."""
    if not SESSION_NAME_RE.match(name):
        return False
    try:
        datetime.datetime.strptime(name[:15].replace("_", ""), "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def _format_mtime(timestamp):
    """Local-time mtime as YYYY-MM-DDTHH:MM:SS (spec 4.6 result field)."""
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%dT%H:%M:%S")


def _is_reparse_point(st):
    """True when an lstat result carries the directory reparse attribute.

    Covers Windows junctions / directory symlinks (st_file_attributes has
    FILE_ATTRIBUTE_REPARSE_POINT); always False on POSIX.
    """
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def _dir_exempt(name, dirs_entries):
    """True when a root entry's first segment matches an allowlist dirs entry.

    v2.7 R9 first-segment subtree exemption; casefolded on Windows only
    (POSIX compares case-sensitively, spec 4.6 v2.22 — aligned with
    4.2 audit_workspace.py and the plugin's allowlist matching); the
    entry list comes from the resolver's allowlist loading surface.
    """
    if not dirs_entries:
        return False
    name_cmp = name.casefold() if os.name == "nt" else name
    for entry in dirs_entries:
        first = str(entry).replace("\\", "/").split("/")[0]
        first_cmp = first.casefold() if os.name == "nt" else first
        if first_cmp and name_cmp == first_cmp:
            return True
    return False


def _task_matches(session_name, task):
    """Casefold substring over the TaskName segment (part after YYYYMMDD_HHMMSS_)."""
    if len(session_name) <= 16:
        return False
    return task.casefold() in session_name[16:].casefold()


def _name_matches(rel_path, pattern):
    """Casefold fnmatch over the file BASENAME only (directories never reach here)."""
    base = rel_path.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(base.casefold(), pattern.casefold())


def _collect_session_files(session_name, session_path, errors):
    """All files in a session tree with lstat metadata (spec 4.6).

    Iterative os.scandir walk consuming the scandir stat cache. Directory
    reparse points (symlinked dirs / Windows junctions) are never traversed;
    symlinked FILES are included with lstat size/mtime. Per-directory and
    per-entry OSErrors are captured as {path, error} and the scan continues.
    Returns records sorted by (casefold(rel_path), rel_path).
    """
    found = []
    stack = [session_path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            errors.append({"path": to_fwd(current), "error": str(exc)})
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append({"path": to_fwd(entry.path), "error": str(exc)})
                continue
            if stat.S_ISDIR(st.st_mode):
                # Real directory: recurse unless it is a reparse point
                # (Windows junction lstat mode is S_IFDIR + reparse bit).
                if not _is_reparse_point(st):
                    stack.append(entry.path)
                continue
            if os.name == "nt":
                # A symlink/junction to a directory reports the DIRECTORY
                # attribute without following -- never a result entry.
                if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_DIRECTORY:
                    continue
            elif stat.S_ISLNK(st.st_mode) and entry.is_dir():
                # POSIX classification only (one-level follow): a symlink
                # to a directory is not a file result; contents never walked.
                continue
            rel_path = os.path.relpath(entry.path, session_path).replace(os.sep, "/")
            found.append({
                "path": to_fwd(entry.path),
                "session": session_name,
                "rel_path": rel_path,
                "size_bytes": st.st_size,
                "mtime": _format_mtime(st.st_mtime),
            })
    found.sort(key=lambda record: (record["rel_path"].casefold(), record["rel_path"]))
    return found


def scan(workspace, dirs_entries):
    """Enumerate the search domain (spec 4.6).

    Returns (session_names, file_records, errors): session names are the
    REAL session-format directories in the domain, descending; records are
    in global order (session descending, then rel_path ascending). A
    ROOT-level scan failure yields one errors entry, empty records and an
    empty session list (fail-open; partial results are results).
    """
    sessions = []
    errors = []
    try:
        root_entries = list(os.scandir(workspace))
    except OSError as exc:
        errors.append({"path": to_fwd(workspace), "error": str(exc)})
        return sessions, [], errors
    for entry in root_entries:
        if _dir_exempt(entry.name, dirs_entries):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            errors.append({"path": to_fwd(entry.path), "error": str(exc)})
            continue
        # Only real directories: junction/symlink dirs are out of domain
        # (SCR-049 reparse-point exclusion); loose root files never match.
        if not stat.S_ISDIR(st.st_mode) or _is_reparse_point(st):
            continue
        if not is_session_name(entry.name):
            continue
        sessions.append(entry.name)
    sessions.sort(reverse=True)
    records = []
    for name in sessions:
        records.extend(
            _collect_session_files(name, os.path.join(workspace, name), errors)
        )
    return sessions, records, errors


def _matches(record, args):
    """AND-combined filter check (spec 4.6)."""
    if args.task is not None and not _task_matches(record["session"], args.task):
        return False
    if args.name is not None and not _name_matches(record["rel_path"], args.name):
        return False
    session_date = record["session"][:8]
    if args.since is not None and session_date < args.since:
        return False
    if args.until is not None and session_date > args.until:
        return False
    return True


def _calendar_date(value):
    """argparse type: YYYYMMDD + real calendar date, else a parameter error."""
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError("must be YYYYMMDD")
    try:
        datetime.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise argparse.ArgumentTypeError("must be a real calendar date (YYYYMMDD)")
    return value


def _positive_int(value):
    """argparse type: positive integer, else a parameter error."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a positive integer")
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    """CLI entry: parse args, validate the --workspace boundary, scan + filter Session Directory metadata, emit plain or --json output (spec 4.6)."""
    args = _build_parser().parse_args(argv)

    if args.task is not None and args.task == "":
        sys.stderr.write("error: --task must not be empty\n")
        return EXIT_PARAM_ERROR
    if args.name is not None and args.name == "":
        sys.stderr.write("error: --name must not be empty\n")
        return EXIT_PARAM_ERROR

    hh = workspace_resolver.hermes_home()
    workspace, code = _resolve_workspace(args, hh)
    if code is not None:
        return code

    # Allowlist dirs exclusion (spec 4.6): same loading surface as the audit;
    # a load failure returns the empty state inside the resolver and therefore
    # does NOT exclude (fail-open).
    dirs_entries = workspace_resolver.allowlist_state(hh).get("dirs") or []

    sessions, records, errors = scan(workspace, dirs_entries)
    matches = [record for record in records if _matches(record, args)]

    total = len(matches)
    truncated = total > args.limit
    results = matches[:args.limit]

    if args.json:
        _emit_json(results, total, truncated, sessions, errors)
    else:
        _emit_plain(results, total, truncated, errors)
    return EXIT_OK


def _build_parser():
    parser = _SearchArgumentParser(
        description="Search file metadata across Session Directories under the workspace root."
    )
    parser.add_argument(
        "--task", default=None,
        help="Casefold substring of the TaskName segment (sessions without a task name never match).",
    )
    parser.add_argument(
        "--name", default=None,
        help="Casefold glob over the file basename (directories never appear in results).",
    )
    parser.add_argument(
        "--since", default=None, type=_calendar_date,
        help="Inclusive session date lower bound, YYYYMMDD.",
    )
    parser.add_argument(
        "--until", default=None, type=_calendar_date,
        help="Inclusive session date upper bound, YYYYMMDD.",
    )
    parser.add_argument(
        "--limit", default=DEFAULT_LIMIT, type=_positive_int,
        help="Maximum results after ordering (default: %d)." % DEFAULT_LIMIT,
    )
    parser.add_argument(
        "--workspace", default=None,
        help="Working Directory to search (default: resolved Working Directory, or the current directory on fail-open).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output a single JSON document instead of plain lines.",
    )
    return parser


def _resolve_workspace(args, hh):
    """Resolve + validate the search workspace; (workspace, None) or
    (None, EXIT_PARAM_ERROR / EXIT_BOUNDARY_ERROR)."""
    if args.workspace is None:
        # Omitted --workspace: resolved Working Directory, or the CWD on
        # fail-open (the resolver emitted exactly ONE stderr WARNING; this
        # script adds none).
        resolved = workspace_resolver.resolve_working_dir_root(hh=hh)
        workspace = os.path.abspath(resolved) if resolved is not None else os.getcwd()
        return workspace, None
    workspace = os.path.abspath(args.workspace)
    # Existence check FIRST (parameter error, exit 1) -- never emits the
    # fail-open warning for a missing directory.
    if not os.path.isdir(workspace):
        sys.stderr.write(
            "error: workspace directory does not exist: %s\n" % to_fwd(workspace)
        )
        return None, EXIT_PARAM_ERROR
    # Boundary validation SECOND (exit 2 on mismatch; None -> fail-open
    # pass, the resolver already carried its ONE warning).
    valid, reason = workspace_resolver.validate_workspace(workspace, hh=hh)
    if not valid:
        sys.stderr.write("error: %s\n" % reason)
        return None, EXIT_BOUNDARY_ERROR
    return workspace, None


def _emit_json(results, total, truncated, sessions, errors):
    payload = {
        "results": results,
        "total": total,
        "truncated": truncated,
        "sessions_scanned": len(sessions),
        "errors": errors,
    }
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    )


def _emit_plain(results, total, truncated, errors):
    for record in results:
        sys.stdout.write(
            "%s  %d  %s\n"
            % (
                record["mtime"].replace("T", " ")[:16],
                record["size_bytes"],
                record["path"],
            )
        )
    if truncated:
        # Independent final line (spec 4.6); N = total - returned count.
        sys.stdout.write("(+%d more)\n" % (total - len(results)))
    if errors:
        sys.stderr.write(
            "note: %d unreadable entries skipped (use --json for details)\n"
            % len(errors)
        )


if __name__ == "__main__":
    sys.exit(main())
