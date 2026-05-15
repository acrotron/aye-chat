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


class TestExtractSymbols(unittest.TestCase):
    def test_returns_empty_when_tree_sitter_unavailable(self):
        with patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', False):
            self.assertEqual(ast_chunker.extract_symbols('def foo(): pass', 'python'), [])

    def test_returns_empty_for_unsupported_language(self):
        self.assertEqual(ast_chunker.extract_symbols('code', 'unsupported'), [])

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    @patch('aye.model.ast_chunker.get_language')
    def test_extracts_name_field_from_captured_nodes(self, mock_get_language, mock_get_parser):
        mock_get_parser.return_value = MagicMock()
        mock_get_parser.return_value.parse.return_value = MagicMock()
        mock_language = MagicMock()
        mock_get_language.return_value = mock_language
        mock_query = MagicMock()
        mock_language.query.return_value = mock_query

        def _make_node(name_bytes):
            node = MagicMock()
            name_node = MagicMock()
            name_node.text = name_bytes
            node.child_by_field_name.return_value = name_node
            return node

        mock_query.captures.return_value = [
            (_make_node(b'authenticate_user'), 'chunk'),
            (_make_node(b'AuthService'), 'chunk'),
        ]
        symbols = ast_chunker.extract_symbols('source', 'python')
        self.assertEqual(symbols, ['authenticate_user', 'AuthService'])

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    @patch('aye.model.ast_chunker.get_language')
    def test_skips_nodes_without_name_field(self, mock_get_language, mock_get_parser):
        mock_get_parser.return_value = MagicMock()
        mock_get_parser.return_value.parse.return_value = MagicMock()
        mock_language = MagicMock()
        mock_get_language.return_value = mock_language
        mock_query = MagicMock()
        mock_language.query.return_value = mock_query

        anonymous = MagicMock()
        anonymous.child_by_field_name.return_value = None
        named = MagicMock()
        name_node = MagicMock()
        name_node.text = b'named_fn'
        named.child_by_field_name.return_value = name_node

        mock_query.captures.return_value = [(anonymous, 'chunk'), (named, 'chunk')]
        symbols = ast_chunker.extract_symbols('source', 'python')
        self.assertEqual(symbols, ['named_fn'])

    @patch('aye.model.ast_chunker.TREE_SITTER_AVAILABLE', True)
    @patch('aye.model.ast_chunker.get_parser')
    def test_returns_empty_on_parse_failure(self, mock_get_parser):
        mock_get_parser.side_effect = Exception('boom')
        self.assertEqual(ast_chunker.extract_symbols('source', 'python'), [])


if __name__ == '__main__':
    unittest.main()
