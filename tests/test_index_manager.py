import unittest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import tempfile
import json
import os
import time

import aye.model.index_manager as index_manager

class ImmediateExecutor:
    """A mock executor that runs tasks immediately in the same thread."""
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, fn, *args, **kwargs):
        future = MagicMock()
        try:
            future.result.return_value = fn(*args, **kwargs)
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
        self.assertFalse(self.manager._is_initialized)
        self.assertIsNone(self.manager.collection)

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    def test_lazy_initialize_success(self, mock_init_index, mock_get_status):
        mock_collection = MagicMock()
        mock_init_index.return_value = mock_collection
        result = self.manager._lazy_initialize()
        self.assertTrue(result)
        self.assertEqual(self.manager.collection, mock_collection)
        self.assertTrue(self.manager._is_initialized)

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index', side_effect=Exception("DB error"))
    @patch('aye.model.index_manager.rprint')
    def test_lazy_initialize_db_error(self, mock_rprint, mock_init_index, mock_get_status):
        result = self.manager._lazy_initialize()
        self.assertFalse(result)
        self.assertIsNone(self.manager.collection)
        self.assertTrue(self.manager._is_initialized) # Marked as initialized to prevent retries
        mock_rprint.assert_called_with("[red]Failed to initialize local code search: DB error[/red]")

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='FAILED')
    def test_lazy_initialize_failed(self, mock_get_status):
        result = self.manager._lazy_initialize()
        self.assertFalse(result)
        self.assertIsNone(self.manager.collection)
        self.assertTrue(self.manager._is_initialized)

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='DOWNLOADING')
    def test_lazy_initialize_not_ready(self, mock_get_status):
        result = self.manager._lazy_initialize()
        self.assertFalse(result)
        self.assertFalse(self.manager._is_initialized)

    def test_calculate_hash(self):
        content = 'test content'
        expected_hash = '6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72'
        self.assertEqual(self.manager._calculate_hash(content), expected_hash)

    def test_check_file_status_unchanged(self):
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        stats = file_path.stat()
        old_index = {'file.py': {'hash': self.manager._calculate_hash('content'), 'mtime': stats.st_mtime, 'size': stats.st_size, 'refined': True}}
        status, meta = self.manager._check_file_status(file_path, old_index)
        self.assertEqual(status, 'unchanged')
        self.assertEqual(meta, old_index['file.py'])

    def test_check_file_status_needs_refinement(self):
        file_path = self.root_path / 'file.py'
        file_path.write_text('content')
        stats = file_path.stat()
        # Same content, but marked as not refined
        old_index = {'file.py': {'hash': self.manager._calculate_hash('content'), 'mtime': stats.st_mtime, 'size': stats.st_size, 'refined': False}}
        status, meta = self.manager._check_file_status(file_path, old_index)
        self.assertEqual(status, 'needs_refinement')
        self.assertEqual(meta, old_index['file.py'])

    def test_check_file_status_modified(self):
        file_path = self.root_path / 'file.py'
        file_path.write_text('new content')
        old_index = {'file.py': {'hash': self.manager._calculate_hash('old content'), 'mtime': 0, 'size': 0}}
        status, meta = self.manager._check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertIsNotNone(meta)
        self.assertEqual(meta['refined'], False)

    def test_check_file_status_old_format(self):
        file_path = self.root_path / 'file.py'
        file_path.write_text('new content')
        # Old format just stored the hash string
        old_index = {'file.py': self.manager._calculate_hash('old content')}
        status, meta = self.manager._check_file_status(file_path, old_index)
        self.assertEqual(status, 'modified')
        self.assertIsNotNone(meta)
        self.assertEqual(meta['hash'], self.manager._calculate_hash('new content'))

    def test_check_file_status_error(self):
        file_path = self.root_path / 'missing.py'
        old_index = {'missing.py': {'hash': 'hash'}}
        status, meta = self.manager._check_file_status(file_path, old_index)
        self.assertEqual(status, 'error')
        self.assertIsNone(meta)

    @patch('aye.model.index_manager.get_project_files')
    @patch('aye.model.vector_db.delete_from_index')
    def test_prepare_sync_with_changes(self, mock_delete, mock_get_files):
        # Setup
        self.manager.collection = MagicMock() # Pretend it's initialized
        file1 = self.root_path / 'file1.py'
        file1.write_text('content1')
        file2 = self.root_path / 'file2.py'
        file2.write_text('content2')
        file3_deleted = 'file3.py'
        mock_get_files.return_value = [file1, file2]
        
        # Old index with file1 unrefined, file2 missing, file3 deleted
        old_index = {
            'file1.py': {'hash': self.manager._calculate_hash('content1'), 'mtime': file1.stat().st_mtime, 'size': file1.stat().st_size, 'refined': False},
            file3_deleted: {'hash': 'somehash', 'mtime': 0, 'size': 0, 'refined': True}
        }
        self.hash_index_path.parent.mkdir()
        self.hash_index_path.write_text(json.dumps(old_index))
        
        self.manager.prepare_sync(verbose=True)

        self.assertEqual(self.manager._files_to_coarse_index, ['file2.py'])
        self.assertEqual(self.manager._files_to_refine, ['file1.py'])
        mock_delete.assert_called_once_with(self.manager.collection, [file3_deleted])

    def test_has_work(self):
        self.assertFalse(self.manager.has_work())
        self.manager._files_to_coarse_index = ['file.py']
        self.assertTrue(self.manager.has_work())
        self.manager._files_to_coarse_index = []
        self.manager._files_to_refine = ['file.py']
        self.assertTrue(self.manager.has_work())

    def test_is_indexing(self):
        self.assertFalse(self.manager.is_indexing())
        self.manager._is_indexing = True
        self.assertTrue(self.manager.is_indexing())
        self.manager._is_indexing = False
        self.manager._is_refining = True
        self.assertTrue(self.manager.is_indexing())

    def test_get_progress_display(self):
        self.manager._is_indexing = True
        self.manager._coarse_processed = 5
        self.manager._coarse_total = 10
        self.assertEqual(self.manager.get_progress_display(), 'indexing 5/10')
        
        self.manager._is_indexing = False
        self.manager._is_refining = True
        self.manager._refine_processed = 3
        self.manager._refine_total = 7
        self.assertEqual(self.manager.get_progress_display(), 'refining 3/7')
        
        self.manager._is_refining = False
        self.assertEqual(self.manager.get_progress_display(), '')

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.vector_db.initialize_index')
    @patch('aye.model.vector_db.query_index')
    def test_query_success(self, mock_query_index, mock_init, mock_status):
        mock_init.return_value = MagicMock()
        mock_query_index.return_value = [MagicMock()]
        results = self.manager.query('test query')
        self.assertEqual(len(results), 1)
        mock_query_index.assert_called_once()

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='DOWNLOADING')
    @patch('aye.model.index_manager.rprint')
    def test_query_not_ready(self, mock_rprint, mock_status):
        results = self.manager.query('query')
        self.assertEqual(results, [])
        mock_rprint.assert_called_with("[yellow]Code lookup is still initializing (downloading models)... Search is temporarily disabled.[/]")

    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='FAILED')
    def test_query_disabled(self, mock_status):
        self.manager._lazy_initialize() # This will set collection to None
        results = self.manager.query('query')
        self.assertEqual(results, [])

    @patch('aye.model.index_manager.DaemonThreadPoolExecutor', ImmediateExecutor)
    @patch('aye.model.index_manager.concurrent.futures.as_completed', lambda futures: futures)
    @patch('aye.model.index_manager.vector_db')
    @patch('aye.model.index_manager.onnx_manager.get_model_status', return_value='READY')
    @patch('aye.model.index_manager.time.sleep')
    def test_run_sync_in_background(self, mock_sleep, mock_status, mock_vector_db, *_):
        # Setup
        self.manager.collection = MagicMock()
        
        # Create files to be processed
        (self.root_path / 'coarse.py').write_text('coarse content')
        (self.root_path / 'refine.py').write_text('refine content')
        
        self.manager._files_to_coarse_index = ['coarse.py']
        self.manager._files_to_refine = ['refine.py']
        self.manager._target_index = {
            'coarse.py': {'hash': 'chash', 'mtime': 1, 'size': 1, 'refined': False},
            'refine.py': {'hash': 'rhash', 'mtime': 1, 'size': 1, 'refined': False}
        }
        self.manager._current_index_on_disk = {
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

    @patch('os.nice', side_effect=OSError)
    def test_set_low_priority_os_error(self, mock_nice):
        try:
            index_manager._set_low_priority()
        except OSError:
            self.fail("_set_low_priority() raised an unexpected OSError")
        mock_nice.assert_called_once_with(5)

    @patch('os.replace', side_effect=OSError("Permission denied"))
    def test_save_progress_error(self, mock_replace):
        self.manager.index_dir.mkdir()
        temp_path = self.hash_index_path.with_suffix('.json.tmp')
        self.manager._current_index_on_disk = {'file.py': 'data'}
        
        self.manager._save_progress()

        # Temp file should be created then cleaned up after error
        self.assertFalse(temp_path.exists())
        # The original file should not be created/modified
        self.assertFalse(self.hash_index_path.exists())

if __name__ == '__main__':
    unittest.main()
