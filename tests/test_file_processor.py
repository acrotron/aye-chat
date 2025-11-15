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

    def test_make_paths_relative(self):
        files = [
            {"file_name": str(self.root / "file1.txt")},
            {"file_name": str(self.root / "subdir" / "file2.py")},
            {"file_name": "/some/other/path/file3.txt"}, # Absolute path not under root
            {"file_name": "relative/path.txt"}, # Already relative
            {"no_file_name": "some_value"} # Item without file_name
        ]
        
        result = make_paths_relative(files, self.root)
        
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]["file_name"], "file1.txt")
        self.assertEqual(result[1]["file_name"], str(Path("subdir") / "file2.py"))
        self.assertEqual(result[2]["file_name"], "/some/other/path/file3.txt") # Unchanged
        self.assertEqual(result[3]["file_name"], "relative/path.txt") # Unchanged if not under root
        self.assertNotIn("file_name", result[4])

    def test_filter_unchanged_files(self):
        updated_files = [
            # File with modified content
            {"file_name": str(self.file1), "file_content": "new content"},
            # File with same content
            {"file_name": str(self.file2), "file_content": "def func(): pass"},
            # New file
            {"file_name": str(self.root / "new_file.txt"), "file_content": "I am new"},
            # Item with missing key
            {"file_name": "missing_content.txt"},
            # Item with non-existent path but no content
            {"file_name": "non_existent.txt"}
        ]
        
        changed = filter_unchanged_files(updated_files)
        
        self.assertEqual(len(changed), 2)
        changed_names = {item["file_name"] for item in changed}
        self.assertIn(str(self.file1), changed_names)
        self.assertIn(str(self.root / "new_file.txt"), changed_names)
        self.assertNotIn(str(self.file2), changed_names)

    def test_filter_unchanged_files_read_error(self):
        updated_files = [
            {"file_name": str(self.file1), "file_content": "new content"}
        ]
        
        # Simulate an error when reading the original file
        with patch('pathlib.Path.read_text', side_effect=IOError("Can't read")):
            changed = filter_unchanged_files(updated_files)
            
            # If the original can't be read, it should be included for update
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]['file_name'], str(self.file1))
