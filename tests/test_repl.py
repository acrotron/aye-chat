"""Tests for the refactored REPL module.

Covers CommandContext, CommandDispatcher, and helper functions.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from aye.controller.repl import (
    CommandContext,
    CommandDispatcher,
    BUILTIN_COMMANDS,
    _parse_input,
    _normalize_command,
    _get_completer,
    _handle_shell_command,
    _handle_llm_invocation,
    _setup_context,
    _print_db_status,
    create_key_bindings,
    print_startup_header,
)


class TestCommandContext(unittest.TestCase):
    """Tests for CommandContext dataclass."""

    def test_init_defaults(self):
        """Test default values are set correctly."""
        conf = MagicMock()
        session = MagicMock()
        console = MagicMock()
        
        ctx = CommandContext(
            conf=conf,
            session=session,
            console=console,
        )
        
        self.assertEqual(ctx.conf, conf)
        self.assertEqual(ctx.session, session)
        self.assertEqual(ctx.console, console)
        self.assertEqual(ctx.chat_id, -1)
        self.assertIsNone(ctx.chat_id_file)
        self.assertEqual(ctx.completion_style, "readline")

    def test_init_with_values(self):
        """Test initialization with custom values."""
        conf = MagicMock()
        session = MagicMock()
        console = MagicMock()
        chat_id_file = Path("/tmp/chat_id")
        
        ctx = CommandContext(
            conf=conf,
            session=session,
            console=console,
            chat_id=123,
            chat_id_file=chat_id_file,
            completion_style="multi",
        )
        
        self.assertEqual(ctx.chat_id, 123)
        self.assertEqual(ctx.chat_id_file, chat_id_file)
        self.assertEqual(ctx.completion_style, "multi")

    def test_update_chat_id_with_value(self):
        """Test update_chat_id updates when value provided."""
        ctx = CommandContext(
            conf=MagicMock(),
            session=MagicMock(),
            console=MagicMock(),
            chat_id=100,
        )
        
        ctx.update_chat_id(200)
        
        self.assertEqual(ctx.chat_id, 200)

    def test_update_chat_id_with_none(self):
        """Test update_chat_id does nothing when None provided."""
        ctx = CommandContext(
            conf=MagicMock(),
            session=MagicMock(),
            console=MagicMock(),
            chat_id=100,
        )
        
        ctx.update_chat_id(None)
        
        self.assertEqual(ctx.chat_id, 100)


class TestCommandDispatcher(unittest.TestCase):
    """Tests for CommandDispatcher class."""

    def setUp(self):
        """Set up test fixtures."""
        self.conf = MagicMock()
        self.conf.root = Path("/tmp/project")
        self.conf.plugin_manager = MagicMock()
        self.conf.verbose = False
        
        self.session = MagicMock()
        self.console = MagicMock()
        
        self.ctx = CommandContext(
            conf=self.conf,
            session=self.session,
            console=self.console,
            chat_id=1,
            chat_id_file=Path("/tmp/chat_id"),
        )
        
        self.dispatcher = CommandDispatcher(self.ctx)

    def test_dispatch_unknown_command_returns_none(self):
        """Unknown commands should return None."""
        result = self.dispatcher.dispatch("unknowncommand", ["unknowncommand"])
        self.assertIsNone(result)

    def test_dispatch_exit_returns_true(self):
        """Exit commands should return True."""
        with patch('aye.controller.repl.telemetry'):
            for cmd in ["exit", "quit", ":q"]:
                result = self.dispatcher.dispatch(cmd, [cmd])
                self.assertTrue(result, f"Expected True for {cmd}")

    @patch('aye.controller.repl.handle_model_command')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_model_command(self, mock_telemetry, mock_handle_model):
        """Model command should be dispatched correctly."""
        result = self.dispatcher.dispatch("model", ["model"])
        
        self.assertFalse(result)
        mock_handle_model.assert_called_once()
        mock_telemetry.record_command.assert_called_with("model", has_args=False, prefix="aye:")

    @patch('aye.controller.repl.telemetry')
    def test_dispatch_new_command(self, mock_telemetry):
        """New command should reset chat session."""
        self.ctx.chat_id_file = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None
        
        result = self.dispatcher.dispatch("new", ["new"])
        
        self.assertFalse(result)
        self.assertEqual(self.ctx.chat_id, -1)
        self.ctx.chat_id_file.unlink.assert_called_once_with(missing_ok=True)
        self.conf.plugin_manager.handle_command.assert_called_with(
            "new_chat", {"root": self.conf.root}
        )

    @patch('aye.controller.repl.print_help_message')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_help_command(self, mock_telemetry, mock_print_help):
        """Help command should print help message."""
        result = self.dispatcher.dispatch("help", ["help"])
        
        self.assertFalse(result)
        mock_print_help.assert_called_once()

    @patch('aye.controller.repl.handle_verbose_command')
    @patch('aye.controller.repl.get_user_config', return_value="on")
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_verbose_command(self, mock_telemetry, mock_get_config, mock_handle_verbose):
        """Verbose command should update conf.verbose."""
        result = self.dispatcher.dispatch("verbose", ["verbose", "on"])
        
        self.assertFalse(result)
        mock_handle_verbose.assert_called_once_with(["verbose", "on"])
        self.assertTrue(self.conf.verbose)

    @patch('aye.controller.repl.handle_debug_command')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_debug_command(self, mock_telemetry, mock_handle_debug):
        """Debug command should be dispatched."""
        result = self.dispatcher.dispatch("debug", ["debug", "on"])
        
        self.assertFalse(result)
        mock_handle_debug.assert_called_once_with(["debug", "on"])

    @patch('aye.controller.repl.handle_autodiff_command')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_autodiff_command(self, mock_telemetry, mock_handle_autodiff):
        """Autodiff command should be dispatched."""
        result = self.dispatcher.dispatch("autodiff", ["autodiff", "on"])
        
        self.assertFalse(result)
        mock_handle_autodiff.assert_called_once_with(["autodiff", "on"])

    @patch('aye.controller.repl.commands.get_snapshot_history', return_value=[])
    @patch('aye.controller.repl.cli_ui.print_snapshot_history')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_history_command(self, mock_telemetry, mock_print_history, mock_get_history):
        """History command should list snapshots."""
        result = self.dispatcher.dispatch("history", ["history"])
        
        self.assertFalse(result)
        mock_get_history.assert_called_once()
        mock_print_history.assert_called_once_with([])

    @patch('aye.controller.repl.commands.restore_from_snapshot')
    @patch('aye.controller.repl.cli_ui.print_restore_feedback')
    @patch('aye.controller.repl.set_user_config')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_restore_command(self, mock_telemetry, mock_set_config, mock_print, mock_restore):
        """Restore command should call restore_from_snapshot."""
        result = self.dispatcher.dispatch("restore", ["restore", "001", "file.py"])
        
        self.assertFalse(result)
        mock_restore.assert_called_once_with("001", "file.py")
        mock_print.assert_called_once_with("001", "file.py")
        mock_set_config.assert_called_with("has_used_restore", "on")

    @patch('aye.controller.repl.commands.restore_from_snapshot')
    @patch('aye.controller.repl.cli_ui.print_restore_feedback')
    @patch('aye.controller.repl.set_user_config')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_undo_command(self, mock_telemetry, mock_set_config, mock_print, mock_restore):
        """Undo command should work as alias for restore."""
        result = self.dispatcher.dispatch("undo", ["undo"])
        
        self.assertFalse(result)
        mock_restore.assert_called_once_with(None, None)

    @patch('aye.controller.repl.commands.prune_snapshots', return_value=5)
    @patch('aye.controller.repl.cli_ui.print_prune_feedback')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_keep_command_with_count(self, mock_telemetry, mock_print, mock_prune):
        """Keep command should prune snapshots."""
        result = self.dispatcher.dispatch("keep", ["keep", "5"])
        
        self.assertFalse(result)
        mock_prune.assert_called_once_with(5)
        mock_print.assert_called_once_with(5, 5)

    @patch('aye.controller.repl.commands.prune_snapshots', return_value=3)
    @patch('aye.controller.repl.cli_ui.print_prune_feedback')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_keep_command_default(self, mock_telemetry, mock_print, mock_prune):
        """Keep command without args should use default 10."""
        result = self.dispatcher.dispatch("keep", ["keep"])
        
        self.assertFalse(result)
        mock_prune.assert_called_once_with(10)

    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_keep_command_invalid_number(self, mock_telemetry, mock_rprint):
        """Keep command with invalid number should show error."""
        result = self.dispatcher.dispatch("keep", ["keep", "abc"])
        
        self.assertFalse(result)
        mock_rprint.assert_called()
        self.assertIn("not a valid number", mock_rprint.call_args[0][0])

    @patch('aye.controller.repl.handle_cd_command')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_cd_command(self, mock_telemetry, mock_handle_cd):
        """CD command should be dispatched."""
        result = self.dispatcher.dispatch("cd", ["cd", "/path"])
        
        self.assertFalse(result)
        mock_handle_cd.assert_called_once_with(["cd", "/path"], self.conf)

    @patch('aye.controller.repl._print_db_status')
    @patch('aye.controller.repl.telemetry')
    def test_dispatch_db_command(self, mock_telemetry, mock_print_db):
        """DB command should print database status."""
        result = self.dispatcher.dispatch("db", ["db"])
        
        self.assertFalse(result)
        mock_print_db.assert_called_once_with(self.conf)


class TestParseInput(unittest.TestCase):
    """Tests for _parse_input function."""

    def test_empty_input(self):
        """Empty input should return empty tokens."""
        force_shell, prompt, tokens = _parse_input("")
        
        self.assertFalse(force_shell)
        self.assertEqual(prompt, "")
        self.assertEqual(tokens, [])

    def test_whitespace_only(self):
        """Whitespace-only input should return empty tokens."""
        force_shell, prompt, tokens = _parse_input("   ")
        
        self.assertFalse(force_shell)
        self.assertEqual(tokens, [])

    def test_simple_command(self):
        """Simple command should be parsed correctly."""
        force_shell, prompt, tokens = _parse_input("help")
        
        self.assertFalse(force_shell)
        self.assertEqual(prompt, "help")
        self.assertEqual(tokens, ["help"])

    def test_command_with_args(self):
        """Command with arguments should be parsed correctly."""
        force_shell, prompt, tokens = _parse_input("model 1")
        
        self.assertFalse(force_shell)
        self.assertEqual(tokens, ["model", "1"])

    def test_force_shell_prefix(self):
        """! prefix should set force_shell flag."""
        force_shell, prompt, tokens = _parse_input("!ls -la")
        
        self.assertTrue(force_shell)
        self.assertEqual(prompt, "ls -la")
        self.assertEqual(tokens, ["ls", "-la"])

    def test_force_shell_prefix_only(self):
        """! alone should return empty tokens."""
        force_shell, prompt, tokens = _parse_input("!")
        
        self.assertTrue(force_shell)
        self.assertEqual(tokens, [])

    def test_force_shell_with_spaces(self):
        """! with trailing spaces should return empty tokens."""
        force_shell, prompt, tokens = _parse_input("!   ")
        
        self.assertTrue(force_shell)
        self.assertEqual(tokens, [])

    def test_quoted_arguments(self):
        """Quoted arguments should be parsed correctly."""
        force_shell, prompt, tokens = _parse_input('echo "hello world"')
        
        self.assertFalse(force_shell)
        self.assertEqual(tokens, ["echo", '"hello world"'])


class TestNormalizeCommand(unittest.TestCase):
    """Tests for _normalize_command function."""

    def test_lowercase_conversion(self):
        """Commands should be lowercased."""
        original, lowered, tokens = _normalize_command(["HELP"])
        
        self.assertEqual(original, "HELP")
        self.assertEqual(lowered, "help")

    def test_slash_prefix_removal(self):
        """Slash prefix should be removed."""
        original, lowered, tokens = _normalize_command(["/help"])
        
        self.assertEqual(lowered, "help")
        self.assertEqual(tokens, ["help"])

    def test_model_number_shortcut(self):
        """Single digit should become model command."""
        original, lowered, tokens = _normalize_command(["1"])
        
        self.assertEqual(lowered, "model")
        self.assertEqual(tokens, ["model", "1"])

    def test_model_number_out_of_range(self):
        """Number out of model range should not be converted."""
        # Assuming less than 100 models
        original, lowered, tokens = _normalize_command(["999"])
        
        self.assertEqual(lowered, "999")
        self.assertEqual(tokens, ["999"])

    def test_model_number_with_args_not_converted(self):
        """Number with args should not be converted to model command."""
        original, lowered, tokens = _normalize_command(["1", "arg"])
        
        self.assertEqual(lowered, "1")
        self.assertEqual(tokens, ["1", "arg"])

    def test_non_numeric_not_converted(self):
        """Non-numeric tokens should not be treated as model shortcuts."""
        original, lowered, tokens = _normalize_command(["abc"])
        
        self.assertEqual(lowered, "abc")
        self.assertEqual(tokens, ["abc"])


class TestGetCompleter(unittest.TestCase):
    """Tests for _get_completer function."""

    def test_returns_completer_from_plugin(self):
        """Should return completer from plugin manager."""
        mock_pm = MagicMock()
        mock_completer = MagicMock()
        mock_pm.handle_command.return_value = {"completer": mock_completer}
        
        result = _get_completer(mock_pm, "/project", "readline")
        
        self.assertEqual(result, mock_completer)
        mock_pm.handle_command.assert_called_once_with(
            "get_completer",
            {
                "commands": BUILTIN_COMMANDS,
                "project_root": "/project",
                "completion_style": "readline",
            }
        )

    def test_returns_none_when_plugin_returns_none(self):
        """Should return None when plugin doesn't provide completer."""
        mock_pm = MagicMock()
        mock_pm.handle_command.return_value = None
        
        result = _get_completer(mock_pm, "/project", "multi")
        
        self.assertIsNone(result)


class TestHandleShellCommand(unittest.TestCase):
    """Tests for _handle_shell_command function."""

    def test_successful_shell_command(self):
        """Shell command should be executed and return True."""
        conf = MagicMock()
        conf.plugin_manager.handle_command.return_value = {
            "stdout": "output",
            "stderr": "",
        }
        
        with patch('aye.controller.repl.telemetry'), \
             patch('aye.controller.repl.rprint'):
            result = _handle_shell_command("ls", ["ls", "-la"], conf)
        
        self.assertTrue(result)
        conf.plugin_manager.handle_command.assert_called_once()

    def test_unrecognized_command_returns_false(self):
        """Unrecognized command should return False."""
        conf = MagicMock()
        conf.plugin_manager.handle_command.return_value = None
        
        result = _handle_shell_command("notacommand", ["notacommand"], conf)
        
        self.assertFalse(result)


class TestHandleLlmInvocation(unittest.TestCase):
    """Tests for _handle_llm_invocation function."""

    def setUp(self):
        """Set up test fixtures."""
        self.conf = MagicMock()
        self.conf.root = Path("/project")
        self.conf.plugin_manager = MagicMock()
        self.conf.verbose = False
        
        self.ctx = CommandContext(
            conf=self.conf,
            session=MagicMock(),
            console=MagicMock(),
            chat_id=1,
        )

    @patch('aye.controller.repl.invoke_llm')
    @patch('aye.controller.repl.process_llm_response')
    @patch('aye.controller.repl.telemetry')
    def test_basic_llm_invocation(self, mock_telemetry, mock_process, mock_invoke):
        """Basic LLM invocation without @ references."""
        mock_invoke.return_value = MagicMock(chat_id=None)
        self.conf.plugin_manager.handle_command.return_value = None
        
        _handle_llm_invocation("explain this", self.ctx)
        
        mock_invoke.assert_called_once()
        mock_telemetry.record_llm_prompt.assert_called_with("LLM")

    @patch('aye.controller.repl.invoke_llm')
    @patch('aye.controller.repl.process_llm_response')
    @patch('aye.controller.repl.telemetry')
    def test_llm_invocation_with_at_reference(self, mock_telemetry, mock_process, mock_invoke):
        """LLM invocation with @ file references."""
        mock_invoke.return_value = MagicMock(chat_id=None)
        self.conf.plugin_manager.handle_command.return_value = {
            "file_contents": {"main.py": "content"},
            "cleaned_prompt": "explain this",
        }
        
        _handle_llm_invocation("explain @main.py", self.ctx)
        
        mock_invoke.assert_called_once()
        mock_telemetry.record_llm_prompt.assert_called_with("LLM @")

    @patch('aye.controller.repl.invoke_llm')
    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.telemetry')
    def test_llm_invocation_no_response(self, mock_telemetry, mock_rprint, mock_invoke):
        """LLM invocation with no response."""
        mock_invoke.return_value = None
        self.conf.plugin_manager.handle_command.return_value = None
        
        _handle_llm_invocation("prompt", self.ctx)
        
        mock_rprint.assert_called_with("[yellow]No response from LLM.[/]")

    @patch('aye.controller.repl.invoke_llm')
    @patch('aye.controller.repl.process_llm_response')
    @patch('aye.controller.repl.telemetry')
    def test_llm_invocation_updates_chat_id(self, mock_telemetry, mock_process, mock_invoke):
        """LLM invocation should update chat_id from response."""
        mock_invoke.return_value = MagicMock(chat_id=100)
        mock_process.return_value = 200
        self.conf.plugin_manager.handle_command.return_value = None
        
        _handle_llm_invocation("prompt", self.ctx)
        
        self.assertEqual(self.ctx.chat_id, 200)


class TestPrintDbStatus(unittest.TestCase):
    """Tests for _print_db_status function."""

    @patch('aye.controller.repl.rprint')
    def test_no_index_manager(self, mock_rprint):
        """Should handle missing index manager."""
        conf = SimpleNamespace(index_manager=None, use_rag=True)
        
        _print_db_status(conf)
        
        mock_rprint.assert_called_with("[red]Index manager not available.[/red]")

    @patch('aye.controller.repl.rprint')
    def test_small_project_mode(self, mock_rprint):
        """Should show small project message when use_rag=False."""
        conf = SimpleNamespace(index_manager=None, use_rag=False)
        
        _print_db_status(conf)
        
        mock_rprint.assert_called_with(
            "[yellow]Small project mode: RAG indexing is disabled.[/yellow]"
        )

    @patch('aye.controller.repl.rprint')
    def test_with_index_manager(self, mock_rprint):
        """Should print collection info when index manager exists."""
        mock_collection = MagicMock()
        mock_collection.count.return_value = 100
        mock_collection.name = "test_collection"
        mock_collection.peek.return_value = {
            "ids": ["id1"],
            "metadatas": [{"file": "test.py"}],
            "documents": ["test content"],
        }
        
        mock_index_manager = MagicMock()
        mock_index_manager.collection = mock_collection
        
        conf = SimpleNamespace(index_manager=mock_index_manager)
        
        _print_db_status(conf)
        
        # Verify rprint was called with collection info
        calls = [str(c) for c in mock_rprint.call_args_list]
        self.assertTrue(any("test_collection" in c for c in calls))
        self.assertTrue(any("100" in c for c in calls))


class TestCreateKeyBindings(unittest.TestCase):
    """Tests for create_key_bindings function."""

    def test_returns_key_bindings(self):
        """Should return KeyBindings object."""
        from prompt_toolkit.key_binding import KeyBindings
        
        bindings = create_key_bindings()
        
        self.assertIsInstance(bindings, KeyBindings)


class TestCreatePromptSession(unittest.TestCase):
    """Tests for create_prompt_session function."""

    @patch('aye.controller.repl.PromptSession')
    def test_creates_session_with_completer(self, mock_session_class):
        """Should create session with provided completer."""
        from aye.controller.repl import create_prompt_session
        
        mock_completer = MagicMock()
        mock_session_instance = MagicMock()
        mock_session_class.return_value = mock_session_instance
        
        session = create_prompt_session(mock_completer, "readline")
        
        mock_session_class.assert_called_once()
        call_kwargs = mock_session_class.call_args[1]
        self.assertEqual(call_kwargs['completer'], mock_completer)
        self.assertEqual(session, mock_session_instance)

    @patch('aye.controller.repl.PromptSession')
    def test_creates_session_without_completer(self, mock_session_class):
        """Should create session even without completer."""
        from aye.controller.repl import create_prompt_session
        
        mock_session_instance = MagicMock()
        mock_session_class.return_value = mock_session_instance
        
        session = create_prompt_session(None, "multi")
        
        mock_session_class.assert_called_once()
        call_kwargs = mock_session_class.call_args[1]
        self.assertIsNone(call_kwargs['completer'])
        self.assertEqual(session, mock_session_instance)


class TestPrintStartupHeader(unittest.TestCase):
    """Tests for print_startup_header function."""

    @patch('aye.controller.repl.MODELS', [
        {"id": "test/model", "name": "Test Model"},
    ])
    @patch('aye.controller.repl.print_welcome_message')
    @patch('aye.controller.repl.rprint')
    def test_prints_model_and_mask(self, mock_rprint, mock_welcome):
        """Should print model name and file mask."""
        conf = SimpleNamespace(
            selected_model="test/model",
            file_mask="*.py",
        )
        
        print_startup_header(conf)
        
        calls = [str(c) for c in mock_rprint.call_args_list]
        self.assertTrue(any("*.py" in c for c in calls))
        self.assertTrue(any("Test Model" in c for c in calls))
        mock_welcome.assert_called_once()

    @patch('aye.controller.repl.MODELS', [
        {"id": "default/model", "name": "Default Model"},
    ])
    @patch('aye.controller.repl.DEFAULT_MODEL_ID', "default/model")
    @patch('aye.controller.repl.set_user_config')
    @patch('aye.controller.repl.print_welcome_message')
    @patch('aye.controller.repl.rprint')
    def test_falls_back_to_default_model(self, mock_rprint, mock_welcome, mock_set_config):
        """Should fall back to default model if selected not found."""
        conf = SimpleNamespace(
            selected_model="nonexistent/model",
            file_mask="*.py",
        )
        
        print_startup_header(conf)
        
        self.assertEqual(conf.selected_model, "default/model")
        mock_set_config.assert_called_with("selected_model", "default/model")


class TestSetupContext(unittest.TestCase):
    """Tests for _setup_context function."""

    @patch('aye.controller.repl.get_user_config', return_value="readline")
    @patch('aye.controller.repl._get_completer', return_value=None)
    @patch('aye.controller.repl.create_prompt_session')
    def test_creates_context(self, mock_create_session, mock_completer, mock_config):
        """Should create CommandContext with all components."""
        conf = MagicMock()
        conf.root = Path("/project")
        conf.plugin_manager = MagicMock()
        
        mock_session = MagicMock()
        mock_create_session.return_value = mock_session
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change to temp directory so .aye can be created
            import os
            original_cwd = os.getcwd()
            os.chdir(tmpdir)
            
            try:
                ctx = _setup_context(conf)
                
                self.assertIsInstance(ctx, CommandContext)
                self.assertEqual(ctx.conf, conf)
                self.assertEqual(ctx.session, mock_session)
                self.assertEqual(ctx.chat_id, -1)
                self.assertEqual(ctx.completion_style, "readline")
            finally:
                os.chdir(original_cwd)


class TestBuiltinCommands(unittest.TestCase):
    """Tests for BUILTIN_COMMANDS list."""

    def test_contains_expected_commands(self):
        """BUILTIN_COMMANDS should contain all expected commands."""
        expected = [
            "with", "new", "history", "diff", "restore", "undo",
            "model", "verbose", "debug", "autodiff", "completion",
            "exit", "quit", ":q", "help", "cd", "db", "llm",
        ]
        
        for cmd in expected:
            self.assertIn(cmd, BUILTIN_COMMANDS, f"{cmd} not in BUILTIN_COMMANDS")

    def test_no_duplicates(self):
        """BUILTIN_COMMANDS should not have duplicates."""
        self.assertEqual(len(BUILTIN_COMMANDS), len(set(BUILTIN_COMMANDS)))


if __name__ == "__main__":
    unittest.main()
