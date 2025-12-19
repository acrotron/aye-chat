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

    @patch("aye.presenter.diff_presenter._python_diff_content")
    @patch("aye.model.snapshot.get_backend")
    def test_show_diff_stash_ref_success(self, mock_get_backend, mock_diff_content):
        # Patch GitStashBackend to a dummy base class so isinstance() is satisfied
        DummyGitStashBackend = type("GitStashBackend", (), {})
        with patch(
            "aye.model.snapshot.git_backend.GitStashBackend",
            new=DummyGitStashBackend,
        ):

            class Backend(DummyGitStashBackend):
                def get_file_content_from_snapshot(self, file_path, stash_ref):
                    return "old content\n"

            mock_get_backend.return_value = Backend()

            current_file = self.dir / "test.py"
            current_file.write_text("new content\n")

            diff_presenter.show_diff(str(current_file), "stash@{0}:test.py", is_stash_ref=True)

            backend = mock_get_backend.return_value
            self.assertEqual(backend.get_file_content_from_snapshot("test.py", "stash@{0}"), "old content\n")

            mock_diff_content.assert_called_once()
            args = mock_diff_content.call_args[0]
            self.assertEqual(args[0], "new content\n")
            self.assertEqual(args[1], "old content\n")
            self.assertEqual(args[2], str(current_file))
            self.assertEqual(args[3], "stash@{0}:test.py")

    @patch("aye.presenter.diff_presenter.rprint")
    @patch("aye.model.snapshot.get_backend")
    def test_show_diff_stash_ref_wrong_backend(self, mock_get_backend, mock_rprint):
        # Ensure isinstance() fails by using a dummy GitStashBackend and returning a non-instance
        DummyGitStashBackend = type("GitStashBackend", (), {})
        with patch(
            "aye.model.snapshot.git_backend.GitStashBackend",
            new=DummyGitStashBackend,
        ):
            mock_get_backend.return_value = object()

            diff_presenter.show_diff("file.py", "stash@{0}:file.py", is_stash_ref=True)

        mock_rprint.assert_called_once_with("[red]Error: Stash references only work with git backend[/]")

    @patch("aye.presenter.diff_presenter.rprint")
    @patch("aye.model.snapshot.get_backend")
    def test_show_diff_stash_ref_file_not_in_stash(self, mock_get_backend, mock_rprint):
        DummyGitStashBackend = type("GitStashBackend", (), {})
        with patch(
            "aye.model.snapshot.git_backend.GitStashBackend",
            new=DummyGitStashBackend,
        ):

            class Backend(DummyGitStashBackend):
                def get_file_content_from_snapshot(self, file_path, stash_ref):
                    return None

            mock_get_backend.return_value = Backend()

            diff_presenter.show_diff("file.py", "stash@{0}:file.py", is_stash_ref=True)

        mock_rprint.assert_called_once_with("[red]Error: Could not extract file from stash@{0}[/]")

    @patch("aye.presenter.diff_presenter.rprint")
    @patch("aye.model.snapshot.get_backend")
    def test_show_diff_stash_ref_current_file_missing(self, mock_get_backend, mock_rprint):
        DummyGitStashBackend = type("GitStashBackend", (), {})
        with patch(
            "aye.model.snapshot.git_backend.GitStashBackend",
            new=DummyGitStashBackend,
        ):

            class Backend(DummyGitStashBackend):
                def get_file_content_from_snapshot(self, file_path, stash_ref):
                    return "content"

            mock_get_backend.return_value = Backend()

            missing_file = self.dir / "missing.py"
            diff_presenter.show_diff(str(missing_file), "stash@{0}:missing.py", is_stash_ref=True)

        mock_rprint.assert_called_once()
        self.assertIn("does not exist", mock_rprint.call_args[0][0])

    @patch("aye.presenter.diff_presenter.rprint")
    @patch("aye.model.snapshot.get_backend")
    def test_show_diff_stash_ref_exception(self, mock_get_backend, mock_rprint):
        mock_get_backend.side_effect = Exception("backend error")

        diff_presenter.show_diff("file.py", "stash@{0}:file.py", is_stash_ref=True)

        mock_rprint.assert_called_once_with("[red]Error processing stash diff:[/] backend error")

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
