from unittest import TestCase

import aye.model.config as config


class TestConfig(TestCase):
    def test_default_ignore_set_exists(self):
        self.assertIsInstance(config.DEFAULT_IGNORE_SET, set)
        self.assertIn('node_modules', config.DEFAULT_IGNORE_SET)

    def test_models_list_exists(self):
        self.assertIsInstance(config.MODELS, list)
        self.assertTrue(len(config.MODELS) > 0)

    def test_default_model_id_exists(self):
        self.assertIsInstance(config.DEFAULT_MODEL_ID, str)
        self.assertTrue(len(config.DEFAULT_MODEL_ID) > 0)

    def test_system_prompt_exists(self):
        self.assertIsInstance(config.SYSTEM_PROMPT, str)
        self.assertTrue(len(config.SYSTEM_PROMPT) > 0)
