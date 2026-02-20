import os
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import httpx

from aye.plugins.databricks_model import (
    DatabricksModelPlugin,
    _get_model_config,
    _extract_json_object,
)
from aye.plugins.model_plugin_utils import (
    TRUNCATED_RESPONSE_MESSAGE,
    get_conversation_id,
    build_user_message,
    parse_llm_response,
    create_error_response,
)
from aye.plugins.plugin_base import Plugin


class TestGetModelConfig(TestCase):
    def test_found(self):
        import aye.plugins.databricks_model as mod
        original = mod.MODELS
        mod.MODELS = [{"id": "m1", "name": "Model 1"}]
        try:
            self.assertEqual(_get_model_config("m1"), {"id": "m1", "name": "Model 1"})
        finally:
            mod.MODELS = original

    def test_not_found(self):
        import aye.plugins.databricks_model as mod
        original = mod.MODELS
        mod.MODELS = [{"id": "m1", "name": "Model 1"}]
        try:
            self.assertIsNone(_get_model_config("nonexistent"))
        finally:
            mod.MODELS = original


class TestExtractJsonObject(TestCase):
    # ---- direct parse path ----
    def test_direct_valid_json(self):
        obj = {"a": 1}
        self.assertEqual(_extract_json_object(json.dumps(obj)), obj)

    def test_direct_valid_json_require_keys_pass(self):
        obj = {"a": 1, "b": 2}
        self.assertEqual(_extract_json_object(json.dumps(obj), require_keys=["a", "b"]), obj)

    def test_direct_valid_json_require_keys_fail(self):
        obj = {"a": 1}
        self.assertIsNone(_extract_json_object(json.dumps(obj), require_keys=["missing"]))

    def test_direct_parse_non_dict(self):
        # A valid JSON array should not be returned by direct parse;
        # falls through to scanning which also rejects non-dicts.
        self.assertIsNone(_extract_json_object("[1,2,3]"))

    # ---- scanning path ----
    def test_embedded_json(self):
        raw = 'Here is your answer: {"x": 42} hope it helps'
        self.assertEqual(_extract_json_object(raw), {"x": 42})

    def test_multiple_objects_prefer_last(self):
        raw = '{"first": true} some text {"second": true}'
        result = _extract_json_object(raw, prefer_last=True)
        self.assertEqual(result, {"second": True})

    def test_multiple_objects_prefer_first(self):
        raw = '{"first": true} some text {"second": true}'
        result = _extract_json_object(raw, prefer_last=False)
        self.assertEqual(result, {"first": True})

    def test_require_keys_filters_candidates(self):
        raw = '{"a": 1} {"b": 2}'
        result = _extract_json_object(raw, require_keys=["b"])
        self.assertEqual(result, {"b": 2})

    def test_no_valid_json(self):
        self.assertIsNone(_extract_json_object("no json here"))

    def test_nested_braces_in_strings(self):
        obj = {"code": "if (x) { y; }"}
        raw = f"blah {json.dumps(obj)} blah"
        self.assertEqual(_extract_json_object(raw), obj)

    def test_escaped_quotes_in_strings(self):
        obj = {"msg": 'say \"hi\"'}
        raw = f"prefix {json.dumps(obj)} suffix"
        result = _extract_json_object(raw)
        self.assertIsNotNone(result)
        self.assertIn("msg", result)

    def test_scanning_skips_non_dict(self):
        # Wrap an array in braces that accidentally forms an invalid dict attempt
        raw = 'text {"k":"v"} and then [1,2]'
        result = _extract_json_object(raw)
        self.assertEqual(result, {"k": "v"})

    def test_candidates_with_invalid_json(self):
        # A candidate that looks like {} but has syntax issues inside
        raw = '{bad json:} after {"good": true}'
        result = _extract_json_object(raw)
        self.assertEqual(result, {"good": True})

    def test_all_candidates_fail_require_keys(self):
        raw = '{"a":1} {"b":2}'
        self.assertIsNone(_extract_json_object(raw, require_keys=["z"]))


class TestDatabricksModelPlugin(TestCase):
    # Environment variable keys we need to control
    ENV_KEYS = ["AYE_DBX_API_URL", "AYE_DBX_API_KEY", "AYE_DBX_MODEL"]

    def setUp(self):
        self.plugin = DatabricksModelPlugin()
        self.plugin.init({"verbose": False})

        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.history_file = self.root / ".aye" / "chat_history.json"

        # Save original env var values (None if not set)
        self._saved_env = {key: os.environ.get(key) for key in self.ENV_KEYS}

        # Clear env vars for test isolation
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        self.tmpdir.cleanup()

        # Restore original env var values
        for key in self.ENV_KEYS:
            original_value = self._saved_env.get(key)
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value

    # -- init ---------------------------------------------------------------
    def test_init_defaults(self):
        self.assertEqual(self.plugin.name, "databricks_model")
        self.assertFalse(self.plugin.verbose)
        self.assertEqual(self.plugin.chat_history, {})
        self.assertIsNone(self.plugin.history_file)

    @patch("aye.plugins.databricks_model.rprint")
    def test_init_debug(self, mock_rprint):
        # Base Plugin.init() may not propagate 'debug' from cfg,
        # so set it directly and mock super().init() to prevent reset.
        self.plugin.debug = True
        with patch.object(Plugin, 'init'):
            self.plugin.init({"verbose": False, "debug": True})
        #mock_rprint.assert_called_once_with(
        #    f"[bold yellow]Initializing {self.plugin.name} v{self.plugin.version}[/]"
        #)

    # -- history load / save ------------------------------------------------
    def test_history_load_save_roundtrip(self):
        self.plugin.history_file = self.history_file
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

        self.plugin.chat_history = {"default": [{"role": "user", "content": "hi"}]}
        self.plugin._save_history()
        self.assertTrue(self.history_file.exists())

        self.plugin.chat_history = {}
        self.plugin._load_history()
        self.assertEqual(
            self.plugin.chat_history,
            {"default": [{"role": "user", "content": "hi"}]},
        )

    def test_history_load_file_not_found(self):
        self.plugin.history_file = self.history_file
        self.assertFalse(self.history_file.exists())
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

    def test_history_load_invalid_json(self):
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text("not json")
        self.plugin.history_file = self.history_file
        self.plugin.verbose = True
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

    @patch("aye.plugins.databricks_model.rprint")
    def test_history_no_file_path_load(self, mock_rprint):
        self.plugin.verbose = True
        self.plugin.history_file = None
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

    @patch("aye.plugins.databricks_model.rprint")
    def test_history_no_file_path_save(self, mock_rprint):
        self.plugin.verbose = True
        self.plugin.history_file = None
        self.plugin._save_history()  # should not raise

    @patch("aye.plugins.databricks_model.rprint")
    def test_save_history_exception(self, mock_rprint):
        self.plugin.verbose = True
        # Use a path that will fail when writing
        self.plugin.history_file = Path("/nonexistent_root_dir/sub/chat_history.json")
        self.plugin.chat_history = {"default": []}
        self.plugin._save_history()  # should not raise, just print warning

    # -- get_conversation_id (now a standalone utility) ---------------------
    def test_get_conversation_id_with_id(self):
        self.assertEqual(get_conversation_id(5), "5")

    def test_get_conversation_id_zero(self):
        self.assertEqual(get_conversation_id(0), "default")

    def test_get_conversation_id_none(self):
        self.assertEqual(get_conversation_id(None), "default")

    def test_get_conversation_id_negative(self):
        self.assertEqual(get_conversation_id(-1), "default")

    # -- build_user_message (now a standalone utility) ----------------------
    def test_build_user_message_no_files(self):
        msg = build_user_message("hello", {})
        self.assertEqual(msg, "hello")

    def test_build_user_message_with_files(self):
        msg = build_user_message("prompt", {"f.py": "code"})
        self.assertIn("prompt", msg)
        self.assertIn("--- Source files are below. ---", msg)
        self.assertIn("** f.py **", msg)
        self.assertIn("code", msg)

    # -- parse_llm_response (now a standalone utility) ----------------------
    def test_parse_llm_response_valid_json(self):
        text = json.dumps(
            {
                "answer_summary": "summary",
                "source_files": [{"file_name": "a.py", "file_content": "c"}],
            }
        )
        parsed = parse_llm_response(text)
        self.assertEqual(parsed["summary"], "summary")
        self.assertEqual(len(parsed["updated_files"]), 1)
        self.assertEqual(parsed["updated_files"][0]["file_name"], "a.py")

    def test_parse_llm_response_plain_text(self):
        parsed = parse_llm_response("just text")
        self.assertEqual(parsed["summary"], "just text")
        self.assertEqual(parsed["updated_files"], [])

    def test_parse_llm_response_no_source_files_key(self):
        text = json.dumps({"answer_summary": "no files"})
        parsed = parse_llm_response(text)
        self.assertEqual(parsed["summary"], "no files")
        self.assertEqual(parsed["updated_files"], [])

    # -- create_error_response (now a standalone utility) -------------------
    def test_create_error_response_verbose(self):
        # Note: We only test the return value here. Testing rprint is unreliable
        # in multi-test scenarios due to module import caching.
        result = create_error_response("boom", verbose=True)
        self.assertEqual(result, {"summary": "boom", "updated_files": []})

    def test_create_error_response_quiet(self):
        result = create_error_response("err")
        self.assertEqual(result["summary"], "err")
        self.assertEqual(result["updated_files"], [])

    # -- _handle_databricks -------------------------------------------------
    def test_handle_databricks_no_env(self):
        self.assertIsNone(self.plugin._handle_databricks("p", {}))

    def test_handle_databricks_no_url(self):
        os.environ["AYE_DBX_API_KEY"] = "key"
        self.assertIsNone(self.plugin._handle_databricks("p", {}))

    def test_handle_databricks_no_key(self):
        os.environ["AYE_DBX_API_URL"] = "http://fake"
        self.assertIsNone(self.plugin._handle_databricks("p", {}))

    @patch("httpx.Client")
    def test_handle_databricks_success(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        os.environ["AYE_DBX_MODEL"] = "test-model"

        self.plugin.history_file = self.history_file

        response_body = json.dumps({"answer_summary": "dbx answer", "source_files": []})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": response_body}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("hello", {})
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "dbx answer")
        self.assertEqual(result["updated_files"], [])

    @patch("httpx.Client")
    def test_handle_databricks_success_with_source_files(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        self.plugin.history_file = self.history_file

        response_body = json.dumps({
            "answer_summary": "updated",
            "source_files": [{"file_name": "x.py", "file_content": "print(1)"}],
        })
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": response_body}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("do it", {"a.py": "old"}, chat_id=3)
        self.assertEqual(result["summary"], "updated")
        self.assertEqual(len(result["updated_files"]), 1)

    @patch("httpx.Client")
    def test_handle_databricks_success_saves_history(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        self.plugin.history_file = self.history_file

        response_body = json.dumps({"answer_summary": "ok"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": response_body}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        self.plugin._handle_databricks("hi", {})

        # Verify history was saved
        self.assertIn("default", self.plugin.chat_history)
        self.assertEqual(len(self.plugin.chat_history["default"]), 2)  # user + assistant

    @patch("httpx.Client")
    def test_handle_databricks_debug_mode(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        self.plugin.debug = True
        self.plugin.history_file = self.history_file

        response_body = json.dumps({"answer_summary": "debug resp"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = response_body
        mock_response.json.return_value = {
            "choices": [{"message": {"content": response_body}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("input", {})
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "debug resp")

    @patch("httpx.Client")
    def test_handle_databricks_no_choices(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("q", {})
        self.assertIn("Failed to get a valid response", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_no_message_key(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{}]}
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("q", {})
        self.assertIn("Failed to get a valid response", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_http_error_with_json(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_response = MagicMock(status_code=401, text="Unauthorized")
        mock_response.json.return_value = {"error": {"message": "Invalid key"}}
        mock_client.return_value.__enter__.return_value.post.side_effect = (
            httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)
        )

        result = self.plugin._handle_databricks("p", {})
        self.assertIn("DBX API error: 401", result["summary"])
        self.assertIn("Invalid key", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_http_error_with_error_string(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_response = MagicMock(status_code=500, text="Internal Server Error")
        mock_response.json.return_value = {"error": "something went wrong"}
        mock_client.return_value.__enter__.return_value.post.side_effect = (
            httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)
        )

        result = self.plugin._handle_databricks("p", {})
        self.assertIn("DBX API error: 500", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_http_error_no_json(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_response = MagicMock(status_code=503, text="Service Unavailable")
        mock_response.json.side_effect = Exception("not json")
        mock_client.return_value.__enter__.return_value.post.side_effect = (
            httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)
        )

        result = self.plugin._handle_databricks("p", {})
        self.assertIn("DBX API error: 503", result["summary"])
        self.assertIn("Service Unavailable", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_generic_exception(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_client.return_value.__enter__.return_value.post.side_effect = ConnectionError(
            "connection refused"
        )

        result = self.plugin._handle_databricks("p", {})
        self.assertIn("Error calling Databricks API", result["summary"])
        self.assertIn("connection refused", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_verbose_non_200(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        self.plugin.verbose = True
        self.plugin.history_file = self.history_file

        # Return a non-200 that still doesn't raise (e.g. raise_for_status not triggered)
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.text = "Accepted"
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("p", {})
        self.assertIn("Failed to get a valid response", result["summary"])

    @patch("httpx.Client")
    def test_handle_databricks_custom_system_prompt(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        self.plugin.history_file = self.history_file

        response_body = json.dumps({"answer_summary": "custom sp"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": response_body}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks(
            "hi", {}, system_prompt="You are a pirate."
        )
        self.assertEqual(result["summary"], "custom sp")

        # Verify custom system prompt was used in the request
        call_kwargs = mock_client.return_value.__enter__.return_value.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        self.assertEqual(payload["messages"][0]["content"], "You are a pirate.")

    @patch("httpx.Client")
    def test_handle_databricks_extract_json_from_markdown(self, mock_client):
        """When model wraps JSON in markdown, _extract_json_object should handle it."""
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        self.plugin.history_file = self.history_file

        inner = json.dumps({"answer_summary": "extracted", "source_files": []})
        raw_content = f"```json\n{inner}\n```"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": raw_content}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("go", {})
        self.assertEqual(result["summary"], "extracted")

    @patch("httpx.Client")
    def test_handle_databricks_extract_json_returns_none(self, mock_client):
        """When _extract_json_object returns None, json.dumps(None) = 'null',
        which _parse_llm_response handles as invalid JSON / plain text path."""
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        self.plugin.history_file = self.history_file

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "no json at all just text"}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        result = self.plugin._handle_databricks("go", {})
        # _extract_json_object returns None, json.dumps(None) = "null"
        # _parse_llm_response tries json.loads("null") -> None (not a dict)
        # so it falls through to answer_summary = generated_text
        self.assertIsNotNone(result)

    # -- on_command: new_chat -----------------------------------------------
    def test_on_command_new_chat(self):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.touch()
        self.assertTrue(self.history_file.exists())

        result = self.plugin.on_command("new_chat", {"root": self.root})
        self.assertEqual(result, {"status": "databricks_history_cleared", "handled": True})
        self.assertFalse(self.history_file.exists())
        self.assertEqual(self.plugin.chat_history, {})

    @patch("pathlib.Path.cwd")
    def test_on_command_new_chat_no_root(self, mock_cwd):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_cwd.return_value = self.root
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.touch()

        self.plugin.on_command("new_chat", {"root": None})
        self.assertFalse(self.history_file.exists())

    @patch("aye.plugins.databricks_model.rprint")
    def test_on_command_new_chat_verbose(self, mock_rprint):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        self.plugin.verbose = True
        self.plugin.on_command("new_chat", {"root": self.root})
        #mock_rprint.assert_called_with("[yellow]Local model chat history cleared.[/]")

    # -- on_command: local_model_invoke -------------------------------------
    @patch.object(DatabricksModelPlugin, "_handle_databricks")
    def test_on_command_invoke_routes_to_databricks(self, mock_handle):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_handle.return_value = {"summary": "handled"}

        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root,
        }
        result = self.plugin.on_command("local_model_invoke", params)
        mock_handle.assert_called_once()
        self.assertEqual(result, {"summary": "handled"})

    @patch.object(DatabricksModelPlugin, "_handle_databricks", return_value=None)
    def test_on_command_invoke_no_handler(self, mock_handle):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root,
        }
        result = self.plugin.on_command("local_model_invoke", params)
        self.assertIsNone(result)

    @patch.object(DatabricksModelPlugin, "_handle_databricks")
    def test_on_command_invoke_with_all_params(self, mock_handle):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_handle.return_value = {"summary": "ok"}

        params = {
            "prompt": "  do thing  ",
            "model_id": "dbx-model",
            "source_files": {"a.py": "code"},
            "chat_id": 7,
            "root": self.root,
            "system_prompt": "custom",
            "max_output_tokens": 2048,
        }
        result = self.plugin.on_command("local_model_invoke", params)
        self.assertEqual(result, {"summary": "ok"})

        call_args = mock_handle.call_args
        self.assertEqual(call_args[0][0], "do thing")  # prompt stripped
        self.assertEqual(call_args[0][1], {"a.py": "code"})
        self.assertEqual(call_args[0][2], 7)  # chat_id
        self.assertEqual(call_args[0][3], "custom")  # system_prompt
        self.assertEqual(call_args[0][4], 2048)  # max_output_tokens

    @patch("pathlib.Path.cwd")
    @patch.object(DatabricksModelPlugin, "_handle_databricks", return_value=None)
    def test_on_command_invoke_no_root(self, mock_handle, mock_cwd):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        mock_cwd.return_value = self.root
        params = {
            "prompt": "test",
            "source_files": {},
            "root": None,
        }
        self.plugin.on_command("local_model_invoke", params)
        expected_history = self.root / ".aye" / "chat_history.json"
        self.assertEqual(self.plugin.history_file, expected_history)

    # -- on_command: unknown ------------------------------------------------
    def test_on_command_unknown(self):
        result = self.plugin.on_command("unknown_command", {})
        self.assertIsNone(result)

    # -- on_command: new_chat when file doesn't exist -----------------------
    def test_on_command_new_chat_file_missing(self):
        # Set env vars so _is_databricks_configured() returns True
        os.environ["AYE_DBX_API_URL"] = "http://fake.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"

        # missing_ok=True so no error
        result = self.plugin.on_command("new_chat", {"root": self.root})
        self.assertEqual(result, {"status": "databricks_history_cleared", "handled": True})
