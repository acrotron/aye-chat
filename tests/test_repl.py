import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

import aye.controller.repl as repl
from aye.model.config import MODELS

class TestRepl(TestCase):
    def setUp(self):
        self.conf = SimpleNamespace(root=Path.cwd(), file_mask="*.py")
        self.session = MagicMock()

    @patch('os.chdir')
    @patch('aye.controller.repl.rprint')
    def test_handle_cd_command_success(self, mock_rprint, mock_chdir):
        target_dir = '/tmp'
        
        with patch('pathlib.Path.cwd', return_value=Path(target_dir)):
            result = repl.handle_cd_command(['cd', target_dir], self.conf)
        
        self.assertTrue(result)
        mock_chdir.assert_called_once_with(target_dir)
        self.assertEqual(self.conf.root, Path(target_dir))
        mock_rprint.assert_called_once_with(str(Path(target_dir)))

    @patch('os.chdir')
    @patch('aye.controller.repl.rprint')
    def test_handle_cd_command_home(self, mock_rprint, mock_chdir):
        home_dir = str(Path.home())
        with patch('pathlib.Path.cwd', return_value=Path(home_dir)):
            repl.handle_cd_command(['cd'], self.conf)
        mock_chdir.assert_called_once_with(home_dir)

    @patch('os.chdir', side_effect=FileNotFoundError("No such file or directory"))
    @patch('aye.controller.repl.print_error')
    def test_handle_cd_command_failure(self, mock_print_error, mock_chdir):
        result = repl.handle_cd_command(['cd', '/nonexistent'], self.conf)
        self.assertFalse(result)
        mock_chdir.assert_called_once_with('/nonexistent')
        mock_print_error.assert_called_once()

    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.set_user_config')
    def test_handle_model_command_list_and_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        self.session.prompt.return_value = "2" # Select the second model
        
        repl.handle_model_command(self.session, MODELS, self.conf, ['model'])
        
        self.session.prompt.assert_called_once()
        self.assertEqual(self.conf.selected_model, MODELS[1]['id'])
        mock_set_config.assert_called_once_with("selected_model", MODELS[1]['id'])
        # Check for the success message
        self.assertIn(f"Selected: {MODELS[1]['name']}", str(mock_rprint.call_args_list))

    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.set_user_config')
    def test_handle_model_command_direct_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        
        repl.handle_model_command(self.session, MODELS, self.conf, ['model', '3'])
        
        self.session.prompt.assert_not_called()
        self.assertEqual(self.conf.selected_model, MODELS[2]['id'])
        mock_set_config.assert_called_once_with("selected_model", MODELS[2]['id'])
        self.assertIn(f"Selected model: {MODELS[2]['name']}", str(mock_rprint.call_args_list))

    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.set_user_config')
    def test_handle_model_command_invalid_input(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        repl.handle_model_command(self.session, MODELS, self.conf, ['model', '99'])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid model number.[/]" )

        repl.handle_model_command(self.session, MODELS, self.conf, ['model', 'abc'])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid input. Use a number.[/]")

        self.session.prompt.return_value = "xyz"
        repl.handle_model_command(self.session, MODELS, self.conf, ['model'])
        mock_rprint.assert_any_call("[red]Invalid input.[/]")

    @patch('aye.controller.repl.rprint')
    @patch('aye.controller.repl.set_user_config')
    def test_handle_verbose_command(self, mock_set_config, mock_rprint):
        # Toggle on
        repl.handle_verbose_command(['verbose', 'on'])
        mock_set_config.assert_called_with('verbose', 'on')
        mock_rprint.assert_any_call("[green]Verbose mode set to On[/]")

        # Toggle off
        repl.handle_verbose_command(['verbose', 'off'])
        mock_set_config.assert_called_with('verbose', 'off')
        mock_rprint.assert_any_call("[green]Verbose mode set to Off[/]")

        # Invalid
        repl.handle_verbose_command(['verbose', 'invalid'])
        mock_rprint.assert_any_call("[red]Usage: verbose on|off[/]")

    @patch('aye.controller.repl.get_user_config', return_value='off')
    @patch('aye.controller.repl.rprint')
    def test_handle_verbose_command_status(self, mock_rprint, mock_get_config):
        repl.handle_verbose_command(['verbose'])
        mock_get_config.assert_called_with("verbose", "off")
        mock_rprint.assert_called_with("[yellow]Verbose mode is Off[/]")

    @patch('aye.controller.repl.rprint')
    def test_print_startup_header_unknown_model(self, mock_rprint):
        conf = SimpleNamespace(selected_model='unknown/model', file_mask='*.py')
        with patch('aye.controller.repl.set_user_config') as mock_set_config:
            repl.print_startup_header(conf)
            mock_set_config.assert_called_once_with("selected_model", repl.DEFAULT_MODEL_ID)
            self.assertEqual(conf.selected_model, repl.DEFAULT_MODEL_ID)

    @patch('aye.controller.repl.send_feedback')
    def test_collect_and_send_feedback(self, mock_send_feedback):
        with patch('prompt_toolkit.PromptSession.prompt', return_value="Great tool!"):
            repl.collect_and_send_feedback(chat_id=123)
            mock_send_feedback.assert_called_once_with("Great tool!", chat_id=123)

    @patch('aye.controller.repl.send_feedback')
    def test_collect_and_send_feedback_empty(self, mock_send_feedback):
        with patch('prompt_toolkit.PromptSession.prompt', return_value="  \n  "):
            repl.collect_and_send_feedback(chat_id=123)
            mock_send_feedback.assert_not_called()

    @patch('aye.controller.repl.send_feedback')
    def test_collect_and_send_feedback_ctrl_c(self, mock_send_feedback):
        with patch('prompt_toolkit.PromptSession.prompt', side_effect=KeyboardInterrupt):
            repl.collect_and_send_feedback(chat_id=123)
            mock_send_feedback.assert_not_called()

    @patch('aye.controller.repl.send_feedback', side_effect=Exception("API down"))
    @patch('aye.controller.repl.rprint')
    def test_collect_and_send_feedback_api_error(self, mock_rprint, mock_send_feedback):
        with patch('prompt_toolkit.PromptSession.prompt', return_value="feedback"):
            repl.collect_and_send_feedback(chat_id=123)
            mock_rprint.assert_any_call("\n[cyan]Goodbye![/cyan]")

    def test_chat_repl_main_loop_commands(self):
        # This test is rewritten to correctly mock dependencies and test the command dispatch logic.
        with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
             patch('aye.controller.repl.run_first_time_tutorial_if_needed'), \
             patch('aye.controller.repl.get_user_config', return_value="on"), \
             patch('aye.controller.repl.print_startup_header'), \
             patch('aye.controller.repl.Path') as mock_path, \
             patch('aye.controller.repl.handle_model_command') as mock_model_cmd, \
             patch('aye.controller.repl.commands') as mock_commands, \
             patch('aye.controller.repl.cli_ui') as mock_cli_ui, \
             patch('aye.controller.repl.diff_presenter') as mock_diff, \
             patch('aye.controller.repl.print_help_message') as mock_help, \
             patch('aye.controller.repl.invoke_llm') as mock_invoke, \
             patch('aye.controller.repl.process_llm_response', return_value=None) as mock_process, \
             patch('aye.controller.repl.collect_and_send_feedback'):

            mock_session = MagicMock()
            mock_session.prompt.side_effect = [
                'model',
                'history',
                'diff file.py 001',
                'restore 001',
                'keep 5',
                'new',
                'help',
                'ls -l',
                'a real prompt',
                'exit'
            ]
            mock_session_cls.return_value = mock_session

            # Mock chat_id file handling
            mock_chat_id_file = MagicMock()
            mock_chat_id_file.exists.return_value = False
            mock_path.return_value = mock_chat_id_file

            mock_commands.get_diff_paths.return_value = (Path('p1'), Path('p2'))

            mock_plugin_manager = MagicMock()
            mock_plugin_manager.handle_command.side_effect = [
                {"completer": None}, # get_completer on startup
                None,                # new_chat command
                {"stdout": "files"}, # execute_shell_command for 'ls -l'
                None,                # execute_shell_command for 'a real prompt' (fallback to LLM)
            ]

            mock_index_manager = MagicMock()
            mock_index_manager.has_work.return_value = False
            mock_index_manager.is_indexing.return_value = False

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask="*.py",
                plugin_manager=mock_plugin_manager,
                index_manager=mock_index_manager,
                verbose=True,
                selected_model='test-model'
            )

            repl.chat_repl(conf)

            # Assertions
            self.assertEqual(mock_session.prompt.call_count, 10)
            self.assertEqual(mock_model_cmd.call_count, 2) # Once on startup (verbose), once for 'model' command
            mock_commands.get_snapshot_history.assert_called_once()
            mock_commands.get_diff_paths.assert_called_once_with('file.py', '001', None)
            mock_diff.show_diff.assert_called_once()
            mock_commands.restore_from_snapshot.assert_called_once_with('001', None)
            mock_commands.prune_snapshots.assert_called_once_with(5)
            mock_chat_id_file.unlink.assert_called_once()
            self.assertEqual(mock_help.call_count, 2) # Once on startup (verbose), once for 'help' command

            expected_plugin_calls = [
                call('get_completer', {'commands': ['with', 'new', 'history', 'diff', 'restore', 'undo', 'keep', 'model', 'verbose', 'exit', 'quit', ':q', 'help', 'cd']}),
                call('new_chat', {'root': conf.root}),
                call('execute_shell_command', {'command': 'ls', 'args': ['-l']}),
                call('execute_shell_command', {'command': 'a', 'args': ['real', 'prompt']})
            ]
            self.assertEqual(mock_plugin_manager.handle_command.call_args_list, expected_plugin_calls)

            mock_invoke.assert_called_once()
            mock_process.assert_called_once()
