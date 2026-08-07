"""Tests for S1 create_session_dir.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "create_session_dir.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"


def run_script(*args, cwd=None):
    return subprocess.run(
        [str(PYTHON), str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def make_workspace(tmp_path, name="ws"):
    """Create a valid Default Working Directory (with AGENTS.md)."""
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text("# Rules\n")
    return ws


def assert_stdout_path(result):
    """Assert stdout is exactly one line containing an absolute forward-slash path."""
    lines = result.stdout.strip("\n").split("\n")
    assert len(lines) == 1, f"expected single stdout line, got: {result.stdout!r}"
    out = lines[0]
    assert "\\" not in out, f"backslashes not allowed in output: {out!r}"
    assert Path(out).is_absolute() or re.match(r"^[A-Za-z]:/", out), f"not an absolute path: {out!r}"
    return out


def is_valid_session_dir(out, task_name=None, ts_regex=r"\d{8}_\d{6}"):
    name = out.rstrip("/").split("/")[-1]
    if task_name is None:
        assert re.fullmatch(ts_regex, name), f"expected bare timestamp name, got: {name!r}"
    else:
        assert re.fullmatch(ts_regex + "_" + task_name, name), f"unexpected dir name: {name!r}"
    return Path(out)


def assert_structure(session_dir):
    assert session_dir.is_dir()
    assert (session_dir / "Outputs").is_dir()
    assert (session_dir / ".tmp").is_dir()


# ---------------------------------------------------------------- Core

class TestCore:
    def test_create_with_task_name(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("MyTask", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "MyTask")
        assert_structure(session)

    def test_create_without_task_name(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, None)
        assert_structure(session)

    def test_stdout_single_line_absolute_forward_slash(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        assert_stdout_path(r)

    def test_exit_code_zero_on_success(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr

    def test_parent_alias_still_works(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--parent", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_structure(Path(out))

    def test_parent_hidden_from_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "--workspace" in r.stdout
        assert "--parent" not in r.stdout


# ---------------------------------------------------------------- Boundary validation

class TestBoundaryValidation:
    def test_workspace_without_agents_md_exit_2(self, tmp_path):
        ws = tmp_path / "no_agents"
        ws.mkdir()
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 2
        assert "not a valid Default Working Directory" in r.stderr

    def test_workspace_with_agents_md_passes(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr

    def test_workspace_not_exist_exit_1(self, tmp_path):
        missing = tmp_path / "no_such_workspace"
        r = run_script("Task", "--workspace", str(missing))
        assert r.returncode == 1
        assert not missing.exists()


# ---------------------------------------------------------------- Boundary

class TestBoundary:
    def test_empty_task_name_same_as_omitted(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, None)
        assert_structure(session)

    def test_long_task_name_truncated_to_80(self, tmp_path):
        ws = make_workspace(tmp_path)
        long_name = "A" * 200
        r = run_script(long_name, "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        name = out.rstrip("/").split("/")[-1]
        task_part = name[16:]
        assert len(task_part) == 80, f"expected 80-char task part, got {len(task_part)}"
        assert_structure(Path(out))

    def test_chinese_task_name_preserved(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("数据分析", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "数据分析")
        assert_structure(session)

    def test_task_name_with_spaces_preserved(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("My Task Name", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "My Task Name")
        assert_structure(session)

    def test_task_name_illegal_chars_replaced(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script('a\\b/c:d*e?f"g<h>i|j', "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "a_b_c_d_e_f_g_h_i_j")
        assert_structure(session)

    def test_task_name_only_illegal_chars(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script('\\/:*?"<>|', "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "_________")
        assert_structure(session)


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_existing_directory_exit_2_no_overwrite(self, tmp_path):
        ws = make_workspace(tmp_path)
        r1 = run_script("Existing", "--workspace", str(ws))
        assert r1.returncode == 0, r1.stderr
        out1 = assert_stdout_path(r1)
        target = Path(out1)
        (target / "precious.txt").write_text("keep me")
        r2 = run_script("Existing", "--workspace", str(ws))
        assert r2.returncode == 2
        out2 = assert_stdout_path(r2)
        assert out2 == out1
        assert (target / "precious.txt").read_text() == "keep me"
        assert r2.stderr == ""

    def test_never_writes_files(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = Path(out)
        assert_structure(session)
        for root, dirs, files in __import__("os").walk(session):
            assert not files, f"script must not create files, found: {files} in {root}"


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_output_forward_slashes(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert "\\" not in out

    def test_drive_letter_preserved(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert re.match(r"^[A-Za-z]:/", out), f"drive letter missing: {out!r}"

    def test_unicode_workspace_dir(self, tmp_path):
        ws = tmp_path / "项目_工作区"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("# Rules\n")
        r = run_script("任务", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert "项目_工作区" in out
        session = is_valid_session_dir(out, "任务")
        assert_structure(session)


# ---------------------------------------------------------------- Regression

class TestRegression:
    def test_all_exit_codes_covered(self):
        blob = Path(__file__).read_text(encoding="utf-8")
        for code in ("returncode == 0", "returncode == 1", "returncode == 2"):
            assert code in blob, f"no test asserts exit code {code}"

    def test_stdout_no_trailing_whitespace_beyond_newline(self, tmp_path):
        ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws))
        assert r.returncode == 0, r.stderr
        assert r.stdout.endswith("\n")
        assert r.stdout.rstrip("\n").rstrip() == r.stdout.rstrip("\n"), "trailing whitespace on path line"

    def test_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "usage" in r.stdout.lower()
        assert "taskname" in r.stdout.lower() or "task_name" in r.stdout.lower()
        assert "--workspace" in r.stdout

    def test_no_hardcoded_paths_in_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "E:/" not in source
        assert "C:/Users" not in source
