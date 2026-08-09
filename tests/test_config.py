"""Tests for workspace-guard plugin config.py.

Tests config resolution chain, exempt paths, session dir detection,
guard-config.yaml loading, the SCR-001 profile-workspaces memo
lifecycle, the SCR-002 runtime allowlist, and the terminal_guard
toggle. Uses mock ctx and real tmp_path filesystem.
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add plugin directory to path for direct module import
_PLUGIN_DIR = str(Path(__file__).parent.parent / "src" / "workspace-guard")
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from config import (
    _format_memo,
    get_cached_config,
    is_exempt,
    is_inside_session_dir,
    is_runtime_allowlisted,
    load_guard_config,
    load_memo,
    resolve_working_dir_root,
    reset_cache,
    runtime_allowlist_add,
    runtime_allowlist_snapshot,
    save_memo,
    sync_memo,
    terminal_guard_enabled,
    workspace_guard_allow_path,
    workspace_guard_auto_update_workspace,
    workspace_guard_register_workspace,
)


def make_mock_ctx(profile_name="default"):
    ctx = MagicMock()
    ctx.profile_name = profile_name
    return ctx


@pytest.fixture(autouse=True)
def clean_cache():
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------- Resolution chain

class TestResolutionChain:
    def test_env_var_fallback(self, tmp_path):
        ctx = make_mock_ctx()
        fake_hermes = tmp_path / "fake_hermes"
        with patch("config._get_hermes_home", return_value=fake_hermes):
            with patch.dict(os.environ, {"TERMINAL_CWD": str(tmp_path)}):
                result = resolve_working_dir_root(ctx, config_path=tmp_path / "nonexist.yaml")
        assert result == str(tmp_path)

    def test_plugin_config_fallback(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("working_dir_root: /some/path\nexempt_paths: []\n")
        ctx = make_mock_ctx()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TERMINAL_CWD", None)
            result = resolve_working_dir_root(ctx, config_path=str(cfg))
        assert result == "/some/path"

    def test_fail_open_returns_none(self, tmp_path):
        ctx = make_mock_ctx()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TERMINAL_CWD", None)
            result = resolve_working_dir_root(ctx, config_path=tmp_path / "nonexist.yaml")
        assert result is None

    def test_profile_config_preferred_over_env(self, tmp_path):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_yaml = hermes_home / "config.yaml"
        config_yaml.write_text("terminal:\n  cwd: /from/profile\n")
        ctx = make_mock_ctx("default")
        with patch("config._get_hermes_home", return_value=hermes_home):
            with patch.dict(os.environ, {"TERMINAL_CWD": "/from/env"}):
                result = resolve_working_dir_root(ctx, config_path=tmp_path / "nonexist.yaml")
        assert result == "/from/profile"

    def test_resolution_source_logged(self, tmp_path, caplog):
        # SCR-005 task 13.6: successful resolution logs the resolving source
        # at INFO (profile-config / env / guard-config); caplog only, no real
        # log files (testing-standards 8 mock strategy).
        ctx = make_mock_ctx("default")

        # profile-config source
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("terminal:\n  cwd: /from/profile\n")
        with caplog.at_level(logging.INFO, logger="workspace-guard"):
            with patch("config._get_hermes_home", return_value=hermes_home):
                resolve_working_dir_root(ctx, config_path=tmp_path / "nonexist.yaml")
        assert any(
            "resolved from profile-config: /from/profile" in r.message
            for r in caplog.records
        )
        caplog.clear()

        # env source
        with caplog.at_level(logging.INFO, logger="workspace-guard"):
            with patch("config._get_hermes_home", return_value=tmp_path / "empty"):
                with patch.dict(os.environ, {"TERMINAL_CWD": "/from/env"}):
                    resolve_working_dir_root(ctx, config_path=tmp_path / "nonexist.yaml")
        assert any(
            "resolved from env: /from/env" in r.message for r in caplog.records
        )
        caplog.clear()

        # guard-config source
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("working_dir_root: /from/guardconfig\nexempt_paths: []\n")
        with caplog.at_level(logging.INFO, logger="workspace-guard"):
            with patch("config._get_hermes_home", return_value=tmp_path / "empty"):
                with patch.dict(os.environ, {}, clear=True):
                    os.environ.pop("TERMINAL_CWD", None)
                    resolve_working_dir_root(ctx, config_path=str(cfg))
        assert any(
            "resolved from guard-config: /from/guardconfig" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------- Exempt paths

class TestExemptPaths:
    def test_prefix_match(self):
        assert is_exempt("E:/ws/projects/foo/bar.py", ["E:/ws/projects/foo"])

    def test_no_match(self):
        assert not is_exempt("E:/ws/other/file.py", ["E:/ws/projects/foo"])

    def test_backslash_normalized(self):
        assert is_exempt("E:\\ws\\projects\\foo\\bar.py", ["E:/ws/projects/foo"])

    def test_empty_exempt_list(self):
        assert not is_exempt("E:/ws/file.py", [])

    def test_exact_match(self):
        assert is_exempt("E:/ws/AGENTS.md", ["E:/ws/AGENTS.md"])


# ---------------------------------------------------------------- Session dir detection

class TestSessionDirDetection:
    def test_valid_session_dir(self):
        assert is_inside_session_dir(
            "E:/ws/20260801_120000_Task/Outputs/file.txt", "E:/ws"
        )

    def test_bare_timestamp(self):
        assert is_inside_session_dir(
            "E:/ws/20260801_120000/file.txt", "E:/ws"
        )

    def test_not_session_dir(self):
        assert not is_inside_session_dir("E:/ws/random/file.txt", "E:/ws")

    def test_invalid_timestamp(self):
        assert not is_inside_session_dir(
            "E:/ws/99999999_999999_Task/file.txt", "E:/ws"
        )

    def test_root_level_file(self):
        assert not is_inside_session_dir("E:/ws/file.txt", "E:/ws")

    def test_backslash_paths(self):
        assert is_inside_session_dir(
            "E:\\ws\\20260801_120000_Task\\.tmp\\debug.py", "E:\\ws"
        )


# ---------------------------------------------------------------- Guard config loading

class TestGuardConfigLoading:
    def test_load_valid_yaml(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths:\n  - E:/ws/projects\nworking_dir_root: E:/ws\n")
        result = load_guard_config(str(cfg))
        assert "E:/ws/projects" in result["exempt_paths"]
        assert result["working_dir_root"] == "E:/ws"

    def test_load_missing_file(self, tmp_path):
        result = load_guard_config(str(tmp_path / "nonexist.yaml"))
        assert result["exempt_paths"] == []

    def test_load_empty_exempt(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\n")
        result = load_guard_config(str(cfg))
        assert result["exempt_paths"] == []

    # -- SCR-013 (task 16.3): default path resolution --

    def test_default_path_resolves_to_hermes_home_guard_config(self, tmp_path):
        # load_guard_config() with no args resolves to
        # HERMES_HOME/workspace-guard/guard-config.yaml (SCR-013 2.2/2.3).
        hermes_home = tmp_path / "hermes"
        cfg_dir = hermes_home / "workspace-guard"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "guard-config.yaml").write_text(
            "exempt_paths:\n  - E:/ws/projects\n"
            "allowed_root_files:\n  - AGENTS.md\n",
            encoding="utf-8",
        )
        with patch("config._get_hermes_home", return_value=hermes_home):
            result = load_guard_config()
        assert result["exempt_paths"] == ["E:/ws/projects"]
        assert result["allowed_root_files"] == ["AGENTS.md"]

    def test_default_path_missing_returns_defaults(self, tmp_path):
        # No config file -> default dict with the three always-present keys.
        with patch("config._get_hermes_home", return_value=tmp_path / "hermes"):
            result = load_guard_config()
        assert result["exempt_paths"] == []
        assert result["terminal_guard"] is True
        assert result["allowed_root_files"] == []


# ---------------------------------------------------------------- allowed_root_files parsing (SCR-011 D1, task 14.11)

class TestAllowedRootFiles:
    """allowed_root_files parsing (SCR-011 D1).

    Both the YAML parser and the no-PyYAML fallback line parser must
    accept the inline list form and the block form; the key missing ->
    empty list (STRICT fallback, fail-closed). The sys.modules patch
    forces the fallback parser deterministically regardless of whether
    PyYAML is installed.
    """

    def test_yaml_block_form(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text(
            "allowed_root_files:\n  - AGENTS.md\n  - RULES.txt\n",
            encoding="utf-8",
        )
        result = load_guard_config(str(cfg))
        assert result["allowed_root_files"] == ["AGENTS.md", "RULES.txt"]

    def test_yaml_inline_form(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text(
            'allowed_root_files: ["AGENTS.md", "RULES.txt"]\n', encoding="utf-8"
        )
        result = load_guard_config(str(cfg))
        assert result["allowed_root_files"] == ["AGENTS.md", "RULES.txt"]

    def test_fallback_inline_form(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text('allowed_root_files: ["AGENTS.md"]\n', encoding="utf-8")
        with patch.dict(sys.modules, {"yaml": None}):
            result = load_guard_config(str(cfg))
        assert result["allowed_root_files"] == ["AGENTS.md"]

    def test_fallback_block_form(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text(
            "allowed_root_files:\n  - AGENTS.md\n  - RULES.txt\n",
            encoding="utf-8",
        )
        with patch.dict(sys.modules, {"yaml": None}):
            result = load_guard_config(str(cfg))
        assert result["allowed_root_files"] == ["AGENTS.md", "RULES.txt"]

    def test_key_missing_returns_empty_list(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\n", encoding="utf-8")
        result = load_guard_config(str(cfg))
        assert result["allowed_root_files"] == []


# ---------------------------------------------------------------- Singleton caching

class TestSingletonCaching:
    def test_cache_returns_same_result(self, tmp_path):
        ctx = make_mock_ctx()
        fake_hermes = tmp_path / "fake_hermes"
        with patch("config._get_hermes_home", return_value=fake_hermes):
            with patch.dict(os.environ, {"TERMINAL_CWD": str(tmp_path)}):
                r1 = get_cached_config(ctx)
                r2 = get_cached_config(ctx)
        assert r1 == r2
        assert r1[0] == str(tmp_path)

    def test_reset_clears_cache(self, tmp_path):
        ctx = make_mock_ctx()
        fake_hermes = tmp_path / "fake_hermes"
        with patch("config._get_hermes_home", return_value=fake_hermes):
            with patch.dict(os.environ, {"TERMINAL_CWD": str(tmp_path)}):
                get_cached_config(ctx)
            reset_cache()
            with patch.dict(os.environ, {"TERMINAL_CWD": "/other"}):
                r = get_cached_config(ctx)
        assert r[0] == "/other"


# ---------------------------------------------------------------- Memo lifecycle (SCR-001, tasks 9.2/9.3)

def make_profile_home(tmp_path, name, cwd=None):
    """Create a fake Hermes profile home dir under tmp_path/hermes/profiles/<name>."""
    home = tmp_path / "hermes" / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.yaml"
    if cwd is None:
        cfg.write_text("terminal: {}\n", encoding="utf-8")
    else:
        cfg.write_text("terminal:\n  cwd: %s\n" % cwd, encoding="utf-8")
    return str(home)


class TestCrossProfile:
    """SCR-001 memo lifecycle: sync add/change/delete, valid/invalid, corruption -> .bak."""

    def test_sync_adds_valid_profile(self, tmp_path):
        ws = tmp_path / "ws_default"
        ws.mkdir()
        home = make_profile_home(tmp_path, "default")
        hermes_home = str(tmp_path / "hermes")
        with patch("config.list_profiles", return_value=[("default", home)]):
            with patch(
                "config.read_user_config_raw",
                return_value={"terminal": {"cwd": str(ws)}},
            ):
                memo = sync_memo(hermes_home=hermes_home)
        entry = memo["profiles"]["default"]
        assert entry["workspace"] == str(ws)
        assert entry["status"] == "valid"
        assert entry["changed_at"] is not None

    def test_sync_persists_to_hermes_home_root(self, tmp_path):
        # Memo lives at HERMES_HOME root (shared across profiles), not per-profile
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "default")
        hermes_home = str(tmp_path / "hermes")
        with patch("config.list_profiles", return_value=[("default", home)]):
            with patch(
                "config.read_user_config_raw",
                return_value={"terminal": {"cwd": str(ws)}},
            ):
                sync_memo(hermes_home=hermes_home)
        target = Path(hermes_home) / "workspace-guard" / "profile-workspaces.json"
        assert target.is_file()
        disk = json.loads(target.read_text(encoding="utf-8"))
        assert disk["profiles"]["default"]["status"] == "valid"

    def test_sync_adds_invalid_profile_when_no_cwd(self, tmp_path):
        home = make_profile_home(tmp_path, "learn")
        hermes_home = str(tmp_path / "hermes")
        with patch("config.list_profiles", return_value=[("learn", home)]):
            with patch("config.read_user_config_raw", return_value={"terminal": {}}):
                memo = sync_memo(hermes_home=hermes_home)
        entry = memo["profiles"]["learn"]
        assert entry["status"] == "invalid"
        assert entry["workspace"] is None

    def test_sync_invalid_when_workspace_dir_missing(self, tmp_path):
        home = make_profile_home(tmp_path, "ghost")
        hermes_home = str(tmp_path / "hermes")
        missing = str(tmp_path / "no_such_workspace")
        with patch("config.list_profiles", return_value=[("ghost", home)]):
            with patch(
                "config.read_user_config_raw",
                return_value={"terminal": {"cwd": missing}},
            ):
                memo = sync_memo(hermes_home=hermes_home)
        entry = memo["profiles"]["ghost"]
        assert entry["status"] == "invalid"
        assert entry["workspace"] is None

    def test_sync_rejects_relative_placeholder_workspace(self, tmp_path):
        # Relative placeholders (., auto, cwd) are not absolute workspaces -> invalid
        home = make_profile_home(tmp_path, "rel")
        hermes_home = str(tmp_path / "hermes")
        with patch("config.list_profiles", return_value=[("rel", home)]):
            with patch("config.read_user_config_raw", return_value={"terminal": {"cwd": "."}}):
                memo = sync_memo(hermes_home=hermes_home)
        entry = memo["profiles"]["rel"]
        assert entry["status"] == "invalid"
        assert entry["workspace"] is None

    def test_sync_keeps_changed_at_when_unchanged(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "stable")
        hermes_home = tmp_path / "hermes"
        memo_dir = hermes_home / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text(
            json.dumps(
                {
                    "synced_at": "2020-01-01T00:00:00",
                    "profiles": {
                        "stable": {
                            "workspace": str(ws),
                            "status": "valid",
                            "changed_at": "2020-01-01T00:00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        with patch("config.list_profiles", return_value=[("stable", home)]):
            with patch(
                "config.read_user_config_raw",
                return_value={"terminal": {"cwd": str(ws)}},
            ):
                memo = sync_memo(hermes_home=str(hermes_home))
        assert memo["profiles"]["stable"]["changed_at"] == "2020-01-01T00:00:00"

    def test_sync_change_sets_new_changed_at(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "moving")
        hermes_home = tmp_path / "hermes"
        memo_dir = hermes_home / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text(
            json.dumps(
                {
                    "synced_at": "2020-01-01T00:00:00",
                    "profiles": {
                        "moving": {
                            "workspace": "E:/old/place",
                            "status": "invalid",
                            "changed_at": "2020-01-01T00:00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        with patch("config.list_profiles", return_value=[("moving", home)]):
            with patch(
                "config.read_user_config_raw",
                return_value={"terminal": {"cwd": str(ws)}},
            ):
                memo = sync_memo(hermes_home=str(hermes_home))
        entry = memo["profiles"]["moving"]
        assert entry["workspace"] == str(ws)
        assert entry["status"] == "valid"
        assert entry["changed_at"] != "2020-01-01T00:00:00"

    def test_sync_drops_deleted_profile(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "gone")
        hermes_home = tmp_path / "hermes"
        memo_dir = hermes_home / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text(
            json.dumps(
                {
                    "synced_at": "2020-01-01T00:00:00",
                    "profiles": {
                        "gone": {
                            "workspace": str(ws),
                            "status": "valid",
                            "changed_at": "2020-01-01T00:00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        # profile no longer returned by list_profiles -> dropped
        with patch("config.list_profiles", return_value=[]):
            memo = sync_memo(hermes_home=str(hermes_home))
        assert "gone" not in memo["profiles"]

    def test_memo_corruption_falls_back_to_bak(self, tmp_path):
        hermes_home = tmp_path / "hermes"
        memo_dir = hermes_home / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text("{not json!!", encoding="utf-8")
        good = {
            "synced_at": "2020-01-01T00:00:00",
            "profiles": {
                "a": {"workspace": None, "status": "invalid", "changed_at": "2020-01-01T00:00:00"}
            },
        }
        (memo_dir / "profile-workspaces.json.bak").write_text(json.dumps(good), encoding="utf-8")
        memo = load_memo(hermes_home=str(hermes_home))
        assert memo == good

    def test_memo_corruption_both_files_returns_empty(self, tmp_path):
        hermes_home = tmp_path / "hermes"
        memo_dir = hermes_home / "workspace-guard"
        memo_dir.mkdir(parents=True)
        (memo_dir / "profile-workspaces.json").write_text("corrupt", encoding="utf-8")
        (memo_dir / "profile-workspaces.json.bak").write_text("also corrupt", encoding="utf-8")
        memo = load_memo(hermes_home=str(hermes_home))
        assert memo["profiles"] == {}
        assert memo["synced_at"] is None

    def test_load_memo_missing_file_returns_empty(self, tmp_path):
        memo = load_memo(hermes_home=str(tmp_path / "hermes"))
        assert memo["profiles"] == {}
        assert memo["synced_at"] is None

    def test_save_memo_atomic_creates_bak(self, tmp_path):
        hermes_home = str(tmp_path / "hermes")
        memo = {"synced_at": "2020-01-01T00:00:00", "profiles": {}}
        assert save_memo(memo, hermes_home=hermes_home) is True
        target = Path(hermes_home) / "workspace-guard" / "profile-workspaces.json"
        assert target.is_file()
        assert json.loads(target.read_text(encoding="utf-8")) == memo
        # second save retains a .bak of the previous good version
        memo2 = {"synced_at": "2020-01-02T00:00:00", "profiles": {}}
        assert save_memo(memo2, hermes_home=hermes_home) is True
        bak = Path(hermes_home) / "workspace-guard" / "profile-workspaces.json.bak"
        assert bak.is_file()
        assert json.loads(bak.read_text(encoding="utf-8"))["synced_at"] == "2020-01-01T00:00:00"

    def test_load_memo_round_trip(self, tmp_path):
        memo = {
            "synced_at": "2020-01-01T00:00:00",
            "profiles": {
                "a": {"workspace": "E:/w", "status": "valid", "changed_at": "2020-01-01T00:00:00"}
            },
        }
        save_memo(memo, hermes_home=str(tmp_path / "hermes"))
        assert load_memo(hermes_home=str(tmp_path / "hermes")) == memo

    def test_sync_without_hermes_cli_fails_open(self, tmp_path):
        # list_profiles is None (hermes_cli absent) -> empty memo, no crash
        memo = sync_memo(hermes_home=str(tmp_path / "hermes"))
        assert memo["profiles"] == {}
        assert memo["synced_at"] is not None

    def test_sync_falls_back_to_parse_terminal_cwd(self, tmp_path):
        # read_user_config_raw unavailable -> parse_terminal_cwd reads the file directly
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "default", cwd=str(ws))
        with patch("config.list_profiles", return_value=[("default", home)]):
            with patch("config.read_user_config_raw", None):
                memo = sync_memo(hermes_home=str(tmp_path / "hermes"))
        entry = memo["profiles"]["default"]
        assert entry["status"] == "valid"
        assert entry["workspace"] == str(ws)


# ---------------------------------------------------------------- Runtime allowlist (SCR-002, task 10.1)

class TestRuntimeAllowlist:
    def test_allow_path_returns_confirmation(self):
        result = workspace_guard_allow_path("E:/ws/projects/foo")
        assert "E:/ws/projects/foo" in result
        assert is_runtime_allowlisted("E:/ws/projects/foo")

    def test_allow_path_accepts_args_dict_and_kwargs(self):
        result = workspace_guard_allow_path(
            {"path": "E:/ws/projects/foo"}, task_id="t1", session_id="s1"
        )
        assert is_runtime_allowlisted("E:/ws/projects/foo")
        assert "E:/ws/projects/foo" in result

    def test_membership_normalizes_backslashes(self):
        runtime_allowlist_add("E:\\ws\\projects\\foo")
        assert is_runtime_allowlisted("E:/ws/projects/foo")

    def test_not_allowlisted_returns_false(self):
        runtime_allowlist_add("E:/ws/a")
        assert not is_runtime_allowlisted("E:/ws/b")

    def test_allowlist_exempts_subtree_writes(self):
        runtime_allowlist_add("E:/ws/a")
        assert is_runtime_allowlisted("E:/ws/a/x.txt")
        assert is_runtime_allowlisted("E:/ws/a/sub/dir/y.txt")
        assert is_runtime_allowlisted("e:/WS/A/X.txt")

    def test_reset_cache_clears_allowlist(self):
        runtime_allowlist_add("E:/ws/a")
        reset_cache()
        assert not is_runtime_allowlisted("E:/ws/a")
        assert runtime_allowlist_snapshot() == set()


# ---------------------------------------------------------------- terminal_guard toggle (SCR-002, task 10.2)

class TestTerminalGuardConfig:
    def test_default_enabled_when_absent(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\n", encoding="utf-8")
        assert terminal_guard_enabled(str(cfg)) is True

    def test_disabled_in_yaml(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\nterminal_guard: disabled\n", encoding="utf-8")
        assert terminal_guard_enabled(str(cfg)) is False

    def test_enabled_in_yaml(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\nterminal_guard: enabled\n", encoding="utf-8")
        assert terminal_guard_enabled(str(cfg)) is True

    def test_bool_false_disables(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\nterminal_guard: false\n", encoding="utf-8")
        assert terminal_guard_enabled(str(cfg)) is False

    def test_load_guard_config_exposes_key(self, tmp_path):
        cfg = tmp_path / "guard-config.yaml"
        cfg.write_text("exempt_paths: []\nterminal_guard: disabled\n", encoding="utf-8")
        result = load_guard_config(str(cfg))
        assert result["terminal_guard"] is False


# ---------------------------------------------------------------- Memo display format (SCR-011 2.6, task 14.10)

class TestFormatMemo:
    """_format_memo display format (SCR-011 2.6).

    Shape: header line, "Synced: <synced_at>", then one line per profile
    "<name>: <workspace> [<status>]  changed: <changed_at>" (names
    aligned to the widest name). Missing/corrupt memos degrade to
    "Synced: n/a" without raising.
    """

    @staticmethod
    def _two_profile_memo():
        return {
            "synced_at": "2026-08-08T20:29:48",
            "profiles": {
                "default": {
                    "workspace": "E:/ws/default",
                    "status": "valid",
                    "changed_at": "2026-08-07T19:49:26",
                },
                "job-hunt": {
                    "workspace": "E:/ws/job-hunt",
                    "status": "invalid",
                    "changed_at": "2026-08-07T19:49:26",
                },
            },
        }

    def test_format_shows_synced_at_and_one_line_per_profile(self):
        result = _format_memo(self._two_profile_memo())
        lines = result.splitlines()
        assert lines[0] == "[workspace-guard] Profile workspace memo"
        assert lines[1] == "Synced: 2026-08-08T20:29:48"
        assert len(lines) == 4  # header + synced + 2 profile lines
        # "default" is padded to the width of "job-hunt" (ljust).
        assert "  default : E:/ws/default [valid]  changed: 2026-08-07T19:49:26" in result
        assert "  job-hunt: E:/ws/job-hunt [invalid]  changed: 2026-08-07T19:49:26" in result

    def test_format_single_profile_line(self):
        memo = {
            "synced_at": "2026-08-08T20:29:48",
            "profiles": {
                "default": {
                    "workspace": "E:/ws/default",
                    "status": "valid",
                    "changed_at": "2026-08-07T19:49:26",
                }
            },
        }
        result = _format_memo(memo)
        assert "  default: E:/ws/default [valid]  changed: 2026-08-07T19:49:26" in result

    def test_format_empty_memo(self):
        result = _format_memo({"synced_at": None, "profiles": {}})
        assert result == "[workspace-guard] Profile workspace memo\nSynced: n/a"

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "garbage",
            [],
            {"synced_at": "t"},
            {"profiles": []},
            {"profiles": {"a": "not a dict"}},
        ],
    )
    def test_format_corrupt_inputs_never_raises(self, bad):
        result = _format_memo(bad)
        assert isinstance(result, str)
        assert result.startswith("[workspace-guard] Profile workspace memo")


# ---------------------------------------------------------------- Registration tool (SCR-011 2.2/2.6, task 14.10)

class TestRegisterWorkspace:
    """workspace_guard_register_workspace handler (config-level).

    ACTIVE-PROFILE-ONLY + CONFIG-FIRST: rejects non-current profiles,
    missing ctx, non-existent workspaces; sets terminal.cwd via Hermes
    set_config_value BEFORE writing the memo; a config failure aborts
    with no memo write; set_config_value unavailable -> reject.
    """

    @staticmethod
    def _make_ws(tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        return ws

    def test_rejects_non_current_profile(self, tmp_path):
        ws = self._make_ws(tmp_path)
        ctx = make_mock_ctx("default")
        result = workspace_guard_register_workspace(
            {"profile": "job-hunt", "workspace": str(ws)}, ctx=ctx
        )
        assert "Registration rejected" in result
        assert "not the active profile" in result
        assert "Registered" not in result

    def test_rejects_missing_ctx(self, tmp_path):
        # Fail-safe: a ctx-less dispatch (e.g. tests) is rejected.
        ws = self._make_ws(tmp_path)
        result = workspace_guard_register_workspace(
            {"profile": "default", "workspace": str(ws)}, ctx=None
        )
        assert "Registration rejected" in result
        assert "not the active profile" in result

    def test_rejects_nonexistent_workspace(self, tmp_path):
        ctx = make_mock_ctx("default")
        missing = tmp_path / "no_such"
        with patch("config.set_config_value", MagicMock()) as setter:
            result = workspace_guard_register_workspace(
                {"profile": "default", "workspace": str(missing)}, ctx=ctx
            )
        assert "Registration rejected" in result
        assert "not an existing directory" in result
        setter.assert_not_called()

    def test_success_sets_terminal_cwd_and_writes_memo(self, tmp_path):
        ws = self._make_ws(tmp_path)
        ctx = make_mock_ctx("default")
        hermes_home = tmp_path / "hermes"
        with patch("config.set_config_value", MagicMock()) as setter:
            with patch("config._get_hermes_home", return_value=hermes_home):
                result = workspace_guard_register_workspace(
                    {"profile": "default", "workspace": str(ws)}, ctx=ctx
                )
        assert "Registered profile 'default'" in result
        # Config-first: the durable terminal.cwd write happened.
        setter.assert_called_once_with("terminal.cwd", str(ws))
        # Then the real memo file was written (immediacy for scripts).
        memo_file = hermes_home / "workspace-guard" / "profile-workspaces.json"
        assert memo_file.is_file()
        disk = json.loads(memo_file.read_text(encoding="utf-8"))
        entry = disk["profiles"]["default"]
        assert entry["workspace"] == str(ws)
        assert entry["status"] == "valid"
        assert entry["changed_at"]

    def test_config_write_failure_aborts_without_memo_write(self, tmp_path):
        ws = self._make_ws(tmp_path)
        ctx = make_mock_ctx("default")
        hermes_home = tmp_path / "hermes"
        with patch("config.set_config_value", side_effect=RuntimeError("boom")):
            with patch("config._get_hermes_home", return_value=hermes_home):
                result = workspace_guard_register_workspace(
                    {"profile": "default", "workspace": str(ws)}, ctx=ctx
                )
        assert "Registration aborted" in result
        assert "no memo entry was written" in result
        assert not (hermes_home / "workspace-guard" / "profile-workspaces.json").exists()

    def test_set_config_value_unavailable_rejects(self, tmp_path):
        ws = self._make_ws(tmp_path)
        ctx = make_mock_ctx("default")
        with patch("config.set_config_value", None):
            result = workspace_guard_register_workspace(
                {"profile": "default", "workspace": str(ws)}, ctx=ctx
            )
        assert "set_config_value is unavailable" in result
        assert "no memo entry was written" in result


# ---------------------------------------------------------------- Auto-update tool (SCR-011 2.2/2.6, task 14.10)

class TestAutoUpdateWorkspace:
    """workspace_guard_auto_update_workspace handler (config-level).

    Rebuilds the memo through the same sync_memo() the update command
    uses; returns "[workspace-guard] Memo updated." plus the formatted
    memo display. Errors become the message (never raises).
    """

    def test_auto_update_rebuilds_memo_via_real_sync(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        home = make_profile_home(tmp_path, "default")
        hermes_home = tmp_path / "hermes"
        with patch("config._get_hermes_home", return_value=hermes_home):
            with patch("config._list_profiles", return_value=[("default", home)]):
                with patch("config._profile_workspace", return_value=str(ws)):
                    result = workspace_guard_auto_update_workspace({})
        assert result.startswith("[workspace-guard] Memo updated.")
        assert "[workspace-guard] Profile workspace memo" in result
        assert "Synced:" in result
        assert "default: %s [valid]" % str(ws) in result
        # The real memo file was rebuilt.
        memo_file = hermes_home / "workspace-guard" / "profile-workspaces.json"
        assert memo_file.is_file()
        disk = json.loads(memo_file.read_text(encoding="utf-8"))
        assert disk["profiles"]["default"]["status"] == "valid"
        assert disk["profiles"]["default"]["workspace"] == str(ws)

    def test_auto_update_calls_sync_memo(self):
        memo = {"synced_at": "2026-08-08T20:29:48", "profiles": {}}
        with patch("config.sync_memo", return_value=memo) as sync:
            result = workspace_guard_auto_update_workspace({})
        sync.assert_called_once_with()
        assert result.startswith("[workspace-guard] Memo updated.")
        assert "Synced: 2026-08-08T20:29:48" in result

    def test_auto_update_sync_failure_returns_error_message(self):
        with patch("config.sync_memo", side_effect=RuntimeError("boom")):
            result = workspace_guard_auto_update_workspace({})
        assert result == "[workspace-guard] Memo update failed: boom"
