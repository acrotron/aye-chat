"""Tests for ChromaDB corruption detection and recovery.

Tests the fix for https://github.com/acrotron/aye-chat/issues/203
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import sys

# This setup allows the test to be run directly or with a test runner.
try:
    from aye.model.index_manager.index_manager_state import (
        _is_corruption_error,
        _CORRUPTION_INDICATORS,
        InitializationCoordinator,
        IndexConfig,
    )
except ImportError:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from aye.model.index_manager.index_manager_state import (
        _is_corruption_error,
        _CORRUPTION_INDICATORS,
        InitializationCoordinator,
        IndexConfig,
    )


class TestCorruptionDetection(unittest.TestCase):
    """Test cases for _is_corruption_error function."""

    def test_detects_hnsw_segment_reader_error(self):
        """Should detect HNSW segment reader errors from issue #203."""
        error = Exception(
            "Error executing plan: Error sending backfill request to compactor: "
            "Error constructing hnsw segment reader"
        )
        self.assertTrue(_is_corruption_error(error))

    def test_detects_hnsw_error(self):
        """Should detect generic HNSW errors."""
        error = Exception("HNSW index is corrupted")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_segment_reader_error(self):
        """Should detect segment reader errors."""
        error = Exception("Failed to open segment reader")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_compactor_error(self):
        """Should detect compactor errors."""
        error = Exception("Compactor failed to process segment")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_executing_plan_error(self):
        """Should detect 'executing plan' errors."""
        error = Exception("Error executing plan for query")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_backfill_error(self):
        """Should detect backfill errors."""
        error = Exception("Backfill request failed")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_malformed_database(self):
        """Should detect malformed database errors."""
        error = Exception("database disk image is malformed")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_not_a_database(self):
        """Should detect 'not a database' errors."""
        error = Exception("file is not a database")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_sqlite_error(self):
        """Should detect SQLite errors."""
        error = Exception("sqlite3.OperationalError: unable to open database")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_corrupt_keyword(self):
        """Should detect errors containing 'corrupt' keyword."""
        error = Exception("Index file is corrupt")
        self.assertTrue(_is_corruption_error(error))

    def test_detects_operational_error_type(self):
        """Should detect OperationalError by exception type name."""
        # Create a mock exception with the right type name
        class OperationalError(Exception):
            pass
        error = OperationalError("Some database error")
        self.assertTrue(_is_corruption_error(error))

    def test_does_not_detect_normal_errors(self):
        """Should not flag normal errors as corruption."""
        normal_errors = [
            Exception("File not found"),
            Exception("Connection timeout"),
            Exception("Invalid query syntax"),
            ValueError("Invalid argument"),
            TypeError("Expected string, got int"),
        ]
        for error in normal_errors:
            self.assertFalse(
                _is_corruption_error(error),
                f"Should not detect '{error}' as corruption"
            )

    def test_case_insensitive_detection(self):
        """Should detect errors case-insensitively."""
        error = Exception("HNSW INDEX CORRUPTED")
        self.assertTrue(_is_corruption_error(error))

    def test_corruption_indicators_include_hnsw_terms(self):
        """Verify HNSW-related terms are in the indicators tuple."""
        indicators_lower = [ind.lower() for ind in _CORRUPTION_INDICATORS]
        self.assertIn("hnsw", indicators_lower)
        self.assertIn("segment reader", indicators_lower)
        self.assertIn("compactor", indicators_lower)
        self.assertIn("executing plan", indicators_lower)
        self.assertIn("backfill", indicators_lower)


class TestInitializationCoordinatorRecovery(unittest.TestCase):
    """Test cases for InitializationCoordinator recovery methods."""

    def setUp(self):
        """Set up a temporary directory for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.config = IndexConfig.from_params(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )
        # Create the .aye directory structure
        self.config.index_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up the temporary directory."""
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            # On Windows, ChromaDB might still hold file locks
            pass

    def test_reset_and_recover_resets_state(self):
        """reset_and_recover should reset initialization state."""
        coordinator = InitializationCoordinator(self.config)

        # Simulate initialized state
        coordinator._is_initialized = True
        coordinator._recovery_attempted = True
        coordinator.collection = MagicMock()

        # Mock _attempt_recovery to avoid actual ChromaDB calls
        with patch.object(coordinator, '_attempt_recovery', return_value=True) as mock_recovery:
            result = coordinator.reset_and_recover()

        # Verify _attempt_recovery was called
        mock_recovery.assert_called_once()
        self.assertTrue(result)

    def test_reset_and_recover_clears_collection_before_recovery(self):
        """reset_and_recover should clear collection before attempting recovery."""
        coordinator = InitializationCoordinator(self.config)
        coordinator._is_initialized = True
        coordinator.collection = MagicMock()

        collection_during_recovery = []

        def capture_state():
            collection_during_recovery.append(coordinator.collection)
            return True

        with patch.object(coordinator, '_attempt_recovery', side_effect=capture_state):
            coordinator.reset_and_recover()

        # Collection should have been None when _attempt_recovery was called
        self.assertIsNone(collection_during_recovery[0])

    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.index_manager.index_manager_state.rprint')
    def test_attempt_recovery_quarantines_corrupt_db(self, mock_rprint, mock_init_index):
        """_attempt_recovery should quarantine the corrupt database."""
        coordinator = InitializationCoordinator(self.config)

        # Create a fake corrupt chroma_db directory
        corrupt_db_path = self.config.chroma_db_path
        corrupt_db_path.mkdir(parents=True, exist_ok=True)
        (corrupt_db_path / "test_file.txt").write_text("corrupt data")

        # Mock initialize_index to avoid creating real ChromaDB
        mock_init_index.return_value = MagicMock()
        coordinator._attempt_recovery()

        # Original path should be gone (moved to quarantine)
        self.assertFalse(corrupt_db_path.exists())

        # Should have created a quarantine directory
        quarantine_dirs = list(self.config.index_dir.glob("chroma_db.corrupt.*"))
        self.assertEqual(len(quarantine_dirs), 1)

    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.index_manager.index_manager_state.rprint')
    def test_attempt_recovery_returns_true_on_success(self, mock_rprint, mock_init_index):
        """_attempt_recovery should return True when initialization succeeds."""
        coordinator = InitializationCoordinator(self.config)

        mock_collection = MagicMock()
        mock_init_index.return_value = mock_collection

        result = coordinator._attempt_recovery()

        self.assertTrue(result)
        self.assertEqual(coordinator.collection, mock_collection)
        self.assertTrue(coordinator._is_initialized)

    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.index_manager.index_manager_state.rprint')
    def test_attempt_recovery_returns_false_on_failure(self, mock_rprint, mock_init_index):
        """_attempt_recovery should return False when initialization fails."""
        coordinator = InitializationCoordinator(self.config)

        mock_init_index.side_effect = Exception("Init failed")

        result = coordinator._attempt_recovery()

        self.assertFalse(result)
        self.assertIsNone(coordinator.collection)
        # Should still mark as initialized to prevent retry loops
        self.assertTrue(coordinator._is_initialized)


class TestIndexManagerQueryRecovery(unittest.TestCase):
    """Test cases for IndexManager.query() corruption recovery."""

    def setUp(self):
        """Set up a temporary directory for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        (self.root_path / ".aye").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up the temporary directory."""
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    @patch('aye.model.index_manager.index_manager.register_manager')
    @patch('aye.model.index_manager.index_manager.vector_db')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_query_recovers_from_hnsw_corruption(self, mock_rprint, mock_vector_db, mock_register):
        """query() should recover when HNSW corruption is detected."""
        from aye.model.index_manager.index_manager import IndexManager

        # Create manager
        manager = IndexManager(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )

        # Set up as if initialized
        mock_collection = MagicMock()
        manager._init_coordinator._is_initialized = True
        manager._init_coordinator.collection = mock_collection

        # Mock query to raise HNSW error
        hnsw_error = Exception(
            "Error executing plan: Error constructing hnsw segment reader"
        )
        mock_vector_db.query_index.side_effect = hnsw_error

        # Mock reset_and_recover
        with patch.object(manager._init_coordinator, 'reset_and_recover', return_value=True) as mock_recover:
            result = manager.query("test query")

        # Should return empty list after recovery
        self.assertEqual(result, [])
        # Should have triggered recovery
        mock_recover.assert_called_once()

    @patch('aye.model.index_manager.index_manager.register_manager')
    @patch('aye.model.index_manager.index_manager.vector_db')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_query_reraises_non_corruption_errors(self, mock_rprint, mock_vector_db, mock_register):
        """query() should re-raise errors that are not corruption-related."""
        from aye.model.index_manager.index_manager import IndexManager

        manager = IndexManager(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )

        mock_collection = MagicMock()
        manager._init_coordinator._is_initialized = True
        manager._init_coordinator.collection = mock_collection

        # Mock query to raise a non-corruption error
        non_corruption_error = ValueError("Invalid query parameter")
        mock_vector_db.query_index.side_effect = non_corruption_error

        # Query should re-raise the error
        with self.assertRaises(ValueError) as context:
            manager.query("test query")

        self.assertEqual(str(context.exception), "Invalid query parameter")

    @patch('aye.model.index_manager.index_manager.register_manager')
    @patch('aye.model.index_manager.index_manager.vector_db')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_query_returns_empty_on_recovery_failure(self, mock_rprint, mock_vector_db, mock_register):
        """query() should return empty list if recovery also fails."""
        from aye.model.index_manager.index_manager import IndexManager

        manager = IndexManager(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )

        mock_collection = MagicMock()
        manager._init_coordinator._is_initialized = True
        manager._init_coordinator.collection = mock_collection

        # Mock query to raise HNSW error
        hnsw_error = Exception("Error constructing hnsw segment reader")
        mock_vector_db.query_index.side_effect = hnsw_error

        # Mock recovery to fail
        with patch.object(manager._init_coordinator, 'reset_and_recover', return_value=False):
            result = manager.query("test query")

        # Should return empty list even when recovery fails
        self.assertEqual(result, [])


class TestDeleteRecovery(unittest.TestCase):
    """Test cases for _handle_deleted_files corruption recovery."""

    def setUp(self):
        """Set up a temporary directory for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        (self.root_path / ".aye").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up the temporary directory."""
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    @patch('aye.model.index_manager.index_manager.register_manager')
    @patch('aye.model.index_manager.index_manager.vector_db')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_delete_recovers_from_corruption(self, mock_rprint, mock_vector_db, mock_register):
        """_handle_deleted_files should recover from corruption errors."""
        from aye.model.index_manager.index_manager import IndexManager

        manager = IndexManager(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )

        mock_collection = MagicMock()
        manager._init_coordinator._is_initialized = True
        manager._init_coordinator.collection = mock_collection

        # Mock delete to raise corruption error
        corruption_error = Exception("database disk image is malformed")
        mock_vector_db.delete_from_index.side_effect = corruption_error

        # Mock recovery
        with patch.object(manager._init_coordinator, 'reset_and_recover', return_value=True) as mock_recover:
            old_index = {"file1.py": {"hash": "abc"}}
            current_paths = set()  # file1.py was deleted

            # Should not raise an exception
            manager._handle_deleted_files(current_paths, old_index)

        # Recovery should have been attempted
        mock_recover.assert_called_once()

    @patch('aye.model.index_manager.index_manager.register_manager')
    @patch('aye.model.index_manager.index_manager.vector_db')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_delete_reraises_non_corruption_errors(self, mock_rprint, mock_vector_db, mock_register):
        """_handle_deleted_files should re-raise non-corruption errors."""
        from aye.model.index_manager.index_manager import IndexManager

        manager = IndexManager(
            root_path=self.root_path,
            file_mask="*.py",
            verbose=False,
            debug=False
        )

        mock_collection = MagicMock()
        manager._init_coordinator._is_initialized = True
        manager._init_coordinator.collection = mock_collection

        # Mock delete to raise non-corruption error
        other_error = ValueError("Some other error")
        mock_vector_db.delete_from_index.side_effect = other_error

        old_index = {"file1.py": {"hash": "abc"}}
        current_paths = set()

        # Should re-raise the error
        with self.assertRaises(ValueError):
            manager._handle_deleted_files(current_paths, old_index)


if __name__ == '__main__':
    unittest.main()
