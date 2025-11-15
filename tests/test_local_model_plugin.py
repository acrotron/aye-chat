import os
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import httpx

import aye.plugins.local_model

from aye.plugins.local_model import LocalModelPlugin

class TestLocalModelPlugin(TestCase):
    def setUp(self):
        self.plugin = LocalModelPlugin()
        self.plugin.init({"verbose": False})
        
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.history_file = self.root / ".aye" / "chat_history.json"
        
        # Clear any environment variables that might interfere
        for key in ["AYE_DBX_API_URL", "AYE_DBX_API_KEY", "AYE_LLM_API_URL", "AYE_LLM_API_KEY", "GEMINI_API_KEY"]:
            if key in os.environ:
                del os.environ[key]

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_init(self):
        self.assertEqual(self.plugin.name, "local_model")
        self.assertFalse(self.plugin.verbose)

    @patch('aye.plugins.local_model.rprint')
    def test_init_verbose(self, mock_rprint):
        self.plugin.init({"verbose": True})
        self.assertTrue(self.plugin.verbose)
        #mock_rprint.assert_called_once_with(f"[bold yellow]Initializing {self.plugin.name} v{self.plugin.version}[/]")

    def test_history_load_save_roundtrip(self):
        self.plugin.history_file = self.history_file
        
        # Initially, history is empty
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})
        
        # Add some history and save
        self.plugin.chat_history = {"default": [{"role": "user", "content": "hi"}]}
        self.plugin._save_history()
        
        self.assertTrue(self.history_file.exists())
        
        # Clear in-memory history and reload
        self.plugin.chat_history = {}
        self.plugin._load_history()
        
        self.assertEqual(self.plugin.chat_history, {"default": [{"role": "user", "content": "hi"}]})

    def test_history_load_file_not_found(self):
        self.plugin.history_file = self.history_file
        self.assertFalse(self.history_file.exists())
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

    def test_history_load_invalid_json(self):
        self.history_file.parent.mkdir(exist_ok=True)
        self.history_file.write_text("not json")
        self.plugin.history_file = self.history_file
        self.plugin._load_history()
        self.assertEqual(self.plugin.chat_history, {})

    @patch('aye.plugins.local_model.rprint')
    def test_history_no_file_path(self, mock_rprint):
        self.plugin.init({"verbose": True})
        self.plugin.history_file = None
        self.plugin._load_history()
        #mock_rprint.assert_any_call("[yellow]History file path not set for local model. Skipping load.[/]")
        self.plugin._save_history()
        #mock_rprint.assert_any_call("[yellow]History file path not set for local model. Skipping save.[/]")

    def test_build_user_message(self):
        prompt = "My prompt"
        source_files = {"file1.py": "content1"}
        message = self.plugin._build_user_message(prompt, source_files)
        self.assertIn(prompt, message)
        self.assertIn("--- Source files are below. ---", message)
        self.assertIn("** file1.py **", message)
        self.assertIn("content1", message)

    def test_parse_llm_response_valid_json(self):
        llm_text = json.dumps({
            "answer_summary": "summary",
            "source_files": [{"file_name": "f1", "file_content": "c1"}]
        })
        parsed = self.plugin._parse_llm_response(llm_text)
        self.assertEqual(parsed["summary"], "summary")
        self.assertEqual(len(parsed["updated_files"]), 1)
        self.assertEqual(parsed["updated_files"][0]["file_name"], "f1")

    def test_parse_llm_response_plain_text(self):
        llm_text = "just plain text"
        parsed = self.plugin._parse_llm_response(llm_text)
        self.assertEqual(parsed["summary"], "just plain text")
        self.assertEqual(parsed["updated_files"], [])

    @patch('httpx.Client')
    def test_handle_openai_compatible_success(self, mock_client):
        os.environ["AYE_LLM_API_URL"] = "http://fake.api"
        os.environ["AYE_LLM_API_KEY"] = "fake_key"
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"answer_summary": "openai response"})}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        result = self.plugin._handle_openai_compatible("prompt", {})
        
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "openai response")

    @patch('httpx.Client')
    def test_handle_openai_compatible_http_error(self, mock_client):
        os.environ["AYE_LLM_API_URL"] = "http://fake.api"
        os.environ["AYE_LLM_API_KEY"] = "fake_key"
        mock_response = MagicMock(status_code=401, text="Unauthorized")
        mock_response.json.return_value = {"error": {"message": "Invalid API key"}}
        mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_response
        )
        result = self.plugin._handle_openai_compatible("prompt", {})
        self.assertIn("OpenAI API error: 401 - Invalid API key", result["summary"])

    def test_handle_openai_no_key(self):
        self.assertIsNone(self.plugin._handle_openai_compatible("p", {}))

    @patch('httpx.Client')
    def test_handle_databricks_success(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.dbx.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": [{"type": "text", "text": json.dumps({"answer_summary": "dbx response"})}]}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        result = self.plugin._handle_databricks("prompt", {})
        
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "dbx response")

    @patch('httpx.Client')
    def test_handle_databricks_http_error(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.dbx.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        mock_response = MagicMock(status_code=500, text="Server Error")
        mock_response.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_response
        )
        result = self.plugin._handle_databricks("prompt", {})
        self.assertIn("DBX API error: 500 - Server Error", result["summary"])

    @patch('httpx.Client')
    def test_handle_databricks_generic_error(self, mock_client):
        os.environ["AYE_DBX_API_URL"] = "http://fake.dbx.api"
        os.environ["AYE_DBX_API_KEY"] = "fake_key"
        mock_client.return_value.__enter__.return_value.post.side_effect = Exception("Network down")
        result = self.plugin._handle_databricks("prompt", {})
        self.assertIn("Error calling Databricks API: Network down", result["summary"])

    def test_handle_databricks_no_key(self):
        self.assertIsNone(self.plugin._handle_databricks("p", {}))

    @patch('httpx.Client')
    def test_handle_gemini_success(self, mock_client):
        os.environ["GEMINI_API_KEY"] = "fake_key"
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"answer_summary": "gemini response"})}]}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response
        
        result = self.plugin._handle_gemini_pro_25("prompt", {})
        
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], "gemini response")

    @patch('httpx.Client')
    def test_handle_gemini_http_error(self, mock_client):
        os.environ["GEMINI_API_KEY"] = "fake_key"
        mock_response = MagicMock(status_code=400, text="Bad Request")
        mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_response
        )
        result = self.plugin._handle_gemini_pro_25("prompt", {})
        self.assertIn("Gemini API error: 400 - Bad Request", result["summary"])

    def test_handle_gemini_no_key(self):
        self.assertIsNone(self.plugin._handle_gemini_pro_25("p", {}))

    def test_on_command_new_chat(self):
        self.history_file.parent.mkdir(exist_ok=True)
        self.history_file.touch()
        self.assertTrue(self.history_file.exists())
        
        result = self.plugin.on_command("new_chat", {"root": self.root})
        
        self.assertEqual(result, {"status": "local_history_cleared"})
        self.assertFalse(self.history_file.exists())
        self.assertEqual(self.plugin.chat_history, {})

    @patch('pathlib.Path.cwd')
    def test_on_command_new_chat_no_root(self, mock_cwd):
        mock_cwd.return_value = self.root

        # The file that should be unlinked
        history_file = self.root / ".aye" / "chat_history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.touch()
        self.assertTrue(history_file.exists())

        self.plugin.on_command("new_chat", {"root": None})

        self.assertFalse(history_file.exists())

    @patch.object(LocalModelPlugin, '_handle_openai_compatible')
    def test_on_command_invoke_routes_to_openai(self, mock_handle_openai):
        mock_handle_openai.return_value = {"summary": "openai handled"}
        
        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root
        }
        result = self.plugin.on_command("local_model_invoke", params)
        
        mock_handle_openai.assert_called_once()
        self.assertEqual(result, {"summary": "openai handled"})

    @patch.object(LocalModelPlugin, '_handle_openai_compatible', return_value=None)
    @patch.object(LocalModelPlugin, '_handle_databricks')
    def test_on_command_invoke_falls_back_to_dbx(self, mock_handle_dbx, mock_handle_openai):
        mock_handle_dbx.return_value = {"summary": "dbx handled"}
        
        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root
        }
        result = self.plugin.on_command("local_model_invoke", params)
        
        mock_handle_openai.assert_called_once()
        mock_handle_dbx.assert_called_once()
        self.assertEqual(result, {"summary": "dbx handled"})

    @patch.object(LocalModelPlugin, '_handle_openai_compatible', return_value=None)
    @patch.object(LocalModelPlugin, '_handle_databricks', return_value=None)
    @patch.object(LocalModelPlugin, '_handle_gemini_pro_25')
    def test_on_command_invoke_routes_to_gemini_by_id(self, mock_handle_gemini, mock_handle_dbx, mock_handle_openai):
        mock_handle_gemini.return_value = {"summary": "gemini handled"}
        
        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root,
            "model_id": "google/gemini-2.5-pro"
        }
        result = self.plugin.on_command("local_model_invoke", params)
        
        mock_handle_openai.assert_called_once()
        mock_handle_dbx.assert_called_once()
        mock_handle_gemini.assert_called_once()
        self.assertEqual(result, {"summary": "gemini handled"})

    def test_on_command_invoke_no_handlers(self):
        # No env vars set, so all handlers should return None
        params = {
            "prompt": "test",
            "source_files": {},
            "root": self.root
        }
        result = self.plugin.on_command("local_model_invoke", params)
        self.assertIsNone(result)
