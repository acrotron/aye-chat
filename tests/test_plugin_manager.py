# Test suite for aye.plugin_manager module
import os
from types import SimpleNamespace
from typing import Any, Dict
from unittest import TestCase
from unittest.mock import patch, MagicMock

from aye.plugin_manager import PluginManager
from aye.plugin_base import Plugin


class TestPlugin(Plugin):
    name = "test_plugin"
    version = "1.0.0"
    premium = "free"

    def _init(self, cfg: Dict[str, Any]) -> None:
        pass

    def init(self, cfg: Dict[str, Any]) -> None:
        pass


class TestPluginManager(TestCase):
    def setUp(self):
        self.plugin_manager = PluginManager()

    @patch('aye.plugin_manager.PLUGIN_ROOT')
    def test_discover_no_plugins(self, mock_plugin_root):
        mock_plugin_root.is_dir.return_value = False
        self.plugin_manager.discover()
        #self.assertEqual(len(self.plugin_manager.registry), 0)

    @patch('aye.plugin_manager.PLUGIN_ROOT')
    @patch('importlib.util.spec_from_file_location')
    @patch('importlib.util.module_from_spec')
    def test_discover_with_plugins(self, mock_module, mock_spec, mock_plugin_root):
        mock_plugin = MagicMock()
        mock_plugin.TestPlugin = TestPlugin
        mock_module.return_value = mock_plugin
        mock_plugin_root.is_dir.return_value = True

        my_dict = {'name': 'test_plugin.py', 'stem': 'test_plugin'}
        file_obj = SimpleNamespace(**my_dict)

        mock_plugin_root.glob.return_value = [ file_obj ]

        self.plugin_manager.discover()
        self.assertIn('test_plugin', self.plugin_manager.registry)

    def test_handle_command_no_plugins(self):
        response = self.plugin_manager.handle_command('test_command')
        self.assertIsNone(response)

    def test_handle_command_with_plugin(self):
        test_plugin = TestPlugin()
        self.plugin_manager.registry['test_plugin'] = test_plugin

        with patch.object(test_plugin, 'on_command', return_value={'data': 'test'}) as mock_on_command:
            response = self.plugin_manager.handle_command('test_command')
            self.assertEqual(response, {'data': 'test'})
            mock_on_command.assert_called_once_with('test_command', {})


if __name__ == '__main__':
    import unittest
    unittest.main()
