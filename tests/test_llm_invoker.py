from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call, PropertyMock
import json
import os
from pathlib import Path

import aye.controller.llm_invoker as llm_invoker
from aye.model.models import LLMResponse, LLMSource, VectorIndexResult
from aye.model.config import SYSTEM_PROMPT, DEFAULT_MAX_OUTPUT_TOKENS

# Default hard limit used in llm_invoker for unknown models
CONTEXT_HARD_LIMIT = 170 * 1024


class TestIsDebug(TestCase):
    """Tests for _is_debug function."""

    @patch('aye.controller.llm_invoker.get_user_config', return_value='on')
    def test_is_debug_on(self, mock_get_cfg):
        self.assertTrue(llm_invoker._is_debug())
        mock_get_cfg.assert_called_once_with("debug", "off")

    @patch('aye.controller.llm_invoker.get_user_config', return_value='off')
    def test_is_debug_off(self, mock_get_cfg):
        self.assertFalse(llm_invoker._is_debug())

    @patch('aye.controller.llm_invoker.get_user_config', return_value='OFF')
    def test_is_debug_off_uppercase(self, mock_get_cfg):
        self.assertFalse(llm_invoker._is_debug())

    @patch('aye.controller.llm_invoker.get_user_config', return_value='ON')
    def test_is_debug_on_uppercase(self, mock_get_cfg):
        self.assertTrue(llm_invoker._is_debug())


class TestGetIntEnv(TestCase):
    """Tests for _get_int_env function."""

    def test_get_int_env_valid_value(self):
        with patch.dict(os.environ, {'TEST_VAR': '42'}):
            result = llm_invoker._get_int_env('TEST_VAR', 10)
            self.assertEqual(result, 42)

    def test_get_int_env_invalid_value(self):
        with patch.dict(os.environ, {'TEST_VAR': 'not_a_number'}):
            result = llm_invoker._get_int_env('TEST_VAR', 10)
            self.assertEqual(result, 10)

    def test_get_int_env_unset(self):
        # Ensure the var is not set
        env_copy = os.environ.copy()
        if 'UNSET_TEST_VAR' in env_copy:
            del env_copy['UNSET_TEST_VAR']
        with patch.dict(os.environ, env_copy, clear=True):
            result = llm_invoker._get_int_env('UNSET_TEST_VAR', 99)
            self.assertEqual(result, 99)

    def test_get_int_env_empty_string(self):
        with patch.dict(os.environ, {'TEST_VAR': ''}):
            result = llm_invoker._get_int_env('TEST_VAR', 50)
            self.assertEqual(result, 50)

    def test_get_int_env_negative_value(self):
        with patch.dict(os.environ, {'TEST_VAR': '-100'}):
            result = llm_invoker._get_int_env('TEST_VAR', 10)
            self.assertEqual(result, -100)


class TestGetModelConfig(TestCase):
    """Tests for _get_model_config function."""

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "model-1", "name": "Model 1", "max_prompt_kb": 100},
        {"id": "model-2", "name": "Model 2", "max_prompt_kb": 200}
    ])
    def test_get_model_config_found(self):
        config = llm_invoker._get_model_config("model-1")
        self.assertIsNotNone(config)
        self.assertEqual(config["name"], "Model 1")
        self.assertEqual(config["max_prompt_kb"], 100)

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "model-1", "name": "Model 1"}
    ])
    def test_get_model_config_not_found(self):
        config = llm_invoker._get_model_config("nonexistent")
        self.assertIsNone(config)

    @patch('aye.controller.llm_invoker.MODELS', [])
    def test_get_model_config_empty_models(self):
        config = llm_invoker._get_model_config("any-model")
        self.assertIsNone(config)


class TestGetContextTargetSize(TestCase):
    """Tests for _get_context_target_size function."""

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "test-model", "context_target_kb": 50}
    ])
    def test_get_context_target_size_from_model_config(self):
        with patch.dict(os.environ, {}, clear=True):
            size = llm_invoker._get_context_target_size("test-model")
            self.assertEqual(size, 50 * 1024)

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "test-model", "context_target_kb": 50}
    ])
    def test_get_context_target_size_env_override(self):
        with patch.dict(os.environ, {'AYE_CONTEXT_TARGET': '102400'}):
            size = llm_invoker._get_context_target_size("test-model")
            self.assertEqual(size, 102400)

    @patch('aye.controller.llm_invoker.MODELS', [])
    @patch('aye.controller.llm_invoker.DEFAULT_CONTEXT_TARGET_KB', 100)
    def test_get_context_target_size_default_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            size = llm_invoker._get_context_target_size("unknown-model")
            self.assertEqual(size, 100 * 1024)

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "test-model", "context_target_kb": 50}
    ])
    def test_get_context_target_size_invalid_env(self):
        with patch.dict(os.environ, {'AYE_CONTEXT_TARGET': 'invalid'}):
            size = llm_invoker._get_context_target_size("test-model")
            self.assertEqual(size, 50 * 1024)


class TestGetContextHardLimit(TestCase):
    """Tests for _get_context_hard_limit function."""

    @patch('aye.controller.llm_invoker.MODELS', [
        {"id": "test-model", "max_prompt_kb": 200}
    ])
    def test_get_context_hard_limit_from_model_config(self):
        limit = llm_invoker._get_context_hard_limit("test-model")
        self.assertEqual(limit, 200 * 1024)

    @patch('aye.controller.llm_invoker.MODELS', [])
    def test_get_context_hard_limit_default_fallback(self):
        limit = llm_invoker._get_context_hard_limit("unknown-model")
        self.assertEqual(limit, 170 * 1024)


class TestFilterGroundTruth(TestCase):
    """Tests for _filter_ground_truth function."""

    def test_filter_ground_truth_no_ground_truth(self):
        conf = SimpleNamespace(root=Path('.'))
        files = {"file1.py": "content1", "file2.py": "content2"}
        result = llm_invoker._filter_ground_truth(files, conf, verbose=False)
        self.assertEqual(result, files)

    def test_filter_ground_truth_empty_ground_truth(self):
        conf = SimpleNamespace(root=Path('.'), ground_truth="")
        files = {"file1.py": "content1"}
        result = llm_invoker._filter_ground_truth(files, conf, verbose=False)
        self.assertEqual(result, files)

    def test_filter_ground_truth_matches_file(self):
        conf = SimpleNamespace(root=Path('.'), ground_truth="ground truth content")
        files = {
            "file1.py": "other content",
            "gt.txt": "ground truth content",
            "file2.py": "more content"
        }
        result = llm_invoker._filter_ground_truth(files, conf, verbose=False)
        self.assertEqual(result, {"file1.py": "other content", "file2.py": "more content"})
        self.assertNotIn("gt.txt", result)

    @patch('aye.controller.llm_invoker.rprint')
    def test_filter_ground_truth_verbose(self, mock_rprint):
        conf = SimpleNamespace(root=Path('.'), ground_truth="ground truth content")
        files = {"gt.txt": "ground truth content"}
        llm_invoker._filter_ground_truth(files, conf, verbose=True)
        mock_rprint.assert_called_once()
        self.assertIn("Excluding ground truth", str(mock_rprint.call_args))


class TestGetRagContextFiles(TestCase):
    """Tests for _get_rag_context_files function."""

    def setUp(self):
        self.conf = SimpleNamespace(
            root=Path('.'),
            file_mask='*.py',
            selected_model='test-model',
            index_manager=None
        )

    def test_no_index_manager(self):
        result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)
        self.assertEqual(result, {})

    def test_no_index_manager_attribute(self):
        conf_no_attr = SimpleNamespace(root=Path('.'), file_mask='*.py')
        result = llm_invoker._get_rag_context_files("prompt", conf_no_attr, verbose=False)
        self.assertEqual(result, {})

    @patch('aye.controller.llm_invoker.rprint')
    def test_verbose_searching_message(self, mock_rprint):
        mock_index_manager = MagicMock()
        mock_index_manager.query.return_value = []
        self.conf.index_manager = mock_index_manager

        llm_invoker._get_rag_context_files("prompt", self.conf, verbose=True)
        mock_rprint.assert_called_with("[cyan]Searching for relevant context...[/]")

    @patch('aye.controller.llm_invoker.rprint')
    @patch('aye.controller.llm_invoker.get_user_config', return_value='on')
    def test_debug_mode_prints_chunks(self, mock_get_cfg, mock_rprint):
        mock_index_manager = MagicMock()
        chunk = VectorIndexResult(file_path="test.py", score=0.85, content="content")
        mock_index_manager.query.return_value = [chunk]
        self.conf.index_manager = mock_index_manager

        with patch('pathlib.Path.is_file', return_value=False):
            llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        # Check debug output was printed
        calls = [str(c) for c in mock_rprint.call_args_list]
        self.assertTrue(any("Retrieved context chunks" in c for c in calls))

    def test_file_not_found(self):
        mock_index_manager = MagicMock()
        chunk = VectorIndexResult(file_path="missing.py", score=0.9, content="")
        mock_index_manager.query.return_value = [chunk]
        self.conf.index_manager = mock_index_manager

        with patch('pathlib.Path.is_file', return_value=False):
            result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        self.assertEqual(result, {})

    def test_deduplicates_files(self):
        mock_index_manager = MagicMock()
        chunks = [
            VectorIndexResult(file_path="same.py", score=0.9, content="chunk1"),
            VectorIndexResult(file_path="same.py", score=0.8, content="chunk2"),
            VectorIndexResult(file_path="other.py", score=0.7, content="chunk3"),
        ]
        mock_index_manager.query.return_value = chunks
        self.conf.index_manager = mock_index_manager

        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', return_value="content"):
            result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        self.assertIn("same.py", result)
        self.assertIn("other.py", result)
        self.assertEqual(len(result), 2)

    @patch('aye.controller.llm_invoker._get_context_target_size', return_value=500)
    @patch('aye.controller.llm_invoker._get_context_hard_limit', return_value=2000)
    def test_stops_at_context_target_size(self, mock_hard_limit, mock_target_size):
        """Test that file collection stops when target size is exceeded."""
        mock_index_manager = MagicMock()
        chunks = [
            VectorIndexResult(file_path="file1.py", score=0.9, content=""),
            VectorIndexResult(file_path="file2.py", score=0.8, content=""),
            VectorIndexResult(file_path="file3.py", score=0.7, content=""),
        ]
        mock_index_manager.query.return_value = chunks
        self.conf.index_manager = mock_index_manager

        # file1.py has 600 bytes - after adding it, current_size (600) > target (500)
        # so the loop should break before reading file2.py
        file_contents = {
            "file1.py": "a" * 600,
            "file2.py": "b" * 100,
            "file3.py": "c" * 100,
        }

        def mock_read_text(self, encoding=None):
            # Extract filename from the path
            filename = str(self).split('/')[-1]
            return file_contents.get(filename, "")

        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', mock_read_text):
            result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        # Only file1.py should be included because after adding it,
        # current_size (600) > context_target_size (500), so loop breaks
        self.assertEqual(len(result), 1)
        self.assertIn("file1.py", result)

    @patch('aye.controller.llm_invoker._get_context_hard_limit', return_value=1000)
    @patch('aye.controller.llm_invoker._get_context_target_size', return_value=2000)
    def test_respects_context_hard_limit(self, mock_target, mock_hard_limit):
        """Test that files exceeding hard limit are skipped."""
        mock_index_manager = MagicMock()
        chunks = [
            VectorIndexResult(file_path="file1.py", score=0.9, content=""),
            VectorIndexResult(file_path="file2.py", score=0.8, content=""),
        ]
        mock_index_manager.query.return_value = chunks
        self.conf.index_manager = mock_index_manager

        # file1.py is 600 bytes, file2.py is 600 bytes
        # Together they would be 1200 > hard_limit (1000)
        # So file2 should be skipped
        file_contents = {
            "file1.py": "a" * 600,
            "file2.py": "b" * 600,
        }

        def mock_read_text(self, encoding=None):
            filename = str(self).split('/')[-1]
            return file_contents.get(filename, "")

        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', mock_read_text):
            result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        # Only file1 should be included, file2 would exceed hard limit
        self.assertEqual(len(result), 1)
        self.assertIn("file1.py", result)

    @patch('aye.controller.llm_invoker._get_context_hard_limit', return_value=500)
    @patch('aye.controller.llm_invoker._get_context_target_size', return_value=2000)
    def test_skips_individual_large_files(self, mock_target, mock_hard_limit):
        """Test that individual files larger than remaining space are skipped."""
        mock_index_manager = MagicMock()
        chunks = [
            VectorIndexResult(file_path="large.py", score=0.9, content=""),
            VectorIndexResult(file_path="small.py", score=0.8, content=""),
        ]
        mock_index_manager.query.return_value = chunks
        self.conf.index_manager = mock_index_manager

        file_contents = {
            "large.py": "a" * 600,  # Too large (600 > 500 hard limit)
            "small.py": "b" * 100,  # Small enough
        }

        def mock_read_text(self, encoding=None):
            filename = str(self).split('/')[-1]
            return file_contents.get(filename, "")

        with patch('pathlib.Path.is_file', return_value=True), \
             patch('pathlib.Path.read_text', mock_read_text):
            result = llm_invoker._get_rag_context_files("prompt", self.conf, verbose=False)

        # large.py should be skipped (too big), small.py should be included
        self.assertEqual(len(result), 1)
        self.assertIn("small.py", result)
        self.assertNotIn("large.py", result)


class TestDetermineSourceFiles(TestCase):
    """Tests for _determine_source_files function."""

    def setUp(self):
        self.conf = SimpleNamespace(
            root=Path('.'),
            file_mask='*.py',
            selected_model='test-model',
            index_manager=None
        )

    def test_explicit_source_files_returned_directly(self):
        explicit = {"explicit.py": "content"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, False, explicit
        )
        self.assertEqual(result, explicit)
        self.assertFalse(use_all)
        self.assertEqual(prompt, "prompt")

    @patch('aye.controller.llm_invoker.rprint')
    def test_home_directory_skips_scan(self, mock_rprint):
        self.conf.root = Path.home()
        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, verbose=True, explicit_source_files=None
        )
        self.assertEqual(result, {})
        self.assertFalse(use_all)
        mock_rprint.assert_called_with("[cyan]In home directory: skipping file scan, using empty context.[/]")

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_all_command_exact(self, mock_collect):
        mock_collect.return_value = {"file.py": "content"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "/all", self.conf, False, None
        )
        self.assertTrue(use_all)
        self.assertEqual(prompt, "")

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_all_command_with_space_and_text(self, mock_collect):
        mock_collect.return_value = {"file.py": "content"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "/all do something", self.conf, False, None
        )
        self.assertTrue(use_all)
        self.assertEqual(prompt, "do something")

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_all_command_with_tab(self, mock_collect):
        mock_collect.return_value = {"file.py": "content"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "/all\tdo something", self.conf, False, None
        )
        self.assertTrue(use_all)
        self.assertEqual(prompt, "do something")

    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.rprint')
    def test_rag_disabled_uses_all_files(self, mock_rprint, mock_collect):
        """Test that when use_rag=False, all files are included."""
        self.conf.use_rag = False
        mock_collect.return_value = {"file.py": "content"}

        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, verbose=True, explicit_source_files=None
        )

        self.assertTrue(use_all)
        self.assertEqual(result, {"file.py": "content"})
        mock_rprint.assert_called_with("[cyan]Small project mode: including all files.[/]")

    @patch('aye.controller.llm_invoker._get_rag_context_files')
    @patch('aye.controller.llm_invoker.rprint')
    def test_rag_enabled_uses_vector_search(self, mock_rprint, mock_rag):
        """Test that when use_rag=True and index_manager exists, RAG is used."""
        self.conf.use_rag = True
        self.conf.index_manager = MagicMock()
        mock_rag.return_value = {"relevant.py": "content"}

        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, verbose=True, explicit_source_files=None
        )

        self.assertFalse(use_all)
        self.assertEqual(result, {"relevant.py": "content"})
        mock_rprint.assert_called_with("[cyan]Using code lookup for context...[/]")

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_all_as_prefix_not_command(self, mock_collect):
        """Test that /allfiles is not treated as /all command."""
        mock_collect.return_value = {"file.py": "content"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "/allfiles", self.conf, False, None
        )
        # Should not be treated as /all command since 5th char is not whitespace
        self.assertTrue(use_all)  # Small project, all files included
        self.assertEqual(prompt, "/allfiles")  # Prompt unchanged

    @patch('aye.controller.llm_invoker.collect_sources')
    def test_small_project_includes_all(self, mock_collect):
        mock_collect.return_value = {"small.py": "print('hi')"}
        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, False, None
        )
        self.assertTrue(use_all)
        self.assertEqual(result, {"small.py": "print('hi')"})

    @patch('aye.controller.llm_invoker._get_rag_context_files')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_large_project_uses_rag(self, mock_collect, mock_rag):
        large_content = "a" * (CONTEXT_HARD_LIMIT + 1)
        mock_collect.return_value = {"large.py": large_content}
        mock_rag.return_value = {"relevant.py": "content"}

        # Set up index_manager so RAG path is triggered
        self.conf.index_manager = MagicMock()
        self.conf.use_rag = True  # Explicitly enable RAG

        result, use_all, prompt = llm_invoker._determine_source_files(
            "prompt", self.conf, False, None
        )

        self.assertFalse(use_all)
        self.assertEqual(result, {"relevant.py": "content"})
        mock_rag.assert_called_once()  # Verify RAG was actually called


class TestPrintContextMessage(TestCase):
    """Tests for _print_context_message function."""

    @patch('aye.controller.llm_invoker.rprint')
    def test_verbose_with_files(self, mock_rprint):
        source_files = {"a.py": "content", "b.py": "content"}
        llm_invoker._print_context_message(source_files, False, None, verbose=True)
        mock_rprint.assert_called_with("[yellow]Included with prompt: a.py, b.py[/]")

    @patch('aye.controller.llm_invoker.rprint')
    def test_verbose_no_files(self, mock_rprint):
        llm_invoker._print_context_message({}, False, None, verbose=True)
        mock_rprint.assert_called_with("[yellow]No files found to include with prompt.[/]")

    @patch('aye.controller.llm_invoker.rprint')
    def test_non_verbose_with_files(self, mock_rprint):
        source_files = {"a.py": "content"}
        llm_invoker._print_context_message(source_files, False, None, verbose=False)
        mock_rprint.assert_not_called()

    @patch('aye.controller.llm_invoker.rprint')
    def test_non_verbose_no_files(self, mock_rprint):
        llm_invoker._print_context_message({}, False, None, verbose=False)
        mock_rprint.assert_not_called()


class TestParseApiResponse(TestCase):
    """Tests for _parse_api_response function."""

    def test_valid_json_response(self):
        resp = {
            "assistant_response": json.dumps({"answer_summary": "hello", "source_files": []}),
            "chat_id": 123
        }
        parsed, chat_id = llm_invoker._parse_api_response(resp)
        self.assertEqual(parsed["answer_summary"], "hello")
        self.assertEqual(chat_id, 123)

    def test_no_assistant_response(self):
        resp = {"chat_id": 456}
        parsed, chat_id = llm_invoker._parse_api_response(resp)
        self.assertEqual(parsed["answer_summary"], "No response from assistant.")
        self.assertEqual(parsed["source_files"], [])
        self.assertEqual(chat_id, 456)

    def test_plain_text_response(self):
        resp = {"assistant_response": "just plain text", "chat_id": 789}
        parsed, chat_id = llm_invoker._parse_api_response(resp)
        self.assertEqual(parsed["answer_summary"], "just plain text")
        self.assertEqual(parsed["source_files"], [])

    def test_server_error_in_response(self):
        resp = {
            "assistant_response": "An error occurred",
            "chat_title": "Test Chat"
        }
        with self.assertRaisesRegex(Exception, "Server error in chat 'Test Chat'"):
            llm_invoker._parse_api_response(resp)

    def test_server_error_no_chat_title(self):
        resp = {"assistant_response": "Internal error occurred"}
        with self.assertRaisesRegex(Exception, "Server error in chat 'Unknown'"):
            llm_invoker._parse_api_response(resp)

    @patch('aye.controller.llm_invoker.is_truncated_json', return_value=True)
    def test_truncated_json_response(self, mock_is_truncated):
        resp = {"assistant_response": '{"answer_summary": "partial', "chat_id": 111}
        parsed, chat_id = llm_invoker._parse_api_response(resp)
        self.assertIn("cut off", parsed["answer_summary"])
        self.assertEqual(parsed["source_files"], [])
        self.assertEqual(chat_id, 111)

    @patch('builtins.print')
    @patch('aye.controller.llm_invoker.is_truncated_json', return_value=True)
    @patch('aye.controller.llm_invoker.get_user_config', return_value='on')
    def test_truncated_json_debug_output(self, mock_cfg, mock_is_truncated, mock_print):
        resp = {"assistant_response": '{"partial', "chat_id": 111}
        llm_invoker._parse_api_response(resp)

        debug_prints = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any("truncated" in c.lower() for c in debug_prints))


class TestLlmInvoker(TestCase):
    def setUp(self):
        self.conf = SimpleNamespace(
            root=Path('.'),
            file_mask='*.py',
            selected_model='test-model',
            index_manager=None
        )
        self.console = MagicMock()
        self.plugin_manager = MagicMock()
        self.source_files = {"main.py": "print('hello')"}

        # Telemetry is global, in-memory state. Reset between tests to avoid leakage.
        llm_invoker.telemetry.reset()
        llm_invoker.telemetry.set_enabled(False)

    def tearDown(self):
        pass

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

        mock_collect_sources.assert_called_once_with(root_dir=str(self.conf.root), file_mask=self.conf.file_mask)
        self.plugin_manager.handle_command.assert_called_once_with(
            "local_model_invoke",
            {
                "prompt": "test prompt",
                "model_id": self.conf.selected_model,
                "source_files": self.source_files,
                "chat_id": None,
                "root": self.conf.root,
                "system_prompt": SYSTEM_PROMPT,
                "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS
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
        self.plugin_manager.handle_command.return_value = None

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

        mock_collect_sources.assert_called_once_with(root_dir=str(self.conf.root), file_mask=self.conf.file_mask)
        mock_cli_invoke.assert_called_once_with(
            message="test prompt",
            chat_id=123,
            source_files=self.source_files,
            model=self.conf.selected_model,
            system_prompt=SYSTEM_PROMPT,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            telemetry=None
        )
        self.assertEqual(response.source, LLMSource.API)
        self.assertEqual(response.summary, "api summary")
        self.assertEqual(response.chat_id, 456)
        self.assertEqual(len(response.updated_files), 1)

    @patch('aye.controller.llm_invoker.rprint')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    def test_invoke_llm_large_project_uses_rag(self, mock_spinner, mock_rprint):
        """Test that large projects use RAG to select relevant files."""
        large_content = "a" * (CONTEXT_HARD_LIMIT + 1)

        # Mock _determine_source_files to simulate large project behavior
        mock_index_manager = MagicMock()
        self.conf.index_manager = mock_index_manager

        # The plugin returns a successful response
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        # Patch _determine_source_files to return RAG-selected files (simulating large project)
        with patch.object(llm_invoker, '_determine_source_files') as mock_determine:
            mock_determine.return_value = ({"relevant.py": "relevant content"}, False, "p")

            llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

            # Verify _determine_source_files was called
            mock_determine.assert_called_once()

            # Verify the plugin was called with RAG-selected files
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

        api_response = {"chat_id": 111}
        mock_cli_invoke.return_value = api_response

        response = llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        self.assertEqual(response.summary, "No response from assistant.")
        self.assertEqual(response.updated_files, [])
        self.assertEqual(response.chat_id, 111)

    @patch('aye.controller.llm_invoker.rprint')
    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    def test_invoke_llm_verbose_mode_small_project(self, mock_spinner, mock_collect, mock_rprint):
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager, verbose=True)

        size_kb = len(self.source_files['main.py'].encode('utf-8')) / 1024
        mock_rprint.assert_any_call(f"[cyan]Project size ({size_kb:.1f}KB) is small; including all files.[/]")
        mock_rprint.assert_any_call(f"[yellow]Included with prompt: {', '.join(self.source_files.keys())}[/]")

    @patch('builtins.print')
    @patch('aye.controller.llm_invoker.collect_sources')
    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    @patch('aye.controller.llm_invoker.get_user_config', return_value='on')
    def test_invoke_llm_debug_mode(self, mock_get_cfg, mock_cli_invoke, mock_spinner, mock_collect, mock_print):
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
            model=self.conf.selected_model,
            system_prompt=SYSTEM_PROMPT,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            telemetry=None
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
        self.plugin_manager.handle_command.return_value = None
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
        self.plugin_manager.handle_command.return_value = None
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

        large_content = "a" * (CONTEXT_HARD_LIMIT + 1)

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
    @patch('aye.controller.llm_invoker.get_user_config', return_value='on')
    def test_parse_api_response_debug_mode(self, mock_get_cfg, mock_print):
        llm_invoker._parse_api_response({"assistant_response": "not json"})
        debug_prints = [c[0][0] for c in mock_print.call_args_list]
        self.assertIn("Checking for truncation", debug_prints[0])

        mock_print.reset_mock()

        llm_invoker._parse_api_response({"assistant_response": "{}"})
        debug_prints = [c[0][0] for c in mock_print.call_args_list]
        self.assertIn("[DEBUG] Successfully parsed assistant_response JSON", debug_prints)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_passes_chat_id_to_api(self, mock_collect, mock_cli, mock_spinner):
        """Test that chat_id is correctly passed to cli_invoke."""
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None
        mock_cli.return_value = {"assistant_response": "{}", "chat_id": 999}

        llm_invoker.invoke_llm(
            "prompt", self.conf, self.console, self.plugin_manager,
            chat_id=555
        )

        mock_cli.assert_called_once()
        self.assertEqual(mock_cli.call_args[1]['chat_id'], 555)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_spinner_messages(self, mock_collect, mock_spinner):
        """Test that spinner is called with progressive messages."""
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"summary": "s", "updated_files": []}

        llm_invoker.invoke_llm("prompt", self.conf, self.console, self.plugin_manager)

        mock_spinner.assert_called_once()
        call_kwargs = mock_spinner.call_args[1]
        self.assertIn('messages', call_kwargs)
        self.assertEqual(len(call_kwargs['messages']), 5)
        self.assertIn('interval', call_kwargs)
        self.assertEqual(call_kwargs['interval'], 15.0)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_local_model_empty_summary(self, mock_collect, mock_spinner):
        """Test local model response with missing summary key."""
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"updated_files": []}

        response = llm_invoker.invoke_llm(
            "prompt", self.conf, self.console, self.plugin_manager
        )

        self.assertEqual(response.summary, "")
        self.assertEqual(response.source, LLMSource.LOCAL)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_local_model_missing_updated_files(self, mock_collect, mock_spinner):
        """Test local model response with missing updated_files key."""
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = {"summary": "test"}

        response = llm_invoker.invoke_llm(
            "prompt", self.conf, self.console, self.plugin_manager
        )

        self.assertEqual(response.updated_files, [])
        self.assertEqual(response.source, LLMSource.LOCAL)

    @patch('aye.controller.llm_invoker.thinking_spinner')
    @patch('aye.controller.llm_invoker.cli_invoke')
    @patch('aye.controller.llm_invoker.collect_sources')
    def test_invoke_llm_api_response_missing_answer_summary(self, mock_collect, mock_cli, mock_spinner):
        """Test API response with missing answer_summary in parsed JSON."""
        mock_collect.return_value = self.source_files
        self.plugin_manager.handle_command.return_value = None
        mock_cli.return_value = {
            "assistant_response": json.dumps({"source_files": [{"file_name": "f.py", "file_content": "c"}]}),
            "chat_id": 1
        }

        response = llm_invoker.invoke_llm("p", self.conf, self.console, self.plugin_manager)

        self.assertEqual(response.summary, "")
        self.assertEqual(len(response.updated_files), 1)
