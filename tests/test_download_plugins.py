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
    def test_fetch_plugins_hash_match_skip_write(self, mock_is_file, mock_write_text, mock_read_text, mock_mkdir, mock_rmtree, mock_manifest, mock_get_token):
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
