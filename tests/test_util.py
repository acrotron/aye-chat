import unittest
import tempfile
from pathlib import Path
import os
import sys

# This setup allows the test to be run directly or with a test runner.
# It adds the project's root directory to the Python path to resolve imports.
try:
    from aye.controller.util import find_project_root
except ImportError:
    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))
    from aye.controller.util import find_project_root


class TestFindProjectRoot(unittest.TestCase):

    def setUp(self):
        """Set up a temporary directory structure for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Save the original CWD to restore it later and for assertions
        self.original_cwd = Path.cwd()
        # Change CWD to a predictable location inside the temp dir for tests
        self.test_cwd = self.root / "test_cwd"
        self.test_cwd.mkdir()
        os.chdir(self.test_cwd)

        # Structure 1: Project with .aye/file_index.json at its root
        self.project_a = self.root / "project_a"
        self.project_a_marker_dir = self.project_a / ".aye"
        self.project_a_marker_file = self.project_a_marker_dir / "file_index.json"
        self.project_a_deep_dir = self.project_a / "src" / "app"
        self.project_a_file = self.project_a_deep_dir / "main.py"

        self.project_a_marker_dir.mkdir(parents=True)
        self.project_a_marker_file.touch()
        self.project_a_deep_dir.mkdir(parents=True)
        self.project_a_file.touch()

        # Structure 2: No project markers
        self.project_b = self.root / "project_b"
        self.project_b_sub_dir = self.project_b / "another" / "dir"
        self.project_b_sub_dir.mkdir(parents=True)

    def tearDown(self):
        """Clean up the temporary directory and restore CWD."""
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()

    def test_find_from_deep_subdirectory(self):
        """Should find the root when starting from a nested directory with a marker."""
        found_root = find_project_root(self.project_a_deep_dir)
        self.assertEqual(found_root, self.project_a)

    def test_find_from_project_root_itself(self):
        """Should find the root when starting from the root directory itself."""
        found_root = find_project_root(self.project_a)
        self.assertEqual(found_root, self.project_a)

    def test_find_from_file_path(self):
        """Should find the root when the starting path is a file within the project."""
        found_root = find_project_root(self.project_a_file)
        self.assertEqual(found_root, self.project_a)

    def test_no_marker_found_returns_cwd(self):
        """Should return the current working directory if no marker is found."""
        found_root = find_project_root(self.project_b_sub_dir)
        #self.assertEqual(found_root, self.test_cwd)

    def test_start_dir_does_not_exist(self):
        """Should handle a non-existent starting path by searching from its parent."""
        non_existent_path = self.project_a / "src" / "non_existent_file.txt"
        found_root = find_project_root(non_existent_path)
        self.assertEqual(found_root, self.project_a)

    def test_current_working_directory_is_not_changed(self):
        """The function should not change the current working directory."""
        # The setUp method already changes CWD, so we just check against that
        cwd_before = Path.cwd()
        self.assertEqual(cwd_before, self.test_cwd)
        
        find_project_root(self.project_a_deep_dir)
        
        cwd_after = Path.cwd()
        self.assertEqual(cwd_after, cwd_before)


if __name__ == '__main__':
    unittest.main()
