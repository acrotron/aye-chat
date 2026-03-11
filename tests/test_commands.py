"""Tests for the commands module.

Covers ProjectConfig, ProjectContextBuilder, and command functions.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from aye.controller.commands import (
    ProjectConfig,
    ProjectContextBuilder,
    initialize_project_context,
    get_snapshot_history,
    restore_from_snapshot,
    prune_snapshots,
    get_diff_paths,
    _find_project_root,
    _is_small_project,
)


class TestProjectConfig(unittest.TestCase):
    """Tests for ProjectConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        conf = ProjectConfig()
        
        self.assertIsNone(conf.root)
        self.assertFalse(conf.verbose)
        self.assertIsNone(conf.ground_truth)
        self.assertEqual(conf.file_mask, "*.py")
        self.assertFalse(conf.use_rag)
        self.assertIsNone(conf.index_manager)
        self.assertIsNone(conf.plugin_manager)
        self.assertFalse(conf._restore_tip_shown)

    def test_custom_values(self):
        """Test configuration with custom values."""
        conf = ProjectConfig(
            root=Path("/project"),
            verbose=True,
            ground_truth="test content",
            file_mask="*.js,*.ts",
            selected_model="test-model",
            use_rag=True,
        )
        
        self.assertEqual(conf.root, Path("/project"))
        self.assertTrue(conf.verbose)
        self.assertEqual(conf.ground_truth, "test content")
        self.assertEqual(conf.file_mask, "*.js,*.ts")
        self.assertEqual(conf.selected_model, "test-model")
        self.assertTrue(conf.use_rag)


class TestProjectContextBuilder(unittest.TestCase):
    """Tests for ProjectContextBuilder class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        
        # Create some test files
        (self.project_root / "main.py").write_text("# main")
        (self.project_root / "utils.py").write_text("# utils")

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def test_builder_creates_empty_config(self):
        """Test builder starts with empty config."""
        builder = ProjectContextBuilder()
        conf = builder.build()
        
        self.assertIsInstance(conf, ProjectConfig)
        self.assertIsNone(conf.root)

    @patch('aye.controller.commands.get_user_config', return_value="on")
    def test_with_verbose_on(self, mock_get_config):
        """Test verbose setting loaded as True."""
        builder = ProjectContextBuilder()
        builder.with_verbose()
        conf = builder.build()
        
        self.assertTrue(conf.verbose)
        mock_get_config.assert_called_once_with("verbose", "off")

    @patch('aye.controller.commands.get_user_config', return_value="off")
    def test_with_verbose_off(self, mock_get_config):
        """Test verbose setting loaded as False."""
        builder = ProjectContextBuilder()
        builder.with_verbose()
        conf = builder.build()
        
        self.assertFalse(conf.verbose)

    def test_with_ground_truth_valid_file(self):
        """Test loading ground truth from valid file."""
        gt_file = self.project_root / "ground_truth.txt"
        gt_file.write_text("test content")
        
        builder = ProjectContextBuilder()
        builder.with_ground_truth(str(gt_file))
        conf = builder.build()
        
        self.assertEqual(conf.ground_truth, "test content")

    def test_with_ground_truth_none(self):
        """Test ground truth with None path."""
        builder = ProjectContextBuilder()
        builder.with_ground_truth(None)
        conf = builder.build()
        
        self.assertIsNone(conf.ground_truth)

    def test_with_ground_truth_missing_file(self):
        """Test ground truth with missing file raises error."""
        builder = ProjectContextBuilder()
        
        with self.assertRaises(FileNotFoundError):
            builder.with_ground_truth("/nonexistent/file.txt")

    def test_with_root_explicit(self):
        """Test setting explicit root path."""
        builder = ProjectContextBuilder()
        builder.with_root(self.project_root)
        conf = builder.build()
        
        self.assertEqual(conf.root, self.project_root.resolve())

    @patch('aye.controller.commands._find_project_root')
    def test_with_root_auto_detect(self, mock_find_root):
        """Test auto-detecting root path."""
        mock_find_root.return_value = self.project_root
        
        builder = ProjectContextBuilder()
        builder.with_root(None)
        conf = builder.build()
        
        mock_find_root.assert_called_once()
        self.assertEqual(conf.root, self.project_root)

    @patch('aye.controller.commands.PluginManager')
    def test_with_plugins(self, mock_pm_class):
        """Test plugin manager initialization."""
        mock_pm = MagicMock()
        mock_pm_class.return_value = mock_pm
        
        builder = ProjectContextBuilder()
        builder.with_plugins()
        conf = builder.build()
        
        mock_pm_class.assert_called_once_with(verbose=False)
        mock_pm.discover.assert_called_once()
        self.assertEqual(conf.plugin_manager, mock_pm)

    def test_with_file_mask_explicit(self):
        """Test setting explicit file mask."""
        builder = ProjectContextBuilder()
        builder.with_file_mask("*.js,*.ts")
        conf = builder.build()
        
        self.assertEqual(conf.file_mask, "*.js,*.ts")

    def test_with_file_mask_auto_detect(self):
        """Test auto-detecting file mask via plugin."""
        mock_pm = MagicMock()
        mock_pm.handle_command.return_value = {"mask": "*.py,*.txt"}
        
        builder = ProjectContextBuilder()
        builder._conf.plugin_manager = mock_pm
        builder._conf.root = self.project_root
        builder.with_file_mask(None, auto_detect=True)
        conf = builder.build()
        
        self.assertEqual(conf.file_mask, "*.py,*.txt")
        mock_pm.handle_command.assert_called_once_with(
            "auto_detect_mask",
            {"project_root": str(self.project_root)}
        )

    def test_with_file_mask_auto_detect_fallback(self):
        """Test file mask fallback when auto-detect fails."""
        mock_pm = MagicMock()
        mock_pm.handle_command.return_value = None
        
        builder = ProjectContextBuilder()
        builder._conf.plugin_manager = mock_pm
        builder._conf.root = self.project_root
        builder.with_file_mask(None, auto_detect=True)
        conf = builder.build()
        
        self.assertEqual(conf.file_mask, "*.py")

    @patch('aye.controller.commands._is_small_project')
    def test_with_indexing_small_project(self, mock_is_small):
        """Test indexing skipped for small projects."""
        mock_is_small.return_value = (True, [])
        
        builder = ProjectContextBuilder()
        builder._conf.root = self.project_root
        builder.with_indexing()
        conf = builder.build()
        
        self.assertFalse(conf.use_rag)
        self.assertIsNone(conf.index_manager)

    @patch('aye.controller.commands._is_small_project')
    @patch('aye.model.index_manager.index_manager.IndexManager')
    def test_with_indexing_large_project(self, mock_im_class, mock_is_small):
        """Test indexing enabled for large projects."""
        mock_is_small.return_value = (False, [])
        mock_im = MagicMock()
        mock_im_class.return_value = mock_im
        
        builder = ProjectContextBuilder()
        builder._conf.root = self.project_root
        builder._conf.file_mask = "*.py"
        builder._conf.verbose = False
        builder.with_indexing()
        conf = builder.build()
        
        self.assertTrue(conf.use_rag)
        self.assertEqual(conf.index_manager, mock_im)
        mock_im.prepare_sync.assert_called_once()

    @patch('aye.controller.commands._is_small_project')
    @patch('aye.model.index_manager.index_manager.IndexManager', side_effect=Exception("Init failed"))
    def test_with_indexing_error_handling(self, mock_im_class, mock_is_small):
        """Test graceful handling of indexing errors."""
        mock_is_small.return_value = (False, [])
        
        builder = ProjectContextBuilder()
        builder._conf.root = self.project_root
        builder._conf.verbose = True
        builder.with_indexing()
        conf = builder.build()
        
        # Should fall back to no RAG
        self.assertFalse(conf.use_rag)
        self.assertIsNone(conf.index_manager)

    def test_with_indexing_no_root(self):
        """Test indexing skipped when no root set."""
        builder = ProjectContextBuilder()
        builder.with_indexing()  # No root set
        conf = builder.build()
        
        self.assertFalse(conf.use_rag)

    @patch('aye.controller.commands.get_user_config', return_value="test-model")
    @patch('aye.controller.commands.MODELS', [{"id": "test-model", "name": "Test"}])
    def test_with_model_saved(self, mock_get_config):
        """Test loading saved model."""
        builder = ProjectContextBuilder()
        builder.with_model()
        conf = builder.build()
        
        self.assertEqual(conf.selected_model, "test-model")

    @patch('aye.controller.commands.get_user_config', return_value="nonexistent-model")
    @patch('aye.controller.commands.set_user_config')
    @patch('aye.controller.commands.MODELS', [{"id": "default-model", "name": "Default"}])
    @patch('aye.controller.commands.DEFAULT_MODEL_ID', "default-model")
    def test_with_model_invalid_resets(self, mock_set_config, mock_get_config):
        """Test invalid saved model resets to default."""
        builder = ProjectContextBuilder()
        builder.with_model()
        conf = builder.build()
        
        self.assertEqual(conf.selected_model, "default-model")
        mock_set_config.assert_called_once_with("selected_model", "default-model")

    @patch('aye.controller.commands.get_user_config', return_value=None)
    def test_with_model_no_saved(self, mock_get_config):
        """Test default model when none saved."""
        builder = ProjectContextBuilder()
        builder.with_model()
        conf = builder.build()
        
        # Should use DEFAULT_MODEL_ID
        self.assertIsNotNone(conf.selected_model)

    def test_method_chaining(self):
        """Test that builder methods can be chained."""
        builder = ProjectContextBuilder()
        
        result = (
            builder
            .with_root(self.project_root)
            .with_file_mask("*.py")
        )
        
        self.assertIs(result, builder)
        conf = result.build()
        self.assertEqual(conf.root, self.project_root.resolve())
        self.assertEqual(conf.file_mask, "*.py")


class TestFindProjectRoot(unittest.TestCase):
    """Tests for _find_project_root function."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_finds_git_directory(self):
        """Test finding root by .git directory."""
        git_dir = self.base_path / ".git"
        git_dir.mkdir()
        subdir = self.base_path / "src" / "nested"
        subdir.mkdir(parents=True)
        
        result = _find_project_root(subdir)
        
        self.assertEqual(result, self.base_path)

    def test_finds_pyproject_toml(self):
        """Test finding root by pyproject.toml."""
        (self.base_path / "pyproject.toml").write_text("[project]")
        subdir = self.base_path / "src"
        subdir.mkdir()
        
        result = _find_project_root(subdir)
        
        self.assertEqual(result, self.base_path)

    def test_finds_package_json(self):
        """Test finding root by package.json."""
        (self.base_path / "package.json").write_text("{}")
        subdir = self.base_path / "src"
        subdir.mkdir()
        
        result = _find_project_root(subdir)
        
        self.assertEqual(result, self.base_path)

    def test_finds_ayeignore(self):
        """Test finding root by .ayeignore."""
        (self.base_path / ".ayeignore").write_text("node_modules")
        subdir = self.base_path / "src"
        subdir.mkdir()
        
        result = _find_project_root(subdir)
        
        self.assertEqual(result, self.base_path)

    def test_fallback_to_start(self):
        """Test fallback to start directory when no markers found."""
        subdir = self.base_path / "src"
        subdir.mkdir()
        
        result = _find_project_root(subdir)
        
        self.assertEqual(result, subdir.resolve())


class TestIsSmallProject(unittest.TestCase):
    """Tests for _is_small_project function."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch('aye.controller.commands.get_project_files')
    def test_small_project(self, mock_get_files):
        """Test small project detection."""
        mock_get_files.return_value = [Path("f1.py"), Path("f2.py")]
        
        is_small, files = _is_small_project(self.project_root, "*.py")
        
        self.assertTrue(is_small)
        self.assertEqual(len(files), 2)

    @patch('aye.controller.commands.get_project_files')
    @patch('aye.controller.commands.SMALL_PROJECT_FILE_LIMIT', 5)
    def test_large_project(self, mock_get_files):
        """Test large project detection."""
        mock_get_files.return_value = [Path(f"f{i}.py") for i in range(10)]
        
        is_small, files = _is_small_project(self.project_root, "*.py")
        
        self.assertFalse(is_small)
        self.assertEqual(len(files), 10)

    @patch('aye.controller.commands.get_project_files', side_effect=Exception("Error"))
    def test_error_handling(self, mock_get_files):
        """Test graceful error handling."""
        is_small, files = _is_small_project(self.project_root, "*.py", verbose=True)
        
        self.assertTrue(is_small)  # Fallback to small
        self.assertEqual(files, [])


class TestInitializeProjectContext(unittest.TestCase):
    """Tests for initialize_project_context function."""

    @patch('aye.controller.commands.onnx_manager.download_model_if_needed')
    @patch('aye.controller.commands.ProjectContextBuilder')
    def test_calls_builder_methods(self, mock_builder_class, mock_onnx):
        """Test that all builder methods are called."""
        mock_builder = MagicMock()
        mock_builder.with_verbose.return_value = mock_builder
        mock_builder.with_ground_truth.return_value = mock_builder
        mock_builder.with_root.return_value = mock_builder
        mock_builder.with_plugins.return_value = mock_builder
        mock_builder.with_file_mask.return_value = mock_builder
        mock_builder.with_indexing.return_value = mock_builder
        mock_builder.with_model.return_value = mock_builder
        mock_builder.build.return_value = ProjectConfig()
        mock_builder_class.return_value = mock_builder
        
        result = initialize_project_context(
            root=Path("/project"),
            file_mask="*.py",
            ground_truth_file="gt.txt"
        )
        
        mock_onnx.assert_called_once_with(background=False)
        mock_builder.with_verbose.assert_called_once()
        mock_builder.with_ground_truth.assert_called_once_with("gt.txt")
        mock_builder.with_root.assert_called_once_with(Path("/project"))
        mock_builder.with_plugins.assert_called_once()
        mock_builder.with_file_mask.assert_called_once_with("*.py")
        mock_builder.with_indexing.assert_called_once()
        mock_builder.with_model.assert_called_once()
        mock_builder.build.assert_called_once()
        self.assertIsInstance(result, ProjectConfig)

    @patch('aye.controller.commands.onnx_manager.download_model_if_needed', side_effect=Exception())
    @patch('aye.controller.commands.ProjectContextBuilder')
    def test_continues_on_onnx_error(self, mock_builder_class, mock_onnx):
        """Test that ONNX errors don't stop initialization."""
        mock_builder = MagicMock()
        mock_builder.with_verbose.return_value = mock_builder
        mock_builder.with_ground_truth.return_value = mock_builder
        mock_builder.with_root.return_value = mock_builder
        mock_builder.with_plugins.return_value = mock_builder
        mock_builder.with_file_mask.return_value = mock_builder
        mock_builder.with_indexing.return_value = mock_builder
        mock_builder.with_model.return_value = mock_builder
        mock_builder.build.return_value = ProjectConfig()
        mock_builder_class.return_value = mock_builder
        
        # Should not raise
        result = initialize_project_context()
        
        self.assertIsInstance(result, ProjectConfig)


class TestSnapshotCommands(unittest.TestCase):
    """Tests for snapshot command functions."""

    @patch('aye.controller.commands._list_all_snapshots_with_metadata')
    def test_get_snapshot_history(self, mock_list):
        """Test get_snapshot_history delegates correctly."""
        mock_list.return_value = ["snap1", "snap2"]
        
        result = get_snapshot_history()
        
        mock_list.assert_called_once()
        self.assertEqual(result, ["snap1", "snap2"])

    @patch('aye.controller.commands.restore_snapshot')
    def test_restore_from_snapshot(self, mock_restore):
        """Test restore_from_snapshot delegates correctly."""
        restore_from_snapshot(ordinal="001", file_name="test.py")
        
        mock_restore.assert_called_once_with(ordinal="001", file_name="test.py")

    @patch('aye.controller.commands.restore_snapshot')
    def test_restore_from_snapshot_defaults(self, mock_restore):
        """Test restore_from_snapshot with defaults."""
        restore_from_snapshot()
        
        mock_restore.assert_called_once_with(ordinal=None, file_name=None)

    @patch('aye.controller.commands.snapshot_prune')
    def test_prune_snapshots(self, mock_prune):
        """Test prune_snapshots delegates correctly."""
        mock_prune.return_value = 5
        
        result = prune_snapshots(keep_count=3)
        
        mock_prune.assert_called_once_with(keep_count=3)
        self.assertEqual(result, 5)

    @patch('aye.controller.commands.snapshot_prune')
    def test_prune_snapshots_default(self, mock_prune):
        """Test prune_snapshots with default keep count."""
        mock_prune.return_value = 0
        
        result = prune_snapshots()
        
        mock_prune.assert_called_once_with(keep_count=10)


class TestGetDiffPaths(unittest.TestCase):
    """Tests for get_diff_paths function."""

    @patch('aye.controller.commands.list_snapshots')
    def test_no_snapshots(self, mock_list):
        """Test when no snapshots exist."""
        mock_list.return_value = []
        
        current, snapshot, is_stash = get_diff_paths("test.py")
        
        self.assertEqual(current, Path("test.py"))
        self.assertIsNone(snapshot)
        self.assertFalse(is_stash)

    @patch('aye.controller.commands.list_snapshots')
    def test_with_snapshot(self, mock_list):
        """Test when snapshot exists."""
        mock_list.return_value = [("001", "/snap/test.py")]
        
        current, snapshot, is_stash = get_diff_paths("test.py")
        
        self.assertEqual(current, Path("test.py"))
        self.assertEqual(snapshot, Path("/snap/test.py"))
        self.assertFalse(is_stash)


if __name__ == "__main__":
    unittest.main()
