#!/usr/bin/env python3
"""S0: Shared READ-ONLY workspace resolver (v0.2.0, spec 4.4).

Shared Working Directory resolution module imported by the two session
scripts (create_session_dir.py, audit_workspace.py) -- the ONLY
cross-import exception (engineering-constraints SCR-011).

READ-ONLY by design: never writes to HERMES_HOME (no persisted state, no
rebuild, no hermes_cli import). Resolves the current profile's Working
Directory with the v0.2.0 layered chain (spec 4.4):

    1. guard-config.yaml working_dir_root (explicit, authoritative)
    2. HERMES_SESSION_PROFILE -> profile config.yaml terminal.cwd
    3. profile enumeration + TERMINAL_CWD candidate roots, path matching
    4. fail-open: None + exactly ONE concise stderr WARNING

Self-contained: stdlib only, no PyYAML (absent from the venv) -- config
parsing is minimal line-based, mirroring the plugin fallback parser
(workspace-guard/config.py _parse_terminal_cwd_fallback).

Functions:
    hermes_home()            -- Hermes home (HERMES_HOME override first;
                               Windows LOCALAPPDATA/hermes, POSIX ~/.hermes)
    normalize_path(path)     -- SCR-006 normalization for exact matching
                               (MSYS mapping, drive inheritance, normpath;
                               POSIX normpath identity; UNC unaffected)
    parse_terminal_cwd(path) -- minimal terminal.cwd parser for config.yaml
    allowed_root_files(hh)   -- guard-config allowed_root_files whitelist
                               (strict EMPTY list when absent; shared audit)
    resolve_working_dir_root(workspace, hh, env) -- 4-step chain (spec 4.4)
    validate_workspace(path, hh, env) -- boundary validation (spec 4.4)
"""

import os
import posixpath
import re
import sys

GUARD_SUBDIR = "workspace-guard"
CONFIG_FILE = "guard-config.yaml"

# Fail-open warning (spec 4.4 step 4): exactly ONE concise stderr line,
# never pollutes stdout.
UNRESOLVED_WARNING_WORKSPACE = (
    "[workspace-guard] Working Directory unresolved, using the provided --workspace\n"
)
UNRESOLVED_WARNING_CWD = (
    "[workspace-guard] Working Directory unresolved, using the current directory\n"
)

WORKSPACE_MISMATCH_MESSAGE = (
    "--workspace does not match the resolved Working Directory"
)

_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")


def hermes_home(env=None):
    """Return the Hermes home directory path.

    HERMES_HOME environment variable wins when set (Hermes' canonical
    override, used by tests to isolate from any real installation);
    otherwise Windows: LOCALAPPDATA/hermes, POSIX: ~/.hermes. `env`
    overrides os.environ (test isolation).
    """
    if env is None:
        env = os.environ
    env_home = (env.get("HERMES_HOME") or "").strip()
    if env_home:
        return env_home
    if os.name == "nt":
        return os.path.join(env.get("LOCALAPPDATA", ""), "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _msys_drive(path):
    """Map an MSYS-style forward-slash path to a drive-letter form.

    Handles /c/, //c/ and /cygdrive/c/ (any drive-letter case, output
    uppercased). Returns None when the path is not an MSYS form.
    """
    m = _MSYS_DRIVE_RE.match(path)
    if m:
        return "{}:/{}".format(m.group(1).upper(), m.group(2) or "")
    m = _CYGWIN_DRIVE_RE.match(path)
    if m:
        return "{}:/{}".format(m.group(1).upper(), m.group(2) or "")
    return None


def normalize_path(path):
    """Normalize a path for exact matching (SCR-006 rules).

    Windows branch: MSYS mapping, then normpath; rooted-no-drive paths
    inherit the CWD drive; forward slashes + casefold (case-insensitive
    filesystem). POSIX branch: normpath identity (forward slashes).

    MSYS mapping caveat (current semantics, consistent with guard.py's
    SCR-006 legacy regex): /<letter>... and //<letter>... map to a drive
    letter, so forward-slash forms are NOT reliably UNC-safe -- a
    single-letter "server" such as //s/share is misread as drive S:
    (S:/share). Multi-letter server names (//server/share) do not match
    the regex (it requires a slash right after the letter) and pass
    through unchanged. Only BACKSLASH UNC paths (\\\\server\\share) are
    guaranteed unaffected -- they never match the MSYS forms. A
    coordinated regex change is tracked separately (out of scope here).
    """
    path = os.fspath(path)
    if os.name == "nt":
        mapped = _msys_drive(path)
        if mapped is not None:
            path = mapped
        norm = os.path.normpath(path)
        if not os.path.splitdrive(norm)[0] and os.path.isabs(norm):
            drive = os.path.splitdrive(os.getcwd())[0]
            if drive:
                norm = drive + norm
        return norm.replace("\\", "/").casefold()
    return os.path.normpath(path).replace("\\", "/")


def parse_terminal_cwd(config_path):
    """Parse terminal.cwd from a Hermes config.yaml (minimal parser).

    Mirrors workspace-guard/config.py _parse_terminal_cwd_fallback: the
    `terminal:` block starts at column 0, `cwd:` is read inside it,
    quoted values are stripped; empty/placeholder value -> None;
    missing/unreadable file -> None.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    in_terminal = False
    for line in lines:
        stripped = line.strip()
        if stripped == "terminal:":
            in_terminal = True
            continue
        if in_terminal:
            if stripped.startswith("cwd:"):
                value = stripped[4:].strip().strip("'\"")
                return value if value else None
            if not line.startswith(" ") and not line.startswith("\t"):
                in_terminal = False
    return None


def _parse_yaml_scalar(path, key):
    """Minimal line-based parser for a top-level scalar YAML key.

    The key must start at column 0 (comments and indented keys are
    skipped); quoted values are stripped; empty/missing -> None.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None
    prefix = key + ":"
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix):].strip().strip("'\"")
            return value if value else None
    return None


def _profile_config_paths(hh):
    """Yield (name, config_path) for the default and named profiles."""
    default_cfg = os.path.join(hh, "config.yaml")
    if os.path.isfile(default_cfg):
        yield "default", default_cfg
    profiles_dir = os.path.join(hh, "profiles")
    try:
        names = sorted(os.listdir(profiles_dir))
    except Exception:
        return
    for name in names:
        cfg = os.path.join(profiles_dir, name, "config.yaml")
        if os.path.isfile(cfg):
            yield name, cfg


def _candidate_roots(hh, env=None):
    """Candidate roots R: every profile's terminal.cwd + TERMINAL_CWD.

    TERMINAL_CWD joins as a candidate root ONLY (spec 4.4 step 3) -- it
    is never a config-source step. Empty/placeholder cwd values are
    skipped; duplicates are deduped after normalization.
    """
    if env is None:
        env = os.environ
    roots = []
    for _name, cfg in _profile_config_paths(hh):
        cwd = parse_terminal_cwd(cfg)
        if cwd:
            roots.append(cwd)
    terminal_cwd = (env.get("TERMINAL_CWD") or "").strip()
    if terminal_cwd:
        roots.append(terminal_cwd)
    seen = set()
    out = []
    for root in roots:
        norm = normalize_path(root)
        if norm not in seen:
            seen.add(norm)
            out.append(root)
    return out


def _is_within(child, root):
    """True iff normalized child equals or nests inside normalized root."""
    rel = posixpath.relpath(normalize_path(child), normalize_path(root))
    return rel != ".." and not rel.startswith("../")


def resolve_working_dir_root(workspace=None, hh=None, env=None):
    """Resolve the Working Directory via the v0.2.0 layered chain (spec 4.4).

    Chain order:
      1. guard-config.yaml working_dir_root (authoritative when set)
      2. HERMES_SESSION_PROFILE -> HERMES_HOME/(config.yaml for "default"
         | profiles/<name>/config.yaml) terminal.cwd
      3. Profile enumeration + path matching: candidate roots R =
         {every profile's terminal.cwd} + {TERMINAL_CWD if set}. An
         explicit `workspace` matches by normalized equality against R;
         otherwise the CWD matches by containment (equals a root /
         contained in exactly one root / longest root when nested).
      4. Fail-open: None + exactly ONE concise stderr WARNING.

    Return contract:
      - resolved -> the root (str), stderr untouched (warning-free success);
      - explicit `workspace` with candidates present but NO match -> None
        with NO stderr output (boundary failure, caller-side exit 2);
      - chain entirely unresolvable (no override, no session-profile
        result, AND empty candidate set) -> None after ONE warning
        (fail-open, caller-side fallback).
    """
    if env is None:
        env = os.environ
    if hh is None:
        hh = hermes_home(env)

    # Step 1: guard-config working_dir_root (authoritative when set).
    guard_cfg = os.path.join(hh, GUARD_SUBDIR, CONFIG_FILE)
    root = _parse_yaml_scalar(guard_cfg, "working_dir_root")
    if root:
        return root

    # Step 2: HERMES_SESSION_PROFILE -> profile config terminal.cwd.
    profile = (env.get("HERMES_SESSION_PROFILE") or "").strip()
    if profile:
        if profile == "default":
            profile_cfg = os.path.join(hh, "config.yaml")
        else:
            profile_cfg = os.path.join(hh, "profiles", profile, "config.yaml")
        root = parse_terminal_cwd(profile_cfg)
        if root:
            return root

    # Step 3: candidate roots + path matching.
    candidates = _candidate_roots(hh, env)
    if workspace is not None:
        if not candidates:
            # Step 4 fail-open: boundary entirely unresolvable.
            sys.stderr.write(UNRESOLVED_WARNING_WORKSPACE)
            return None
        target_norm = normalize_path(workspace)
        for root in candidates:
            if normalize_path(root) == target_norm:
                return root
        # Candidates exist but --workspace matches none: boundary failure
        # (caller exits 2). NO warning -- stderr stays clean.
        return None
    if not candidates:
        # Step 4 fail-open: boundary entirely unresolvable.
        sys.stderr.write(UNRESOLVED_WARNING_CWD)
        return None
    cwd = os.getcwd()
    containing = [r for r in candidates if _is_within(cwd, r)]
    if containing:
        return max(containing, key=lambda r: len(normalize_path(r)))
    # CWD not contained in any candidate root: fail-open.
    sys.stderr.write(UNRESOLVED_WARNING_CWD)
    return None


def _parse_allowed_root_files_yaml(data):
    """Extract allowed_root_files from a parsed YAML dict."""
    if not isinstance(data, dict):
        return []
    raw = data.get("allowed_root_files")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _parse_allowed_root_files_lines(path):
    """Line-based allowed_root_files parser (PyYAML unavailable).

    Handles the template shape:
        allowed_root_files:
          - <name>
    and the inline shape:  allowed_root_files: ["<name>"]
    """
    result = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("allowed_root_files:"):
            rest = stripped[len("allowed_root_files:"):].strip()
            in_list = True
            if rest:
                # Inline list: ["<name>"] or single quoted/unquoted value
                inner = rest.strip("[]").strip()
                for part in inner.split(","):
                    part = part.strip().strip("'\"")
                    if part:
                        result.append(part)
                in_list = False
            continue
        if in_list:
            if stripped.startswith("- "):
                value = stripped[2:].strip().strip("'\"")
                if value:
                    result.append(value)
            else:
                in_list = False
    return result


def allowed_root_files(hh=None):
    """Root-file whitelist from guard-config.yaml (audit side of D1).

    Reads the `allowed_root_files` key from
    <HERMES_HOME>/workspace-guard/guard-config.yaml. STRICT fallback: when
    the config file or the key is absent, returns an EMPTY list -> every
    root file is flagged (fail-closed, over-report), matching the plugin
    guard's D1 semantics so guard and audit never disagree about root
    files. No rules-file name is hardcoded here.
    """
    if hh is None:
        hh = hermes_home()
    path = os.path.join(hh, GUARD_SUBDIR, CONFIG_FILE)
    try:
        import yaml  # noqa: PLC0415 -- optional dependency, fallback below

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _parse_allowed_root_files_yaml(data)
    except ImportError:
        return _parse_allowed_root_files_lines(path)
    except Exception:
        return []


def validate_workspace(path, hh=None, env=None):
    """Boundary validation of an explicit --workspace (spec 4.4).

    Existence check FIRST; then normalized equality against the resolved
    Working Directory. Returns (bool, reason):

      - (True, None) when usable, including the fail-open fallback when
        the boundary is entirely unresolvable (the ONE stderr WARNING is
        emitted by resolve_working_dir_root);
      - (False, reason) on boundary failure: non-existent directory, or
        the explicit --workspace does not equal the resolved root / any
        candidate root (stderr stays clean).

    Exit-code mapping is caller-side.
    """
    if not os.path.isdir(path):
        return False, "directory does not exist"
    if hh is None:
        if env is None:
            env = os.environ
        hh = hermes_home(env)
    root = resolve_working_dir_root(workspace=path, hh=hh, env=env)
    if root is None:
        if _candidate_roots(hh, env):
            # Candidates exist but --workspace matched none: boundary
            # failure, no warning (resolve already kept stderr clean).
            return False, WORKSPACE_MISMATCH_MESSAGE
        return True, None
    if normalize_path(path) == normalize_path(root):
        return True, None
    return False, WORKSPACE_MISMATCH_MESSAGE
