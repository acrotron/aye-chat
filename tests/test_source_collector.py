import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from aye.model.source_collector import collect_sources, _is_hidden, _load_patterns_from_file, driver

class TestSourceCollector(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        
        # Create a directory structure for testing
        (self.root / "file1.py").write_text("python content")
        (self.root / "file2.txt").write_text("text content")
        (self.root / "image.jpg").write_text("binary", encoding="latin-1") # Not a real image
        
        # Hidden file
        (self.root / ".hidden_file.py").write_text("hidden")
        
        # Subdirectory
        self.subdir = self.root / "subdir"
        self.subdir.mkdir()
        (self.subdir / "sub_file.py").write_text("sub python")
        (self.subdir / "another.txt").write_text("sub text")
        
        # Hidden subdirectory
        self.hidden_subdir = self.root / ".venv"
        self.hidden_subdir.mkdir()
        (self.hidden_subdir / "ignored.py").write_text("should be ignored")
        
        # .gitignore file
        (self.root / ".gitignore").write_text("*.txt\nignored_dir/\n")
        
        # .ayeignore file
        (self.root / ".ayeignore").write_text("subdir/another.txt\n")
        
        # Ignored directory
        self.ignored_dir = self.root / "ignored_dir"
        self.ignored_dir.mkdir()
        (self.ignored_dir / "ignored.py").write_text("should be ignored")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_is_hidden(self):
        self.assertTrue(_is_hidden(Path("foo/.hidden/file.txt")))
        self.assertTrue(_is_hidden(Path(".hidden/file.txt")))
        self.assertFalse(_is_hidden(Path("normal/file.txt")))
        self.assertTrue(_is_hidden(Path("foo/bar/.git/config")))
        self.assertFalse(_is_hidden(Path("foo/bar/baz")))

    def test_collect_sources_single_mask_recursive(self):
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py")
        
        self.assertIn("file1.py", sources)
        self.assertIn(str(Path("subdir") / "sub_file.py"), sources)
        
        # Check ignored files
        self.assertNotIn(".hidden_file.py", sources)
        self.assertNotIn(str(Path(".venv") / "ignored.py"), sources)
        self.assertNotIn(str(Path("ignored_dir") / "ignored.py"), sources)
        
        # Check other extensions not included
        self.assertNotIn("file2.txt", sources)
        self.assertNotIn("image.jpg", sources)
        
        self.assertEqual(len(sources), 2)

    def test_collect_sources_multiple_masks(self):
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py, *.txt")
        
        self.assertIn("file1.py", sources)
        self.assertIn(str(Path("subdir") / "sub_file.py"), sources)
        
        # .txt files are in .gitignore, so they should be excluded
        self.assertNotIn("file2.txt", sources)
        # subdir/another.txt is in .ayeignore
        self.assertNotIn(str(Path("subdir") / "another.txt"), sources)
        
        self.assertEqual(len(sources), 2)

    def test_collect_sources_non_recursive(self):
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py", recursive=False)
        
        self.assertIn("file1.py", sources)
        self.assertNotIn(str(Path("subdir") / "sub_file.py"), sources)
        self.assertEqual(len(sources), 1)

    def test_collect_sources_invalid_dir(self):
        with self.assertRaises(NotADirectoryError):
            collect_sources(root_dir="non_existent_dir")

    def test_collect_sources_from_parent_gitignore(self):
        # Test that .gitignore in parent directories is respected
        deeper_dir = self.subdir / "deeper"
        deeper_dir.mkdir()
        (deeper_dir / "deep_file.txt").write_text("deep text")
        
        # This should be ignored by the .gitignore in the root
        sources = collect_sources(root_dir=str(deeper_dir), file_mask="*.txt")
        self.assertEqual(len(sources), 0)

    def test_collect_sources_with_non_utf8_file(self):
        # Create a file with non-utf8 content
        non_utf8_file = self.root / "non_utf8.py"
        non_utf8_file.write_bytes(b'\x80abc') # Invalid start byte for UTF-8

        with patch('builtins.print') as mock_print:
            sources = collect_sources(root_dir=str(self.root), file_mask="*.py")
            
            # The non-utf8 file should be skipped
            self.assertNotIn("non_utf8.py", sources)
            # Check that a warning was printed
            mock_print.assert_any_call(f"   Skipping non‑UTF8 file: {non_utf8_file}")

    def test_load_patterns_from_unreadable_file(self):
        unreadable_ignore = self.root / ".gitignore"
        # Don't write it, just patch read_text to fail
        with patch.object(Path, 'read_text', side_effect=IOError("Permission denied")):
            patterns = _load_patterns_from_file(unreadable_ignore)
            self.assertEqual(patterns, [])

    @patch('builtins.print')
    @patch('aye.model.source_collector.collect_sources')
    def test_driver(self, mock_collect, mock_print):
        mock_collect.return_value = {"file1.py": "content"}
        driver()
        mock_collect.assert_called_once()
        self.assertIn("Collected .py files:", mock_print.call_args_list[0][0][0])
        self.assertIn("--- file1.py ---", mock_print.call_args_list[1][0][0])
