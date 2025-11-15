import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

from aye.controller import tutorial

class TestTutorial(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.tmpdir.name)
        self.tutorial_flag_file = self.home_dir / ".aye" / ".tutorial_ran"

        self.home_patcher = patch('pathlib.Path.home', return_value=self.home_dir)
        self.home_patcher.start()

        # Ensure the flag file doesn't exist before each test
        self.tutorial_flag_file.unlink(missing_ok=True)
        if self.tutorial_flag_file.parent.exists():
            self.tutorial_flag_file.parent.rmdir()

    def tearDown(self):
        self.home_patcher.stop()
        self.tmpdir.cleanup()

    @patch('aye.controller.tutorial.run_tutorial')
    def test_run_first_time_tutorial_if_needed_runs(self, mock_run_tutorial):
        self.assertFalse(self.tutorial_flag_file.exists())
        tutorial.run_first_time_tutorial_if_needed()
        mock_run_tutorial.assert_called_once()

    @patch('aye.controller.tutorial.run_tutorial')
    def test_run_first_time_tutorial_if_needed_skips(self, mock_run_tutorial):
        self.tutorial_flag_file.parent.mkdir(parents=True)
        self.tutorial_flag_file.touch()
        self.assertTrue(self.tutorial_flag_file.exists())
        
        tutorial.run_first_time_tutorial_if_needed()
        mock_run_tutorial.assert_not_called()

    @patch('aye.controller.tutorial.Confirm.ask', return_value=False)
    @patch('aye.controller.tutorial.rprint')
    def test_run_tutorial_user_declines(self, mock_rprint, mock_confirm):
        tutorial.run_tutorial()
        mock_confirm.assert_called_once()
        self.assertTrue(self.tutorial_flag_file.exists())
        mock_rprint.assert_any_call("\nSkipping tutorial. You can run it again by deleting the `~/.aye/.tutorial_ran` file.")

    @patch('pathlib.Path.write_text')
    @patch('pathlib.Path.read_text')
    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates', return_value="001_ts")
    @patch('aye.controller.tutorial.list_snapshots', return_value=[('001_ts', 'snap_path')])
    @patch('aye.controller.tutorial.show_diff')
    @patch('aye.controller.tutorial.restore_snapshot')
    @patch('pathlib.Path.unlink')
    def test_run_tutorial_success_flow(self, mock_unlink, mock_restore, mock_diff, mock_list_snaps, mock_apply, mock_sleep, mock_input, mock_confirm, mock_read_text, mock_write_text):
        # Mock file content for read_text after restore
        mock_read_text.return_value = 'def hello_world():\n    print("Hello, World!")\n'
        # Mock write_text to do nothing (no real file creation)
        mock_write_text.return_value = None
        
        tutorial.run_tutorial()
        
        mock_confirm.assert_called_once()
        self.assertGreaterEqual(mock_input.call_count, 3)
        mock_apply.assert_called_once()
        mock_diff.assert_called_once()
        mock_restore.assert_called_once_with(file_name='tutorial_example.py')
        
        # Check that temp file and flag file were handled
        self.assertTrue(self.tutorial_flag_file.exists())
        mock_unlink.assert_called_with() # called on the temp file
        mock_write_text.assert_called_once()  # Called during creation
        mock_read_text.assert_called_once()  # Called after restore

    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates', side_effect=RuntimeError("Model failed"))
    @patch('aye.controller.tutorial.rprint')
    def test_run_tutorial_step1_error(self, mock_rprint, mock_apply, mock_sleep, mock_input, mock_confirm):
        tutorial.run_tutorial()
        mock_rprint.assert_any_call("[red]An error occurred during the tutorial: Model failed[/red]")
        self.assertTrue(self.tutorial_flag_file.exists())