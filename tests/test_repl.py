import os
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock

from prompt_toolkit.filters import completion_is_selected
from prompt_toolkit.keys import Keys

import aye.controller.repl as repl
from aye.model.config import MODELS


def _setup_mock_chat_id_path(mock_path, *, exists=False, contents=""):
    mock_chat_id_file = MagicMock()
    mock_chat_id_file.exists.return_value = exists
    mock_chat_id_file.read_text.return_value = contents
    mock_chat_id_file.parent = MagicMock()
    mock_chat_id_file.parent.mkdir = MagicMock()
    mock_path.return_value = mock_chat_id_file
    return mock_chat_id_file


class TestRepl(TestCase):
    def setUp(self):
        self.conf = SimpleNamespace(root=Path.cwd(), file_mask="*.py")
        self.session = MagicMock()

        # Telemetry is global, in-memory state. Reset between tests to avoid leakage.
        repl.telemetry.reset()
        repl.telemetry.set_enabled(False)

    @patch("os.chdir")
    @patch("aye.controller.command_handlers.rprint")
    def test_handle_cd_command_success(self, mock_rprint, mock_chdir):
        target_dir = "/tmp"

        with patch("pathlib.Path.cwd", return_value=Path(target_dir)):
            result = repl.handle_cd_command(["cd", target_dir], self.conf)

        self.assertTrue(result)
        mock_chdir.assert_called_once_with(target_dir)
        self.assertEqual(self.conf.root, Path(target_dir))
        mock_rprint.assert_called_once_with(str(Path(target_dir)))

    @patch("os.chdir")
    @patch("aye.controller.command_handlers.rprint")
    def test_handle_cd_command_home(self, mock_rprint, mock_chdir):
        home_dir = str(Path.home())
        with patch("pathlib.Path.cwd", return_value=Path(home_dir)):
            repl.handle_cd_command(["cd"], self.conf)
        mock_chdir.assert_called_once_with(home_dir)

    @patch("os.chdir", side_effect=FileNotFoundError("No such file or directory"))
    @patch("aye.controller.command_handlers.print_error")
    def test_handle_cd_command_failure(self, mock_print_error, mock_chdir):
        result = repl.handle_cd_command(["cd", "/nonexistent"], self.conf)
        self.assertFalse(result)
        mock_chdir.assert_called_once_with("/nonexistent")
        mock_print_error.assert_called_once()

    @patch("aye.controller.command_handlers.rprint")
    @patch("aye.controller.command_handlers.set_user_config")
    def test_handle_model_command_list_and_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]["id"]
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None
        self.session.prompt.return_value = "2"

        repl.handle_model_command(self.session, MODELS, self.conf, ["model"])

        self.session.prompt.assert_called_once()
        self.assertEqual(self.conf.selected_model, MODELS[1]["id"])
        mock_set_config.assert_called_once_with("selected_model", MODELS[1]["id"])
        self.assertIn(f"Selected: {MODELS[1]['name']}", str(mock_rprint.call_args_list))

    @patch("aye.controller.command_handlers.rprint")
    @patch("aye.controller.command_handlers.set_user_config")
    def test_handle_model_command_direct_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]["id"]
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None

        repl.handle_model_command(self.session, MODELS, self.conf, ["model", "3"])

        self.session.prompt.assert_not_called()
        self.assertEqual(self.conf.selected_model, MODELS[2]["id"])
        mock_set_config.assert_called_once_with("selected_model", MODELS[2]["id"])
        self.assertIn(f"Selected model: {MODELS[2]['name']}", str(mock_rprint.call_args_list))

    @patch("aye.controller.command_handlers.rprint")
    @patch("aye.controller.command_handlers.set_user_config")
    def test_handle_model_command_invalid_input(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]["id"]
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None

        repl.handle_model_command(self.session, MODELS, self.conf, ["model", "99"])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid model number.[/]")

        repl.handle_model_command(self.session, MODELS, self.conf, ["model", "abc"])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid input. Use a number.[/]")

        self.session.prompt.return_value = "xyz"
        repl.handle_model_command(self.session, MODELS, self.conf, ["model"])
        mock_rprint.assert_any_call("[red]Invalid input.[/]")

    @patch("aye.controller.command_handlers.rprint")
    @patch("aye.controller.command_handlers.set_user_config")
    def test_handle_verbose_command(self, mock_set_config, mock_rprint):
        repl.handle_verbose_command(["verbose", "on"])
        mock_set_config.assert_called_with("verbose", "on")
        mock_rprint.assert_any_call("[green]Verbose mode set to On[/]")

        repl.handle_verbose_command(["verbose", "off"])
        mock_set_config.assert_called_with("verbose", "off")
        mock_rprint.assert_any_call("[green]Verbose mode set to Off[/]")

        repl.handle_verbose_command(["verbose", "invalid"])
        mock_rprint.assert_any_call("[red]Usage: verbose on|off[/]")

    @patch("aye.controller.command_handlers.get_user_config", return_value="off")
    @patch("aye.controller.command_handlers.rprint")
    def test_handle_verbose_command_status(self, mock_rprint, mock_get_config):
        repl.handle_verbose_command(["verbose"])
        mock_get_config.assert_called_with("verbose", "off")
        mock_rprint.assert_called_with("[yellow]Verbose mode is Off[/]")

    @patch("aye.controller.repl.print_welcome_message")
    @patch("aye.controller.repl.rprint")
    def test_print_startup_header_known_model(self, mock_rprint, mock_welcome):
        conf = SimpleNamespace(selected_model=MODELS[0]["id"], file_mask="*.*")
        repl.print_startup_header(conf)
        mock_rprint.assert_any_call(f"[bold cyan]Session context: {conf.file_mask}[/]")
        mock_rprint.assert_any_call(f"[bold cyan]Current model: {MODELS[0]['name']}[/]")
        mock_welcome.assert_called_once()

    @patch("aye.controller.repl.rprint")
    def test_print_startup_header_unknown_model(self, mock_rprint):
        conf = SimpleNamespace(selected_model="unknown/model", file_mask="*.py")
        with patch("aye.controller.repl.set_user_config") as mock_set_config:
            repl.print_startup_header(conf)
            mock_set_config.assert_called_once_with("selected_model", repl.DEFAULT_MODEL_ID)
            self.assertEqual(conf.selected_model, repl.DEFAULT_MODEL_ID)

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.Confirm.ask")
    @patch("aye.controller.repl.set_user_config")
    @patch("aye.controller.repl.get_user_config")
    def test_prompt_for_telemetry_consent_already_set(
        self, mock_get, mock_set, mock_confirm, mock_rprint
    ):
        mock_get.return_value = "on"
        self.assertTrue(repl._prompt_for_telemetry_consent_if_needed())
        mock_confirm.assert_not_called()
        mock_set.assert_not_called()

        mock_get.return_value = "off"
        self.assertFalse(repl._prompt_for_telemetry_consent_if_needed())
        mock_confirm.assert_not_called()
        mock_set.assert_not_called()

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.Confirm.ask", return_value=False)
    @patch("aye.controller.repl.set_user_config")
    @patch("aye.controller.repl.get_user_config", return_value=None)
    def test_prompt_for_telemetry_consent_prompts_and_persists(
        self, mock_get, mock_set, mock_confirm, mock_rprint
    ):
        self.assertFalse(repl._prompt_for_telemetry_consent_if_needed())
        mock_confirm.assert_called_once()
        mock_set.assert_called_once_with(repl._TELEMETRY_OPT_IN_KEY, "off")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.Confirm.ask", side_effect=EOFError)
    @patch("aye.controller.repl.set_user_config")
    @patch("aye.controller.repl.get_user_config", return_value=None)
    def test_prompt_for_telemetry_consent_handles_eof_ctrlc(
        self, mock_get, mock_set, mock_confirm, mock_rprint
    ):
        self.assertFalse(repl._prompt_for_telemetry_consent_if_needed())
        mock_set.assert_called_once_with(repl._TELEMETRY_OPT_IN_KEY, "off")

    @patch("aye.controller.repl._is_feedback_prompt_enabled", return_value=True)
    @patch("aye.controller.repl.send_feedback")
    @patch("aye.controller.repl.PromptSession")
    def test_collect_and_send_feedback(self, mock_session_cls, mock_send_feedback, mock_enabled):
        mock_session_cls.return_value.prompt.return_value = "Great tool!"
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_called_once_with("Great tool!", chat_id=123, telemetry=None)

    @patch("aye.controller.repl.send_feedback")
    @patch("aye.controller.repl.PromptSession")
    def test_collect_and_send_feedback_empty(self, mock_session_cls, mock_send_feedback):
        # Telemetry disabled in setUp, so empty feedback should not send.
        mock_session_cls.return_value.prompt.return_value = "  \n  "
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_not_called()

    @patch("aye.controller.repl.send_feedback")
    @patch("aye.controller.repl.PromptSession")
    def test_collect_and_send_feedback_ctrl_c(self, mock_session_cls, mock_send_feedback):
        # Telemetry disabled in setUp, so Ctrl+C with no feedback should not send.
        mock_session_cls.return_value.prompt.side_effect = KeyboardInterrupt
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_not_called()

    @patch("aye.controller.repl._is_feedback_prompt_enabled", return_value=True)
    @patch("aye.controller.repl.send_feedback", side_effect=Exception("API down"))
    @patch("aye.controller.repl.PromptSession")
    def test_collect_and_send_feedback_api_error(self, mock_session_cls, mock_send_feedback, mock_enabled):
        # New implementation does not swallow send_feedback exceptions.
        mock_session_cls.return_value.prompt.return_value = "feedback"
        with self.assertRaises(Exception):
            repl.collect_and_send_feedback(chat_id=123)

    @patch("aye.controller.repl._is_feedback_prompt_enabled", return_value=True)
    @patch("aye.controller.repl.telemetry.reset")
    @patch("aye.controller.repl.telemetry.build_payload", return_value={"x": 1})
    @patch("aye.controller.repl.telemetry.is_enabled", return_value=True)
    @patch("aye.controller.repl.send_feedback")
    @patch("aye.controller.repl.PromptSession")
    def test_collect_and_send_feedback_includes_telemetry_and_resets(
        self,
        mock_session_cls,
        mock_send_feedback,
        mock_is_enabled,
        mock_build,
        mock_reset,
        mock_enabled,
    ):
        mock_session_cls.return_value.prompt.return_value = "hello"
        repl.collect_and_send_feedback(chat_id=5)
        mock_send_feedback.assert_called_once_with("hello", chat_id=5, telemetry={"x": 1})
        mock_reset.assert_called_once()

    def test_chat_repl_main_loop_commands(self):
        with (
            patch("aye.controller.repl.PromptSession") as mock_session_cls,
            patch("aye.controller.repl.run_first_time_tutorial_if_needed"),
            patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
            patch("aye.controller.repl.get_user_config", return_value="on"),
            patch("aye.controller.repl.print_startup_header"),
            patch("aye.controller.repl.Path") as mock_path,
            patch("aye.controller.repl.handle_model_command") as mock_model_cmd,
            patch("aye.controller.repl.commands") as mock_commands,
            patch("aye.controller.repl.cli_ui") as mock_cli_ui,
            patch("aye.controller.repl.diff_presenter") as mock_diff,
            patch("aye.controller.repl.print_help_message") as mock_help,
            patch("aye.controller.repl.invoke_llm") as mock_invoke,
            patch("aye.controller.repl.process_llm_response", return_value=None) as mock_process,
            patch("aye.controller.repl.collect_and_send_feedback"),
        ):
            mock_session = MagicMock()
            mock_session.prompt.side_effect = [
                "model",
                "history",
                "diff file.py 001",
                "restore 001",
                "keep 5",
                "new",
                "help",
                "ls -l",
                "a real prompt",
                "exit",
            ]
            mock_session_cls.return_value = mock_session

            mock_chat_id_file = MagicMock()
            mock_chat_id_file.exists.return_value = False
            mock_path.return_value = mock_chat_id_file

            # Return 3-tuple: (path1, path2, is_stash)
            mock_commands.get_diff_paths.return_value = (Path("p1"), "p2", False)

            # Use a function to handle different command calls dynamically
            def plugin_side_effect(command, params):
                if command == "get_completer":
                    return {"completer": None}
                elif command == "new_chat":
                    return None
                elif command == "execute_shell_command":
                    cmd = params.get("command", "")
                    if cmd == "ls":
                        return {"stdout": "files"}
                    # 'a' is not a real shell command, return None to trigger LLM
                    return None
                elif command == "parse_at_references":
                    # No @ references found
                    return None
                return None

            mock_plugin_manager = MagicMock()
            mock_plugin_manager.handle_command.side_effect = plugin_side_effect

            mock_index_manager = MagicMock()
            mock_index_manager.has_work.return_value = False
            mock_index_manager.is_indexing.return_value = False

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=mock_plugin_manager,
                index_manager=mock_index_manager,
                verbose=True,
                selected_model="test-model",
            )

            repl.chat_repl(conf)

            self.assertEqual(mock_session.prompt.call_count, 10)
            self.assertEqual(mock_model_cmd.call_count, 2)
            mock_commands.get_snapshot_history.assert_called_once()
            mock_commands.get_diff_paths.assert_called_once_with("file.py", "001", None)
            mock_diff.show_diff.assert_called_once_with(Path("p1"), "p2", is_stash_ref=False)
            mock_commands.restore_from_snapshot.assert_called_once_with("001", None)
            mock_commands.prune_snapshots.assert_called_once_with(5)
            mock_chat_id_file.unlink.assert_called_once()
            self.assertEqual(mock_help.call_count, 2)

            # Verify specific plugin calls were made
            call_commands = [c[0][0] for c in mock_plugin_manager.handle_command.call_args_list]
            self.assertIn("get_completer", call_commands)
            self.assertIn("new_chat", call_commands)
            self.assertIn("execute_shell_command", call_commands)
            self.assertIn("parse_at_references", call_commands)

            mock_invoke.assert_called_once()
            mock_process.assert_called_once()


class TestExecuteForcedShellCommand(TestCase):
    """Tests for _execute_forced_shell_command function."""

    def setUp(self):
        repl.telemetry.reset()
        repl.telemetry.set_enabled(False)

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_executes_with_force_flag_and_prints_stdout(self, mock_record, mock_rprint):
        """Test that command executes with force=True and stdout is printed."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "file1.txt\nfile2.txt",
            "stderr": "",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("ls", ["-la"], conf)

        plugin_manager.handle_command.assert_called_once_with(
            "execute_shell_command",
            {"command": "ls", "args": ["-la"], "force": True}
        )
        mock_rprint.assert_called_once_with("file1.txt\nfile2.txt")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_prints_stderr_with_yellow_formatting(self, mock_record, mock_rprint):
        """Test that stderr is printed with yellow formatting."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "",
            "stderr": "warning: something happened",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("cmd", [], conf)

        mock_rprint.assert_called_once_with("[yellow]warning: something happened[/]")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_prints_both_stdout_and_stderr(self, mock_record, mock_rprint):
        """Test that both stdout and stderr are printed when present."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "output here",
            "stderr": "warning here",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("cmd", ["arg1"], conf)

        assert mock_rprint.call_count == 2
        mock_rprint.assert_any_call("output here")
        mock_rprint.assert_any_call("[yellow]warning here[/]")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_prints_error_with_red_formatting(self, mock_record, mock_rprint):
        """Test that error in response is printed with red formatting."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "partial output",
            "stderr": "",
            "error": "Command failed with exit code 1",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("failing_cmd", [], conf)

        assert mock_rprint.call_count == 2
        mock_rprint.assert_any_call("partial output")
        mock_rprint.assert_any_call("[red]Error:[/] Command failed with exit code 1")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_prints_message_for_interactive_commands(self, mock_record, mock_rprint):
        """Test that message-only responses (e.g., interactive commands) are printed."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "message": "Interactive command 'vim' completed (exit code: 0)."
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("vim", ["file.txt"], conf)

        mock_rprint.assert_called_once_with(
            "Interactive command 'vim' completed (exit code: 0)."
        )

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_prints_error_when_plugin_returns_none(self, mock_record, mock_rprint):
        """Test that error is printed when plugin returns None."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = None
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("nonexistent", [], conf)

        mock_rprint.assert_called_once_with("[red]Error:[/] Failed to execute shell command")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_records_telemetry_with_args(self, mock_record, mock_rprint):
        """Test that telemetry is recorded with has_args=True when args present."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {"stdout": "ok", "stderr": ""}
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("git", ["status", "-s"], conf)

        mock_record.assert_called_once_with("git", has_args=True, prefix="cmd:")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_records_telemetry_without_args(self, mock_record, mock_rprint):
        """Test that telemetry is recorded with has_args=False when no args."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {"stdout": "ok", "stderr": ""}
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("pwd", [], conf)

        mock_record.assert_called_once_with("pwd", has_args=False, prefix="cmd:")

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_does_not_print_empty_stdout_or_stderr(self, mock_record, mock_rprint):
        """Test that empty stdout/stderr strings don't result in prints."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "   ",  # whitespace only
            "stderr": "",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("cmd", [], conf)

        mock_rprint.assert_not_called()

    @patch("aye.controller.repl.rprint")
    @patch("aye.controller.repl.telemetry.record_command")
    def test_handles_stdout_stderr_and_error_together(self, mock_record, mock_rprint):
        """Test all three outputs printed when all present."""
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "stdout": "some output",
            "stderr": "some warning",
            "error": "but it failed",
        }
        conf = SimpleNamespace(plugin_manager=plugin_manager)

        repl._execute_forced_shell_command("cmd", ["arg"], conf)

        assert mock_rprint.call_count == 3
        mock_rprint.assert_any_call("some output")
        mock_rprint.assert_any_call("[yellow]some warning[/]")
        mock_rprint.assert_any_call("[red]Error:[/] but it failed")


def test_chat_repl_force_shell_prefix_executes_via_forced_function():
    """Test that ! prefix triggers _execute_forced_shell_command in REPL."""
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl._execute_forced_shell_command") as mock_forced,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["!mycommand arg1 arg2", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        mock_forced.assert_called_once_with("mycommand", ["arg1", "arg2"], conf)


def test_chat_repl_force_shell_empty_after_bang_skips():
    """Test that '!' alone is skipped without error."""
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl._execute_forced_shell_command") as mock_forced,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["!", "!   ", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        mock_forced.assert_not_called()


def test_create_key_bindings_registers_two_enter_bindings_and_handlers_work():
    bindings = repl.create_key_bindings()
    enter_bindings = bindings.get_bindings_for_keys((Keys.Enter,))

    assert len(enter_bindings) == 2

    selected_binding = next(b for b in enter_bindings if b.filter == completion_is_selected)
    first_binding = next(b for b in enter_bindings if b.filter != completion_is_selected)

    # accept_selected_completion
    completion = object()
    complete_state = SimpleNamespace(current_completion=completion, completions=[completion])
    buffer = MagicMock()
    buffer.complete_state = complete_state
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=buffer))

    selected_binding.handler(event)
    buffer.apply_completion.assert_called_once_with(completion)
    assert buffer.complete_state is None

    # accept_first_completion
    completion2 = object()
    complete_state2 = SimpleNamespace(current_completion=None, completions=[completion2])
    buffer2 = MagicMock()
    buffer2.complete_state = complete_state2
    event2 = SimpleNamespace(app=SimpleNamespace(current_buffer=buffer2))

    first_binding.handler(event2)
    buffer2.apply_completion.assert_called_once_with(completion2)
    assert buffer2.complete_state is None


def test_create_prompt_session_uses_multicolumn_and_key_bindings():
    completer = MagicMock()

    with patch("aye.controller.repl.PromptSession") as mock_session_cls:
        repl.create_prompt_session(completer=completer, completion_style="readline")

        assert mock_session_cls.call_count == 1
        kwargs = mock_session_cls.call_args.kwargs
        assert kwargs["completer"] is completer
        assert kwargs["complete_style"] == repl.CompleteStyle.MULTI_COLUMN
        assert kwargs["complete_while_typing"] is True
        assert kwargs["key_bindings"] is not None


def test_chat_repl_starts_background_indexing_when_has_work():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.threading.Thread") as mock_thread_cls,
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.print_help_message"),
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        thread_instance = MagicMock()
        mock_thread_cls.return_value = thread_instance

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = True
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
        )

        repl.chat_repl(conf)

        mock_thread_cls.assert_called_once_with(
            target=index_manager.run_sync_in_background, daemon=True
        )
        thread_instance.start.assert_called_once()


def test_chat_repl_shell_command_outputs_error_info():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
        patch("aye.controller.repl.rprint") as mock_rprint,
    ):
        session = MagicMock()
        session.prompt.side_effect = ["ls", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [
            {"completer": None},
            {"stdout": "files", "stderr": "warn", "error": "bad"},
        ]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
        )

        repl.chat_repl(conf)

        outputs = [c.args[0] for c in mock_rprint.call_args_list]
        assert "files" in outputs
        assert "[yellow]warn[/]" in outputs
        assert "[red]Error:[/] bad" in outputs


def test_chat_repl_db_command_with_collection_sample():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
        patch("aye.controller.repl.rprint") as mock_rprint,
    ):
        session = MagicMock()
        session.prompt.side_effect = ["/db", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        long_doc = "A" * 80 + "\nmore text"
        collection = MagicMock()
        collection.name = "test"
        collection.count.return_value = 2
        collection.peek.return_value = {
            "ids": ["1"],
            "metadatas": [{"foo": "bar"}],
            "documents": [long_doc],
        }

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False
        index_manager.collection = collection

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        printed = [call.args[0] for call in mock_rprint.call_args_list]
        assert any("Vector DB Status" in str(line) for line in printed)
        assert any("Sample of up to 5 records" in str(line) for line in printed)
        assert any(
            "[yellow]Content:[/]" in str(line) and str(line).endswith('..."')
            for line in printed
        )


def test_chat_repl_db_command_empty_collection_prints_empty_message():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
        patch("aye.controller.repl.rprint") as mock_rprint,
    ):
        session = MagicMock()
        session.prompt.side_effect = ["db", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        collection = MagicMock()
        collection.name = "empty"
        collection.count.return_value = 0

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False
        index_manager.collection = collection

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        printed = [call.args[0] for call in mock_rprint.call_args_list]
        assert any("The vector index is empty" in str(x) for x in printed)


def test_chat_repl_db_command_peek_exception_is_handled():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
        patch("aye.controller.repl.rprint") as mock_rprint,
    ):
        session = MagicMock()
        session.prompt.side_effect = ["db", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        collection = MagicMock()
        collection.name = "boom"
        collection.count.return_value = 1
        collection.peek.side_effect = RuntimeError("nope")

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False
        index_manager.collection = collection

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        printed = [call.args[0] for call in mock_rprint.call_args_list]
        assert any("Could not retrieve sample records" in str(x) for x in printed)


def test_chat_repl_handles_tokenization_error():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.print_error") as mock_print_error,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ['bad "input', "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
        )

        repl.chat_repl(conf)

        mock_print_error.assert_called_once()
        assert isinstance(mock_print_error.call_args[0][0], ValueError)


def test_chat_repl_handles_exception_and_calls_error_handler():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.handle_llm_error") as mock_handle_error,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["ls", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        def plugin_side_effect(command, *_args, **_kwargs):
            if command == "get_completer":
                return {"completer": None}
            raise RuntimeError("boom")

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = plugin_side_effect

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
        )

        repl.chat_repl(conf)

        mock_handle_error.assert_called_once()


def test_chat_repl_invalid_chat_id_file_is_cleaned():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["exit"]
        mock_session_cls.return_value = session

        chat_id_file = _setup_mock_chat_id_path(mock_path, exists=True, contents="abc")

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
        )

        repl.chat_repl(conf)

        chat_id_file.unlink.assert_called_once_with(missing_ok=True)


def test_chat_repl_completion_command_recreates_completer_and_session():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.handle_completion_command", return_value="multi") as mock_completion,
        patch("aye.controller.repl.create_prompt_session") as mock_create_session,
        patch("aye.controller.repl.rprint") as mock_rprint,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["completion multi", "exit"]
        mock_create_session.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()

        def plugin_side_effect(command, params):
            if command == "get_completer":
                return {"completer": None}
            return None

        plugin_manager.handle_command.side_effect = plugin_side_effect

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        # One session created at startup, one recreated after completion command
        assert mock_create_session.call_count == 2
        assert mock_completion.call_count == 1
        assert any(
            "Completion style is now active" in str(c.args[0]) for c in mock_rprint.call_args_list
        )

        # get_completer should be called twice (startup + after completion style change)
        get_completer_calls = [
            c
            for c in plugin_manager.handle_command.call_args_list
            if c.args and c.args[0] == "get_completer"
        ]
        assert len(get_completer_calls) == 2


def test_chat_repl_model_number_shortcut_calls_model_handler():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.handle_model_command") as mock_model_cmd,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        # Use real create_prompt_session path via patched PromptSession
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["2", "exit"]
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model="model",
                use_rag=True,
            )

            repl.chat_repl(conf)

        mock_model_cmd.assert_called_once()
        args = mock_model_cmd.call_args.args
        assert args[3] == ["model", "2"]


def test_chat_repl_slash_prefixed_shell_command_executes_normalized_command():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.rprint") as mock_rprint,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["/ls -l", "exit"]
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            def plugin_side_effect(command, params):
                if command == "get_completer":
                    return {"completer": None}
                if command == "execute_shell_command":
                    assert params["command"] == "ls"
                    assert params["args"] == ["-l"]
                    return {"stdout": "ok"}
                return None

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = plugin_side_effect

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model="model",
                use_rag=True,
            )

            repl.chat_repl(conf)

        mock_rprint.assert_any_call("ok")


def test_chat_repl_reads_valid_chat_id_file_passes_to_llm_and_updates_chat_id():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.invoke_llm") as mock_invoke,
        patch("aye.controller.repl.process_llm_response", return_value=9) as mock_process,
        patch("aye.controller.repl.collect_and_send_feedback") as mock_feedback,
    ):
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["hello", "exit"]
            mock_session_cls.return_value = session

            chat_id_file = _setup_mock_chat_id_path(mock_path, exists=True, contents="7")

            def plugin_side_effect(command, params):
                if command == "get_completer":
                    return {"completer": None}
                if command == "execute_shell_command":
                    return None
                if command == "parse_at_references":
                    return None
                return None

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = plugin_side_effect

            # LLM response object with chat_id; process_llm_response should receive chat_id_file
            mock_invoke.return_value = SimpleNamespace(chat_id=9)

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model="model",
                use_rag=True,
            )

            repl.chat_repl(conf)

        # First LLM invocation should receive chat_id from file
        assert mock_invoke.call_count == 1
        assert mock_invoke.call_args.kwargs["chat_id"] == 7

        # process_llm_response should have been passed a chat_id_file (since llm_response.chat_id truthy)
        assert mock_process.call_count == 1
        assert mock_process.call_args.kwargs["chat_id_file"] is chat_id_file

        # Final feedback should be called with updated chat_id (9)
        mock_feedback.assert_called_once_with(9)


def test_chat_repl_db_command_no_index_manager_rag_disabled_prints_small_project_mode():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.rprint") as mock_rprint,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["db", "exit"]
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model="model",
                use_rag=False,
            )

            repl.chat_repl(conf)

        mock_rprint.assert_any_call(
            "[yellow]Small project mode: RAG indexing is disabled.[/yellow]"
        )


def test_chat_repl_always_shuts_down_index_manager_in_finally():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["exit"]
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            index_manager = MagicMock()
            index_manager.has_work.return_value = False
            index_manager.is_indexing.return_value = False

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=index_manager,
                verbose=False,
                selected_model="model",
                use_rag=True,
            )

            repl.chat_repl(conf)

        index_manager.shutdown.assert_called_once()


def test_chat_repl_when_indexing_and_verbose_prefixes_prompt_with_progress():
    with (
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.handle_model_command"),
        patch("aye.controller.repl.print_help_message"),
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        with patch("aye.controller.repl.PromptSession") as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ["exit"]
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            index_manager = MagicMock()
            index_manager.has_work.return_value = False
            index_manager.is_indexing.return_value = True
            index_manager.get_progress_display.return_value = "1/3"

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=plugin_manager,
                index_manager=index_manager,
                verbose=True,
                selected_model="model",
                use_rag=True,
            )

            repl.chat_repl(conf)

        # The prompt should be overridden when indexing and verbose
        assert session.prompt.call_args.args[0] == "(ツ (1/3) » "


def test_chat_repl_diff_without_args_prints_error_and_continues():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.rprint") as mock_rprint,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["diff", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        mock_rprint.assert_any_call("[red]Error:[/] No file specified for diff.")


def test_chat_repl_restore_with_file_arg_sets_has_used_restore():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.commands") as mock_commands,
        patch("aye.controller.repl.cli_ui") as mock_cli_ui,
        patch("aye.controller.repl.set_user_config") as mock_set_user_config,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["restore 001 file.py", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        mock_commands.restore_from_snapshot.assert_called_once_with("001", "file.py")
        mock_cli_ui.print_restore_feedback.assert_called_once_with("001", "file.py")
        mock_set_user_config.assert_any_call("has_used_restore", "on")


def test_chat_repl_keep_without_arg_defaults_to_10():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.commands") as mock_commands,
        patch("aye.controller.repl.cli_ui") as mock_cli_ui,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["keep", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        mock_commands.prune_snapshots.return_value = ["001"]

        repl.chat_repl(conf)

        mock_commands.prune_snapshots.assert_called_once_with(10)
        mock_cli_ui.print_prune_feedback.assert_called_once_with(["001"], 10)


def test_chat_repl_keep_with_invalid_arg_shows_error():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.commands") as mock_commands,
        patch("aye.controller.repl.collect_and_send_feedback"),
        patch("aye.controller.repl.rprint") as mock_rprint,
    ):
        session = MagicMock()
        session.prompt.side_effect = ["keep abc", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        # prune_snapshots should NOT be called when input is invalid
        mock_commands.prune_snapshots.assert_not_called()

        # rprint should have been called with the error message
        mock_rprint.assert_any_call(
            "[red]Error:[/] 'abc' is not a valid number. Please provide a positive integer."
        )


def test_chat_repl_at_references_verbose_prints_and_llm_called_with_explicit_source_files():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.invoke_llm") as mock_invoke,
        patch("aye.controller.repl.process_llm_response", return_value=None),
        patch("aye.controller.repl.rprint") as mock_rprint,
        patch("aye.controller.repl.telemetry.record_llm_prompt") as mock_record_llm,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["explain @a.py", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        def plugin_side_effect(command, params):
            if command == "get_completer":
                return {"completer": None}
            if command == "execute_shell_command":
                return None
            if command == "parse_at_references":
                return {
                    "file_contents": {"a.py": "print('x')"},
                    "cleaned_prompt": "explain file",
                }
            return None

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = plugin_side_effect

        mock_invoke.return_value = SimpleNamespace(chat_id=None)

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=True,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        assert any("Including 1 file(s)" in str(c.args[0]) for c in mock_rprint.call_args_list)
        mock_record_llm.assert_any_call("LLM @")
        assert mock_invoke.call_count == 1
        assert mock_invoke.call_args.kwargs["prompt"] == "explain file"
        assert mock_invoke.call_args.kwargs["explicit_source_files"] == {"a.py": "print('x')"}


def test_chat_repl_at_references_error_falls_back_to_llm_without_explicit_files():
    with (
        patch("aye.controller.repl.PromptSession") as mock_session_cls,
        patch("aye.controller.repl.run_first_time_tutorial_if_needed", return_value=False),
        patch("aye.controller.repl._prompt_for_telemetry_consent_if_needed", return_value=False),
        patch("aye.controller.repl.print_startup_header"),
        patch("aye.controller.repl.print_prompt", return_value="> "),
        patch("aye.controller.repl.Path") as mock_path,
        patch("aye.controller.repl.invoke_llm") as mock_invoke,
        patch("aye.controller.repl.process_llm_response", return_value=None),
        patch("aye.controller.repl.telemetry.record_llm_prompt") as mock_record_llm,
        patch("aye.controller.repl.collect_and_send_feedback"),
    ):
        session = MagicMock()
        session.prompt.side_effect = ["explain @a.py", "exit"]
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        def plugin_side_effect(command, params):
            if command == "get_completer":
                return {"completer": None}
            if command == "execute_shell_command":
                return None
            if command == "parse_at_references":
                return {"error": "bad parse"}
            return None

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = plugin_side_effect

        mock_invoke.return_value = SimpleNamespace(chat_id=None)

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask="*.py",
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model="model",
            use_rag=True,
        )

        repl.chat_repl(conf)

        mock_record_llm.assert_any_call("LLM")
        assert mock_invoke.call_count == 1
        assert mock_invoke.call_args.kwargs["explicit_source_files"] is None
