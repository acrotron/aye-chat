from unittest import TestCase
from unittest.mock import patch, MagicMock
import tempfile
from pathlib import Path
import subprocess

import aye.presenter.cli_ui as cli_ui
import aye.presenter.diff_presenter as diff_presenter
import aye.presenter.repl_ui as repl_ui
import aye.presenter.ui_utils as ui_utils
from rich.console import Console
from rich.spinner import Spinner

class TestCliUi(TestCase):

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_auth_status_real_token(self, mock_rprint):
        cli_ui.print_auth_status("real_token_1234567890abc")
        self.assertEqual(mock_rprint.call_count, 2)
        mock_rprint.assert_any_call("[green]Authenticated[/] - Token is saved")
        mock_rprint.assert_any_call("  Token: real_token_1...")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_auth_status_demo_token(self, mock_rprint):
        cli_ui.print_auth_status("aye_demo_12345")
        self.assertEqual(mock_rprint.call_count, 2)
        mock_rprint.assert_any_call("[yellow]Demo Mode[/] - Using demo token")
        mock_rprint.assert_any_call("  Run 'aye auth login' to authenticate with a real token")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_auth_status_no_token(self, mock_rprint):
        cli_ui.print_auth_status(None)
        self.assertEqual(mock_rprint.call_count, 2)
        mock_rprint.assert_any_call("[red]Not Authenticated[/] - No token saved")
        mock_rprint.assert_any_call("  Run 'aye auth login' to authenticate")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_snapshot_history_with_snapshots(self, mock_rprint):
        cli_ui.print_snapshot_history(["snap1", "snap2"])
        self.assertEqual(mock_rprint.call_count, 3)
        mock_rprint.assert_any_call("[bold]Snapshot History:[/]")
        mock_rprint.assert_any_call("  snap1")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_snapshot_history_no_snapshots(self, mock_rprint):
        cli_ui.print_snapshot_history([])
        mock_rprint.assert_called_once_with("[yellow]No snapshots found.[/]")

    @patch('builtins.print')
    def test_print_snapshot_content_found(self, mock_print):
        cli_ui.print_snapshot_content("file content")
        mock_print.assert_called_once_with("file content")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_snapshot_content_not_found(self, mock_rprint):
        cli_ui.print_snapshot_content(None)
        mock_rprint.assert_called_once_with("Snapshot not found.", err=True)

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_restore_feedback(self, mock_rprint):
        cli_ui.print_restore_feedback("001", "file.txt")
        mock_rprint.assert_called_with("✅ File 'file.txt' restored to 001")
        cli_ui.print_restore_feedback("001", None)
        mock_rprint.assert_called_with("✅ All files restored to 001")
        cli_ui.print_restore_feedback(None, "file.txt")
        mock_rprint.assert_called_with("✅ File 'file.txt' restored to latest snapshot")
        cli_ui.print_restore_feedback(None, None)
        mock_rprint.assert_called_with("✅ All files restored to latest snapshot")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_prune_feedback(self, mock_rprint):
        cli_ui.print_prune_feedback(5, 10)
        mock_rprint.assert_called_with("✅ 5 snapshots deleted. 10 most recent snapshots kept.")
        cli_ui.print_prune_feedback(0, 10)
        mock_rprint.assert_called_with("✅ No snapshots deleted. You have fewer than the specified keep count.")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_cleanup_feedback(self, mock_rprint):
        cli_ui.print_cleanup_feedback(3, 30)
        mock_rprint.assert_called_with("✅ 3 snapshots older than 30 days deleted.")
        cli_ui.print_cleanup_feedback(0, 30)
        mock_rprint.assert_called_with("✅ No snapshots older than 30 days found.")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_config_list(self, mock_rprint):
        cli_ui.print_config_list({"key": "value"})
        mock_rprint.assert_any_call("  key: value")
        cli_ui.print_config_list({})
        mock_rprint.assert_called_with("[yellow]No configuration values set.[/]")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_config_value(self, mock_rprint):
        cli_ui.print_config_value("key", "value")
        mock_rprint.assert_called_with("key: value")
        cli_ui.print_config_value("key", None)
        mock_rprint.assert_called_with("[yellow]Configuration key 'key' not found.[/]")

    @patch('aye.presenter.cli_ui.rprint')
    def test_print_generic_message(self, mock_rprint):
        cli_ui.print_generic_message("Success")
        mock_rprint.assert_called_with("[green]Success[/]")
        cli_ui.print_generic_message("Failure", is_error=True)
        mock_rprint.assert_called_with("[red]Failure[/]")


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

    @patch('subprocess.run')
    @patch('aye.presenter.diff_presenter._diff_console')
    def test_show_diff_with_system_diff(self, mock_console, mock_run):
        stdout_content = "--- file2.txt\n+++ file1.txt\n...diff output..."
        mock_run.return_value = MagicMock(stdout=stdout_content)
        diff_presenter.show_diff(self.file1, self.file2)
        mock_run.assert_called_once_with(
            ["diff", "--color=always", "-u", str(self.file2), str(self.file1)],
            capture_output=True,
            text=True
        )
        mock_console.print.assert_called_once()

    @patch('subprocess.run')
    @patch('aye.presenter.diff_presenter.rprint')
    def test_show_diff_no_differences(self, mock_rprint, mock_run):
        mock_run.return_value = MagicMock(stdout="  ")
        diff_presenter.show_diff(self.file1, self.file1)
        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch('subprocess.run')
    @patch('aye.presenter.diff_presenter._python_diff_files')
    def test_show_diff_system_diff_not_found(self, mock_python_diff, mock_run):
        mock_run.side_effect = FileNotFoundError
        diff_presenter.show_diff(self.file1, self.file2)
        mock_python_diff.assert_called_once_with(self.file1, self.file2)

    @patch('subprocess.run')
    @patch('aye.presenter.diff_presenter.rprint')
    def test_show_diff_system_diff_other_error(self, mock_rprint, mock_run):
        mock_run.side_effect = Exception("some error")
        diff_presenter.show_diff(self.file1, self.file2)
        mock_rprint.assert_called_once_with("[red]Error running diff:[/] some error")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_with_diff(self, mock_console):
        diff_presenter._python_diff_files(self.file1, self.file2)
        mock_console.print.assert_called_once()
        self.assertIn("--- ", mock_console.print.call_args[0][0])
        self.assertIn("+++ ", mock_console.print.call_args[0][0])

    @patch('aye.presenter.diff_presenter.rprint')
    def test_python_diff_files_no_diff(self, mock_rprint):
        diff_presenter._python_diff_files(self.file1, self.file1)
        mock_rprint.assert_called_once_with("[green]No differences found.[/]")

    @patch('aye.presenter.diff_presenter._diff_console')
    def test_python_diff_files_one_missing(self, mock_console):
        missing_file = self.dir / "missing.txt"
        diff_presenter._python_diff_files(self.file1, missing_file)
        mock_console.print.assert_called_once()
        self.assertIn("+++ ", mock_console.print.call_args[0][0]) # All lines are additions

    @patch('aye.presenter.diff_presenter.rprint')
    def test_python_diff_files_error(self, mock_rprint):
        with patch('pathlib.Path.read_text', side_effect=IOError("read error")):
            diff_presenter._python_diff_files(self.file1, self.file2)
            mock_rprint.assert_called_with("[red]Error running Python diff:[/] read error")


class TestReplUi(TestCase):
    @patch('aye.presenter.repl_ui.rprint')
    def test_print_welcome_message(self, mock_rprint):
        repl_ui.print_welcome_message()
        mock_rprint.assert_called_once_with("[bold cyan]Aye Chat – type `help` for available commands, `exit` or Ctrl+D to quit[/]")

    @patch('aye.presenter.repl_ui.rprint')
    @patch('builtins.print')
    def test_print_help_message(self, mock_print, mock_rprint):
        repl_ui.print_help_message()
        self.assertGreater(mock_rprint.call_count, 0)
        self.assertGreater(mock_print.call_count, 0)

    def test_print_prompt(self):
        self.assertEqual(repl_ui.print_prompt(), "(ツ» ")

    def test_print_thinking_spinner(self):
        spinner = repl_ui.print_thinking_spinner(MagicMock())
        self.assertIsInstance(spinner, Spinner)
        self.assertEqual(str(spinner.text), "Thinking...")

    @patch('aye.presenter.repl_ui.rprint')
    def test_print_assistant_response(self, mock_rprint):
        repl_ui.print_assistant_response("summary text")
        self.assertEqual(mock_rprint.call_count, 3)

    @patch('rich.console.Console.print')
    def test_print_no_files_changed(self, mock_print):
        console = Console()
        repl_ui.print_no_files_changed(console)
        mock_print.assert_called_once()
        self.assertIn("No files were changed", str(mock_print.call_args))

    @patch('rich.console.Console.print')
    def test_print_files_updated(self, mock_print):
        console = Console()
        repl_ui.print_files_updated(console, ["file1.py", "file2.py"])
        mock_print.assert_called_once()
        self.assertIn("Files updated:[/] file1.py,file2.py", str(mock_print.call_args))

    @patch('aye.presenter.repl_ui.rprint')
    def test_print_error(self, mock_rprint):
        exc = ValueError("test error")
        repl_ui.print_error(exc)
        mock_rprint.assert_called_once_with(f"[red]Error:[/] {exc}")


class TestUiUtils(TestCase):
    @patch('aye.presenter.ui_utils.Spinner')
    def test_thinking_spinner(self, mock_spinner_class):
        mock_console = MagicMock(spec=Console)
        mock_spinner_instance = MagicMock(spec=Spinner)
        mock_spinner_class.return_value = mock_spinner_instance

        with ui_utils.thinking_spinner(mock_console, text="Loading..."):
            pass

        mock_spinner_class.assert_called_once_with("dots", text="[yellow]Loading...[/]")
        mock_console.status.assert_called_once_with(mock_spinner_instance)
