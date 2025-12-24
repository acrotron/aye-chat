from unittest import TestCase
from unittest.mock import patch, MagicMock
import tempfile
from pathlib import Path

import aye.presenter.diff_presenter as diff_presenter


class TestDiffPresenter(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)
        self.file1 = self.dir / "file1.txt"
        self.file2 = self.dir / "file2.txt"
        self.file1.write_text("hello\nworld")
        self.file2.write_text("hello\nthere")

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff(self, mock_console):
        diff_presenter.show_diff(self.file1, self.file2)
        # Should call print multiple times for header, chunks, changes
        self.assertTrue(mock_console.print.called)
        
        # Collect all calls to print to verify output
        calls_args = [str(args[0]) for args, _ in mock_console.print.call_args_list]
        combined_output = "\n".join(calls_args)
        
        self.assertIn("---", combined_output)
        self.assertIn("+++", combined_output)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_no_differences(self, mock_console):
        diff_presenter.show_diff(self.file1, self.file1)
        # When no differences, it prints "No differences found."
        mock_console.print.assert_called_once_with("No differences found.", style="diff.warning")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_one_missing(self, mock_console):
        missing_file = self.dir / "missing.txt"
        # file1 exists, missing_file does not.
        diff_presenter._python_diff_files(self.file1, missing_file)
        
        # When one file is missing, difflib still produces output showing the diff
        # (all lines added/removed). The code prints via _diff_console.print
        self.assertTrue(
            mock_console.print.called,
            "Should have printed diff content"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_error(self, mock_console):
        with patch('pathlib.Path.read_text', side_effect=IOError("read error")):
            diff_presenter._python_diff_files(self.file1, self.file2)
            mock_console.print.assert_called_with("Error running Python diff: read error", style="diff.error")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_with_diff(self, mock_console):
        diff_presenter._python_diff_files(self.file1, self.file2)
        # Should call print multiple times for headers, hunks, and content lines
        self.assertTrue(mock_console.print.call_count >= 1)
        
        # Verify diff output contains expected elements
        calls_args = [str(args[0]) if args else "" for args, _ in mock_console.print.call_args_list]
        combined_output = "\n".join(calls_args)
        self.assertIn("---", combined_output)
        self.assertIn("+++", combined_output)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_no_diff(self, mock_console):
        # Same file on both sides = no differences
        diff_presenter._python_diff_files(self.file1, self.file1)
        mock_console.print.assert_called_once_with("No differences found.", style="diff.warning")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_both_missing_no_diff(self, mock_console):
        missing1 = self.dir / "missing1.txt"
        missing2 = self.dir / "missing2.txt"
        diff_presenter._python_diff_files(missing1, missing2)
        # Both files missing = both empty = no diff
        mock_console.print.assert_called_once_with("No differences found.", style="diff.warning")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_content_with_diff(self, mock_console):
        content1 = "hello\nworld"
        content2 = "hello\nthere"
        diff_presenter._python_diff_content(content1, content2, "file1.txt", "file2.txt")
        # Should call print multiple times for headers and content
        self.assertTrue(mock_console.print.call_count >= 1)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_content_no_diff(self, mock_console):
        content = "hello\nworld"
        diff_presenter._python_diff_content(content, content, "file1.txt", "file2.txt")
        mock_console.print.assert_called_once_with("No differences found.", style="diff.warning")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_content_error(self, mock_console):
        # Force an error by passing non-string content
        with patch('aye.presenter.diff_presenter.difflib.unified_diff', side_effect=Exception("diff error")):
            diff_presenter._python_diff_content("a", "b", "f1", "f2")
            mock_console.print.assert_called_with("Error running Python diff: diff error", style="diff.error")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_with_path_objects(self, mock_console):
        # Test that Path objects work correctly
        diff_presenter.show_diff(self.file1, self.file2)
        self.assertTrue(mock_console.print.called)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_with_system_diff(self, mock_console):
        # The refactored code uses Python difflib exclusively, not subprocess
        diff_presenter.show_diff(str(self.file1), str(self.file2))
        self.assertTrue(mock_console.print.called)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_strips_ansi_codes(self, mock_console):
        # The refactored code uses Python difflib, so ANSI stripping is not needed
        # Just verify the diff works with string paths
        diff_presenter.show_diff(str(self.file1), str(self.file2))
        self.assertTrue(mock_console.print.called)

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_system_diff_other_error(self, mock_console):
        # Test error handling - use a non-existent file that will cause read error
        with patch('pathlib.Path.read_text', side_effect=IOError("read error")):
            diff_presenter.show_diff(self.file1, self.file2)
            mock_console.print.assert_called_with("Error running Python diff: read error", style="diff.error")

    # Tests for git stash reference handling
    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_wrong_backend(self, mock_get_backend, mock_console):
        # Non-GitRefBackend should produce an error
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend
        
        diff_presenter.show_diff(self.file1, "stash@{0}:file.txt", is_stash_ref=True)
        mock_console.print.assert_called_with(
            "Error: Git snapshot references only work with GitRefBackend",
            style="diff.error"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_current_vs_snapshot_success(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_backend.get_file_content_from_snapshot.return_value = "snapshot content\n"
        mock_get_backend.return_value = mock_backend
        
        diff_presenter.show_diff(self.file1, "stash@{0}:file.txt", is_stash_ref=True)
        # Should have called print for diff output
        self.assertTrue(mock_console.print.called)

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_snapshot_content_missing(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_backend.get_file_content_from_snapshot.return_value = None
        mock_get_backend.return_value = mock_backend
        
        diff_presenter.show_diff(self.file1, "stash@{0}:file.txt", is_stash_ref=True)
        mock_console.print.assert_called_with(
            "Error: Could not extract file from stash@{0}",
            style="diff.error"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_current_file_missing(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_backend.get_file_content_from_snapshot.return_value = "snapshot content\n"
        mock_get_backend.return_value = mock_backend
        
        missing_file = self.dir / "nonexistent.txt"
        diff_presenter.show_diff(missing_file, "stash@{0}:file.txt", is_stash_ref=True)
        mock_console.print.assert_called_with(
            f"Error: Current file {missing_file} does not exist",
            style="diff.error"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_two_snapshot_success(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_backend.get_file_content_from_snapshot.side_effect = [
            "left content\n",
            "right content\n"
        ]
        mock_get_backend.return_value = mock_backend
        
        diff_presenter.show_diff(
            self.file1,
            "stash@{0}:file.txt|stash@{1}:file.txt",
            is_stash_ref=True
        )
        # Should have called print for diff output
        self.assertTrue(mock_console.print.called)

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_two_snapshot_left_missing(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_backend.get_file_content_from_snapshot.side_effect = [None, "right content\n"]
        mock_get_backend.return_value = mock_backend
        
        diff_presenter.show_diff(
            self.file1,
            "stash@{0}:file.txt|stash@{1}:file.txt",
            is_stash_ref=True
        )
        mock_console.print.assert_called_with(
            "Error: Could not extract file from stash@{0}",
            style="diff.error"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_exception_handled(self, mock_get_backend, mock_console):
        mock_get_backend.side_effect = Exception("Backend error")
        
        diff_presenter.show_diff(self.file1, "stash@{0}:file.txt", is_stash_ref=True)
        mock_console.print.assert_called_with(
            "Error processing stash diff: Backend error",
            style="diff.error"
        )

    @patch('aye.presenter.diff_presenter._diff_console')
    @patch('aye.presenter.diff_presenter.get_backend')
    def test_show_diff_git_snapshot_malformed_ref_string_handled(self, mock_get_backend, mock_console):
        from aye.model.snapshot.git_ref_backend import GitRefBackend
        
        mock_backend = MagicMock(spec=GitRefBackend)
        mock_get_backend.return_value = mock_backend
        
        # Malformed ref without colon will raise ValueError on split
        diff_presenter.show_diff(self.file1, "malformed_ref_no_colon", is_stash_ref=True)
        # Should catch the exception and print error
        self.assertTrue(mock_console.print.called)
        call_args = mock_console.print.call_args
        self.assertEqual(call_args[1].get('style'), 'diff.error')
