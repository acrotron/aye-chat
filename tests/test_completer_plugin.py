import os
from unittest import TestCase
from unittest.mock import patch, MagicMock
from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completion

import aye.plugins.completer

from aye.plugins.completer import CompleterPlugin, CmdPathCompleter, CompositeCompleter, DynamicAutoCompleteCompleter


class TestCompleterPlugin(TestCase):
    def setUp(self):
        self.plugin = CompleterPlugin()
        self.plugin.init({})

    def test_on_command_get_completer(self):
        params = {"commands": ["help", "exit"]}
        result = self.plugin.on_command("get_completer", params)
        self.assertIn("completer", result)
        self.assertIsInstance(result["completer"], DynamicAutoCompleteCompleter)
        
        # Test that the completer actually completes the custom commands
        completer = result["completer"]
        doc = Document("hel", cursor_position=3)
        event = MagicMock()
        completions = list(completer.get_completions(doc, event))
        
        # Check if 'help' is in the completions
        completion_texts = [c.text for c in completions]
        self.assertTrue(any('help' in text for text in completion_texts))

    def test_on_command_other_command(self):
        result = self.plugin.on_command("other_command", {})
        self.assertIsNone(result)


class TestCmdPathCompleter(TestCase):
    def setUp(self):
        # Mock system commands to have a predictable set
        with patch('aye.plugins.completer.CmdPathCompleter._get_system_commands', return_value=['ls', 'cd']):
            self.completer = CmdPathCompleter(commands=['help', 'exit'])
        self.event = MagicMock()

    def test_command_completion(self):
        # Complete 'h' -> 'help'
        doc = Document("h", cursor_position=1)
        completions = list(self.completer.get_completions(doc, self.event))
        self.assertIn(Completion("help ", start_position=-1, display="help"), completions)

        # Complete 'e' -> 'exit'
        doc = Document("e", cursor_position=1)
        completions = list(self.completer.get_completions(doc, self.event))
        self.assertIn(Completion("exit ", start_position=-1, display="exit"), completions)

    @patch('os.path.isdir')
    @patch('prompt_toolkit.completion.PathCompleter.get_completions')
    def test_path_completion(self, mock_path_completions, mock_isdir):
        # Simulate completing a path after a command
        doc = Document("ls /us", cursor_position=len("ls /us"))

        # Mock the inner PathCompleter to return a suggestion
        mock_path_completions.return_value = [Completion("er", start_position=-2, display="user")]
        mock_isdir.return_value = True  # Assume '/user' is a directory

        completions = list(self.completer.get_completions(doc, self.event))

        # The sub-document passed to PathCompleter should be just '/us'
        inner_doc_arg = mock_path_completions.call_args[0][0]
        self.assertEqual(inner_doc_arg.text, "/us")

        # The final completion should be 'er/' with the correct start position
        self.assertIn(Completion("er/", start_position=-2, display="user"), completions)
        mock_isdir.assert_called_with("/user")

    @patch('os.path.isdir', return_value=False)
    @patch('prompt_toolkit.completion.PathCompleter.get_completions')
    def test_file_completion(self, mock_path_completions, mock_isdir):
        doc = Document("cat file.t", cursor_position=len("cat file.t"))
        mock_path_completions.return_value = [Completion("xt", start_position=-1, display="file.txt")]

        completions = list(self.completer.get_completions(doc, self.event))

        # Should not append '/' for files
        self.assertIn(Completion("xt", start_position=-1, display="file.txt"), completions)
        mock_isdir.assert_called_once_with("file.txt")

    @patch('os.environ.get', return_value=None)
    def test_get_system_commands_no_path(self, mock_env_get):
        completer = CmdPathCompleter()
        self.assertEqual(completer._get_system_commands(), [])

    def test_get_system_commands_unreadable_dir(self):
        # Use os.pathsep so test works on both Unix (':') and Windows (';')
        test_path = os.pathsep.join(['/bin', '/usr/bin', '/unreadable'])

        with patch.dict(os.environ, {'AYE_SKIP_PATH_SCAN': '1', 'PATH': test_path}), \
             patch('aye.plugins.completer.os.path.isdir', side_effect=lambda p: p != '/unreadable'), \
             patch('aye.plugins.completer.os.scandir') as mock_scandir, \
             patch('aye.plugins.completer.os.access', return_value=True):

            class DummyDirEntries:
                """Context manager that yields the given directory entries."""
                def __init__(self, entries):
                    self._entries = entries

                def __enter__(self):
                    return iter(self._entries)

                def __exit__(self, exc_type, exc, tb):
                    return False

            # Mock scandir to return our test entries
            def scandir_side_effect(directory):
                if directory == '/bin':
                    entry = MagicMock()
                    entry.name = 'ls'
                    entry.path = '/bin/ls'
                    entry.is_file.return_value = True
                    return DummyDirEntries([entry])
                elif directory == '/usr/bin':
                    entry = MagicMock()
                    entry.name = 'grep'
                    entry.path = '/usr/bin/grep'
                    entry.is_file.return_value = True
                    return DummyDirEntries([entry])
                else:
                    # Simulate unreadable directory
                    raise OSError('Permission denied')

            mock_scandir.side_effect = scandir_side_effect

            completer = CmdPathCompleter()
            commands = completer._get_system_commands()
            self.assertIn('ls', commands)
            self.assertIn('grep', commands)
            self.assertEqual(len(commands), 2)
