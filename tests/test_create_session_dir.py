"""Tests for S1 create_session_dir.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "create_session_dir.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"

# Exact standalone-mode warning from workspace_resolver.STANDALONE_WARNING.
STANDALONE_WARNING = (
    "warning: memo unavailable and no workspace-guard plugin detected; "
    "standalone mode (trusting the provided --workspace)\n"
)


def run_script(*args, cwd=None, hh=None, env_override=None):
    """Run the script with HERMES_HOME pointed at an isolated tmp dir."""
    env = os.environ.copy()
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
    """Write a real profile-workspaces.json (SCR-001 schema) under hh.

    Schema: {"synced_at": iso, "profiles": {name: {"workspace", "status",
    "changed_at"}}}.
    """
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


def make_workspace(tmp_path, name="ws"):
    """Create an isolated HERMES_HOME + a memo-registered workspace.

    Returns (hh, ws): hh is the fake Hermes home (memo lives under
    hh/workspace-guard/profile-workspaces.json), ws is a real directory.
    """
    hh = tmp_path / "hermes_home"
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    write_memo(hh, {"default": ws})
    return hh, ws


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
        hh, ws = make_workspace(tmp_path)
        r = run_script("MyTask", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "MyTask")
        assert_structure(session)

    def test_create_without_task_name(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, None)
        assert_structure(session)

    def test_stdout_single_line_absolute_forward_slash(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        assert_stdout_path(r)

    def test_exit_code_zero_on_success(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr

    def test_parent_alias_still_works(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--parent", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert_structure(Path(out))

    def test_parent_hidden_from_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "--workspace" in r.stdout
        assert "--parent" not in r.stdout


# ---------------------------------------------------------------- Memo validation (SCR-011)

class TestMemoValidation:
    def test_memo_exact_match_passes_without_warning(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        assert r.stderr == "", f"expected no warning with memo present, got: {r.stderr!r}"
        assert_structure(Path(assert_stdout_path(r)))

    def test_memo_match_is_separator_and_case_normalized(self, tmp_path):
        # Memo stores the workspace with forward slashes; the script receives
        # a native (backslash) path. normalize_path must still match.
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        write_memo(hh, {"default": str(ws).replace(os.sep, "/")})
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        assert_structure(Path(assert_stdout_path(r)))

    def test_unregistered_existing_dir_exit_2_with_prompt(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        write_memo(hh, {"default": tmp_path / "other"})
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 2
        assert "not a registered profile workspace" in r.stderr
        assert "workspace_guard_register_workspace" in r.stderr

    def test_memo_missing_no_plugin_trace_standalone(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "ws"
        ws.mkdir()
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        assert r.stderr == STANDALONE_WARNING
        assert "warning" not in r.stdout  # stdout stays clean
        assert_structure(Path(assert_stdout_path(r)))

    def test_corrupt_memo_with_plugin_trace_fail_closed(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "ws"
        ws.mkdir()
        memo_dir = hh / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text("{ not json", encoding="utf-8")
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 2
        assert "workspace_update" in r.stderr

    def test_plugin_trace_memo_missing_fail_closed(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "ws"
        ws.mkdir()
        (hh / "workspace-guard").mkdir(parents=True)  # plugin trace only
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 2
        assert "workspace_update" in r.stderr

    def test_profile_narrow_match_accepts_own_profile(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws_a = tmp_path / "ws_a"
        ws_a.mkdir()
        ws_b = tmp_path / "ws_b"
        ws_b.mkdir()
        write_memo(hh, {"default": ws_a, "other": ws_b})
        r = run_script("Task", "--workspace", str(ws_b), "--profile", "other", hh=hh)
        assert r.returncode == 0, r.stderr
        assert_structure(Path(assert_stdout_path(r)))

    def test_profile_narrow_match_rejects_other_profile_workspace(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws_a = tmp_path / "ws_a"
        ws_a.mkdir()
        ws_b = tmp_path / "ws_b"
        ws_b.mkdir()
        write_memo(hh, {"default": ws_a, "other": ws_b})
        r = run_script("Task", "--workspace", str(ws_a), "--profile", "other", hh=hh)
        assert r.returncode == 2
        assert "not a registered profile workspace" in r.stderr

    def test_profile_with_unknown_name_exit_2(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), "--profile", "ghost", hh=hh)
        assert r.returncode == 2
        assert "not a registered profile workspace" in r.stderr


# ---------------------------------------------------------------- Boundary validation

class TestBoundaryValidation:
    def test_registered_workspace_passes(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr

    def test_workspace_not_exist_exit_1(self, tmp_path):
        hh = tmp_path / "hermes_home"
        missing = tmp_path / "no_such_workspace"
        r = run_script("Task", "--workspace", str(missing), hh=hh)
        assert r.returncode == 1
        assert not missing.exists()


# ---------------------------------------------------------------- Boundary

class TestBoundary:
    def test_empty_task_name_same_as_omitted(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, None)
        assert_structure(session)

    def test_long_task_name_truncated_to_80(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        long_name = "A" * 200
        r = run_script(long_name, "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        name = out.rstrip("/").split("/")[-1]
        task_part = name[16:]
        assert len(task_part) == 80, f"expected 80-char task part, got {len(task_part)}"
        assert_structure(Path(out))

    def test_chinese_task_name_preserved(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("数据分析", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "数据分析")
        assert_structure(session)

    def test_task_name_with_spaces_preserved(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("My Task Name", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "My Task Name")
        assert_structure(session)

    def test_task_name_illegal_chars_replaced(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script('a\\b/c:d*e?f"g<h>i|j', "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "a_b_c_d_e_f_g_h_i_j")
        assert_structure(session)

    def test_task_name_only_illegal_chars(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script('\\/:*?"<>|', "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = is_valid_session_dir(out, "_________")
        assert_structure(session)


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_existing_directory_exit_2_no_overwrite(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r1 = run_script("Existing", "--workspace", str(ws), hh=hh)
        assert r1.returncode == 0, r1.stderr
        out1 = assert_stdout_path(r1)
        target = Path(out1)
        (target / "precious.txt").write_text("keep me")
        # The session dir name embeds the current second, so the collision
        # only occurs when r2 lands in the same second as r1. Retry until
        # it does (a non-colliding attempt merely creates another session
        # dir inside tmp_path, which is harmless).
        r2 = None
        for _ in range(10):
            r2 = run_script("Existing", "--workspace", str(ws), hh=hh)
            if r2.returncode == 2:
                break
        assert r2 is not None, "no same-second collision within 10 attempts"
        assert r2.returncode == 2, r2.stdout
        out2 = assert_stdout_path(r2)
        assert out2 == out1
        assert (target / "precious.txt").read_text() == "keep me"
        assert r2.stderr == ""

    def test_never_writes_files(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        session = Path(out)
        assert_structure(session)
        for root, dirs, files in __import__("os").walk(session):
            assert not files, f"script must not create files, found: {files} in {root}"


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_output_forward_slashes(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert "\\" not in out

    def test_drive_letter_preserved(self, tmp_path):
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        out = assert_stdout_path(r)
        assert re.match(r"^[A-Za-z]:/", out), f"drive letter missing: {out!r}"

    def test_unicode_workspace_dir(self, tmp_path):
        hh = tmp_path / "hermes_home"
        ws = tmp_path / "项目_工作区"
        ws.mkdir()
        write_memo(hh, {"default": ws})
        r = run_script("任务", "--workspace", str(ws), hh=hh)
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
        hh, ws = make_workspace(tmp_path)
        r = run_script("Task", "--workspace", str(ws), hh=hh)
        assert r.returncode == 0, r.stderr
        assert r.stdout.endswith("\n")
        assert r.stdout.rstrip("\n").rstrip() == r.stdout.rstrip("\n"), "trailing whitespace on path line"

    def test_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "usage" in r.stdout.lower()
        assert "taskname" in r.stdout.lower() or "task_name" in r.stdout.lower()
        assert "--workspace" in r.stdout
        assert "--profile" in r.stdout
        assert "--parent" not in r.stdout

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
