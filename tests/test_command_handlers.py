import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
from prompt_toolkit import PromptSession
from rich.console import Console

from aye.controller.command_handlers import (
    handle_cd_command,
    handle_model_command,
    handle_verbose_command,
    handle_debug_command,
    handle_sslverify_command,
    handle_autodiff_command,
    handle_completion_command,
    handle_llm_command,
    handle_printraw_command,
    handle_blog_command,
    _expand_file_patterns,
    handle_with_command,
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
            {"id": "model-3", "name": "Model Three", "type": "online"},
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

    def test_select_online_model_no_download(self, mock_models, mock_conf):
        """Test selecting an online model does NOT trigger download."""
        tokens = ["model", "1"]

        with patch('aye.controller.command_handlers.set_user_config'):
            handle_model_command(None, mock_models, mock_conf, tokens)

            mock_conf.plugin_manager.handle_command.assert_not_called()
            assert mock_conf.selected_model == "model-1"

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
                    "size_gb": 5,
                },
            )

    def test_select_offline_model_download_fails(self, mock_models, mock_conf):
        """Test handling failed offline model download."""
        mock_conf.plugin_manager.handle_command = Mock(
            return_value={"success": False, "error": "Download failed"}
        )
        tokens = ["model", "2"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)

            mock_set_config.assert_not_called()

    def test_select_invalid_model_number(self, mock_models, mock_conf):
        """Test selecting an invalid model number."""
        tokens = ["model", "99"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(None, mock_models, mock_conf, tokens)

            mock_set_config.assert_not_called()

    def test_select_model_number_zero(self, mock_models, mock_conf):
        """Test selecting model number 0 (out of valid range)."""
        tokens = ["model", "0"]

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

    def test_interactive_select_offline_model_download_fails(self, mock_models, mock_conf):
        """Test interactive offline model selection when download fails."""
        mock_conf.plugin_manager.handle_command = Mock(
            return_value={"success": False, "error": "Disk full"}
        )
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="2")
        tokens = ["model"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)

            mock_set_config.assert_not_called()

    def test_interactive_select_offline_model_download_succeeds(self, mock_models, mock_conf):
        """Test interactive offline model selection when download succeeds."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="2")
        tokens = ["model"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)

            assert mock_conf.selected_model == "model-2"
            mock_set_config.assert_called_once_with("selected_model", "model-2")

    def test_interactive_invalid_number(self, mock_models, mock_conf):
        """Test interactive model selection with out-of-range number."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="99")
        tokens = ["model"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)

            mock_set_config.assert_not_called()

    def test_interactive_invalid_input(self, mock_models, mock_conf):
        """Test interactive model selection with non-numeric input."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(return_value="abc")
        tokens = ["model"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_model_command(mock_session, mock_models, mock_conf, tokens)

            mock_set_config.assert_not_called()

    def test_list_models_shows_offline_size(self, mock_models, mock_conf):
        """Test that listing models shows offline model size."""
        tokens = ["model"]

        with patch('aye.controller.command_handlers.rprint') as mock_rprint:
            handle_model_command(None, mock_models, mock_conf, tokens)

            # Verify offline model size is displayed
            printed_strings = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed_strings)
            assert "5GB download" in combined

    def test_current_model_unknown(self, mock_models, mock_conf):
        """Test listing models when current model is not in the list."""
        mock_conf.selected_model = "nonexistent-model"
        tokens = ["model"]

        with patch('aye.controller.command_handlers.rprint') as mock_rprint:
            handle_model_command(None, mock_models, mock_conf, tokens)

            printed_strings = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed_strings)
            assert "Unknown" in combined


class TestHandleVerboseCommand:
    """Tests for handle_verbose_command function."""

    def test_set_verbose_on(self):
        tokens = ["verbose", "on"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)

            mock_set_config.assert_called_once_with("verbose", "on")

    def test_set_verbose_off(self):
        tokens = ["verbose", "off"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)

            mock_set_config.assert_called_once_with("verbose", "off")

    def test_set_verbose_invalid_value(self):
        tokens = ["verbose", "invalid"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_verbose_command(tokens)

            mock_set_config.assert_not_called()

    def test_get_verbose_status(self):
        tokens = ["verbose"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="on"):
            handle_verbose_command(tokens)


class TestHandleDebugCommand:
    """Tests for handle_debug_command function."""

    def test_set_debug_on(self):
        tokens = ["debug", "on"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)

            mock_set_config.assert_called_once_with("debug", "on")

    def test_set_debug_off(self):
        tokens = ["debug", "off"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)

            mock_set_config.assert_called_once_with("debug", "off")

    def test_set_debug_invalid_value(self):
        tokens = ["debug", "invalid"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_debug_command(tokens)

            mock_set_config.assert_not_called()

    def test_get_debug_status(self):
        tokens = ["debug"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="off"):
            handle_debug_command(tokens)


class TestHandleSslverifyCommand:
    """Tests for handle_sslverify_command function."""

    def test_set_sslverify_on(self):
        tokens = ["sslverify", "on"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_sslverify_command(tokens)

            mock_set_config.assert_called_once_with("sslverify", "on")

    def test_set_sslverify_off(self):
        tokens = ["sslverify", "off"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_sslverify_command(tokens)

            mock_set_config.assert_called_once_with("sslverify", "off")

    def test_set_sslverify_invalid_value(self):
        tokens = ["sslverify", "invalid"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_sslverify_command(tokens)

            mock_set_config.assert_not_called()

    def test_get_sslverify_status(self):
        tokens = ["sslverify"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="on") as mock_get:
            handle_sslverify_command(tokens)

            mock_get.assert_called_once_with("sslverify", "on")


class TestHandleAutodiffCommand:
    """Tests for handle_autodiff_command function."""

    def test_set_autodiff_on(self):
        tokens = ["autodiff", "on"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_autodiff_command(tokens)

            mock_set_config.assert_called_once_with("autodiff", "on")

    def test_set_autodiff_off(self):
        tokens = ["autodiff", "off"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_autodiff_command(tokens)

            mock_set_config.assert_called_once_with("autodiff", "off")

    def test_set_autodiff_invalid_value(self):
        tokens = ["autodiff", "invalid"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            handle_autodiff_command(tokens)

            mock_set_config.assert_not_called()

    def test_get_autodiff_status(self):
        tokens = ["autodiff"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="off") as mock_get, \
             patch('aye.controller.command_handlers.rprint'):
            handle_autodiff_command(tokens)

            mock_get.assert_called_once_with("autodiff", "off")


class TestHandleCompletionCommand:
    """Tests for handle_completion_command function."""

    def test_set_completion_readline(self):
        tokens = ["completion", "readline"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            result = handle_completion_command(tokens)

            mock_set_config.assert_called_once_with("completion_style", "readline")
            assert result == "readline"

    def test_set_completion_multi(self):
        tokens = ["completion", "multi"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            result = handle_completion_command(tokens)

            mock_set_config.assert_called_once_with("completion_style", "multi")
            assert result == "multi"

    def test_set_completion_invalid_value(self):
        tokens = ["completion", "invalid"]

        with patch('aye.controller.command_handlers.set_user_config') as mock_set_config:
            result = handle_completion_command(tokens)

            mock_set_config.assert_not_called()
            assert result is None

    def test_get_completion_status(self):
        tokens = ["completion"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="readline") as mock_get:
            result = handle_completion_command(tokens)

            mock_get.assert_called_once_with("completion_style", "readline")
            assert result is None


class TestHandleLlmCommand:
    """Tests for handle_llm_command function."""

    def test_llm_clear(self):
        """Test 'llm clear' removes all LLM config values."""
        tokens = ["llm", "clear"]

        with patch('aye.controller.command_handlers.delete_user_config') as mock_delete:
            handle_llm_command(None, tokens)

            assert mock_delete.call_count == 3
            mock_delete.assert_any_call("llm_api_url")
            mock_delete.assert_any_call("llm_api_key")
            mock_delete.assert_any_call("llm_model")

    def test_llm_no_session(self):
        """Test 'llm' without session prints error."""
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.rprint') as mock_rprint:
            handle_llm_command(None, tokens)

            # Should print error about no interactive session
            printed = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed)
            assert "Interactive session not available" in combined

    def test_llm_interactive_all_new_values(self):
        """Test interactive config sets all new values."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=[
            "http://localhost:1234/v1",
            "my-secret-key",
            "llama3",
        ])
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config') as mock_set, \
             patch('aye.controller.command_handlers.delete_user_config'):
            handle_llm_command(mock_session, tokens)

            mock_set.assert_any_call("llm_api_url", "http://localhost:1234/v1")
            mock_set.assert_any_call("llm_api_key", "my-secret-key")
            mock_set.assert_any_call("llm_model", "llama3")

    def test_llm_interactive_keep_existing_values(self):
        """Test interactive config keeps existing values when Enter pressed."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=["", "", ""])
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value="existing"), \
             patch('aye.controller.command_handlers.set_user_config') as mock_set, \
             patch('aye.controller.command_handlers.delete_user_config') as mock_delete:
            handle_llm_command(mock_session, tokens)

            # Should save existing values (since final_* = existing)
            mock_set.assert_any_call("llm_api_url", "existing")
            mock_set.assert_any_call("llm_api_key", "existing")
            mock_set.assert_any_call("llm_model", "existing")

    def test_llm_interactive_cancel_eof(self):
        """Test interactive config cancelled with EOFError."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=EOFError)
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config') as mock_set, \
             patch('aye.controller.command_handlers.rprint'):
            handle_llm_command(mock_session, tokens)

            mock_set.assert_not_called()

    def test_llm_interactive_cancel_keyboard_interrupt(self):
        """Test interactive config cancelled with KeyboardInterrupt."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=KeyboardInterrupt)
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config') as mock_set, \
             patch('aye.controller.command_handlers.rprint'):
            handle_llm_command(mock_session, tokens)

            mock_set.assert_not_called()

    def test_llm_interactive_empty_values_no_existing(self):
        """Test interactive config with empty inputs and no existing values triggers delete."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=["", "", ""])
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config') as mock_set, \
             patch('aye.controller.command_handlers.delete_user_config') as mock_delete:
            handle_llm_command(mock_session, tokens)

            mock_delete.assert_any_call("llm_api_url")
            mock_delete.assert_any_call("llm_api_key")
            mock_delete.assert_any_call("llm_model")

    def test_llm_interactive_partial_config_warning(self):
        """Test that partial config (URL but no key) shows warning."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=[
            "http://localhost:1234/v1",
            "",  # no key
            "llama3",
        ])
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config'), \
             patch('aye.controller.command_handlers.delete_user_config'), \
             patch('aye.controller.command_handlers.rprint') as mock_rprint:
            handle_llm_command(mock_session, tokens)

            printed = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed)
            assert "Both URL and KEY are required" in combined

    def test_llm_interactive_full_config_success(self):
        """Test that full config (URL + key) shows success message."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=[
            "http://localhost:1234/v1",
            "secret",
            "llama3",
        ])
        tokens = ["llm"]

        with patch('aye.controller.command_handlers.get_user_config', return_value=""), \
             patch('aye.controller.command_handlers.set_user_config'), \
             patch('aye.controller.command_handlers.delete_user_config'), \
             patch('aye.controller.command_handlers.rprint') as mock_rprint:
            handle_llm_command(mock_session, tokens)

            printed = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed)
            assert "configured and active" in combined

    def test_llm_interactive_shows_current_values(self):
        """Test that existing values are shown in prompts."""
        mock_session = Mock(spec=PromptSession)
        mock_session.prompt = Mock(side_effect=["", "", ""])
        tokens = ["llm"]

        def get_config_side_effect(key, default=""):
            mapping = {
                "llm_api_url": "EXISTS_HOST",
                "llm_api_key": "secret123",
                "llm_model": "gpt-4",
            }
            return mapping.get(key, default)

        with patch('aye.controller.command_handlers.get_user_config', side_effect=get_config_side_effect), \
             patch('aye.controller.command_handlers.set_user_config'), \
             patch('aye.controller.command_handlers.delete_user_config'):
            handle_llm_command(mock_session, tokens)

            # Session.prompt should have been called with current values in display
            prompt_args = [c[0][0] for c in mock_session.prompt.call_args_list]
            assert any("EXISTS_HOST" in p for p in prompt_args)
            assert any("set" in p for p in prompt_args)  # key is shown as "set"
            assert any("gpt-4" in p for p in prompt_args)


class TestHandlePrintrawCommand:
    """Tests for handle_printraw_command function."""

    def test_printraw_calls_raw_output(self):
        """Test printraw retrieves last response and prints it raw."""
        with patch('aye.presenter.repl_ui.get_last_assistant_response', return_value="Hello world") as mock_get, \
             patch('aye.presenter.raw_output.print_assistant_response_raw') as mock_print_raw:
            handle_printraw_command()

            mock_get.assert_called_once()
            mock_print_raw.assert_called_once_with("Hello world")

    def test_printraw_with_none_response(self):
        """Test printraw when no previous response exists."""
        with patch('aye.presenter.repl_ui.get_last_assistant_response', return_value=None) as mock_get, \
             patch('aye.presenter.raw_output.print_assistant_response_raw') as mock_print_raw:
            handle_printraw_command()

            mock_get.assert_called_once()
            mock_print_raw.assert_called_once_with(None)


class TestExpandFilePatterns:
    """Tests for _expand_file_patterns function."""

    def test_expand_single_file(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path
        test_file = tmp_path / "test.py"
        test_file.write_text("content")

        result = _expand_file_patterns(["test.py"], conf)

        assert result == ["test.py"]

    def test_expand_wildcard_pattern(self, tmp_path):
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
        conf = Mock()
        conf.root = tmp_path
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("content")

        result = _expand_file_patterns(["src/*.py"], conf)

        assert len(result) == 1
        assert "src/main.py" in result or "src\\main.py" in result

    def test_expand_multiple_patterns(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "file.py").write_text("content")
        (tmp_path / "file.txt").write_text("content")

        result = _expand_file_patterns(["*.py", "*.txt"], conf)

        assert len(result) == 2

    def test_expand_empty_pattern(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path

        result = _expand_file_patterns([""], conf)

        assert result == []

    def test_expand_nonexistent_pattern(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path

        result = _expand_file_patterns(["*.nonexistent"], conf)

        assert result == []

    def test_expand_directory_not_included(self, tmp_path):
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "dir").mkdir()
        (tmp_path / "file.py").write_text("content")

        result = _expand_file_patterns(["*"], conf)

        assert "file.py" in result
        assert "dir" not in result

    def test_expand_whitespace_only_pattern(self, tmp_path):
        """Test that whitespace-only patterns are skipped."""
        conf = Mock()
        conf.root = tmp_path

        result = _expand_file_patterns(["   "], conf)

        assert result == []

    def test_expand_mixed_existing_and_glob(self, tmp_path):
        """Test mixing direct file paths and glob patterns."""
        conf = Mock()
        conf.root = tmp_path
        (tmp_path / "direct.py").write_text("content")
        (tmp_path / "glob1.txt").write_text("content")
        (tmp_path / "glob2.txt").write_text("content")

        result = _expand_file_patterns(["direct.py", "*.txt"], conf)

        assert "direct.py" in result
        assert len(result) == 3


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
        prompt = "with : some prompt"

        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

        assert result is None

    def test_with_empty_prompt(self, mock_conf, mock_console, tmp_path):
        prompt = "with test.py:"

        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

        assert result is None

    def test_with_prompt_with_only_spaces_after_colon(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with only whitespace after colon."""
        prompt = "with test.py:   "

        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

        assert result is None

    def test_with_nonexistent_file(self, mock_conf, mock_console, tmp_path):
        prompt = "with nonexistent.py: explain this"

        result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

        assert result is None

    def test_with_unreadable_file(self, mock_conf, mock_console, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("content")

        prompt = "with test.py: explain this"

        with patch('pathlib.Path.read_text', side_effect=PermissionError("Access denied")):
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None

    def test_with_partial_file_failure(self, mock_conf, mock_console, tmp_path):
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")

        prompt = "with file1.py, file2.py: analyze"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)

            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

    def test_with_verbose_mode(self, mock_conf, mock_console, tmp_path):
        mock_conf.verbose = True
        (tmp_path / "test.py").write_text("content")

        prompt = "with test.py: explain"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)

            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

    def test_with_exception_handling(self, mock_conf, mock_console, tmp_path):
        prompt = "with test.py: explain"

        with patch('aye.controller.command_handlers._expand_file_patterns', side_effect=Exception("Unexpected error")), \
             patch('aye.controller.command_handlers.handle_llm_error') as mock_error:
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None
            mock_error.assert_called_once()

    def test_with_no_llm_response(self, mock_conf, mock_console, tmp_path):
        (tmp_path / "test.py").write_text("content")
        prompt = "with test.py: explain"

        with patch('aye.controller.command_handlers.invoke_llm', return_value=None):
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None

    def test_with_llm_response_no_chat_id(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command when LLM response has no chat_id."""
        (tmp_path / "test.py").write_text("content")
        prompt = "with test.py: explain"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=None)
            mock_process.return_value = None

            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            # chat_id_file should be None when response has no chat_id
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["chat_id_file"] is None

    def test_with_no_colon_in_prompt(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with no colon separator."""
        prompt = "with test.py explain this"

        with patch('aye.controller.command_handlers.handle_llm_error') as mock_error:
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            # This should trigger an exception because split(":", 1) returns only 1 element
            # causing the unpacking to fail
            assert result is None

    def test_with_file_not_found_during_read(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command when file is found by glob but missing on read."""
        (tmp_path / "test.py").write_text("content")
        prompt = "with test.py, missing.py: explain"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)

            # missing.py doesn't exist, so it should be skipped
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")

            call_kwargs = mock_invoke.call_args[1]
            assert "test.py" in call_kwargs["explicit_source_files"]
            assert "missing.py" not in call_kwargs["explicit_source_files"]


class TestHandleBlogCommand:
    """Tests for handle_blog_command function."""

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

    def test_blog_no_intent(self, mock_conf, mock_console, tmp_path):
        """Test blog command with no intent shows usage."""
        tokens = ["blog"]

        with patch('aye.controller.command_handlers.rprint') as mock_rprint:
            result = handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None
            printed = [str(c) for c in mock_rprint.call_args_list]
            combined = " ".join(printed)
            assert "Usage" in combined

    def test_blog_with_intent(self, mock_conf, mock_console, tmp_path):
        """Test blog command with intent invokes LLM."""
        tokens = ["blog", "write", "about", "refactoring"]

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=2)
            mock_process.return_value = 2

            result = handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result == 2
            mock_invoke.assert_called_once()
            call_kwargs = mock_invoke.call_args[1]
            assert "write about refactoring" in call_kwargs["prompt"]
            assert call_kwargs["explicit_source_files"] is None

    def test_blog_llm_returns_no_response(self, mock_conf, mock_console, tmp_path):
        """Test blog command when LLM returns None."""
        tokens = ["blog", "some", "intent"]

        with patch('aye.controller.command_handlers.invoke_llm', return_value=None), \
             patch('aye.controller.command_handlers.rprint'):
            result = handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None

    def test_blog_exception_handling(self, mock_conf, mock_console, tmp_path):
        """Test blog command handles exceptions."""
        tokens = ["blog", "some", "intent"]

        with patch('aye.controller.command_handlers.invoke_llm', side_effect=Exception("Network error")), \
             patch('aye.controller.command_handlers.handle_llm_error') as mock_error:
            result = handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            assert result is None
            mock_error.assert_called_once()

    def test_blog_response_with_chat_id(self, mock_conf, mock_console, tmp_path):
        """Test blog command passes chat_id_file when response has chat_id."""
        tokens = ["blog", "intent"]
        chat_id_file = tmp_path / "chat_id"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=5)
            mock_process.return_value = 5

            result = handle_blog_command(tokens, mock_conf, mock_console, 1, chat_id_file)

            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["chat_id_file"] == chat_id_file

    def test_blog_response_without_chat_id(self, mock_conf, mock_console, tmp_path):
        """Test blog command passes None for chat_id_file when response has no chat_id."""
        tokens = ["blog", "intent"]
        chat_id_file = tmp_path / "chat_id"

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=None)
            mock_process.return_value = None

            result = handle_blog_command(tokens, mock_conf, mock_console, 1, chat_id_file)

            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["chat_id_file"] is None

    def test_blog_prompt_contains_preamble(self, mock_conf, mock_console, tmp_path):
        """Test that the blog prompt includes the preamble instruction."""
        tokens = ["blog", "deep", "dive"]

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)

            handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            call_kwargs = mock_invoke.call_args[1]
            assert "technical blog post" in call_kwargs["prompt"]
            assert "blog.md" in call_kwargs["prompt"]
            assert "deep dive" in call_kwargs["prompt"]

    def test_blog_snapshot_prompt_is_concise(self, mock_conf, mock_console, tmp_path):
        """Test that the snapshot prompt stored is concise."""
        tokens = ["blog", "our", "work"]

        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response') as mock_process:
            mock_invoke.return_value = Mock(chat_id=2)
            mock_process.return_value = 2

            handle_blog_command(tokens, mock_conf, mock_console, 1, tmp_path / "chat_id")

            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["prompt"] == "blog our work"
