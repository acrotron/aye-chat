import os
from unittest import TestCase
from unittest.mock import patch, MagicMock
from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completion

import aye.plugins.completer

from aye.plugins.completer import CompleterPlugin, CmdPathCompleter

class TestCompleterPlugin(TestCase):
    def setUp(self):
        self.plugin = CompleterPlugin()
        self.plugin.init({})

    def test_on_command_get_completer(self):
        params = {"commands": ["help", "exit"]}
        result = self.plugin.on_command("get_completer", params)
        self.assertIn("completer", result)
        self.assertIsInstance(result["completer"], CmdPathCompleter)
        # Check if the custom commands were passed
        self.assertIn("help", result["completer"].commands)

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
        mock_isdir.return_value = True # Assume '/user' is a directory

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

    @patch('os.environ.get', return_value='/bin:/usr/bin:/unreadable')
    @patch('os.path.isdir', side_effect=lambda p: p != '/unreadable')
    @patch('os.listdir', side_effect=lambda p: ['ls'] if p == '/bin' else ['grep'] if p == '/usr/bin' else OSError('Permission denied'))
    @patch('os.path.isfile', return_value=True)
    @patch('os.access', return_value=True)
    def test_get_system_commands_unreadable_dir(self, mock_access, mock_isfile, mock_listdir, mock_isdir, mock_env_get):
        completer = CmdPathCompleter()
        commands = completer._get_system_commands()
        self.assertIn('ls', commands)
        self.assertIn('grep', commands)
        self.assertEqual(len(commands), 2)
