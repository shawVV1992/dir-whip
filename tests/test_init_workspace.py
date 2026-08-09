"""Tests for S5 init_workspace.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "init_workspace.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"


def run_script(*args, cwd=None, hh=None, env_override=None):
    """Run the script with HERMES_HOME pointed at an isolated tmp dir."""
    env = os.environ.copy()
    # Remove HERMES_WORKSPACE_ROOT by default so CWD fallback is deterministic.
    env.pop("HERMES_WORKSPACE_ROOT", None)
    if hh is None:
        hh = Path(tempfile.mkdtemp(prefix="wg-test-hh-"))  # isolation net
    env["HERMES_HOME"] = str(hh)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [str(PYTHON), str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def write_memo(hh, workspaces):
    """Write a real profile-workspaces.json (SCR-001 schema) under hh."""
    memo_dir = Path(hh) / "workspace-guard"
    memo_dir.mkdir(parents=True, exist_ok=True)
    profiles = {}
    for name, ws in workspaces.items():
        profiles[name] = {
            "workspace": str(ws),
            "status": "valid",
            "changed_at": "2026-08-09T00:00:00",
        }
    data = {"synced_at": "2026-08-09T00:00:00", "profiles": profiles}
    (memo_dir / "profile-workspaces.json").write_text(json.dumps(data), encoding="utf-8")


def memo_path(hh):
    return Path(hh) / "workspace-guard" / "profile-workspaces.json"


def assert_stdout_path(result):
    """Assert stdout is exactly one line containing an absolute forward-slash path."""
    lines = result.stdout.strip("\n").split("\n")
    assert len(lines) == 1, f"expected single stdout line, got: {result.stdout!r}"
    out = lines[0]
    assert "\\" not in out, f"backslashes not allowed in output: {out!r}"
    assert Path(out).is_absolute() or re.match(r"^[A-Za-z]:/", out), f"not an absolute path: {out!r}"
    return out


def assert_workspace(out, parent, expected_name):
    assert Path(out) == Path(parent) / expected_name
    ws = Path(out)
    assert ws.is_dir()
    return ws


def assert_empty(ws):
    """The created workspace must contain NO files and NO subdirectories
    (mkdir + sanitize only; no rules file, no template, no memo write)."""
    entries = list(ws.iterdir())
    assert entries == [], f"workspace must be empty after init, found: {entries}"


# ---------------------------------------------------------------- Core

class TestCore:
    def test_create_with_name_dir_exists_and_empty(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("MyTask", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, tmp_path, "MyTask")
        assert_empty(ws)

    def test_stdout_single_line_absolute_forward_slash(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert_stdout_path(r)

    def test_exit_code_zero_on_success(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0

    def test_memo_not_touched_when_absent(self, tmp_path):
        # Scenario B: no memo before -> still no memo after.
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert not memo_path(hh).exists(), "init must not create the memo"

    def test_memo_content_unchanged_when_present(self, tmp_path):
        # Scenario A: existing memo -> content byte-identical after init.
        hh = tmp_path / "hermes_home"
        other = tmp_path / "other_ws"
        other.mkdir()
        write_memo(hh, {"default": other})
        before = memo_path(hh).read_text(encoding="utf-8")
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert memo_path(hh).read_text(encoding="utf-8") == before

    def test_registration_hint_when_plugin_trace(self, tmp_path):
        hh = tmp_path / "hermes_home"
        (hh / "workspace-guard").mkdir(parents=True)  # plugin trace present
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip("\n").split("\n")
        assert len(lines) == 2, f"expected path + registration hint, got: {r.stdout!r}"
        out = lines[0]
        assert "\\" not in out
        assert "workspace_guard_register_workspace" in lines[1]
        assert_workspace(out, tmp_path, "Task")

    def test_single_line_when_no_plugin_trace(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert_stdout_path(r)  # asserts exactly one line


# ---------------------------------------------------------------- Boundary

class TestBoundary:
    def test_illegal_chars_sanitized(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script('a\\b/c:d*e?f"g<h>i|j', "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, tmp_path, "a_b_c_d_e_f_g_h_i_j")
        assert_empty(ws)

    def test_long_name_truncated_to_80(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("A" * 200, "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        name = out.rstrip("/").split("/")[-1]
        assert len(name) == 80, f"expected 80-char name, got {len(name)}"
        ws = assert_workspace(out, tmp_path, "A" * 80)
        assert_empty(ws)

    def test_chinese_name_preserved(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("数据分析", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, tmp_path, "数据分析")
        assert_empty(ws)

    def test_only_illegal_chars_becomes_underscores(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script('\\/:*?"<>|', "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, tmp_path, "_________")
        assert_empty(ws)

    def test_empty_name_exit_2(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 2
        assert not (tmp_path / "AGENTS.md").exists()
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- Environment variable

class TestEnvVar:
    def test_env_var_sets_default_parent(self, tmp_path):
        hh = tmp_path / "hermes_home"
        root = tmp_path / "root"
        root.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        r = run_script("Task", cwd=str(cwd), hh=hh, env_override={"HERMES_WORKSPACE_ROOT": str(root)})
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, root, "Task")

    def test_unset_env_defaults_to_cwd(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", cwd=str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "Task")

    def test_explicit_parent_overrides_env_var(self, tmp_path):
        hh = tmp_path / "hermes_home"
        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        r = run_script("Task", "--workspace", str(other), hh=hh, env_override={"HERMES_WORKSPACE_ROOT": str(root)})
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, other, "Task")


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_existing_dir_exit_2_no_overwrite(self, tmp_path):
        hh = tmp_path / "hermes_home"
        target = tmp_path / "Task"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("sentinel content", encoding="utf-8")
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 2
        out = assert_stdout_path(r)
        assert Path(out) == target
        assert sentinel.read_text(encoding="utf-8") == "sentinel content"
        assert r.stderr == ""

    def test_duplicate_init_does_not_touch_memo(self, tmp_path):
        hh = tmp_path / "hermes_home"
        target = tmp_path / "Task"
        target.mkdir()
        write_memo(hh, {"default": tmp_path / "other_ws"})
        (tmp_path / "other_ws").mkdir()
        before = memo_path(hh).read_text(encoding="utf-8")
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 2
        assert_stdout_path(r)  # duplicate init still prints the target path
        assert memo_path(hh).read_text(encoding="utf-8") == before

    def test_no_session_dirs_created(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = Path(out)
        assert not (ws / "Outputs").exists()
        assert not (ws / ".tmp").exists()

    def test_no_files_written(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        for root, dirs, files in os.walk(Path(out)):
            assert not files, f"init must not write files, found: {files} in {root}"
            assert not dirs, f"init must not create subdirectories, found: {dirs} in {root}"

    def test_parent_missing_exit_1(self, tmp_path):
        hh = tmp_path / "hermes_home"
        missing = tmp_path / "no_such_parent"
        r = run_script("Task", "--workspace", str(missing), hh=hh)
        assert r.returncode == 1
        assert not missing.exists()


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_output_forward_slashes(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert "\\" not in assert_stdout_path(r)

    def test_drive_letter_preserved(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        assert re.match(r"^[A-Za-z]:/", assert_stdout_path(r))

    def test_unicode_parent_and_name(self, tmp_path):
        hh = tmp_path / "hermes_home"
        parent = tmp_path / "项目_工作区"
        parent.mkdir()
        r = run_script("任务分析", "--workspace", str(parent), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert "项目_工作区" in out
        ws = assert_workspace(out, parent, "任务分析")
        assert_empty(ws)


# ---------------------------------------------------------------- Regression

class TestRegression:
    def test_all_exit_codes_covered(self):
        blob = Path(__file__).read_text(encoding="utf-8")
        for code in ("returncode == 0", "returncode == 1", "returncode == 2"):
            assert code in blob, f"no test asserts exit code {code}"

    def test_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "usage" in r.stdout.lower()
        assert "name" in r.stdout.lower()
        assert "--workspace" in r.stdout
        assert "--template" not in r.stdout  # --template removed (SCR-011)
        assert "--parent" not in r.stdout

    def test_template_flag_rejected_by_argparse(self, tmp_path):
        # --template was removed: an explicit flag is an argparse error (exit 2).
        hh = tmp_path / "hermes_home"
        custom = tmp_path / "custom.md"
        custom.write_text("x")
        r = run_script("Task", "--workspace", str(tmp_path), "--template", str(custom), hh=hh)
        assert r.returncode == 2
        assert "--template" in r.stderr
        assert not (tmp_path / "Task").exists()

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

    def test_no_hardcoded_paths_in_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "E:/" not in source
        assert "C:/Users" not in source


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--workspace", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr

    def test_parent_alias_still_works(self, tmp_path):
        hh = tmp_path / "hermes_home"
        r = run_script("Task", "--parent", str(tmp_path), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "Task")

    def test_parent_hidden_from_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "--workspace" in r.stdout
        assert "--parent" not in r.stdout


# ---------------------------------------------------------------- Boundary exemption

class TestBoundaryExemption:
    def test_bare_parent_accepted(self, tmp_path):
        # init is exempt from workspace validation: it creates new workspaces.
        hh = tmp_path / "hermes_home"
        bare = tmp_path / "bare_parent"
        bare.mkdir()
        r = run_script("Task", "--workspace", str(bare), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, bare, "Task")
        assert_empty(ws)
