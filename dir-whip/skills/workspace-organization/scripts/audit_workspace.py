#!/usr/bin/env python3
"""S2: Audit a workspace for structural compliance violations.

Scans the Working Directory root against 6 compliance checks and reports
violations. Boundary validation (spec 4.4): an explicit --workspace must
equal the resolved Working Directory; the default target is the resolved
Working Directory (dir-whip-config override -> HERMES_SESSION_PROFILE ->
profile enumeration + TERMINAL_CWD candidate root). When the chain is
unresolvable, interactive mode falls back to the current directory with
ONE concise stderr warning (fail-open); cron mode (--gate) REFUSES to
fall back -- it exits 2 with no wakeAgent line (SCR-042 H1, reframed by
SCR-043 R6 as cron failure visibility). A missing directory
or a mismatch is a parameter error.

Checks:
  1. Root level may only contain files on the dir-whip allowlist files
     whitelist (structured mapping v2.7; legacy flat values ignored with
     a stderr hint).
  2. No Outputs/ directory directly at workspace root.
  3. Root directories must be session dirs (YYYYMMDD_HHMMSS[_TaskName])
     or a directory covered by an allowlist dirs entry (recursive
     subtree exemption). SCR-043 R5: the former .hermes/ whitelist is
     removed -- a leftover .hermes/ directory is flagged like any other
     non-session directory (the audit quarantine lives in the dir-whip
     home now).
  4. Each valid session dir must contain both Outputs/ and .tmp/.
  5. Outputs/ must not contain build artifacts (__pycache__, *.pyc,
     node_modules, .DS_Store, Thumbs.db) at its immediate level.
  6. Script files (.py, .sh, .bat, .ps1) directly inside a session dir
     belong in .tmp/ instead.

Embedded .tmp inventory (spec 3.4 / 8.1; SCR-043 R6): expired session
.tmp/ entries (default age threshold 30 days, hidden --days flag) are
listed as a READ-ONLY proposal in interactive mode; the plugin never
deletes (zero auto-delete anywhere -- cleanup decisions belong to the
agent). Cron mode (--gate) outputs no expired list. The inventory
boundary never follows symlinks: a session-name symlink at the root and
a symlinked .tmp/ body are both excluded entirely (SCR-042 N1, kept as
inventory correctness -- never list outside content).

Output:
  Plain text: one block per violation (check number, name, path,
  suggestion), or a single "OK" line when compliant.
  --json: a JSON array of violation objects, or [] when compliant.
  --gate: regular output first, then a final JSON line
  {"wakeAgent": bool, "violations": N} -- exactly two keys (SCR-043 R6:
  the removed/failed cleanup keys are gone with auto-delete). In
  --gate + --json mode stdout is exactly two lines, each
  json.loads-able: the violations JSON array and the wakeAgent line.
  Interactive --json keeps the plain JSON array (no inventory
  proposal).

Exit codes:
  0 = compliant (no violations)
  1 = violations found
  2 = parameter/path error (missing directory, --workspace mismatch,
      invalid --days, or --gate with an unresolved Working Directory --
      cron failure visibility, SCR-042 H1)
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
import time

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
# non-ASCII paths) -- encode errors degrade to replacement characters
# (stderr too: error messages carry the same non-ASCII paths).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

SESSION_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")
OUTPUTS_DIR = "Outputs"
TMP_DIR = ".tmp"
SCRIPT_EXTENSIONS = (".py", ".sh", ".bat", ".ps1")
BLACKLIST_NAMES = {
    "__pycache__": "directory",
    "node_modules": "directory",
    ".ds_store": "file",
    "thumbs.db": "file",
}


# --- SCR-037 enablement precheck (spec 5.7, ADR-0008 D4) ---
# Inline layout-aware helpers (do NOT import workspace_resolver for this;
# stdlib only, per #55 boundary). Logic mirrors dir-whip/config.py:292-311
# and workspace_resolver.py:220-242.

def _precheck_hermes_home():
    """Inline hermes_home() (env-aware, stdlib only)."""
    env_home = (os.environ.get("HERMES_HOME") or "").strip()
    if env_home:
        return env_home
    if os.name == "nt":
        # SCR-042 N7: unset/empty LOCALAPPDATA falls back to the user home
        # so the precheck never reads config relative to the CWD.
        local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return os.path.join(local_app_data, "hermes")
        return os.path.join(os.path.expanduser("~"), "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _precheck_current_profile(hh):
    """Current profile: HERMES_SESSION_PROFILE wins, else infer from hh shape."""
    profile = (os.environ.get("HERMES_SESSION_PROFILE") or "").strip()
    if profile:
        return profile
    norm = os.path.normpath(str(hh))
    if os.path.basename(os.path.dirname(norm)) == "profiles":
        name = os.path.basename(norm)
        if name:
            return name
    return "default"


def _precheck_profile_config_path(hh, profile):
    """Layout-aware config.yaml path (both home layouts, per R2)."""
    if not profile or profile == "default":
        norm = os.path.normpath(str(hh))
        if os.path.basename(os.path.dirname(norm)) == "profiles":
            return os.path.join(os.path.dirname(os.path.dirname(norm)), "config.yaml")
        return os.path.join(hh, "config.yaml")
    norm = os.path.normpath(str(hh))
    if os.path.basename(norm) == profile and os.path.basename(os.path.dirname(norm)) == "profiles":
        return os.path.join(hh, "config.yaml")
    return os.path.join(hh, "profiles", profile, "config.yaml")


def _precheck_parse_plugins_lists(path):
    """Parse plugins.enabled / plugins.disabled from config.yaml.

    Uses yaml.safe_load when available, else a minimal line scan that
    handles both inline (enabled: [dir-whip]) and block
    (enabled:\\n  - dir-whip) forms. Returns (enabled, disabled) lists.
    """
    # Try PyYAML first
    try:
        import yaml  # noqa: PLC0415

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            plugins = data.get("plugins")
            if isinstance(plugins, dict):
                en = plugins.get("enabled")
                dis = plugins.get("disabled")
                enabled = [str(x) for x in en if isinstance(x, str)] if isinstance(en, list) else []
                disabled = [str(x) for x in dis if isinstance(x, str)] if isinstance(dis, list) else []
                return enabled, disabled
        return [], []
    except ImportError:
        pass
    except Exception:
        pass
    # Fallback line scan (stdlib only)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return [], []
    enabled = []
    disabled = []
    in_plugins = False
    current_key = None
    in_list = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw.startswith("plugins:"):
            in_plugins = True
            current_key = None
            in_list = False
            continue
        if in_plugins:
            if raw[0] not in (" ", "\t"):
                if ":" in raw:
                    in_plugins = False
                    current_key = None
                    in_list = False
                    continue
            if stripped.startswith("enabled:"):
                rest = stripped[len("enabled:"):].strip()
                if rest:
                    inner = rest.strip("[]").strip()
                    if inner:
                        for part in inner.split(","):
                            part = part.strip().strip("'\"")
                            if part:
                                enabled.append(part)
                    current_key = None
                    in_list = False
                else:
                    current_key = "enabled"
                    in_list = True
                continue
            if stripped.startswith("disabled:"):
                rest = stripped[len("disabled:"):].strip()
                if rest:
                    inner = rest.strip("[]").strip()
                    if inner:
                        for part in inner.split(","):
                            part = part.strip().strip("'\"")
                            if part:
                                disabled.append(part)
                    current_key = None
                    in_list = False
                else:
                    current_key = "disabled"
                    in_list = True
                continue
            if in_list and current_key and stripped.startswith("- "):
                val = stripped[2:].strip().strip("'\"")
                if val:
                    if current_key == "enabled":
                        enabled.append(val)
                    else:
                        disabled.append(val)
                continue
            if in_list and current_key and stripped and not stripped.startswith("- "):
                # End of current block list on next key or non-list line
                if ":" in stripped:
                    in_list = False
                    current_key = None
                    continue
                in_list = False
                current_key = None
    return enabled, disabled


def _precheck_plugin_status(hh=None):
    """Determine enablement: enabled / disabled / not-enabled (fail-safe)."""
    try:
        if hh is None:
            hh = _precheck_hermes_home()
        profile = _precheck_current_profile(hh)
        cfg_path = _precheck_profile_config_path(hh, profile)
        enabled, disabled = _precheck_parse_plugins_lists(cfg_path)
        if "dir-whip" in enabled:
            return "enabled"
        if "dir-whip" in disabled:
            return "disabled"
        return "not-enabled"
    except Exception:
        return "not-enabled"


def _run_enablement_precheck(hh=None):
    """Emit WARN when dir-whip is not enabled/disabled; quiet when enabled.

    Must NOT affect exit code. Output goes to stderr (preserves --json stdout)
    and is visible in combined stdout+stderr. Message contains the required
    substrings per spec 5.7 / testing-standards §7.8 row 8.
    """
    try:
        status = _precheck_plugin_status(hh)
        if status == "enabled":
            return
        if status == "disabled":
            sys.stderr.write("[WARN] dir-whip plugin is disabled - run 'hermes plugins enable dir-whip' to enable\n")
        else:
            sys.stderr.write("[WARN] dir-whip plugin is not enabled - run 'hermes plugins enable dir-whip' to enable\n")
    except Exception:
        pass


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
        # SCR-042 M1: match through the resolver's allowlist helper (the
        # same code path shape as allowlist.is_allowlist_file, Windows
        # casefold) so guard and audit never disagree on case variants.
        if entry.is_file() and not workspace_resolver._ws_is_allowlist_file(
            entry.name, {"files": allowed}
        ):
            violations.append({
                "check": 1,
                "name": "Root-level files",
                "path": to_fwd(entry.path),
                "suggestion": "Only files listed in the dir-whip allowed_root_files whitelist are allowed at the workspace root; move other files into a session dir.",
            })


def _dir_exempt(name, dirs_entries):
    """True when a root-level directory is covered by an allowlist dirs
    entry (v2.7 R9: first path segment match, casefolded on Windows)."""
    if not dirs_entries:
        return False
    cf = name.casefold() if os.name == "nt" else name
    for d in dirs_entries:
        first = str(d).replace("\\", "/").split("/")[0]
        first_cmp = first.casefold() if os.name == "nt" else first
        if cf == first_cmp:
            return True
    return False


def check_root_outputs(root, violations):
    path = os.path.join(root, OUTPUTS_DIR)
    if os.path.isdir(path):
        violations.append({
            "check": 2,
            "name": "Root-level Outputs directory",
            "path": to_fwd(path),
            "suggestion": "Deliverables belong inside a session dir's Outputs/; move this directory into a session.",
        })


def check_root_session_format(root, violations, dirs_entries=None):
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        # SCR-043 R5: the .hermes root-dir whitelist is removed -- after
        # the quarantine relocation to the dir-whip home no .hermes/
        # directory should exist in the workspace; a leftover one is a
        # non-session violation like any other (_dir_exempt is the
        # allowlist dirs channel, untouched).
        if _dir_exempt(entry.name, dirs_entries):
            continue  # allowlist dirs subtree (v2.7 R9)
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
        # SCR-042 M1: blacklist comparisons are case-insensitive (keys are
        # already lowercase; .pyc style matches check_session_scripts).
        name_lower = entry.name.lower()
        if name_lower == "__pycache__" or name_lower == "node_modules":
            if kind != "directory":
                continue
        if name_lower in BLACKLIST_NAMES or (
            kind == "file" and name_lower.endswith(".pyc")
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
    # v2.7 R9: structured allowlist {files, dirs}; legacy flat values are
    # ignored fail-closed with ONE stderr hint (clean-break visibility).
    al_state = workspace_resolver.allowlist_state(hh)
    if al_state.get("legacy"):
        sys.stderr.write(
            "[WARN] dir-whip-config allowlist uses the removed flat format; "
            "%d legacy entry(ies) ignored -- re-add via /dir-whip allow\n"
            % al_state["legacy"]
        )
    violations = []
    check_root_files(root, al_state["files"], violations)
    check_root_outputs(root, violations)
    check_root_session_format(root, violations, dirs_entries=al_state["dirs"])

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
    scans .tmp/ directories outside session dirs. Symlink boundary
    (SCR-042 N1), two layers: session-name symlinks are not session dirs
    (follow_symlinks=False), and a symlinked .tmp/ body skips the whole
    session (scandir would list external content through the link).
    """
    entries = []
    for entry in os.scandir(parent):
        if not entry.is_dir(follow_symlinks=False) or not is_session_name(entry.name):
            continue
        tmp_dir = os.path.join(entry.path, TMP_DIR)
        if os.path.islink(tmp_dir) or not os.path.isdir(tmp_dir):
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


def cleanup_tmp(root, days):
    """Read-only inventory of expired session .tmp/ entries (SCR-043 R6).

    Returns the sorted entry paths that have not been modified for
    `days` days or longer (find_tmp_entries + is_old filter). The
    plugin never deletes: cleanup decisions belong to the agent; the
    interactive audit lists the result as a proposal and gate mode
    outputs no expired list.
    """
    return [p for p in find_tmp_entries(root) if is_old(p, days)]


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
        help="Cron mode: audit-only run (zero deletion) and append the wakeAgent JSON line.",
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
        if root is None and args.gate:
            # SCR-042 H1, reframed by SCR-043 R6 as cron failure
            # visibility: gate mode never falls back to the fail-open
            # CWD -- an unresolved Working Directory is a gate failure
            # (exit 2, zero stdout, no wakeAgent line) so cron surfaces
            # the broken environment instead of auditing the wrong
            # directory. Checked BEFORE the enablement precheck: the
            # exit-2 path stays output-clean on stdout.
            sys.stderr.write(
                "error: Working Directory unresolved; --gate refuses to "
                "fall back to the current directory\n"
            )
            return 2
        if root is None:
            # Fail-open (spec 4.4 step 4): the resolver already emitted
            # exactly ONE concise stderr warning; fall back to CWD.
            root = os.getcwd()
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            sys.stderr.write("error: resolved Working Directory does not exist: %s\n" % to_fwd(root))
            return 2

    # SCR-037 enablement precheck (spec 5.7): quiet when enabled, WARN otherwise; no exit code change
    _run_enablement_precheck(hh)

    violations = audit(root, hh)

    if args.json:
        print_json(violations)
    else:
        print_plain(violations)

    # SCR-043 R6: read-only inventory -- the expired entries are listed
    # as a proposal in interactive plain mode only (gate outputs no
    # expired list; --json keeps stdout schema-clean). Nothing is ever
    # deleted; --days serves the inventory threshold.
    items = cleanup_tmp(root, args.days)
    if items and not args.gate and not args.json:
        sys.stdout.write(
            "Expired .tmp entries (proposal only; cleanup needs your confirmation):\n"
        )
        for path in items:
            sys.stdout.write(to_fwd(path) + "\n")
        sys.stdout.write("Proposed %d item(s) for .tmp cleanup.\n" % len(items))

    if args.gate:
        # SCR-043 R6: exactly two keys (removed/failed are gone with
        # auto-delete); the exit code stays violations-driven.
        payload = {
            "wakeAgent": bool(violations),
            "violations": len(violations),
        }
        sys.stdout.write(json.dumps(payload) + "\n")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
