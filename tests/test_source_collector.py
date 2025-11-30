import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

from aye.model.source_collector import (
    collect_sources,
    get_project_files,
    get_project_files_with_limit,
    _load_ignore_patterns
)


class TestSourceCollector(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        # On Windows, tempfile.TemporaryDirectory() can return a short 8.3 path.
        # Path.glob() later returns full paths. To make relative_to work,
        # we need to resolve the root path to its canonical form.
        self.root = Path(self.tmpdir.name).resolve()
        
        # Create a directory structure for testing
        (self.root / "file1.py").write_text("python content")
        (self.root / "file2.txt").write_text("text content")
        (self.root / "image.jpg").write_text("binary", encoding="latin-1")
        
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

    def test_collect_sources_single_mask(self):
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py")

        self.assertIn("file1.py", sources)
        self.assertIn("subdir/sub_file.py", sources)

        # Check ignored files
        self.assertNotIn(".hidden_file.py", sources)
        self.assertNotIn(".venv/ignored.py", sources)
        self.assertNotIn("ignored_dir/ignored.py", sources)

        # Check other extensions not included
        self.assertNotIn("file2.txt", sources)
        self.assertNotIn("image.jpg", sources)

        self.assertEqual(len(sources), 2)

    def test_collect_sources_multiple_masks(self):
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py, *.txt")

        self.assertIn("file1.py", sources)
        self.assertIn("subdir/sub_file.py", sources)

        # .txt files are in .gitignore, so they should be excluded
        self.assertNotIn("file2.txt", sources)
        # subdir/another.txt is in .ayeignore
        self.assertNotIn("subdir/another.txt", sources)

        self.assertEqual(len(sources), 2)

    def test_collect_sources_invalid_dir(self):
        sources = collect_sources(root_dir="non_existent_dir", file_mask="*.py")
        self.assertEqual(sources, {})

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
        non_utf8_file.write_bytes(b'\x80abc')

        sources = collect_sources(root_dir=str(self.root), file_mask="*.py")
        
        # The non-utf8 file should be skipped
        self.assertNotIn("non_utf8.py", sources)

    def test_get_project_files(self):
        files = get_project_files(root_dir=str(self.root), file_mask="*.py")
        
        file_names = [f.relative_to(self.root).as_posix() for f in files]
        
        self.assertIn("file1.py", file_names)
        self.assertIn("subdir/sub_file.py", file_names)
        self.assertNotIn(".hidden_file.py", file_names)
        self.assertEqual(len(files), 2)

    def test_get_project_files_invalid_dir(self):
        files = get_project_files(root_dir="non_existent_dir", file_mask="*.py")
        self.assertEqual(files, [])

    def test_get_project_files_with_limit_not_hit(self):
        files, limit_hit = get_project_files_with_limit(
            root_dir=str(self.root),
            file_mask="*.py",
            limit=10
        )
        
        self.assertFalse(limit_hit)
        self.assertEqual(len(files), 2)

    def test_get_project_files_with_limit_hit(self):
        files, limit_hit = get_project_files_with_limit(
            root_dir=str(self.root),
            file_mask="*.py",
            limit=1
        )
        
        self.assertTrue(limit_hit)
        self.assertEqual(len(files), 1)

    def test_load_ignore_patterns(self):
        spec = _load_ignore_patterns(self.root)
        
        # Test that patterns from .gitignore are loaded
        self.assertTrue(spec.match_file("test.txt"))
        self.assertTrue(spec.match_file("ignored_dir/file.py"))
        
        # Test that patterns from .ayeignore are loaded
        self.assertTrue(spec.match_file("subdir/another.txt"))

    def test_load_ignore_patterns_with_unreadable_file(self):
        # Create a .gitignore file
        gitignore = self.root / ".gitignore"
        gitignore.write_text("*.txt")
        
        # Mock the open function to raise an exception
        with patch('builtins.open', side_effect=IOError("Permission denied")):
            # Should not raise, just skip the unreadable file
            spec = _load_ignore_patterns(self.root)
            # Default patterns should still be loaded
            self.assertIsNotNone(spec)

    def test_hidden_directories_excluded(self):
        # Files in hidden directories should be excluded
        sources = collect_sources(root_dir=str(self.root), file_mask="*.py")
        
        self.assertNotIn(".venv/ignored.py", sources)
        self.assertNotIn(".hidden_file.py", sources)

    def test_collect_sources_handles_read_errors(self):
        # Create a file that will fail to read
        test_file = self.root / "test_error.py"
        test_file.write_text("content")
        
        # Mock read_text to raise an exception
        with patch.object(Path, 'read_text', side_effect=Exception("Read error")):
            sources = collect_sources(root_dir=str(self.root), file_mask="*.py")
            # Should return empty dict since all reads fail
            self.assertEqual(sources, {})
