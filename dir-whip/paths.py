"""Path normalization / resolution / containment under working_dir_root -- pure functions.

Normalizes targets (MSYS/Cygwin drive mapping, drive inheritance, cross-platform Windows-style handling), resolves relative targets, and decides containment; unclassifiable Windows paths fail open (warn + treat as external, never raise). Pure functions plus ONE state-reading helper: config_file_path() (SCR-052 R1 single source) reads state.session for the profile-aware dir-whip-config.yaml location, fail-open guarded (SCR-035 core module discipline, ADR-0007); extracted from dir_whip.py and config.py (task 31.6). SCR-050 v3 R6.3 (spec 5.9): SESSION_DIR_RE + is_inside_session_dir homed here from config.py (pure pattern containment; config keeps the same-name re-export alias).

Layer: core
Refs: spec 5.3, spec 5.5, spec 5.9, spec 5.13, SCR-006, SCR-026, SCR-027, SCR-035, SCR-042, SCR-044, SCR-045, SCR-050, SCR-052 R1, ADR-0007
Key exports:
  - normalize_target -- normalize a target path before classification (chain step 0).
  - within_working_dir -- containment of target under working_dir_root (spec 5.3 step 6).
  - relativize_target -- privacy relativization; external paths -> ``h:<sha256-prefix>``.
  - is_absolute_any -- rooted on the local OS, Windows-drive-rooted, or backslash-rooted.
  - is_inside_session_dir -- True when the path sits under working_dir_root/<session_dir>/... (spec 5.9; SCR-050 v3 R6.3 homing).
  - dirwhip_home -- profile-aware dir-whip home (stats.jsonl / dir-whip.log / audit-quarantine family).
  - config_file_path -- profile-aware dir-whip-config.yaml location (SCR-052 R1 single source).
  - get_hermes_home, profile_home, paths_equal -- public home resolution + path equality helpers.
"""

import datetime
import hashlib
import logging
import ntpath
import os
import re
from pathlib import Path

from . import state

logger = logging.getLogger("dir-whip")

# MSYS-style forward-slash drive forms (SCR-006, task 9.9).
# Matches /c/..., //c/... (single drive letter) but NOT UNC \\server\share.
_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")

_DRIVE_ROOTED_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Session-directory name pattern (spec 5.9; SCR-050 v3 R6.3: homed from
# config.py -- session dirs exist only at the Working Directory root).
SESSION_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\S.*)?$")


def is_inside_session_dir(path, working_dir_root):
    """Check if path is under working_dir_root/<session_dir>/... (spec 5.9)."""
    try:
        rel = os.path.relpath(path, working_dir_root)
    except ValueError:
        return False
    parts = rel.replace("\\", "/").split("/")
    if parts and SESSION_DIR_RE.match(parts[0]):
        try:
            datetime.datetime.strptime(parts[0][:15].replace("_", ""), "%Y%m%d%H%M%S")
            return True
        except ValueError:
            return False
    return False


def get_hermes_home():
    """Return the Hermes home directory path (D5).

    HERMES_HOME environment override FIRST, then the platform default:
    Windows LOCALAPPDATA/hermes -- with a Path.home()/"hermes" fallback
    when LOCALAPPDATA is unset/blank so the home is NEVER a relative
    path resolvable against the CWD (SCR-044 R8, script-side SCR-042 N7
    parity) --, POSIX ~/.hermes.
    """
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    if os.name == "nt":
        local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return Path(local_app_data) / "hermes"
        return Path.home() / "hermes"   # R8: unset/blank fallback (script-side N7 parity)
    return Path.home() / ".hermes"


def profile_home(hermes_home, profile):
    """The profile's home directory, aware of both layouts (SCR-026/027).

    profile default: home-shaped (parent named "profiles", i.e. HERMES_HOME
    IS a named profile's dir) -> hermes_home.parent.parent (the default
    home is two levels up); otherwise hermes_home. profile named: home IS
    the profile dir -> hermes_home; otherwise hermes_home/profiles/<name>.
    """
    hermes_home = Path(hermes_home)
    if not profile or profile == "default":
        if hermes_home.parent.name == "profiles":
            return hermes_home.parent.parent
        return hermes_home
    if hermes_home.name == profile and hermes_home.parent.name == "profiles":
        return hermes_home
    return hermes_home / "profiles" / profile


def is_absolute_any(target):
    """Rooted on the local OS, Windows-drive-rooted, or backslash-rooted.

    On POSIX, posixpath.isabs() returns False for Windows-style paths like
    ``E:/ws/x.txt`` or ``\\evil\\file.txt``; joining such a target onto the
    base would double-prefix it (``E:/ws/E:/ws/x.txt``). Rooted targets
    resolve as-is and the classifier then decides external vs in-workspace
    via the normalized root.
    """
    if os.path.isabs(target):
        return True
    if _DRIVE_ROOTED_RE.match(target):
        return True
    return target.startswith("\\") and not target.startswith("\\\\")


# ---------------------------------------------------------------- Path normalization (SCR-006)

def _normalize_windows(path, working_dir_root):
    """Normalize a target path on Windows (MSYS mapping + drive inheritance).

    1. Map MSYS forward-slash forms to drive-qualified paths:
       /c/..., //c/... -> C:/<rest>; /cygdrive/c/... -> C:/<rest>.
       UNC paths (//server/share) do not match these regexes.
    2. os.path.normpath (separator and dot-segment normalization).
    3. Drive inheritance: rooted paths that still lack a drive get the
       drive of working_dir_root; skipped if working_dir_root has no drive.
    4. Fail-open: a path that STILL has no drive after inheritance is
       unclassifiable on Windows; log a warning and return it unchanged
       (never raise -- the caller classifies it as external and allows).
    """
    match = _MSYS_DRIVE_RE.match(path)
    if match:
        drive, rest = match.group(1), match.group(2)
        path = "%s:/%s" % (drive.upper(), rest or "")
    else:
        match = _CYGWIN_DRIVE_RE.match(path)
        if match:
            drive, rest = match.group(1), match.group(2)
            path = "%s:/%s" % (drive.upper(), rest or "")

    path = os.path.normpath(path)

    drive, _ = ntpath.splitdrive(path)
    if not drive and working_dir_root:
        root_drive, _ = ntpath.splitdrive(working_dir_root)
        if root_drive:
            path = root_drive + path

    drive, _ = ntpath.splitdrive(path)
    if not drive:
        logger.warning(
            "dir-whip: target %r unclassifiable after "
            "normalization (no drive); treating as external "
            "(fail-open)",
            path,
        )

    return path


def _normalize_posix(path):
    """Normalize a target path on POSIX hosts (normpath identity)."""
    return os.path.normpath(path)


def _looks_windowsy(path):
    """Windows-style target on ANY host (SCR-006 cross-platform).

    MSYS/Cygwin forms, drive-rooted paths, and single-backslash-rooted
    paths follow Windows normalization even on POSIX hosts (a WSL/Git-Bash
    session can carry Windows-style roots and targets).
    """
    return bool(
        _DRIVE_ROOTED_RE.match(path)
        or _MSYS_DRIVE_RE.match(path)
        or _CYGWIN_DRIVE_RE.match(path)
        or (path.startswith("\\") and not path.startswith("\\\\"))
    )


def normalize_target(path, working_dir_root):
    """Normalize a target path before classification (chain step 0)."""
    if os.name == "nt" or _looks_windowsy(path):
        return _normalize_windows(path, working_dir_root)
    return _normalize_posix(path)


# ---------------------------------------------------------------- Containment (spec 5.3 step 6)

def within_working_dir(target, working_dir_root):
    """Containment of target under working_dir_root (5.3 step 6).

    Windows-style (drive-rooted) pairs are compared case-insensitively on
    ANY host — Windows paths follow Windows matching rules even on POSIX
    (SCR-006; e.g. a WSL session carrying a Windows-style root). Native
    paths use os.path.relpath (case-sensitive on POSIX).
    """
    target_fwd = str(target).replace("\\", "/")
    root_fwd = str(working_dir_root).replace("\\", "/")
    if _DRIVE_ROOTED_RE.match(target_fwd) and _DRIVE_ROOTED_RE.match(root_fwd):
        target_cf = target_fwd.casefold()
        root_cf = root_fwd.casefold()
        if target_cf == root_cf:
            return True
        prefix = root_cf.rstrip("/") + "/"
        return target_cf.startswith(prefix)
    try:
        rel = os.path.relpath(target, working_dir_root)
    except ValueError:
        # Different drive on Windows: cannot relate -> external.
        return False
    return not rel.startswith("..")


# ---------------------------------------------------------------- Privacy relativization (spec 5.13)

def _hash_prefix(value):
    """Deterministic privacy-preserving prefix for external paths (5.13)."""
    return "h:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def relativize_target(target, working_dir_root):
    """Privacy: target relative to working_dir_root; external -> hash prefix.

    None target stays None (omitted). External paths (outside the root,
    different drive, or unrelatable) become a 'h:<sha256-prefix>' hash so
    no absolute external path ever lands in stats.jsonl (5.13 privacy).
    """
    if target is None:
        return None
    target = str(target)
    if working_dir_root is None:
        return _hash_prefix(target)
    try:
        rel = os.path.relpath(target, str(working_dir_root))
    except ValueError:  # different drive on Windows -> cannot relate
        return _hash_prefix(target)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(".." + os.sep):
        return _hash_prefix(target)
    return rel.replace("\\", "/")


def paths_equal(a, b):
    """Forward-slash path equality; case-insensitive on Windows."""
    a = str(a).replace("\\", "/")
    b = str(b).replace("\\", "/")
    if os.name == "nt":
        return a.casefold() == b.casefold()
    return a == b


def dirwhip_home(profile=None):
    """The profile-aware dir-whip home directory (SCR-045 R7 single source).

    Pure function: profile comes from the caller (usually
    state.session.session_profile); None -> HERMES_HOME directly
    (register-time / no session profile). Returns <home>/dir-whip --
    the stats.jsonl / dir-whip.log / audit-quarantine family home. The
    five former hand-rolled get_hermes_home + profile dance sites
    (stats / logsetup / audit x2 / report) all call this now.
    """
    home = get_hermes_home()
    if profile:
        home = profile_home(home, profile)
    return Path(home) / "dir-whip"


def config_file_path():
    """The profile-aware dir-whip-config.yaml location (SCR-052 R1 single
    source; merges the former config._get_guard_config_path and
    config_writer._get_config_path).

    Resolution (superset of the two merged chains; in production the
    registered ctx and the report command ctx are the same object):
    state.session.session_profile -> registered_ctx.profile_name ->
    report command ctx.profile_name (function-local import: the
    documented cycle-break idiom -- report imports config which imports
    paths). Falls back to HERMES_HOME/dir-whip/... when no profile is
    findable (default tests). Built on dirwhip_home() so the config file
    lands in the same profile-aware dir-whip home family as stats.jsonl /
    dir-whip.log / audit-quarantine. Fail-open on any state read error.
    """
    profile = None
    try:
        if getattr(state.session, "session_profile", None):
            profile = state.session.session_profile
    except Exception:
        profile = None
    if not profile:
        try:
            ctx = getattr(state.session, "registered_ctx", None)
            if ctx is not None and getattr(ctx, "profile_name", None):
                profile = ctx.profile_name
        except Exception:
            pass
    if not profile:
        try:
            from . import report as _report
            ctx = _report._get_cmd_ctx()
            if ctx is not None and getattr(ctx, "profile_name", None):
                profile = ctx.profile_name
        except Exception:
            pass
    return dirwhip_home(profile) / "dir-whip-config.yaml"


# Single authoritative names (SCR-052 R1 alias convergence: the former
# module-tail get_hermes_home/profile_home/paths_equal alias lines are
# gone; the defs above carry the public names).

__all__ = [
    "normalize_target",
    "relativize_target",
    "within_working_dir",
    "is_absolute_any",
    "is_inside_session_dir",
    "SESSION_DIR_RE",
    "get_hermes_home",
    "profile_home",
    "paths_equal",
    "dirwhip_home",
    "config_file_path",
]
