import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

import aye.model.vector_db as vector_db

class TestVectorDb(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch('chromadb.PersistentClient')
    @patch('aye.model.vector_db.ONNXMiniLM_L6_V2')
    def test_initialize_index_success(self, mock_onnx, mock_client):
        mock_collection = MagicMock()
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        collection = vector_db.initialize_index(self.root_path)
        self.assertEqual(collection, mock_collection)
        mock_client.assert_called_once_with(path=str(self.root_path / '.aye' / 'chroma_db'))
        mock_onnx.assert_called_once()

    @patch('chromadb.PersistentClient')
    @patch('aye.model.vector_db.ONNXMiniLM_L6_V2')
    def test_initialize_index_embedding_error(self, mock_onnx, mock_client):
        mock_onnx.side_effect = Exception('Embedding error')
        with self.assertRaises(Exception):
            vector_db.initialize_index(self.root_path)

    def test_chunk_file(self):
        content = 'line1\nline2\nline3\nline4\nline5'
        chunks = vector_db._chunk_file(content, chunk_size=2, overlap=1)
        expected = ['line1\nline2', 'line2\nline3', 'line3\nline4', 'line4\nline5', 'line5']
        self.assertEqual(chunks, expected)

    def test_chunk_file_empty(self):
        chunks = vector_db._chunk_file('')
        self.assertEqual(chunks, [])

    @patch('aye.model.vector_db.ast_chunker')
    def test_update_index_coarse(self, mock_ast_chunker):
        mock_collection = MagicMock()
        files_to_update = {'file1.py': 'content1', 'file2.py': 'content2'}
        vector_db.update_index_coarse(mock_collection, files_to_update)
        mock_collection.upsert.assert_called_once_with(
            ids=['file1.py', 'file2.py'],
            documents=['content1', 'content2'],
            metadatas=[{'file_path': 'file1.py'}, {'file_path': 'file2.py'}]
        )

    def test_update_index_coarse_empty(self):
        mock_collection = MagicMock()
        vector_db.update_index_coarse(mock_collection, {})
        mock_collection.upsert.assert_not_called()

    @patch('aye.model.vector_db.ast_chunker')
    def test_refine_file_in_index_with_chunks(self, mock_ast_chunker):
        mock_collection = MagicMock()
        mock_ast_chunker.return_value = ['chunk1', 'chunk2']
        vector_db.refine_file_in_index(mock_collection, 'file.py', 'content')
        mock_collection.delete.assert_called_once_with(ids=['file.py'])
        mock_collection.upsert.assert_called_once()
        call_args = mock_collection.upsert.call_args
        self.assertEqual(call_args[1]['documents'], ['chunk1', 'chunk2'])
        self.assertEqual(call_args[1]['ids'], ['file.py:0', 'file.py:1'])

    @patch('aye.model.vector_db.ast_chunker', return_value=[])
    def test_refine_file_in_index_fallback_chunking(self, mock_ast_chunker):
        mock_collection = MagicMock()
        vector_db.refine_file_in_index(mock_collection, 'file.py', 'line1\nline2')
        mock_collection.delete.assert_called_once_with(ids=['file.py'])
        mock_collection.upsert.assert_called_once()
        # Should use _chunk_file fallback

    def test_refine_file_in_index_no_chunks(self):
        mock_collection = MagicMock()
        with patch('aye.model.vector_db.ast_chunker', return_value=[]):
            with patch('aye.model.vector_db._chunk_file', return_value=[]):
                vector_db.refine_file_in_index(mock_collection, 'file.py', '')
                mock_collection.delete.assert_called_once_with(ids=['file.py'])
                mock_collection.upsert.assert_not_called()

    def test_delete_from_index(self):
        mock_collection = MagicMock()
        deleted_files = ['file1.py', 'file2.py']
        vector_db.delete_from_index(mock_collection, deleted_files)
        mock_collection.delete.assert_called_once_with(where={'file_path': {'$in': deleted_files}})

    def test_delete_from_index_empty(self):
        mock_collection = MagicMock()
        vector_db.delete_from_index(mock_collection, [])
        mock_collection.delete.assert_not_called()

    def test_query_index_success(self):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [['id1', 'id2']],
            'documents': [['doc1', 'doc2']],
            'metadatas': [[{'file_path': 'file1.py'}, {'file_path': 'file2.py'}]],
            'distances': [[0.1, 0.2]]
        }
        results = vector_db.query_index(mock_collection, 'query', n_results=10, min_relevance=0.0)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].file_path, 'file1.py')
        self.assertAlmostEqual(results[0].score, 0.9, places=1)

    def test_query_index_no_results(self):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]]}
        results = vector_db.query_index(mock_collection, '', n_results=10)
        self.assertEqual(results, [])

    def test_query_index_with_min_relevance_filter(self):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [['id1', 'id2']],
            'documents': [['doc1', 'doc2']],
            'metadatas': [[{'file_path': 'file1.py'}, {'file_path': 'file2.py'}]],
            'distances': [[0.1, 0.8]]  # Scores: 0.9 and 0.2
        }
        results = vector_db.query_index(mock_collection, 'query', min_relevance=0.5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].score, 0.9)

    def test_query_index_fallback_when_filtered_empty(self):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [['id1', 'id2', 'id3']],
            'documents': [['doc1', 'doc2', 'doc3']],
            'metadatas': [[{'file_path': 'f1'}, {'file_path': 'f2'}, {'file_path': 'f3'}]],
            'distances': [[0.1, 0.2, 0.3]]  # All scores > 0.5, but filter at 0.95
        }
        results = vector_db.query_index(mock_collection, 'query', min_relevance=0.95)
        self.assertEqual(len(results), 3)  # Fallback to top 10

if __name__ == '__main__':
    unittest.main()