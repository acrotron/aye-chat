import subprocess
from unittest import TestCase
from unittest.mock import patch, MagicMock

import aye.plugins.shell_executor

from aye.plugins.shell_executor import ShellExecutorPlugin

class TestShellExecutorPlugin(TestCase):
    def setUp(self):
        self.plugin = ShellExecutorPlugin()
        self.plugin.init({})

    @patch('aye.plugins.shell_executor.rprint')
    def test_init_verbose(self, mock_rprint):
        self.plugin.init({"verbose": True})
        self.assertTrue(self.plugin.verbose)

    @patch('shutil.which', return_value='/bin/ls')
    def test_is_valid_command_exists(self, mock_which):
        self.assertTrue(self.plugin._is_valid_command('ls'))
        mock_which.assert_called_with('ls')

    @patch('shutil.which', return_value=None)
    @patch('subprocess.run')
    def test_is_valid_command_not_exists(self, mock_run, mock_which):
        # Simulate 'command -v' failing on non-windows
        with patch.object(self.plugin, '_is_windows', return_value=False):
            mock_run.side_effect = subprocess.CalledProcessError(1, 'command -v')
            self.assertFalse(self.plugin._is_valid_command('nonexistentcommand'))
            mock_which.assert_called_with('nonexistentcommand')
            mock_run.assert_called_once()

    @patch('shutil.which', return_value=None)
    @patch('subprocess.run')
    def test_is_valid_command_windows(self, mock_run, mock_which):
        with patch.object(self.plugin, '_is_windows', return_value=True):
            # Command is not found
            mock_run.return_value = MagicMock(stderr="is not recognized")
            self.assertFalse(self.plugin._is_valid_command('badcmd'))
            # Command is found (no 'not recognized' error)
            mock_run.return_value = MagicMock(stderr="")
            self.assertTrue(self.plugin._is_valid_command('goodcmd'))
            # Command check fails
            mock_run.side_effect = subprocess.TimeoutExpired('cmd', 1)
            self.assertFalse(self.plugin._is_valid_command('timeoutcmd'))

    def test_is_interactive(self):
        self.assertTrue(self.plugin._is_interactive('vim'))
        self.assertTrue(self.plugin._is_interactive('less'))
        self.assertFalse(self.plugin._is_interactive('ls'))
        self.assertFalse(self.plugin._is_interactive('echo'))

    @patch('os.system')
    def test_execute_interactive(self, mock_os_system):
        mock_os_system.return_value = 0
        result = self.plugin._execute_interactive('vim my_file.txt')
        mock_os_system.assert_called_once_with('vim my_file.txt')
        self.assertEqual(result['exit_code'], 0)

    @patch('os.system', side_effect=Exception("OS Error"))
    def test_execute_interactive_error(self, mock_os_system):
        result = self.plugin._execute_interactive('vim')
        self.assertIn("error", result)
        self.assertIn("Failed to run interactive command", result["error"])

    @patch('subprocess.run')
    def test_execute_non_interactive_success(self, mock_subprocess_run):
        mock_result = MagicMock()
        mock_result.stdout = "file.txt\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_subprocess_run.return_value = mock_result
        
        # On non-windows, it's called as a list
        with patch.object(self.plugin, '_is_windows', return_value=False):
            result = self.plugin._execute_non_interactive('ls', ['-l'])
            mock_subprocess_run.assert_called_with(['ls', '-l'], capture_output=True, text=True, check=True, shell=False)
            self.assertEqual(result['stdout'], "file.txt\n")
            self.assertEqual(result['returncode'], 0)

    @patch('subprocess.run')
    def test_execute_non_interactive_windows(self, mock_subprocess_run):
        with patch.object(self.plugin, '_is_windows', return_value=True):
            self.plugin._execute_non_interactive('dir', [])
            mock_subprocess_run.assert_called_with('dir ', capture_output=True, text=True, check=True, shell=True)

    @patch('subprocess.run')
    def test_execute_non_interactive_failure(self, mock_subprocess_run):
        # Simulate a CalledProcessError
        error = subprocess.CalledProcessError(
            returncode=1,
            cmd='ls',
            output='some output',
            stderr='ls: cannot access \'nonexistent\': No such file or directory'
        )
        # The mock needs to have stdout/stderr attributes for the except block
        error.stdout = 'some output'
        error.stderr = 'ls: cannot access \'nonexistent\': No such file or directory'
        mock_subprocess_run.side_effect = error
        
        with patch.object(self.plugin, '_is_windows', return_value=False):
            result = self.plugin._execute_non_interactive('ls', ['nonexistent'])
        
        self.assertIn('error', result)
        self.assertEqual(result['returncode'], 1)
        self.assertIn('No such file or directory', result['stderr'])

    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_execute_non_interactive_file_not_found(self, mock_run):
        result = self.plugin._execute_non_interactive('badcmd', [])
        self.assertIsNone(result)

    @patch.object(ShellExecutorPlugin, '_is_valid_command', return_value=True)
    @patch.object(ShellExecutorPlugin, '_execute_non_interactive')
    def test_on_command_non_interactive(self, mock_execute, mock_is_valid):
        mock_execute.return_value = {"stdout": "ok"}
        params = {"command": "echo", "args": ["hello"]}
        
        result = self.plugin.on_command("execute_shell_command", params)
        
        mock_is_valid.assert_called_once_with("echo")
        mock_execute.assert_called_once_with("echo", ["hello"])
        self.assertEqual(result, {"stdout": "ok"})

    @patch.object(ShellExecutorPlugin, '_is_valid_command', return_value=True)
    @patch.object(ShellExecutorPlugin, '_execute_interactive')
    def test_on_command_interactive(self, mock_execute, mock_is_valid):
        mock_execute.return_value = {"exit_code": 0}
        params = {"command": "vim", "args": []}
        
        result = self.plugin.on_command("execute_shell_command", params)
        
        mock_is_valid.assert_called_once_with("vim")
        mock_execute.assert_called_once_with("vim ")
        self.assertEqual(result, {"exit_code": 0})

    @patch.object(ShellExecutorPlugin, '_is_valid_command', return_value=False)
    def test_on_command_invalid_command(self, mock_is_valid):
        params = {"command": "invalidcmd", "args": []}
        result = self.plugin.on_command("execute_shell_command", params)
        self.assertIsNone(result)
        mock_is_valid.assert_called_once_with("invalidcmd")

    def test_on_command_not_shell_command(self):
        result = self.plugin.on_command("some_other_command", {})
        self.assertIsNone(result)

    def test_build_full_cmd_with_special_chars(self):
        with patch.object(self.plugin, '_is_windows', return_value=False):
            cmd = self.plugin._build_full_cmd('echo', ['hello world', 'its a test'])
            self.assertEqual(cmd, "echo 'hello world' 'its a test'")

        with patch.object(self.plugin, '_is_windows', return_value=True):
            cmd = self.plugin._build_full_cmd('echo', ['hello world', 'C:\\Users\\Test'])
            self.assertEqual(cmd, 'echo "hello world" C:\\Users\\Test')
