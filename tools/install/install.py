#!/usr/bin/env python3
"""workspace-guard installer (tasks 14.9 + 16.2; SCR-011 2.7, SCR-013).

Command-line installer for the workspace-guard Hermes plugin + skill.
Lives at repo root tools/install/ (outside both skill packages), so it is
never part of a scanned skill payload and is NOT synced by Hermes-side
deployment.

Responsibilities (SCR-011 2.7 + SCR-013):
  - Config: ensure HERMES_HOME/workspace-guard/guard-config.yaml exists.
    A deployed plugin-dir copy (<HERMES_HOME>/plugins/workspace-guard/
    guard-config.yaml) is migrated FIRST (copy + single-file os.remove,
    NEVER recursive) so a --force plugin reinstall cannot wipe user config;
    an existing runtime copy is left untouched; only when neither exists is a
    fresh default written. The config step always runs BEFORE the plugin
    step (16.2: migration must precede the --force reinstall's rmtree).
  - Plugin: wraps `hermes plugins install <url>#src/workspace-guard
    --enable`, appending --force when the installed plugin.yaml version
    differs from the repo one (same version -> skip with a notice).
  - Skill: wraps `hermes skills install <url>/src/workspace-organization`;
    idempotent -- a detected existing install is skipped with a notice.
  - Memo readiness is NOT this script's responsibility (SCR-011 2.7): the
    plugin runs a full sync_memo() at register() on the next Hermes restart;
    the user may also run /workspace-guard workspace_update. This script
    never invokes runtime tools and never rebuilds the memo.

Interface:
  python tools/install/install.py --dry-run [--url URL] [--hermes-home DIR]
  python tools/install/install.py --apply   [--url URL] [--hermes-home DIR]

No input() prompts. --dry-run prints every step (paths + commands) and
modifies nothing. hermes CLI failures print a clear error and exit non-zero;
no uncaught exceptions escape main().
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/shawVV1992/workspace-guard"
PLUGIN_SRC = "src/workspace-guard"
SKILL_SRC = "src/workspace-organization"

# Hermes plugin dir layout (shared state dir = plugin presence heuristic in
# workspace_resolver.py; plugin dir is what `hermes plugins install` manages).
PLUGIN_DIR_NAME = "workspace-guard"
PLUGIN_MANIFEST = "plugin.yaml"
CONFIG_SUBDIR = "workspace-guard"
CONFIG_FILE = "guard-config.yaml"

# Installed-skill markers checked for idempotency detection. Hermes skills
# install puts skills under HERMES_HOME/skills/; the category layer
# (productivity/) appears in the current deployment layout, so both shapes
# are probed.
SKILL_MARKER_RELS = (
    "skills/workspace-organization/SKILL.md",
    "skills/productivity/workspace-organization/SKILL.md",
)

# Fresh default runtime config written when neither the runtime location nor
# the deployed plugin-dir copy exists (SCR-013 2.2: working_dir_root fallback,
# exempt_paths, terminal_guard, allowed_root_files). Mirrors the shipped
# template src/workspace-guard/guard-config.yaml. This file lives OUTSIDE the
# skill package, so the rules-file literal in allowed_root_files is fine.
DEFAULT_CONFIG_TEXT = """\
# workspace-guard configuration
# User-managed. Written by tools/install/install.py (SCR-013): the runtime
# config lives at HERMES_HOME/workspace-guard/guard-config.yaml (Windows:
# %LOCALAPPDATA%/hermes/workspace-guard/guard-config.yaml; POSIX:
# ~/.hermes/workspace-guard/guard-config.yaml). It lives OUTSIDE the plugin
# directory, so forced plugin reinstalls (--force) never touch your settings.

# Optional fallback: Default Working Directory, used ONLY when auto-detection
# from the Hermes profile config (terminal.cwd) or the TERMINAL_CWD
# environment variable fails. Never overrides auto-detection.
# working_dir_root: E:/HermesWorkspace/default

exempt_paths: []

# Terminal write guard: false disables terminal write blocking (default on).
terminal_guard: true

# Root files allowed at the Default Working Directory root (D1: guard and
# audit read the same key; strict fallback = empty list when absent).
allowed_root_files: ["AGENTS.md"]
"""


def repo_root():
    """Repo root = three parents up from tools/install/install.py."""
    return Path(__file__).resolve().parents[2]


def hermes_home(override=None):
    """Hermes home dir (mirrors workspace_resolver.hermes_home()).

    Explicit --hermes-home wins (tests/preview); else HERMES_HOME env var
    first; else Windows LOCALAPPDATA/hermes, POSIX ~/.hermes. Deliberately
    duplicated here instead of importing the skill package: tools/ must stay
    independent of the skill/plugin packages.
    """
    if override:
        return override
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return env_home
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def read_version(path):
    """Read the `version:` value from a plugin.yaml; None when unreadable.

    Uses yaml.safe_load when PyYAML is available; falls back to simple line
    parsing (`version:` prefix line) otherwise or when the YAML fails to
    parse. Both manifests (repo and installed) use the plain
    `version: x.y.z` shape.
    """
    try:
        import yaml  # noqa: PLC0415 -- optional dependency, line fallback below

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            ver = data.get("version")
            if isinstance(ver, str) and ver.strip():
                return ver.strip()
        return None
    except ImportError:
        pass
    except Exception:
        pass
    # yaml unavailable or unparseable -> line-based fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("version:"):
                    value = stripped[len("version:"):].strip().strip("'\"")
                    return value or None
    except Exception:
        return None
    return None


def plugin_state(hh):
    """Detect the installed plugin: (installed, installed_version, repo_version).

    installed is False when <hh>/plugins/workspace-guard/plugin.yaml is
    missing (never installed or wiped). repo_version comes from the repo
    manifest src/workspace-guard/plugin.yaml.
    """
    installed_yaml = os.path.join(hh, "plugins", PLUGIN_DIR_NAME, PLUGIN_MANIFEST)
    repo_yaml = os.path.join(str(repo_root()), PLUGIN_SRC, PLUGIN_MANIFEST)
    repo_version = read_version(repo_yaml)
    if not os.path.isfile(installed_yaml):
        return False, None, repo_version
    return True, read_version(installed_yaml), repo_version


def config_plan(hh):
    """Decide the config step: (action, source, target) (SCR-013 2.2/5).

    action in {"keep", "migrate", "write"}:
      - keep:    runtime config already exists at the new location; user
                 config is preserved untouched.
      - migrate: deployed plugin-dir copy exists -> copy it to the new
                 location, then remove the single old file (copy + remove
                 keeps the plugin working if the script is interrupted;
                 NEVER recursive).
      - write:   neither location has a config -> write the fresh default.
    """
    target = os.path.join(hh, CONFIG_SUBDIR, CONFIG_FILE)
    if os.path.isfile(target):
        return "keep", None, target
    source = os.path.join(hh, "plugins", PLUGIN_DIR_NAME, CONFIG_FILE)
    if os.path.isfile(source):
        return "migrate", source, target
    return "write", None, target


def skill_installed(hh):
    """True when a workspace-organization SKILL.md exists under HERMES_HOME
    (idempotency probe for the skill install step)."""
    return any(os.path.isfile(os.path.join(hh, rel)) for rel in SKILL_MARKER_RELS)


def run_cli(cmd):
    """Run a hermes CLI command, echo stdout, surface stderr on failure.

    Returns the process exit code (127 when the hermes executable is not on
    PATH). Never raises.
    """
    print("> " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        print("error: hermes CLI not found on PATH", file=sys.stderr)
        return 127
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out)
    if proc.returncode != 0:
        if err:
            print(err, file=sys.stderr)
        print(
            "error: hermes command failed with exit code {}: {}".format(
                proc.returncode, " ".join(cmd)
            ),
            file=sys.stderr,
        )
    return proc.returncode


def plugin_cmd(url, force):
    """Native plugin install command (SCR-011 2.2 channel)."""
    cmd = ["hermes", "plugins", "install", "{}#{}".format(url, PLUGIN_SRC)]
    if force:
        # Overwrite path: same channel, --force replaces the plugin dir
        # (the config step already ran, so user config is safe).
        cmd.append("--force")
    cmd.append("--enable")
    return cmd


def skill_cmd(url):
    """Native skill install command (SCR-011 2.2 channel)."""
    return ["hermes", "skills", "install", "{}/{}".format(url, SKILL_SRC)]


def plan_lines(hh, url):
    """Human-readable plan for --dry-run (and for the apply summary)."""
    installed, inst_ver, repo_ver = plugin_state(hh)
    action, source, target = config_plan(hh)
    skill_present = skill_installed(hh)

    lines = []
    lines.append("Hermes home: {}".format(hh))
    lines.append("")

    # 1. config
    lines.append("[1/4] config: {}".format(
        {
            "keep": "runtime config exists; user config preserved",
            "migrate": "migrate plugin-dir copy to the runtime location",
            "write": "write fresh default config",
        }[action]
    ))
    if action == "migrate":
        lines.append("      source: {}".format(source))
        lines.append("      target: {}".format(target))
        lines.append("      (copy, then os.remove the single old file; never recursive)")
    elif action == "write":
        lines.append("      mkdir:  {}".format(os.path.dirname(target)))
        lines.append("      target: {}".format(target))
    else:
        lines.append("      target: {}".format(target))

    # 2. plugin
    lines.append("")
    if not installed:
        lines.append("[2/4] plugin: not installed -> install")
        lines.append("      command: {}".format(" ".join(plugin_cmd(url, force=False))))
    elif inst_ver == repo_ver:
        lines.append(
            "[2/4] plugin: already installed, version {} == repo {} -> "
            "already latest, skip".format(inst_ver, repo_ver)
        )
    else:
        lines.append(
            "[2/4] plugin: installed version {} differs from repo {} -> "
            "overwrite with --force".format(inst_ver, repo_ver)
        )
        lines.append("      command: {}".format(" ".join(plugin_cmd(url, force=True))))

    # 3. skill
    lines.append("")
    if skill_present:
        lines.append("[3/4] skill: already installed -> skip (idempotent)")
    else:
        lines.append("[3/4] skill: not installed -> install")
        lines.append("      command: {}".format(" ".join(skill_cmd(url))))

    # 4. memo
    lines.append("")
    lines.append("[4/4] memo: not rebuilt by install.py (SCR-011 2.7); the")
    lines.append("      plugin register() syncs on the next Hermes restart, or")
    lines.append("      run /workspace-guard workspace_update to sync manually")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install/update workspace-guard (plugin + skill + config) "
        "for Hermes. Idempotent; config migration runs before any plugin "
        "reinstall (SCR-013).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan (paths + commands) without modifying anything",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="execute the install/update",
    )
    parser.add_argument(
        "--url",
        default=REPO_URL,
        help="repository URL (default: {})".format(REPO_URL),
    )
    parser.add_argument(
        "--hermes-home",
        default=None,
        metavar="DIR",
        help="Hermes home dir (tests/preview; default: HERMES_HOME env var, "
        "else Windows LOCALAPPDATA/hermes, POSIX ~/.hermes)",
    )
    args = parser.parse_args(argv)
    hh = hermes_home(args.hermes_home)

    try:
        if args.dry_run:
            print("workspace-guard install plan (dry-run)")
            for line in plan_lines(hh, args.url):
                print(line)
            return 0

        # --apply ---------------------------------------------------------
        installed, inst_ver, repo_ver = plugin_state(hh)
        action, source, target = config_plan(hh)

        print("workspace-guard install (apply)")
        print("Hermes home: {}".format(hh))

        # Step 1: config migration/write BEFORE any plugin reinstall
        # (16.2: migration must complete before --force's rmtree).
        if action == "keep":
            print("[1/4] config: runtime config already exists; keeping user config: {}".format(target))
        elif action == "migrate":
            if source is None:  # config_plan guarantees a source for "migrate"
                raise RuntimeError("config_plan returned migrate without a source")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
            os.remove(source)  # single-file remove only; never recursive
            print("[1/4] config: migrated plugin-dir copy -> runtime location")
            print("      source: {}".format(source))
            print("      target: {}".format(target))
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(DEFAULT_CONFIG_TEXT)
            print("[1/4] config: wrote fresh default config: {}".format(target))

        # Step 2: plugin (skip when same version -> idempotent).
        if not installed:
            print("[2/4] plugin: not installed -> install")
            rc = run_cli(plugin_cmd(args.url, force=False))
        elif inst_ver == repo_ver:
            print(
                "[2/4] plugin: already installed, version {} == repo {} -> "
                "already latest, skip".format(inst_ver, repo_ver)
            )
            rc = 0
        else:
            print(
                "[2/4] plugin: installed version {} differs from repo {} -> "
                "overwrite with --force".format(inst_ver, repo_ver)
            )
            rc = run_cli(plugin_cmd(args.url, force=True))
        if rc != 0:
            print("error: plugin install failed; re-run install.py after fixing the cause", file=sys.stderr)
            return rc

        # Step 3: skill (idempotent).
        if skill_installed(hh):
            print("[3/4] skill: already installed -> skip (idempotent)")
        else:
            print("[3/4] skill: not installed -> install")
            rc = run_cli(skill_cmd(args.url))
            if rc != 0:
                print("error: skill install failed; re-run install.py after fixing the cause", file=sys.stderr)
                return rc

        # Step 4: summary (no memo rebuild -- SCR-011 2.7).
        print("[4/4] memo: not rebuilt by install.py; the plugin register()")
        print("      syncs the memo on the next Hermes restart, or run")
        print("      /workspace-guard workspace_update to sync manually")
        print("")
        print("Summary:")
        print("  - config: {} -> {}".format(action, target))
        print("  - plugin: {}".format(
            "not installed -> installed" if not installed
            else ("already latest (skip)" if inst_ver == repo_ver else "updated with --force")
        ))
        print("  - skill:  {}".format("already installed (skip)" if skill_installed(hh) else "installed"))
        print("  - new config location: {}".format(target))
        print("Restart Hermes to activate the guard; register() then syncs the")
        print("profile workspace memo automatically.")
        return 0
    except Exception as exc:  # noqa: BLE001 -- no uncaught exceptions
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
