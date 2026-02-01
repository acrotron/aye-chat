"""Tests for write_validator module.

Tests the fix for https://github.com/acrotron/aye-chat/issues/50
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import sys

# This setup allows the test to be run directly or with a test runner.
try:
    from aye.model.write_validator import (
        check_files_against_ignore_patterns,
        is_strict_mode_enabled,
        format_ignored_files_warning,
        BLOCK_IGNORED_WRITES_KEY,
    )
except ImportError:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from aye.model.write_validator import (
        check_files_against_ignore_patterns,
        is_strict_mode_enabled,
        format_ignored_files_warning,
        BLOCK_IGNORED_WRITES_KEY,
    )


class TestCheckFilesAgainstIgnorePatterns(unittest.TestCase):
    """Test cases for check_files_against_ignore_patterns function."""

    def setUp(self):
        """Set up a temporary directory with ignore files."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up the temporary directory."""
        self.temp_dir.cleanup()

    def test_no_ignore_files_allows_all(self):
        """All files should be allowed when no ignore files exist."""
        files = [
            {"file_name": "main.py", "file_content": "print('hello')"},
            {"file_name": "app.jsx", "file_content": "export default App;"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 2)
        self.assertEqual(len(ignored), 0)

    def test_gitignore_pattern_filters_files(self):
        """Files matching .gitignore patterns should be marked as ignored."""
        # Create .gitignore with *.jsx pattern
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("*.jsx\n")

        files = [
            {"file_name": "main.py", "file_content": "print('hello')"},
            {"file_name": "app.jsx", "file_content": "export default App;"},
            {"file_name": "components/Button.jsx", "file_content": "export Button;"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["file_name"], "main.py")
        self.assertEqual(len(ignored), 2)
        ignored_names = [f["file_name"] for f in ignored]
        self.assertIn("app.jsx", ignored_names)
        self.assertIn("components/Button.jsx", ignored_names)

    def test_ayeignore_pattern_filters_files(self):
        """Files matching .ayeignore patterns should be marked as ignored."""
        # Create .ayeignore with specific pattern
        ayeignore = self.root_path / ".ayeignore"
        ayeignore.write_text("secret.txt\n*.env\n")

        files = [
            {"file_name": "main.py", "file_content": "print('hello')"},
            {"file_name": "secret.txt", "file_content": "api_key=xxx"},
            {"file_name": "prod.env", "file_content": "DB_PASSWORD=xxx"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["file_name"], "main.py")
        self.assertEqual(len(ignored), 2)

    def test_default_ignore_set_filters_files(self):
        """Files in default ignore directories should be marked as ignored."""
        files = [
            {"file_name": "main.py", "file_content": "print('hello')"},
            {"file_name": "node_modules/package/index.js", "file_content": "module.exports = {};"},
            {"file_name": "__pycache__/main.cpython-39.pyc", "file_content": "binary"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["file_name"], "main.py")
        self.assertEqual(len(ignored), 2)

    def test_combined_gitignore_and_ayeignore(self):
        """Both .gitignore and .ayeignore patterns should be applied."""
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("*.log\n")

        ayeignore = self.root_path / ".ayeignore"
        ayeignore.write_text("*.tmp\n")

        files = [
            {"file_name": "main.py", "file_content": "print('hello')"},
            {"file_name": "error.log", "file_content": "error message"},
            {"file_name": "cache.tmp", "file_content": "temp data"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(len(ignored), 2)

    def test_empty_file_list(self):
        """Empty file list should return empty results."""
        allowed, ignored = check_files_against_ignore_patterns([], self.root_path)

        self.assertEqual(allowed, [])
        self.assertEqual(ignored, [])

    def test_directory_pattern(self):
        """Directory patterns should match files within that directory."""
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("dist/\nbuild/\n")

        files = [
            {"file_name": "src/main.py", "file_content": "print('hello')"},
            {"file_name": "dist/bundle.js", "file_content": "bundled code"},
            {"file_name": "build/output.css", "file_content": "compiled css"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["file_name"], "src/main.py")
        self.assertEqual(len(ignored), 2)

    def test_backslash_paths_normalized(self):
        """Windows-style backslash paths should be handled correctly."""
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("*.jsx\n")

        files = [
            {"file_name": "src\\components\\App.jsx", "file_content": "export App;"},
        ]

        allowed, ignored = check_files_against_ignore_patterns(files, self.root_path)

        self.assertEqual(len(allowed), 0)
        self.assertEqual(len(ignored), 1)


class TestIsStrictModeEnabled(unittest.TestCase):
    """Test cases for is_strict_mode_enabled function."""

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_off_by_default(self, mock_config):
        """Strict mode should be off by default."""
        mock_config.return_value = "off"

        result = is_strict_mode_enabled()

        self.assertFalse(result)
        mock_config.assert_called_with(BLOCK_IGNORED_WRITES_KEY, "off")

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_on(self, mock_config):
        """Strict mode should be enabled when set to 'on'."""
        mock_config.return_value = "on"

        result = is_strict_mode_enabled()

        self.assertTrue(result)

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_true(self, mock_config):
        """Strict mode should be enabled when set to 'true'."""
        mock_config.return_value = "true"

        result = is_strict_mode_enabled()

        self.assertTrue(result)

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_yes(self, mock_config):
        """Strict mode should be enabled when set to 'yes'."""
        mock_config.return_value = "yes"

        result = is_strict_mode_enabled()

        self.assertTrue(result)

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_1(self, mock_config):
        """Strict mode should be enabled when set to '1'."""
        mock_config.return_value = "1"

        result = is_strict_mode_enabled()

        self.assertTrue(result)

    @patch('aye.model.write_validator.get_user_config')
    def test_strict_mode_case_insensitive(self, mock_config):
        """Strict mode setting should be case insensitive."""
        mock_config.return_value = "ON"

        result = is_strict_mode_enabled()

        self.assertTrue(result)


class TestFormatIgnoredFilesWarning(unittest.TestCase):
    """Test cases for format_ignored_files_warning function."""

    def test_warning_non_strict_mode(self):
        """Warning in non-strict mode should mention how to enable strict mode."""
        ignored_files = [
            {"file_name": "app.jsx", "file_content": "content"},
        ]

        warning = format_ignored_files_warning(ignored_files, strict_mode=False)

        self.assertIn("app.jsx", warning)
        self.assertIn("Warning", warning)
        self.assertIn("block_ignored_file_writes=on", warning)
        self.assertIn("AYE_BLOCK_IGNORED_FILE_WRITES=on", warning)

    def test_warning_strict_mode(self):
        """Warning in strict mode should indicate files were blocked."""
        ignored_files = [
            {"file_name": "app.jsx", "file_content": "content"},
        ]

        warning = format_ignored_files_warning(ignored_files, strict_mode=True)

        self.assertIn("app.jsx", warning)
        self.assertIn("Blocked", warning)
        self.assertIn("not written", warning)

    def test_warning_multiple_files(self):
        """Warning should list all ignored files."""
        ignored_files = [
            {"file_name": "app.jsx", "file_content": "content1"},
            {"file_name": "Button.jsx", "file_content": "content2"},
        ]

        warning = format_ignored_files_warning(ignored_files, strict_mode=False)

        self.assertIn("app.jsx", warning)
        self.assertIn("Button.jsx", warning)


class TestLlmHandlerIntegration(unittest.TestCase):
    """Integration tests for llm_handler with write validation."""

    def setUp(self):
        """Set up a temporary directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.is_strict_mode_enabled')
    def test_strict_mode_blocks_ignored_files(self, mock_strict, mock_apply):
        """In strict mode, ignored files should not be written."""
        from unittest.mock import MagicMock
        from aye.controller.llm_handler import process_llm_response
        from aye.model.models import LLMResponse
        from rich.console import Console

        # Create .gitignore
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("*.jsx\n")

        # Setup mocks
        mock_strict.return_value = True
        mock_apply.return_value = "snapshot_id"

        # Create response with mixed files
        response = LLMResponse(
            summary="Updated files",
            updated_files=[
                {"file_name": "main.py", "file_content": "print('hello')"},
                {"file_name": "app.jsx", "file_content": "export App;"},
            ]
        )

        # Create mock config - root must be a Path object
        conf = MagicMock()
        conf.root = self.root_path

        console = Console(force_terminal=True)

        # Process response
        process_llm_response(response, conf, console, "test prompt")

        # Verify only main.py was written
        mock_apply.assert_called_once()
        written_files = mock_apply.call_args[0][0]
        self.assertEqual(len(written_files), 1)
        self.assertEqual(written_files[0]["file_name"], "main.py")

    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.is_strict_mode_enabled')
    def test_non_strict_mode_writes_all_files(self, mock_strict, mock_apply):
        """In non-strict mode, all files should be written (with warning)."""
        from unittest.mock import MagicMock
        from aye.controller.llm_handler import process_llm_response
        from aye.model.models import LLMResponse
        from rich.console import Console

        # Create .gitignore
        gitignore = self.root_path / ".gitignore"
        gitignore.write_text("*.jsx\n")

        # Setup mocks
        mock_strict.return_value = False
        mock_apply.return_value = "snapshot_id"

        # Create response with mixed files
        response = LLMResponse(
            summary="Updated files",
            updated_files=[
                {"file_name": "main.py", "file_content": "print('hello')"},
                {"file_name": "app.jsx", "file_content": "export App;"},
            ]
        )

        # Create mock config - root must be a Path object
        conf = MagicMock()
        conf.root = self.root_path

        console = Console(force_terminal=True)

        # Process response
        process_llm_response(response, conf, console, "test prompt")

        # Verify both files were written
        mock_apply.assert_called_once()
        written_files = mock_apply.call_args[0][0]
        self.assertEqual(len(written_files), 2)


if __name__ == '__main__':
    unittest.main()
