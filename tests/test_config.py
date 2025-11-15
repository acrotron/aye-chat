import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import aye.model.config as config

class TestConfig(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / ".aye" / "config.json"
        
        # Patch the CONFIG_FILE constant and clear the internal _config dict
        self.config_patcher = patch("aye.model.config.CONFIG_FILE", self.config_path)
        self.config_patcher.start()
        config._config.clear()

    def tearDown(self):
        self.config_patcher.stop()
        self.tmpdir.cleanup()

    def test_load_config_file_not_exists(self):
        self.assertFalse(self.config_path.exists())
        config.load_config()
        self.assertEqual(config.list_config(), {})

    def test_load_config_invalid_json(self):
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("not json")
        config.load_config()
        self.assertEqual(config.list_config(), {})

    def test_set_and_get_value(self):
        config.set_value("key1", "value1")
        config.set_value("key2", 123)
        
        self.assertEqual(config.get_value("key1"), "value1")
        self.assertEqual(config.get_value("key2"), 123)
        self.assertIsNone(config.get_value("nonexistent"))
        self.assertEqual(config.get_value("nonexistent", "default"), "default")

    def test_save_and_load_config_roundtrip(self):
        test_data = {"user": "test", "level": 5, "active": True}
        config.set_value("user", "test")
        config.set_value("level", 5)
        config.set_value("active", True)
        
        # config._config is now populated, let's clear it and reload from file
        config._config.clear()
        self.assertEqual(config.list_config(), {})
        
        config.load_config()
        self.assertEqual(config.list_config(), test_data)
        
        # Check file content
        content = json.loads(self.config_path.read_text())
        self.assertEqual(content, test_data)

    def test_delete_value(self):
        config.set_value("key_to_delete", "value")
        config.set_value("key_to_keep", "another_value")
        
        self.assertTrue(config.delete_value("key_to_delete"))
        self.assertNotIn("key_to_delete", config.list_config())
        self.assertIn("key_to_keep", config.list_config())
        
        # Deleting a non-existent key
        self.assertFalse(config.delete_value("nonexistent_key"))

    def test_list_config(self):
        self.assertEqual(config.list_config(), {})
        config.set_value("a", 1)
        self.assertEqual(config.list_config(), {"a": 1})
        # Ensure it returns a copy
        cfg_copy = config.list_config()
        cfg_copy["b"] = 2
        self.assertNotEqual(config.list_config(), cfg_copy)

    def test_set_value_invalid_key(self):
        with self.assertRaises(TypeError):
            config.set_value(123, "value")

    @patch('builtins.print')
    @patch('aye.model.config.load_config')
    @patch('aye.model.config.list_config', return_value={"key": "value"})
    def test_driver(self, mock_list, mock_load, mock_print):
        config.driver()
        mock_load.assert_called_once()
        mock_list.assert_called_once()
        mock_print.assert_called_once_with(json.dumps({"key": "value"}, indent=2))
