# Test suite for aye.model.download_plugins module
import os
import json
import hashlib
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

import aye.model.download_plugins as dl
from aye.model.auth import get_token
from aye.model.api import fetch_plugin_manifest
from pathlib import Path


class TestDownloadPlugins(TestCase):
    def setUp(self):
        self.plugin_root_patcher = patch('aye.model.download_plugins.PLUGIN_ROOT', Path('/tmp/mock_plugins'))
        self.manifest_file_patcher = patch('aye.model.download_plugins.MANIFEST_FILE', Path('/tmp/mock_plugins/manifest.json'))
        self.mock_plugin_root = self.plugin_root_patcher.start()
        self.mock_manifest_file = self.manifest_file_patcher.start()

    def tearDown(self):
        self.plugin_root_patcher.stop()
        self.manifest_file_patcher.stop()

    @patch('aye.model.download_plugins.get_token')
    @patch('aye.model.download_plugins.fetch_plugin_manifest')
    @patch('aye.model.download_plugins.shutil.rmtree')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.read_text', return_value='{}')
    def test_fetch_plugins_no_token(self, mock_read, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = None

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_not_called()
        # rmtree is not called if there is no token
        mock_rmtree.assert_not_called()

    @patch('aye.model.download_plugins.get_token')
    @patch('aye.model.download_plugins.fetch_plugin_manifest')
    @patch('aye.model.download_plugins.shutil.rmtree')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.read_text', return_value='{}')
    @patch('pathlib.Path.write_text')
    @patch('pathlib.Path.is_file', return_value=False)
    def test_fetch_plugins_success(self, mock_is_file, mock_write_text, mock_read_text, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        source_content = 'def test(): pass'
        expected_hash = hashlib.sha256(source_content.encode('utf-8')).hexdigest()
        mock_manifest.return_value = {
            'test_plugin.py': {
                'content': source_content,
                'sha256': expected_hash
            }
        }

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_called_once_with(dry_run=True)
        mock_rmtree.assert_called_once_with(str(self.mock_plugin_root), ignore_errors=True)
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

        # Check that plugin file and manifest file were written
        write_calls = mock_write_text.call_args_list
        self.assertIn(call(source_content, encoding='utf-8'), write_calls)
        self.assertEqual(len(write_calls), 2)  # One for plugin, one for manifest

    @patch('aye.model.download_plugins.get_token')
    @patch('aye.model.download_plugins.fetch_plugin_manifest')
    @patch('aye.model.download_plugins.shutil.rmtree')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.read_text', return_value='{}')
    @patch('pathlib.Path.write_text')
    @patch('pathlib.Path.is_file', return_value=True)
    @patch('hashlib.sha256')
    def test_fetch_plugins_hash_match_skip_write(self, mock_sha256, mock_is_file, mock_write_text, mock_read_text, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        source_content = 'def test(): pass'
        expected_hash = 'matching_hash'
        
        mock_hash_obj = MagicMock()
        mock_hash_obj.hexdigest.return_value = expected_hash
        mock_sha256.return_value = mock_hash_obj

        mock_manifest.return_value = {
            'test_plugin.py': {
                'content': source_content,
                'sha256': expected_hash
            }
        }

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_called_once_with(dry_run=True)
        mock_rmtree.assert_called_once_with(str(self.mock_plugin_root), ignore_errors=True)
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        
        # Check that plugin file was NOT written, but manifest was
        plugin_write_call = call(source_content, encoding='utf-8')
        self.assertNotIn(plugin_write_call, mock_write_text.call_args_list)
        self.assertEqual(mock_write_text.call_count, 1) # Only manifest

    @patch('aye.model.download_plugins.get_token')
    @patch('aye.model.download_plugins.fetch_plugin_manifest')
    @patch('aye.model.download_plugins.shutil.rmtree')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.read_text', return_value='{}')
    def test_fetch_plugins_api_error(self, mock_read, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        mock_manifest.side_effect = RuntimeError('API error')

        with self.assertRaises(RuntimeError) as cm:
            dl.fetch_plugins(dry_run=True)
        
        self.assertIn('API error', str(cm.exception))
        mock_manifest.assert_called_once_with(dry_run=True)

    @patch('aye.model.download_plugins._now_ts', return_value=100000)
    @patch('aye.model.download_plugins.get_token')
    @patch('aye.model.download_plugins.fetch_plugin_manifest')
    @patch('aye.model.download_plugins.shutil.rmtree')
    @patch('pathlib.Path.mkdir')
    @patch('pathlib.Path.read_text')
    @patch('pathlib.Path.write_text')
    @patch('pathlib.Path.is_file', return_value=False) # Assume file needs writing
    def test_fetch_plugins_preserves_timestamps(self, mock_is_file, mock_write_text, mock_read_text, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token, mock_now):
        mock_get_token.return_value = 'fake_token'
        source_content = 'def test(): pass'
        expected_hash = hashlib.sha256(source_content.encode('utf-8')).hexdigest()
        
        # Simulate an old manifest with existing timestamps
        old_manifest_content = json.dumps({
            'test_plugin.py': {
                'sha256': 'old_hash',
                'checked': 50000,
                'expires': 60000
            }
        })
        mock_read_text.return_value = old_manifest_content
        
        mock_manifest.return_value = {
            'test_plugin.py': {
                'content': source_content,
                'sha256': expected_hash
            }
        }

        dl.fetch_plugins(dry_run=True)

        # Find the call to write the new manifest
        new_manifest_call = None
        for c in mock_write_text.call_args_list:
            try:
                # The manifest is written with indent=4
                data = json.loads(c.args[0])
                if 'test_plugin.py' in data:
                    new_manifest_call = data
                    break
            except (json.JSONDecodeError, IndexError):
                continue
        
        self.assertIsNotNone(new_manifest_call, "New manifest was not written or was not valid JSON")
        plugin_entry = new_manifest_call['test_plugin.py']
        
        # Assert that old timestamps were preserved, not regenerated with _now_ts()
        self.assertEqual(plugin_entry['checked'], 50000)
        self.assertEqual(plugin_entry['expires'], 60000)
        self.assertEqual(plugin_entry['sha256'], expected_hash) # Hash should be updated

    @patch('aye.model.download_plugins.fetch_plugins')
    @patch('builtins.print')
    def test_driver_success(self, mock_print, mock_fetch_plugins):
        dl.driver()
        mock_fetch_plugins.assert_called_once()
        mock_print.assert_called_with("Plugins fetched successfully.")

    @patch('aye.model.download_plugins.fetch_plugins', side_effect=Exception("Network Error"))
    @patch('builtins.print')
    def test_driver_failure(self, mock_print, mock_fetch_plugins):
        dl.driver()
        mock_fetch_plugins.assert_called_once()
        mock_print.assert_called_with("Error fetching plugins: Network Error")
