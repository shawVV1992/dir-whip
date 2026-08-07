"""Tests for S3 clean_tmp.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import os
import re
import stat
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "clean_tmp.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"

DAY = 86400


def run_script(*args, cwd=None):
    return subprocess.run(
        [str(PYTHON), str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def make_old(path, days_old=60):
    """Set a file/dir mtime `days_old` days in the past."""
    t = time.time() - (days_old * DAY)
    os.utime(str(path), (t, t))


def make_workspace_with_old_tmp(root, session_name="20260701_120000_Task", days_old=60):
    """Create a workspace with a session dir containing old .tmp/ files.

    Returns (root, tmp_dir, outputs_dir). .tmp/ gets one file with mtime
    `days_old` days in the past and one freshly created file.
    """
    session = root / session_name
    tmp_dir = session / ".tmp"
    outputs_dir = session / "Outputs"
    tmp_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Rules\n")

    old_file = tmp_dir / "old_debug.py"
    old_file.write_text("print('old')\n")
    make_old(old_file, days_old)

    new_file = tmp_dir / "new_debug.py"
    new_file.write_text("print('new')\n")

    return root, tmp_dir, outputs_dir


def snapshot(root):
    """Return a sorted list of relative paths under root, dirs with trailing '/'."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel != ".":
            entries.append(rel + "/")
        for f in filenames:
            entries.append(os.path.join(rel, f).replace(os.sep, "/"))
    return sorted(entries)


# ---------------------------------------------------------------- Core

class TestCore:
    def test_dry_run_is_default_lists_and_keeps_files(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout
        assert "new_debug.py" not in r.stdout
        assert "Dry run: 1 item(s) would be removed." in r.stdout
        assert (tmp_dir / "old_debug.py").exists()
        assert (tmp_dir / "new_debug.py").exists()

    def test_explicit_dry_run_flag_same_as_default(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout
        assert (tmp_dir / "old_debug.py").exists()

    def test_confirm_deletes_eligible_files(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout
        assert "Removed 1 item(s)." in r.stdout
        assert not (tmp_dir / "old_debug.py").exists()
        assert (tmp_dir / "new_debug.py").exists()

    def test_newer_files_not_listed_or_deleted(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert "new_debug.py" not in r.stdout
        assert (tmp_dir / "new_debug.py").exists()

    def test_nothing_to_clean_line(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--days", "99999")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "Nothing to clean."

    def test_default_workspace_is_cwd(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script(cwd=str(root))
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout


# ---------------------------------------------------------------- Traversal scope

class TestTraversalScope:
    def test_non_session_tmp_dir_ignored(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        stray = root / "random-dir" / ".tmp"
        stray.mkdir(parents=True)
        stray_file = stray / "stray_old.txt"
        stray_file.write_text("x")
        make_old(stray_file)
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "stray_old.txt" not in r.stdout
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert stray_file.exists()

    def test_root_level_tmp_ignored(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        root_tmp = root / ".tmp"
        root_tmp.mkdir()
        root_file = root_tmp / "root_old.txt"
        root_file.write_text("x")
        make_old(root_file)
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert root_file.exists()

    def test_bare_timestamp_session_scanned(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(
            tmp_path / "ws", session_name="20260701_120000"
        )
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout

    def test_invalid_timestamp_session_skipped(self, tmp_path):
        """A dir named 99999999_999999 matches regex but is not a real timestamp - should be skipped."""
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        invalid = root / "99999999_999999" / ".tmp"
        invalid.mkdir(parents=True)
        old_file = invalid / "old_file.txt"
        old_file.write_text("x")
        make_old(old_file)
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_file.txt" not in r.stdout
        assert "Dry run: 1 item(s) would be removed." in r.stdout  # only the valid session
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert old_file.exists()
        assert not (tmp_dir / "old_debug.py").exists()  # valid session still processed

    def test_multiple_sessions_all_processed(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        root2, tmp2, out2 = make_workspace_with_old_tmp(
            tmp_path / "ws", session_name="20260702_090000_OtherTask"
        )
        assert root == root2
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "Dry run: 2 item(s) would be removed." in r.stdout
        assert "20260701_120000_Task" in r.stdout
        assert "20260702_090000_OtherTask" in r.stdout
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert not (tmp_dir / "old_debug.py").exists()
        assert not (tmp2 / "old_debug.py").exists()

    def test_session_without_tmp_dir_skipped(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        (tmp_path / "ws" / "20260703_080000_NoTmp").mkdir()
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_outputs_never_touched_dry_run(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        deliverable = outputs_dir / "deliverable.pdf"
        deliverable.write_text("x")
        make_old(deliverable)  # old enough to be eligible if scanned
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "deliverable.pdf" not in r.stdout
        assert deliverable.exists()

    def test_outputs_never_touched_confirm(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        deliverable = outputs_dir / "deliverable.pdf"
        deliverable.write_text("x")
        make_old(deliverable)
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert "deliverable.pdf" not in r.stdout
        assert deliverable.exists()
        assert (outputs_dir / "new_debug.py").exists() is False  # sanity: only .tmp touched

    def test_workspace_root_files_never_touched(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        agents = root / "AGENTS.md"
        before = agents.read_text()
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert agents.exists()
        assert agents.read_text() == before

    def test_session_dir_files_outside_tmp_untouched(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        loose = tmp_dir.parent / "debug.py"
        loose.write_text("print(1)\n")
        make_old(loose)
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert loose.exists()

    def test_tmp_dir_itself_preserved(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert tmp_dir.is_dir()
        assert not (tmp_dir / "old_debug.py").exists()
        assert (tmp_dir / "new_debug.py").exists()

    def test_old_subdirectory_removed(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        sub = tmp_dir / "scratch"
        sub.mkdir()
        (sub / "inner.txt").write_text("x")
        make_old(sub / "inner.txt")
        make_old(sub)
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert ("scratch" in r.stdout) and ("inner.txt" not in r.stdout)
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert not sub.exists()
        assert tmp_dir.is_dir()

    def test_new_subdirectory_with_old_contents_kept(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        sub = tmp_dir / "recent"
        sub.mkdir()
        inner = sub / "inner_old.txt"
        inner.write_text("x")
        make_old(inner)  # file old, but the subdir itself is new
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert sub.exists()
        assert inner.exists()

    def test_dry_run_produces_zero_filesystem_changes(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        before = snapshot(root)
        r1 = run_script("--workspace", str(root))
        r2 = run_script("--workspace", str(root), "--dry-run")
        assert r1.returncode == 0 and r2.returncode == 0
        assert snapshot(root) == before
        assert "old_debug.py" in r1.stdout


# ---------------------------------------------------------------- Boundary

class TestBoundary:
    def test_days_zero_all_files_eligible(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--days", "0", "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout
        assert "new_debug.py" in r.stdout
        assert "Dry run: 2 item(s) would be removed." in r.stdout

    def test_days_negative_exit_2(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--days", "-5")
        assert r.returncode == 2
        assert r.stderr != ""

    def test_days_non_numeric_exit_2(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--days", "abc")
        assert r.returncode == 2
        assert r.stderr != ""

    def test_parent_dir_missing_exit_2(self, tmp_path):
        missing = tmp_path / "no_such_workspace"
        r = run_script("--workspace", str(missing))
        assert r.returncode == 2
        assert r.stderr != ""

    def test_empty_tmp_dir_nothing_to_clean(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        (tmp_dir / "old_debug.py").unlink()
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "Nothing to clean."

    def test_chinese_filename_handled(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        zh = tmp_dir / "调试脚本.py"
        zh.write_text("print(1)\n")
        make_old(zh)
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "调试脚本.py" in r.stdout
        assert zh.exists()
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert not zh.exists()

    def test_filename_with_spaces_handled(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        spaced = tmp_dir / "my debug file.txt"
        spaced.write_text("x")
        make_old(spaced)
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert "my debug file.txt" in r.stdout
        assert not spaced.exists()


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_output_paths_use_forward_slashes(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        line = [l for l in r.stdout.splitlines() if "old_debug.py" in l][0]
        assert "\\" not in line
        assert "/" in line

    def test_drive_letter_preserved(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        line = [l for l in r.stdout.splitlines() if "old_debug.py" in l][0]
        assert re.match(r"^[A-Za-z]:/", line) is not None

    def test_chinese_session_dir_name(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(
            tmp_path / "ws", session_name="20260701_120000_数据分析"
        )
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        assert "数据分析" in r.stdout
        assert not (tmp_dir / "old_debug.py").exists()

    def test_long_paths_near_max_path(self, tmp_path):
        root = tmp_path / "ws"
        base_len = (
            len(str(root))
            + len("/20260701_120000_")
            + len("/.tmp")
            + len("/old_debug.py")
        )
        task_len = max(1, 250 - base_len)
        session_name = "20260701_120000_" + ("T" * task_len)
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(root, session_name=session_name)
        target = str(tmp_dir / "old_debug.py")
        assert len(target) > 230  # near MAX_PATH
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout
        r2 = run_script("--workspace", str(root), "--confirm")
        assert r2.returncode == 0, r2.stderr
        assert not (tmp_dir / "old_debug.py").exists()


# ---------------------------------------------------------------- Regression

class TestRegression:
    def test_all_exit_codes_covered(self):
        blob = Path(__file__).read_text(encoding="utf-8")
        for code in ("returncode == 0", "returncode == 1", "returncode == 2"):
            assert code in blob, "no test asserts exit code %s" % code

    def test_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "usage" in r.stdout.lower()
        assert "--days" in r.stdout
        assert "--workspace" in r.stdout
        assert "--confirm" in r.stdout
        assert "--dry-run" in r.stdout
        assert "--parent" not in r.stdout

    def test_exit_1_when_delete_fails(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        blocked = tmp_dir / "blocked.txt"
        blocked.write_text("x")
        make_old(blocked)
        os.chmod(str(blocked), stat.S_IREAD)
        try:
            r = run_script("--workspace", str(root), "--confirm")
        finally:
            os.chmod(str(blocked), stat.S_IWRITE)
        assert r.returncode == 1
        assert r.stderr != ""
        assert blocked.exists()
        assert "Removed 1 item(s)." in r.stdout  # the other old file still removed

    def test_dry_run_with_readonly_file_still_exit_0(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        blocked = tmp_dir / "blocked.txt"
        blocked.write_text("x")
        make_old(blocked)
        os.chmod(str(blocked), stat.S_IREAD)
        try:
            r = run_script("--workspace", str(root), "--dry-run")
        finally:
            os.chmod(str(blocked), stat.S_IWRITE)
        assert r.returncode == 0, r.stderr
        assert "blocked.txt" in r.stdout

    def test_summary_line_format_confirm(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--confirm")
        assert r.returncode == 0, r.stderr
        lines = r.stdout.splitlines()
        assert lines[-1] == "Removed 1 item(s)."
        assert lines[-2].endswith("old_debug.py")

    def test_no_hardcoded_paths_in_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "E:/" not in source
        assert "C:/Users" not in source


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr

    def test_parent_alias_still_works(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--parent", str(root), "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "old_debug.py" in r.stdout

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
        session = ws / "20260701_120000_Task"
        (session / ".tmp").mkdir(parents=True)
        r = run_script("--workspace", str(ws))
        assert r.returncode == 2
        assert "not a valid Default Working Directory" in r.stderr

    def test_workspace_with_agents_md_passes(self, tmp_path):
        root, tmp_dir, outputs_dir = make_workspace_with_old_tmp(tmp_path / "ws")
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr

    def test_workspace_not_exist_exit_2(self, tmp_path):
        missing = tmp_path / "no_such_workspace"
        r = run_script("--workspace", str(missing))
        assert r.returncode == 2
