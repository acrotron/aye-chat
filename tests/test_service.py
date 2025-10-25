import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

import aye.service as service


class TestService(TestCase):

    # --- Authentication handlers ---
    @patch('aye.service.rprint')
    @patch('aye.service.fetch_plugins')
    @patch('aye.service.get_token', return_value='fake-token')
    @patch('aye.service.login_flow')
    def test_handle_login_success(self, mock_login_flow, mock_get_token, mock_fetch_plugins, mock_rprint):
        service.handle_login()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()
        # In the success case for handle_login, no output is printed.
        # The original assertion was syntactically incorrect, causing a TypeError.
        mock_rprint.assert_not_called()

    @patch('aye.service.rprint')
    @patch('aye.service.fetch_plugins')
    @patch('aye.service.get_token', return_value=None)
    @patch('aye.service.login_flow')
    def test_handle_login_no_token(self, mock_login_flow, mock_get_token, mock_fetch_plugins, mock_rprint):
        service.handle_login()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_not_called()
        mock_rprint.assert_called_with("[yellow]No token found - skipping plugin download[/]")

    @patch('aye.service.rprint')
    @patch('aye.service.fetch_plugins', side_effect=Exception("Network error"))
    @patch('aye.service.get_token', return_value='fake-token')
    @patch('aye.service.login_flow')
    def test_handle_login_plugin_error(self, mock_login_flow, mock_get_token, mock_fetch_plugins, mock_rprint):
        service.handle_login()
        mock_login_flow.assert_called_once()
        mock_get_token.assert_called_once()
        mock_fetch_plugins.assert_called_once()
        mock_rprint.assert_called_with("[red]Error: Could not download plugins - Network error[/]")

    @patch('aye.service.rprint')
    @patch('aye.service.delete_token')
    def test_handle_logout(self, mock_delete_token, mock_rprint):
        service.handle_logout()
        mock_delete_token.assert_called_once()
        mock_rprint.assert_called_with("🔐 Token removed.")

    # --- Core command handlers ---
    @patch('aye.service.rprint')
    @patch('aye.service.cli_invoke', return_value={'generated_code': 'print("hello")'})
    def test_handle_generate_cmd(self, mock_cli_invoke, mock_rprint):
        service.handle_generate_cmd("generate hello world")
        mock_cli_invoke.assert_called_once_with(message="generate hello world")
        mock_rprint.assert_called_with('print("hello")')


    # --- Chat message processing ---
    @patch('aye.service.cli_invoke')
    @patch('aye.service.collect_sources', return_value={'file.py': 'content'})
    def test_process_chat_message(self, mock_collect_sources, mock_cli_invoke):
        assistant_response = {"answer_summary": "summary", "source_files": ["file1.py"]}
        mock_api_response = {"assistant_response": json.dumps(assistant_response), "chat_id": 123}
        mock_cli_invoke.return_value = mock_api_response
        result = service.process_chat_message("prompt", 100, Path('.'), "*.py", "model")
        mock_collect_sources.assert_called_once_with(Path('.'), "*.py")
        mock_cli_invoke.assert_called_once_with(message="prompt", chat_id=100, source_files={'file.py': 'content'}, model="model")
        self.assertEqual(result['summary'], "summary")
        self.assertEqual(result['new_chat_id'], 123)
        self.assertEqual(result['updated_files'], ["file1.py"])

    # --- Snapshot command handlers ---
    @patch('builtins.print')
    @patch('aye.service.list_snapshots', return_value=['snap1', 'snap2'])
    def test_handle_history_cmd_with_snapshots(self, mock_list_snapshots, mock_print):
        service.handle_history_cmd(None)
        mock_list_snapshots.assert_called_once_with(None)
        mock_print.assert_has_calls([call('snap1'), call('snap2')])


    @patch('builtins.print')
    @patch('aye.service.rprint')
    @patch('aye.service.list_snapshots')
    def test_handle_snap_show_cmd_found(self,
                                        mock_list_snapshots,
                                        mock_rprint,
                                        mock_print):
        """
        Verify that ``handle_snap_show_cmd`` prints the contents of the
        snapshot when the requested timestamp exists.
        """
        # -------------------------------------------------------------
        # 1️⃣  list_snapshots returns a single (ts, path‑as‑str) tuple
        # -------------------------------------------------------------
        mock_list_snapshots.return_value = [('ts1', '/path/to/snap1')]

        # -------------------------------------------------------------
        # 2️⃣  Build a fake Path object **only** for the snapshot file.
        #    All other Path() calls fall back to the real implementation.
        # -------------------------------------------------------------
        fake_snapshot_path = MagicMock(spec=Path)
        fake_snapshot_path.read_text.return_value = "snap content"

        def path_ctor(arg):
            # The code under test calls Path(snap_path) with the string above.
            if arg == '/path/to/snap1':
                return fake_snapshot_path
            # Anything else (e.g. the ``file`` argument we pass) should be a real Path.
            return Path(arg)

        with patch('aye.service.Path', side_effect=path_ctor):
            service.handle_snap_show_cmd(Path('file.py'), 'ts1')

        # -------------------------------------------------------------
        # 3️⃣  Assertions
        # -------------------------------------------------------------
        mock_list_snapshots.assert_called_once_with(Path('file.py'))
        mock_print.assert_called_once_with("snap content")
        # ``rprint`` must not be called because the snapshot is found.
        mock_rprint.assert_not_called()

    @patch('aye.service.rprint')
    @patch('aye.service.list_snapshots')
    def test_handle_snap_show_cmd_not_found(self,
                                            mock_list_snapshots,
                                            mock_rprint):
        """
        When the requested timestamp is not present, the function should
        call ``rprint`` with the error message and must **not** touch the
        filesystem.
        """
        mock_list_snapshots.return_value = [('ts1', '/path/to/snap1')]

        service.handle_snap_show_cmd(Path('file.py'), 'ts2')

        mock_list_snapshots.assert_called_once_with(Path('file.py'))
        mock_rprint.assert_called_once_with("Snapshot not found.", err=True)

    @patch('aye.service.rprint')
    @patch('aye.service.list_snapshots')
    def test_handle_snap_show_cmd_not_found_2(self, mock_list_snapshots, mock_rprint):
        file_path = Path('file.py')
        mock_list_snapshots.return_value = [('ts1', '/path/to/snap1')]
        service.handle_snap_show_cmd(file_path, 'ts2')
        mock_rprint.assert_called_with("Snapshot not found.", err=True)

    @patch('aye.service.rprint')
    @patch('aye.service.restore_snapshot')
    def test_handle_restore_cmd_success(self, mock_restore, mock_rprint):
        service.handle_restore_cmd('001', 'file.py')
        mock_restore.assert_called_once_with('001', 'file.py')
        mock_rprint.assert_called_with("✅ File 'file.py' restored to 001")

    @patch('aye.service.rprint')
    @patch('aye.service.restore_snapshot', side_effect=ValueError("Not found"))
    def test_handle_restore_cmd_error(self, mock_restore, mock_rprint):
        service.handle_restore_cmd('999', None)
        mock_restore.assert_called_once_with('999', None)
        mock_rprint.assert_called_with("Error: Not found", err=True)

    @patch('aye.service.rprint')
    @patch('aye.service.prune_snapshots', return_value=5)
    def test_handle_prune_cmd(self, mock_prune, mock_rprint):
        service.handle_prune_cmd(10)
        mock_prune.assert_called_once_with(10)
        mock_rprint.assert_called_with("✅ 5 snapshots deleted. 10 most recent snapshots kept.")

    @patch('aye.service.rprint')
    @patch('aye.service.cleanup_snapshots', return_value=3)
    def test_handle_cleanup_cmd(self, mock_cleanup, mock_rprint):
        service.handle_cleanup_cmd(30)
        mock_cleanup.assert_called_once_with(30)
        mock_rprint.assert_called_with("✅ 3 snapshots older than 30 days deleted.")

    # --- Diff and file helpers ---
    @patch('subprocess.run')
    def test_is_valid_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(service._is_valid_command('ls'))
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(service._is_valid_command('nonexistent'))

    def XXXtest_filter_unchanged_files(self):
        updated_files = [
            {'file_name': 'new.txt', 'file_content': 'new'},
            {'file_name': 'changed.txt', 'file_content': 'changed'},
            {'file_name': 'same.txt', 'file_content': 'same'},
        ]
        path_map = {
            'new.txt': MagicMock(spec=Path, **{'exists.return_value': False}),
            'changed.txt': MagicMock(spec=Path, **{'exists.return_value': True, 'read_text.return_value': 'original'}),
            'same.txt': MagicMock(spec=Path, **{'exists.return_value': True, 'read_text.return_value': 'same'}),
        }
        with patch('pathlib.Path', autospec=True) as mock_path_class:
            #mock_path_class.return_value._flavour.return_value = 'same.txt'
            mock_path_class.side_effect = lambda p: path_map[p]
            changed = service.filter_unchanged_files(updated_files)
            self.assertEqual(len(changed), 2)
            self.assertEqual(changed[0]['file_name'], 'new.txt')
            self.assertEqual(changed[1]['file_name'], 'changed.txt')

    def test_filter_unchanged_files(self):
        """
        ``filter_unchanged_files`` should return only the entries whose
        content differs from the on‑disk file (or that do not exist on disk).
        """

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # 1️⃣  Create a file that already contains the *same* content.
            same_path = base / "same.txt"
            same_path.write_text("same", encoding="utf-8")

            # 2️⃣  Create a file with *different* content.
            changed_path = base / "changed.txt"
            changed_path.write_text("original", encoding="utf-8")

            # 3️⃣  Do **not** create ``new.txt`` – it should be treated as new.

            updated_files = [
                {"file_name": str(base / "new.txt"), "file_content": "new"},
                {"file_name": str(changed_path), "file_content": "changed"},
                {"file_name": str(same_path), "file_content": "same"},
            ]

            # Run the helper
            changed = service.filter_unchanged_files(updated_files)

            # Expect two entries: the new file and the changed file.
            self.assertEqual(len(changed), 2)
            self.assertEqual(changed[0]["file_name"], str(base / "new.txt"))
            self.assertEqual(changed[1]["file_name"], str(changed_path))

    @patch('aye.service._python_diff_files')
    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_diff_files_fallback(self, mock_run, mock_py_diff):
        service.diff_files(Path('a.txt'), Path('b.txt'))
        mock_run.assert_called_once()
        mock_py_diff.assert_called_once_with(Path('a.txt'), Path('b.txt'))

    @patch('aye.service.rprint')
    @patch('pathlib.Path.exists', return_value=False)
    def test_handle_diff_command_file_not_exist(self, mock_exists, mock_rprint):
        service.handle_diff_command(['nonexistent.py'])
        mock_rprint.assert_called_with("[red]Error:[/] File 'nonexistent.py' does not exist.")

    # --- Config handlers ---
    @patch('aye.service.rprint')
    @patch('aye.service.list_config', return_value={'key': 'value'})
    def test_handle_config_list(self, mock_list_config, mock_rprint):
        service.handle_config_list()
        mock_rprint.assert_has_calls([call('[bold]Current Configuration:[/]'), call('  key: value')])

    @patch('aye.service.rprint')
    @patch('aye.service.set_value')
    def test_handle_config_set(self, mock_set_value, mock_rprint):
        service.handle_config_set('key', 'value')
        mock_set_value.assert_called_once_with('key', 'value')
        mock_rprint.assert_called_with("[green]Configuration 'key' set to 'value'.[/]")

    @patch('aye.service.rprint')
    @patch('aye.service.get_value', return_value='value')
    def test_handle_config_get(self, mock_get_value, mock_rprint):
        service.handle_config_get('key')
        mock_rprint.assert_called_with('key: value')

    @patch('aye.service.rprint')
    @patch('aye.service.delete_value', return_value=True)
    def test_handle_config_delete(self, mock_delete_value, mock_rprint):
        service.handle_config_delete('key')
        mock_rprint.assert_called_with("[green]Configuration 'key' deleted.[/]")


if __name__ == '__main__':
    import unittest
    unittest.main()
