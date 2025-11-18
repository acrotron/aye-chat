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

    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates')
    @patch('aye.controller.tutorial.list_snapshots')
    @patch('aye.controller.tutorial.show_diff')
    @patch('aye.controller.tutorial.restore_snapshot')
    def test_run_tutorial_success_flow(self, mock_restore, mock_diff, mock_list_snaps, mock_apply, mock_sleep, mock_input, mock_confirm):
        # The tutorial creates a snapshot via apply_updates. The snapshot contains the original content.
        # For the diff step, we need list_snapshots to return a path to a file with that original content.
        snap_content = 'def hello_world():\n    print("Hello, World!")\n'
        snap_file = self.test_root / "snap_for_diff.py"
        snap_file.write_text(snap_content)

        mock_apply.return_value = "001_ts"
        mock_list_snaps.return_value = [('001_ts', str(snap_file))]

        tutorial_file = Path("tutorial_example.py")
        
        # The tutorial should run in a clean state
        self.assertFalse(tutorial_file.exists())

        tutorial.run_tutorial()
        
        # Assertions
        mock_confirm.assert_called_once()
        self.assertGreaterEqual(mock_input.call_count, 5)
        
        # Step 1: apply_updates is called
        mock_apply.assert_called_once()
        prompt_arg = mock_apply.call_args[0][1]
        self.assertEqual(prompt_arg, "add a docstring to the hello_world function")
        
        # Step 3: diff is called
        mock_list_snaps.assert_called_once_with(tutorial_file)
        mock_diff.assert_called_once_with(tutorial_file, snap_file)
        
        # Step 4: restore is called
        mock_restore.assert_called_once_with(file_name='tutorial_example.py')
        
        # Check that flag file was created
        self.assertTrue(self.tutorial_flag_file.exists())
        
        # Check that tutorial file was cleaned up
        self.assertFalse(tutorial_file.exists(), "The tutorial example file should be deleted at the end.")

    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    @patch('aye.controller.tutorial.input', return_value="")
    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.apply_updates', side_effect=RuntimeError("Model failed"))
    @patch('aye.controller.tutorial.rprint')
    def test_run_tutorial_step1_error(self, mock_rprint, mock_apply, mock_sleep, mock_input, mock_confirm):
        tutorial_file = Path("tutorial_example.py")

        tutorial.run_tutorial()

        mock_rprint.assert_any_call("[red]An error occurred during the tutorial: Model failed[/red]")
        self.assertTrue(self.tutorial_flag_file.exists())

        # Check that tutorial file was cleaned up even on error
        self.assertFalse(tutorial_file.exists(), "The tutorial example file should be deleted on error.")
