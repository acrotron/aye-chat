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
    _should_skip_path_part,
    _get_explicitly_allowed_parts,
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


class TestShouldSkipPathPart:
    """Tests for _should_skip_path_part function."""

    def test_normal_path_not_skipped(self):
        """Test that normal paths are not skipped."""
        assert _should_skip_path_part("src", set()) is False
        assert _should_skip_path_part("main.py", set()) is False
        assert _should_skip_path_part("workflows", set()) is False

    def test_hidden_path_skipped(self):
        """Test that hidden paths are skipped."""
        assert _should_skip_path_part(".git", set()) is True
        assert _should_skip_path_part(".github", set()) is True
        assert _should_skip_path_part(".venv", set()) is True
        assert _should_skip_path_part(".hidden", set()) is True

    def test_explicitly_allowed_not_skipped(self):
        """Test that explicitly allowed hidden paths are not skipped."""
        assert _should_skip_path_part(".github", {".github"}) is False
        assert _should_skip_path_part(".venv", {".venv"}) is False
        assert _should_skip_path_part(".vscode", {".vscode", ".github"}) is False

    def test_different_explicitly_allowed_still_skipped(self):
        """Test that other hidden paths are still skipped when one is allowed."""
        assert _should_skip_path_part(".git", {".github"}) is True
        assert _should_skip_path_part(".venv", {".github"}) is True


class TestGetExplicitlyAllowedParts:
    """Tests for _get_explicitly_allowed_parts function."""

    def test_simple_hidden_dir(self):
        """Test simple hidden directory patterns."""
        assert _get_explicitly_allowed_parts(".github") == {".github"}
        assert _get_explicitly_allowed_parts(".vscode") == {".vscode"}

    def test_hidden_dir_with_trailing_slash(self):
        """Test hidden directory with trailing slash."""
        assert _get_explicitly_allowed_parts(".github/") == {".github"}

    def test_hidden_dir_with_subpath(self):
        """Test hidden directory with subpath."""
        assert _get_explicitly_allowed_parts(".github/workflows") == {".github"}
        assert _get_explicitly_allowed_parts(".github/workflows/ci.yml") == {".github"}

    def test_multiple_hidden_parts(self):
        """Test pattern with multiple hidden parts."""
        result = _get_explicitly_allowed_parts(".vscode/.settings")
        assert result == {".vscode", ".settings"}

    def test_no_hidden_parts(self):
        """Test pattern with no hidden parts."""
        assert _get_explicitly_allowed_parts("src") == set()
        assert _get_explicitly_allowed_parts("src/main.py") == set()

    def test_double_wildcard_pattern(self):
        """Test double wildcard patterns."""
        assert _get_explicitly_allowed_parts(".github/**") == {".github"}
        assert _get_explicitly_allowed_parts(".github/**/*.yml") == {".github"}

    def test_windows_path_separator(self):
        """Test Windows path separators are handled."""
        assert _get_explicitly_allowed_parts(".github\\workflows") == {".github"}


class TestExpandFilePatterns:
    """Tests for _expand_file_patterns function."""

    def test_expand_single_file(self, tmp_path):
        """Test expanding a single file pattern."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        test_file = tmp_path / "test.py"
        test_file.write_text("content")
        
        result = _expand_file_patterns(["test.py"], conf)
        
        assert result == ["test.py"]

    def test_expand_wildcard_pattern(self, tmp_path):
        """Test expanding wildcard patterns."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
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
        conf.verbose = False
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("content")
        
        result = _expand_file_patterns(["src/*.py"], conf)
        
        assert len(result) == 1
        assert "src/main.py" in result

    def test_expand_multiple_patterns(self, tmp_path):
        """Test expanding multiple patterns."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        (tmp_path / "file.py").write_text("content")
        (tmp_path / "file.txt").write_text("content")
        
        result = _expand_file_patterns(["*.py", "*.txt"], conf)
        
        assert len(result) == 2

    def test_expand_empty_pattern(self, tmp_path):
        """Test expanding empty pattern."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        result = _expand_file_patterns([""], conf)
        
        assert result == []

    def test_expand_nonexistent_pattern(self, tmp_path):
        """Test expanding pattern with no matches."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        result = _expand_file_patterns(["*.nonexistent"], conf)
        
        assert result == []

    def test_expand_directory_not_included(self, tmp_path):
        """Test that directories are not included in expansion."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        (tmp_path / "dir").mkdir()
        (tmp_path / "file.py").write_text("content")
        
        result = _expand_file_patterns(["*"], conf)
        
        assert "file.py" in result
        assert "dir" not in result

    def test_expand_directory_includes_all_files_recursively(self, tmp_path):
        """Test that specifying a directory includes all files recursively."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / "utils.py").write_text("content")
        
        sub_dir = src_dir / "submodule"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("content")
        
        result = _expand_file_patterns(["src"], conf)
        
        assert len(result) == 3
        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "src/submodule/helper.py" in result

    def test_expand_directory_with_trailing_slash(self, tmp_path):
        """Test that directory with trailing slash works."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        
        result = _expand_file_patterns(["src/"], conf)
        
        assert len(result) == 1
        assert "src/main.py" in result

    def test_expand_directory_respects_gitignore(self, tmp_path):
        """Test that directory expansion respects .gitignore patterns."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create .gitignore
        (tmp_path / ".gitignore").write_text("*.log\nbuild/\n")
        
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / "debug.log").write_text("log content")  # Should be ignored
        
        build_dir = src_dir / "build"
        build_dir.mkdir()
        (build_dir / "output.py").write_text("content")  # Should be ignored
        
        result = _expand_file_patterns(["src"], conf)
        
        assert "src/main.py" in result
        assert "src/debug.log" not in result
        assert "src/build/output.py" not in result

    def test_expand_directory_skips_hidden_files(self, tmp_path):
        """Test that directory expansion skips hidden files and directories."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / ".hidden.py").write_text("hidden content")  # Should be skipped
        
        hidden_dir = src_dir / ".cache"
        hidden_dir.mkdir()
        (hidden_dir / "temp.py").write_text("content")  # Should be skipped
        
        result = _expand_file_patterns(["src"], conf)
        
        assert "src/main.py" in result
        assert "src/.hidden.py" not in result
        assert "src/.cache/temp.py" not in result

    def test_expand_double_wildcard(self, tmp_path):
        """Test that ** wildcard works for recursive matching."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        
        sub_dir = src_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("content")
        
        result = _expand_file_patterns(["src/**/*.py"], conf)
        
        assert len(result) == 2
        assert "src/main.py" in result
        assert "src/sub/helper.py" in result

    def test_expand_double_wildcard_all_files(self, tmp_path):
        """Test that src/** includes all files in src recursively."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / "config.json").write_text("{}")
        
        sub_dir = src_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("content")
        
        result = _expand_file_patterns(["src/**"], conf)
        
        assert len(result) == 3
        assert "src/main.py" in result
        assert "src/config.json" in result
        assert "src/sub/helper.py" in result

    def test_expand_root_double_wildcard(self, tmp_path):
        """Test that **/*.py finds all Python files recursively from root."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create files at various levels
        (tmp_path / "root.py").write_text("content")
        
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / "data.txt").write_text("content")  # Not .py
        
        sub_dir = src_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("content")
        
        result = _expand_file_patterns(["**/*.py"], conf)
        
        # Should find all .py files
        assert "root.py" in result
        assert "src/main.py" in result
        assert "src/sub/helper.py" in result
        assert "src/data.txt" not in result

    def test_expand_double_wildcard_respects_gitignore(self, tmp_path):
        """Test that ** expansion respects .gitignore patterns."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create .gitignore
        (tmp_path / ".gitignore").write_text("*.log\nnode_modules/\n")
        
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("content")
        (src_dir / "debug.log").write_text("log")  # Should be ignored
        
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.js").write_text("content")  # Should be ignored
        
        result = _expand_file_patterns(["**/*"], conf)
        
        assert "src/main.py" in result
        assert "src/debug.log" not in result
        assert "node_modules/package.js" not in result

    def test_expand_double_wildcard_skips_hidden(self, tmp_path):
        """Test that ** expansion skips hidden files and directories."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        (tmp_path / "visible.py").write_text("content")
        (tmp_path / ".hidden.py").write_text("content")  # Should be skipped
        
        hidden_dir = tmp_path / ".git"
        hidden_dir.mkdir()
        (hidden_dir / "config").write_text("content")  # Should be skipped
        
        result = _expand_file_patterns(["**/*"], conf)
        
        assert "visible.py" in result
        assert ".hidden.py" not in result
        assert ".git/config" not in result

    def test_expand_hidden_directory_when_explicit(self, tmp_path):
        """Test that explicitly specifying .github includes its contents."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        # Create .github directory structure
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text("name: CI")
        (workflows_dir / "deploy.yml").write_text("name: Deploy")
        
        (github_dir / "CODEOWNERS").write_text("* @owner")
        (github_dir / "dependabot.yml").write_text("version: 2")
        
        result = _expand_file_patterns([".github"], conf)
        
        assert len(result) == 4
        assert ".github/workflows/ci.yml" in result
        assert ".github/workflows/deploy.yml" in result
        assert ".github/CODEOWNERS" in result
        assert ".github/dependabot.yml" in result

    def test_expand_hidden_directory_with_trailing_slash(self, tmp_path):
        """Test that .github/ with trailing slash works."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text("name: CI")
        (github_dir / "CODEOWNERS").write_text("* @owner")
        
        result = _expand_file_patterns([".github/"], conf)
        
        assert len(result) == 2
        assert ".github/workflows/ci.yml" in result
        assert ".github/CODEOWNERS" in result

    def test_expand_hidden_directory_double_wildcard(self, tmp_path):
        """Test that .github/** works."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text("name: CI")
        (github_dir / "CODEOWNERS").write_text("* @owner")
        
        result = _expand_file_patterns([".github/**"], conf)
        
        assert len(result) == 2
        assert ".github/workflows/ci.yml" in result
        assert ".github/CODEOWNERS" in result

    def test_expand_hidden_directory_skips_nested_hidden(self, tmp_path):
        """Test that .github expansion still skips nested hidden directories."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "CODEOWNERS").write_text("* @owner")
        
        # Create nested hidden dir inside .github
        nested_hidden = github_dir / ".secrets"
        nested_hidden.mkdir()
        (nested_hidden / "token.txt").write_text("secret")  # Should still be skipped
        
        result = _expand_file_patterns([".github"], conf)
        
        assert ".github/CODEOWNERS" in result
        assert ".github/.secrets/token.txt" not in result

    def test_expand_hidden_directory_vscode(self, tmp_path):
        """Test that explicitly specifying .vscode works."""
        conf = Mock()
        conf.root = tmp_path
        conf.verbose = False
        
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text("{}")
        (vscode_dir / "launch.json").write_text("{}")
        
        result = _expand_file_patterns([".vscode"], conf)
        
        assert len(result) == 2
        assert ".vscode/settings.json" in result
        assert ".vscode/launch.json" in result


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

    def test_with_directory(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with a directory includes all files recursively."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("main content")
        (src_dir / "utils.py").write_text("utils content")
        
        sub_dir = src_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("helper content")
        
        prompt = "with src: analyze this module"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            files = call_kwargs["explicit_source_files"]
            assert len(files) == 3
            assert "src/main.py" in files
            assert "src/utils.py" in files
            assert "src/sub/helper.py" in files

    def test_with_double_wildcard(self, mock_conf, mock_console, tmp_path):
        """Test 'with' command with ** wildcard."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("main content")
        (src_dir / "data.txt").write_text("data content")
        
        sub_dir = src_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "helper.py").write_text("helper content")
        
        prompt = "with src/**/*.py: analyze python files"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            files = call_kwargs["explicit_source_files"]
            assert len(files) == 2
            assert "src/main.py" in files
            assert "src/sub/helper.py" in files
            assert "src/data.txt" not in files

    def test_with_double_wildcard_all(self, mock_conf, mock_console, tmp_path):
        """Test 'with src/**' includes all files in src."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("main content")
        (src_dir / "config.json").write_text("{}")
        
        prompt = "with src/**: analyze everything"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            files = call_kwargs["explicit_source_files"]
            assert len(files) == 2
            assert "src/main.py" in files
            assert "src/config.json" in files

    def test_with_hidden_directory_github(self, mock_conf, mock_console, tmp_path):
        """Test 'with .github' includes all files in .github."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text("name: CI")
        (workflows_dir / "deploy.yml").write_text("name: Deploy")
        
        (github_dir / "CODEOWNERS").write_text("* @owner")
        
        prompt = "with .github: explain the CI setup"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            files = call_kwargs["explicit_source_files"]
            assert len(files) == 3
            assert ".github/workflows/ci.yml" in files
            assert ".github/workflows/deploy.yml" in files
            assert ".github/CODEOWNERS" in files

    def test_with_hidden_directory_with_slash(self, mock_conf, mock_console, tmp_path):
        """Test 'with .github/' with trailing slash."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "CODEOWNERS").write_text("* @owner")
        
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text("name: CI")
        
        prompt = "with .github/: explain"
        
        with patch('aye.controller.command_handlers.invoke_llm') as mock_invoke, \
             patch('aye.controller.command_handlers.process_llm_response'):
            mock_invoke.return_value = Mock(chat_id=2)
            
            result = handle_with_command(prompt, mock_conf, mock_console, 1, tmp_path / "chat_id")
            
            call_kwargs = mock_invoke.call_args[1]
            files = call_kwargs["explicit_source_files"]
            assert ".github/CODEOWNERS" in files
            assert ".github/workflows/ci.yml" in files

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
