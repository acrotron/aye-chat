from unittest import TestCase
from unittest.mock import MagicMock

from prompt_toolkit.document import Document

from aye.plugins.slash_completer import SlashCompleter, SlashCompleterPlugin


def _display_text(completion):
    """prompt_toolkit Completion.display can vary by version; normalize to plain text."""
    return getattr(completion, "display_text", None) or str(getattr(completion, "display", ""))


def _display_meta_text(completion):
    """prompt_toolkit Completion.display_meta can vary by version; normalize to plain text."""
    return getattr(completion, "display_meta_text", None) or str(getattr(completion, "display_meta", ""))


class TestSlashCompleter(TestCase):
    def setUp(self):
        # Purposely unsorted to confirm sorting behavior.
        self.completer = SlashCompleter(commands=["help", "exit", "history"])
        self.event = MagicMock()

    def test_no_completion_without_leading_slash(self):
        doc = Document("help", cursor_position=4)
        completions = list(self.completer.get_completions(doc, self.event))
        self.assertEqual(completions, [])

        doc = Document("echo /h", cursor_position=len("echo /h"))
        completions = list(self.completer.get_completions(doc, self.event))
        self.assertEqual(completions, [])

    def test_slash_only_shows_all_commands_sorted(self):
        doc = Document("/", cursor_position=1)
        completions = list(self.completer.get_completions(doc, self.event))

        self.assertEqual([c.text for c in completions], ["exit", "help", "history"])
        for c in completions:
            self.assertEqual(c.start_position, 0)
            self.assertEqual(_display_text(c), f"/{c.text}")
            self.assertEqual(_display_meta_text(c), "Aye command")

    def test_slash_prefix_filters_and_sets_start_position(self):
        doc = Document("/he", cursor_position=3)
        completions = list(self.completer.get_completions(doc, self.event))

        self.assertEqual([c.text for c in completions], ["help"])
        self.assertEqual(completions[0].start_position, -2)
        self.assertEqual(_display_text(completions[0]), "/help")
        self.assertEqual(_display_meta_text(completions[0]), "Aye command")

    def test_slash_prefix_no_matches(self):
        doc = Document("/zzz", cursor_position=4)
        completions = list(self.completer.get_completions(doc, self.event))
        self.assertEqual(completions, [])


class TestSlashCompleterPlugin(TestCase):
    def setUp(self):
        self.plugin = SlashCompleterPlugin()
        self.plugin.init({})

    def test_on_command_get_slash_completer(self):
        result = self.plugin.on_command("get_slash_completer", {"commands": ["b", "a"]})
        self.assertIn("completer", result)
        self.assertIsInstance(result["completer"], SlashCompleter)

        completer = result["completer"]
        doc = Document("/", cursor_position=1)
        completions = list(completer.get_completions(doc, MagicMock()))
        self.assertEqual([c.text for c in completions], ["a", "b"])

    def test_on_command_other_command(self):
        self.assertIsNone(self.plugin.on_command("other_command", {}))
