"""Tests for S5 init_workspace.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "init_workspace.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"

# Exact built-in template the script must write (see S5 spec).
TEMPLATE = """# Workspace Rules

## Session Directory Structure

Every conversation creates a session directory:

    YYYYMMDD_HHMMSS_TaskName/
    \u251c\u2500\u2500 Outputs/    <- formal deliverables only
    \u2514\u2500\u2500 .tmp/       <- intermediate files, safe to clean

## File Placement

- All deliverables go in a session dir's Outputs/
- Never save files to workspace root directly
- Shared space (<SHARED_SPACE_PATH>) requires explicit user confirmation
  # Configure your shared space path here

## Prohibitions

- No rm -rf, del /S/Q, bulk rename, or recursive delete
- No overwriting existing files without reading first
- No secrets or credentials in any file

## Conventions

- Absolute paths with forward slashes
- ASCII straight quotes only
- No emoji
"""


def run_script(*args, cwd=None, env_override=None):
    env = os.environ.copy()
    # Remove HERMES_WORKSPACE_ROOT by default so CWD fallback is deterministic.
    env.pop("HERMES_WORKSPACE_ROOT", None)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [str(PYTHON), str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


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


# ---------------------------------------------------------------- Core

class TestCore:
    def test_create_with_name(self, tmp_path):
        r = run_script("MyTask", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = assert_workspace(out, tmp_path, "MyTask")
        assert (ws / "AGENTS.md").is_file()

    def test_agents_content_matches_builtin_template(self, tmp_path):
        r = run_script("MyTask", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        content = (Path(out) / "AGENTS.md").read_text(encoding="utf-8")
        assert content == TEMPLATE

    def test_stdout_single_line_absolute_forward_slash(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        assert_stdout_path(r)

    def test_exit_code_zero_on_success(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0


# ---------------------------------------------------------------- Boundary

class TestBoundary:
    def test_illegal_chars_sanitized(self, tmp_path):
        r = run_script('a\\b/c:d*e?f"g<h>i|j', "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "a_b_c_d_e_f_g_h_i_j")
        assert (Path(out) / "AGENTS.md").is_file()

    def test_long_name_truncated_to_80(self, tmp_path):
        r = run_script("A" * 200, "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        name = out.rstrip("/").split("/")[-1]
        assert len(name) == 80, f"expected 80-char name, got {len(name)}"
        assert_workspace(out, tmp_path, "A" * 80)

    def test_chinese_name_preserved(self, tmp_path):
        r = run_script("数据分析", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "数据分析")
        assert (Path(out) / "AGENTS.md").is_file()

    def test_only_illegal_chars_becomes_underscores(self, tmp_path):
        r = run_script('\\/:*?"<>|', "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "_________")
        assert (Path(out) / "AGENTS.md").is_file()

    def test_empty_name_exit_2(self, tmp_path):
        r = run_script("", "--workspace", str(tmp_path))
        assert r.returncode == 2
        assert not (tmp_path / "AGENTS.md").exists()
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- --template

class TestTemplate:
    def test_template_valid_file(self, tmp_path):
        custom = tmp_path / "custom.md"
        custom.write_text("Custom rules line 1\nCustom rules line 2\n", encoding="utf-8", newline="\n")
        r = run_script("Task", "--workspace", str(tmp_path), "--template", str(custom))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert (Path(out) / "AGENTS.md").read_text(encoding="utf-8") == "Custom rules line 1\nCustom rules line 2\n"

    def test_template_missing_file_exit_1(self, tmp_path):
        missing = tmp_path / "nope.md"
        r = run_script("Task", "--workspace", str(tmp_path), "--template", str(missing))
        assert r.returncode == 1
        assert not (tmp_path / "Task").exists()

    def test_template_chinese_content_preserved(self, tmp_path):
        custom = tmp_path / "cn.md"
        custom.write_text("# 中文规则\n- 不删除文件\n", encoding="utf-8", newline="\n")
        r = run_script("Task", "--workspace", str(tmp_path), "--template", str(custom))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert (Path(out) / "AGENTS.md").read_text(encoding="utf-8") == "# 中文规则\n- 不删除文件\n"


# ---------------------------------------------------------------- Environment variable

class TestEnvVar:
    def test_env_var_sets_default_parent(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        r = run_script("Task", cwd=str(cwd), env_override={"HERMES_WORKSPACE_ROOT": str(root)})
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, root, "Task")

    def test_unset_env_defaults_to_cwd(self, tmp_path):
        r = run_script("Task", cwd=str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, tmp_path, "Task")

    def test_explicit_parent_overrides_env_var(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        r = run_script("Task", "--workspace", str(other), env_override={"HERMES_WORKSPACE_ROOT": str(root)})
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, other, "Task")


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_existing_dir_exit_2_no_overwrite(self, tmp_path):
        target = tmp_path / "Task"
        target.mkdir()
        agents = target / "AGENTS.md"
        agents.write_text("sentinel content", encoding="utf-8")
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 2
        out = assert_stdout_path(r)
        assert Path(out) == target
        assert agents.read_text(encoding="utf-8") == "sentinel content"
        assert r.stderr == ""

    def test_no_session_dirs_created(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        ws = Path(out)
        assert not (ws / "Outputs").exists()
        assert not (ws / ".tmp").exists()

    def test_no_files_other_than_agents_md(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        for root, dirs, files in os.walk(Path(out)):
            assert files == ["AGENTS.md"], f"unexpected files: {files} in {root}"

    def test_parent_missing_exit_1(self, tmp_path):
        missing = tmp_path / "no_such_parent"
        r = run_script("Task", "--workspace", str(missing))
        assert r.returncode == 1
        assert not missing.exists()


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_output_forward_slashes(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        assert "\\" not in assert_stdout_path(r)

    def test_drive_letter_preserved(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        assert re.match(r"^[A-Za-z]:/", assert_stdout_path(r))

    def test_unicode_parent_and_name(self, tmp_path):
        parent = tmp_path / "项目_工作区"
        parent.mkdir()
        r = run_script("任务分析", "--workspace", str(parent))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert "项目_工作区" in out
        assert_workspace(out, parent, "任务分析")
        assert (Path(out) / "AGENTS.md").read_text(encoding="utf-8") == TEMPLATE


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
        assert "--template" in r.stdout
        assert "--parent" not in r.stdout

    def test_no_hardcoded_paths_in_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "E:/" not in source
        assert "C:/Users" not in source


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr

    def test_parent_alias_still_works(self, tmp_path):
        r = run_script("Task", "--parent", str(tmp_path))
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
    def test_no_agents_md_required_in_parent(self, tmp_path):
        bare = tmp_path / "bare_parent"
        bare.mkdir()
        r = run_script("Task", "--workspace", str(bare))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_workspace(out, bare, "Task")
        assert (Path(out) / "AGENTS.md").is_file()

    def test_template_has_no_hardcoded_paths(self, tmp_path):
        r = run_script("Task", "--workspace", str(tmp_path))
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        content = (Path(out) / "AGENTS.md").read_text(encoding="utf-8")
        assert "E:/" not in content
        assert "C:/" not in content
        assert "<SHARED_SPACE_PATH>" in content
