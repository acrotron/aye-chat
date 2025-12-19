from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

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

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter._diff_console")
    def test_show_diff_with_system_diff(self, mock_console, mock_run):
        stdout_content = "--- file2.txt\n+++ file1.txt\n...diff output..."
        mock_run.return_value = MagicMock(stdout=stdout_content)

        diff_presenter.show_diff(self.file1, self.file2)

        mock_run.assert_called_once_with(
            ["diff", "--color=always", "-u", str(self.file2), str(self.file1)],
            capture_output=True,
            text=True,
        )
        mock_console.print.assert_called_once()

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_no_differences(self, mock_rprint, mock_run):
        mock_run.return_value = MagicMock(stdout="  ")

        diff_presenter.show_diff(self.file1, self.file1)

        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter._python_diff_files")
    def test_show_diff_system_diff_not_found(self, mock_python_diff, mock_run):
        mock_run.side_effect = FileNotFoundError

        diff_presenter.show_diff(self.file1, self.file2)

        mock_python_diff.assert_called_once_with(self.file1, self.file2)

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_system_diff_other_error(self, mock_rprint, mock_run):
        mock_run.side_effect = Exception("some error")

        diff_presenter.show_diff(self.file1, self.file2)

        mock_rprint.assert_called_once_with("[red]Error running diff:[/] some error")

    @patch("aye.presenter.diff_presenter._diff_console")
    def test_python_diff_files_with_diff(self, mock_console):
        diff_presenter._python_diff_files(self.file1, self.file2)

        mock_console.print.assert_called_once()
        self.assertIn("--- ", mock_console.print.call_args[0][0])
        self.assertIn("+++ ", mock_console.print.call_args[0][0])

    @patch("aye.presenter.diff_presenter.rprint")
    def test_python_diff_files_no_diff(self, mock_rprint):
        diff_presenter._python_diff_files(self.file1, self.file1)

        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch("aye.presenter.diff_presenter._diff_console")
    def test_python_diff_files_one_missing(self, mock_console):
        missing_file = self.dir / "missing.txt"

        diff_presenter._python_diff_files(self.file1, missing_file)

        mock_console.print.assert_called_once()
        self.assertIn("+++ ", mock_console.print.call_args[0][0])

    @patch("aye.presenter.diff_presenter.rprint")
    def test_python_diff_files_both_missing_no_diff(self, mock_rprint):
        missing1 = self.dir / "m1.txt"
        missing2 = self.dir / "m2.txt"

        diff_presenter._python_diff_files(missing1, missing2)

        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch("aye.presenter.diff_presenter.rprint")
    def test_python_diff_files_error(self, mock_rprint):
        with patch("pathlib.Path.read_text", side_effect=IOError("read error")):
            diff_presenter._python_diff_files(self.file1, self.file2)

        mock_rprint.assert_called_with("[red]Error running Python diff:[/] read error")

    @patch("aye.presenter.diff_presenter._diff_console")
    def test_python_diff_content_with_diff(self, mock_console):
        content1 = "line1\nline2\nline3"
        content2 = "line1\nmodified\nline3"

        diff_presenter._python_diff_content(content1, content2, "current", "snapshot")

        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        self.assertIn("--- snapshot", output)
        self.assertIn("+++ current", output)

    @patch("aye.presenter.diff_presenter.rprint")
    def test_python_diff_content_no_diff(self, mock_rprint):
        content = "same\ncontent"

        diff_presenter._python_diff_content(content, content, "file1", "file2")

        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch("aye.presenter.diff_presenter.rprint")
    def test_python_diff_content_error(self, mock_rprint):
        # unified_diff is imported inside the function, so patch difflib.unified_diff
        with patch("difflib.unified_diff", side_effect=Exception("diff error")):
            diff_presenter._python_diff_content("a", "b", "f1", "f2")

        mock_rprint.assert_called_with("[red]Error running Python diff:[/] diff error")

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter._diff_console")
    def test_show_diff_with_path_objects(self, mock_console, mock_run):
        stdout_content = "diff output"
        mock_run.return_value = MagicMock(stdout=stdout_content)

        diff_presenter.show_diff(self.file1, self.file2)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[3], str(self.file2))
        self.assertEqual(call_args[4], str(self.file1))

    @patch("subprocess.run")
    @patch("aye.presenter.diff_presenter._diff_console")
    def test_show_diff_strips_ansi_codes(self, mock_console, mock_run):
        stdout_with_ansi = "\x1b[31m--- file\x1b[0m\n\x1b[32m+++ file\x1b[0m"
        mock_run.return_value = MagicMock(stdout=stdout_with_ansi)

        diff_presenter.show_diff(self.file1, self.file2)

        mock_console.print.assert_called_once()
        output = mock_console.print.call_args[0][0]
        self.assertNotIn("\x1b[", output)
        self.assertIn("--- file", output)
        self.assertIn("+++ file", output)

    # -------------------------------------------------------------------------
    # Git snapshot reference (GitRefBackend) coverage
    # -------------------------------------------------------------------------

    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_wrong_backend(self, mock_rprint):
        class DummyGitRefBackend:  # used only for isinstance check
            pass

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=object()
        ):
            diff_presenter.show_diff(self.file1, "refs/aye/snapshots/001:path.txt", is_stash_ref=True)

        mock_rprint.assert_called_once_with("[red]Error: Git snapshot references only work with GitRefBackend[/]")

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_current_vs_snapshot_success(self, mock_rprint, mock_py_diff):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(return_value="snap-content\n")

        backend = DummyGitRefBackend()

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(self.file1, "refs/aye/snapshots/001:file1.txt", is_stash_ref=True)

        mock_rprint.assert_not_called()
        backend.get_file_content_from_snapshot.assert_called_once_with("file1.txt", "refs/aye/snapshots/001")
        mock_py_diff.assert_called_once()

        # Ensure labels match the implementation
        args = mock_py_diff.call_args[0]
        self.assertEqual(args[0], self.file1.read_text(encoding="utf-8"))  # current content
        self.assertEqual(args[1], "snap-content\n")
        self.assertEqual(args[2], str(self.file1))
        self.assertEqual(args[3], "refs/aye/snapshots/001:file1.txt")

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_current_file_missing(self, mock_rprint, mock_py_diff):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(return_value="snap")

        backend = DummyGitRefBackend()
        missing_current = self.dir / "does_not_exist.txt"

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(missing_current, "refs/aye/snapshots/001:file.txt", is_stash_ref=True)

        mock_py_diff.assert_not_called()
        mock_rprint.assert_called_once()
        self.assertIn("does not exist", mock_rprint.call_args[0][0])

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_snapshot_content_missing(self, mock_rprint, mock_py_diff):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(return_value=None)

        backend = DummyGitRefBackend()

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(self.file1, "refs/aye/snapshots/001:missing.txt", is_stash_ref=True)

        mock_py_diff.assert_not_called()
        mock_rprint.assert_called_once_with("[red]Error: Could not extract file from refs/aye/snapshots/001[/]")

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_two_snapshot_success(self, mock_rprint, mock_py_diff):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(side_effect=["left\n", "right\n"])

        backend = DummyGitRefBackend()

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(
                self.file1,
                "refs/aye/snapshots/001:a.txt|refs/aye/snapshots/002:b.txt",
                is_stash_ref=True,
            )

        mock_rprint.assert_not_called()
        self.assertEqual(
            backend.get_file_content_from_snapshot.mock_calls,
            [
                # args are (repo_rel_path, refname)
                # left
                (("a.txt", "refs/aye/snapshots/001"),),
                # right
                (("b.txt", "refs/aye/snapshots/002"),),
            ],
        )
        mock_py_diff.assert_called_once_with(
            "left\n",
            "right\n",
            "refs/aye/snapshots/001:a.txt",
            "refs/aye/snapshots/002:b.txt",
        )

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_two_snapshot_left_missing(self, mock_rprint, mock_py_diff):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(side_effect=[None, "right\n"])

        backend = DummyGitRefBackend()

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(
                self.file1,
                "refs/aye/snapshots/001:a.txt|refs/aye/snapshots/002:b.txt",
                is_stash_ref=True,
            )

        mock_py_diff.assert_not_called()
        mock_rprint.assert_called_once_with("[red]Error: Could not extract file from refs/aye/snapshots/001[/]")

    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_exception_handled(self, mock_rprint):
        class DummyGitRefBackend:
            def __init__(self):
                self.get_file_content_from_snapshot = MagicMock(side_effect=RuntimeError("boom"))

        backend = DummyGitRefBackend()

        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=backend
        ):
            diff_presenter.show_diff(self.file1, "refs/aye/snapshots/001:file1.txt", is_stash_ref=True)

        mock_rprint.assert_called_once()
        self.assertIn("Error processing git snapshot diff", mock_rprint.call_args[0][0])
        self.assertIn("boom", mock_rprint.call_args[0][0])

    @patch("aye.presenter.diff_presenter.rprint")
    def test_show_diff_git_snapshot_malformed_ref_string_handled(self, mock_rprint):
        class DummyGitRefBackend:
            pass

        # Missing ':' triggers ValueError in _extract; should be caught by outer try
        with patch("aye.model.snapshot.git_ref_backend.GitRefBackend", DummyGitRefBackend), patch(
            "aye.model.snapshot.get_backend", return_value=DummyGitRefBackend()
        ):
            diff_presenter.show_diff(self.file1, "not-a-ref-with-colon", is_stash_ref=True)

        mock_rprint.assert_called_once()
        self.assertIn("Error processing git snapshot diff", mock_rprint.call_args[0][0])
