import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open
from prompt_toolkit import PromptSession
from rich.console import Console

from aye.controller.command_handlers import (
    handle_cd_command,
    handle_model_command,
    handle_verbose_command,
    handle_debug_command,
    _expand_file_patterns,
    handle_with_command
)


class TestHandleCdCommand:
    """Tests for handle_cd_command function."""

    def test_cd_to_home_when_no_target_provided(self, tmp_path):
        """Test cd with no arguments changes to home directory."""
        conf = Mock()
        conf.root = tmp_path
        tokens = ["cd"]
        
        with patch('os.chdir') as mock_chdir:
            result = handle_cd_command(tokens, conf)
            
            assert result is True
            mock_chdir.assert_called_once_with(str(Path.home()))
            assert conf.root == Path.cwd()

    def test_cd_to_specific_directory(self, tmp_path):
        """Test cd to a specific directory."""
        conf = Mock()
        conf.root = tmp_path
        target_dir = tmp_path / "subdir"
        target_dir.mkdir()
        tokens = ["cd", str(target_dir)]
        
        result = handle_cd_command(tokens, conf)
        
        assert result is True
        assert conf.root == Path.cwd()

    def test_cd_with_spaces_in_path(self, tmp_path):
        """Test cd with directory name containing spaces."""
        conf = Mock()
        conf.root = tmp_path
        target_dir = tmp_path / "dir with spaces"
        target_dir.mkdir()
        tokens = ["cd", "dir", "with", "spaces"]
        
        with patch('os.chdir') as mock_chdir:
            result = handle_cd_command(tokens, conf)
            
            assert result is True
            mock_chdir.assert_called_once_with("dir with spaces")

    def test_cd_to_nonexistent_directory(self, tmp_path):
        """Test cd to a directory that doesn't exist."""
        conf = Mock()
        conf.root = tmp_path
        tokens = ["cd", "/nonexistent/path"]
        
        with patch('aye.controller.command_handlers.print_error') as mock_print_error:
            result = handle_cd_command(tokens, conf)
            
            assert result is False
            mock_print_error.assert_called_once()


class TestHandleModelCommand:
    """Tests for handle_model_command function."""

    @pytest.fixture
    def mock_models(self):
        return [
            {"id": "model-1", "name": "Model One", "type": "online"},
            {"id": "model-2", "name": "Model Two", "type": "offline", "size_gb": 5},
            {"id": "model-3", "name": "Model Three", "type": "online"}
        ]

    @pytest.fixture
    def mock_conf(self):
        conf = Mock()
        conf.selected_model = "model-1"
        conf.plugin_manager = Mock()
        conf.plugin_manager.handle_command = Mock(return_value={"success": True})
        return conf

    def test_select_model_by_number(self, mock_models, mock_conf):
        """Test selecting a model by number."""
        tokens = ["model", "2"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)
            
            assert mock_conf.selected_model == "model-2"
            mock_set_config.assert_called_once_with("selected_model", "model-2")

    def test_select_offline_model_triggers_download(self, mock_models, mock_conf):
        """Test selecting an offline model triggers download."""
        tokens = ["model", "2"]
        
        with patch('aye.controller.command_handlers.set_user_config'):
            handle_model_command(None, mock_models, mock_conf, tokens)
            
            mock_conf.plugin_manager.handle_command.assert_called_once_with(
                "download_offline_model",
                {
                    "model_id": "model-2",
                    "model_name": "Model Two",
                    "size_gb": 5
                }
            )

    def test_select_offline_model_download_fails(self, mock_models, mock_conf):
        """Test handling failed offline model download."""
        mock_conf.plugin_manager.handle_command = Mock(
            return_value={"success": False, "error": "Download failed"}
        )
        tokens = ["model", "2"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)
            
            # Model should not be selected if download fails
            mock_set_config.assert_not_called()

    def test_select_invalid_model_number(self, mock_models, mock_conf):
        """Test selecting an invalid model number."""
        tokens = ["model", "99"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)
            
            mock_set_config.assert_not_called()

    def test_select_model_with_invalid_input(self, mock_models, mock_conf):
        """Test selecting a model with non-numeric input."""
        tokens = ["model", "invalid"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)
            
            mock_set_config.assert_not_called()

    def test_list_models_without_session(self, mock_models, mock_conf):
        """Test listing models without a session."""
        tokens = ["model"]
        
        # Should not raise an exception
        handle_model_command(None, mock_models, mock_conf, tokens)

    def test_interactive_model_selection(self, mock_models, mock_conf):
        """Test interactive model selection with session."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="3")
        tokens = ["model"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)
            
            assert mock_conf.selected_model == "model-3"
            mock_set_config.assert_called_once_with("selected_model", "model-3")

    def test_interactive_model_selection_cancelled(self, mock_models, mock_conf):
        """Test interactive model selection when user presses Enter."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="")
        tokens = ["model"]
        original_model = mock_conf.selected_model
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)
            
            assert mock_conf.selected_model == original_model
            mock_set_config.assert_not_called()


class TestHandleVerboseCommand:
    """Tests for handle_verbose_command function."""

    def test_set_verbose_on(self):
        """Test setting verbose mode on."""
        tokens = ["verbose", "on"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)
            
            mock_set_config.assert_called_once_with("verbose", "on")

    def test_set_verbose_off(self):
        """Test setting verbose mode off."""
        tokens = ["verbose", "off"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)
            
            mock_set_config.assert_called_once_with("verbose", "off")

    def test_set_verbose_invalid_value(self):
        """Test setting verbose mode with invalid value."""
        tokens = ["verbose", "invalid"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)
            
            mock_set_config.assert_not_called()

    def test_get_verbose_status(self):
        """Test getting current verbose status."""
        tokens = ["verbose"]
        
        with patch('aye.controller.command_handlers.get_user_config', return_value="on"):
            handle_verbose_command(tokens)


class TestHandleDebugCommand:
    """Tests for handle_debug_command function."""

    def test_set_debug_on(self):
        """Test setting debug mode on."""
        tokens = ["debug", "on"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)
            
            mock_set_config.assert_called_once_with("debug", "on")

    def test_set_debug_off(self):
        """Test setting debug mode off."""
        tokens = ["debug", "off"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)
            
            mock_set_config.assert_called_once_with("debug", "off")

    def test_set_debug_invalid_value(self):
        """Test setting debug mode with invalid value."""
        tokens = ["debug", "invalid"]
        
        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)
            
            mock_set_config.assert_not_called()

    def test_get_debug_status(self):
        """Test getting current debug status."""
        tokens = ["debug"]
        
        with patch('aye.controller.command_handlers.get_user_config', return_value="off"):
            handle_debug_command(tokens)


class TestExpandFilePatterns:
    """Tests for _expand_file_patterns function."""

    def test_expand_single_file(self, tmp_path):
        """Test expanding a single file pattern."""
        conf = Mock()
        conf.root = tmp_path
        test_file = tmp_path / "test.py"
        test_file.write_text("content")
        
        result = _expand_file_patterns(["test.py"], conf)
        
        assert result == ["test.py"]

    def test_expand_wildcard_pattern(self, tmp_path):
        """Test expanding wildcard patterns."""
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "file1.py").write_text("content")
        (tmp_path / "file2.py").write_text("content")
        (tmp_path / "file.txt").write_text("content")
        
        result = _expand_file_patterns(["*.py"], conf)
        
        assert len(result) == 2
        assert "file1.py" in result
        assert "file2.py" in result

    def test_expand_nested_wildcard(self, tmp_path):
        """Test expanding nested wildcard patterns."""
        conf = Mock()
        conf.root = tmp_path
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("content")
        
        result = _expand_file_patterns(["src/*.py"], conf)
        
        assert len(result) == 1
        assert "src/main.py" in result or "src\\main.py" in result

    def test_expand_multiple_patterns(self, tmp_path):
        """Test expanding multiple patterns."""
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "file.py").write_text("content")
        (tmp_path / "file.txt").write_text("content")
        
        result = _expand_file_patterns(["*.py", "*.txt"], conf)
        
        assert len(result) == 2

    def test_expand_empty_pattern(self, tmp_path):
        """Test expanding empty pattern."""
        conf = Mock()
        conf.root = tmp_path
        
        result = _expand_file_patterns([""], conf)
        
        assert result == []

    def test_expand_nonexistent_pattern(self, tmp_path):
        """Test expanding pattern with no matches."""
        conf = Mock()
        conf.root = tmp_path
        
        result = _expand_file_patterns(["*.nonexistent"], conf)
        
        assert result == []

    def test_expand_directory_not_included(self, tmp_path):
        """Test that directories are not included in expansion."""
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "dir").mkdir()
        (tmp_path / "file.py").write_text("content")
        
        result = _expand_file_patterns(["*"], conf)
        
        assert "file.py" in result
        assert "dir" not in result


class TestHandleWithCommand:
    """Tests for handle_with_command function."""

    @pytest.fixture
    def mock_conf(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        conf.plugin_manager = Mock()
        return conf

    @pytest.fixture
    def mock_console(self):
        return Mock(spec=Console)

    def test_with_single_file(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with a single file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        prompt = "with test.py: explain this code"
        chat_id = 1
        chat_id_file = tmp_path / "chat_id"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=2)
            mock_process.return_value = 2
            
            result = handle_with_command(prompt, mock_conf, mock_console, chat_id, chat_id_file)
            
            assert result == 2
            mock_invoke.assert_called_once()
            call_kwargs = mock_invoke.call_args[1]
            assert "test.py" in call_kwargs["explicit_source_files"]

    def test_with_multiple_files(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with multiple files."""
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")
        
        prompt = "with file1.py, file2.py: analyze these files"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            assert "file1.py" in call_kwargs["explicit_source_files"]
            assert "file2.py" in call_kwargs["explicit_source_files"]

    def test_with_wildcard_pattern(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with wildcard pattern."""
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")
        (tmp_path / "file.txt").write_text("content")
        
        prompt = "with *.py: analyze python files"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            assert len(call_kwargs["explicit_source_files"]) == 2

    def test_with_empty_file_list(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with empty file list."""
        prompt = "with : some prompt"
        
        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
        
        assert result is None

    def test_with_empty_prompt(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with empty prompt after colon."""
        prompt = "with test.py:"
        
        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
        
        assert result is None

    def test_with_nonexistent_file(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with nonexistent file."""
        prompt = "with nonexistent.py: explain this"
        
        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
        
        assert result is None

    def test_with_unreadable_file(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with unreadable file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("content")
        
        prompt = "with test.py: explain this"
        
        with patch('pathlib.Path.read_text', side_effect=PermissionError("Access denied")):
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            assert result is None

    def test_with_partial_file_failure(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command when some files fail to read."""
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")
        
        prompt = "with file1.py, file2.py: analyze"
        
        def mock_read_text(encoding=None):
            if "file1" in str(mock_read_text.call_count):
                raise IOError("Cannot read")
            return "content2"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            # Should continue with readable files
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

    def test_with_verbose_mode(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command in verbose mode shows file list."""
        mock_conf.verbose = True
        (tmp_path / "test.py").write_text("content")
        
        prompt = "with test.py: explain"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

    def test_with_exception_handling(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command handles unexpected exceptions."""
        prompt = "with test.py: explain"
        
        with patch('aye.controller.command_handlers._expand_file_patterns', side_effect=Exception("Unexpected error")), \
             patch('aye.controller.command_handlers.handle_llm_error') as mock_error:
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            assert result is None
            mock_error.assert_called_once()

    def test_with_no_llm_response(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command when LLM returns no response."""
        (tmp_path / "test.py").write_text("content")
        prompt = "with test.py: explain"
        
        with patch('aye.controller.command_handlers.invoke_llm', return_value=None):
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            assert result is None
