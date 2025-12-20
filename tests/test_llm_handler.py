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
        mock_filter.assert_called_once_with(updated_files)
        mock_relative.assert_called_once()
        mock_apply.assert_called_once_with(updated_files, "prompt")
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
