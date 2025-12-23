import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock, call

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

    @patch('os.chdir')
    @patch('aye.controller.command_handlers.rprint')
    def test_handle_cd_command_success(self, mock_rprint, mock_chdir):
        target_dir = '/tmp'

        with patch('pathlib.Path.cwd', return_value=Path(target_dir)):
            result = repl.handle_cd_command(['cd', target_dir], self.conf)

        self.assertTrue(result)
        mock_chdir.assert_called_once_with(target_dir)
        self.assertEqual(self.conf.root, Path(target_dir))
        mock_rprint.assert_called_once_with(str(Path(target_dir)))

    @patch('os.chdir')
    @patch('aye.controller.command_handlers.rprint')
    def test_handle_cd_command_home(self, mock_rprint, mock_chdir):
        home_dir = str(Path.home())
        with patch('pathlib.Path.cwd', return_value=Path(home_dir)):
            repl.handle_cd_command(['cd'], self.conf)
        mock_chdir.assert_called_once_with(home_dir)

    @patch('os.chdir', side_effect=FileNotFoundError("No such file or directory"))
    @patch('aye.controller.command_handlers.print_error')
    def test_handle_cd_command_failure(self, mock_print_error, mock_chdir):
        result = repl.handle_cd_command(['cd', '/nonexistent'], self.conf)
        self.assertFalse(result)
        mock_chdir.assert_called_once_with('/nonexistent')
        mock_print_error.assert_called_once()

    @patch('aye.controller.command_handlers.rprint')
    @patch('aye.controller.command_handlers.set_user_config')
    def test_handle_model_command_list_and_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None
        self.session.prompt.return_value = "2"

        repl.handle_model_command(self.session, MODELS, self.conf, ['model'])

        self.session.prompt.assert_called_once()
        self.assertEqual(self.conf.selected_model, MODELS[1]['id'])
        mock_set_config.assert_called_once_with("selected_model", MODELS[1]['id'])
        self.assertIn(f"Selected: {MODELS[1]['name']}", str(mock_rprint.call_args_list))

    @patch('aye.controller.command_handlers.rprint')
    @patch('aye.controller.command_handlers.set_user_config')
    def test_handle_model_command_direct_select(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None

        repl.handle_model_command(self.session, MODELS, self.conf, ['model', '3'])

        self.session.prompt.assert_not_called()
        self.assertEqual(self.conf.selected_model, MODELS[2]['id'])
        mock_set_config.assert_called_once_with("selected_model", MODELS[2]['id'])
        self.assertIn(f"Selected model: {MODELS[2]['name']}", str(mock_rprint.call_args_list))

    @patch('aye.controller.command_handlers.rprint')
    @patch('aye.controller.command_handlers.set_user_config')
    def test_handle_model_command_invalid_input(self, mock_set_config, mock_rprint):
        self.conf.selected_model = MODELS[0]['id']
        self.conf.plugin_manager = MagicMock()
        self.conf.plugin_manager.handle_command.return_value = None

        repl.handle_model_command(self.session, MODELS, self.conf, ['model', '99'])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid model number.[/]")

        repl.handle_model_command(self.session, MODELS, self.conf, ['model', 'abc'])
        mock_set_config.assert_not_called()
        mock_rprint.assert_any_call("[red]Invalid input. Use a number.[/]")

        self.session.prompt.return_value = "xyz"
        repl.handle_model_command(self.session, MODELS, self.conf, ['model'])
        mock_rprint.assert_any_call("[red]Invalid input.[/]")

    @patch('aye.controller.command_handlers.rprint')
    @patch('aye.controller.command_handlers.set_user_config')
    def test_handle_verbose_command(self, mock_set_config, mock_rprint):
        repl.handle_verbose_command(['verbose', 'on'])
        mock_set_config.assert_called_with('verbose', 'on')
        mock_rprint.assert_any_call("[green]Verbose mode set to On[/]")

        repl.handle_verbose_command(['verbose', 'off'])
        mock_set_config.assert_called_with('verbose', 'off')
        mock_rprint.assert_any_call("[green]Verbose mode set to Off[/]")

        repl.handle_verbose_command(['verbose', 'invalid'])
        mock_rprint.assert_any_call("[red]Usage: verbose on|off[/]")

    @patch('aye.controller.command_handlers.get_user_config', return_value='off')
    @patch('aye.controller.command_handlers.rprint')
    def test_handle_verbose_command_status(self, mock_rprint, mock_get_config):
        repl.handle_verbose_command(['verbose'])
        mock_get_config.assert_called_with("verbose", "off")
        mock_rprint.assert_called_with("[yellow]Verbose mode is Off[/]")

    @patch('aye.controller.repl.print_welcome_message')
    @patch('aye.controller.repl.rprint')
    def test_print_startup_header_known_model(self, mock_rprint, mock_welcome):
        conf = SimpleNamespace(selected_model=MODELS[0]['id'], file_mask='*.*')
        repl.print_startup_header(conf)
        mock_rprint.assert_any_call(f"[bold cyan]Session context: {conf.file_mask}[/]")
        mock_rprint.assert_any_call(f"[bold cyan]Current model: {MODELS[0]['name']}[/]")
        mock_welcome.assert_called_once()

    @patch('aye.controller.repl.rprint')
    def test_print_startup_header_unknown_model(self, mock_rprint):
        conf = SimpleNamespace(selected_model='unknown/model', file_mask='*.py')
        with patch('aye.controller.repl.set_user_config') as mock_set_config:
            repl.print_startup_header(conf)
            mock_set_config.assert_called_once_with("selected_model", repl.DEFAULT_MODEL_ID)
            self.assertEqual(conf.selected_model, repl.DEFAULT_MODEL_ID)

    @patch('aye.controller.repl.send_feedback')
    @patch('aye.controller.repl.PromptSession')
    def test_collect_and_send_feedback(self, mock_session_cls, mock_send_feedback):
        mock_session_cls.return_value.prompt.return_value = "Great tool!"
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_called_once_with("Great tool!", chat_id=123, telemetry=None)

    @patch('aye.controller.repl.send_feedback')
    @patch('aye.controller.repl.PromptSession')
    def test_collect_and_send_feedback_empty(self, mock_session_cls, mock_send_feedback):
        # Telemetry disabled in setUp, so empty feedback should not send.
        mock_session_cls.return_value.prompt.return_value = "  \n  "
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_not_called()

    @patch('aye.controller.repl.send_feedback')
    @patch('aye.controller.repl.PromptSession')
    def test_collect_and_send_feedback_ctrl_c(self, mock_session_cls, mock_send_feedback):
        # Telemetry disabled in setUp, so Ctrl+C with no feedback should not send.
        mock_session_cls.return_value.prompt.side_effect = KeyboardInterrupt
        repl.collect_and_send_feedback(chat_id=123)
        mock_send_feedback.assert_not_called()

    @patch('aye.controller.repl.send_feedback', side_effect=Exception("API down"))
    @patch('aye.controller.repl.PromptSession')
    def test_collect_and_send_feedback_api_error(self, mock_session_cls, mock_send_feedback):
        # New implementation does not swallow send_feedback exceptions.
        mock_session_cls.return_value.prompt.return_value = "feedback"
        with self.assertRaises(Exception):
            repl.collect_and_send_feedback(chat_id=123)

    def test_chat_repl_main_loop_commands(self):
        with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
             patch('aye.controller.repl.run_first_time_tutorial_if_needed'), \
             patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
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

            mock_chat_id_file = MagicMock()
            mock_chat_id_file.exists.return_value = False
            mock_path.return_value = mock_chat_id_file

            # Return 3-tuple: (path1, path2, is_stash)
            mock_commands.get_diff_paths.return_value = (Path('p1'), 'p2', False)

            # Use a function to handle different command calls dynamically
            def plugin_side_effect(command, params):
                if command == 'get_completer':
                    return {"completer": None}
                elif command == 'new_chat':
                    return None
                elif command == 'execute_shell_command':
                    cmd = params.get('command', '')
                    if cmd == 'ls':
                        return {"stdout": "files"}
                    # 'a' is not a real shell command, return None to trigger LLM
                    return None
                elif command == 'parse_at_references':
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
                selected_model='test-model'
            )

            repl.chat_repl(conf)

            self.assertEqual(mock_session.prompt.call_count, 10)
            self.assertEqual(mock_model_cmd.call_count, 2)
            mock_commands.get_snapshot_history.assert_called_once()
            mock_commands.get_diff_paths.assert_called_once_with('file.py', '001', None)
            mock_diff.show_diff.assert_called_once_with(Path('p1'), 'p2', is_stash_ref=False)
            mock_commands.restore_from_snapshot.assert_called_once_with('001', None)
            mock_commands.prune_snapshots.assert_called_once_with(5)
            mock_chat_id_file.unlink.assert_called_once()
            self.assertEqual(mock_help.call_count, 2)

            # Verify specific plugin calls were made
            call_commands = [c[0][0] for c in mock_plugin_manager.handle_command.call_args_list]
            self.assertIn('get_completer', call_commands)
            self.assertIn('new_chat', call_commands)
            self.assertIn('execute_shell_command', call_commands)
            self.assertIn('parse_at_references', call_commands)

            mock_invoke.assert_called_once()
            mock_process.assert_called_once()


def test_create_key_bindings_registers_two_enter_bindings():
    bindings = repl.create_key_bindings()
    enter_bindings = bindings.get_bindings_for_keys((Keys.Enter,))

    # Two Enter bindings are expected:
    # - completion_is_selected
    # - has_completions & ~completion_is_selected
    assert len(enter_bindings) == 2
    assert any(b.filter == completion_is_selected for b in enter_bindings)
    assert any(b.filter != completion_is_selected for b in enter_bindings)


def test_create_prompt_session_uses_multicolumn_and_key_bindings():
    completer = MagicMock()

    with patch('aye.controller.repl.PromptSession') as mock_session_cls:
        repl.create_prompt_session(completer=completer, completion_style='readline')

        assert mock_session_cls.call_count == 1
        kwargs = mock_session_cls.call_args.kwargs
        assert kwargs['completer'] is completer
        assert kwargs['complete_style'] == repl.CompleteStyle.MULTI_COLUMN
        assert kwargs['complete_while_typing'] is True
        assert kwargs['key_bindings'] is not None


def test_chat_repl_starts_background_indexing_when_has_work():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.threading.Thread') as mock_thread_cls, \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.print_help_message'), \
         patch('aye.controller.repl.collect_and_send_feedback'):

        session = MagicMock()
        session.prompt.side_effect = ['exit']
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
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model'
        )

        repl.chat_repl(conf)

        mock_thread_cls.assert_called_once_with(target=index_manager.run_sync_in_background, daemon=True)
        thread_instance.start.assert_called_once()


def test_chat_repl_shell_command_outputs_error_info():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.collect_and_send_feedback'), \
         patch('aye.controller.repl.rprint') as mock_rprint:

        session = MagicMock()
        session.prompt.side_effect = ['ls', 'exit']
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [
            {"completer": None},
            {"stdout": "files", "stderr": "warn", "error": "bad"}
        ]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model'
        )

        repl.chat_repl(conf)

        outputs = [args[0] for args, _ in [(call.args, call.kwargs) for call in mock_rprint.call_args_list]]
        assert "files" in outputs
        assert "[yellow]warn[/]" in outputs
        assert "[red]Error:[/] bad" in outputs


def test_chat_repl_db_command_with_collection_sample():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.collect_and_send_feedback'), \
         patch('aye.controller.repl.rprint') as mock_rprint:

        session = MagicMock()
        session.prompt.side_effect = ['/db', 'exit']
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        long_doc = "A" * 80 + "\nmore text"
        collection = MagicMock()
        collection.name = 'test'
        collection.count.return_value = 2
        collection.peek.return_value = {
            'ids': ['1'],
            'metadatas': [{'foo': 'bar'}],
            'documents': [long_doc]
        }

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False
        index_manager.collection = collection

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model',
            use_rag=True,
        )

        repl.chat_repl(conf)

        printed = [call.args[0] for call in mock_rprint.call_args_list]
        assert any("Vector DB Status" in line for line in printed)
        assert any("Sample of up to 5 records" in line for line in printed)
        assert any(line.endswith('..."') for line in printed if line.strip().startswith('[yellow]Content'))


def test_chat_repl_handles_tokenization_error():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.print_error') as mock_print_error, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        session = MagicMock()
        session.prompt.side_effect = ['bad "input', 'exit']
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model'
        )

        repl.chat_repl(conf)

        mock_print_error.assert_called_once()
        assert isinstance(mock_print_error.call_args[0][0], ValueError)


def test_chat_repl_handles_exception_and_calls_error_handler():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.handle_llm_error') as mock_handle_error, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        session = MagicMock()
        session.prompt.side_effect = ['ls', 'exit']
        mock_session_cls.return_value = session

        _setup_mock_chat_id_path(mock_path)

        def plugin_side_effect(command, *_args, **_kwargs):
            if command == 'get_completer':
                return {"completer": None}
            raise RuntimeError('boom')

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = plugin_side_effect

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model'
        )

        repl.chat_repl(conf)

        mock_handle_error.assert_called_once()


def test_chat_repl_invalid_chat_id_file_is_cleaned():
    with patch('aye.controller.repl.PromptSession') as mock_session_cls, \
         patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        session = MagicMock()
        session.prompt.side_effect = ['exit']
        mock_session_cls.return_value = session

        chat_id_file = _setup_mock_chat_id_path(mock_path, exists=True, contents='abc')

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        index_manager = MagicMock()
        index_manager.has_work.return_value = False
        index_manager.is_indexing.return_value = False

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=index_manager,
            verbose=False,
            selected_model='model'
        )

        repl.chat_repl(conf)

        chat_id_file.unlink.assert_called_once_with(missing_ok=True)


def test_chat_repl_with_command_updates_chat_id_and_feedback_receives_it():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.create_prompt_session') as mock_create_session, \
         patch('aye.controller.repl.Console') as mock_console_cls, \
         patch('aye.controller.repl.handle_with_command', return_value=42) as mock_with, \
         patch('aye.controller.repl.collect_and_send_feedback') as mock_feedback:

        session = MagicMock()
        session.prompt.side_effect = ['with foo: bar', 'exit']
        mock_create_session.return_value = session

        _setup_mock_chat_id_path(mock_path, exists=False)

        plugin_manager = MagicMock()
        plugin_manager.handle_command.side_effect = [{"completer": None}]

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model='model',
            use_rag=True,
        )

        repl.chat_repl(conf)

        assert mock_with.call_count == 1
        # collect_and_send_feedback(max(0, chat_id)) should see 42
        mock_feedback.assert_called_once_with(42)


def test_chat_repl_completion_command_recreates_completer_and_session():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.handle_completion_command', return_value='multi') as mock_completion, \
         patch('aye.controller.repl.create_prompt_session') as mock_create_session, \
         patch('aye.controller.repl.rprint') as mock_rprint, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        session = MagicMock()
        session.prompt.side_effect = ['completion multi', 'exit']
        mock_create_session.return_value = session

        _setup_mock_chat_id_path(mock_path)

        plugin_manager = MagicMock()

        def plugin_side_effect(command, params):
            if command == 'get_completer':
                return {"completer": None}
            return None

        plugin_manager.handle_command.side_effect = plugin_side_effect

        conf = SimpleNamespace(
            root=Path.cwd(),
            file_mask='*.py',
            plugin_manager=plugin_manager,
            index_manager=None,
            verbose=False,
            selected_model='model',
            use_rag=True,
        )

        repl.chat_repl(conf)

        # One session created at startup, one recreated after completion command
        assert mock_create_session.call_count == 2
        assert mock_completion.call_count == 1
        assert any("Completion style is now active" in str(c.args[0]) for c in mock_rprint.call_args_list)

        # get_completer should be called twice (startup + after completion style change)
        get_completer_calls = [c for c in plugin_manager.handle_command.call_args_list if c.args and c.args[0] == 'get_completer']
        assert len(get_completer_calls) == 2


def test_chat_repl_model_number_shortcut_calls_model_handler():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.handle_model_command') as mock_model_cmd, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        # Use real create_prompt_session path via patched PromptSession
        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['2', 'exit']
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model='model',
                use_rag=True,
            )

            repl.chat_repl(conf)

        mock_model_cmd.assert_called_once()
        args = mock_model_cmd.call_args.args
        assert args[3] == ['model', '2']


def test_chat_repl_slash_prefixed_shell_command_executes_normalized_command():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.rprint') as mock_rprint, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['/ls -l', 'exit']
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            def plugin_side_effect(command, params):
                if command == 'get_completer':
                    return {"completer": None}
                if command == 'execute_shell_command':
                    assert params['command'] == 'ls'
                    assert params['args'] == ['-l']
                    return {"stdout": "ok"}
                return None

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = plugin_side_effect

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model='model',
                use_rag=True,
            )

            repl.chat_repl(conf)

        mock_rprint.assert_any_call('ok')


def test_chat_repl_reads_valid_chat_id_file_passes_to_llm_and_updates_chat_id():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.invoke_llm') as mock_invoke, \
         patch('aye.controller.repl.process_llm_response', return_value=9) as mock_process, \
         patch('aye.controller.repl.collect_and_send_feedback') as mock_feedback:

        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['hello', 'exit']
            mock_session_cls.return_value = session

            chat_id_file = _setup_mock_chat_id_path(mock_path, exists=True, contents='7')

            def plugin_side_effect(command, params):
                if command == 'get_completer':
                    return {"completer": None}
                if command == 'execute_shell_command':
                    return None
                if command == 'parse_at_references':
                    return None
                return None

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = plugin_side_effect

            # LLM response object with chat_id; process_llm_response should receive chat_id_file
            mock_invoke.return_value = SimpleNamespace(chat_id=9)

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model='model',
                use_rag=True,
            )

            repl.chat_repl(conf)

        # First LLM invocation should receive chat_id from file
        assert mock_invoke.call_count == 1
        assert mock_invoke.call_args.kwargs['chat_id'] == 7

        # process_llm_response should have been passed a chat_id_file (since llm_response.chat_id truthy)
        assert mock_process.call_count == 1
        assert mock_process.call_args.kwargs['chat_id_file'] is chat_id_file

        # Final feedback should be called with updated chat_id (9)
        mock_feedback.assert_called_once_with(9)


def test_chat_repl_db_command_no_index_manager_rag_disabled_prints_small_project_mode():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.rprint') as mock_rprint, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['db', 'exit']
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=None,
                verbose=False,
                selected_model='model',
                use_rag=False,
            )

            repl.chat_repl(conf)

        mock_rprint.assert_any_call('[yellow]Small project mode: RAG indexing is disabled.[/yellow]')


def test_chat_repl_always_shuts_down_index_manager_in_finally():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.collect_and_send_feedback'):

        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['exit']
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            index_manager = MagicMock()
            index_manager.has_work.return_value = False
            index_manager.is_indexing.return_value = False

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=index_manager,
                verbose=False,
                selected_model='model',
                use_rag=True,
            )

            repl.chat_repl(conf)

        index_manager.shutdown.assert_called_once()


def test_chat_repl_when_indexing_and_verbose_prefixes_prompt_with_progress():
    with patch('aye.controller.repl.run_first_time_tutorial_if_needed', return_value=False), \
         patch('aye.controller.repl._prompt_for_telemetry_consent_if_needed', return_value=False), \
         patch('aye.controller.repl.print_startup_header'), \
         patch('aye.controller.repl.print_prompt', return_value='> '), \
         patch('aye.controller.repl.Path') as mock_path, \
         patch('aye.controller.repl.handle_model_command'), \
         patch('aye.controller.repl.print_help_message'), \
         patch('aye.controller.repl.collect_and_send_feedback'):

        with patch('aye.controller.repl.PromptSession') as mock_session_cls:
            session = MagicMock()
            session.prompt.side_effect = ['exit']
            mock_session_cls.return_value = session

            _setup_mock_chat_id_path(mock_path)

            plugin_manager = MagicMock()
            plugin_manager.handle_command.side_effect = [{"completer": None}]

            index_manager = MagicMock()
            index_manager.has_work.return_value = False
            index_manager.is_indexing.return_value = True
            index_manager.get_progress_display.return_value = '1/3'

            conf = SimpleNamespace(
                root=Path.cwd(),
                file_mask='*.py',
                plugin_manager=plugin_manager,
                index_manager=index_manager,
                verbose=True,
                selected_model='model',
                use_rag=True,
            )

            repl.chat_repl(conf)

        # The prompt should be overridden when indexing and verbose
        assert session.prompt.call_args.args[0] == '(ツ (1/3) » '
