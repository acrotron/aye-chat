# Test suite for aye.download_plugins module
import os
import json
import hashlib
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

import aye.download_plugins as dl
from aye.auth import get_token
from aye.api import fetch_plugin_manifest
from pathlib import Path


class TestDownloadPlugins(TestCase):
    def setUp(self):
        self.plugin_root = '/tmp/mock_plugins'
        self.manifest_file = f'{self.plugin_root}/manifest.json'

    @patch('aye.download_plugins.get_token')
    @patch('aye.download_plugins.fetch_plugin_manifest')
    @patch('aye.download_plugins.shutil.rmtree')
    @patch('aye.download_plugins.PLUGIN_ROOT')
    @patch('aye.download_plugins.MANIFEST_FILE')
    def test_fetch_plugins_no_token(self, mock_manifest_file, mock_plugin_root, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = None
        mock_plugin_root.__str__.return_value = self.plugin_root
        mock_manifest_file.__str__.return_value = self.manifest_file
        mock_plugin_root.mkdir.return_value = None
        mock_manifest_file.write_text.return_value = None
        mock_manifest_file.read_text.return_value = '{}'

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_not_called()
        mock_rmtree.assert_not_called()

    @patch('aye.download_plugins.get_token')
    @patch('aye.download_plugins.fetch_plugin_manifest')
    @patch('aye.download_plugins.shutil.rmtree')
    @patch('aye.download_plugins.PLUGIN_ROOT')
    @patch('aye.download_plugins.MANIFEST_FILE')
    @patch('pathlib.Path')
    def test_fetch_plugins_success(self, mock_path, mock_manifest_file, mock_plugin_root, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        mock_manifest.return_value = {
            'test_plugin.py': {
                'content': 'def test(): pass',
                'sha256': 'abc123'
            }
        }
        mock_plugin_root.__str__.return_value = self.plugin_root
        mock_plugin_root.mkdir.return_value = None
        mock_manifest_file.__str__.return_value = self.manifest_file
        mock_manifest_file.write_text.return_value = None
        mock_manifest_file.read_text.return_value = '{}'

        # Mock Path for dest = PLUGIN_ROOT / name
        mock_dest = MagicMock(spec=Path)
        mock_dest.is_file.return_value = False
        mock_dest.write_text.return_value = None
        mock_plugin_root.__truediv__.return_value = mock_dest
        mock_path.return_value = mock_dest

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_called_once_with(dry_run=True)
        mock_rmtree.assert_called_once_with(self.plugin_root, ignore_errors=True)
        mock_plugin_root.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_dest.write_text.assert_called_once_with('def test(): pass', encoding='utf-8')
        mock_manifest_file.write_text.assert_called_once()

    @patch('aye.download_plugins.get_token')
    @patch('aye.download_plugins.fetch_plugin_manifest')
    @patch('aye.download_plugins.shutil.rmtree')
    @patch('aye.download_plugins.PLUGIN_ROOT')
    @patch('aye.download_plugins.MANIFEST_FILE')
    @patch('pathlib.Path')
    def test_fetch_plugins_hash_match_skip_write(self, mock_path, mock_manifest_file, mock_plugin_root, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        source_content = 'def test(): pass'
        expected_hash = hashlib.sha256(source_content.encode('utf-8')).hexdigest()
        mock_manifest.return_value = {
            'test_plugin.py': {
                'content': source_content,
                'sha256': expected_hash
            }
        }
        mock_plugin_root.__str__.return_value = self.plugin_root
        mock_plugin_root.mkdir.return_value = None
        mock_manifest_file.__str__.return_value = self.manifest_file
        mock_manifest_file.write_text.return_value = None
        mock_manifest_file.read_text.return_value = '{}'

        # Mock dest for hash match: is_file=True (computed_hash == expected_hash always with this setup)
        mock_dest = MagicMock(spec=Path)
        mock_dest.is_file.return_value = True
        mock_plugin_root.__truediv__.return_value = mock_dest
        mock_path.return_value = mock_dest

        dl.fetch_plugins(dry_run=True)

        mock_get_token.assert_called_once()
        mock_manifest.assert_called_once_with(dry_run=True)
        mock_rmtree.assert_called_once_with(self.plugin_root, ignore_errors=True)
        mock_plugin_root.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_dest.write_text.assert_not_called()  # Skipped since file exists and hashes match
        mock_manifest_file.write_text.assert_called_once()

    @patch('aye.download_plugins.get_token')
    @patch('aye.download_plugins.fetch_plugin_manifest')
    @patch('aye.download_plugins.shutil.rmtree')
    @patch('aye.download_plugins.PLUGIN_ROOT')
    @patch('aye.download_plugins.MANIFEST_FILE')
    def test_fetch_plugins_api_error(self, mock_manifest_file, mock_plugin_root, mock_rmtree, mock_manifest, mock_get_token):
        mock_get_token.return_value = 'fake_token'
        mock_manifest.side_effect = Exception('API error')
        mock_plugin_root.__str__.return_value = self.plugin_root
        mock_manifest_file.__str__.return_value = self.manifest_file
        mock_plugin_root.mkdir.return_value = None
        mock_manifest_file.write_text.return_value = None
        mock_manifest_file.read_text.return_value = '{}'

        with self.assertRaises(RuntimeError):
            dl.fetch_plugins(dry_run=True)

        mock_manifest.assert_called_once_with(dry_run=True)


if __name__ == '__main__':
    import unittest
    unittest.main()
