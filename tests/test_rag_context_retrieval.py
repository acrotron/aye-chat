"""Tests for RAG context retrieval in large repositories.

Tests the fix for https://github.com/acrotron/aye-chat/issues/118
which reported that large repos (500+ files) code lookup does not
include correct files for context even when file paths are mentioned.
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

try:
    from aye.controller.llm_invoker import _get_rag_context_files, _determine_source_files
    from aye.model.models import VectorIndexResult
except ImportError:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from aye.controller.llm_invoker import _get_rag_context_files, _determine_source_files
    from aye.model.models import VectorIndexResult


class TestRagContextRetrieval(unittest.TestCase):
    """Test cases for RAG-based context retrieval."""

    def setUp(self):
        """Set up a temporary directory with test files."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Create test files
        self.config_py = self.root_path / "config.py"
        self.config_py.write_text("DATABASE_URL = 'postgresql://localhost/mydb'\nDEBUG = True\n")

        self.main_py = self.root_path / "main.py"
        self.main_py.write_text("from config import DATABASE_URL\n\ndef main():\n    print('Hello')\n")

        self.utils_py = self.root_path / "utils.py"
        self.utils_py.write_text("def helper():\n    return 42\n")

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def _create_mock_conf(self, index_manager=None):
        """Create a mock configuration object."""
        conf = MagicMock()
        conf.root = self.root_path
        conf.index_manager = index_manager
        conf.selected_model = "google/gemini-2.5-flash"
        conf.file_mask = "*.py"
        return conf

    def test_rag_query_returns_files_by_content_relevance(self):
        """RAG query should return files based on content relevance.

        This tests that when a user asks about a specific topic,
        the RAG system returns files with relevant content.
        """
        # Create mock index manager that returns relevant files
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = [
            VectorIndexResult(file_path="config.py", content="DATABASE_URL = ...", score=0.95),
            VectorIndexResult(file_path="main.py", content="from config import...", score=0.85),
        ]

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        # Query about database configuration
        result = _get_rag_context_files("How do I configure the database?", conf, verbose=False)

        # Should return the relevant files
        self.assertIn("config.py", result)
        self.assertIn("main.py", result)
        mock_index_manager.query.assert_called_once()

    def test_rag_query_with_explicit_file_name_in_prompt(self):
        """RAG query should find files when their names are mentioned in prompts.

        This is the core issue from #118 - when users mention specific file names,
        those files should be included in the context.
        """
        # Create mock index manager that returns the mentioned file
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = [
            VectorIndexResult(file_path="config.py", content="DATABASE_URL = ...", score=0.92),
        ]

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        # Query explicitly mentioning config.py
        result = _get_rag_context_files("Please update config.py to change the debug setting", conf, verbose=False)

        # The query should have been made with the prompt
        mock_index_manager.query.assert_called_once()
        call_args = mock_index_manager.query.call_args
        self.assertIn("config.py", call_args[0][0])  # Prompt should be passed to query

        # If the index returns config.py, it should be in the result
        self.assertIn("config.py", result)

    def test_rag_returns_empty_when_index_not_ready(self):
        """RAG should return empty dict when index manager is not available.

        This handles the case where the index is still building for large repos.
        """
        conf = self._create_mock_conf(index_manager=None)

        result = _get_rag_context_files("Update config.py", conf, verbose=False)

        self.assertEqual(result, {})

    def test_rag_respects_context_size_limits(self):
        """RAG should respect context size limits when accumulating files."""
        # Create mock index manager returning many files
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = [
            VectorIndexResult(file_path="config.py", content="...", score=0.95),
            VectorIndexResult(file_path="main.py", content="...", score=0.90),
            VectorIndexResult(file_path="utils.py", content="...", score=0.85),
        ]

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        result = _get_rag_context_files("Explain the codebase", conf, verbose=False)

        # Should return files (actual count depends on file sizes and limits)
        self.assertIsInstance(result, dict)

    def test_rag_handles_query_returning_no_results(self):
        """RAG should handle empty query results gracefully."""
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = []

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        result = _get_rag_context_files("Something completely unrelated", conf, verbose=False)

        self.assertEqual(result, {})

    def test_rag_deduplicates_files_from_multiple_chunks(self):
        """RAG should deduplicate files when multiple chunks from same file match."""
        mock_index_manager = MagicMock()
        # Same file appears multiple times (different chunks)
        mock_index_manager.query.return_value = [
            VectorIndexResult(file_path="config.py", content="chunk1", score=0.95),
            VectorIndexResult(file_path="config.py", content="chunk2", score=0.90),
            VectorIndexResult(file_path="config.py", content="chunk3", score=0.85),
            VectorIndexResult(file_path="main.py", content="main chunk", score=0.80),
        ]

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        result = _get_rag_context_files("Tell me about config", conf, verbose=False)

        # config.py should appear only once in the result
        config_count = list(result.keys()).count("config.py")
        self.assertEqual(config_count, 1)


class TestDetermineSourceFiles(unittest.TestCase):
    """Test cases for _determine_source_files function."""

    def setUp(self):
        """Set up a temporary directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Create test files
        (self.root_path / "test.py").write_text("print('test')")

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def _create_mock_conf(self, index_manager=None, use_rag=True):
        """Create a mock configuration object."""
        conf = MagicMock()
        conf.root = self.root_path
        conf.index_manager = index_manager
        conf.selected_model = "google/gemini-2.5-flash"
        conf.file_mask = "*.py"
        conf.use_rag = use_rag
        return conf

    def test_explicit_files_take_precedence(self):
        """Explicit source files should be used directly without RAG."""
        mock_index_manager = MagicMock()
        conf = self._create_mock_conf(index_manager=mock_index_manager)

        explicit_files = {"explicit.py": "explicit content"}
        result, use_all, prompt = _determine_source_files(
            "Update the file", conf, verbose=False, explicit_source_files=explicit_files
        )

        # Should use explicit files, not query RAG
        self.assertEqual(result, explicit_files)
        mock_index_manager.query.assert_not_called()

    def test_uses_rag_when_index_available(self):
        """Should use RAG when index manager is available."""
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = [
            VectorIndexResult(file_path="test.py", content="print('test')", score=0.9),
        ]

        conf = self._create_mock_conf(index_manager=mock_index_manager)

        result, use_all, prompt = _determine_source_files(
            "Explain test.py", conf, verbose=False, explicit_source_files=None
        )

        # Should have queried the index
        mock_index_manager.query.assert_called_once()

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_small_project_mode_includes_all_files(self, mock_collect):
        """In small project mode (use_rag=False), all files should be included."""
        mock_collect.return_value = {"test.py": "content"}
        conf = self._create_mock_conf(index_manager=None, use_rag=False)

        result, use_all, prompt = _determine_source_files(
            "Explain everything", conf, verbose=False, explicit_source_files=None
        )

        self.assertTrue(use_all)
        mock_collect.assert_called_once()

    def test_home_directory_returns_empty_context(self):
        """Should return empty context when in home directory."""
        conf = self._create_mock_conf()
        conf.root = Path.home()

        result, use_all, prompt = _determine_source_files(
            "Do something", conf, verbose=False, explicit_source_files=None
        )

        self.assertEqual(result, {})

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_all_command_includes_all_files(self, mock_collect):
        """The /all command should include all project files."""
        mock_collect.return_value = {"test.py": "content", "other.py": "other"}
        conf = self._create_mock_conf(index_manager=MagicMock())

        result, use_all, prompt = _determine_source_files(
            "/all explain everything", conf, verbose=False, explicit_source_files=None
        )

        self.assertTrue(use_all)
        self.assertEqual(prompt, "explain everything")
        mock_collect.assert_called_once()


class TestLargeRepoScenario(unittest.TestCase):
    """Integration-style tests simulating the issue #118 scenario.

    These tests simulate the behavior of a large repository (500+ files)
    to verify that files mentioned in prompts are correctly retrieved.
    """

    def setUp(self):
        """Set up a simulated large repository."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Create the target file that users would ask about
        self.target_file = self.root_path / "src" / "config.py"
        self.target_file.parent.mkdir(parents=True, exist_ok=True)
        self.target_file.write_text(
            "# Configuration settings\n"
            "DATABASE_URL = 'postgresql://localhost/mydb'\n"
            "DEBUG = True\n"
            "SECRET_KEY = 'change-me-in-production'\n"
        )

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def _create_mock_conf(self, query_results):
        """Create a mock configuration simulating a large repo with RAG."""
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = query_results

        conf = MagicMock()
        conf.root = self.root_path
        conf.index_manager = mock_index_manager
        conf.selected_model = "google/gemini-2.5-flash"
        conf.file_mask = "*.py"
        conf.use_rag = True
        return conf

    def test_file_mentioned_in_prompt_is_retrieved(self):
        """When a file is mentioned by name, it should be in the RAG results.

        This is the core test for issue #118. When a user says
        "update src/config.py", the file should be included in context.
        """
        # Simulate RAG returning the mentioned file
        query_results = [
            VectorIndexResult(
                file_path="src/config.py",
                content="DATABASE_URL = ...",
                score=0.93
            ),
        ]

        conf = self._create_mock_conf(query_results)

        # User prompt explicitly mentions the file path
        prompt = "Please update src/config.py to set DEBUG = False"
        result = _get_rag_context_files(prompt, conf, verbose=False)

        # The file should be in the context
        self.assertIn("src/config.py", result)

        # Verify the query was made with the prompt
        conf.index_manager.query.assert_called_once()
        call_args = conf.index_manager.query.call_args
        self.assertIn("src/config.py", call_args[0][0])

    def test_file_retrieved_even_with_many_other_results(self):
        """The mentioned file should be retrieved even among many results.

        In large repos, the RAG might return many files. The mentioned
        file should still be included if it has reasonable relevance.
        """
        # Simulate RAG returning multiple files with the mentioned one
        query_results = [
            VectorIndexResult(file_path="src/database.py", content="...", score=0.95),
            VectorIndexResult(file_path="src/config.py", content="...", score=0.92),
            VectorIndexResult(file_path="src/settings.py", content="...", score=0.90),
            VectorIndexResult(file_path="src/constants.py", content="...", score=0.88),
            VectorIndexResult(file_path="src/utils.py", content="...", score=0.85),
        ]

        conf = self._create_mock_conf(query_results)

        prompt = "Update src/config.py to change database settings"
        result = _get_rag_context_files(prompt, conf, verbose=False)

        # config.py should be in results
        self.assertIn("src/config.py", result)

    def test_workaround_explicit_files_always_work(self):
        """Explicit file specification should always include the file.

        This tests the workaround for issue #118: using @file or with command
        to explicitly specify files.
        """
        conf = MagicMock()
        conf.root = self.root_path
        conf.index_manager = MagicMock()
        conf.selected_model = "google/gemini-2.5-flash"

        # User explicitly specifies the file
        explicit_files = {"src/config.py": self.target_file.read_text()}

        result, use_all, prompt = _determine_source_files(
            "Set DEBUG = False",
            conf,
            verbose=False,
            explicit_source_files=explicit_files
        )

        # Explicit files should be returned directly
        self.assertIn("src/config.py", result)
        # RAG should not be queried
        conf.index_manager.query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
