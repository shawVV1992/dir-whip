#!/usr/bin/env python3
"""S0: Shared READ-ONLY workspace resolver (v0.4.0, spec 4.4, v2.6 B2).

Shared Working Directory resolution module imported by the two session
scripts (create_session_dir.py, audit_workspace.py) -- the ONLY
cross-import exception (engineering-constraints SCR-011).

READ-ONLY by design: never writes to HERMES_HOME (no persisted state, no
rebuild, no hermes_cli import). Resolves the current profile's Working
Directory with the v0.2.0 layered chain (spec 4.4):

    1. dir-whip-config.yaml working_dir_root (explicit, authoritative)
    2. HERMES_SESSION_PROFILE -> profile config.yaml terminal.cwd
    3. profile enumeration + TERMINAL_CWD candidate roots, path matching
    4. fail-open: None + exactly ONE concise stderr WARNING

Self-contained: stdlib only, no PyYAML dependency -- config parsing is
minimal line-based, mirroring the plugin's PyYAML-based parser
(dir-whip/config.py parse_terminal_cwd).

Spec v2.7 R9: structured allowlist mapping ``{files: [...], dirs: [...]}``
with root-relative entries (the v2.6 flat tagged list is REMOVED clean
break; legacy values are ignored fail-closed and surfaced as a legacy
count). This module duplicates allowlist parsing/validation (no import)
to keep parity with dir-whip/allowlist.py per ADR-0006.

Functions:
    hermes_home()            -- Hermes home (HERMES_HOME override first;
                                Windows LOCALAPPDATA/hermes, POSIX ~/.hermes)
    normalize_path(path)     -- SCR-006 normalization for exact matching
                                (MSYS mapping, drive inheritance, normpath;
                                POSIX normpath identity; UNC unaffected)
    parse_terminal_cwd(path) -- minimal terminal.cwd parser for config.yaml
    allowlist_state(hh)      -- structured allowlist {files, dirs, legacy}
                                (strict empty when absent; v2.7 R9 parity)
    allowed_root_files(hh)   -- allowlist files subset (strict EMPTY when
                                absent; shared audit, v2.7 R9 parity)
    resolve_working_dir_root(workspace, hh, env) -- 4-step chain (spec 4.4)
    validate_workspace(path, hh, env) -- boundary validation (spec 4.4)
"""

import os
import posixpath
import re
import sys

# SCR-042 M3: never crash on a non-UTF-8 console/pipe (e.g. cp936 with
# non-ASCII paths) -- encode errors degrade to replacement characters
# instead of raising UnicodeEncodeError. errors= only; encoding itself is
# untouched, and the hasattr guard covers non-standard streams.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

GUARD_SUBDIR = "dir-whip"
CONFIG_FILE = "dir-whip-config.yaml"

# Fail-open warning (spec 4.4 step 4): exactly ONE concise stderr line,
# never pollutes stdout.
UNRESOLVED_WARNING_WORKSPACE = (
    "[dir-whip] Working Directory unresolved, using the provided --workspace\n"
)
UNRESOLVED_WARNING_CWD = (
    "[dir-whip] Working Directory unresolved, using the current directory\n"
)

WORKSPACE_MISMATCH_MESSAGE = (
    "--workspace does not match the resolved Working Directory"
)

_MSYS_DRIVE_RE = re.compile(r"^//?([a-zA-Z])(?:/(.*))?$")
_CYGWIN_DRIVE_RE = re.compile(r"^/cygdrive/([a-zA-Z])(?:/(.*))?$")
_DRIVE_ROOTED_RE = re.compile(r"^[A-Za-z]:[\\/]")

MAX_FILENAME_LEN = 255
MAX_DIR_LEN = 4096


def hermes_home(env=None):
    """Return the Hermes home directory path.

    HERMES_HOME environment variable wins when set (Hermes' canonical
    override, used by tests to isolate from any real installation);
    otherwise Windows: LOCALAPPDATA/hermes -- with a user-home fallback
    when LOCALAPPDATA is unset/empty so the home is NEVER a relative path
    resolvable against the CWD (SCR-042 N7) --, POSIX: ~/.hermes. `env`
    overrides os.environ (test isolation).
    """
    if env is None:
        env = os.environ
    env_home = (env.get("HERMES_HOME") or "").strip()
    if env_home:
        return env_home
    if os.name == "nt":
        local_app_data = (env.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return os.path.join(local_app_data, "hermes")
        return os.path.join(os.path.expanduser("~"), "hermes")
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

    MSYS mapping caveat (current semantics, consistent with paths.py's
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

    Mirrors dir-whip/config.py parse_terminal_cwd (PyYAML-based): the
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


def _profile_config_path(hh, profile):
    """Profile config path aware of both home layouts (SCR-026/027).

    At runtime Hermes sets HERMES_HOME to the PROFILE DIRECTORY itself for
    non-default profiles (e.g. HERMES_HOME=.../profiles/learn); tests keep
    HERMES_HOME at the root with named profiles under profiles/<name>/.
    Detect the layout by path shape: when hh already IS the profile dir
    (basename == profile, parent basename == "profiles"), the profile
    config is hh/config.yaml. The reverse case (R2): a "default" session
    while hh is a NAMED profile's dir -> the default home is TWO levels up
    (dirname(dirname(hh))/config.yaml).
    """
    if not profile or profile == "default":
        norm = os.path.normpath(str(hh))
        if os.path.basename(os.path.dirname(norm)) == "profiles":
            return os.path.join(
                os.path.dirname(os.path.dirname(norm)), "config.yaml"
            )
        return os.path.join(hh, "config.yaml")
    norm = os.path.normpath(str(hh))
    if (os.path.basename(norm) == profile
            and os.path.basename(os.path.dirname(norm)) == "profiles"):
        return os.path.join(hh, "config.yaml")
    return os.path.join(hh, "profiles", profile, "config.yaml")


def resolve_working_dir_root(workspace=None, hh=None, env=None):
    """Resolve the Working Directory via the v0.2.0 layered chain (spec 4.4).

    Chain order:
      1. dir-whip-config.yaml working_dir_root (authoritative when set)
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

    # Step 1: dir-whip-config working_dir_root (authoritative when set).
    guard_cfg = os.path.join(hh, GUARD_SUBDIR, CONFIG_FILE)
    root = _parse_yaml_scalar(guard_cfg, "working_dir_root")
    if root:
        return root

    # Step 2: HERMES_SESSION_PROFILE -> profile config terminal.cwd.
    profile = (env.get("HERMES_SESSION_PROFILE") or "").strip()
    if profile:
        profile_cfg = _profile_config_path(hh, profile)
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


# ---------------------------------------------------------------- Structured allowlist parity (spec v2.7 R9, duplicated from allowlist.py)

def _ws_is_absolute_any(target):
    """Rooted on local OS, Windows-drive-rooted, or backslash-rooted (parity)."""
    if os.path.isabs(target):
        return True
    if _DRIVE_ROOTED_RE.match(target):
        return True
    return target.startswith("\\") and not target.startswith("\\\\")


def _ws_validate_file(name):
    if not isinstance(name, str):
        return False, "filename must be a string"
    stripped = name.strip()
    if not stripped:
        return False, "filename must not be empty"
    if len(stripped) > MAX_FILENAME_LEN:
        return False, "filename too long (max %d)" % MAX_FILENAME_LEN
    if "/" in stripped or "\\" in stripped:
        return False, "filename must not contain path separators"
    if stripped in (".", ".."):
        return False, "filename must not be '.' or '..'"
    if ".." in stripped:
        return False, "filename must not contain '..'"
    if ":" in stripped:
        return False, "filename must not contain ':'"
    if os.path.basename(stripped) != stripped:
        return False, "filename must be basename only"
    return True, ""


def _ws_validate_dir_rel(path):
    """Relative-dir validation (parity duplicate of allowlist._validate_dir_rel).

    Valid dir entry: non-empty string RELATIVE to working_dir_root (no
    drive/absolute forms, no ':'), no '.'/'..'/empty segments,
    length <= MAX_DIR_LEN. Multi-level allowed.
    """
    if not isinstance(path, str):
        return False, "dir entry must be a string"
    stripped = path.strip()
    if not stripped:
        return False, "dir entry must not be empty"
    if len(stripped) > MAX_DIR_LEN:
        return False, "dir entry too long (max %d)" % MAX_DIR_LEN
    if stripped in (".", ".."):
        return False, "dir entry must not be '.' or '..'"
    if _ws_is_absolute_any(stripped):
        return False, "dir entry must be relative to the Working Directory root"
    if ":" in stripped:
        return False, "dir entry must not contain ':'"
    normalized = stripped.replace("\\", "/")
    if normalized.startswith("/"):
        return False, "dir entry must be relative to the Working Directory root"
    for part in normalized.split("/"):
        if part in ("", ".", ".."):
            return False, "dir entry must not contain '.', '..' or empty segments"
    return True, ""


def _ws_normalize_dir_rel(path):
    """Normalize a validated dir entry: forward slashes, trailing slash
    stripped, duplicate slashes collapsed (R7 storage normalization)."""
    if not isinstance(path, str):
        return ""
    s = path.strip().replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    return s.rstrip("/")


def _ws_parse_allowlist(raw):
    """Parity duplicate of allowlist.parse_allowlist (v2.7 R9, stdlib only).

    Expected MAPPING ``{"files": [...], "dirs": [...]}`` with root-relative
    entries. A legacy FLAT value (the v2.6 list of ``file:``/``prefix:``
    tagged strings) or any non-dict input is IGNORED fail-closed -> empty
    sets (clean break); the caller surfaces the legacy count as a hint.
    """
    if not isinstance(raw, dict):
        return {"files": set(), "dirs": set()}
    files = set()
    raw_files = raw.get("files")
    if isinstance(raw_files, (list, tuple, set)):
        for item in raw_files:
            ok, _ = _ws_validate_file(item)
            if ok:
                files.add(item.strip())
    dirs = set()
    raw_dirs = raw.get("dirs")
    if isinstance(raw_dirs, (list, tuple, set)):
        for item in raw_dirs:
            ok, _ = _ws_validate_dir_rel(item)
            if ok:
                dirs.add(_ws_normalize_dir_rel(item))
    return {"files": files, "dirs": dirs}


def _ws_is_allowlist_file(name, parsed):
    if not isinstance(name, str):
        return False
    base = os.path.basename(name.strip().replace("\\", "/"))
    if not base:
        base = name.strip()
    files = (parsed or {}).get("files") or set()
    if os.name == "nt":
        base_cf = base.casefold()
        for f in files:
            if isinstance(f, str) and f.casefold() == base_cf:
                return True
        return False
    else:
        return base in files


def _ws_is_allowlist_dir(path, working_dir_root, parsed):
    """Parity duplicate of allowlist.is_allowlist_dir (v2.7 R9).

    True when ``path`` equals or is under ``<root>/<entry>`` for any dirs
    entry (recursive subtree, forward-slash normalized, casefolded on
    Windows). The root itself and anything outside it are never exempt.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    if not working_dir_root:
        return False

    def _norm(p):
        s = str(p).replace("\\", "/")
        s = re.sub(r"/{2,}", "/", s)
        if s != "/" and s.endswith("/"):
            s = s.rstrip("/")
        return s

    t = _norm(path)
    r = _norm(working_dir_root)
    if not t or not r:
        return False
    cf = os.name == "nt"
    t_cmp = t.casefold() if cf else t
    r_cmp = r.casefold() if cf else r
    r_cmp = r_cmp.rstrip("/")
    if t_cmp == r_cmp:
        return False
    prefix = r_cmp + "/"
    if not t_cmp.startswith(prefix):
        return False
    rel = t[len(r.rstrip("/")) + 1:]
    rel_cmp = rel.casefold() if cf else rel
    for d in (parsed or {}).get("dirs") or set():
        if not isinstance(d, str):
            continue
        d_norm = _norm(d)
        d_cmp = d_norm.casefold() if cf else d_norm
        if rel_cmp == d_cmp or rel_cmp.startswith(d_cmp + "/"):
            return True
    return False


def _split_flow_list(rest):
    """Parse a YAML flow list body ``[a, b]`` / ``a, b`` into string parts."""
    inner = rest.strip().strip("[]").strip()
    parts = []
    if inner:
        for part in inner.split(","):
            part = part.strip().strip("'\"")
            if part:
                parts.append(part)
    return parts


def _parse_allowlist_yaml(data):
    """Extract the structured allowlist from parsed YAML (v2.7 R9 parity).

    Reads the single key ``allowlist`` (mapping form). Legacy flat values /
    old keys are ignored fail-closed; the legacy entry count is returned
    alongside for the clean-break hint.
    """
    empty = {"files": [], "dirs": [], "legacy": 0}
    if not isinstance(data, dict):
        return dict(empty)
    raw = data.get("allowlist")
    parsed = _ws_parse_allowlist(raw)
    legacy = 0
    if isinstance(raw, list):
        legacy = sum(1 for x in raw if isinstance(x, str) and x.strip())
    return {
        "files": sorted(parsed.get("files") or []),
        "dirs": sorted(parsed.get("dirs") or []),
        "legacy": legacy,
    }


def _parse_allowlist_lines(path):
    """Line-based structured allowlist parser (PyYAML unavailable, v2.7 R9).

    Handles the mapping block form::

        allowlist:
          files: ["a.txt", "b.txt"]
          dirs: ["proj/sub"]

    and the inline mapping ``allowlist: {files: [...], dirs: [...]}``.
    A legacy FLAT value (inline ``["file:a", ...]`` or a ``- item`` block)
    is IGNORED fail-closed and counted for the clean-break hint.
    Returns {"files": [...], "dirs": [...], "legacy": N}.
    """
    result = {"files": [], "dirs": [], "legacy": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return result
    in_map = False       # inside an allowlist: block
    in_sub = None        # "files" | "dirs" while reading its flow/block list
    raw_map = {"files": [], "dirs": []}
    legacy_items = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        if stripped.startswith("allowlist:"):
            rest = stripped[len("allowlist:"):].strip()
            in_map = True
            in_sub = None
            if rest:
                # Inline form: mapping {...} -> parse sub-keys; list [...] ->
                # legacy flat (counted, ignored).
                if rest.startswith("{"):
                    body = rest.strip("{}")
                    # minimal inline-mapping scan: files:[...], dirs:[...]
                    m_files = re.search(r"files\s*:\s*\[([^\]]*)\]", body)
                    m_dirs = re.search(r"dirs\s*:\s*\[([^\]]*)\]", body)
                    if m_files:
                        raw_map["files"].extend(_split_flow_list(m_files.group(1)))
                    if m_dirs:
                        raw_map["dirs"].extend(_split_flow_list(m_dirs.group(1)))
                elif rest.startswith("["):
                    inner = rest.strip("[]").strip()
                    if inner:
                        legacy_items.extend(_split_flow_list(inner))
                in_map = False
            continue
        if in_map:
            if not indented:
                in_map = False
                in_sub = None
            elif stripped.startswith("files:"):
                rest = stripped[len("files:"):].strip()
                if rest.startswith("["):
                    raw_map["files"].extend(_split_flow_list(rest))
                    in_sub = None
                else:
                    in_sub = "files"
            elif stripped.startswith("dirs:"):
                rest = stripped[len("dirs:"):].strip()
                if rest.startswith("["):
                    raw_map["dirs"].extend(_split_flow_list(rest))
                    in_sub = None
                else:
                    in_sub = "dirs"
            elif in_sub and stripped.startswith("- "):
                raw_map[in_sub].append(stripped[2:].strip().strip("'\""))
            else:
                in_sub = None
    parsed = _ws_parse_allowlist({
        "files": raw_map["files"],
        "dirs": raw_map["dirs"],
    })
    legacy = len(legacy_items)
    if not raw_map["files"] and not raw_map["dirs"]:
        # No mapping content found: treat any collected strings under a
        # legacy "- item" block as flat entries for the hint count.
        pass
    return {
        "files": sorted(parsed.get("files") or []),
        "dirs": sorted(parsed.get("dirs") or []),
        "legacy": legacy,
    }


def allowlist_state(hh=None):
    """Structured allowlist state from dir-whip-config.yaml (v2.7 R9).

    Reads the ``allowlist`` mapping from <HERMES_HOME>/dir-whip/
    dir-whip-config.yaml. Returns {"files": [sorted basenames],
    "dirs": [sorted relative paths], "legacy": N} where N counts ignored
    legacy flat entries (clean-break hint). STRICT fallback: missing
    config/key -> empty state (fail-closed, matching the plugin guard so
    guard and audit never disagree).
    """
    if hh is None:
        hh = hermes_home()
    path = os.path.join(hh, GUARD_SUBDIR, CONFIG_FILE)
    try:
        import yaml  # noqa: PLC0415 -- optional dependency, fallback below

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _parse_allowlist_yaml(data)
    except ImportError:
        return _parse_allowlist_lines(path)
    except Exception:
        return {"files": [], "dirs": [], "legacy": 0}


def allowed_root_files(hh=None):
    """Root-file whitelist from dir-whip-config.yaml (audit side, v2.7 R9).

    The ``files`` subset of the structured ``allowlist`` mapping (root-level
    basenames). STRICT fallback: when the config file or the key is absent,
    returns an EMPTY list -> every root file is flagged (fail-closed,
    over-report), matching the plugin guard's semantics so guard and audit
    never disagree. Legacy flat values are ignored (empty subset).
    """
    return allowlist_state(hh)["files"]


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
