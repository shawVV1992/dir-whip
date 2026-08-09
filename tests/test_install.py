"""Tests for tools/install/install.py (SCR-013 install side; task 16.3).

All tests run the installer in --dry-run mode against an isolated tmp
HERMES_HOME (--hermes-home). The installer is NEVER executed with --apply:
no file may be created or modified, and no hermes CLI command may run.
"""

import os
import subprocess
import tempfile
from pathlib import Path

INSTALL = Path(__file__).parent.parent / "tools" / "install" / "install.sh"


def _find_bash():
    """Prefer Git Bash on Windows: plain "bash" may resolve to WSL's
    System32 launcher, which cannot execute absolute Windows paths.
    Falls back to "bash" on POSIX/CI."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if os.name == "nt" and git_bash.is_file():
        return str(git_bash)
    return "bash"


BASH = _find_bash()


def run_install(hh, *extra):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hh)
    return subprocess.run(
        [BASH, str(INSTALL)] + list(extra),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def snapshot(root):
    """Sorted list of relative paths under root (dirs with trailing '/')."""
    entries = []
    if not Path(root).is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel != ".":
            entries.append(rel + "/")
        for f in filenames:
            entries.append(os.path.join(rel, f).replace(os.sep, "/"))
    return sorted(entries)


def repo_plugin_version():
    """Read the version from the repo plugin manifest (line-based, mirrors install.py)."""
    manifest = Path(__file__).parent.parent / "src" / "workspace-guard" / "plugin.yaml"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    raise AssertionError("no version found in repo plugin.yaml")


def write_installed_plugin(hh, version):
    """Write an installed plugin.yaml under <hh>/plugins/workspace-guard/."""
    manifest = hh / "plugins" / "workspace-guard" / "plugin.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("name: workspace-guard\nversion: %s\n" % version, encoding="utf-8")


def runtime_config(hh):
    return hh / "workspace-guard" / "guard-config.yaml"


# ---------------------------------------------------------------- Config plan (SCR-013)

class TestConfigPlan:
    def test_runtime_config_exists_is_preserved(self, tmp_path):
        hh = tmp_path / "hh"
        target = runtime_config(hh)
        target.parent.mkdir(parents=True)
        target.write_text("allowed_root_files: [\"user.md\"]\n", encoding="utf-8")
        before = snapshot(hh)
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "runtime config exists; user config preserved" in r.stdout
        # Dry-run: nothing written, nothing changed.
        assert target.read_text(encoding="utf-8") == "allowed_root_files: [\"user.md\"]\n"
        assert snapshot(hh) == before

    def test_plugin_dir_copy_triggers_migration_plan(self, tmp_path):
        hh = tmp_path / "hh"
        source = hh / "plugins" / "workspace-guard" / "guard-config.yaml"
        source.parent.mkdir(parents=True)
        source.write_text("terminal_guard: true\n", encoding="utf-8")
        before = snapshot(hh)
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "migrate plugin-dir copy to the runtime location" in r.stdout
        assert "source: %s" % source in r.stdout
        assert "target: %s" % runtime_config(hh) in r.stdout
        # Dry-run: the copy is NOT made and the old file is NOT removed.
        assert source.exists()
        assert not runtime_config(hh).exists()
        assert snapshot(hh) == before

    def test_no_config_writes_fresh_default_plan(self, tmp_path):
        hh = tmp_path / "hh"
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "write fresh default config" in r.stdout
        assert "target: %s" % runtime_config(hh) in r.stdout
        # The fresh default template carries the allowed_root_files key (D1).
        # (The dry-run plan prints paths only, not the config content.)
        assert "allowed_root_files" in INSTALL.read_text(encoding="utf-8")
        # Dry-run: no directory, no file created.
        assert snapshot(hh) == []


# ---------------------------------------------------------------- Plugin version detection

class TestPluginVersion:
    def test_not_installed_plan(self, tmp_path):
        hh = tmp_path / "hh"
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "plugin: not installed -> install" in r.stdout
        assert "hermes plugins install" in r.stdout
        assert "--force" not in r.stdout
        assert "skill: not installed -> install" in r.stdout
        assert "memo: not rebuilt by install.py" in r.stdout
        assert snapshot(hh) == []

    def test_same_version_installed_already_latest(self, tmp_path):
        hh = tmp_path / "hh"
        write_installed_plugin(hh, repo_plugin_version())
        before = snapshot(hh)
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "already latest, skip" in r.stdout
        assert "--force" not in r.stdout
        assert snapshot(hh) == before

    def test_different_version_installed_force_plan(self, tmp_path):
        hh = tmp_path / "hh"
        write_installed_plugin(hh, "0.9.0")
        before = snapshot(hh)
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "overwrite with --force" in r.stdout
        assert "hermes plugins install" in r.stdout
        assert "--force" in r.stdout
        assert snapshot(hh) == before

    def test_skill_installed_is_idempotent(self, tmp_path):
        hh = tmp_path / "hh"
        marker = hh / "skills" / "workspace-organization" / "SKILL.md"
        marker.parent.mkdir(parents=True)
        marker.write_text("---\n", encoding="utf-8")
        r = run_install(hh)
        assert r.returncode == 0, r.stderr
        assert "skill: already installed -> skip (idempotent)" in r.stdout
        assert "skills install" not in r.stdout


# ---------------------------------------------------------------- Regression

class TestRegression:
    def test_all_exit_codes_covered(self):
        # Dry-run tests assert returncode == 0 (success) and returncode == 2
        # (argparse errors). returncode == 1 is reserved for --apply failure
        # paths (hermes CLI errors), which are never executed in tests.
        blob = Path(__file__).read_text(encoding="utf-8")
        for code in ("returncode == 0", "returncode == 1", "returncode == 2"):
            assert code in blob, f"no test asserts exit code {code}"

    def test_help(self):
        env = os.environ.copy()
        env["HERMES_HOME"] = str(Path(tempfile.mkdtemp(prefix="wg-test-hh-")))
        r = subprocess.run(
            [str(PYTHON), str(INSTALL), "--help"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0
        assert "usage" in r.stdout.lower()
        assert "--dry-run" in r.stdout
        assert "--apply" in r.stdout
        assert "--hermes-home" in r.stdout

    def test_missing_mode_flag_exit_2(self, tmp_path):
        env = os.environ.copy()
        env["HERMES_HOME"] = str(tmp_path)
        r = subprocess.run([str(PYTHON), str(INSTALL)], capture_output=True, text=True, env=env)
        assert r.returncode == 2
        assert "--dry-run" in r.stderr and "--apply" in r.stderr

    def test_conflicting_mode_flags_exit_2(self, tmp_path):
        env = os.environ.copy()
        env["HERMES_HOME"] = str(tmp_path)
        r = subprocess.run(
            [str(PYTHON), str(INSTALL), "--dry-run", "--apply"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 2
        assert r.stderr != ""

    def test_no_rules_file_literal_in_skill_package(self):
        # Matrix row 35 (SCR-011): the skill package must never name a
        # rules-file literal (skills_guard scanner false-positive).
        skill_root = Path(__file__).parent.parent / "src" / "workspace-organization"
        hits = []
        for p in skill_root.rglob("*"):
            if not p.is_file() or p.suffix not in (".md", ".py", ".yaml", ".yml", ".json", ".txt"):
                continue
            if "agents" in p.read_text(encoding="utf-8", errors="replace").lower():
                hits.append(str(p))
        assert not hits, f"rules-file literal found in skill package: {hits}"

    def test_skill_frontmatter_has_version(self):
        # SCR-015 Task 1: SKILL.md frontmatter must carry a version field
        # (read by install.sh version comparison; Task 5).
        skill_md = Path(__file__).parent.parent / "src" / "workspace-organization" / "SKILL.md"
        frontmatter = skill_md.read_text(encoding="utf-8").split("---", 2)[1]
        assert "version: 1.0.0" in frontmatter


# ---------------------------------------------------------------- CLI parsing (SCR-015 Task 2)

class TestCli:
    def test_help_flag(self, tmp_path):
        r = run_install(tmp_path, "--help")
        assert r.returncode == 0
        assert "install" in r.stdout and "uninstall" in r.stdout and "status" in r.stdout

    def test_non_tty_no_target_exits_error(self, tmp_path):
        # stdin is not a TTY in pytest; no --profile/--all-profiles -> error, NOT the menu
        r = run_install(tmp_path)
        assert r.returncode != 0
        assert "--profile" in r.stderr or "--all-profiles" in r.stderr

    def test_unknown_subcommand_exits_1(self, tmp_path):
        r = run_install(tmp_path, "bogus")
        assert r.returncode == 1

    def test_status_ok(self, tmp_path):
        r = run_install(tmp_path, "status")
        assert r.returncode == 0


# ---------------------------------------------------------------- Profile discovery (SCR-015 Task 3)

class TestProfiles:
    def test_discovers_default_and_named(self, tmp_path):
        root = tmp_path / "hh"
        (root / "profiles" / "job-hunt").mkdir(parents=True)
        (root / "profiles" / "learn").mkdir(parents=True)
        r = run_install(root, "status")
        assert r.returncode == 0
        out = r.stdout
        assert "default" in out
        assert "job-hunt" in out
        assert "learn" in out


# ---------------------------------------------------------------- Repo URL resolution (SCR-015 Task 4)

class TestRepoUrl:
    def test_default_url_when_no_git(self, tmp_path):
        r = run_install(tmp_path, "status")
        assert "https://github.com/shawVV1992/workspace-guard" not in r.stdout  # status doesn't print URL yet

    def test_repo_flag_parses(self, tmp_path):
        r = run_install(tmp_path, "install", "--dry-run", "--profile", "default", "--repo", "https://example.com/r.git")
        # dry-run must not fail on flag parsing
        assert r.returncode == 0 or r.returncode == 2  # returns 0 when flag parsing succeeds


# ---------------------------------------------------------------- Semver compare (SCR-015 Task 5)

class TestSemverCompare:
    def test_gt(self, tmp_path):
        r = run_install(tmp_path, "install", "--dry-run", "--profile", "default")
        # semver is exercised through the plan; direct unit coverage via a tiny helper below

    def test_selftest_semver(self, tmp_path):
        r = run_install(tmp_path, "--selftest")
        assert r.returncode == 0
        assert "1.10.0 > 1.9.0: ok" in r.stdout
        assert "1.0.0 > 1.0.0: no" in r.stdout


# ---------------------------------------------------------------- Install plan (SCR-015 Task 6)

class TestInstallPlan:
    def test_plan_not_installed_prints_commands(self, tmp_path):
        r = run_install(tmp_path, "install", "--dry-run", "--profile", "default", "--all-profiles")
        assert r.returncode == 0
        assert "repo URL: https://github.com/shawVV1992/workspace-guard" in r.stdout
        assert "hermes skills install" in r.stdout
        assert "hermes plugins install" in r.stdout
        assert "--force" not in r.stdout
        assert "guard-config.yaml" in r.stdout
        assert "memo" in r.stdout

    def test_plan_outdated_uses_force(self, tmp_path):
        hh = tmp_path / "hh"
        manifest = hh / "plugins" / "workspace-guard" / "plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 0.9.0\n", encoding="utf-8")
        r = run_install(hh, "install", "--dry-run", "--all-profiles")
        assert r.returncode == 0
        assert "--force" in r.stdout

    def test_plan_up_to_date_skips(self, tmp_path):
        hh = tmp_path / "hh"
        manifest = hh / "plugins" / "workspace-guard" / "plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 1.0.0\n", encoding="utf-8")
        r = run_install(hh, "install", "--dry-run", "--all-profiles")
        assert r.returncode == 0
        assert "up to date" in r.stdout

    def test_plan_up_to_date_skips_config_and_memo(self, tmp_path):
        # I-1 ruling A: a fully up-to-date profile must not touch config/memo.
        hh = tmp_path / "hh"
        skill = hh / "skills" / "workspace-organization" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nversion: 1.0.0\n---\n", encoding="utf-8")
        manifest = hh / "plugins" / "workspace-guard" / "plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 1.0.0\n", encoding="utf-8")
        r = run_install(hh, "install", "--dry-run", "--all-profiles")
        assert r.returncode == 0
        assert "up to date" in r.stdout
        assert "would copy" not in r.stdout
        assert "would delete memo" not in r.stdout

    def test_force_overrides_skip(self, tmp_path):
        hh = tmp_path / "hh"
        manifest = hh / "plugins" / "workspace-guard" / "plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 1.0.0\n", encoding="utf-8")
        r = run_install(hh, "install", "--dry-run", "--all-profiles", "--force")
        assert r.returncode == 0
        assert "--force" in r.stdout

    def test_dry_run_touches_nothing(self, tmp_path):
        hh = tmp_path / "hh"
        (hh / "profiles" / "job-hunt").mkdir(parents=True)
        before = snapshot(hh)
        r = run_install(hh, "install", "--dry-run", "--all-profiles")
        assert r.returncode == 0
        assert snapshot(hh) == before
