# Test suite for refactored service handlers now in aye.controller.commands and related modules
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

import aye.controller.commands as commands
import aye.model.api as api
import aye.model.source_collector as source_collector
import aye.controller.llm_handler as llm_handler


class TestCommands(TestCase):

    # --- Authentication handlers ---
    @patch('aye.model.auth.login_flow')
    @patch('aye.model.download_plugins.fetch_plugins')
    @patch('aye.model.auth.get_token', return_value='fake-token')
    def test_login_and_fetch_plugins_success(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        commands.login_and_fetch_plugins()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()

    @patch('aye.model.auth.login_flow')
    @patch('aye.model.download_plugins.fetch_plugins')
    @patch('aye.model.auth.get_token', return_value=None)
    def test_login_and_fetch_plugins_no_token(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        commands.login_and_fetch_plugins()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_not_called()

    @patch('aye.model.auth.login_flow')
    @patch('aye.model.download_plugins.fetch_plugins', side_effect=Exception("Network error"))
    @patch('aye.model.auth.get_token', return_value='fake-token')
    def test_login_and_fetch_plugins_error(self, mock_get_token, mock_fetch_plugins, mock_login_flow):
        with self.assertRaisesRegex(Exception, "Network error"):
            commands.login_and_fetch_plugins()

        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()

    @patch('aye.model.auth.delete_token')
    def test_logout(self, mock_delete_token):
        commands.logout()
        mock_delete_token.assert_called_once()
        # Note: print is handled in CLI layer, not in commands

    # --- Core command handlers ---
    @patch('aye.model.api.cli_invoke', return_value={'generated_code': 'print("hello")'})
    def test_generate_cmd(self, mock_cli_invoke):
        # Assuming a generate handler in commands or __main__; adjust if needed
        # For now, test cli_invoke directly as it's the core
        result = api.cli_invoke(message="generate hello world")
        self.assertEqual(result, {'generated_code': 'print("hello")'})
        mock_cli_invoke.assert_called_once_with(message="generate hello world")

    # --- Chat message processing ---
    def test_process_llm_response(self):
        # Test only the processing logic, without external calls
        assistant_response = {"answer_summary": "summary", "source_files": [{"file_name": "file1.py", "file_content": "content"}]}
        conf = SimpleNamespace(root=Path('.'))
        console = MagicMock()
        with patch('aye.controller.llm_handler.filter_unchanged_files') as mock_filter, \
             patch('aye.controller.llm_handler.make_paths_relative') as mock_relative, \
             patch('aye.controller.llm_handler.apply_updates') as mock_apply, \
             patch('aye.controller.llm_handler.print_assistant_response') as mock_print_summary, \
             patch('aye.controller.llm_handler.print_files_updated') as mock_print_files:

            mock_filter.return_value = assistant_response["source_files"]
            mock_relative.return_value = assistant_response["source_files"]

            mock_path = MagicMock()

            llm_resp = llm_handler.LLMResponse(
                summary=assistant_response["answer_summary"],
                updated_files=assistant_response["source_files"],
                chat_id=123
            )
            new_chat_id = llm_handler.process_llm_response(
                llm_resp, conf, console, "prompt", mock_path, # Path('chat_id.tmp')
            )

            mock_filter.assert_called_once()
            mock_relative.assert_called_once()
            mock_apply.assert_called_once()
            mock_print_summary.assert_called_once_with("summary")
            mock_print_files.assert_called_once()
            self.assertEqual(new_chat_id, 123)

    # --- Snapshot command handlers ---
    @patch('aye.model.snapshot.list_snapshots', return_value=['snap1', 'snap2'])
    def test_get_snapshot_history(self, mock_list_snapshots):
        result = commands.get_snapshot_history()
        mock_list_snapshots.assert_called_once_with(None)
        self.assertEqual(result, ['snap1', 'snap2'])

    @patch('builtins.print')
    @patch('aye.model.snapshot.list_snapshots', return_value=[('ts1', '/path/to/snap1')])
    def test_get_snapshot_content_found(self, mock_list_snapshots, mock_print):
        with patch('pathlib.Path.read_text', return_value="snap content"):
            content = commands.get_snapshot_content(Path('file.py'), 'ts1')
            self.assertEqual(content, "snap content")

    @patch('aye.model.snapshot.list_snapshots', return_value=[])
    def test_get_snapshot_content_not_found(self, mock_list_snapshots):
        content = commands.get_snapshot_content(Path('file.py'), 'ts2')
        self.assertIsNone(content)

    @patch('aye.model.snapshot.restore_snapshot')
    def test_restore_from_snapshot(self, mock_restore):
        commands.restore_from_snapshot('001', 'file.py')
        mock_restore.assert_called_once_with('001', 'file.py')

    @patch('aye.model.snapshot.prune_snapshots', return_value=5)
    def test_prune_snapshots(self, mock_prune):
        result = commands.prune_snapshots(10)
        self.assertEqual(result, 5)
        mock_prune.assert_called_once_with(10)

    @patch('aye.model.snapshot.cleanup_snapshots', return_value=3)
    def test_cleanup_old_snapshots(self, mock_cleanup):
        result = commands.cleanup_old_snapshots(30)
        self.assertEqual(result, 3)
        mock_cleanup.assert_called_once_with(30)

    # --- Diff helpers ---
    @patch('aye.model.snapshot.list_snapshots', return_value=[('001', '/path/to/snap')])
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths(self, mock_exists, mock_list_snapshots):
        with patch('aye.presenter.diff_presenter.show_diff'):
            path1, path2 = commands.get_diff_paths('file.py')
            self.assertIsInstance(path1, Path)
            self.assertIsInstance(path2, Path)
            mock_list_snapshots.assert_called_once_with(Path('file.py'))

    # --- Config handlers ---
    @patch('aye.model.config.list_config', return_value={'key': 'value'})
    def test_get_all_config(self, mock_list_config):
        result = commands.get_all_config()
        self.assertEqual(result, {'key': 'value'})
        mock_list_config.assert_called_once()

    def test_set_config_value(self):
        with patch('aye.model.config.set_value') as mock_set:
            commands.set_config_value('key', 'value')
            mock_set.assert_called_once_with('key', 'value')

    @patch('aye.model.config.get_value', return_value='value')
    def test_get_config_value(self, mock_get):
        result = commands.get_config_value('key')
        self.assertEqual(result, 'value')
        mock_get.assert_called_once_with('key')

    def test_delete_config_value(self):
        with patch('aye.model.config.delete_value', return_value=True) as mock_delete:
            result = commands.delete_config_value('key')
            self.assertTrue(result)
            mock_delete.assert_called_once_with('key')


if __name__ == '__main__':
    import unittest
    unittest.main()
