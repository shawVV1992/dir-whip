"""The /dir-whip report command surface (spec 5.7, SCR-029).

Renders the merged report (version, State, Working Directory + source,
Terminal Guard, Exempt Paths, Root Allowlist, Health, WARNING, Stats File)
and registers the slash command. Depends on the config resolution/stats
surface (report -> config direction, per the plan's dependency graph).
Extracted from config.py (task 31.8).
"""

import logging
import os
import re
from pathlib import Path

try:
    from . import state
except ImportError:
    import state

try:
    from .config import (
        _effective_root,
        _get_hermes_home,
        _paths_equal,
        _profile_terminal_cwd,
        load_guard_config,
        parse_terminal_cwd,
    )
except ImportError:
    from config import (
        _effective_root,
        _get_hermes_home,
        _paths_equal,
        _profile_terminal_cwd,
        load_guard_config,
        parse_terminal_cwd,
    )

try:
    from .stats import _stats_jsonl_path
except ImportError:
    from stats import _stats_jsonl_path

logger = logging.getLogger("dir-whip")

# The ctx captured by register_dir_whip_commands; command handlers
# read profile_name from it (the host invokes handlers as fn(raw_args)).
_cmd_ctx = None


def _get_cmd_ctx():
    """The ctx captured at command registration (None when unregistered)."""
    return _cmd_ctx


def _resolution_source(ctx):
    """The resolution-chain step that produces working_dir_root (5.5).

    Mirrors resolve_working_dir_root's order: dir-whip-config override ->
    profile terminal.cwd -> fail-open. Source strings match the chain's
    INFO log sources exactly.
    """
    try:
        if load_guard_config().get("working_dir_root"):
            return "dir-whip-config"
    except Exception:
        pass
    try:
        profile = getattr(ctx, "profile_name", None)
        if profile:
            hermes_home = _get_hermes_home()
            if profile == "default":
                cfg_path = hermes_home / "config.yaml"
            else:
                cfg_path = hermes_home / "profiles" / profile / "config.yaml"
            if parse_terminal_cwd(cfg_path):
                return "profile-config"
    except Exception:
        pass
    return "fail-open"


def _guard_config_key_present(key):
    """True when the key appears in dir-whip-config.yaml (raw line scan)."""
    try:
        path = _get_hermes_home() / "dir-whip" / "dir-whip-config.yaml"
        if not path.is_file():
            return False
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return re.search(r"^\s*%s\s*:" % re.escape(key), text, re.MULTILINE) is not None
    except Exception:
        return False


def _stats_writable():
    """Check stats.jsonl writability (Health). Returns (ok, error)."""
    path = _stats_jsonl_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, str(exc)
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


# Report display labels for the resolution-chain sources (SCR-029): the
# dir-whip-config source renders as "guard-config" per the report contract;
# profile-config / fail-open render as-is.
_SOURCE_LABELS = {"dir-whip-config": "guard-config"}


def _plugin_version(path=None):
    """The plugin version from the sibling plugin.yaml (the single version
    source, SCR-029). Simple text parse, NO PyYAML: the first `version:`
    line. On ANY failure (missing/unreadable file, no match) -> 'unknown';
    never raises. P6 (31.13): the register-time precomputed value in
    state.session.plugin_version wins when present.
    """
    if path is None and state.session.plugin_version:
        return state.session.plugin_version
    if path is None:
        path = Path(__file__).resolve().parent / "plugin.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        match = re.search(r"^version:\s*(\S+)$", text, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "unknown"


def _dir_whip_report():
    """Render the merged /dir-whip report (SCR-029 Plan A; spec 5.7).

    Fixed field order: version, State, Working Directory + source,
    Terminal Guard, Exempt Paths, Root Allowlist, Health (+ one
    line per problem), WARNING (anomaly-only), Stats File path. A missing
    dir-whip-config.yaml is the design default, NOT a Health problem.
    Never raises.
    """
    try:
        ctx = _get_cmd_ctx()
        cfg = load_guard_config()
        root = _effective_root(ctx)
        lines = []

        # Line 1: version (plugin.yaml, unknown fallback).
        lines.append("[dir-whip] v%s" % _plugin_version())

        # Line 2: state.
        lines.append("State: ACTIVE" if root else "State: FAIL-OPEN")

        # Line 3: Working Directory + resolving source (5.5 chain).
        if root:
            source = _resolution_source(ctx)
            source = _SOURCE_LABELS.get(source, source)
            lines.append("Working Directory: %s  (source: %s)" % (root, source))
        else:
            lines.append("Working Directory: (unresolved)")

        # Line 4: terminal guard.
        lines.append(
            "Terminal Guard: %s"
            % ("enabled" if cfg.get("terminal_guard", True) else "disabled")
        )

        # Line 5: exempt paths.
        exempts = cfg.get("exempt_paths", [])
        lines.append(
            "Exempt Paths: %s" % (", ".join(exempts) if exempts else "(none)")
        )

        # Line 6: root allowlist (allowed_root_files). Missing key =
        # fail-closed hint (doctor semantics); present-but-empty keeps the
        # status "(none)" semantics; otherwise comma-joined.
        allowed = cfg.get("allowed_root_files", [])
        if not _guard_config_key_present("allowed_root_files"):
            lines.append("Root Allowlist: (strict empty allowlist)")
        elif allowed:
            lines.append("Root Allowlist: %s" % ", ".join(allowed))
        else:
            lines.append("Root Allowlist: (none)")

        # Line 7: health (one line per problem when PROBLEM).
        problems = []
        if not root:
            problems.append("resolution: FAIL-OPEN")
        writable, error = _stats_writable()
        if not writable:
            problems.append("stats.jsonl: NOT WRITABLE (%s)" % error)
        if problems:
            lines.append("Health: PROBLEM")
            lines.extend("- %s" % p for p in problems)
        else:
            lines.append("Health: OK")

        # Line 8 (anomaly only): Q6 footgun — explicit override differs
        # from the profile terminal.cwd (doctor logic retained).
        override = cfg.get("working_dir_root")
        if override:
            profile_cwd = _profile_terminal_cwd(ctx)
            if profile_cwd is not None and not _paths_equal(override, profile_cwd):
                lines.append(
                    "WARNING: dir-whip-config working_dir_root (%s) differs from "
                    "profile terminal.cwd (%s); the desktop-settings edit is "
                    "masked by the override" % (override, profile_cwd)
                )

        # Last line (always): stats.jsonl absolute path (session profile
        # home, 5.13/SCR-027).
        lines.append("Stats File: %s" % _stats_jsonl_path())
        return "\n".join(lines)
    except Exception as exc:
        return "[dir-whip] report failed: %s" % exc


def _dir_whip_cmd(raw_args):
    """/dir-whip dispatcher (spec 5.7, SCR-029): no subcommands.

    The host invokes the handler as fn(raw_args) with everything after the
    first token. Bare /dir-whip renders the merged report; ANY argument
    renders exactly one Usage line (the status/stats/doctor subcommands
    are removed). Never raises (errors become the message).
    """
    try:
        if (raw_args or "").strip():
            return "Usage: /dir-whip"
        return _dir_whip_report()
    except Exception as exc:
        return "[dir-whip] command failed: %s" % exc


def register_dir_whip_commands(ctx):
    """Register the /dir-whip slash command (spec 5.7).

    Exactly ONE command named "dir-whip": Hermes dispatches slash commands
    on the FIRST token only (cli.py: base_cmd = split()[0]), so every
    argument reaches the same handler, which renders the one-line Usage
    (SCR-029: status/stats/doctor subcommands removed). Guarded: a ctx
    without register_command still registers. allow_path is a TOOL and is
    NOT registered here (__init__.py registers it).
    """
    global _cmd_ctx
    _cmd_ctx = ctx
    if not hasattr(ctx, "register_command"):
        return
    try:
        ctx.register_command(
            "dir-whip", _dir_whip_cmd,
            description="dir-whip: Working Directory guard report",
            args_hint="",
        )
    except Exception as exc:
        logger.warning("dir-whip: register_command failed: %s", exc)


# Public thin aliases (SCR-035 interface convergence point).
register_commands = register_dir_whip_commands
render = _dir_whip_report

__all__ = ["register_commands", "render"]
