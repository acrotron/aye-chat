from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock
import json

import aye.controller.llm_invoker as llm_invoker
from aye.model.models import LLMResponse, LLMSource

class TestLlmInvoker(TestCase):
    def setUp(self):
        self.conf = SimpleNamespace(
            root='.',
            file_mask='*.py',
            selected_model='test-model'
        )
        self.console = MagicMock()
        self.plugin_manager = MagicMock()
        self.source_files = {"main.py": "print('hello')"}
        llm_invoker.DEBUG = False

    def tearDown(self):
        llm_invoker.DEBUG = False

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    def test_invoke_llm_local_model_success(self, mock_spinner, mock_collect_sources):
        mock_collect_sources.return_value = self.source_files
        local_response = {
            "summary": "local summary",
            "updated_files": [{"file_name": "f1", "file_content": "c1"}]
        }
        self.plugin_manager.handle_command.return_value = local_response

        response = llm_invoker.invoke_llm(
            prompt="test prompt",
            conf=self.conf,
            console=self.console,
            plugin_manager=self.plugin_manager
        )

        mock_collect_sources.assert_called_once_with(self.conf.root, self.conf.file_mask)
        self.plugin_manager.handle_command.assert_called_once_with(
            "local_model_invoke",
            {
                "prompt": "test prompt",
                "model_id": self.conf.selected_model,
                "source_files": self.source_files,
                "chat_id": None,
                "root": self.conf.root
            }
        )
        self.assertEqual(response.source, LLMSource.LOCAL)
        self.assertEqual(response.summary, "local summary")
        self.assertEqual(len(response.updated_files), 1)

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    def test_invoke_llm_api_fallback_success(self, mock_cli_invoke, mock_spinner, mock_collect_sources):
        mock_collect_sources.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None # Local model fails

        api_response_payload = {
            "answer_summary": "api summary",
            "source_files": [{"file_name": "f2", "file_content": "c2"}]
        }
        api_response = {
            "assistant_response": json.dumps(api_response_payload),
            "chat_id": 456
        }
        mock_cli_invoke.return_value = api_response

        response = llm_invoker.invoke_llm(
            prompt="test prompt",
            conf=self.conf,
            console=self.console,
            plugin_manager=self.plugin_manager,
            chat_id=123
        )

        mock_cli_invoke.assert_called_once_with(
            message="test prompt",
            chat_id=123,
            source_files=self.source_files,
            model=self.conf.selected_model
        )
        self.assertEqual(response.source, LLMSource.API)
        self.assertEqual(response.summary, "api summary")
        self.assertEqual(response.chat_id, 456)
        self.assertEqual(len(response.updated_files), 1)

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    def test_invoke_llm_api_plain_text_response(self, mock_cli_invoke, mock_spinner, mock_collect_sources):
        mock_collect_sources.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None

        api_response = {
            "assistant_response": "just plain text",
            "chat_id": 789
        }
        mock_cli_invoke.return_value = api_response

        response = llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        self.assertEqual(response.summary, "just plain text")
        self.assertEqual(response.updated_files, [])
        self.assertEqual(response.chat_id, 789)

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    def test_invoke_llm_api_server_error_in_response(self, mock_cli_invoke, mock_spinner, mock_collect_sources):
        mock_collect_sources.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None

        api_response = {
            "assistant_response": "An error occurred on the server.",
            "chat_title": "My Chat"
        }
        mock_cli_invoke.return_value = api_response

        with self.assertRaisesRegex(Exception, "Server error in chat 'My Chat'"):
            llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    def test_invoke_llm_api_no_assistant_response(self, mock_cli_invoke, mock_spinner, mock_collect_sources):
        mock_collect_sources.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None

        api_response = {"chat_id": 111} # Missing 'assistant_response'
        mock_cli_invoke.return_value = api_response

        response = llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        self.assertEqual(response.summary, "No response from assistant.")
        self.assertEqual(response.updated_files, [])
        self.assertEqual(response.chat_id, 111)

    @patch('aye.controller.llm_invoker.rprint')
    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    def test_invoke_llm_verbose_mode(self, mock_spinner, mock_collect, mock_rprint):
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager, verbose=True)
        mock_rprint.assert_any_call(f"[yellow]Included with prompt: {', '.join(self.source_files.keys())}")

    @patch('builtins.print')
    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    def test_invoke_llm_debug_mode(self, mock_cli_invoke, mock_spinner, mock_collect, mock_print):
        llm_invoker.DEBUG = True
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None
        mock_cli_invoke.return_value = {
            "assistant_response": json.dumps({"answer_summary": "s"}),
            "chat_id": 123
        }

        llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        debug_prints = [call[0][0] for call in mock_print.call_args_list]
        self.assertIn("[DEBUG] Processing chat message with chat_id=-1, model=test-model", debug_prints)
        self.assertIn("[DEBUG] Chat message processed, response keys: dict_keys(['assistant_response', 'chat_id'])", debug_prints)
        self.assertIn("[DEBUG] Successfully parsed assistant_response JSON", debug_prints)
