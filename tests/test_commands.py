import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import aye.controller.commands as commands


class TestCommands(TestCase):
    # --- Authentication handlers ---
    @patch("aye.model.auth.login_flow")
    @patch("aye.model.download_plugins.fetch_plugins")
    @patch("aye.model.auth.get_token", return_value="fake-token")
    def test_login_and_fetch_plugins_success(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        commands.login_and_fetch_plugins()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()

    @patch("aye.model.auth.login_flow")
    @patch("aye.model.download_plugins.fetch_plugins")
    @patch("aye.model.auth.get_token", return_value=None)
    def test_login_and_fetch_plugins_no_token(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        commands.login_and_fetch_plugins()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_not_called()

    @patch("aye.model.auth.login_flow")
    @patch("aye.model.download_plugins.fetch_plugins", side_effect=Exception("Network error"))
    @patch("aye.model.auth.get_token", return_value="fake-token")
    def test_login_and_fetch_plugins_error(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        with self.assertRaisesRegex(Exception, "Network error"):
            commands.login_and_fetch_plugins()

        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()

    @patch("aye.model.auth.delete_token")
    def test_logout(self, mock_delete_token):
        commands.logout()
        mock_delete_token.assert_called_once()

    @patch("aye.model.auth.get_token", return_value="fake-token")
    def test_get_auth_status_token(self, mock_get_token):
        token = commands.get_auth_status_token()
        self.assertEqual(token, "fake-token")
        mock_get_token.assert_called_once()

    # --- Snapshot command handlers ---
    @patch("aye.model.snapshot.list_snapshots", return_value=["snap1", "snap2"])
    def test_get_snapshot_history(self, mock_list_snapshots):
        result = commands.get_snapshot_history()
        mock_list_snapshots.assert_called_once_with(None)
        self.assertEqual(result, ["snap1", "snap2"])

    @patch("aye.model.snapshot.list_snapshots", return_value=[("ts1", "/path/to/snap1")])
    def test_get_snapshot_content_found(self, mock_list_snapshots):
        with patch("pathlib.Path.read_text", return_value="snap content") as mock_read:
            content = commands.get_snapshot_content(Path("file.py"), "ts1")
            self.assertEqual(content, "snap content")
            mock_list_snapshots.assert_called_once_with(Path("file.py"))
            mock_read.assert_called_once()

    @patch("aye.model.snapshot.list_snapshots", return_value=[])
    def test_get_snapshot_content_not_found(self, mock_list_snapshots):
        content = commands.get_snapshot_content(Path("file.py"), "ts2")
        self.assertIsNone(content)

    @patch("aye.model.snapshot.restore_snapshot")
    def test_restore_from_snapshot(self, mock_restore):
        commands.restore_from_snapshot("001", "file.py")
        mock_restore.assert_called_once_with("001", "file.py")

    @patch("aye.model.snapshot.prune_snapshots", return_value=5)
    def test_prune_snapshots(self, mock_prune):
        result = commands.prune_snapshots(10)
        self.assertEqual(result, 5)
        mock_prune.assert_called_once_with(10)

    @patch("aye.model.snapshot.cleanup_snapshots", return_value=3)
    def test_cleanup_old_snapshots(self, mock_cleanup):
        result = commands.cleanup_old_snapshots(30)
        self.assertEqual(result, 3)
        mock_cleanup.assert_called_once_with(30)

    # --- Diff helpers (file-backend) ---
    @patch("aye.controller.commands.snapshot.get_backend")
    @patch("aye.model.snapshot.list_snapshots")
    @patch("pathlib.Path.exists", return_value=True)
    def test_get_diff_paths_latest(self, mock_exists, mock_list_snapshots, mock_get_backend):
        # Mock file backend (not git)
        mock_get_backend.return_value = MagicMock(spec=[])
        mock_list_snapshots.return_value = [("002_ts", "/path/snap2"), ("001_ts", "/path/snap1")]
        file_path = Path("file.py")

        path1, path2, is_stash = commands.get_diff_paths("file.py")

        self.assertEqual(path1, file_path)
        self.assertEqual(Path(path2), Path("/path/snap2"))
        self.assertFalse(is_stash)
        mock_list_snapshots.assert_called_once_with(file_path)

    @patch("aye.controller.commands.snapshot.get_backend")
    @patch("aye.model.snapshot.list_snapshots")
    @patch("pathlib.Path.exists", return_value=True)
    def test_get_diff_paths_one_snap(self, mock_exists, mock_list_snapshots, mock_get_backend):
        # Mock file backend (not git)
        mock_get_backend.return_value = MagicMock(spec=[])
        mock_list_snapshots.return_value = [("002_ts", "/path/snap2"), ("001_ts", "/path/snap1")]
        file_path = Path("file.py")

        path1, path2, is_stash = commands.get_diff_paths("file.py", snap_id1="001")

        self.assertEqual(path1, file_path)
        self.assertEqual(Path(path2), Path("/path/snap1"))
        self.assertFalse(is_stash)

    @patch("aye.controller.commands.snapshot.get_backend")
    @patch("aye.model.snapshot.list_snapshots")
    @patch("pathlib.Path.exists", return_value=True)
    def test_get_diff_paths_two_snaps(self, mock_exists, mock_list_snapshots, mock_get_backend):
        # Mock file backend (not git)
        mock_get_backend.return_value = MagicMock(spec=[])
        mock_list_snapshots.return_value = [("002_ts", "/path/snap2"), ("001_ts", "/path/snap1")]

        path1, path2, is_stash = commands.get_diff_paths("file.py", snap_id1="002", snap_id2="001")

        self.assertEqual(Path(path1), Path("/path/snap2"))
        self.assertEqual(Path(path2), Path("/path/snap1"))
        self.assertFalse(is_stash)

    @patch("pathlib.Path.exists", return_value=False)
    def test_get_diff_paths_file_not_exist(self, mock_exists):
        with self.assertRaises(FileNotFoundError):
            commands.get_diff_paths("file.py")

    @patch("aye.controller.commands.snapshot.get_backend")
    @patch("aye.model.snapshot.list_snapshots", return_value=[])
    @patch("pathlib.Path.exists", return_value=True)
    def test_get_diff_paths_no_snapshots(self, mock_exists, mock_list_snapshots, mock_get_backend):
        mock_get_backend.return_value = MagicMock(spec=[])
        with self.assertRaises(ValueError):
            commands.get_diff_paths("file.py")

    @patch("aye.controller.commands.snapshot.get_backend")
    @patch("aye.model.snapshot.list_snapshots")
    @patch("pathlib.Path.exists", return_value=True)
    def test_get_diff_paths_snap_id_not_found(self, mock_exists, mock_list_snapshots, mock_get_backend):
        mock_get_backend.return_value = MagicMock(spec=[])
        mock_list_snapshots.return_value = [("001_ts", "/path/snap1")]
        with self.assertRaises(ValueError):
            commands.get_diff_paths("file.py", snap_id1="999")
        with self.assertRaises(ValueError):
            commands.get_diff_paths("file.py", snap_id1="001", snap_id2="999")

    # --- GitRefBackend-specific coverage: get_snapshot_content + get_diff_paths ---

    def test_get_snapshot_content_git_ref_backend_matches_and_reads(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            f = repo / "a.txt"
            f.write_text("x", encoding="utf-8")

            class DummyGitRefBackend:
                def __init__(self, git_root: Path):
                    self.git_root = git_root
                    self.get_file_content_from_snapshot = MagicMock(return_value="SNAP")

            backend = DummyGitRefBackend(repo)

            with patch("aye.controller.commands.snapshot.get_backend", return_value=backend), patch(
                "aye.controller.commands.snapshot.list_snapshots",
                return_value=[("001_20250101T000000", "refs/aye/snapshots/001_20250101T000000")],
            ), patch("aye.controller.commands.GitRefBackend", DummyGitRefBackend):
                # Pass a short ordinal; code normalizes to 3 digits
                content = commands.get_snapshot_content(f, "1")

            self.assertEqual(content, "SNAP")
            backend.get_file_content_from_snapshot.assert_called_once_with(
                "a.txt", "refs/aye/snapshots/001_20250101T000000"
            )

    def test_get_snapshot_content_git_ref_backend_outside_git_root_returns_none(self):
        with tempfile.TemporaryDirectory() as repo_td, tempfile.TemporaryDirectory() as outside_td:
            repo = Path(repo_td)
            outside = Path(outside_td)
            f = outside / "a.txt"
            f.write_text("x", encoding="utf-8")

            class DummyGitRefBackend:
                def __init__(self, git_root: Path):
                    self.git_root = git_root
                    self.get_file_content_from_snapshot = MagicMock(return_value="SHOULD_NOT_BE_CALLED")

            backend = DummyGitRefBackend(repo)

            with patch("aye.controller.commands.snapshot.get_backend", return_value=backend), patch(
                "aye.controller.commands.snapshot.list_snapshots",
                return_value=[("001_20250101T000000", "refs/aye/snapshots/001_20250101T000000")],
            ), patch("aye.controller.commands.GitRefBackend", DummyGitRefBackend):
                content = commands.get_snapshot_content(f, "001")

            self.assertIsNone(content)
            backend.get_file_content_from_snapshot.assert_not_called()

    def test_get_snapshot_content_full_batch_id_match(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            f = repo / "a.txt"
            f.write_text("x", encoding="utf-8")

            class DummyGitRefBackend:
                def __init__(self, git_root: Path):
                    self.git_root = git_root
                    self.get_file_content_from_snapshot = MagicMock(return_value="SNAP")

            backend = DummyGitRefBackend(repo)
            batch_id = "002_20250101T000000"

            with patch("aye.controller.commands.snapshot.get_backend", return_value=backend), patch(
                "aye.controller.commands.snapshot.list_snapshots",
                return_value=[(batch_id, f"refs/aye/snapshots/{batch_id}")],
            ), patch("aye.controller.commands.GitRefBackend", DummyGitRefBackend):
                content = commands.get_snapshot_content(f, batch_id)

            self.assertEqual(content, "SNAP")
            backend.get_file_content_from_snapshot.assert_called_once_with("a.txt", f"refs/aye/snapshots/{batch_id}")

    def test_get_diff_paths_git_ref_latest_one_two_and_zero_normalization(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            f = repo / "a.txt"
            f.write_text("x", encoding="utf-8")

            class DummyGitRefBackend:
                def __init__(self, git_root: Path):
                    self.git_root = git_root

            backend = DummyGitRefBackend(repo)

            # snapshots list should be newest-first
            snapshots = [
                ("002_20250101T000002", "refs/aye/snapshots/002_20250101T000002"),
                ("001_20250101T000001", "refs/aye/snapshots/001_20250101T000001"),
            ]

            with patch("aye.controller.commands.snapshot.get_backend", return_value=backend), patch(
                "aye.controller.commands.snapshot.list_snapshots", return_value=snapshots
            ), patch("aye.controller.commands.Path.exists", return_value=True), patch(
                "aye.controller.commands.GitRefBackend", DummyGitRefBackend
            ):
                # latest
                path1, ref, is_git = commands.get_diff_paths(str(f))
                self.assertEqual(path1, Path(str(f)))
                self.assertTrue(is_git)
                self.assertIn("refs/aye/snapshots/002_", ref)
                self.assertTrue(ref.endswith(":a.txt"))

                # one snapshot (zero normalization: "1" matches "001")
                path1, ref, is_git = commands.get_diff_paths(str(f), snap_id1="1")
                self.assertEqual(path1, Path(str(f)))
                self.assertTrue(is_git)
                self.assertIn("refs/aye/snapshots/001_", ref)
                self.assertTrue(ref.endswith(":a.txt"))

                # two snapshots
                path1, ref, is_git = commands.get_diff_paths(str(f), snap_id1="002", snap_id2="001")
                self.assertEqual(path1, Path(str(f)))
                self.assertTrue(is_git)
                self.assertIn("|", ref)
                left, right = ref.split("|", 1)
                self.assertIn("refs/aye/snapshots/002_", left)
                self.assertIn("refs/aye/snapshots/001_", right)

    def test_get_diff_paths_git_ref_outside_git_root_errors(self):
        with tempfile.TemporaryDirectory() as repo_td, tempfile.TemporaryDirectory() as outside_td:
            repo = Path(repo_td)
            outside = Path(outside_td)
            f = outside / "a.txt"
            f.write_text("x", encoding="utf-8")

            class DummyGitRefBackend:
                def __init__(self, git_root: Path):
                    self.git_root = git_root

            backend = DummyGitRefBackend(repo)

            with patch("aye.controller.commands.snapshot.get_backend", return_value=backend), patch(
                "aye.controller.commands.snapshot.list_snapshots",
                return_value=[("001_20250101T000000", "refs/aye/snapshots/001_20250101T000000")],
            ), patch("aye.controller.commands.Path.exists", return_value=True), patch(
                "aye.controller.commands.GitRefBackend", DummyGitRefBackend
            ):
                with self.assertRaises(ValueError):
                    commands.get_diff_paths(str(f))

    # --- Config handlers ---
    @patch("aye.model.config.list_config", return_value={"key": "value"})
    def test_get_all_config(self, mock_list_config):
        result = commands.get_all_config()
        self.assertEqual(result, {"key": "value"})
        mock_list_config.assert_called_once()

    def test_set_config_value(self):
        with patch("aye.model.config.set_value") as mock_set:
            # Test with plain string
            commands.set_config_value("key", "value")
            mock_set.assert_called_with("key", "value")

            # Test with JSON string (number)
            commands.set_config_value("key_num", "123")
            mock_set.assert_called_with("key_num", 123)

            # Test with JSON string (boolean)
            commands.set_config_value("key_bool", "true")
            mock_set.assert_called_with("key_bool", True)

    @patch("aye.model.config.get_value", return_value="value")
    def test_get_config_value(self, mock_get):
        result = commands.get_config_value("key")
        self.assertEqual(result, "value")
        mock_get.assert_called_once_with("key")

    def test_delete_config_value(self):
        with patch("aye.model.config.delete_value", return_value=True) as mock_delete:
            result = commands.delete_config_value("key")
            self.assertTrue(result)
            mock_delete.assert_called_once_with("key")


class TestCommandsProjectSizing(TestCase):
    def test_calculate_total_file_size_skips_stat_errors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f1 = root / "a.txt"
            f2 = root / "b.txt"
            f1.write_text("hello", encoding="utf-8")
            f2.write_text("world", encoding="utf-8")

            # Force one stat() to fail
            original_stat = Path.stat

            def failing_stat(self_path: Path):
                # When patched with autospec=True, this receives the Path instance as the arg
                if self_path.name == "b.txt":
                    raise OSError("no stat")
                return original_stat(self_path)

            with patch("pathlib.Path.stat", autospec=True, side_effect=failing_stat):
                size = commands._calculate_total_file_size([f1, f2])

            self.assertEqual(size, f1.stat().st_size)

    @patch("aye.controller.commands.get_project_files_with_limit")
    @patch("aye.controller.commands.rprint")
    def test_is_small_project_limit_hit_large(self, mock_rprint, mock_get_files):
        root = Path("/tmp/project")
        mock_get_files.return_value = ([Path("a")], True)

        is_small, files = commands._is_small_project(root, "*.py", verbose=True)

        self.assertFalse(is_small)
        self.assertEqual(files, [Path("a")])
        mock_rprint.assert_called()

    @patch("aye.controller.commands.get_project_files_with_limit")
    @patch("aye.controller.commands._calculate_total_file_size")
    @patch("aye.controller.commands.rprint")
    def test_is_small_project_total_size_large(self, mock_rprint, mock_total, mock_get_files):
        root = Path("/tmp/project")
        mock_get_files.return_value = ([Path("a"), Path("b")], False)
        mock_total.return_value = commands.SMALL_PROJECT_TOTAL_SIZE_LIMIT

        is_small, files = commands._is_small_project(root, "*.py", verbose=True)

        self.assertFalse(is_small)
        self.assertEqual(files, [Path("a"), Path("b")])
        mock_rprint.assert_called()

    @patch("aye.controller.commands.get_project_files_with_limit")
    @patch("aye.controller.commands._calculate_total_file_size")
    @patch("aye.controller.commands.rprint")
    def test_is_small_project_small(self, mock_rprint, mock_total, mock_get_files):
        root = Path("/tmp/project")
        files = [Path("a"), Path("b")]
        mock_get_files.return_value = (files, False)
        mock_total.return_value = commands.SMALL_PROJECT_TOTAL_SIZE_LIMIT - 1

        is_small, discovered = commands._is_small_project(root, "*.py", verbose=True)

        self.assertTrue(is_small)
        self.assertEqual(discovered, files)
        mock_rprint.assert_called()


class TestCommandsInitializeProjectContext(TestCase):
    @patch("aye.controller.commands.onnx_manager.download_model_if_needed")
    @patch("aye.controller.commands.get_user_config")
    @patch("aye.controller.commands.rprint")
    def test_initialize_project_context_ground_truth_missing_exits(self, mock_rprint, mock_get_user_config, mock_dl):
        mock_get_user_config.side_effect = lambda k, d=None: "off" if k == "verbose" else d

        with self.assertRaises(SystemExit):
            commands.initialize_project_context(
                root=Path("/tmp/root"),
                file_mask="*.py",
                ground_truth_file="/tmp/does-not-exist.txt",
            )

        # initialize_project_context exits before the onnx download step when ground truth fails
        mock_dl.assert_not_called()
        self.assertTrue(any("Ground truth file not found" in str(c.args[0]) for c in mock_rprint.mock_calls if c.args))

    @patch("aye.controller.commands.onnx_manager.download_model_if_needed")
    @patch("aye.controller.commands.get_user_config")
    @patch("aye.controller.commands.rprint")
    def test_initialize_project_context_ground_truth_read_error_exits(self, mock_rprint, mock_get_user_config, mock_dl):
        mock_get_user_config.side_effect = lambda k, d=None: "off" if k == "verbose" else d

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "gt.txt"
            p.write_text("x", encoding="utf-8")

            with patch("pathlib.Path.read_text", side_effect=OSError("readfail")):
                with self.assertRaises(SystemExit):
                    commands.initialize_project_context(root=Path(td), file_mask="*.py", ground_truth_file=str(p))

        # initialize_project_context exits before the onnx download step when ground truth fails
        mock_dl.assert_not_called()
        self.assertTrue(
            any("Error reading ground truth file" in str(c.args[0]) for c in mock_rprint.mock_calls if c.args)
        )

    @patch("aye.controller.commands.IndexManager")
    @patch("aye.controller.commands._is_small_project")
    @patch("aye.controller.commands.PluginManager")
    @patch("aye.controller.commands.find_project_root")
    @patch("aye.controller.commands.onnx_manager.download_model_if_needed")
    @patch("aye.controller.commands.get_user_config")
    @patch("aye.controller.commands.rprint")
    def test_initialize_project_context_small_project_auto_mask_and_root_search(
        self,
        mock_rprint,
        mock_get_user_config,
        mock_dl,
        mock_find_root,
        mock_plugin_mgr_cls,
        mock_is_small,
        mock_index_mgr_cls,
    ):
        # verbose on, selected_model custom
        def _get_conf(k, default=None):
            if k == "verbose":
                return "on"
            if k == "selected_model":
                return "MODEL-X"
            return default

        mock_get_user_config.side_effect = _get_conf

        mock_find_root.return_value = Path("/tmp/proj")

        plugin_mgr = MagicMock()
        plugin_mgr.handle_command.return_value = {"mask": "*.js"}
        mock_plugin_mgr_cls.return_value = plugin_mgr

        mock_is_small.return_value = (True, [Path("a"), Path("b")])

        conf = commands.initialize_project_context(root=None, file_mask=None, ground_truth_file=None)

        mock_dl.assert_called_once_with(background=False)
        mock_find_root.assert_called_once()
        plugin_mgr.discover.assert_called_once()
        plugin_mgr.handle_command.assert_called_once()

        self.assertEqual(conf.root, Path("/tmp/proj"))
        self.assertEqual(conf.file_mask, "*.js")
        self.assertFalse(conf.use_rag)
        self.assertIsNone(conf.index_manager)
        self.assertEqual(conf.selected_model, "MODEL-X")
        mock_index_mgr_cls.assert_not_called()

    @patch("aye.controller.commands._is_small_project")
    @patch("aye.controller.commands.PluginManager")
    @patch("aye.controller.commands.onnx_manager.download_model_if_needed")
    @patch("aye.controller.commands.get_user_config")
    @patch("aye.controller.commands.rprint")
    def test_initialize_project_context_root_provided_no_auto_mask(
        self, mock_rprint, mock_get_user_config, mock_dl, mock_plugin_mgr_cls, mock_is_small
    ):
        mock_get_user_config.side_effect = lambda k, d=None: "off" if k in ("verbose",) else d

        plugin_mgr = MagicMock()
        mock_plugin_mgr_cls.return_value = plugin_mgr

        mock_is_small.return_value = (True, [])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conf = commands.initialize_project_context(root=root, file_mask="*.py", ground_truth_file=None)

        self.assertEqual(conf.root, root.resolve())
        self.assertEqual(conf.file_mask, "*.py")
        plugin_mgr.handle_command.assert_not_called()  # file_mask already provided
        mock_dl.assert_called_once_with(background=False)

    @patch("aye.controller.commands.IndexManager")
    @patch("aye.controller.commands._is_small_project")
    @patch("aye.controller.commands.PluginManager")
    @patch("aye.controller.commands.onnx_manager.download_model_if_needed")
    @patch("aye.controller.commands.get_user_config")
    @patch("aye.controller.commands.rprint")
    def test_initialize_project_context_large_project_prepare_sync_exception_handled(
        self,
        mock_rprint,
        mock_get_user_config,
        mock_dl,
        mock_plugin_mgr_cls,
        mock_is_small,
        mock_index_mgr_cls,
    ):
        mock_get_user_config.side_effect = lambda k, d=None: "on" if k == "verbose" else d

        plugin_mgr = MagicMock()
        plugin_mgr.handle_command.return_value = {"mask": "*.py"}
        mock_plugin_mgr_cls.return_value = plugin_mgr

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mock_is_small.return_value = (False, [root / "a.py"])

            index_mgr = MagicMock()
            index_mgr.prepare_sync.side_effect = RuntimeError("scan failed")
            mock_index_mgr_cls.return_value = index_mgr

            conf = commands.initialize_project_context(root=root, file_mask=None, ground_truth_file=None)

        self.assertTrue(conf.use_rag)
        self.assertIsNotNone(conf.index_manager)
        index_mgr.prepare_sync.assert_called_once()
        # Ensure exception path printed warnings
        printed = "\n".join(str(c.args[0]) for c in mock_rprint.mock_calls if c.args)
        self.assertIn("Error during project scan", printed)
        self.assertIn("Proceeding without index updates", printed)
