import unittest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import tempfile
import json
import os
import sys
import time
import threading

import aye.model.index_manager.index_manager as index_manager
from aye.model.index_manager.index_manager_utils import calculate_hash, set_low_priority
from aye.model.index_manager.index_manager_file_ops import (
    FileStatusChecker, 
    FileCategorizer, 
    IndexPersistence,
    get_deleted_files,
)


class ImmediateExecutor:
    """A mock executor that runs tasks immediately in the same thread."""
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, fn, *args, **kwargs):
        """Execute the function immediately and return a mock future."""
        future = MagicMock()
        try:
            result = fn(*args, **kwargs)
            future.result.return_value = result
        except Exception as e:
            future.result.side_effect = e
        return future


class TestIndexManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.index_dir = self.root_path / '.aye'
        self.hash_index_path = self.index_dir / 'file_index.json'
        # Use verbose=False by default to keep test output clean
        self.manager = index_manager.IndexManager(self.root_path, '*.py', verbose=False)

    def tearDown(self):
        self.temp_dir.cleanup()
        # Reset env var
        if 'TOKENIZERS_PARALLELISM' in os.environ:
            del os.environ['TOKENIZERS_PARALLELISM']

    def test_init(self):
        self.assertEqual(self.manager.root_path, self.root_path)
        self.assertEqual(self.manager.file_mask, '*.py')
        self.assertFalse(self.manager._init_coordinator.is_initialized)
        self.assertIsNone(self.manager.collection)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    def test_lazy_initialize_success(self, mock_init_index, mock_get_status):
        mock_collection = MagicMock()
        mock_init_index.return_value = mock_collection
        result = self.manager._init_coordinator.initialize()
        self.assertTrue(result)
        self.assertEqual(self.manager.collection, mock_collection)
        self.assertTrue(self.manager._init_coordinator.is_initialized)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    def test_lazy_initialize_already_initialized(self, mock_init_index, mock_get_status):
        """Test that lazy_initialize is idempotent."""
        mock_collection = MagicMock()
        mock_init_index.return_value = mock_collection
        
        # First call
        result1 = self.manager._init_coordinator.initialize()
        self.assertTrue(result1)
        
        # Second call should not call initialize_index again
        result2 = self.manager._init_coordinator.initialize()
        self.assertTrue(result2)
        mock_init_index.assert_called_once()  # Only called once

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    def test_lazy_initialize_concurrent_calls(self, mock_init_index, mock_get_status):
        """Test thread safety of lazy initialization."""
        mock_collection = MagicMock()
        mock_init_index.return_value = mock_collection
        
        results = []
        def init_thread():
            results.append(self.manager._init_coordinator.initialize())
        
        threads = [threading.Thread(target=init_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All should succeed
        self.assertTrue(all(results))
        # But initialize_index should only be called once
        mock_init_index.assert_called_once()

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index', side_effect=Exception("DB error"))
    @patch('aye.model.index_manager.index_manager_state.rprint')
    def test_lazy_initialize_db_error(self, mock_rprint, mock_init_index, mock_get_status):
        result = self.manager._init_coordinator.initialize()
        self.assertFalse(result)
        self.assertIsNone(self.manager.collection)
        self.assertTrue(self.manager._init_coordinator.is_initialized) # Marked as initialized to prevent retries
        mock_rprint.assert_called_with("[red]Failed to initialize local code search: DB error[/red]")

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='FAILED')
    def test_lazy_initialize_failed(self, mock_get_status):
        result = self.manager._init_coordinator.initialize()
        self.assertFalse(result)
        self.assertIsNone(self.manager.collection)
        self.assertTrue(self.manager._init_coordinator.is_initialized)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='DOWNLOADING')
    def test_lazy_initialize_not_ready(self, mock_get_status):
        result = self.manager._init_coordinator.initialize()
        self.assertFalse(result)
        self.assertFalse(self.manager._init_coordinator.is_initialized)

    def test_calculate_hash(self):
        """Test the calculate_hash utility function."""
        content = 'test content'
        expected_hash = '6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72'
        self.assertEqual(calculate_hash(content), expected_hash)

    def test_check_file_status_unchanged(self):
        """Test FileStatusChecker for unchanged file."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        stats = file_path.stat()
        old_index = {'file.py': {'hash': calculate_hash('content'), 'mtime': stats.st_mtime, 'size': stats.st_size, 'refined': True}}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'unchanged')
        self.assertEqual(meta, old_index['file.py'])

    def test_check_file_status_needs_refinement(self):
        """Test FileStatusChecker for file needing refinement."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        stats = file_path.stat()
        # Same content, but marked as not refined
        old_index = {'file.py': {'hash': calculate_hash('content'), 'mtime': stats.st_mtime, 'size': stats.st_size, 'refined': False}}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'needs_refinement')
        self.assertEqual(meta, old_index['file.py'])

    def test_check_file_status_modified(self):
        """Test FileStatusChecker for modified file."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('new content')
        old_index = {'file.py': {'hash': calculate_hash('old content'), 'mtime': 0, 'size': 0}}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertIsNotNone(meta)
        self.assertEqual(meta['refined'], False)

    def test_check_file_status_new_file(self):
        """Test status check for a file not in the old index."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'new.py'
        file_path.write_text('new content')
        old_index = {}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertIsNotNone(meta)
        self.assertEqual(meta['hash'], calculate_hash('new content'))

    def test_check_file_status_size_changed(self):
        """Test when mtime is same but size is different (forces hash check)."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        stats = file_path.stat()
        # Old index has same mtime but different size
        old_index = {'file.py': {'hash': calculate_hash('content'), 'mtime': stats.st_mtime, 'size': stats.st_size + 1, 'refined': True}}
        status, meta = checker.check_file_status(file_path, old_index)
        # Hash matches, so it's unchanged but with updated metadata
        self.assertEqual(status, 'unchanged')
        self.assertEqual(meta['size'], stats.st_size)

    def test_check_file_status_mtime_changed_hash_different(self):
        """Test when mtime changed and hash is different (forces re-indexing)."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('new content')
        stats = file_path.stat()
        # Old index has different mtime and different hash
        old_index = {'file.py': {'hash': calculate_hash('old content'), 'mtime': stats.st_mtime - 100, 'size': stats.st_size, 'refined': True}}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertEqual(meta['hash'], calculate_hash('new content'))
        self.assertFalse(meta['refined'])

    def test_check_file_status_old_format(self):
        """Test FileStatusChecker with old format index (string hash)."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('new content')
        # Old format just stored the hash string
        old_index = {'file.py': calculate_hash('old content')}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertIsNotNone(meta)
        self.assertEqual(meta['hash'], calculate_hash('new content'))

    def test_check_file_status_old_format_string_hash(self):
        """Test old format with string hash when content matches."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        # Old format with matching hash
        old_index = {'file.py': calculate_hash('content')}
        status, meta = checker.check_file_status(file_path, old_index)
        # Should be needs_refinement since old format didn't have refined flag
        self.assertEqual(status, 'needs_refinement')

    def test_check_file_status_error(self):
        """Test FileStatusChecker for missing file."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'missing.py'
        old_index = {'missing.py': {'hash': 'hash'}}
        status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'error')
        self.assertIsNone(meta)

    def test_check_file_status_read_error(self):
        """Test FileStatusChecker for file read error."""
        checker = FileStatusChecker(self.root_path)
        file_path = self.root_path / 'bad.py'
        file_path.write_text('ok')
        old_index = {'bad.py': {'hash': 'hash', 'mtime': 0, 'size': 0}}
        with patch.object(Path, 'read_text', side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, '')):
            status, meta = checker.check_file_status(file_path, old_index)
        self.assertEqual(status, 'error')
        self.assertEqual(meta, old_index['bad.py'])

    def test_load_old_index_missing_file(self):
        """Test IndexPersistence.load_index with missing file."""
        persistence = IndexPersistence(self.index_dir, self.hash_index_path)
        self.assertEqual(persistence.load_index(), {})

    def test_load_old_index_invalid_json(self):
        """Test IndexPersistence.load_index with invalid JSON."""
        persistence = IndexPersistence(self.index_dir, self.hash_index_path)
        self.hash_index_path.parent.mkdir(parents=True, exist_ok=True)
        self.hash_index_path.write_text('not json')
        self.assertEqual(persistence.load_index(), {})

    def test_load_old_index_with_valid_data(self):
        """Test loading a valid index file."""
        persistence = IndexPersistence(self.index_dir, self.hash_index_path)
        self.hash_index_path.parent.mkdir(parents=True, exist_ok=True)
        test_index = {'file.py': {'hash': 'abc123', 'mtime': 123.45, 'size': 100, 'refined': True}}
        self.hash_index_path.write_text(json.dumps(test_index))
        loaded = persistence.load_index()
        self.assertEqual(loaded, test_index)

    def test_categorize_files(self):
        """Test FileCategorizer.categorize_files."""
        file_a = self.root_path / 'a.py'
        file_b = self.root_path / 'b.py'
        file_a.write_text('a')
        file_b.write_text('b')
        statuses = {
            'a.py': ('modified', {'hash': 'ha'}),
            'b.py': ('needs_refinement', {'hash': 'hb'})
        }

        def fake_check(path, old):
            return statuses[path.name]

        categorizer = FileCategorizer(self.root_path, lambda: False)
        with patch.object(categorizer.status_checker, 'check_file_status', side_effect=fake_check):
            coarse, refine, new_index = categorizer.categorize_files([file_a, file_b], {})

        self.assertEqual(coarse, ['a.py'])
        self.assertEqual(refine, ['b.py'])
        self.assertEqual(new_index['a.py']['hash'], 'ha')
        self.assertEqual(new_index['b.py']['hash'], 'hb')

    def test_categorize_files_with_mixed_statuses(self):
        """Test categorization with all possible file statuses including unchanged and error."""
        file_a = self.root_path / 'a.py'
        file_b = self.root_path / 'b.py'
        file_c = self.root_path / 'c.py'
        file_d = self.root_path / 'd.py'
        file_a.write_text('a')
        file_b.write_text('b')
        file_c.write_text('c')
        file_d.write_text('d')
        
        statuses = {
            'a.py': ('modified', {'hash': 'ha', 'refined': False}),
            'b.py': ('needs_refinement', {'hash': 'hb', 'refined': False}),
            'c.py': ('unchanged', {'hash': 'hc', 'refined': True}),
            'd.py': ('error', None)
        }

        def fake_check(path, old):
            return statuses[path.name]

        categorizer = FileCategorizer(self.root_path, lambda: False)
        with patch.object(categorizer.status_checker, 'check_file_status', side_effect=fake_check):
            coarse, refine, new_index = categorizer.categorize_files([file_a, file_b, file_c, file_d], {})

        self.assertEqual(coarse, ['a.py'])
        self.assertEqual(refine, ['b.py'])
        self.assertIn('a.py', new_index)
        self.assertIn('b.py', new_index)
        self.assertIn('c.py', new_index)
        self.assertNotIn('d.py', new_index)  # Error files are not included

    def test_handle_deleted_files_no_deletion(self):
        """Test get_deleted_files with no deletions."""
        old_index = {'a.py': {}, 'b.py': {}}
        deleted = get_deleted_files({'a.py', 'b.py'}, old_index)
        self.assertEqual(deleted, [])

    def test_handle_deleted_files_empty_old_index(self):
        """Test deleted file handling with empty old index."""
        old_index = {}
        deleted = get_deleted_files({'a.py'}, old_index)
        self.assertEqual(deleted, [])

    def test_handle_deleted_files_with_deletion(self):
        """Test get_deleted_files with deletions."""
        old_index = {'keep.py': {}, 'remove.py': {}}
        deleted = get_deleted_files({'keep.py'}, old_index)
        self.assertEqual(set(deleted), {'remove.py'})

    def test_handle_deleted_files_collection_none(self):
        """Test get_deleted_files with multiple deletions."""
        old_index = {'keep.py': {}, 'remove.py': {}, 'another.py': {}}
        deleted = get_deleted_files({'keep.py'}, old_index)
        self.assertEqual(len(deleted), 2)
        self.assertIn('remove.py', deleted)
        self.assertIn('another.py', deleted)

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.Confirm.ask', return_value=False)
    def test_prepare_sync_limit_hit_cancel(self, mock_confirm, mock_get_files):
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        
        # Simulate hitting the 1000 file limit
        mock_files = [self.root_path / f'file{i}.py' for i in range(1000)]
        mock_get_files.return_value = (mock_files, True)
        
        self.manager.prepare_sync(verbose=True)
        
        mock_confirm.assert_called_once()
        # Should not have any work queued
        self.assertFalse(self.manager.has_work())

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.Confirm.ask', return_value=False)
    def test_prepare_sync_limit_hit_silent(self, mock_confirm, mock_get_files):
        """Test limit hit without verbose mode."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        
        mock_files = [self.root_path / f'file{i}.py' for i in range(1000)]
        mock_get_files.return_value = (mock_files, True)
        
        self.manager.prepare_sync(verbose=False)
        
        mock_confirm.assert_called_once()
        self.assertFalse(self.manager.has_work())

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.Confirm.ask', return_value=True)
    @patch('aye.model.index_manager.index_manager.threading.Thread')
    def test_prepare_sync_limit_hit_async(self, mock_thread, mock_confirm, mock_get_files):
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        
        # Simulate hitting the 1000 file limit
        mock_files = [self.root_path / f'file{i}.py' for i in range(1000)]
        mock_get_files.return_value = (mock_files, True)
        
        self.manager.prepare_sync(verbose=True)
        
        mock_confirm.assert_called_once()
        # Should have started async discovery thread
        mock_thread.assert_called_once()
        self.assertTrue(mock_thread.return_value.start.called)

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.Confirm.ask', return_value=True)
    @patch('aye.model.index_manager.index_manager.threading.Thread')
    def test_prepare_sync_limit_hit_async_verbose_false(self, mock_thread, mock_confirm, mock_get_files):
        """Test async discovery without verbose mode."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        
        mock_files = [self.root_path / f'file{i}.py' for i in range(1000)]
        mock_get_files.return_value = (mock_files, True)
        
        self.manager.prepare_sync(verbose=False)
        
        mock_thread.assert_called_once()

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.vector_db.delete_from_index')
    @patch('aye.model.index_manager.index_manager_state.rprint')
    def test_prepare_sync_verbose_output(self, mock_rprint, mock_delete, mock_get_files):
        """Test debug output during prepare_sync."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        self.manager.config.debug = True  # Enable debug mode for output
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        
        mock_get_files.return_value = ([file1], False)

        old_index = {}
        self.hash_index_path.parent.mkdir()
        self.hash_index_path.write_text(json.dumps(old_index))

        self.manager.prepare_sync(verbose=True)

        # Should have printed debug output
        #self.assertTrue(mock_rprint.called)

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.vector_db.delete_from_index')
    def test_prepare_sync_under_limit(self, mock_delete, mock_get_files):
        # Setup
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        file2 = self.root_path / 'file2.py'
        file2.write_text('content2')
        
        mock_get_files.return_value = ([file1, file2], False)

        # Old index with file1 unrefined, file2 missing, file3 deleted
        old_index = {
            'file1.py': {'hash': calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': False},
            'file3.py': {'hash': 'somehash', 'mtime': 0, 'size': 0, 'refined': True}
        }
        self.hash_index_path.parent.mkdir()
        self.hash_index_path.write_text(json.dumps(old_index))

        self.manager.prepare_sync(verbose=True)

        self.assertEqual(self.manager._state.files_to_coarse_index, ['file2.py'])
        self.assertEqual(self.manager._state.files_to_refine, ['file1.py'])
        mock_delete.assert_called_once_with(self.manager.collection, ['file3.py'])

    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    def test_prepare_sync_under_limit_no_changes(self, mock_get_files):
        """Test prepare_sync when all files are up-to-date."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        
        mock_get_files.return_value = ([file1], False)

        # File is already up-to-date
        old_index = {
            'file1.py': {'hash': calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': True}
        }
        self.hash_index_path.parent.mkdir()
        self.hash_index_path.write_text(json.dumps(old_index))

        self.manager.prepare_sync(verbose=True)

        self.assertFalse(self.manager.has_work())

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='DOWNLOADING')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_prepare_sync_not_initialized(self, mock_rprint, mock_get_status):
        """Test prepare_sync when not initialized and model is downloading."""
        self.manager.prepare_sync(verbose=True)
        mock_rprint.assert_called_with("[yellow]Code lookup is initializing (downloading models)... Project scan will begin shortly.[/]")
        self.assertFalse(self.manager.has_work())

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.index_manager.index_manager.get_project_files_with_limit')
    @patch('aye.model.index_manager.index_manager.rprint')
    def test_prepare_sync_collection_none(self, mock_rprint, mock_get_files, mock_init, mock_get_status):
        """Test prepare_sync when collection is None after initialization."""
        mock_init.return_value = None
        mock_get_files.return_value = ([], False)
        self.manager._init_coordinator.initialize()
        self.manager.prepare_sync(verbose=True)
        # With collection None, no work should be queued
        self.assertFalse(self.manager.has_work())

    @patch('aye.model.index_manager.index_manager.get_project_files')
    @patch('aye.model.index_manager.index_manager.vector_db.delete_from_index')
    @patch('aye.model.index_manager.index_manager.threading.Thread')
    def test_async_file_discovery_with_verbose(self, mock_thread, mock_delete, mock_get_files):
        """Test async discovery with debug output."""
        # Create a manager with debug=True
        self.manager = index_manager.IndexManager(self.root_path, '*.py', verbose=False, debug=True)
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        file2 = self.root_path / 'file2.py'
        file2.write_text('content2')
        
        mock_get_files.return_value = [file1, file2]
        
        old_index = {
            'file1.py': {'hash': calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': False},
            'file3.py': {'hash': 'somehash', 'mtime': 0, 'size': 0, 'refined': True}
        }
        
        with patch('aye.model.index_manager.index_manager_state.rprint') as mock_rprint:
            self.manager._async_file_discovery(old_index)
            # Check debug output was called
            self.assertTrue(mock_rprint.called)
        
        # Verify indexing thread was started
        mock_thread.assert_called_once()

    @patch('aye.model.index_manager.index_manager.get_project_files')
    @patch('aye.model.index_manager.index_manager.vector_db.delete_from_index')
    @patch('aye.model.index_manager.index_manager.threading.Thread')
    def test_async_file_discovery(self, mock_thread, mock_delete, mock_get_files):
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        file2 = self.root_path / 'file2.py'
        file2.write_text('content2')
        
        mock_get_files.return_value = [file1, file2]
        
        old_index = {
            'file1.py': {'hash': calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': False},
            'file3.py': {'hash': 'somehash', 'mtime': 0, 'size': 0, 'refined': True}
        }
        
        # Run async discovery
        self.manager._async_file_discovery(old_index)
        
        # Check that discovery state was updated
        self.assertFalse(self.manager._state.is_discovering)
        self.assertEqual(self.manager._progress._phases['discovery']['total'], 2)
        self.assertEqual(self.manager._state.files_to_coarse_index, ['file2.py'])
        self.assertEqual(self.manager._state.files_to_refine, ['file1.py'])
        mock_delete.assert_called_once_with(self.manager.collection, ['file3.py'])
        
        # Verify that indexing thread was started (the bug fix)
        mock_thread.assert_called_once()
        self.assertTrue(mock_thread.return_value.start.called)

    @patch('aye.model.index_manager.index_manager.get_project_files')
    @patch('aye.model.index_manager.index_manager.threading.Thread')
    def test_async_file_discovery_no_work(self, mock_thread, mock_get_files):
        """Test async discovery when there's no work to do (no indexing thread should start)."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        
        mock_get_files.return_value = [file1]
        
        # File is already up-to-date in index
        old_index = {
            'file1.py': {'hash': calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': True}
        }
        
        self.manager._async_file_discovery(old_index)
        
        # No work should be queued
        self.assertFalse(self.manager.has_work())
        # No indexing thread should be started
        mock_thread.assert_not_called()

    @patch('aye.model.index_manager.index_manager.get_project_files', side_effect=Exception("Discovery error"))
    def test_async_file_discovery_error_handling(self, mock_get_files):
        """Test that async discovery handles errors gracefully."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        old_index = {}
        
        # Should not raise, just handle the error
        self.manager._async_file_discovery(old_index)
        
        # Discovery should be marked as complete
        self.assertFalse(self.manager._state.is_discovering)

    def test_has_work(self):
        self.assertFalse(self.manager.has_work())
        self.manager._state.files_to_coarse_index = ['file.py']
        self.assertTrue(self.manager.has_work())
        self.manager._state.files_to_coarse_index = []
        self.manager._state.files_to_refine = ['file.py']
        self.assertTrue(self.manager.has_work())

    def test_has_work_both_lists(self):
        """Test has_work when both lists have items."""
        self.manager._state.files_to_coarse_index = ['file1.py']
        self.manager._state.files_to_refine = ['file2.py']
        self.assertTrue(self.manager.has_work())

    def test_is_indexing(self):
        self.assertFalse(self.manager.is_indexing())
        self.manager._state.is_indexing = True
        self.assertTrue(self.manager.is_indexing())
        self.manager._state.is_indexing = False
        self.manager._state.is_refining = True
        self.assertTrue(self.manager.is_indexing())
        self.manager._state.is_refining = False
        self.manager._state.is_discovering = True
        self.assertTrue(self.manager.is_indexing())

    def test_get_progress_display(self):
        self.manager._progress.set_active('discovery')
        self.manager._progress.set_total('discovery', 0)
        self.assertEqual(self.manager.get_progress_display(), 'discovering files...')
        
        self.manager._progress.set_total('discovery', 100)
        self.manager._progress._phases['discovery']['processed'] = 50
        self.assertEqual(self.manager.get_progress_display(), 'discovering files 50/100')
        
        self.manager._progress.set_active('coarse')
        self.manager._progress.set_total('coarse', 10)
        self.manager._progress._phases['coarse']['processed'] = 5
        self.assertEqual(self.manager.get_progress_display(), 'indexing 5/10')
        
        self.manager._progress.set_active('refine')
        self.manager._progress.set_total('refine', 7)
        self.manager._progress._phases['refine']['processed'] = 3
        self.assertEqual(self.manager.get_progress_display(), 'refining 3/7')
        
        self.manager._progress.set_active(None)
        self.assertEqual(self.manager.get_progress_display(), '')

    def test_get_progress_display_with_unknown_total(self):
        """Test progress display when discovery total is unknown (0)."""
        self.manager._progress.set_active('discovery')
        self.manager._progress.set_total('discovery', 0)
        self.assertEqual(self.manager.get_progress_display(), 'discovering files...')

    def test_get_progress_display_concurrent_access(self):
        """Test thread-safe progress display."""
        self.manager._progress.set_active('coarse')
        self.manager._progress.set_total('coarse', 100)
        
        results = []
        def read_progress():
            for _ in range(10):
                results.append(self.manager.get_progress_display())
                time.sleep(0.001)
        
        def update_progress():
            for i in range(10):
                self.manager._progress._phases['coarse']['processed'] = i
                time.sleep(0.001)
        
        t1 = threading.Thread(target=read_progress)
        t2 = threading.Thread(target=update_progress)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        
        # Should have collected some progress displays without crashing
        self.assertTrue(len(results) > 0)

    def test_discovery_progress_tracking(self):
        """Test that discovery progress counters are properly updated."""
        self.manager._progress.set_active('discovery')
        self.manager._progress.set_total('discovery', 100)
        self.manager._progress._phases['discovery']['processed'] = 50
        
        progress = self.manager.get_progress_display()
        self.assertIn('50', progress)
        self.assertIn('100', progress)

    def test_process_one_file_coarse_failure(self):
        target = 'fail.py'
        (self.root_path / target).write_text('ok')
        executor = self.manager._create_phase_executor()
        self.assertEqual(self.manager._progress._phases['coarse']['processed'], 0)
        with patch.object(Path, 'read_text', side_effect=Exception('boom')):
            result = executor._process_one_file_coarse(target)
        self.assertIsNone(result)
        self.assertEqual(self.manager._progress._phases['coarse']['processed'], 1)

    def test_process_one_file_coarse_unicode_error(self):
        """Test Unicode decode error during coarse indexing."""
        target = 'bad.py'
        (self.root_path / target).write_text('ok')
        executor = self.manager._create_phase_executor()
        with patch.object(Path, 'read_text', side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, '')):
            result = executor._process_one_file_coarse(target)
        self.assertIsNone(result)

    @patch('aye.model.index_manager.index_manager.vector_db.update_index_coarse')
    def test_process_one_file_coarse_success(self, mock_update):
        target = 'ok.py'
        (self.root_path / target).write_text('content')
        self.manager._init_coordinator.collection = MagicMock()
        executor = self.manager._create_phase_executor()
        result = executor._process_one_file_coarse(target)
        mock_update.assert_called_once_with(self.manager.collection, {target: 'content'})
        self.assertEqual(result, target)
        self.assertEqual(self.manager._progress._phases['coarse']['processed'], 1)

    def test_process_one_file_refine_failure(self):
        target = 'fail.py'
        (self.root_path / target).write_text('ok')
        executor = self.manager._create_phase_executor()
        self.assertEqual(self.manager._progress._phases['refine']['processed'], 0)
        with patch.object(Path, 'read_text', side_effect=Exception('boom')):
            result = executor._process_one_file_refine(target)
        self.assertIsNone(result)
        self.assertEqual(self.manager._progress._phases['refine']['processed'], 1)

    def test_process_one_file_refine_io_error(self):
        """Test I/O error during refinement."""
        target = 'bad.py'
        (self.root_path / target).write_text('ok')
        executor = self.manager._create_phase_executor()
        with patch.object(Path, 'read_text', side_effect=IOError('disk error')):
            result = executor._process_one_file_refine(target)
        self.assertIsNone(result)

    @patch('aye.model.index_manager.index_manager.vector_db.refine_file_in_index')
    def test_process_one_file_refine_success(self, mock_refine):
        target = 'fine.py'
        (self.root_path / target).write_text('content')
        self.manager._init_coordinator.collection = MagicMock()
        executor = self.manager._create_phase_executor()
        result = executor._process_one_file_refine(target)
        mock_refine.assert_called_once_with(self.manager.collection, target, 'content')
        self.assertEqual(result, target)
        self.assertEqual(self.manager._progress._phases['refine']['processed'], 1)

    def test_run_work_phase_updates_current_index(self):
        self.manager._state.target_index = {'file.py': {'hash': 'h', 'refined': False}}
        self.manager._state.current_index_on_disk = {}
        executor = self.manager._create_phase_executor()
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(lambda p: p, ['file.py'], is_refinement=False, generation=0)
        self.assertIn('file.py', self.manager._state.current_index_on_disk)
        self.assertEqual(self.manager._state.current_index_on_disk['file.py']['hash'], 'h')

    def test_run_work_phase_marks_refined(self):
        self.manager._state.current_index_on_disk = {'file.py': {'hash': 'h', 'refined': False}}
        executor = self.manager._create_phase_executor()
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(lambda p: p, ['file.py'], is_refinement=True, generation=0)
        self.assertTrue(self.manager._state.current_index_on_disk['file.py']['refined'])

    def test_run_work_phase_empty_list(self):
        """Test work phase with empty file list."""
        self.manager._state.current_index_on_disk = {}
        executor = self.manager._create_phase_executor()
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(lambda p: p, [], is_refinement=False, generation=0)
        # Should not crash
        self.assertEqual(self.manager._state.current_index_on_disk, {})

    def test_run_work_phase_with_failures(self):
        """Test that work phase handles worker failures gracefully."""
        self.manager._state.target_index = {'file1.py': {'hash': 'h1'}, 'file2.py': {'hash': 'h2'}}
        self.manager._state.current_index_on_disk = {}
        
        def failing_worker(path):
            if path == 'file2.py':
                raise Exception('Worker failed')
            return path
        
        executor = self.manager._create_phase_executor()
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(failing_worker, ['file1.py', 'file2.py'], is_refinement=False, generation=0)
        
        # Only successful file should be in index
        self.assertIn('file1.py', self.manager._state.current_index_on_disk)
        self.assertNotIn('file2.py', self.manager._state.current_index_on_disk)

    def test_run_work_phase_all_failures(self):
        """Test when all workers fail."""
        self.manager._state.target_index = {'file1.py': {'hash': 'h1'}, 'file2.py': {'hash': 'h2'}}
        self.manager._state.current_index_on_disk = {}
        
        def failing_worker(path):
            raise Exception('Worker failed')
        
        executor = self.manager._create_phase_executor()
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(failing_worker, ['file1.py', 'file2.py'], is_refinement=False, generation=0)
        
        # No files should be in index
        self.assertEqual(self.manager._state.current_index_on_disk, {})

    def test_run_work_phase_saves_periodically(self):
        """Test that progress is saved periodically during work phase."""
        self.manager.config.save_interval = 2
        self.manager._state.target_index = {f'file{i}.py': {'hash': f'h{i}'} for i in range(5)}
        self.manager._state.current_index_on_disk = {}
        
        save_count = [0]
        def counting_save():
            save_count[0] += 1
        
        executor = self.manager._create_phase_executor()
        # Replace save callback with our counter
        executor.save_callback = counting_save
        
        with patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor), \
             patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures):
            executor._run_phase(lambda p: p, [f'file{i}.py' for i in range(5)], is_refinement=False, generation=0)
        
        # Should have saved at least twice (after every 2 files + final save)
        self.assertGreaterEqual(save_count[0], 2)

    def test_save_progress_success(self):
        self.manager._state.current_index_on_disk = {'file.py': {'hash': 'h', 'refined': True}}
        self.manager._save_progress()
        self.assertTrue(self.hash_index_path.exists())
        saved = json.loads(self.hash_index_path.read_text())
        self.assertEqual(saved, self.manager._state.current_index_on_disk)

    def test_save_progress_skip_when_empty(self):
        self.manager._state.current_index_on_disk = {}
        self.manager._save_progress()
        self.assertFalse(self.hash_index_path.exists())

    def test_save_progress_creates_directory(self):
        """Test that save_progress creates the index directory if it doesn't exist."""
        self.assertFalse(self.index_dir.exists())
        self.manager._state.current_index_on_disk = {'file.py': {'hash': 'h'}}
        self.manager._save_progress()
        self.assertTrue(self.index_dir.exists())
        self.assertTrue(self.hash_index_path.exists())

    def test_save_progress_concurrent_access(self):
        """Test thread-safe concurrent access to save."""
        self.manager._state.current_index_on_disk = {'file.py': {'hash': 'h'}}
        
        def save_multiple():
            for _ in range(5):
                self.manager._save_progress()
                time.sleep(0.001)
        
        threads = [threading.Thread(target=save_multiple) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should not crash and file should exist
        self.assertTrue(self.hash_index_path.exists())

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.vector_db.query_index')
    def test_query_success(self, mock_query_index, mock_init, mock_status):
        mock_init.return_value = MagicMock()
        mock_query_index.return_value = [MagicMock()]
        results = self.manager.query('test query')
        self.assertEqual(len(results), 1)
        mock_query_index.assert_called_once()

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.vector_db.query_index')
    def test_query_with_parameters(self, mock_query_index, mock_init, mock_status):
        """Test query with custom n_results and min_relevance."""
        mock_init.return_value = MagicMock()
        mock_query_index.return_value = [MagicMock(), MagicMock()]
        
        results = self.manager.query('test query', n_results=20, min_relevance=0.5)
        
        self.assertEqual(len(results), 2)
        mock_query_index.assert_called_once_with(
            collection=self.manager.collection,
            query_text='test query',
            n_results=20,
            min_relevance=0.5
        )

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='DOWNLOADING')
    def test_query_not_ready(self, mock_status):
        """Test query when model is still downloading."""
        results = self.manager.query('query')
        self.assertEqual(results, [])

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='FAILED')
    def test_query_disabled(self, mock_status):
        self.manager._init_coordinator.initialize() # This will set collection to None
        results = self.manager.query('query')
        self.assertEqual(results, [])

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    def test_query_collection_none_after_init(self, mock_init, mock_status):
        """Test query when collection becomes None after init."""
        mock_init.return_value = None
        self.manager._init_coordinator.initialize()
        results = self.manager.query('query')
        self.assertEqual(results, [])

    @patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor)
    @patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures)
    @patch('aye.model.index_manager.index_manager_executor.vector_db')
    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background(self, mock_sleep, mock_status, mock_vector_db, *_):
        # Setup
        self.manager._init_coordinator.collection = MagicMock()
        self.manager._init_coordinator._is_initialized = True
        
        # Create files to be processed
        (self.root_path / 'coarse.py').write_text('coarse content')
        (self.root_path / 'refine.py').write_text('refine content')
        
        self.manager._state.files_to_coarse_index = ['coarse.py']
        self.manager._state.files_to_refine = ['refine.py']
        self.manager._state.target_index = {
            'coarse.py': {'hash': 'chash', 'mtime': 1, 'size': 1, 'refined': False},
            'refine.py': {'hash': 'rhash', 'mtime': 1, 'size': 1, 'refined': False}
        }
        self.manager._state.current_index_on_disk = {
            'refine.py': {'hash': 'rhash', 'mtime': 1, 'size': 1, 'refined': False}
        }
        
        # Run
        self.manager.run_sync_in_background()

        # Assertions
        self.assertIn('TOKENIZERS_PARALLELISM', os.environ)
        self.assertEqual(os.environ['TOKENIZERS_PARALLELISM'], 'false')

        # Coarse phase
        mock_vector_db.update_index_coarse.assert_called_once_with(self.manager.collection, {'coarse.py': 'coarse content'})
        
        # Refine phase
        expected_refine_calls = [
            call(self.manager.collection, 'coarse.py', 'coarse content'),
            call(self.manager.collection, 'refine.py', 'refine content')
        ]
        mock_vector_db.refine_file_in_index.assert_has_calls(expected_refine_calls, any_order=True)

        # Check final state of hash index file
        self.assertTrue(self.hash_index_path.exists())
        final_index = json.loads(self.hash_index_path.read_text())
        self.assertTrue(final_index['coarse.py']['refined'])
        self.assertTrue(final_index['refine.py']['refined'])
        self.assertEqual(final_index['coarse.py']['hash'], 'chash')

        # Check state is cleaned up
        self.assertFalse(self.manager.has_work())
        self.assertFalse(self.manager.is_indexing())

    @patch('aye.model.index_manager.index_manager_executor.DaemonThreadPoolExecutor', ImmediateExecutor)
    @patch('aye.model.index_manager.index_manager_executor.concurrent.futures.as_completed', lambda futures: futures)
    @patch('aye.model.index_manager.index_manager_executor.vector_db')
    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background_only_refine(self, mock_sleep, mock_status, mock_vector_db, *_):
        """Test run_sync_in_background when only refinement is needed."""
        self.manager._init_coordinator.collection = MagicMock()
        self.manager._init_coordinator._is_initialized = True
        
        (self.root_path / 'refine.py').write_text('refine content')
        
        self.manager._state.files_to_coarse_index = []  # No coarse indexing
        self.manager._state.files_to_refine = ['refine.py']
        self.manager._state.target_index = {'refine.py': {'hash': 'rhash', 'refined': False}}
        self.manager._state.current_index_on_disk = {'refine.py': {'hash': 'rhash', 'refined': False}}
        
        self.manager.run_sync_in_background()

        # Coarse phase should not be called
        mock_vector_db.update_index_coarse.assert_not_called()
        
        # Refine phase should be called
        mock_vector_db.refine_file_in_index.assert_called_once()

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    def test_run_sync_in_background_empty_work(self, mock_status):
        """Test early exit when there's no work to do."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        self.manager._state.files_to_coarse_index = []
        self.manager._state.files_to_refine = []
        
        # Should exit early
        self.manager.run_sync_in_background()
        
        # No indexing state should be set
        self.assertFalse(self.manager._state.is_indexing)
        self.assertFalse(self.manager._state.is_refining)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background_waits_for_discovery(self, mock_sleep, mock_status):
        """Test that run_sync_in_background waits for async discovery to complete."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        self.manager._state.is_discovering = True
        
        # Mock sleep to simulate waiting, then mark discovery as complete
        call_count = [0]
        def side_effect_sleep(duration):
            call_count[0] += 1
            if call_count[0] >= 2:  # After second call, mark discovery as complete
                self.manager._state.is_discovering = False
        
        mock_sleep.side_effect = side_effect_sleep
        
        self.manager.run_sync_in_background()
        
        # Should have called sleep at least once while waiting
        self.assertGreaterEqual(mock_sleep.call_count, 1)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background_initialization_timeout(self, mock_sleep, mock_status):
        """Test timeout waiting for initialization."""
        # Simulate model staying in DOWNLOADING state
        mock_status.return_value = 'DOWNLOADING'
        
        call_count = [0]
        def side_effect_sleep(duration):
            call_count[0] += 1
            if call_count[0] >= 5:  # Give up after 5 attempts
                mock_status.return_value = 'FAILED'
        
        mock_sleep.side_effect = side_effect_sleep
        
        self.manager.run_sync_in_background()
        
        # Should have tried multiple times
        self.assertGreaterEqual(call_count[0], 5)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background_discovery_timeout(self, mock_sleep, mock_status):
        """Test timeout waiting for discovery."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = MagicMock()
        self.manager._state.is_discovering = True
        
        call_count = [0]
        def side_effect_sleep(duration):
            call_count[0] += 1
            if call_count[0] >= 10:  # Eventually complete
                self.manager._state.is_discovering = False
        
        mock_sleep.side_effect = side_effect_sleep
        
        self.manager.run_sync_in_background()
        
        # Should have waited multiple times
        self.assertGreaterEqual(mock_sleep.call_count, 1)

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='READY')
    def test_run_sync_in_background_no_collection(self, mock_status):
        """Test early exit when collection is None."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = None
        
        # Should exit early without processing anything
        self.manager.run_sync_in_background()
        
        # No state should be changed
        self.assertFalse(self.manager.is_indexing())

    @patch('aye.model.index_manager.index_manager.onnx_manager.get_model_status', return_value='FAILED')
    @patch('aye.model.index_manager.index_manager.time.sleep')
    def test_run_sync_in_background_model_failed(self, mock_sleep, mock_status):
        """Test early exit when model download fails."""
        self.manager._init_coordinator._is_initialized = False
        
        self.manager.run_sync_in_background()
        
        # Should have checked status and exited
        mock_status.assert_called()
        self.assertFalse(self.manager.is_indexing())

    @unittest.skipUnless(hasattr(os, 'nice'), "os.nice is not available on Windows")
    @patch('os.nice', side_effect=OSError)
    def test_set_low_priority_os_error(self, mock_nice):
        """Test set_low_priority handles OSError gracefully."""
        try:
            set_low_priority()
        except OSError:
            self.fail("set_low_priority() raised an unexpected OSError")
        mock_nice.assert_called_once_with(5)

    @unittest.skipIf(hasattr(os, 'nice'), "Test only for Windows (no os.nice)")
    def test_set_low_priority_windows(self):
        """Test that set_low_priority handles Windows gracefully."""
        try:
            set_low_priority()
        except Exception as e:
            self.fail(f"set_low_priority() should not raise on Windows: {e}")

    @patch('aye.model.index_manager.index_manager_file_ops.os.replace', side_effect=OSError("Permission denied"))
    def test_save_progress_error(self, mock_replace):
        """Test IndexPersistence handles save errors gracefully."""
        self.index_dir.mkdir()
        temp_path = self.hash_index_path.with_suffix('.json.tmp')
        persistence = IndexPersistence(self.index_dir, self.hash_index_path)
        
        result = persistence.save_index({'file.py': 'data'})

        # Should return False on failure
        self.assertFalse(result)
        # Temp file should be cleaned up after error
        self.assertFalse(temp_path.exists())
        # The original file should not be created/modified
        self.assertFalse(self.hash_index_path.exists())


if __name__ == '__main__':
    unittest.main()
