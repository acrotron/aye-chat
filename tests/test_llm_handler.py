import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock

from rich.padding import Padding

import aye.controller.llm_handler as llm_handler
from aye.model.models import LLMResponse, LLMSource


class TestLlmHandler(TestCase):
    def setUp(self):
        self.console = MagicMock()
        self.conf = SimpleNamespace(root=Path('.'))
        self.tmpdir = tempfile.TemporaryDirectory()
        self.chat_id_file = Path(self.tmpdir.name) / "chat_id.tmp"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch('aye.controller.llm_handler.print_assistant_response')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    def test_process_llm_response_with_updates(self, mock_print_files, mock_apply, mock_relative, mock_filter, mock_print_summary):
        updated_files = [{"file_name": "file1.py", "file_content": "content"}]
        llm_resp = LLMResponse(
            summary="summary",
            updated_files=updated_files,
            chat_id=123,
            source=LLMSource.API
        )

        mock_filter.return_value = updated_files
        mock_relative.return_value = updated_files

        new_chat_id = llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=self.chat_id_file
        )

        self.assertEqual(new_chat_id, 123)
        self.assertTrue(self.chat_id_file.exists())
        self.assertEqual(self.chat_id_file.read_text(), "123")

        mock_print_summary.assert_called_once_with("summary")
        mock_filter.assert_called_once_with(updated_files, self.conf.root)
        mock_relative.assert_called_once()
        mock_apply.assert_called_once_with(updated_files, "prompt", root=self.conf.root)
        mock_print_files.assert_called_once_with(self.console, ["file1.py"])

    @patch('aye.controller.llm_handler.print_assistant_response')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.print_no_files_changed')
    def test_process_llm_response_no_updates(self, mock_print_no_change, mock_relative, mock_filter, mock_print_summary):
        llm_resp = LLMResponse(
            summary="summary",
            updated_files=[{"file_name": "f1", "file_content": "c1"}],
            chat_id=None,  # No chat_id
            source=LLMSource.LOCAL
        )

        mock_filter.return_value = []  # Simulate files being unchanged
        mock_relative.return_value = []  # It should return an empty list if given one

        new_chat_id = llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=self.chat_id_file
        )

        self.assertIsNone(new_chat_id)
        self.assertFalse(self.chat_id_file.exists())

        mock_print_summary.assert_called_once_with("summary")
        mock_filter.assert_called_once()
        mock_relative.assert_called_once()  # It's called before the check
        mock_print_no_change.assert_called_once_with(self.console)

    @patch('aye.controller.llm_handler.rprint')
    def test_process_llm_response_apply_error(self, mock_rprint):
        updated_files = [{"file_name": "file1.py", "file_content": "content"}]
        llm_resp = LLMResponse(summary="s", updated_files=updated_files)

        with patch('aye.controller.llm_handler.filter_unchanged_files', return_value=updated_files), \
             patch('aye.controller.llm_handler.make_paths_relative', return_value=updated_files), \
             patch('aye.controller.llm_handler.apply_updates', side_effect=Exception("Disk full")):

            llm_handler.process_llm_response(llm_resp, self.conf, self.console, "prompt")

            mock_rprint.assert_called_with("[red]Error applying updates:[/] Disk full")

    @patch('aye.controller.llm_handler.print_error')
    @patch('traceback.print_exc')
    def test_handle_llm_error_403(self, mock_traceback, mock_print_error):
        mock_response = MagicMock()
        mock_response.status_code = 403
        exc = Exception("Auth error")
        exc.response = mock_response

        llm_handler.handle_llm_error(exc)

        mock_traceback.assert_called_once()
        mock_print_error.assert_called_once()
        arg = mock_print_error.call_args[0][0]
        self.assertIsInstance(arg, Exception)
        self.assertIn("Unauthorized", str(arg))

    @patch('aye.controller.llm_handler.print_error')
    def test_handle_llm_error_generic(self, mock_print_error):
        exc = ValueError("Generic error")

        llm_handler.handle_llm_error(exc)

        mock_print_error.assert_called_once_with(exc)

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=False)
    @patch('aye.controller.llm_handler.get_user_config', return_value='off')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    def test_restore_tip_printed_once_per_session_when_never_used_restore(
        self,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        updated_files = [{"file_name": "file1.py", "file_content": "content"}]
        llm_resp = LLMResponse(
            summary=None,
            updated_files=updated_files,
            chat_id=None,
            source=LLMSource.LOCAL,
        )

        mock_filter.return_value = updated_files
        mock_relative.return_value = updated_files

        # First update: should print the tip.
        llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=None,
        )

        # Second update in same session: should NOT print tip again.
        llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=None,
        )

        # The restore tip uses console.print(Padding(...))
        self.assertEqual(self.console.print.call_count, 1)
        arg0 = self.console.print.call_args[0][0]
        self.assertIsInstance(arg0, Padding)
        self.assertIn("roll back", str(arg0.renderable))
        self.assertIn("restore", str(arg0.renderable))

        # Per-session gate is stored on conf
        self.assertTrue(getattr(self.conf, "_restore_tip_shown", False))

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=False)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    def test_restore_tip_not_printed_when_restore_used_globally(
        self,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        updated_files = [{"file_name": "file1.py", "file_content": "content"}]
        llm_resp = LLMResponse(
            summary=None,
            updated_files=updated_files,
            chat_id=None,
            source=LLMSource.LOCAL,
        )

        mock_filter.return_value = updated_files
        mock_relative.return_value = updated_files

        llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=None,
        )

        # Global gate says user has used restore -> no tip
        self.console.print.assert_not_called()
        self.assertFalse(hasattr(self.conf, "_restore_tip_shown"))

    @patch('aye.controller.llm_handler.print_assistant_response')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    def test_process_llm_response_empty_updated_files(self, mock_filter, mock_print_summary):
        """Empty updated_files returns early without calling filter."""
        llm_resp = LLMResponse(
            summary="summary",
            updated_files=[],
            chat_id=42,
            source=LLMSource.API
        )

        new_chat_id = llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
            chat_id_file=self.chat_id_file,
        )

        self.assertEqual(new_chat_id, 42)
        self.assertEqual(self.chat_id_file.read_text(), "42")
        mock_print_summary.assert_called_once_with("summary")
        mock_filter.assert_not_called()

    @patch('aye.controller.llm_handler.print_assistant_response')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    def test_process_llm_response_none_updated_files(self, mock_filter, mock_print_summary):
        """None updated_files is treated as empty."""
        llm_resp = LLMResponse(
            summary="done",
            updated_files=None,
            chat_id=None,
            source=LLMSource.LOCAL
        )

        new_chat_id = llm_handler.process_llm_response(
            response=llm_resp,
            conf=self.conf,
            console=self.console,
            prompt="prompt",
        )

        self.assertIsNone(new_chat_id)
        mock_filter.assert_not_called()

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=False)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.rprint')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    @patch('aye.controller.llm_handler.check_files_against_ignore_patterns')
    @patch('aye.controller.llm_handler.is_strict_mode_enabled', return_value=True)
    @patch('aye.controller.llm_handler.format_ignored_files_warning', return_value="[yellow]Warning[/]")
    def test_process_llm_response_ignored_files_strict_mode(
        self,
        _mock_format_warning,
        _mock_strict,
        mock_check_ignore,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        mock_rprint,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        """In strict mode, only allowed files are written."""
        all_files = [
            {"file_name": "allowed.py", "file_content": "ok"},
            {"file_name": "secret.env", "file_content": "SECRET=x"},
        ]
        allowed_only = [{"file_name": "allowed.py", "file_content": "ok"}]
        ignored_only = [{"file_name": "secret.env", "file_content": "SECRET=x"}]

        mock_filter.return_value = all_files
        mock_relative.return_value = all_files
        mock_check_ignore.return_value = (allowed_only, ignored_only)

        llm_resp = LLMResponse(summary=None, updated_files=all_files)

        llm_handler.process_llm_response(
            llm_resp, self.conf, self.console, "prompt"
        )

        # Warning printed
        mock_rprint.assert_any_call("[yellow]Warning[/]")
        # Only allowed file passed to apply_updates
        mock_apply.assert_called_once_with(allowed_only, "prompt", root=self.conf.root)
        mock_print_files.assert_called_once_with(self.console, ["allowed.py"])

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=False)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.rprint')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    @patch('aye.controller.llm_handler.check_files_against_ignore_patterns')
    @patch('aye.controller.llm_handler.is_strict_mode_enabled', return_value=True)
    @patch('aye.controller.llm_handler.format_ignored_files_warning', return_value="[yellow]Warning[/]")
    def test_process_llm_response_all_files_ignored_strict_mode(
        self,
        _mock_format_warning,
        _mock_strict,
        mock_check_ignore,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        mock_rprint,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        """In strict mode, if all files are ignored, nothing is written."""
        files = [{"file_name": "secret.env", "file_content": "SECRET=x"}]

        mock_filter.return_value = files
        mock_relative.return_value = files
        mock_check_ignore.return_value = ([], files)  # All ignored

        llm_resp = LLMResponse(summary=None, updated_files=files)

        result = llm_handler.process_llm_response(
            llm_resp, self.conf, self.console, "prompt"
        )

        mock_rprint.assert_any_call("[yellow]Warning[/]")
        mock_apply.assert_not_called()
        mock_print_files.assert_not_called()
        self.assertIsNone(result)

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=False)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.rprint')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates')
    @patch('aye.controller.llm_handler.print_files_updated')
    @patch('aye.controller.llm_handler.check_files_against_ignore_patterns')
    @patch('aye.controller.llm_handler.is_strict_mode_enabled', return_value=False)
    @patch('aye.controller.llm_handler.format_ignored_files_warning', return_value="[yellow]Warning[/]")
    def test_process_llm_response_ignored_files_non_strict_mode(
        self,
        _mock_format_warning,
        _mock_strict,
        mock_check_ignore,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        mock_rprint,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        """In non-strict mode, ignored files are warned about but still written."""
        files = [
            {"file_name": "app.py", "file_content": "code"},
            {"file_name": "secret.env", "file_content": "SECRET=x"},
        ]

        mock_filter.return_value = files
        mock_relative.return_value = files
        mock_check_ignore.return_value = (
            [files[0]],  # allowed
            [files[1]],  # ignored
        )

        llm_resp = LLMResponse(summary=None, updated_files=files)

        llm_handler.process_llm_response(
            llm_resp, self.conf, self.console, "prompt"
        )

        # Warning printed
        mock_rprint.assert_any_call("[yellow]Warning[/]")
        # All files still passed to apply_updates (non-strict)
        mock_apply.assert_called_once_with(files, "prompt", root=self.conf.root)

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=True)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates', return_value="001_20240101T000000")
    @patch('aye.controller.llm_handler.print_files_updated')
    @patch('aye.controller.llm_handler.get_diff_base_for_file')
    @patch('aye.controller.llm_handler.show_diff')
    def test_process_llm_response_autodiff_enabled(
        self,
        mock_show_diff,
        mock_get_diff_base,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        """When autodiff is enabled, show_diff is called for each updated file."""
        updated_files = [{"file_name": "file1.py", "file_content": "content"}]
        mock_filter.return_value = updated_files
        mock_relative.return_value = updated_files
        mock_get_diff_base.return_value = ("/snapshots/file1.py", False)

        llm_resp = LLMResponse(summary=None, updated_files=updated_files)

        llm_handler.process_llm_response(
            llm_resp, self.conf, self.console, "prompt"
        )

        mock_get_diff_base.assert_called_once_with(
            "001_20240101T000000", self.conf.root / "file1.py"
        )
        mock_show_diff.assert_called_once_with(
            self.conf.root / "file1.py", Path("/snapshots/file1.py")
        )

    @patch('aye.controller.llm_handler.is_autodiff_enabled', return_value=True)
    @patch('aye.controller.llm_handler.get_user_config', return_value='on')
    @patch('aye.controller.llm_handler.filter_unchanged_files')
    @patch('aye.controller.llm_handler.make_paths_relative')
    @patch('aye.controller.llm_handler.apply_updates', return_value="001_20240101T000000")
    @patch('aye.controller.llm_handler.print_files_updated')
    @patch('aye.controller.llm_handler.get_diff_base_for_file', return_value=None)
    @patch('aye.controller.llm_handler.show_diff')
    def test_process_llm_response_autodiff_no_diff_base(
        self,
        mock_show_diff,
        _mock_get_diff_base,
        mock_print_files,
        mock_apply,
        mock_relative,
        mock_filter,
        _mock_get_user_config,
        _mock_autodiff,
    ):
        """When diff base is None (new file), show_diff is not called."""
        updated_files = [{"file_name": "new_file.py", "file_content": "content"}]
        mock_filter.return_value = updated_files
        mock_relative.return_value = updated_files

        llm_resp = LLMResponse(summary=None, updated_files=updated_files)

        llm_handler.process_llm_response(
            llm_resp, self.conf, self.console, "prompt"
        )

        mock_show_diff.assert_not_called()
