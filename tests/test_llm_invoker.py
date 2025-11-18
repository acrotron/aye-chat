from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
import json
from pathlib import Path

import aye.controller.llm_invoker as llm_invoker
from aye.model.models import LLMResponse, LLMSource, VectorIndexResult

class TestLlmInvoker(TestCase):
    def setUp(self):
        self.conf = SimpleNamespace(
            root=Path('.'),
            file_mask='*.py',
            selected_model='test-model',
            index_manager=None # No index_manager by default
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
        # GIVEN: A small project that fits into context
        mock_collect_sources.return_value = self.source_files
        local_response = {
            "summary": "local summary",
            "updated_files": [{"file_name": "f1", "file_content": "c1"}]
        }
        self.plugin_manager.handle_command.return_value = local_response

        # WHEN: invoke_llm is called
        response = llm_invoker.invoke_llm(
            prompt="test prompt",
            conf=self.conf,
            console=self.console,
            plugin_manager=self.plugin_manager
        )

        # THEN: all source files are passed to the local model
        mock_collect_sources.assert_called_once_with(root_dir=str(self.conf.root), file_mask=self.conf.file_mask)
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
        # GIVEN: A small project and local model fails
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

        # WHEN: invoke_llm is called
        response = llm_invoker.invoke_llm(
            prompt="test prompt",
            conf=self.conf,
            console=self.console,
            plugin_manager=self.plugin_manager,
            chat_id=123
        )

        # THEN: all source files are passed to the API
        mock_collect_sources.assert_called_once_with(root_dir=str(self.conf.root), file_mask=self.conf.file_mask)
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

    @patch('aye.controller.llm_invoker.rprint')
    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    def test_invoke_llm_large_project_uses_rag(self, mock_spinner, mock_collect, mock_rprint):
        # GIVEN: A project larger than the context hard limit
        large_content = "a" * (llm_invoker.CONTEXT_HARD_LIMIT + 1)
        mock_collect.return_value = {"large.py": large_content}

        # And a mocked index manager that finds one relevant file
        mock_index_manager = MagicMock()
        mock_chunk = VectorIndexResult(file_path="relevant.py", score=0.9, content="relevant content")
        mock_index_manager.query.return_value = [mock_chunk]
        self.conf.index_manager = mock_index_manager
        
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        # WHEN: invoke_llm is called
        with patch('pathlib.Path.read_text', return_value="relevant content"), \
             patch('pathlib.Path.is_file', return_value=True):
            llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        # THEN: RAG is used instead of sending all files
        mock_collect.assert_called_once()
        mock_index_manager.query.assert_called_once()

        # And only the file from RAG is passed to the model
        self.plugin_manager.handle_command.assert_called_once()
        final_source_files = self.plugin_manager.handle_command.call_args[0][1]['source_files']
        self.assertIn("relevant.py", final_source_files)
        self.assertNotIn("large.py", final_source_files)

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
    def test_invoke_llm_verbose_mode_small_project(self, mock_spinner, mock_collect, mock_rprint):
        # GIVEN: a small project
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        # WHEN: invoking in verbose mode
        llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager, verbose=True)
        
        # THEN: correct verbose messages are printed
        size_kb = len(self.source_files['main.py'].encode('utf-8')) / 1024
        mock_rprint.assert_any_call(f"[cyan]Project size ({size_kb:.1f}KB) is small; including all files.[/]")
        mock_rprint.assert_any_call(f"[yellow]Included with prompt: {', '.join(self.source_files.keys())}[/]")

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

        mock_cli_invoke.assert_called_once_with(
            message="p",
            chat_id=-1,
            source_files=self.source_files,
            model=self.conf.selected_model
        )

        debug_prints = [call[0][0] for call in mock_print.call_args_list]
        self.assertIn("[DEBUG] Processing chat message with chat_id=-1, model=test-model", debug_prints)
        self.assertIn("[DEBUG] Chat message processed, response keys: dict_keys(['assistant_response', 'chat_id'])", debug_prints)
        self.assertIn("[DEBUG] Successfully parsed assistant_response JSON", debug_prints)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_with_explicit_source_files(self, mock_collect, mock_cli, mock_spinner):
        explicit_files = {"explicit.py": "content"}
        self.plugin_manager.handle_command.return_value = None # Fallback to API
        mock_cli.return_value = {"assistant_response": "{}", "chat_id": 1}

        llm_invoker.invoke_llm(
            "p", self.conf, self.console, self.plugin_manager,
            explicit_source_files=explicit_files
        )

        mock_collect.assert_not_called()
        mock_cli.assert_called_once()
        self.assertEqual(mock_cli.call_args[1]['source_files'], explicit_files)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_with_all_command(self, mock_collect, mock_cli, mock_spinner):
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None # Fallback to API
        mock_cli.return_value = {"assistant_response": "{}", "chat_id": 1}

        llm_invoker.invoke_llm("/all do something", self.conf, self.console, self.plugin_manager)

        mock_collect.assert_called_once()
        mock_cli.assert_called_once()
        self.assertEqual(mock_cli.call_args[1]['message'], "do something")
        self.assertEqual(mock_cli.call_args[1]['source_files'], self.source_files)

    @patch('aye.controller.llm_invoker.rprint')
    def test_get_rag_context_files_skips_large_file(self, mock_rprint):
        mock_index_manager = MagicMock()
        mock_chunk = VectorIndexResult(file_path="large.py", score=0.9, content="")
        mock_index_manager.query.return_value = [mock_chunk]
        self.conf.index_manager = mock_index_manager
        
        large_content = "a" * (llm_invoker.CONTEXT_HARD_LIMIT + 1)
        
        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', return_value=large_content):
            
            result = llm_invoker._get_rag_context_files("p", self.conf, verbose=True)

        self.assertEqual(result, {})
        mock_rprint.assert_any_call(f"[yellow]Skipping large file large.py ({len(large_content.encode('utf-8')) / 1024:.1f}KB) to stay within payload limits.[/]")

    def test_get_rag_context_files_no_chunks(self):
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = []
        self.conf.index_manager = mock_index_manager
        
        result = llm_invoker._get_rag_context_files("p", self.conf, verbose=False)
        self.assertEqual(result, {})

    @patch('aye.controller.llm_invoker.rprint')
    def test_get_rag_context_files_file_read_error(self, mock_rprint):
        mock_index_manager = MagicMock()
        mock_chunk = VectorIndexResult(file_path="bad.py", score=0.9, content="")
        mock_index_manager.query.return_value = [mock_chunk]
        self.conf.index_manager = mock_index_manager

        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', side_effect=IOError("read error")):
            
            result = llm_invoker._get_rag_context_files("p", self.conf, verbose=True)

        self.assertEqual(result, {})
        mock_rprint.assert_any_call("[red]Could not read file bad.py: read error[/red]")

    @patch('builtins.print')
    def test_parse_api_response_debug_mode(self, mock_print):
        llm_invoker.DEBUG = True
        
        # Test JSON failure
        llm_invoker._parse_api_response({"assistant_response": "not json"})
        debug_prints = [c[0][0] for c in mock_print.call_args_list]
        self.assertIn("[DEBUG] Failed to parse assistant_response as JSON: "
                      "Expecting value: line 1 column 1 (char 0). Treating as plain text.", debug_prints)
        
        mock_print.reset_mock()

        # Test JSON success
        llm_invoker._parse_api_response({"assistant_response": "{}"})
        debug_prints = [c[0][0] for c in mock_print.call_args_list]
        self.assertIn("[DEBUG] Successfully parsed assistant_response JSON", debug_prints)
