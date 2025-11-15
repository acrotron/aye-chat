# Test suite for aye.model.auth module
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import aye.model.auth as auth


class TestAuth(TestCase):
    def setUp(self):
        # Create a temporary TOKEN_FILE location for each test and patch the module const
        self.tmpdir = tempfile.TemporaryDirectory()
        self.token_path = Path(self.tmpdir.name) / ".ayecfg"
        self.token_patcher = patch("aye.model.auth.TOKEN_FILE", new=self.token_path)
        self.token_patcher.start()

        # Ensure env overrides are clean unless explicitly set in a test
        os.environ.pop("AYE_TOKEN", None)
        os.environ.pop("AYE_SELECTED_MODEL", None)

    def tearDown(self):
        # Cleanup environment variables
        os.environ.pop("AYE_TOKEN", None)
        os.environ.pop("AYE_SELECTED_MODEL", None)
        # Stop patcher and cleanup temp dir
        self.token_patcher.stop()
        self.tmpdir.cleanup()

    # --------------------------- _parse_user_config ----------------------------
    def test_parse_user_config_missing_file(self):
        self.assertFalse(self.token_path.exists())
        parsed = auth._parse_user_config()
        self.assertEqual(parsed, {})

    def test_parse_user_config_with_sections_and_comments(self):
        content = """
# comment line
; comment too
[other]
token=ignored
[default]
 token = abc123 
 selected_model = foo/bar

[extra]
key=value
""".strip()
        self.token_path.write_text(content, encoding="utf-8")
        parsed = auth._parse_user_config()
        self.assertEqual(parsed, {"token": "abc123", "selected_model": "foo/bar"})

    def test_parse_user_config_malformed_file(self):
        self.token_path.write_text("this is not a valid config file", encoding="utf-8")
        parsed = auth._parse_user_config()
        self.assertEqual(parsed, {})

    # --------------------------- get/set user config ---------------------------
    def test_set_and_get_user_config_roundtrip(self):
        # Patch chmod on Path class, not on the instance
        with patch("pathlib.Path.chmod") as mock_chmod:
            auth.set_user_config("selected_model", "openai/gpt")
            self.assertTrue(self.token_path.exists())
            text = self.token_path.read_text(encoding="utf-8")
            self.assertIn("[default]", text)
            self.assertIn("selected_model=openai/gpt", text)
            mock_chmod.assert_called_once_with(0o600)

        # Reads back from file when env not set
        val = auth.get_user_config("selected_model")
        self.assertEqual(val, "openai/gpt")

    def test_get_user_config_env_override(self):
        with patch("pathlib.Path.chmod"):
            auth.set_user_config("selected_model", "file/value")
        os.environ["AYE_SELECTED_MODEL"] = "env/value"
        self.assertEqual(auth.get_user_config("selected_model"), "env/value")

    # -------------------------------- token I/O --------------------------------
    def test_store_and_get_token_from_file(self):
        with patch("pathlib.Path.chmod"):
            auth.store_token("  secret-token\n")
        # get_token will generate a demo token if file is empty before store_token is called
        # so we need to re-read to get the stored one
        self.assertEqual(auth.get_user_config("token"), "secret-token")
        self.assertIn("token=secret-token", self.token_path.read_text(encoding="utf-8"))

    def test_get_token_env_over_file(self):
        with patch("pathlib.Path.chmod"):
            auth.store_token("file-token")
        os.environ["AYE_TOKEN"] = "ENV_TOKEN"
        self.assertEqual(auth.get_token(), "ENV_TOKEN")

    def test_get_token_generates_demo_token_if_none(self):
        """When no token exists in env or file, a demo token should be generated and stored."""
        self.assertFalse(self.token_path.exists())
        os.environ.pop("AYE_TOKEN", None)

        with patch("pathlib.Path.chmod"):
            token = auth.get_token()
            self.assertIsNotNone(token)
            self.assertTrue(token.startswith("aye_demo_"))

            # Verify it was also stored
            self.assertTrue(self.token_path.exists())
            text = self.token_path.read_text(encoding="utf-8")
            self.assertIn(f"token={token}", text)

    # ------------------------------- delete_token ------------------------------
    def test_delete_token_preserves_other_settings(self):
        # Prepare a config with token and another key
        self.token_path.write_text("""
[default]
token=abc
selected_model=x-ai/grok
""".strip(), encoding="utf-8")
        with patch("pathlib.Path.chmod") as mock_chmod:
            auth.delete_token()
            self.assertTrue(self.token_path.exists())
            text = self.token_path.read_text(encoding="utf-8")
            self.assertNotIn("token=", text)
            self.assertIn("selected_model=x-ai/grok", text)
            mock_chmod.assert_called_once_with(0o600)

    def test_delete_token_removes_file_if_last_entry(self):
        self.token_path.write_text("""
[default]
token=only
""".strip(), encoding="utf-8")
        auth.delete_token()
        self.assertFalse(self.token_path.exists())

    # -------------------------------- login_flow -------------------------------
    def test_login_flow_prompts_and_stores_token(self):
        with patch("aye.model.auth.typer.prompt", return_value="MY_TOKEN\n") as mock_prompt, \
             patch.object(auth, "store_token") as mock_store, \
             patch("aye.model.auth.typer.secho") as mock_secho:
            auth.login_flow()
            mock_prompt.assert_called_once()
            mock_store.assert_called_once_with("MY_TOKEN")
            mock_secho.assert_called()
