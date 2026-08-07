"""Tests for workspace-guard plugin guard.py.

Tests the pre-tool-call hook (allow/block decisions), fail-open behavior,
session start reminder injection, and target path extraction.
Uses mock ctx and real tmp_path filesystem.
"""

import ntpath
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Add plugin directory to path for direct module import
_PLUGIN_DIR = str(Path(__file__).parent.parent / "src" / "workspace-guard")
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import guard
import config
from config import reset_cache


def _configure(working_dir_root, exempt_paths=None):
    """Patch config resolution so get_cached_config returns controlled values."""
    stack = ExitStack()
    stack.enter_context(
        patch("config.resolve_working_dir_root", return_value=working_dir_root)
    )
    stack.enter_context(
        patch(
            "config.load_guard_config",
            return_value={"exempt_paths": exempt_paths or []},
        )
    )
    return stack


@pytest.fixture(autouse=True)
def clean_cache():
    reset_cache()
    guard._reset_fail_open_flag()
    yield
    reset_cache()
    guard._reset_fail_open_flag()


# ---------------------------------------------------------------- Allow cases

class TestGuardHookAllow:
    def test_non_intercepted_tool_returns_none(self, tmp_path):
        with _configure(str(tmp_path)):
            result = guard._guard_hook("read_file", {"path": "anything.txt"})
        assert result is None

    def test_write_file_outside_root_returns_none(self, tmp_path):
        root = str(tmp_path)
        outside = str(tmp_path.parent / "elsewhere" / "file.txt")
        with _configure(root):
            result = guard._guard_hook("write_file", {"path": outside})
        assert result is None

    def test_write_file_in_exempt_paths_returns_none(self, tmp_path):
        root = str(tmp_path)
        exempt = str(tmp_path / "projects")
        with _configure(root, exempt_paths=[exempt]):
            result = guard._guard_hook(
                "write_file",
                {"path": str(tmp_path / "projects" / "notes.txt")},
            )
        assert result is None

    def test_write_file_agents_md_at_root_returns_none(self, tmp_path):
        root = str(tmp_path)
        with _configure(root):
            result = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "AGENTS.md")}
            )
        assert result is None

    def test_write_file_inside_session_dir_returns_none(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "20260801_120000_Task" / "Outputs" / "file.txt")
        with _configure(root):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is None

    def test_patch_inside_session_dir_returns_none(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "20260801_120000_Task" / "Outputs" / "file.txt")
        with _configure(root):
            result = guard._guard_hook(
                "patch", {"mode": "replace", "path": target}
            )
        assert result is None

    def test_working_dir_root_none_fail_open_returns_none(self, tmp_path):
        with _configure(None):
            result = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "notes.txt")}
            )
        assert result is None


# ---------------------------------------------------------------- Block cases

class TestGuardHookBlock:
    def test_write_file_at_root_returns_block(self, tmp_path):
        root = str(tmp_path)
        with _configure(root):
            result = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "notes.txt")}
            )
        assert result is not None
        assert result["action"] == "block"
        assert "BLOCKED" in result["message"]

    def test_write_file_in_non_session_subdir_returns_block(self, tmp_path):
        root = str(tmp_path)
        with _configure(root):
            result = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "misc" / "notes.txt")}
            )
        assert result["action"] == "block"

    def test_patch_replace_mode_root_file_returns_block(self, tmp_path):
        root = str(tmp_path)
        with _configure(root):
            result = guard._guard_hook(
                "patch",
                {"mode": "replace", "path": str(tmp_path / "notes.txt")},
            )
        assert result["action"] == "block"

    def test_patch_v4a_mode_root_file_returns_block(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "notes.txt").replace("\\", "/")
        patch_content = "*** Update File: %s\n@@ -1,2 +1,2 @@\n" % target
        with _configure(root):
            result = guard._guard_hook(
                "patch", {"mode": "patch", "patch": patch_content}
            )
        assert result["action"] == "block"

    def test_block_message_contains_fix_instructions(self, tmp_path):
        root = str(tmp_path)
        with _configure(root):
            result = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "notes.txt")}
            )
        assert "BLOCKED" in result["message"]
        assert "create_session_dir.py" in result["message"]
        assert "guard-config.yaml" in result["message"]


# ---------------------------------------------------------------- Fail open

class TestFailOpen:
    def test_exception_in_guard_logic_returns_none(self):
        with patch.object(
            guard, "_guard_logic", side_effect=RuntimeError("boom")
        ):
            result = guard._guard_hook("write_file", {"path": "x.txt"})
        assert result is None

    def test_exception_in_get_cached_config_returns_none(self):
        with patch.object(
            guard, "get_cached_config", side_effect=RuntimeError("boom")
        ):
            result = guard._guard_hook("write_file", {"path": "x.txt"})
        assert result is None


# ---------------------------------------------------------------- Session start

class TestSessionStartHook:
    def test_injects_reminder_when_root_resolved(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(str(tmp_path)):
                guard._session_start_hook()
        ctx.inject_message.assert_called_once_with(guard.REMINDER_MESSAGE)

    def test_no_inject_when_root_none(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                guard._session_start_hook()
        ctx.inject_message.assert_not_called()

    def test_exception_in_hook_does_not_escape(self):
        with patch.object(guard, "_registered_ctx", MagicMock()):
            with patch.object(
                guard, "get_cached_config", side_effect=RuntimeError("boom")
            ):
                guard._session_start_hook()

    def test_inject_message_false_skips_without_error(self, tmp_path):
        ctx = MagicMock()
        ctx.inject_message.return_value = False
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(str(tmp_path)):
                guard._session_start_hook()

    def test_session_start_clears_runtime_allowlist(self, tmp_path):
        config.runtime_allowlist_add("E:/ws/stale")
        assert config.is_runtime_allowlisted("E:/ws/stale/x.txt")
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(str(tmp_path)):
                guard._session_start_hook()
        assert not config.is_runtime_allowlisted("E:/ws/stale/x.txt")
        ctx.inject_message.assert_called_once_with(guard.REMINDER_MESSAGE)


# ---------------------------------------------------------------- Register + memo sync + runtime tool (task 9.6)

class TestRegisterSync:
    """register() full memo sync + runtime allowlist tool (task 9.6).

    register() performs a FULL memo sync (SCR-001 2.4) after the config
    load -- fail-open, a sync error must never fail registration -- and
    registers the workspace_guard_allow_path tool (SCR-002 2.5) when the
    ctx supports register_tool (a ctx without it must not break
    registration). _session_start_hook runs the INCREMENTAL memo check
    (SCR-001 2.4): re-sync only when the live profile set differs from the
    memo's profile keys.
    """

    def test_register_calls_sync_memo_once(self):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", None):
            with patch.object(guard, "get_cached_config", return_value=(None, [])):
                with patch.object(guard, "sync_memo", return_value={}) as sync:
                    guard.register(ctx)
        sync.assert_called_once_with()

    def test_register_registers_allow_path_tool(self):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", None):
            with patch.object(guard, "get_cached_config", return_value=(None, [])):
                with patch.object(guard, "sync_memo", return_value={}):
                    guard.register(ctx)
        ctx.register_tool.assert_called_once_with(
            "workspace_guard_allow_path",
            toolset="workspace-guard",
            schema=guard.ALLOW_PATH_TOOL_SCHEMA,
            handler=guard.workspace_guard_allow_path,
        )

    def test_register_without_register_tool_still_registers(self):
        ctx = MagicMock(spec=["register_hook"])
        with patch.object(guard, "_registered_ctx", None):
            with patch.object(guard, "get_cached_config", return_value=(None, [])):
                with patch.object(guard, "sync_memo", return_value={}):
                    guard.register(ctx)
        ctx.register_hook.assert_called()

    def test_sync_memo_raising_during_register_is_fail_open(self):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", None):
            with patch.object(guard, "get_cached_config", return_value=(None, [])):
                with patch.object(
                    guard, "sync_memo", side_effect=RuntimeError("boom")
                ):
                    guard.register(ctx)
        # Registration still completes: both hooks are registered.
        ctx.register_hook.assert_called()

    # -- Incremental memo check at on_session_start (SCR-001 2.4) --

    @staticmethod
    def _memo(profile_names):
        return {
            "synced_at": "2026-08-06T00:00:00",
            "profiles": {
                name: {"workspace": None, "status": "invalid", "changed_at": "t"}
                for name in profile_names
            },
        }

    def _session_start(self, live, memo, tmp_path):
        """Run _session_start_hook with _list_profiles/load_memo patched."""
        stack = ExitStack()
        stack.enter_context(patch.object(guard, "_registered_ctx", MagicMock()))
        stack.enter_context(patch.object(guard, "_list_profiles", return_value=live))
        stack.enter_context(patch.object(guard, "load_memo", return_value=memo))
        stack.enter_context(_configure(str(tmp_path)))
        return stack

    def test_session_start_skips_sync_when_profile_set_unchanged(self, tmp_path):
        memo = self._memo(["default"])
        live = [("default", "home")]
        with self._session_start(live, memo, tmp_path):
            with patch.object(guard, "sync_memo", return_value={}) as sync:
                guard._session_start_hook()
        sync.assert_not_called()

    def test_session_start_syncs_when_new_profile_appears(self, tmp_path):
        memo = self._memo(["default"])
        live = [("default", "home"), ("job-hunt", "home2")]
        with self._session_start(live, memo, tmp_path):
            with patch.object(guard, "sync_memo", return_value={}) as sync:
                guard._session_start_hook()
        sync.assert_called_once_with()

    def test_session_start_syncs_when_profile_deleted(self, tmp_path):
        memo = self._memo(["default", "job-hunt"])
        live = [("default", "home")]
        with self._session_start(live, memo, tmp_path):
            with patch.object(guard, "sync_memo", return_value={}) as sync:
                guard._session_start_hook()
        sync.assert_called_once_with()


# ---------------------------------------------------------------- Fail-open warning (SCR-004, task 11.3)

class TestFailOpenWarning:
    """One-time fail-open warning injection (SCR-004, task 11.3).

    When the guard is disabled (working_dir_root None), the first eligible
    tool call injects a visible warning via ctx.inject_message and the call
    still proceeds (fail-open, returns None). The module-level flag makes
    the warning fire at most once per session; _session_start_hook resets
    the flag so each new session re-warns while the guard stays disabled.
    No exception may escape when ctx is None / inject_message is missing or
    raises. The autouse clean_cache fixture resets the flag between tests.
    """

    def test_warning_injected_once_when_guard_disabled(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                result = guard._guard_hook(
                    "write_file", {"path": str(tmp_path / "notes.txt")}
                )
        assert result is None
        ctx.inject_message.assert_called_once()
        message = ctx.inject_message.call_args[0][0]
        assert "DISABLED" in message

    def test_warning_not_repeated_within_session(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                guard._guard_hook("write_file", {"path": "a.txt"})
                guard._guard_hook("write_file", {"path": "b.txt"})
                guard._guard_hook("terminal", {"command": "echo x > c.txt"})
        assert ctx.inject_message.call_count == 1

    def test_flag_reset_at_session_start_rewarns(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                guard._guard_hook("write_file", {"path": "a.txt"})
                guard._session_start_hook()
                guard._guard_hook("write_file", {"path": "b.txt"})
        assert ctx.inject_message.call_count == 2

    def test_flag_cleared_by_guard_reset_hook(self, tmp_path):
        # guard._reset_fail_open_flag() is the guard-level reset the
        # autouse clean_cache fixture uses between tests.
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                guard._guard_hook("write_file", {"path": "a.txt"})
                guard._reset_fail_open_flag()
                guard._guard_hook("write_file", {"path": "b.txt"})
        assert ctx.inject_message.call_count == 2

    def test_fail_open_preserved_returns_none(self, tmp_path):
        ctx = MagicMock()
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                for _ in range(3):
                    result = guard._guard_hook(
                        "write_file", {"path": str(tmp_path / "notes.txt")}
                    )
                    assert result is None

    def test_no_crash_when_inject_message_unavailable(self, tmp_path):
        # ctx without inject_message attribute: skipped, no exception.
        ctx = MagicMock(spec=["profile_name"])
        with patch.object(guard, "_registered_ctx", ctx):
            with _configure(None):
                result = guard._guard_hook(
                    "write_file", {"path": str(tmp_path / "notes.txt")}
                )
        assert result is None
        # inject_message raising: swallowed, hook still returns None.
        ctx2 = MagicMock()
        ctx2.inject_message.side_effect = RuntimeError("boom")
        guard._reset_fail_open_flag()
        with patch.object(guard, "_registered_ctx", ctx2):
            with _configure(None):
                result = guard._guard_hook(
                    "write_file", {"path": str(tmp_path / "notes.txt")}
                )
        assert result is None


# ---------------------------------------------------------------- Path extraction

class TestExtractTargetPaths:
    def test_write_file_with_path(self):
        assert guard._extract_target_paths(
            "write_file", {"path": "notes.txt"}
        ) == ["notes.txt"]

    def test_write_file_without_path(self):
        assert guard._extract_target_paths("write_file", {}) == []

    def test_patch_with_path(self):
        assert guard._extract_target_paths(
            "patch", {"path": "notes.txt"}
        ) == ["notes.txt"]

    def test_patch_v4a_content_extracts_paths(self):
        content = (
            "*** Update File: C:/ws/a.txt\n@@ -1,2 +1,2 @@\n"
            "*** Update File: C:/ws/b.txt\n@@ -1,2 +1,2 @@\n"
        )
        assert guard._extract_target_paths(
            "patch", {"patch": content}
        ) == ["C:/ws/a.txt", "C:/ws/b.txt"]

    def test_patch_with_neither(self):
        assert guard._extract_target_paths("patch", {}) == []

    def test_non_dict_args(self):
        assert guard._extract_target_paths("write_file", ["notes.txt"]) == []
        assert guard._extract_target_paths("write_file", None) == []
        assert guard._extract_target_paths("patch", "not a dict") == []


# ---------------------------------------------------------------- Normalization (SCR-006)

class TestNormalization:
    """Pure path-normalization helpers (SCR-006, task 9.9).

    Helpers are called directly so both platform branches are testable on
    any host OS. Expected values are built via os.path.normpath so the
    assertions are host-OS independent.
    """

    @pytest.mark.parametrize(
        "msys_path",
        [
            "/c/Users/x.txt",
            "/C/Users/x.txt",
            "//c/Users/x.txt",
            "//C/Users/x.txt",
            "/cygdrive/c/Users/x.txt",
            "/cygdrive/C/Users/x.txt",
        ],
    )
    def test_msys_forms_map_to_uppercased_drive(self, msys_path):
        result = guard._normalize_windows(msys_path, r"E:\HermesWorkspace\default")
        assert result == os.path.normpath("C:/Users/x.txt")

    @pytest.mark.parametrize(
        "msys_path", ["/c", "//c", "/C", "//C", "/cygdrive/c"]
    )
    def test_bare_msys_drive_maps_to_drive_root(self, msys_path):
        result = guard._normalize_windows(msys_path, r"E:\HermesWorkspace\default")
        assert result == os.path.normpath("C:/")
        assert ntpath.splitdrive(result)[0] == "C:"

    def test_rooted_no_drive_inherits_working_root_drive(self):
        result = guard._normalize_windows(
            "/HermesWorkspace/default/evil.txt", r"E:\HermesWorkspace\default"
        )
        assert result == os.path.normpath("E:/HermesWorkspace/default/evil.txt")

    def test_msys_internal_path_inherits_working_root_drive(self):
        result = guard._normalize_windows("/usr/bin", r"E:\HermesWorkspace\default")
        assert result == os.path.normpath("E:/usr/bin")

    def test_unc_path_not_matched_by_msys_regexes(self):
        result = guard._normalize_windows(
            "//server/share/file.txt", r"E:\HermesWorkspace\default"
        )
        assert result == os.path.normpath("//server/share/file.txt")

    def test_inheritance_skipped_when_working_root_has_no_drive(self):
        result = guard._normalize_windows("/usr/bin", "HermesWorkspace")
        assert result == os.path.normpath("/usr/bin")

    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "a/../b", "notes.txt", "/usr/bin"]
    )
    def test_posix_helper_is_normpath_identity(self, path):
        assert guard._normalize_posix(path) == os.path.normpath(path)

    def test_in_workspace_msys_path_is_drive_qualified(self):
        result = guard._normalize_windows(
            "/e/HermesWorkspace/default/evil.txt", r"E:\HermesWorkspace\default"
        )
        assert ntpath.splitdrive(result)[0] != ""
        assert result == os.path.normpath("E:/HermesWorkspace/default/evil.txt")


# ---------------------------------------------------------------- MSYS integration (SCR-006, task 9.9)

class TestMsysIntegration:
    """Full-chain MSYS path normalization integration (SCR-006, task 9.9).

    Drives the FULL hook (guard._guard_hook "write_file") with a
    configured working_dir_root on the E: drive, proving normalization
    plugs the MSYS / rooted-no-drive bypass end-to-end (spec 7.3 SCR-006
    rows). Before normalization these targets were misread as external
    (relpath ".."-prefixed) and allowed.

    A fixed E:/HermesWorkspace/default root is used because the MSYS
    target forms hardcode the E: drive letter; classification is pure
    string logic (no filesystem access), so the workspace need not exist.
    The memo is seeded empty so external targets never hit a
    cross-profile branch. The autouse clean_cache fixture resets the
    config cache / runtime allowlist before and after every test.
    """

    WORKSPACE = "E:/HermesWorkspace/default"

    def _setup(self, root=WORKSPACE, exempt_paths=None):
        """Open all patch contexts for a full-hook MSYS integration test."""
        stack = ExitStack()
        stack.enter_context(patch.object(
            guard, "_registered_ctx", MagicMock(profile_name="default")
        ))
        stack.enter_context(patch.object(
            guard, "load_memo", return_value={"synced_at": None, "profiles": {}}
        ))
        stack.enter_context(_configure(root, exempt_paths=exempt_paths))
        return stack

    def _hook(self, target, root=WORKSPACE):
        with self._setup(root):
            return guard._guard_hook(
                "write_file", {"path": target}, task_id="task-1"
            )

    def test_in_workspace_msys_path_is_blocked(self):
        # /e/HermesWorkspace/default/evil.txt maps to E:\HermesWorkspace\
        # default\evil.txt -- INSIDE working_dir_root. Headline SCR-006
        # criterion: before normalization the drive-less rooted target was
        # misread as external and allowed (the bypass this closes).
        result = self._hook("/e/HermesWorkspace/default/evil.txt")
        assert result is not None
        assert result["action"] == "block"
        assert "BLOCKED" in result["message"]

    def test_rooted_no_drive_in_workspace_is_blocked(self):
        # \HermesWorkspace\default\evil.txt inherits E: from
        # working_dir_root -> E:\HermesWorkspace\default\evil.txt -> BLOCK.
        result = self._hook(r"\HermesWorkspace\default\evil.txt")
        assert result is not None
        assert result["action"] == "block"

    def test_msys_path_outside_workspace_allowed(self):
        # /c/other/place.txt maps to C:\other\place.txt -- a different
        # drive from the E: workspace -> classified external (no memo
        # hit) -> allow.
        result = self._hook("/c/other/place.txt")
        assert result is None

    def test_msys_internal_path_external_allowed(self):
        # /usr/bin inherits E: -> E:\usr\bin, outside the workspace ->
        # external -> allow.
        result = self._hook("/usr/bin")
        assert result is None

    @pytest.mark.parametrize("name", ["AGENTS.md", "agents.md", "AgEnTs.Md"])
    def test_msys_agents_md_any_case_allowed(self, name):
        # /e/HermesWorkspace/default/<name> maps to the workspace-root
        # AGENTS.md; any case is allowed on Windows (SCR-006 task 9.10).
        result = self._hook("/e/HermesWorkspace/default/%s" % name)
        assert result is None

    def test_unmappable_path_fails_open(self):
        # working_dir_root WITHOUT a drive skips drive inheritance, so a
        # drive-less rooted target stays unclassifiable -> fail-open
        # (allow) with a warning log, no exception.
        result = self._hook(r"\evil\file.txt", root="HermesWorkspace")
        assert result is None


# ---------------------------------------------------------------- Relative-path resolution (SCR-001, task 9.5)

class TestRelativeResolution:
    """_resolve_target relative-path resolution (SCR-001, task 9.5).

    Relative targets resolve against the actual session CWD
    (get_session_cwd(task_id)); when unrecorded (None) or unimportable,
    they fall back to working_dir_root. Never uses os.getcwd().
    """

    def test_relative_target_resolves_against_session_cwd(self, tmp_path):
        session_dir = tmp_path / "session-cwd"
        working_root = tmp_path / "working-root"
        mock_cwd = Mock(return_value=str(session_dir))
        with patch.object(guard, "get_session_cwd", mock_cwd):
            result = guard._resolve_target(
                "output.txt", "task-1", str(working_root)
            )
        mock_cwd.assert_called_once_with("task-1")
        assert result == str(session_dir / "output.txt")

    def test_relative_target_with_none_session_cwd_uses_working_dir_root(self, tmp_path):
        working_root = tmp_path / "working-root"
        with patch.object(guard, "get_session_cwd", return_value=None):
            with patch.object(
                os,
                "getcwd",
                side_effect=AssertionError("os.getcwd() must not be used"),
            ):
                result = guard._resolve_target(
                    "output.txt", "task-1", str(working_root)
                )
        assert result == str(working_root / "output.txt")

    def test_absolute_target_returned_unchanged(self, tmp_path):
        working_root = tmp_path / "working-root"
        absolute = str(tmp_path / "abs" / "file.txt")
        with patch.object(
            guard, "get_session_cwd", return_value=str(tmp_path / "session-cwd")
        ):
            result = guard._resolve_target(absolute, "task-1", str(working_root))
        assert result == absolute

    def test_unimportable_get_session_cwd_falls_back_to_working_dir_root(self, tmp_path):
        working_root = tmp_path / "working-root"
        with patch.object(guard, "get_session_cwd", None):
            result = guard._resolve_target(
                "output.txt", "task-1", str(working_root)
            )
        assert result == str(working_root / "output.txt")


# ---------------------------------------------------------------- classify_target (spec 5.3 unified chain, task 9.1)

class TestClassifyTarget:
    """classify_target direct unit tests (spec 5.3 unified chain).

    classify_target receives an already-normalized absolute target. The
    autouse clean_cache fixture resets the config cache and runtime
    allowlist before/after every test.
    """

    def test_tier0_exempt_path_returns_none(self, tmp_path):
        root = str(tmp_path)
        exempt = str(tmp_path / "projects")
        target = str(tmp_path / "projects" / "notes.txt")
        assert guard.classify_target(target, root, [exempt]) is None

    def test_tier0_runtime_allowlisted_returns_none(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "notes.txt")
        config.runtime_allowlist_add(target)
        assert guard.classify_target(target, root, []) is None

    def test_in_workspace_root_file_returns_block(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "notes.txt")
        result = guard.classify_target(target, root, [])
        assert result is not None
        assert result["action"] == "block"
        assert "BLOCKED" in result["message"]

    def test_session_dir_returns_none(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "20260801_120000_Task" / "Outputs" / "file.txt")
        assert guard.classify_target(target, root, []) is None

    def test_agents_md_at_root_returns_none(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "AGENTS.md")
        assert guard.classify_target(target, root, []) is None

    def test_external_target_returns_none(self, tmp_path):
        root = str(tmp_path)
        outside = str(tmp_path.parent / "elsewhere" / "file.txt")
        assert guard.classify_target(outside, root, []) is None


# ---------------------------------------------------------------- Cross-profile detection (SCR-001, task 9.4 part 1)

class TestCrossProfileHit:
    """_cross_profile_hit pure unit tests (SCR-001, task 9.4 part 1).

    The helper is exercised directly with in-memory memo dicts; no
    filesystem is involved. Memo shape matches config.load_memo output:
    {"synced_at": iso, "profiles": {name: {"workspace", "status",
    "changed_at"}}}.
    """

    @staticmethod
    def _memo(profiles):
        return {"synced_at": "2026-08-06T00:00:00", "profiles": profiles}

    def test_target_under_other_valid_profile_returns_profile(self):
        memo = self._memo({
            "default": {"workspace": "E:/HermesWorkspace/default",
                        "status": "valid", "changed_at": "t"},
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
        ) == "job-hunt"

    def test_target_under_current_profile_is_excluded(self):
        memo = self._memo({
            "default": {"workspace": "E:/HermesWorkspace/default",
                        "status": "valid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/default/output.txt", memo, "default"
        ) is None

    def test_invalid_status_entry_ignored(self):
        memo = self._memo({
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "invalid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
        ) is None

    def test_null_or_empty_workspace_entry_ignored(self):
        memo = self._memo({
            "job-hunt": {"workspace": None, "status": "valid",
                         "changed_at": "t"},
            "learn": {"workspace": "", "status": "valid",
                      "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
        ) is None
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/learn/output.txt", memo, "default"
        ) is None

    def test_target_not_under_any_memo_workspace_returns_none(self):
        memo = self._memo({
            "default": {"workspace": "E:/HermesWorkspace/default",
                        "status": "valid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/SomewhereElse/file.txt", memo, "default"
        ) is None

    def test_exact_equal_target_equals_workspace_matches(self):
        memo = self._memo({
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt", memo, "default"
        ) == "job-hunt"

    def test_sibling_prefix_does_not_match(self):
        # E:/HermesWorkspace/job-hunt-extra is NOT under job-hunt
        # (prefix requires the "/" delimiter).
        memo = self._memo({
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt-extra/file.txt", memo, "default"
        ) is None

    def test_backslash_paths_normalized_before_matching(self):
        memo = self._memo({
            "job-hunt": {"workspace": "E:\\HermesWorkspace\\job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        # Backslash target against forward-slash memo workspace.
        assert guard._cross_profile_hit(
            "E:\\HermesWorkspace\\job-hunt\\output.txt", memo, "default"
        ) == "job-hunt"
        # Backslash workspace against forward-slash target.
        assert guard._cross_profile_hit(
            "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
        ) == "job-hunt"

    def test_malformed_or_empty_memo_returns_none(self):
        for memo in (
            None,
            [],
            "not a dict",
            {},
            {"synced_at": "t"},
            {"profiles": []},
            {"profiles": {"job-hunt": "not a dict"}},
        ):
            assert guard._cross_profile_hit(
                "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
            ) is None

    def test_deterministic_choice_when_multiple_profiles_match(self):
        # Both "job-hunt" and "learn" contain the target; the helper must
        # always return the same profile (alphabetically first -> "job-hunt").
        memo = self._memo({
            "learn": {"workspace": "E:/Shared", "status": "valid",
                      "changed_at": "t"},
            "job-hunt": {"workspace": "E:/Shared", "status": "valid",
                         "changed_at": "t"},
            "default": {"workspace": "E:/Shared", "status": "valid",
                        "changed_at": "t"},
        })
        results = {
            guard._cross_profile_hit("E:/Shared/file.txt", memo, "default")
            for _ in range(10)
        }
        assert results == {"job-hunt"}


# ---------------------------------------------------------------- Cross-profile approve branch (SCR-001, task 9.4 part 2)

class TestCrossProfile:
    """Guard-level cross-profile approve branch (SCR-001, task 9.4 part 2).

    Exercises the full hook (_guard_hook write_file) with guard.load_memo
    patched to a fixture memo and the registered ctx carrying
    profile_name="default". The autouse clean_cache fixture resets the
    config cache (and runtime allowlist) before/after each test.
    """

    @staticmethod
    def _memo(workspaces):
        """Build a valid memo dict from {profile: workspace}."""
        return {
            "synced_at": "2026-08-06T00:00:00",
            "profiles": {
                name: {"workspace": ws, "status": "valid", "changed_at": "t"}
                for name, ws in workspaces.items()
            },
        }

    def _setup(self, root, memo, exempt_paths=None, ctx=None):
        """Open all patch contexts for a guard-level cross-profile test."""
        stack = ExitStack()
        stack.enter_context(patch.object(
            guard,
            "_registered_ctx",
            ctx if ctx is not None else MagicMock(profile_name="default"),
        ))
        stack.enter_context(patch.object(guard, "load_memo", return_value=memo))
        stack.enter_context(_configure(root, exempt_paths=exempt_paths))
        return stack

    @staticmethod
    def _fwd(path):
        return path.replace("\\", "/")

    def test_cross_profile_write_returns_approve(self, tmp_path):
        root = str(tmp_path / "default")
        other = str(tmp_path / "job-hunt")
        target = str(tmp_path / "job-hunt" / "output.txt")
        memo = self._memo({"default": self._fwd(root),
                           "job-hunt": self._fwd(other)})
        with self._setup(root, memo):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is not None
        assert result["action"] == "approve"
        assert result["rule_key"] == "cross-profile-write:job-hunt"
        msg = result["message"]
        assert "Target: %s" % self._fwd(target) in msg
        assert "Profile: job-hunt (workspace: %s)" % self._fwd(other) in msg
        assert "Active profile: default" in msg
        assert "switches profiles without refreshing the workspace" in msg
        assert (
            "choosing [a]lways approves writes to the 'job-hunt' profile's "
            "workspace only" in msg
        )

    def test_two_target_profiles_get_distinct_rule_keys(self, tmp_path):
        root = str(tmp_path / "default")
        job_ws = str(tmp_path / "job-hunt")
        learn_ws = str(tmp_path / "learn")
        memo = self._memo({
            "default": self._fwd(root),
            "job-hunt": self._fwd(job_ws),
            "learn": self._fwd(learn_ws),
        })
        with self._setup(root, memo):
            r1 = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "job-hunt" / "a.txt")}
            )
            r2 = guard._guard_hook(
                "write_file", {"path": str(tmp_path / "learn" / "b.txt")}
            )
        assert r1["action"] == "approve"
        assert r2["action"] == "approve"
        assert r1["rule_key"] == "cross-profile-write:job-hunt"
        assert r2["rule_key"] == "cross-profile-write:learn"
        assert r1["rule_key"] != r2["rule_key"]

    def test_current_profile_workspace_write_still_blocks(self, tmp_path):
        root = str(tmp_path / "default")
        target = str(tmp_path / "default" / "notes.txt")
        memo = self._memo({
            "default": self._fwd(root),
            "job-hunt": self._fwd(str(tmp_path / "job-hunt")),
        })
        with self._setup(root, memo):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is not None
        assert result["action"] == "block"
        assert "cross-profile-write" not in result.get("rule_key", "")

    def test_external_path_no_memo_hit_returns_none(self, tmp_path):
        root = str(tmp_path / "default")
        outside = str(tmp_path.parent / "elsewhere" / "file.txt")
        memo = self._memo({
            "default": self._fwd(root),
            "job-hunt": self._fwd(str(tmp_path / "job-hunt")),
        })
        with self._setup(root, memo):
            result = guard._guard_hook("write_file", {"path": outside})
        assert result is None

    def test_invalid_profile_entry_returns_none(self, tmp_path):
        root = str(tmp_path / "default")
        target = str(tmp_path / "job-hunt" / "output.txt")
        memo = {
            "synced_at": "2026-08-06T00:00:00",
            "profiles": {
                "default": {"workspace": self._fwd(root), "status": "valid",
                            "changed_at": "t"},
                "job-hunt": {"workspace": None, "status": "invalid",
                             "changed_at": "t"},
            },
        }
        with self._setup(root, memo):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is None

    def test_tier0_exempt_wins_over_cross_profile(self, tmp_path):
        root = str(tmp_path / "default")
        exempt = str(tmp_path / "job-hunt")
        target = str(tmp_path / "job-hunt" / "notes.txt")
        memo = self._memo({
            "default": self._fwd(root),
            "job-hunt": self._fwd(exempt),
        })
        with self._setup(root, memo, exempt_paths=[exempt]):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is None

    def test_empty_memo_returns_none(self, tmp_path):
        root = str(tmp_path / "default")
        target = str(tmp_path / "job-hunt" / "output.txt")
        memo = {"synced_at": None, "profiles": {}}
        with self._setup(root, memo):
            result = guard._guard_hook("write_file", {"path": target})
        assert result is None

    def test_load_memo_exception_fails_open(self, tmp_path):
        root = str(tmp_path / "default")
        target = str(tmp_path / "job-hunt" / "output.txt")
        stack = ExitStack()
        stack.enter_context(patch.object(
            guard, "_registered_ctx", MagicMock(profile_name="default")
        ))
        stack.enter_context(patch.object(
            guard, "load_memo", side_effect=RuntimeError("boom")
        ))
        stack.enter_context(_configure(root))
        with stack:
            result = guard._guard_hook("write_file", {"path": target})
        assert result is None


# ---------------------------------------------------------------- Case-insensitive comparisons (SCR-006, task 9.10)

class TestCasefold:
    """Windows case-insensitive comparisons (SCR-006, task 9.10).

    The Windows comparison logic lives in pure *_ci helpers that are
    directly callable on any host OS (they always casefold). The
    os.name-dependent dispatchers (_is_exempt_ci, classify_target,
    _cross_profile_hit) are exercised with os.name patched to "nt" /
    "posix" so BOTH platform branches run on any host. Regression cases
    use same-case inputs and must behave exactly as before.
    """

    @staticmethod
    def _memo(profiles):
        return {"synced_at": "2026-08-06T00:00:00", "profiles": profiles}

    # -- Exempt prefix match (Windows path) --

    def test_exempt_prefix_ci_matches_mixed_case(self):
        # Pure Windows comparison path: e:/hermes... target matches an
        # E:/Hermes... exempt entry (callable directly on any host OS).
        assert guard._exempt_prefix_ci(
            "e:/hermes/job-hunt/notes.txt", "E:/Hermes/job-hunt"
        ) is True
        # Backslash target against forward-slash exempt entry.
        assert guard._exempt_prefix_ci(
            r"e:\hermes\job-hunt\notes.txt", "E:/Hermes/job-hunt"
        ) is True

    def test_is_exempt_ci_case_insensitive_when_windows(self):
        with patch.object(guard.os, "name", "nt"):
            assert guard._is_exempt_ci(
                "e:/hermes/job-hunt/notes.txt", ["E:/Hermes/job-hunt"]
            ) is True

    def test_classify_target_exempt_case_insensitive_when_windows(self):
        # Tier 0 exempt wins for any target (even outside working_dir_root).
        with patch.object(guard.os, "name", "nt"):
            assert guard.classify_target(
                "e:/hermes/projects/notes.txt",
                "E:/HermesWorkspace/default",
                ["E:/Hermes/projects"],
            ) is None

    def test_exempt_exact_comparison_when_posix(self):
        # POSIX branch: config.is_exempt exact semantics preserved.
        with patch.object(guard.os, "name", "posix"):
            assert guard._is_exempt_ci(
                "e:/hermes/job-hunt/notes.txt", ["E:/Hermes/job-hunt"]
            ) is False
            assert guard._is_exempt_ci(
                "E:/Hermes/job-hunt/notes.txt", ["E:/Hermes/job-hunt"]
            ) is True

    # -- AGENTS.md equality --

    def test_agents_md_any_case_equal_windows(self):
        # Pure Windows comparison path: agents.md == AGENTS.md.
        assert guard._paths_equal_ci(
            "E:/HermesWorkspace/default/agents.md",
            "E:/HermesWorkspace/default/AGENTS.md",
        ) is True

    def test_classify_target_agents_md_any_case_when_windows(self):
        with patch.object(guard.os, "name", "nt"):
            assert guard.classify_target(
                "E:/HermesWorkspace/default/agents.md",
                "E:/HermesWorkspace/default",
                [],
            ) is None
            assert guard.classify_target(
                "E:/HermesWorkspace/default/Agents.Md",
                "E:/HermesWorkspace/default",
                [],
            ) is None

    def test_agents_md_exact_comparison_when_posix(self):
        with patch.object(guard.os, "name", "posix"):
            # Lowercase agents.md is NOT the root AGENTS.md on POSIX.
            result = guard.classify_target(
                "E:/HermesWorkspace/default/agents.md",
                "E:/HermesWorkspace/default",
                [],
            )
            assert result is not None
            assert result["action"] == "block"
            # Exact-case AGENTS.md stays allowed.
            assert guard.classify_target(
                "E:/HermesWorkspace/default/AGENTS.md",
                "E:/HermesWorkspace/default",
                [],
            ) is None

    # -- Memo cross-profile prefix --

    def test_prefix_ci_mixed_case(self):
        # Pure Windows comparison path: e:/hermesworkspace/job-hunt target
        # matches E:/HermesWorkspace/job-hunt memo workspace.
        assert guard._prefix_ci(
            "e:/hermesworkspace/job-hunt/output.txt",
            "E:/HermesWorkspace/job-hunt",
        ) is True

    def test_cross_profile_hit_case_insensitive_when_windows(self):
        memo = self._memo({
            "default": {"workspace": "E:/HermesWorkspace/default",
                        "status": "valid", "changed_at": "t"},
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        with patch.object(guard.os, "name", "nt"):
            assert guard._cross_profile_hit(
                "e:/hermesworkspace/job-hunt/output.txt", memo, "default"
            ) == "job-hunt"

    def test_cross_profile_hit_exact_comparison_when_posix(self):
        memo = self._memo({
            "default": {"workspace": "E:/HermesWorkspace/default",
                        "status": "valid", "changed_at": "t"},
            "job-hunt": {"workspace": "E:/HermesWorkspace/job-hunt",
                         "status": "valid", "changed_at": "t"},
        })
        with patch.object(guard.os, "name", "posix"):
            # Mixed case must NOT hit on POSIX (exact comparison).
            assert guard._cross_profile_hit(
                "e:/hermesworkspace/job-hunt/output.txt", memo, "default"
            ) is None
            # Exact case still hits.
            assert guard._cross_profile_hit(
                "E:/HermesWorkspace/job-hunt/output.txt", memo, "default"
            ) == "job-hunt"

    def test_cross_profile_approve_mixed_case_when_windows(self, tmp_path):
        # Full guard-level approve with mixed-case memo workspace and target.
        root = str(tmp_path / "default")
        other = str(tmp_path / "job-hunt")
        target = str(tmp_path / "JOB-HUNT" / "output.txt")
        memo = {
            "synced_at": "2026-08-06T00:00:00",
            "profiles": {
                "default": {"workspace": root.replace("\\", "/"),
                            "status": "valid", "changed_at": "t"},
                "job-hunt": {"workspace": other.replace("\\", "/"),
                             "status": "valid", "changed_at": "t"},
            },
        }
        stack = ExitStack()
        stack.enter_context(patch.object(
            guard, "_registered_ctx", MagicMock(profile_name="default")
        ))
        stack.enter_context(patch.object(guard, "load_memo", return_value=memo))
        stack.enter_context(patch.object(guard.os, "name", "nt"))
        stack.enter_context(_configure(root))
        with stack:
            result = guard._guard_hook("write_file", {"path": target})
        assert result is not None
        assert result["action"] == "approve"
        assert result["rule_key"] == "cross-profile-write:job-hunt"

    # -- Regression: same-case behavior unchanged --

    def test_same_case_regression_unchanged(self):
        # Exempt same-case still matches (Windows path and dispatcher).
        assert guard._exempt_prefix_ci(
            "E:/Hermes/job-hunt/notes.txt", "E:/Hermes/job-hunt"
        ) is True
        assert guard._is_exempt_ci(
            "E:/Hermes/job-hunt/notes.txt", ["E:/Hermes/job-hunt"]
        ) is True
        # AGENTS.md exact-case equality still holds.
        assert guard._paths_equal_ci(
            "E:/HermesWorkspace/default/AGENTS.md",
            "E:/HermesWorkspace/default/AGENTS.md",
        ) is True
        # Cross-profile same-case still hits (Windows path).
        assert guard._prefix_ci(
            "E:/HermesWorkspace/job-hunt/output.txt",
            "E:/HermesWorkspace/job-hunt",
        ) is True


# ---------------------------------------------------------------- Terminal tokenizer (SCR-002, tiered terminal interception)

class TestTerminalTokenizer:
    """_extract_terminal_write_targets / _tokenize_command unit tests (SCR-002).

    Direct unit tests for the two pure helpers to be added to guard.py:
      - guard._tokenize_command(command) -> list of tokens
      - guard._extract_terminal_write_targets(command) -> (targets, uncertain)

    Contract per docs/spec-change-002-terminal-write-interception.md 2.3:
      - Tier 1 structures yield explicit target paths, uncertain=False.
      - Tier 2 (write intent, target uncertain) yields uncertain=True.
      - Read-only / unparseable commands yield ([], False); never raises.

    The helpers do not exist in guard.py yet -- every test here is EXPECTED
    to fail with AttributeError until the implementation step lands.
    """

    # -- Tier 1: high-confidence targets, uncertain=False --

    @pytest.mark.parametrize(
        "command,expected",
        [
            pytest.param(
                "echo x > blank.txt", (["blank.txt"], False),
                id="case1_redirect_gt",
            ),
            pytest.param(
                "echo x >> log.txt", (["log.txt"], False),
                id="case2_redirect_append",
            ),
            pytest.param(
                "cmd 2> err.txt", (["err.txt"], False),
                id="case3_redirect_stderr",
            ),
            pytest.param(
                "cmd &> all.txt", (["all.txt"], False),
                id="case4_redirect_all",
            ),
            pytest.param(
                "touch a.txt b.txt", (["a.txt", "b.txt"], False),
                id="case5_touch_multiple",
            ),
            pytest.param(
                "cp x.txt outputs/", (["outputs/"], False),
                id="case6_cp_dest",
            ),
            pytest.param(
                "mv a.txt b.txt", (["b.txt"], False),
                id="case7_mv_dest",
            ),
            pytest.param(
                "cat data | tee log.txt", (["log.txt"], False),
                id="case8_tee_pipe",
            ),
            pytest.param(
                "tee -a log.txt", (["log.txt"], False),
                id="case9_tee_append",
            ),
            pytest.param(
                "curl -o a.zip http://example.com/a.zip", (["a.zip"], False),
                id="case10_curl_dash_o",
            ),
            pytest.param(
                "wget -O b.zip http://example.com/b.zip", (["b.zip"], False),
                id="case11_wget_dash_O",
            ),
            pytest.param(
                "dd if=x of=y", (["y"], False),
                id="case12_dd_of",
            ),
            pytest.param(
                "sed -i 's/a/b/' f.txt", (["f.txt"], False),
                id="case13_sed_inplace",
            ),
            pytest.param(
                "sed -i.bak 's/a/b/' f.txt", (["f.txt"], False),
                id="case14_sed_inplace_backup_suffix",
            ),
            pytest.param(
                "python -c \"open('out.txt','w').write('x')\"", (["out.txt"], False),
                id="case15_python_literal_open",
            ),
            pytest.param(
                'echo "hello world" > "my file.txt"', (["my file.txt"], False),
                id="case22_quoted_redirect_target",
            ),
        ],
    )
    def test_tier1_high_confidence_targets(self, command, expected):
        assert guard._extract_terminal_write_targets(command) == expected

    # -- Tier 2: write intent, target uncertain -> uncertain=True --

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                "python -c \"open(name,'w').write(x)\"",
                id="case16_python_nonliteral_open",
            ),
            pytest.param(
                "bash -c 'touch inner.txt'",
                id="case17_nested_shell",
            ),
            pytest.param(
                "curl -O http://example.com/file.zip",
                id="case18_curl_dash_O_no_path",
            ),
            pytest.param(
                "wget http://example.com/file.zip",
                id="case19_wget_no_dash_O",
            ),
        ],
    )
    def test_tier2_uncertain_write_intent(self, command):
        assert guard._extract_terminal_write_targets(command) == ([], True)

    # -- Read-only: no write target, uncertain=False --

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("ls -la", id="case20_read_only_ls"),
            pytest.param("cat readme.txt", id="case21_read_only_cat"),
        ],
    )
    def test_read_only_command_no_targets(self, command):
        assert guard._extract_terminal_write_targets(command) == ([], False)

    # -- Unparseable: fail-open, never raises --

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("{{{{", id="case23_garbage_braces"),
            pytest.param("echo >", id="case23_redirect_without_target"),
            pytest.param('"unclosed quote', id="case23_unclosed_quote"),
        ],
    )
    def test_unparseable_command_never_raises(self, command):
        # Fail-open contract: no exception; result is a (list, bool) tuple.
        targets, uncertain = guard._extract_terminal_write_targets(command)
        assert isinstance(targets, list)
        assert isinstance(uncertain, bool)

    # -- _tokenize_command sanity --

    def test_tokenize_simple_split(self):
        assert guard._tokenize_command("echo hello world") == [
            "echo",
            "hello",
            "world",
        ]

    def test_tokenize_double_quoted_string_stays_one_token(self):
        assert guard._tokenize_command('echo "my file.txt"') == [
            "echo",
            "my file.txt",
        ]

    def test_tokenize_single_quoted_string_stays_one_token(self):
        assert guard._tokenize_command("echo 'my file.txt'") == [
            "echo",
            "my file.txt",
        ]


# ---------------------------------------------------------------- Terminal interception wiring (SCR-002, task 10.6)

class TestTerminalTier:
    """Guard-level terminal interception tiers (SCR-002, task 10.6).

    Exercises the full hook (_guard_hook "terminal") across the tier
    ladder from docs/spec-change-002-terminal-write-interception.md 2.3:
      - Tier 1: high-confidence target + violation -> block
      - Tier 2: write intent, target uncertain -> approve (rule_key
        "workspace-guard:terminal-write:<sha256(command + "\\x00" +
        base_dir)[:12]>", base chain workdir -> get_session_cwd ->
        working_dir_root)
      - Tier 3: read-only / unparseable / session-dir target -> allow
    Plus multi-target strictest-wins, the terminal_guard toggle, the
    runtime allowlist (Tier 0), and cross-profile via terminal.

    The wiring in _guard_logic does not exist yet ("terminal" is absent
    from INTERCEPTED_TOOLS), so the block/approve/rule_key tests below
    are EXPECTED to fail (None where block/approve is expected, KeyError
    on rule_key) until the implementation step lands; the allow-tier
    tests pass trivially now and must stay green after the wiring.
    """

    @staticmethod
    def _fwd(path):
        return path.replace("\\", "/")

    @staticmethod
    def _memo(workspaces):
        """Build a valid memo dict from {profile: workspace}."""
        return {
            "synced_at": "2026-08-06T00:00:00",
            "profiles": {
                name: {"workspace": ws, "status": "valid", "changed_at": "t"}
                for name, ws in workspaces.items()
            },
        }

    def _setup(self, root, memo=None, exempt_paths=None, config_override=None):
        """Open all patch contexts for a guard-level terminal test."""
        stack = ExitStack()
        stack.enter_context(patch.object(
            guard, "_registered_ctx", MagicMock(profile_name="default")
        ))
        # Session CWD unrecorded -> relative targets resolve against the
        # terminal workdir (or working_dir_root fallback). Deterministic.
        stack.enter_context(patch.object(guard, "get_session_cwd", return_value=None))
        if memo is not None:
            stack.enter_context(patch.object(guard, "load_memo", return_value=memo))
        stack.enter_context(_configure(root, exempt_paths=exempt_paths))
        if config_override is not None:
            # Simulates a guard-config.yaml with terminal_guard disabled
            # (matches the parsed content of the tmp_path file).
            stack.enter_context(patch(
                "config.load_guard_config", return_value=config_override
            ))
        return stack

    def _hook(self, command, workdir, task_id="task-1"):
        return guard._guard_hook(
            "terminal", {"command": command, "workdir": workdir}, task_id=task_id
        )

    # -- Tier 1: high-confidence violation -> block --

    def test_echo_redirect_to_root_blocked(self, tmp_path):
        root = str(tmp_path)
        with self._setup(root):
            result = self._hook("echo x > blank.txt", root)
        assert result is not None
        assert result["action"] == "block"
        # Same remediation guidance as write_file blocks (spec 2.3).
        assert "BLOCKED" in result["message"]

    # -- Tier 3: allow (session dir / read-only / unparseable) --

    def test_write_inside_session_dir_via_terminal_allowed(self, tmp_path):
        root = str(tmp_path)
        with self._setup(root):
            result = self._hook(
                "echo x > 20260806_120000_Task/Outputs/file.txt", root
            )
        assert result is None

    def test_readonly_command_allowed(self, tmp_path):
        root = str(tmp_path)
        with self._setup(root):
            result = self._hook("ls -la", root)
        assert result is None

    def test_unparseable_command_allowed(self, tmp_path):
        root = str(tmp_path)
        with self._setup(root):
            result = self._hook("{{{{", root)
        assert result is None

    # -- Tier 2: write intent, target uncertain -> approve --

    def test_python_c_non_literal_open_returns_approve(self, tmp_path):
        root = str(tmp_path)
        command = "python -c \"open(name,'w')\""
        with self._setup(root):
            result = self._hook(command, root)
        assert result is not None
        assert result["action"] == "approve"
        assert result["rule_key"].startswith("workspace-guard:terminal-write:")

    def test_curl_capital_O_returns_approve(self, tmp_path):
        root = str(tmp_path)
        with self._setup(root):
            result = self._hook("curl -O http://example.com/f.zip", root)
        assert result is not None
        assert result["action"] == "approve"
        assert result["rule_key"].startswith("workspace-guard:terminal-write:")

    # -- Multi-target: strictest-wins (ANY block -> block) --

    def test_multi_target_strictest_wins(self, tmp_path):
        root = str(tmp_path)
        command = (
            "echo x > root.txt && "
            "echo y > 20260806_120000_Task/Outputs/session.txt"
        )
        with self._setup(root):
            result = self._hook(command, root)
        assert result is not None
        assert result["action"] == "block"
        assert "BLOCKED" in result["message"]

    # -- Safety valve: terminal_guard disabled (spec 2.2) --

    def test_terminal_guard_disabled_skips_interception(self, tmp_path):
        root = str(tmp_path)
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("terminal_guard: disabled\n", encoding="utf-8")
        # config_override mirrors what the tmp_path YAML parses to.
        with self._setup(root, config_override={
            "exempt_paths": [], "terminal_guard": False
        }):
            result = self._hook("echo x > root.txt", root)
        assert result is None

    # -- Tier 0: runtime allowlist (workspace_guard_allow_path) --

    def test_runtime_allowlist_adds_path_and_reset_clears(self, tmp_path):
        root = str(tmp_path)
        target = str(tmp_path / "notes.txt")
        with self._setup(root):
            config.workspace_guard_allow_path(target)
            result = self._hook("echo x > notes.txt", root)
        assert result is None
        # reset_cache() clears the runtime allowlist -> same write blocks.
        with self._setup(root):
            config.reset_cache()
            blocked = self._hook("echo x > notes.txt", root)
        assert blocked is not None
        assert blocked["action"] == "block"

    # -- rule_key granularity: command + base_dir (spec 2.2) --

    def test_rule_key_differs_by_base_dir(self, tmp_path):
        root = str(tmp_path)
        session_dir = str(tmp_path / "20260806_120000_Task")
        command = "curl -O http://example.com/f.zip"
        with self._setup(root):
            r1 = self._hook(command, root)
            r2 = self._hook(command, session_dir)
        assert r1 is not None and r2 is not None
        assert r1["action"] == "approve" and r2["action"] == "approve"
        prefix = "workspace-guard:terminal-write:"
        for rule_key in (r1["rule_key"], r2["rule_key"]):
            assert rule_key.startswith(prefix)
            suffix = rule_key[len(prefix):]
            assert len(suffix) == 12
            assert all(c in "0123456789abcdef" for c in suffix)
        # Same command from a different base dir must not share an
        # [a]lways grant (no reuse across directories).
        assert r1["rule_key"] != r2["rule_key"]

    def test_rule_key_stable_for_normalized_base_dir(self, tmp_path):
        root = str(tmp_path)
        command = "curl -O http://example.com/f.zip"
        with self._setup(root):
            r1 = self._hook(command, root)
            # Separator / case variants of the same dir normalize alike.
            r2 = self._hook(command, self._fwd(root))
        assert r1 is not None and r2 is not None
        assert r1["action"] == "approve" and r2["action"] == "approve"
        assert r1["rule_key"] == r2["rule_key"]

    # -- Cross-profile via terminal (SCR-001 through the terminal lane) --

    def test_cross_profile_via_terminal(self, tmp_path):
        root = str(tmp_path / "default")
        other = str(tmp_path / "job-hunt")
        target = str(tmp_path / "job-hunt" / "output.txt")
        memo = self._memo({
            "default": self._fwd(root),
            "job-hunt": self._fwd(other),
        })
        # Forward-slash absolute target (backslashes are tokenizer escapes).
        command = "echo x > %s" % self._fwd(target)
        with self._setup(root, memo=memo):
            result = self._hook(command, root)
        assert result is not None
        assert result["action"] == "approve"
        assert result["rule_key"] == "cross-profile-write:job-hunt"
