import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from aye.model.file_processor import make_paths_relative, filter_unchanged_files

class TestFileProcessor(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name).resolve()
        
        # Create some files for testing filter_unchanged_files
        self.file1 = self.root / "file1.txt"
        self.file1.write_text("original content")
        
        self.subdir = self.root / "subdir"
        self.subdir.mkdir()
        self.file2 = self.subdir / "file2.py"
        self.file2.write_text("def func(): pass")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_make_paths_relative_absolute_path(self):
        """Test converting absolute paths under root to relative."""
        files = [
            {"file_name": str(self.root / "file1.txt")},
            {"file_name": str(self.root / "subdir" / "file2.py")},
        ]
        
        result = make_paths_relative(files, self.root)
        
        self.assertEqual(result[0]["file_name"], "file1.txt")
        self.assertEqual(result[1]["file_name"], str(Path("subdir") / "file2.py"))

    def test_make_paths_relative_absolute_outside_root(self):
        """Test absolute paths outside root are unchanged."""
        files = [
            {"file_name": "/some/other/path/file3.txt"},
        ]
        
        result = make_paths_relative(files, self.root)
        
        self.assertEqual(result[0]["file_name"], "/some/other/path/file3.txt")

    def test_make_paths_relative_already_relative(self):
        """Test relative paths are normalized."""
        files = [
            {"file_name": "relative/path.txt"},
            {"file_name": "./src/../src/file.py"},
        ]
        
        result = make_paths_relative(files, self.root)
        
        # Relative paths are resolved against root and made relative again
        self.assertEqual(result[0]["file_name"], str(Path("relative/path.txt")))
        self.assertEqual(result[1]["file_name"], str(Path("src/file.py")))

    def test_make_paths_relative_no_file_name(self):
        """Test items without file_name are unchanged."""
        files = [{"no_file_name": "some_value"}]
        
        result = make_paths_relative(files, self.root)
        
        self.assertNotIn("file_name", result[0])

    def test_make_paths_relative_mixed(self):
        """Test mixed absolute and relative paths."""
        files = [
            {"file_name": str(self.root / "file1.txt")},  # Absolute under root
            {"file_name": "subdir/file2.py"},  # Relative
            {"file_name": "/outside/path.txt"},  # Absolute outside root
        ]
        
        result = make_paths_relative(files, self.root)
        
        self.assertEqual(result[0]["file_name"], "file1.txt")
        self.assertEqual(result[1]["file_name"], str(Path("subdir/file2.py")))
        self.assertEqual(result[2]["file_name"], "/outside/path.txt")

    def test_filter_unchanged_files_with_root(self):
        """Test filter_unchanged_files with root parameter."""
        updated_files = [
            # File with modified content (relative path)
            {"file_name": "file1.txt", "file_content": "new content"},
            # File with same content (relative path)
            {"file_name": "subdir/file2.py", "file_content": "def func(): pass"},
            # New file (relative path)
            {"file_name": "new_file.txt", "file_content": "I am new"},
        ]
        
        changed = filter_unchanged_files(updated_files, root=self.root)
        
        self.assertEqual(len(changed), 2)
        changed_names = {item["file_name"] for item in changed}
        self.assertIn("file1.txt", changed_names)
        self.assertIn("new_file.txt", changed_names)
        self.assertNotIn("subdir/file2.py", changed_names)

    def test_filter_unchanged_files_without_root(self):
        """Test filter_unchanged_files without root (backward compatibility)."""
        updated_files = [
            # File with modified content (absolute path)
            {"file_name": str(self.file1), "file_content": "new content"},
            # File with same content (absolute path)
            {"file_name": str(self.file2), "file_content": "def func(): pass"},
            # New file (absolute path)
            {"file_name": str(self.root / "new_file.txt"), "file_content": "I am new"},
        ]
        
        changed = filter_unchanged_files(updated_files)
        
        self.assertEqual(len(changed), 2)
        changed_names = {item["file_name"] for item in changed}
        self.assertIn(str(self.file1), changed_names)
        self.assertIn(str(self.root / "new_file.txt"), changed_names)
        self.assertNotIn(str(self.file2), changed_names)

    def test_filter_unchanged_files_missing_keys(self):
        """Test files with missing keys are skipped."""
        updated_files = [
            {"file_name": "missing_content.txt"},
            {"file_content": "missing_name"},
        ]
        
        changed = filter_unchanged_files(updated_files, root=self.root)
        
        self.assertEqual(len(changed), 0)

    def test_filter_unchanged_files_read_error(self):
        """Test files that can't be read are included for update."""
        updated_files = [
            {"file_name": str(self.file1), "file_content": "new content"}
        ]
        
        with patch('pathlib.Path.read_text', side_effect=IOError("Can't read")):
            changed = filter_unchanged_files(updated_files)
            
            # If the original can't be read, it should be included for update
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]['file_name'], str(self.file1))
