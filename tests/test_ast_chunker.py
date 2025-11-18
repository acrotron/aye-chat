import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

import aye.model.ast_chunker as ast_chunker

class TestAstChunker(unittest.TestCase):
    def test_get_language_from_file_path(self):
        self.assertEqual(ast_chunker.get_language_from_file_path('file.py'), 'python')
        self.assertEqual(ast_chunker.get_language_from_file_path('file.js'), 'javascript')
        self.assertEqual(ast_chunker.get_language_from_file_path('file.java'), 'java')
        self.assertEqual(ast_chunker.get_language_from_file_path('file.txt'), None)
        self.assertEqual(ast_chunker.get_language_from_file_path('file'), None)

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    @patch('aye.model.ast_chunker.get_language')
    def test_ast_chunker_python_success(self, mock_get_language, mock_get_parser):
        mock_parser = MagicMock()
        mock_get_parser.return_value = mock_parser
        mock_tree = MagicMock()
        mock_parser.parse.return_value = mock_tree
        mock_language = MagicMock()
        mock_get_language.return_value = mock_language
        mock_query = MagicMock()
        mock_language.query.return_value = mock_query
        mock_node = MagicMock()
        mock_node.text = b'def func(): pass'
        mock_query.captures.return_value = [(mock_node, 'chunk')]
        chunks = ast_chunker.ast_chunker('def func(): pass', 'python')
        self.assertEqual(len(chunks), 1)
        self.assertIn('def func(): pass', chunks)

    def test_ast_chunker_unsupported_language(self):
        chunks = ast_chunker.ast_chunker('code', 'unsupported')
        self.assertEqual(chunks, [])

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    @patch('aye.model.ast_chunker.get_language')
    def test_ast_chunker_parsing_error(self, mock_get_language, mock_get_parser):
        mock_get_parser.side_effect = Exception('Parse error')
        chunks = ast_chunker.ast_chunker('code', 'python')
        self.assertEqual(chunks, [])

    def test_ast_chunker_empty_content(self):
        chunks = ast_chunker.ast_chunker('', 'python')
        self.assertEqual(chunks, [])

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    @patch('aye.model.ast_chunker.get_language')
    def test_ast_chunker_no_captures(self, mock_get_language, mock_get_parser):
        mock_parser = MagicMock()
        mock_get_parser.return_value = mock_parser
        mock_tree = MagicMock()
        mock_parser.parse.return_value = mock_tree
        mock_language = MagicMock()
        mock_get_language.return_value = mock_language
        mock_query = MagicMock()
        mock_language.query.return_value = mock_query
        mock_query.captures.return_value = []
        chunks = ast_chunker.ast_chunker('no functions', 'python')
        self.assertEqual(chunks, ['no functions'])

if __name__ == '__main__':
    unittest.main()
