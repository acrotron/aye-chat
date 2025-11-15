# Test suite for aye.controller.plugin_manager module
import os
from types import SimpleNamespace
from typing import Any, Dict
from unittest import TestCase
from unittest.mock import patch, MagicMock

from aye.controller.plugin_manager import PluginManager
from aye.plugins.plugin_base import Plugin


class TestPlugin(Plugin):
    name = "test_plugin"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        pass

    def on_command(self, command_name: str, params: Dict[str, Any] = {}) -> Dict[str, Any]:
        return {'data': 'test'}


class TestPluginManager(TestCase):
    def setUp(self):
        self.plugin_manager = PluginManager()

    @patch('pathlib.Path.is_dir', return_value=False)
    def test_discover_no_plugin_dir(self, mock_is_dir):
        self.plugin_manager.discover()
        self.assertEqual(len(self.plugin_manager.registry), 0)

    @patch('pathlib.Path.glob', return_value=[])
    @patch('pathlib.Path.is_dir', return_value=True)
    def test_discover_no_plugins(self, mock_is_dir, mock_glob):
        self.plugin_manager.discover()
        self.assertEqual(len(self.plugin_manager.registry), 0)

    @patch('importlib.util.spec_from_file_location')
    @patch('importlib.util.module_from_spec')
    @patch('pathlib.Path.glob')
    @patch('pathlib.Path.is_dir', return_value=True)
    def test_discover_with_plugins(self, mock_is_dir, mock_glob, mock_module, mock_spec):
        # 1. Mock the file system scan
        mock_file_path = MagicMock(spec=os.PathLike)
        mock_file_path.name = 'test_plugin.py'
        mock_file_path.stem = 'test_plugin'
        mock_glob.return_value = [mock_file_path]

        # 2. Mock the import machinery
        mock_plugin_module = MagicMock()
        mock_plugin_module.TestPlugin = TestPlugin  # Attach the test class
        mock_module.return_value = mock_plugin_module
        
        # Mock spec.loader.exec_module
        mock_loader = MagicMock()
        mock_loader.exec_module.return_value = None
        mock_spec.return_value.loader = mock_loader

        # 3. Run discovery
        self.plugin_manager.discover()

        # 4. Assert plugin was registered
        self.assertIn('test_plugin', self.plugin_manager.registry)
        self.assertIsInstance(self.plugin_manager.registry['test_plugin'], TestPlugin)

    def test_handle_command_no_plugins(self):
        response = self.plugin_manager.handle_command('test_command')
        self.assertIsNone(response)

    def test_handle_command_with_plugin(self):
        test_plugin = TestPlugin()
        self.plugin_manager.registry['test_plugin'] = test_plugin

        with patch.object(test_plugin, 'on_command', return_value={'data': 'test'}) as mock_on_command:
            response = self.plugin_manager.handle_command('test_command', {'param': 1})
            self.assertEqual(response, {'data': 'test'})
            mock_on_command.assert_called_once_with('test_command', {'param': 1})
