"""Path normalization, resolution and containment (spec 5.3/5.5/5.13) —
pure functions.

Normalizes targets (MSYS/Cygwin drive mapping, drive inheritance,
cross-platform Windows-style handling), resolves relative targets, and
decides containment under working_dir_root. Pure functions only: no host
imports, no state (SCR-035 core module discipline, ADR-0007). Extracted
from dir_whip.py and config.py (task 31.6).
"""

import hashlib
import logging
import ntpath
import os
import re
from pathlib import Path

logger = logging.getLogger("dir-whip")

# MSYS-style forward-slash drive forms (SCR-006, task 9.9).
# Matches /c/..., //c/... (single drive letter) but NOT UNC \\server\share.
_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")

_DRIVE_ROOTED_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _get_hermes_home():
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


def _profile_home(hermes_home, profile):
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


def _paths_equal(a, b):
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
    home = _get_hermes_home()
    if profile:
        home = _profile_home(home, profile)
    return Path(home) / "dir-whip"


# Public thin aliases (SCR-035 interface convergence point; SCR-045 R6
# publicized the cross-module home/equality helpers).
get_hermes_home = _get_hermes_home
profile_home = _profile_home
paths_equal = _paths_equal

__all__ = [
    "normalize_target",
    "relativize_target",
    "within_working_dir",
    "is_absolute_any",
    "get_hermes_home",
    "profile_home",
    "paths_equal",
    "dirwhip_home",
]
