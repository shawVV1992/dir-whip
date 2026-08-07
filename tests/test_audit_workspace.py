"""Tests for S2 audit_workspace.py.

All tests use pytest tmp_path isolation and invoke the script via
subprocess with the project venv python. Never touches HermesWorkspace.
"""

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "src" / "workspace-organization" / "scripts" / "audit_workspace.py"
PYTHON = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"


def run_script(*args, cwd=None):
    return subprocess.run(
        [str(PYTHON), str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def make_session(root, name, outputs=True, tmp=True):
    """Create a session dir (name given as-is) with optional Outputs/ and .tmp/."""
    session = root / name
    session.mkdir(exist_ok=True)
    if outputs:
        (session / "Outputs").mkdir(exist_ok=True)
    if tmp:
        (session / ".tmp").mkdir(exist_ok=True)
    return session


def make_clean_workspace(root):
    """Create a minimal compliant workspace for testing."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("# Rules\n")
    make_session(root, "20260731_120000_TestTask")
    return root


def snapshot(root):
    """Return a sorted list of relative paths under root, directories with a trailing '/'."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir != ".":
            entries.append(rel_dir + "/")
        for f in filenames:
            entries.append(os.path.join(rel_dir, f).replace(os.sep, "/"))
    return sorted(entries)


def parse_blocks(text):
    """Split plain-text violation output into blocks of non-empty lines."""
    return [b.splitlines() for b in text.split("---") if b.strip()]


# ---------------------------------------------------------------- Core

class TestCore:
    def test_clean_workspace_ok_exit_0(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "OK"

    def test_workspace_with_violations_exit_1(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "stray.txt" in r.stdout

    def test_non_existent_target_exit_2(self, tmp_path):
        missing = tmp_path / "no_such_dir"
        r = run_script(str(missing))
        assert r.returncode == 2
        assert r.stderr != ""

    def test_target_is_a_file_exit_2(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("x")
        r = run_script(str(f))
        assert r.returncode == 2
        assert r.stderr != ""


# ---------------------------------------------------------------- Check 1: root-level files

class TestCheck1:
    def test_stray_file_at_root_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "script.py").write_text("print(1)\n")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 1" in r.stdout.lower()
        assert "script.py" in r.stdout

    def test_notes_txt_at_root_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "notes.txt").write_text("hi")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 1" in r.stdout.lower()

    def test_only_agents_md_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Check 2: root-level Outputs/

class TestCheck2:
    def test_root_outputs_dir_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "Outputs").mkdir()
        (root / "Outputs" / "report.pdf").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 2" in r.stdout.lower()
        assert "Outputs" in r.stdout

    def test_no_root_outputs_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Check 3: session dir format

class TestCheck3:
    def test_invalid_folder_name_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "my-folder").mkdir()
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 3" in r.stdout.lower()
        assert "my-folder" in r.stdout

    def test_invalid_timestamp_folder_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20269999_000000_Bad")  # month 99 is not a real date
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 3" in r.stdout.lower()
        assert "check 4" not in r.stdout.lower()

    def test_valid_session_name_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000_Task")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"

    def test_bare_timestamp_session_name_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"

    def test_hermes_dir_whitelisted(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / ".hermes").mkdir()
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Check 4: session subdirectories

class TestCheck4:
    def test_session_missing_tmp_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000_NoTmp", tmp=False)
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 4" in r.stdout.lower()
        assert "NoTmp" in r.stdout

    def test_session_missing_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000_NoOut", outputs=False)
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 4" in r.stdout.lower()
        assert "NoOut" in r.stdout

    def test_session_with_both_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Check 5: Outputs/ content blacklist

class TestCheck5:
    def test_pycache_in_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "__pycache__").mkdir()
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 5" in r.stdout.lower()
        assert "__pycache__" in r.stdout

    def test_pyc_file_in_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "mod.pyc").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 5" in r.stdout.lower()
        assert "mod.pyc" in r.stdout

    def test_node_modules_in_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "node_modules").mkdir()
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 5" in r.stdout.lower()
        assert "node_modules" in r.stdout

    def test_ds_store_in_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / ".DS_Store").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 5" in r.stdout.lower()
        assert ".DS_Store" in r.stdout

    def test_thumbs_db_in_outputs_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "Thumbs.db").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 5" in r.stdout.lower()
        assert "Thumbs.db" in r.stdout

    def test_clean_outputs_no_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "deliverable.pdf").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"

    def test_only_immediate_level_checked(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        nested = root / "20260731_120000_TestTask" / "Outputs" / "sub" / "__pycache__"
        nested.mkdir(parents=True)
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Check 6: script files in session dir

class TestCheck6:
    def test_py_in_session_dir_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "debug.py").write_text("print(1)\n")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 6" in r.stdout.lower()
        assert "debug.py" in r.stdout

    def test_bat_in_session_dir_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "run.bat").write_text("@echo off\n")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 6" in r.stdout.lower()
        assert "run.bat" in r.stdout

    def test_sh_in_session_dir_is_violation(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "run.sh").write_text("echo hi\n")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 6" in r.stdout.lower()

    def test_script_in_tmp_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / ".tmp" / "debug.py").write_text("print(1)\n")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"

    def test_script_in_outputs_clean(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "20260731_120000_TestTask" / "Outputs" / "tool.py").write_text("print(1)\n")
        r = run_script(str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK"


# ---------------------------------------------------------------- Output format

class TestOutputFormat:
    def test_plain_text_contains_check_number_path_suggestion(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        assert "check 1" in r.stdout.lower()
        assert "stray.txt" in r.stdout
        assert "suggest" in r.stdout.lower()

    def test_plain_text_multiple_blocks_separated(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        (root / "my-folder").mkdir()
        r = run_script(str(root))
        blocks = parse_blocks(r.stdout)
        assert len(blocks) == 2
        assert any("stray.txt" in " ".join(b) for b in blocks)
        assert any("my-folder" in " ".join(b) for b in blocks)

    def test_json_violations_structure(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script("--json", str(root))
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        v = data[0]
        assert v["check"] == 1
        assert v["name"]
        assert v["path"].endswith("stray.txt")
        assert v["suggestion"]

    def test_json_empty_array_compliant(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script("--json", str(root))
        assert r.returncode == 0
        assert json.loads(r.stdout) == []

    def test_json_all_checks_present_in_one_workspace(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        (root / "Outputs").mkdir()
        (root / "my-folder").mkdir()
        make_session(root, "20260731_120000_MissingTmp", tmp=False)
        (root / "20260731_120000_TestTask" / "Outputs" / "__pycache__").mkdir()
        (root / "20260731_120000_TestTask" / "debug.py").write_text("print(1)\n")
        r = run_script("--json", str(root))
        assert r.returncode == 1
        data = json.loads(r.stdout)
        found = {v["check"] for v in data}
        assert found == {1, 2, 3, 4, 5, 6}


# ---------------------------------------------------------------- Windows-specific

class TestWindowsSpecific:
    def test_backslash_input_path(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root).replace("/", os.sep))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "OK"

    def test_forward_slash_input_path(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root).replace(os.sep, "/"))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "OK"

    def test_output_forward_slashes(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script("--json", str(root))
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert "\\" not in data[0]["path"]
        assert re_match_drive(data[0]["path"])

    def test_chinese_session_dir_names(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000_数据分析")
        r = run_script(str(root))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "OK"

    def test_chinese_violation_path_reported(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        make_session(root, "20260731_120000_数据分析")
        (root / "20260731_120000_数据分析" / "调试.py").write_text("print(1)\n")
        r = run_script("--json", str(root))
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert data[0]["check"] == 6
        assert "调试.py" in data[0]["path"]


def re_match_drive(path):
    import re
    return re.match(r"^[A-Za-z]:/", path) is not None


# ---------------------------------------------------------------- Safety

class TestSafety:
    def test_audit_is_read_only(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        (root / "my-folder").mkdir()
        (root / "Outputs").mkdir()
        (root / "20260731_120000_TestTask" / "Outputs" / "__pycache__").mkdir()
        (root / "20260731_120000_TestTask" / "debug.py").write_text("print(1)\n")
        before = snapshot(root)
        r1 = run_script(str(root))
        r2 = run_script("--json", str(root))
        assert r1.returncode == 1 and r2.returncode == 1
        assert snapshot(root) == before

    def test_no_recursion_into_node_modules(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        nm = root / "20260731_120000_TestTask" / "Outputs" / "node_modules"
        nm.mkdir(parents=True)
        for i in range(5):
            d = nm / ("pkg%d" % i)
            d.mkdir()
            (d / "deep.txt").write_text("x" * 1000)
        r = run_script(str(root), cwd=str(tmp_path))
        assert r.returncode == 1
        assert "node_modules" in r.stdout


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
        assert "--json" in r.stdout
        assert "root" in r.stdout.lower()

    def test_multiple_violations_all_reported(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "a.txt").write_text("x")
        (root / "b.txt").write_text("x")
        (root / "c.txt").write_text("x")
        r = run_script(str(root))
        assert r.returncode == 1
        for name in ("a.txt", "b.txt", "c.txt"):
            assert name in r.stdout

    def test_no_hardcoded_paths_in_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "E:/" not in source
        assert "C:/Users" not in source


# ---------------------------------------------------------------- Naming alignment

class TestNamingAlignment:
    def test_workspace_flag_accepted(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr

    def test_positional_still_works(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script(str(root))
        assert r.returncode == 0, r.stderr

    def test_workspace_flag_in_help(self):
        r = run_script("--help")
        assert r.returncode == 0
        assert "--workspace" in r.stdout


# ---------------------------------------------------------------- Boundary validation

class TestBoundaryValidation:
    def test_dir_without_agents_md_exit_2(self, tmp_path):
        ws = tmp_path / "no_agents"
        ws.mkdir()
        r = run_script("--workspace", str(ws))
        assert r.returncode == 2
        assert "not a valid Default Working Directory" in r.stderr

    def test_dir_with_agents_md_passes(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr

    def test_nonexistent_dir_exit_2(self, tmp_path):
        missing = tmp_path / "no_such"
        r = run_script("--workspace", str(missing))
        assert r.returncode == 2


# ---------------------------------------------------------------- Gate flag

class TestGateFlag:
    def test_gate_compliant_outputs_wake_false(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script("--workspace", str(root), "--gate")
        assert r.returncode == 0, r.stderr
        last_line = r.stdout.strip().splitlines()[-1]
        data = json.loads(last_line)
        assert data == {"wakeAgent": False}

    def test_gate_violations_outputs_wake_true_with_count(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script("--workspace", str(root), "--gate")
        assert r.returncode == 1
        last_line = r.stdout.strip().splitlines()[-1]
        data = json.loads(last_line)
        assert data["wakeAgent"] is True
        assert data["violations"] >= 1

    def test_gate_regular_output_still_present(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script("--workspace", str(root), "--gate")
        assert "check 1" in r.stdout
        last_line = r.stdout.strip().splitlines()[-1]
        json.loads(last_line)

    def test_gate_with_json_flag(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        (root / "stray.txt").write_text("x")
        r = run_script("--workspace", str(root), "--json", "--gate")
        assert r.returncode == 1
        lines = r.stdout.strip().splitlines()
        json.loads(lines[0])
        gate = json.loads(lines[-1])
        assert gate["wakeAgent"] is True

    def test_no_gate_flag_no_json_line(self, tmp_path):
        root = make_clean_workspace(tmp_path / "ws")
        r = run_script("--workspace", str(root))
        assert r.returncode == 0, r.stderr
        assert "wakeAgent" not in r.stdout
