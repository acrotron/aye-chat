from unittest import TestCase
from typing import Dict, Any, Optional

from aye.plugins.plugin_base import Plugin


class DummyPlugin(Plugin):
    name = "dummy"

    def on_command(self, command_name: str, params: Dict[str, Any] = {}) -> Optional[Dict[str, Any]]:
        # This can be implemented for more specific tests if needed
        return super().on_command(command_name, params)


class TestPluginBase(TestCase):
    def test_init_verbose_true(self):
        plugin = DummyPlugin()
        plugin.init({"verbose": True})
        self.assertTrue(plugin.verbose)

    def test_init_verbose_false(self):
        plugin = DummyPlugin()
        plugin.init({"verbose": False})
        self.assertFalse(plugin.verbose)

    def test_init_verbose_missing(self):
        plugin = DummyPlugin()
        plugin.init({})
        self.assertFalse(plugin.verbose)

    def test_init_verbose_non_boolean(self):
        plugin = DummyPlugin()
        plugin.init({"verbose": "true"}) # Any truthy value
        self.assertTrue(plugin.verbose)

        plugin.init({"verbose": 0}) # Falsy value
        self.assertFalse(plugin.verbose)

    def test_on_command_default_is_none(self):
        plugin = DummyPlugin()
        plugin.init({})
        result = plugin.on_command("any_command", {})
        self.assertIsNone(result)
