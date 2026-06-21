# Test suite for aye.model.auth module
import os
import tempfile
import types
import uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import httpx
import pytest
import typer

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
        os.environ.pop("AYE_SSLVERIFY", None)

    def tearDown(self):
        # Cleanup environment variables
        os.environ.pop("AYE_TOKEN", None)
        os.environ.pop("AYE_SELECTED_MODEL", None)
        os.environ.pop("AYE_SSLVERIFY", None)
        os.environ.pop("AYE_TOKEN_FILE", None)
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

    def test_parse_user_config_with_read_error(self):
        """Covers the broad except Exception in _parse_user_config."""
        self.token_path.write_text("[default]\ntoken=abc123\n", encoding="utf-8")
        with patch("pathlib.Path.read_text", side_effect=OSError("read failed")):
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

    def test_get_user_config_returns_default_when_missing(self):
        self.assertEqual(auth.get_user_config("missing", "fallback"), "fallback")

    def test_set_user_config_preserves_existing_values(self):
        with patch("pathlib.Path.chmod"):
            auth.set_user_config("token", "abc12345")
            auth.set_user_config("selected_model", "gpt-4")

        parsed = auth._parse_user_config()
        self.assertEqual(parsed["token"], "abc12345")
        self.assertEqual(parsed["selected_model"], "gpt-4")

    # -------------------------------- token I/O --------------------------------
    def test_store_and_get_token_from_file(self):
        with patch("pathlib.Path.chmod"):
            auth.store_token("  secret-token\n")
        self.assertEqual(auth.get_user_config("token"), "secret-token")
        self.assertIn("token=secret-token", self.token_path.read_text(encoding="utf-8"))

    def test_get_token_env_over_file(self):
        with patch("pathlib.Path.chmod"):
            auth.store_token("file-token")
        os.environ["AYE_TOKEN"] = "ENV_TOKEN"
        self.assertEqual(auth.get_token(), "ENV_TOKEN")

    def test_get_token_generates_demo_token_if_none(self):
        """When no token exists in env or file, a demo token should be requested and stored."""
        self.assertFalse(self.token_path.exists())
        os.environ.pop("AYE_TOKEN", None)

        with patch("aye.model.auth._request_demo_token", return_value="aye_demo_generated123") as mock_req, \
             patch("pathlib.Path.chmod"):
            token = auth.get_token()
            self.assertEqual(token, "aye_demo_generated123")
            mock_req.assert_called_once_with()

            self.assertTrue(self.token_path.exists())
            text = self.token_path.read_text(encoding="utf-8")
            self.assertIn("token=aye_demo_generated123", text)

    def test_get_token_regenerates_demo_if_token_corrupted(self):
        """When token exists but is corrupted/invalid, a demo token should be requested."""
        self.token_path.write_text("[default]\ntoken=valid_token!!!\n", encoding="utf-8")
        os.environ.pop("AYE_TOKEN", None)

        with patch("aye.model.auth._request_demo_token", return_value="aye_demo_clean123") as mock_req, \
             patch("pathlib.Path.chmod"):
            token = auth.get_token()
            self.assertEqual(token, "aye_demo_clean123")
            mock_req.assert_called_once_with()

            text = self.token_path.read_text(encoding="utf-8")
            self.assertNotIn("valid_token!!!", text)
            self.assertIn("token=aye_demo_clean123", text)

    def test_get_token_regenerates_demo_if_token_too_short(self):
        """When token exists but is too short, a demo token should be requested."""
        self.token_path.write_text("[default]\ntoken=abc\n", encoding="utf-8")
        os.environ.pop("AYE_TOKEN", None)

        with patch("aye.model.auth._request_demo_token", return_value="aye_demo_long123"), \
             patch("pathlib.Path.chmod"):
            token = auth.get_token()
            self.assertEqual(token, "aye_demo_long123")

    def test_get_token_regenerates_demo_if_token_empty(self):
        """When token exists but is empty, a demo token should be requested."""
        self.token_path.write_text("[default]\ntoken=\n", encoding="utf-8")
        os.environ.pop("AYE_TOKEN", None)

        with patch("aye.model.auth._request_demo_token", return_value="aye_demo_empty123"), \
             patch("pathlib.Path.chmod"):
            token = auth.get_token()
            self.assertEqual(token, "aye_demo_empty123")

            text = self.token_path.read_text(encoding="utf-8")
            self.assertIn("token=aye_demo_empty123", text)

    def test_get_token_raises_if_demo_request_returns_none(self):
        with patch("aye.model.auth._request_demo_token", return_value=None):
            with self.assertRaises(auth.DemoTokenError) as cm:
                auth.get_token()
            self.assertIn("Failed to obtain demo token", str(cm.exception))

    def test_get_token_propagates_demo_token_error(self):
        with patch("aye.model.auth._request_demo_token", side_effect=auth.DemoTokenError("offline")):
            with self.assertRaises(auth.DemoTokenError) as cm:
                auth.get_token()
            self.assertIn("offline", str(cm.exception))

    def test_is_valid_token_accepts_valid_formats(self):
        """Valid tokens should pass validation."""
        self.assertTrue(auth._is_valid_token("aye_demo_abc123def"))
        self.assertTrue(auth._is_valid_token("valid_personal_access_token"))
        self.assertTrue(auth._is_valid_token("my-token-123"))
        self.assertTrue(auth._is_valid_token("UPPERCASE_TOKEN"))
        self.assertTrue(auth._is_valid_token("12345678"))

    def test_is_valid_token_rejects_invalid_formats(self):
        """Invalid tokens should fail validation."""
        self.assertFalse(auth._is_valid_token(""))
        self.assertFalse(auth._is_valid_token("short"))  # Too short
        self.assertFalse(auth._is_valid_token("has spaces"))
        self.assertFalse(auth._is_valid_token("has!special@chars"))
        self.assertFalse(auth._is_valid_token("token\nwith\nnewlines"))

    # -------------------------------- _ssl_verify ------------------------------
    def test_ssl_verify_false_values(self):
        for value in ("0", "false", "off", "no", " OFF "):
            with self.subTest(value=value), patch.object(auth, "get_user_config", return_value=value):
                self.assertFalse(auth._ssl_verify())

    def test_ssl_verify_true_values_and_unknown_values(self):
        for value in ("1", "true", "on", "yes", "unexpected", ""):
            with self.subTest(value=value), patch.object(auth, "get_user_config", return_value=value):
                self.assertTrue(auth._ssl_verify())

    # ------------------------------ client version -----------------------------
    def test_get_client_version_success(self):
        module = types.ModuleType("aye.model.version_checker")
        module.get_current_version = lambda: "1.2.3"

        with patch.dict("sys.modules", {"aye.model.version_checker": module}):
            self.assertEqual(auth._get_client_version(), "1.2.3")

    def test_get_client_version_returns_unknown_on_error(self):
        real_import = __import__

        def import_side_effect(name, *args, **kwargs):
            if name == "aye.model.version_checker":
                raise RuntimeError("version unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_side_effect):
            self.assertEqual(auth._get_client_version(), "unknown")

    # ----------------------------- install ID ----------------------------------
    def test_get_or_create_install_id_returns_existing_valid_id(self):
        existing = "12345678-1234-5678-1234-567812345678"
        with patch.object(auth, "get_user_config", return_value=existing), \
             patch.object(auth, "set_user_config") as mock_set:
            self.assertEqual(auth._get_or_create_install_id(), existing)
            mock_set.assert_not_called()

    def test_get_or_create_install_id_creates_when_missing(self):
        generated = uuid.UUID("12345678-1234-5678-1234-567812345678")
        with patch.object(auth, "get_user_config", return_value=None), \
             patch.object(auth, "set_user_config") as mock_set, \
             patch("aye.model.auth.uuid.uuid4", return_value=generated):
            result = auth._get_or_create_install_id()
            self.assertEqual(result, str(generated))
            mock_set.assert_called_once_with("install_id", str(generated))

    def test_get_or_create_install_id_replaces_short_existing_id(self):
        generated = uuid.UUID("87654321-4321-8765-4321-876543218765")
        with patch.object(auth, "get_user_config", return_value="short"), \
             patch.object(auth, "set_user_config") as mock_set, \
             patch("aye.model.auth.uuid.uuid4", return_value=generated):
            result = auth._get_or_create_install_id()
            self.assertEqual(result, str(generated))
            mock_set.assert_called_once_with("install_id", str(generated))

    # ----------------------------- /demo/start ---------------------------------
    def test_request_demo_token_success(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"token": "aye_demo_valid123"}

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth.platform.system", return_value="TestOS"), \
             patch("aye.model.auth._ssl_verify", return_value=False), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            token = auth._request_demo_token()

            self.assertEqual(token, "aye_demo_valid123")
            mock_client.assert_called_once_with(timeout=auth._API_TIMEOUT, verify=False)
            post_call = mock_client.return_value.__enter__.return_value.post.call_args
            self.assertEqual(post_call.args[0], f"{auth._API_BASE_URL}/demo/start")
            self.assertEqual(
                post_call.kwargs["json"],
                {
                    "install_id": "install-1",
                    "client": "cli",
                    "version": "9.9.9",
                    "platform": "TestOS",
                },
            )

    def test_request_demo_token_raises_for_invalid_token_from_server(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"token": "bad token!"}

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("invalid token format", str(cm.exception))

    def test_request_demo_token_raises_for_missing_token_from_server(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("invalid token format", str(cm.exception))

    def test_request_demo_token_raises_for_invalid_json_success_response(self):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("not json")

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Invalid response from server", str(cm.exception))

    def test_request_demo_token_429(self):
        resp = MagicMock(status_code=429)

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Too many demo requests", str(cm.exception))

    def test_request_demo_token_503(self):
        resp = MagicMock(status_code=503)

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Service temporarily unavailable", str(cm.exception))

    def test_request_demo_token_other_error_with_json_error_message(self):
        resp = MagicMock(status_code=400)
        resp.json.return_value = {"error": "bad request"}
        resp.text = "fallback text"

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("HTTP 400", str(cm.exception))
            self.assertIn("bad request", str(cm.exception))

    def test_request_demo_token_other_error_with_non_json_body(self):
        resp = MagicMock(status_code=500)
        resp.json.side_effect = ValueError("not json")
        resp.text = "plain failure"

        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = resp

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("HTTP 500", str(cm.exception))
            self.assertIn("plain failure", str(cm.exception))

    def test_request_demo_token_connect_error(self):
        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError(
                "connect failed"
            )

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Could not connect", str(cm.exception))

    def test_request_demo_token_timeout_error(self):
        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = httpx.TimeoutException(
                "timeout"
            )

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Connection timed out", str(cm.exception))

    def test_request_demo_token_unexpected_error(self):
        with patch("aye.model.auth._get_or_create_install_id", return_value="install-1"), \
             patch("aye.model.auth._get_client_version", return_value="9.9.9"), \
             patch("aye.model.auth._ssl_verify", return_value=True), \
             patch("aye.model.auth.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = RuntimeError("boom")

            with self.assertRaises(auth.DemoTokenError) as cm:
                auth._request_demo_token()
            self.assertIn("Unexpected error starting demo session", str(cm.exception))

    # ---------------------------- refresh_demo_token ---------------------------
    def test_refresh_demo_token_success(self):
        with patch("aye.model.auth._request_demo_token", return_value="aye_demo_refresh123"), \
             patch.object(auth, "set_user_config") as mock_set:
            result = auth.refresh_demo_token()
            self.assertEqual(result, "aye_demo_refresh123")
            mock_set.assert_called_once_with("token", "aye_demo_refresh123")

    def test_refresh_demo_token_returns_none_when_request_returns_none(self):
        with patch("aye.model.auth._request_demo_token", return_value=None), \
             patch.object(auth, "set_user_config") as mock_set:
            self.assertIsNone(auth.refresh_demo_token())
            mock_set.assert_not_called()

    def test_refresh_demo_token_swallows_demo_token_error(self):
        with patch("aye.model.auth._request_demo_token", side_effect=auth.DemoTokenError("nope")), \
             patch.object(auth, "set_user_config") as mock_set:
            self.assertIsNone(auth.refresh_demo_token())
            mock_set.assert_not_called()

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

    def test_delete_token_is_safe_when_token_missing(self):
        self.token_path.write_text("""
[default]
selected_model=gpt-4
""".strip(), encoding="utf-8")
        with patch("pathlib.Path.chmod"):
            auth.delete_token()
        self.assertTrue(self.token_path.exists())
        self.assertIn("selected_model=gpt-4", self.token_path.read_text(encoding="utf-8"))

    # -------------------------------- login_flow -------------------------------
    def test_login_flow_prompts_and_stores_token(self):
        with patch("aye.model.auth.typer.prompt", return_value="MY_TOKEN") as mock_prompt, \
             patch("aye.model.api.fetch_plugin_manifest") as mock_fetch, \
             patch.object(auth, "store_token") as mock_store, \
             patch("aye.model.auth.typer.secho") as mock_secho:
            auth.login_flow()
            mock_prompt.assert_called_once_with("Paste your token", hide_input=True)
            mock_fetch.assert_called_once()
            mock_store.assert_called_once_with("MY_TOKEN")
            mock_secho.assert_called_once_with("✅ Token saved.", fg=typer.colors.GREEN)

    def test_login_flow_with_aye_token_env_warning(self):
        """Covers the env var warning rprint branch."""
        os.environ["AYE_TOKEN"] = "env_token"
        with patch("aye.model.auth.rprint") as mock_rprint, \
             patch("aye.model.auth.typer.prompt", return_value="validtoken123") as mock_prompt, \
             patch("aye.model.api.fetch_plugin_manifest") as mock_fetch, \
             patch.object(auth, "store_token") as mock_store, \
             patch("aye.model.auth.typer.secho") as mock_secho:
            auth.login_flow()
            mock_rprint.assert_any_call(
                "[yellow]Note: AYE_TOKEN environment variable is set. "
                "The saved token will not be used until that variable is removed.[/]"
            )
            mock_prompt.assert_called_once_with("Paste your token", hide_input=True)
            mock_fetch.assert_called_once()
            mock_store.assert_called_once_with("validtoken123")
            mock_secho.assert_called_once_with("✅ Token saved.", fg=typer.colors.GREEN)
        os.environ.pop("AYE_TOKEN", None)

    def test_login_flow_invalid_token_format(self):
        """Covers local validation failure path."""
        with patch("aye.model.auth.typer.prompt", return_value="short") as mock_prompt, \
             patch("aye.model.auth.typer.secho") as mock_secho:
            with self.assertRaises(typer.Exit):
                auth.login_flow()
            mock_prompt.assert_called_once_with("Paste your token", hide_input=True)
            mock_secho.assert_called_once_with("Invalid token format.", fg=typer.colors.RED)

    def test_login_flow_verification_failure(self):
        """Covers the _verify_login_token_if_supported exception path."""
        with patch("aye.model.auth.typer.prompt", return_value="validtoken123") as mock_prompt, \
             patch("aye.model.api.fetch_plugin_manifest", side_effect=Exception("backend error")), \
             patch("aye.model.auth.typer.secho") as mock_secho:
            with self.assertRaises(typer.Exit):
                auth.login_flow()
            mock_prompt.assert_called_once_with("Paste your token", hide_input=True)
            mock_secho.assert_called_once_with(
                "Login failed: could not verify token with the backend. "
                "Existing token was not changed. (backend error)",
                fg=typer.colors.RED,
            )

    def test_login_flow_passes_previous_token_to_verification(self):
        self.token_path.write_text("[default]\ntoken=oldtoken123\n", encoding="utf-8")

        with patch("aye.model.auth.typer.prompt", return_value="newtoken123"), \
             patch.object(auth, "_verify_login_token_if_supported") as mock_verify, \
             patch.object(auth, "store_token") as mock_store, \
             patch("aye.model.auth.typer.secho"):
            auth.login_flow()

        mock_verify.assert_called_once_with("newtoken123", "oldtoken123")
        mock_store.assert_called_once_with("newtoken123")

    # ------------------------------- delete_user_config ------------------------
    def test_delete_user_config_key_exists(self):
        auth.set_user_config("token", "abc123")
        auth.set_user_config("selected_model", "gpt-4")
        with patch("pathlib.Path.chmod") as mock_chmod:
            auth.delete_user_config("token")
            mock_chmod.assert_called_once_with(0o600)
        config = auth._parse_user_config()
        self.assertNotIn("token", config)
        self.assertEqual(config.get("selected_model"), "gpt-4")

    def test_delete_user_config_nonexistent_key_is_noop(self):
        auth.set_user_config("selected_model", "gpt-4")
        auth.delete_user_config("nonexistent")
        config = auth._parse_user_config()
        self.assertEqual(config.get("selected_model"), "gpt-4")

    def test_delete_user_config_last_key_removes_file(self):
        auth.set_user_config("token", "abc123")
        self.assertTrue(self.token_path.exists())
        auth.delete_user_config("token")
        self.assertFalse(self.token_path.exists())

    # ------------------------------ helper functions ---------------------------
    def test_supports_kwarg(self):
        def func_with_varkw(a, **kwargs):
            pass

        def func_with_named(a, token_override=None):
            pass

        def func_without(a, b):
            pass

        self.assertTrue(auth._supports_kwarg(func_with_varkw, "token_override"))
        self.assertTrue(auth._supports_kwarg(func_with_named, "token_override"))
        self.assertFalse(auth._supports_kwarg(func_without, "token_override"))
        # Non-callable case
        self.assertFalse(auth._supports_kwarg(123, "foo"))

    def test_supports_kwarg_handles_value_error_from_signature(self):
        with patch("aye.model.auth.inspect.signature", side_effect=ValueError("bad signature")):
            self.assertFalse(auth._supports_kwarg(lambda: None, "token_override"))

    def test_verify_login_token_if_supported_skips_old_api(self):
        with patch("aye.model.api.fetch_plugin_manifest") as mock_fetch, \
             patch.object(auth, "_supports_kwarg", return_value=False) as mock_supports:
            auth._verify_login_token_if_supported("new_token", "old_token")
            mock_supports.assert_called()
            mock_fetch.assert_not_called()

    def test_verify_login_token_if_supported_calls_new_api(self):
        def supports_side_effect(callable_obj, name):
            return name in ("token_override", "previous_token", "dry_run")

        with patch("aye.model.api.fetch_plugin_manifest") as mock_fetch, \
             patch.object(auth, "_supports_kwarg", side_effect=supports_side_effect):
            auth._verify_login_token_if_supported("new_token", "old_token")
            mock_fetch.assert_called_once_with(
                token_override="new_token",
                previous_token="old_token",
                dry_run=False,
            )

    def test_verify_login_token_if_supported_omits_optional_kwargs_when_unsupported(self):
        def supports_side_effect(callable_obj, name):
            return name == "token_override"

        with patch("aye.model.api.fetch_plugin_manifest") as mock_fetch, \
             patch.object(auth, "_supports_kwarg", side_effect=supports_side_effect):
            auth._verify_login_token_if_supported("new_token", "old_token")
            mock_fetch.assert_called_once_with(token_override="new_token")

    # ------------------------- AYE_TOKEN_FILE env var --------------------------
    def test_aye_token_file_env_var_overrides_default_path(self):
        """Test that AYE_TOKEN_FILE environment variable overrides the default config file path."""
        # Create a custom config file location
        custom_tmpdir = tempfile.TemporaryDirectory()
        custom_config_path = Path(custom_tmpdir.name) / "custom_config.cfg"

        try:
            # Set the environment variable
            os.environ["AYE_TOKEN_FILE"] = str(custom_config_path)

            # Reimport the module to pick up the new TOKEN_FILE value
            import importlib
            importlib.reload(auth)

            # Verify TOKEN_FILE now points to the custom path
            self.assertEqual(auth.TOKEN_FILE, custom_config_path)

            # Test that operations use the custom path
            with patch("pathlib.Path.chmod"):
                auth.store_token("custom_location_token")

            # Verify the token was written to the custom location
            self.assertTrue(custom_config_path.exists())
            content = custom_config_path.read_text(encoding="utf-8")
            self.assertIn("token=custom_location_token", content)

            # Verify we can read it back
            token_value = auth.get_user_config("token")
            self.assertEqual(token_value, "custom_location_token")

        finally:
            # Cleanup
            os.environ.pop("AYE_TOKEN_FILE", None)
            custom_tmpdir.cleanup()
            # Reload module again to restore default behavior
            importlib.reload(auth)
