import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
import os

from aye.controller import tutorial

class TestTutorial(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.tmpdir.name)
        self.home_dir = self.test_root
        self.tutorial_flag_file = self.home_dir / ".aye" / ".tutorial_ran"

        self.home_patcher = patch('pathlib.Path.home', return_value=self.home_dir)
        self.home_patcher.start()

        # Change CWD to a temporary directory to contain tutorial_example.py
        self.original_cwd = os.getcwd()
        os.chdir(self.test_root)

        # Ensure the flag file doesn't exist before each test
        self.tutorial_flag_file.unlink(missing_ok=True)
        if self.tutorial_flag_file.parent.exists():
            self.tutorial_flag_file.parent.rmdir()

    def tearDown(self):
        # Restore CWD
        os.chdir(self.original_cwd)
        self.home_patcher.stop()
        self.tmpdir.cleanup()

    @patch('aye.controller.tutorial.run_tutorial')
    def test_run_first_time_tutorial_if_needed_runs(self, mock_run_tutorial):
        """Test that tutorial runs automatically on first invocation."""
        self.assertFalse(self.tutorial_flag_file.exists())
        result = tutorial.run_first_time_tutorial_if_needed()
        self.assertTrue(result)
        mock_run_tutorial.assert_called_once_with(is_first_run=True)

    def test_run_first_time_tutorial_if_needed_subsequent_runs(self):
        """Test that subsequent runs do not prompt or run tutorial."""
        self.tutorial_flag_file.parent.mkdir(parents=True)
        self.tutorial_flag_file.touch()
        self.assertTrue(self.tutorial_flag_file.exists())
        
        result = tutorial.run_first_time_tutorial_if_needed()
        self.assertFalse(result)

    @patch('aye.controller.tutorial.Confirm.ask', return_value=False)
    @patch('aye.controller.tutorial.console')
    def test_run_tutorial_user_declines_subsequent_run(self, mock_console, mock_confirm):
        """Test that subsequent runs prompt user and skip if declined."""
        tutorial.run_tutorial(is_first_run=False)
        
        # Should prompt with default=False
        mock_confirm.assert_called_once()
        call_args = mock_confirm.call_args
        self.assertEqual(call_args[1]['default'], False)
        
        # Should create flag file even when skipped
        self.assertTrue(self.tutorial_flag_file.exists())
        mock_console.print.assert_any_call("\nSkipping tutorial.")

    @patch('aye.controller.tutorial.Confirm.ask')
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates')
    @patch('aye.controller.tutorial.list_snapshots')
    @patch('aye.controller.tutorial.show_diff')
    @patch('aye.controller.tutorial.restore_snapshot')
    @patch('aye.controller.tutorial.print_assistant_response')
    @patch('aye.controller.tutorial.console')
    def test_run_tutorial_first_run_no_prompt(self, mock_console, mock_print_resp, mock_restore, mock_diff, mock_list_snaps, mock_apply, mock_sleep, mock_input, mock_confirm):
        """Test that first run does NOT prompt user and executes all steps."""
        snap_content = 'def hello_world():\n    print("Hello, World!")\n'
        snap_file = self.test_root / "snap_for_diff.py"
        snap_file.write_text(snap_content)

        mock_apply.return_value = "001_ts"
        mock_list_snaps.return_value = [('001_ts', str(snap_file))]

        tutorial_file = Path("tutorial_example.py")
        self.assertFalse(tutorial_file.exists())

        tutorial.run_tutorial(is_first_run=True)
        
        # Should NOT prompt on first run
        mock_confirm.assert_not_called()
        
        # Should complete all steps (5 input prompts: welcome + 4 steps)
        self.assertGreaterEqual(mock_input.call_count, 5)
        mock_apply.assert_called()
        mock_restore.assert_called_once_with(file_name='tutorial_example.py')
        self.assertTrue(self.tutorial_flag_file.exists())
        self.assertFalse(tutorial_file.exists())

    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates')
    @patch('aye.controller.tutorial.list_snapshots')
    @patch('aye.controller.tutorial.show_diff')
    @patch('aye.controller.tutorial.restore_snapshot')
    @patch('aye.controller.tutorial.print_assistant_response')
    @patch('aye.controller.tutorial.console')
    def test_run_tutorial_subsequent_run_with_confirmation(self, mock_console, mock_print_resp, mock_restore, mock_diff, mock_list_snaps, mock_apply, mock_sleep, mock_input, mock_confirm):
        """Test that subsequent runs prompt user and execute if confirmed."""
        snap_content = 'def hello_world():\n    print("Hello, World!")\n'
        snap_file = self.test_root / "snap_for_diff.py"
        snap_file.write_text(snap_content)

        mock_apply.return_value = "001_ts"
        mock_list_snaps.return_value = [('001_ts', str(snap_file))]

        tutorial_file = Path("tutorial_example.py")

        tutorial.run_tutorial(is_first_run=False)
        
        # Should prompt with default=False
        mock_confirm.assert_called_once()
        call_args = mock_confirm.call_args
        self.assertEqual(call_args[1]['default'], False)
        
        # Should complete all steps since user confirmed
        mock_apply.assert_called()
        mock_restore.assert_called_once()
        self.assertTrue(self.tutorial_flag_file.exists())

    @patch('aye.controller.tutorial.Confirm.ask')
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates', side_effect=RuntimeError("Model failed"))
    @patch('aye.controller.tutorial.console')
    def test_run_tutorial_step1_error(self, mock_console, mock_apply, mock_sleep, mock_input, mock_confirm):
        """Test that tutorial handles errors gracefully on first run."""
        tutorial_file = Path("tutorial_example.py")

        tutorial.run_tutorial(is_first_run=True)

        # Should NOT prompt on first run
        mock_confirm.assert_not_called()
        
        # Should show error and clean up
        mock_console.print.assert_any_call("[ui.error]An error occurred during the tutorial: Model failed[/]")
        self.assertTrue(self.tutorial_flag_file.exists())
        self.assertFalse(tutorial_file.exists())