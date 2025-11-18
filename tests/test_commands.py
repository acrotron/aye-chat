import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import aye.controller.commands as commands

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

    @patch('aye.model.auth.get_token', return_value='fake-token')
    def test_get_auth_status_token(self, mock_get_token):
        token = commands.get_auth_status_token()
        self.assertEqual(token, 'fake-token')
        mock_get_token.assert_called_once()

    # --- Snapshot command handlers ---
    @patch('aye.model.snapshot.list_snapshots', return_value=['snap1', 'snap2'])
    def test_get_snapshot_history(self, mock_list_snapshots):
        result = commands.get_snapshot_history()
        mock_list_snapshots.assert_called_once_with(None)
        self.assertEqual(result, ['snap1', 'snap2'])

    @patch('aye.model.snapshot.list_snapshots', return_value=[('ts1', '/path/to/snap1')])
    def test_get_snapshot_content_found(self, mock_list_snapshots):
        with patch('pathlib.Path.read_text', return_value="snap content") as mock_read:
            content = commands.get_snapshot_content(Path('file.py'), 'ts1')
            self.assertEqual(content, "snap content")
            mock_list_snapshots.assert_called_once_with(Path('file.py'))
            mock_read.assert_called_once()

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
    @patch('aye.model.snapshot.list_snapshots')
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths_latest(self, mock_exists, mock_list_snapshots):
        mock_list_snapshots.return_value = [('002_ts', '/path/snap2'), ('001_ts', '/path/snap1')]
        file_path = Path('file.py')
        
        path1, path2 = commands.get_diff_paths('file.py')
        
        self.assertEqual(path1, file_path)
        self.assertEqual(path2, Path('/path/snap2'))
        mock_list_snapshots.assert_called_once_with(file_path)

    @patch('aye.model.snapshot.list_snapshots')
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths_one_snap(self, mock_exists, mock_list_snapshots):
        mock_list_snapshots.return_value = [('002_ts', '/path/snap2'), ('001_ts', '/path/snap1')]
        file_path = Path('file.py')
        
        path1, path2 = commands.get_diff_paths('file.py', snap_id1='001')
        
        self.assertEqual(path1, file_path)
        self.assertEqual(path2, Path('/path/snap1'))

    @patch('aye.model.snapshot.list_snapshots')
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths_two_snaps(self, mock_exists, mock_list_snapshots):
        mock_list_snapshots.return_value = [('002_ts', '/path/snap2'), ('001_ts', '/path/snap1')]
        
        path1, path2 = commands.get_diff_paths('file.py', snap_id1='002', snap_id2='001')
        
        self.assertEqual(path1, Path('/path/snap2'))
        self.assertEqual(path2, Path('/path/snap1'))

    @patch('pathlib.Path.exists', return_value=False)
    def test_get_diff_paths_file_not_exist(self, mock_exists):
        with self.assertRaises(FileNotFoundError):
            commands.get_diff_paths('file.py')

    @patch('aye.model.snapshot.list_snapshots', return_value=[])
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths_no_snapshots(self, mock_exists, mock_list_snapshots):
        with self.assertRaises(ValueError):
            commands.get_diff_paths('file.py')

    @patch('aye.model.snapshot.list_snapshots')
    @patch('pathlib.Path.exists', return_value=True)
    def test_get_diff_paths_snap_id_not_found(self, mock_exists, mock_list_snapshots):
        mock_list_snapshots.return_value = [('001_ts', '/path/snap1')]
        with self.assertRaises(ValueError):
            commands.get_diff_paths('file.py', snap_id1='999')
        with self.assertRaises(ValueError):
            commands.get_diff_paths('file.py', snap_id1='001', snap_id2='999')

    # --- Config handlers ---
    @patch('aye.model.config.list_config', return_value={'key': 'value'})
    def test_get_all_config(self, mock_list_config):
        result = commands.get_all_config()
        self.assertEqual(result, {'key': 'value'})
        mock_list_config.assert_called_once()

    def test_set_config_value(self):
        with patch('aye.model.config.set_value') as mock_set:
            # Test with plain string
            commands.set_config_value('key', 'value')
            mock_set.assert_called_with('key', 'value')
            
            # Test with JSON string (number)
            commands.set_config_value('key_num', '123')
            mock_set.assert_called_with('key_num', 123)
            
            # Test with JSON string (boolean)
            commands.set_config_value('key_bool', 'true')
            mock_set.assert_called_with('key_bool', True)

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
