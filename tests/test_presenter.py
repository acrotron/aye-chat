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

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_auth_status_real_token(self, mock_print):
        cli_ui.print_auth_status("real_token_1234567890abc")
        self.assertEqual(mock_print.call_count, 2)
        mock_print.assert_any_call("[ui.success]Authenticated[/] - [ui.help.text]Token is saved[/]")
        mock_print.assert_any_call("  [ui.help.text]Token: real_token_1...[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_auth_status_demo_token(self, mock_print):
        cli_ui.print_auth_status("aye_demo_12345")
        self.assertEqual(mock_print.call_count, 2)
        mock_print.assert_any_call("[ui.warning]Demo Mode[/] - [ui.help.text]Using demo token[/]")
        mock_print.assert_any_call("  [ui.help.text]Run 'aye auth login' to authenticate with a real token[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_auth_status_no_token(self, mock_print):
        cli_ui.print_auth_status(None)
        self.assertEqual(mock_print.call_count, 2)
        mock_print.assert_any_call("[ui.error]Not Authenticated[/] - [ui.help.text]No token saved[/]")
        mock_print.assert_any_call("  [ui.help.text]Run 'aye auth login' to authenticate[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_snapshot_history_with_snapshots(self, mock_print):
        cli_ui.print_snapshot_history(["snap1", "snap2"])
        self.assertEqual(mock_print.call_count, 3)
        mock_print.assert_any_call("[ui.help.header]Snapshot History:[/]")
        mock_print.assert_any_call("  [ui.help.text]snap1[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_snapshot_history_no_snapshots(self, mock_print):
        cli_ui.print_snapshot_history([])
        mock_print.assert_called_once_with("[ui.warning]No snapshots found.[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_snapshot_content_found(self, mock_print):
        cli_ui.print_snapshot_content("file content")
        mock_print.assert_called_once_with("file content")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_snapshot_content_not_found(self, mock_print):
        cli_ui.print_snapshot_content(None)
        mock_print.assert_called_once_with("Snapshot not found.", style="ui.error")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_restore_feedback(self, mock_print):
        cli_ui.print_restore_feedback("001", "file.txt")
        mock_print.assert_called_with("[ui.success]✅ File 'file.txt' restored to 001[/]")
        cli_ui.print_restore_feedback("001", None)
        mock_print.assert_called_with("[ui.success]✅ All files restored to 001[/]")
        cli_ui.print_restore_feedback(None, "file.txt")
        mock_print.assert_called_with("[ui.success]✅ File 'file.txt' restored to latest snapshot[/]")
        cli_ui.print_restore_feedback(None, None)
        mock_print.assert_called_with("[ui.success]✅ All files restored to latest snapshot[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_prune_feedback(self, mock_print):
        cli_ui.print_prune_feedback(5, 10)
        mock_print.assert_called_with("[ui.success]✅ 5 snapshots deleted. 10 most recent snapshots kept.[/]")
        cli_ui.print_prune_feedback(0, 10)
        mock_print.assert_called_with("[ui.success]✅ No snapshots deleted. You have fewer than the specified keep count.[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_cleanup_feedback(self, mock_print):
        cli_ui.print_cleanup_feedback(3, 30)
        mock_print.assert_called_with("[ui.success]✅ 3 snapshots older than 30 days deleted.[/]")
        cli_ui.print_cleanup_feedback(0, 30)
        mock_print.assert_called_with("[ui.success]✅ No snapshots older than 30 days found.[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_config_list(self, mock_print):
        cli_ui.print_config_list({"key": "value"})
        mock_print.assert_any_call("  [ui.help.command]key[/]: [ui.help.text]value[/]")
        cli_ui.print_config_list({})
        mock_print.assert_called_with("[ui.warning]No configuration values set.[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_config_value(self, mock_print):
        cli_ui.print_config_value("key", "value")
        mock_print.assert_called_with("[ui.help.command]key[/]: [ui.help.text]value[/]")
        cli_ui.print_config_value("key", None)
        mock_print.assert_called_with("[ui.warning]Configuration key 'key' not found.[/]")

    @patch('aye.presenter.cli_ui.console.print')
    def test_print_generic_message(self, mock_print):
        cli_ui.print_generic_message("Success")
        mock_print.assert_called_with("[ui.success]Success[/]")
        cli_ui.print_generic_message("Failure", is_error=True)
        mock_print.assert_called_with("[ui.error]Failure[/]")


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


class TestReplUi(TestCase):
    @patch('aye.presenter.repl_ui.console.print')
    def test_print_welcome_message(self, mock_console_print):
        repl_ui.print_welcome_message()
        mock_console_print.assert_called_once()
        args, kwargs = mock_console_print.call_args
        self.assertIn("Aye Chat", args[0])
        self.assertEqual(kwargs.get('style'), "ui.welcome")

    @patch('aye.presenter.repl_ui.console.print')
    def test_print_help_message(self, mock_console_print):
        repl_ui.print_help_message()
        self.assertEqual(mock_console_print.call_count, 14)

    def test_print_prompt(self):
        self.assertEqual(repl_ui.print_prompt(), "(ツ» ")

    @patch('aye.presenter.repl_ui.console.print')
    def test_print_assistant_response(self, mock_print):
        repl_ui.print_assistant_response("summary text")
        # Should be called for the grid and the newline
        self.assertEqual(mock_print.call_count, 4)

    @patch('rich.console.Console.print')
    def test_print_no_files_changed(self, mock_print):
        # Test with a console that has no theme
        console = Console()
        # Ensure it doesn't look like it has a theme for the test logic
        # (Rich consoles always have a default theme, but our code checks getattr(..., "theme", None))
        # actually Console().theme is always set. 
        # But repl_ui code checks: getattr(console_arg, "theme", None)
        # So we just pass a real console.
        
        repl_ui.print_no_files_changed(console)
        mock_print.assert_called_once()
        # Our code uses padding, so we check the string representation of the call args
        self.assertIn("No files were changed", str(mock_print.call_args))

    @patch('rich.console.Console.print')
    def test_print_files_updated(self, mock_print):
        console = Console()
        repl_ui.print_files_updated(console, ["file1.py", "file2.py"])
        mock_print.assert_called_once()
        self.assertIn("Files updated", str(mock_print.call_args))
        self.assertIn("file1.py,file2.py", str(mock_print.call_args))

    @patch('aye.presenter.repl_ui.console.print')
    def test_print_error(self, mock_console_print):
        exc = ValueError("test error")
        repl_ui.print_error(exc)
        mock_console_print.assert_called_once_with(f"[ui.error]Error:[/] {exc}")


class TestUiUtils(TestCase):
    @patch('aye.presenter.ui_utils.Spinner')
    def test_thinking_spinner(self, mock_spinner_class):
        mock_console = MagicMock(spec=Console)
        mock_spinner_instance = MagicMock(spec=Spinner)
        mock_spinner_class.return_value = mock_spinner_instance

        with ui_utils.thinking_spinner(mock_console, text="Loading..."):
            pass

        mock_spinner_class.assert_called_once_with("dots", text="Loading...")
        mock_console.status.assert_called_once_with(mock_spinner_instance)
