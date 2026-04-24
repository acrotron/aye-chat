"""Integration tests for hybrid retrieval (filename boost + BM25) via IndexManager.query.

These tests exercise the full path: IndexManager.query delegates to
vector_db.query_index (mocked), reads the indexed files from disk to build a
BM25 index, applies filename boost against the known file paths, and fuses
the rankings before returning results.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import aye.model.index_manager.index_manager as index_manager
    from aye.model.models import VectorIndexResult
except ImportError:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    import aye.model.index_manager.index_manager as index_manager
    from aye.model.models import VectorIndexResult


class TestIndexManagerHybridRetrieval(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        (self.root_path / "auth.py").write_text(
            "def authenticate_user(name):\n    return name\n"
        )
        (self.root_path / "config.py").write_text(
            "DATABASE_URL = 'postgres://localhost/db'\nDEBUG = True\n"
        )
        (self.root_path / "utils.py").write_text(
            "def helper():\n    return 42\n"
        )
        (self.root_path / "rare_tokens.py").write_text(
            "SPECIAL_MARKER_ZXQQYYY = 'only here'\n"
        )

        self.manager = index_manager.IndexManager(
            self.root_path, "*.py", verbose=False
        )
        # Populate target_index so the hybrid layer knows which files exist.
        self.manager._state.target_index = {
            "auth.py": "hash",
            "config.py": "hash",
            "utils.py": "hash",
            "rare_tokens.py": "hash",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _setup_ready_collection(self):
        """Put the init coordinator into a ready state without touching real ChromaDB."""
        self.manager._init_coordinator._is_initialized = True
        self.manager._init_coordinator.collection = object()

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_filename_mention_promotes_file_to_top(self, mock_query, _mock_status):
        """When the user mentions a filename, it ranks first even if vector buries it."""
        self._setup_ready_collection()
        # Vector retrieval puts auth.py at the bottom.
        mock_query.return_value = [
            VectorIndexResult(file_path="config.py", content="cfg", score=0.95),
            VectorIndexResult(file_path="utils.py", content="util", score=0.90),
            VectorIndexResult(file_path="auth.py", content="auth", score=0.40),
        ]

        results = self.manager.query("fix the bug in auth.py")

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].file_path, "auth.py")

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_bm25_surfaces_file_with_rare_token(self, mock_query, _mock_status):
        """A query for a rare term pulls in the file containing it even if vector misses it."""
        self._setup_ready_collection()
        # Vector returns unrelated files.
        mock_query.return_value = [
            VectorIndexResult(file_path="utils.py", content="util", score=0.50),
        ]

        results = self.manager.query("SPECIAL_MARKER_ZXQQYYY")

        paths = [r.file_path for r in results]
        self.assertIn("rare_tokens.py", paths)

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_bm25_cache_reused_across_queries(self, mock_query, _mock_status):
        """The BM25 index should be cached when the file set is unchanged."""
        self._setup_ready_collection()
        mock_query.return_value = []

        self.manager.query("first query")
        first_bm25 = self.manager._bm25_cache
        self.assertIsNotNone(first_bm25)

        self.manager.query("second query")
        self.assertIs(self.manager._bm25_cache, first_bm25)

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_bm25_cache_invalidated_when_file_set_changes(
        self, mock_query, _mock_status
    ):
        """Adding a file to target_index should force a BM25 rebuild."""
        self._setup_ready_collection()
        mock_query.return_value = []

        self.manager.query("first")
        first_bm25 = self.manager._bm25_cache

        (self.root_path / "new_file.py").write_text("print('x')\n")
        self.manager._state.target_index["new_file.py"] = "hash"

        self.manager.query("second")
        self.assertIsNot(self.manager._bm25_cache, first_bm25)

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_query_without_indexed_files_passes_through(
        self, mock_query, _mock_status
    ):
        """With an empty target_index, hybrid rerank is a no-op (preserves vector order)."""
        self._setup_ready_collection()
        self.manager._state.target_index = {}

        mock_query.return_value = [
            VectorIndexResult(file_path="a.py", content="", score=0.9),
            VectorIndexResult(file_path="b.py", content="", score=0.5),
        ]

        results = self.manager.query("anything")
        self.assertEqual([r.file_path for r in results], ["a.py", "b.py"])

    @patch(
        "aye.model.index_manager.index_manager.onnx_manager.get_model_status",
        return_value="READY",
    )
    @patch("aye.model.vector_db.query_index")
    def test_bm25_skips_unreadable_files(self, mock_query, _mock_status):
        """Files that can't be read should be skipped silently when building BM25."""
        self._setup_ready_collection()
        mock_query.return_value = []
        # Reference a file that doesn't exist on disk.
        self.manager._state.target_index["nonexistent.py"] = "hash"

        # Should not raise.
        results = self.manager.query("config")
        # The real files should still be indexed and searchable.
        self.assertIsNotNone(self.manager._bm25_cache)
        self.assertIn(
            "config.py", [r.file_path for r in results] + list(self.manager._bm25_cache.doc_ids)
        )


if __name__ == "__main__":
    unittest.main()
