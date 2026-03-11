"""Tests for the tutorial module.

Verifies the TutorialRunner class and helper functions.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from aye.controller.tutorial import (
    TutorialRunner,
    _print_step,
    STEP_DELAY,
    SIMULATE_THINK_DELAY,
    ORIGINAL_FILE_CONTENT,
    MODIFIED_FILE_CONTENT,
    SIMULATED_PROMPT,
)


class TestPrintStep(unittest.TestCase):
    """Tests for the _print_step helper function."""

    @patch('aye.controller.tutorial.console')
    @patch('builtins.input', return_value='')  # Simulate Enter key
    def test_print_step_basic(self, mock_input, mock_console):
        """Test basic step printing without command."""
        _print_step('Test Title', 'Test text')
        
        # Should print newline, panel, and wait for input
        self.assertTrue(mock_console.print.called)
        mock_input.assert_called_once()

    @patch('aye.controller.tutorial.console')
    @patch('aye.controller.tutorial.print_prompt', return_value='> ')
    @patch('builtins.input', return_value='')
    def test_print_step_with_command(self, mock_input, mock_print_prompt, mock_console):
        """Test step printing with simulated command."""
        _print_step('Title', 'Text', simulated_command='test cmd')
        
        # Should include the command in output
        calls = mock_console.print.call_args_list
        command_printed = any('test cmd' in str(c) for c in calls)
        self.assertTrue(command_printed)

    @patch('builtins.input', return_value='')
    def test_print_step_custom_console(self, mock_input):
        """Test step printing with custom console."""
        mock_console = MagicMock()
        _print_step('Title', 'Text', target_console=mock_console)
        
        self.assertTrue(mock_console.print.called)


class TestTutorialRunner(unittest.TestCase):
    """Tests for the TutorialRunner class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_console = MagicMock()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_cwd = Path.cwd()

    def tearDown(self):
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_init_default_console(self):
        """Test initialization with default console."""
        with patch('aye.controller.tutorial.console') as mock_console:
            runner = TutorialRunner()
            self.assertEqual(runner._console, mock_console)

    def test_init_custom_console(self):
        """Test initialization with custom console."""
        runner = TutorialRunner(target_console=self.mock_console)
        self.assertEqual(runner._console, self.mock_console)

    @patch('aye.controller.tutorial.Confirm.ask', return_value=False)
    def test_should_run_not_first_run_declined(self, mock_confirm):
        """Test that tutorial is skipped when user declines."""
        runner = TutorialRunner(target_console=self.mock_console)
        
        with patch.object(runner, '_mark_tutorial_complete'):
            result = runner._should_run(is_first_run=False)
        
        self.assertFalse(result)

    def test_should_run_first_run(self):
        """Test that tutorial runs automatically on first run."""
        runner = TutorialRunner(target_console=self.mock_console)
        
        result = runner._should_run(is_first_run=True)
        
        self.assertTrue(result)

    @patch('aye.controller.tutorial.Confirm.ask', return_value=True)
    def test_should_run_not_first_run_accepted(self, mock_confirm):
        """Test that tutorial runs when user accepts."""
        runner = TutorialRunner(target_console=self.mock_console)
        
        result = runner._should_run(is_first_run=False)
        
        self.assertTrue(result)

    def test_show_welcome(self):
        """Test welcome panel is displayed."""
        runner = TutorialRunner(target_console=self.mock_console)
        
        runner._show_welcome()
        
        self.mock_console.print.assert_called()
        # Check that Panel was passed to print
        call_args = self.mock_console.print.call_args_list
        self.assertTrue(any('Panel' in str(type(c[0][0]).__name__) for c in call_args if c[0]))

    @patch('aye.controller.tutorial.time.sleep')
    @patch('aye.controller.tutorial.TUTORIAL_FLAG_DIR')
    def test_setup_creates_temp_file(self, mock_flag_dir, mock_sleep):
        """Test that setup creates the tutorial temp file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            os.chdir(tmpdir)
            
            try:
                mock_flag_dir.mkdir = MagicMock()
                runner = TutorialRunner(target_console=self.mock_console)
                
                runner._setup()
                
                self.assertTrue(runner._setup_complete)
                self.assertTrue(runner._temp_file.exists())
                content = runner._temp_file.read_text()
                self.assertEqual(content, ORIGINAL_FILE_CONTENT)
            finally:
                os.chdir(self.original_cwd)

    def test_cleanup_removes_temp_file(self):
        """Test that cleanup removes the temp file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            os.chdir(tmpdir)
            
            try:
                runner = TutorialRunner(target_console=self.mock_console)
                runner._temp_file = Path('test_cleanup.py')
                runner._temp_file.write_text('test')
                runner._setup_complete = True
                
                with patch('aye.controller.tutorial.TUTORIAL_FLAG_FILE') as mock_flag:
                    with patch('aye.controller.tutorial.time.sleep'):
                        runner._cleanup()
                
                self.assertFalse(runner._temp_file.exists())
            finally:
                os.chdir(self.original_cwd)

    def test_cleanup_handles_missing_file(self):
        """Test that cleanup handles already-deleted temp file."""
        runner = TutorialRunner(target_console=self.mock_console)
        runner._temp_file = Path('nonexistent_file.py')
        runner._setup_complete = True
        
        with patch('aye.controller.tutorial.TUTORIAL_FLAG_FILE') as mock_flag:
            with patch('aye.controller.tutorial.time.sleep'):
                # Should not raise
                runner._cleanup()


class TestTutorialConstants(unittest.TestCase):
    """Tests for tutorial constants."""

    def test_step_delay_is_positive(self):
        """Test that STEP_DELAY is a positive number."""
        self.assertGreater(STEP_DELAY, 0)

    def test_simulate_think_delay_is_positive(self):
        """Test that SIMULATE_THINK_DELAY is a positive number."""
        self.assertGreater(SIMULATE_THINK_DELAY, 0)

    def test_original_content_is_valid_python(self):
        """Test that ORIGINAL_FILE_CONTENT is valid Python."""
        # Should not raise SyntaxError
        compile(ORIGINAL_FILE_CONTENT, '<string>', 'exec')

    def test_modified_content_is_valid_python(self):
        """Test that MODIFIED_FILE_CONTENT is valid Python."""
        # Should not raise SyntaxError
        compile(MODIFIED_FILE_CONTENT, '<string>', 'exec')

    def test_simulated_prompt_is_non_empty(self):
        """Test that SIMULATED_PROMPT is a non-empty string."""
        self.assertIsInstance(SIMULATED_PROMPT, str)
        self.assertTrue(len(SIMULATED_PROMPT) > 0)


class TestRunTutorialFunction(unittest.TestCase):
    """Tests for the run_tutorial function."""

    @patch('aye.controller.tutorial.TutorialRunner')
    def test_run_tutorial_creates_runner(self, mock_runner_class):
        """Test that run_tutorial creates and runs a TutorialRunner."""
        from aye.controller.tutorial import run_tutorial
        
        run_tutorial(is_first_run=True)
        
        mock_runner_class.assert_called_once()
        mock_runner_class.return_value.run.assert_called_once_with(True)

    @patch('aye.controller.tutorial.TutorialRunner')
    def test_run_tutorial_passes_is_first_run(self, mock_runner_class):
        """Test that is_first_run parameter is passed correctly."""
        from aye.controller.tutorial import run_tutorial
        
        run_tutorial(is_first_run=False)
        
        mock_runner_class.return_value.run.assert_called_once_with(False)


class TestRunFirstTimeTutorial(unittest.TestCase):
    """Tests for run_first_time_tutorial_if_needed function."""

    @patch('aye.controller.tutorial.TUTORIAL_FLAG_FILE')
    @patch('aye.controller.tutorial.run_tutorial')
    def test_runs_when_flag_missing(self, mock_run, mock_flag):
        """Test that tutorial runs when flag file doesn't exist."""
        from aye.controller.tutorial import run_first_time_tutorial_if_needed
        
        mock_flag.exists.return_value = False
        
        result = run_first_time_tutorial_if_needed()
        
        self.assertTrue(result)
        mock_run.assert_called_once_with(is_first_run=True)

    @patch('aye.controller.tutorial.TUTORIAL_FLAG_FILE')
    @patch('aye.controller.tutorial.run_tutorial')
    def test_skips_when_flag_exists(self, mock_run, mock_flag):
        """Test that tutorial is skipped when flag file exists."""
        from aye.controller.tutorial import run_first_time_tutorial_if_needed
        
        mock_flag.exists.return_value = True
        
        result = run_first_time_tutorial_if_needed()
        
        self.assertFalse(result)
        mock_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
