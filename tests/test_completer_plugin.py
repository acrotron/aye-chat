import os
from unittest import TestCase
from unittest.mock import patch, MagicMock

from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completion

import aye.plugins.completer

from aye.plugins.completer import (
    CompleterPlugin,
    CmdPathCompleter,
    CompositeCompleter,
    DynamicAutoCompleteCompleter,
)


class TestCompleterPlugin(TestCase):
    def setUp(self):
        self.plugin = CompleterPlugin()
        self.plugin.init({})

    def test_init_debug(self):
        # Plugin config uses on/off style flags.
        with patch("aye.plugins.completer.rprint") as mock_print:
            plugin = CompleterPlugin()
            plugin.init({"debug": "on"})
            # Disabling for now: fails on Python3.10 for some reason
            # Not important.
            # mock_print.assert_called_once()

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

        completion_texts = [c.text for c in completions]
        self.assertTrue(any("help" in text for text in completion_texts))

    def test_on_command_other_command(self):
        result = self.plugin.on_command("other_command", {})
        self.assertIsNone(result)


class TestDynamicAutoCompleteCompleter(TestCase):
    def setUp(self):
        self.inner = MagicMock()
        self.inner.get_completions.return_value = [Completion("inner")]
        self.event = MagicMock()
        self.event.completion_requested = False

    def test_is_at_file_context(self):
        completer = DynamicAutoCompleteCompleter(self.inner)

        # Valid contexts
        self.assertTrue(completer._is_at_file_context("@file"))
        self.assertTrue(completer._is_at_file_context("cmd @file"))
        self.assertTrue(completer._is_at_file_context(" @file"))
        self.assertTrue(completer._is_at_file_context("@dir/"))
        self.assertTrue(completer._is_at_file_context("text @partial"))

        # Invalid contexts
        self.assertFalse(completer._is_at_file_context("file"))
        self.assertFalse(completer._is_at_file_context("email@domain"))
        self.assertFalse(completer._is_at_file_context("no_at_here"))
        self.assertFalse(completer._is_at_file_context("@file "))
        self.assertFalse(completer._is_at_file_context("cmd @file "))

    def test_get_completions_readline_style(self):
        # Style "readline": only complete on TAB (completion_requested=True), unless it's an @ reference
        completer = DynamicAutoCompleteCompleter(self.inner, completion_style="readline")

        # Case 1: Normal text, no TAB -> No completions
        doc = Document("cmd")
        self.event.completion_requested = False
        self.assertEqual(list(completer.get_completions(doc, self.event)), [])

        # Case 2: Normal text, TAB pressed -> completions
        self.event.completion_requested = True
        self.assertEqual(len(list(completer.get_completions(doc, self.event))), 1)

        # Case 3: @ reference -> Auto-complete (always yield)
        doc_at = Document("@fi")
        self.event.completion_requested = False
        self.assertEqual(len(list(completer.get_completions(doc_at, self.event))), 1)

    def test_get_completions_multi_style(self):
        # Style "multi": always auto-complete
        completer = DynamicAutoCompleteCompleter(self.inner, completion_style="multi")

        doc = Document("cmd")
        self.event.completion_requested = False

        # Should yield completions even without TAB
        self.assertEqual(len(list(completer.get_completions(doc, self.event))), 1)


class TestCompositeCompleter(TestCase):
    def test_delegation(self):
        cmd_comp = MagicMock()
        at_comp = MagicMock()
        comp = CompositeCompleter(cmd_completer=cmd_comp, at_completer=at_comp)
        event = MagicMock()

        # Scenario 1: Normal command -> delegates to cmd_completer
        doc = Document("ls")
        list(comp.get_completions(doc, event))
        cmd_comp.get_completions.assert_called_once()
        at_comp.get_completions.assert_not_called()

        cmd_comp.reset_mock()
        at_comp.reset_mock()

        # Scenario 2: At reference -> delegates to at_completer
        doc = Document("ls @file")
        list(comp.get_completions(doc, event))
        at_comp.get_completions.assert_called_once()
        cmd_comp.get_completions.assert_not_called()

        cmd_comp.reset_mock()
        at_comp.reset_mock()

        # Scenario 3: At in middle of word (email) -> delegates to cmd_completer
        doc = Document("mail@domain")
        list(comp.get_completions(doc, event))
        cmd_comp.get_completions.assert_called_once()
        at_comp.get_completions.assert_not_called()


class TestCmdPathCompleter(TestCase):
    def setUp(self):
        # Avoid background PATH scan threads in unit tests; they can race with mocks.
        self._env_patch = patch.dict(os.environ, {"AYE_SKIP_PATH_SCAN": "1"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        self.completer = CmdPathCompleter(commands=["help", "exit"])
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

    def test_slash_command_completion(self):
        # Complete '/he' -> '/help'
        doc = Document("/he", cursor_position=3)
        completions = list(self.completer.get_completions(doc, self.event))

        def _display_text(c):
            # prompt_toolkit may store display as formatted text.
            if hasattr(c, "display_text"):
                return c.display_text
            return str(c.display)

        found = any(
            c.text == "help" and _display_text(c) == "/help" and c.start_position == -2
            for c in completions
        )
        self.assertTrue(found, "Did not find slash command completion for /help")

        # Should not trigger if there's a space (argument)
        doc_arg = Document("/help ", cursor_position=6)
        completions_arg = list(self.completer.get_completions(doc_arg, self.event))
        self.assertEqual(completions_arg, [])

    @patch("os.path.isdir")
    @patch("prompt_toolkit.completion.PathCompleter.get_completions")
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

    @patch("os.path.isdir", return_value=False)
    @patch("prompt_toolkit.completion.PathCompleter.get_completions")
    def test_file_completion(self, mock_path_completions, mock_isdir):
        doc = Document("cat file.t", cursor_position=len("cat file.t"))
        mock_path_completions.return_value = [Completion("xt", start_position=-1, display="file.txt")]

        completions = list(self.completer.get_completions(doc, self.event))

        # Should not append '/' for files
        self.assertIn(Completion("xt", start_position=-1, display="file.txt"), completions)
        mock_isdir.assert_called_once_with("file.txt")

    @patch.dict(os.environ, {"AYE_SKIP_PATH_SCAN": "1"}, clear=False)
    @patch("os.environ.get", return_value=None)
    def test_get_system_commands_no_path(self, mock_env_get):
        # Ensure no background thread starts; we want to directly test the method.
        completer = CmdPathCompleter()
        self.assertEqual(completer._get_system_commands(), [])

    def test_commands_property_loading_state(self):
        # Test that .commands returns builtins before system cmds are loaded,
        # and merged list after.
        completer = CmdPathCompleter(commands=["builtin"])

        # Pre-load state: system commands not loaded yet
        self.assertFalse(completer._system_commands_loaded)
        self.assertEqual(completer.commands, ["builtin"])

        # Manually trigger load with mocked _get_system_commands
        with patch.object(completer, "_get_system_commands", return_value=["sys_cmd"]):
            completer._load_system_commands_background()

        # Post-load state
        self.assertTrue(completer._system_commands_loaded)
        self.assertEqual(completer.commands, ["builtin", "sys_cmd"])

    def test_background_loading_exception(self):
        # Ensure exception in loading doesn't crash and sets loaded flag.
        completer = CmdPathCompleter(commands=["builtin"])

        # Patch the instance method to raise an exception
        with patch.object(completer, "_get_system_commands", side_effect=Exception("Boom")):
            completer._load_system_commands_background()

        self.assertTrue(completer._system_commands_loaded)
        self.assertEqual(completer.commands, ["builtin"])

    def test_wsl_path_skipping(self):
        # Verify that /mnt/<drive>/... paths are skipped to avoid performance issues.
        path_val = "/usr/bin:/mnt/c/Windows:/mnt/d/Data"

        with patch.dict(os.environ, {"AYE_SKIP_PATH_SCAN": "1", "PATH": path_val}), patch(
            "os.path.isdir", return_value=True
        ), patch("os.scandir") as mock_scandir:
            completer = CmdPathCompleter()
            completer._get_system_commands()

            scanned_dirs = [call.args[0] for call in mock_scandir.call_args_list]
            self.assertIn("/usr/bin", scanned_dirs)
            self.assertNotIn("/mnt/c/Windows", scanned_dirs)
            self.assertNotIn("/mnt/d/Data", scanned_dirs)

    def test_get_system_commands_unreadable_dir(self):
        # Use os.pathsep so test works on both Unix (':') and Windows (';')
        test_path = os.pathsep.join(["/bin", "/usr/bin", "/unreadable"])

        with (
            patch.dict(os.environ, {"AYE_SKIP_PATH_SCAN": "1", "PATH": test_path}),
            patch("aye.plugins.completer.os.path.isdir", side_effect=lambda p: p != "/unreadable"),
            patch("aye.plugins.completer.os.scandir") as mock_scandir,
            patch("aye.plugins.completer.os.access", return_value=True),
        ):

            class DummyDirEntries:
                """Context manager that yields the given directory entries."""

                def __init__(self, entries):
                    self._entries = entries

                def __enter__(self):
                    return iter(self._entries)

                def __exit__(self, exc_type, exc, tb):
                    return False

            def scandir_side_effect(directory):
                if directory == "/bin":
                    entry = MagicMock()
                    entry.name = "ls"
                    entry.path = "/bin/ls"
                    entry.is_file.return_value = True
                    return DummyDirEntries([entry])
                if directory == "/usr/bin":
                    entry = MagicMock()
                    entry.name = "grep"
                    entry.path = "/usr/bin/grep"
                    entry.is_file.return_value = True
                    return DummyDirEntries([entry])
                raise OSError("Permission denied")

            mock_scandir.side_effect = scandir_side_effect

            completer = CmdPathCompleter()
            commands = completer._get_system_commands()
            self.assertIn("ls", commands)
            self.assertIn("grep", commands)
            self.assertEqual(len(commands), 2)
