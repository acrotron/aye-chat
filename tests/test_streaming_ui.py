import os
import unittest
from unittest.mock import MagicMock, patch

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import aye.presenter.streaming_ui as streaming_ui


class FakeLive:
    """Minimal stand-in for rich.live.Live used by StreamingResponseDisplay."""

    def __init__(
        self,
        renderable,
        console=None,
        refresh_per_second=None,
        transient=None,
    ):
        self.initial_renderable = renderable
        self.console = console
        self.refresh_per_second = refresh_per_second
        self.transient = transient

        self.started = False
        self.stopped = False
        self.updates = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def update(self, renderable):
        self.updates.append(renderable)


class TestCreateResponsePanel(unittest.TestCase):
    def test_create_response_panel_markdown_when_enabled_and_content_present(self):
        panel = streaming_ui._create_response_panel("Hello", use_markdown=True)
        self.assertIsInstance(panel, Panel)
        self.assertIsInstance(panel.renderable, Table)

        grid = panel.renderable
        # Rich stores row cells in Column._cells
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Markdown)

    def test_create_response_panel_text_when_markdown_disabled(self):
        panel = streaming_ui._create_response_panel("Hello", use_markdown=False)
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Text)

    def test_create_response_panel_text_when_content_empty(self):
        panel = streaming_ui._create_response_panel("", use_markdown=True)
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Text)
        self.assertEqual(cell.plain, "")


class TestStreamingResponseDisplay(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_autostarts_and_calls_on_first_content_once(self):
        seen = {"count": 0}

        def on_first():
            seen["count"] += 1

        events = []

        def fake_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False):
            # Return a lightweight sentinel to assert on
            events.append((content, use_markdown))
            return {"content": content, "use_markdown": use_markdown}

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
                on_first_content=on_first,
            )
            d.update("Hello world")

        # Auto-start should have printed spacing before the panel
        self.console.print.assert_called_once()
        self.assertTrue(d.is_active())
        self.assertEqual(seen["count"], 1)

        # Initial panel created in start()
        self.assertEqual(events[0], ("", False))

        # Animation updates: "Hello" (word), " " (whitespace), "world" (word)
        # Each update passes the full animated content so far.
        self.assertIn(("Hello", False), events)
        self.assertIn(("Hello ", False), events)
        self.assertIn(("Hello world", False), events)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_same_content_is_noop(self):
        events = []

        def fake_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False):
            events.append((content, use_markdown))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("Hi")
            event_count_after_first = len(events)
            d.update("Hi")
            self.assertEqual(len(events), event_count_after_first)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_non_appended_content_resets_animation(self):
        events = []

        def fake_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False):
            events.append((content, use_markdown))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("Hello world")
            d.update("New")

        # Ensure the final animated content is exactly "New" (i.e., reset occurred)
        self.assertEqual(d.content, "New")
        self.assertIn(("New", False), events)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_stop_final_markdown_render_and_spacing_after(self):
        events = []

        def fake_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False):
            events.append((content, use_markdown))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("Hi")
            d.stop()

        # start() prints once before, stop() prints once after
        self.assertEqual(self.console.print.call_count, 2)

        # stop() does a final update with markdown=True if there is animated content
        self.assertIn(("Hi", True), events)
        self.assertFalse(d.is_active())

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_context_manager_starts_and_stops(self):
        events = []

        def fake_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False):
            events.append((content, use_markdown))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel):
            with streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0) as d:
                self.assertTrue(d.is_active())
            self.assertFalse(d.is_active())

        # Should have printed spacing before and after
        self.assertEqual(self.console.print.call_count, 2)

    def test_env_var_word_delay_used_when_word_delay_none(self):
        old = os.environ.get("AYE_STREAM_WORD_DELAY")
        try:
            os.environ["AYE_STREAM_WORD_DELAY"] = "0.05"
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=None)
            self.assertAlmostEqual(d._word_delay, 0.05)
        finally:
            if old is None:
                os.environ.pop("AYE_STREAM_WORD_DELAY", None)
            else:
                os.environ["AYE_STREAM_WORD_DELAY"] = old

    def test_env_var_word_delay_invalid_falls_back_to_default(self):
        old = os.environ.get("AYE_STREAM_WORD_DELAY")
        try:
            os.environ["AYE_STREAM_WORD_DELAY"] = "not-a-float"
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=None)
            self.assertAlmostEqual(d._word_delay, 0.20)
        finally:
            if old is None:
                os.environ.pop("AYE_STREAM_WORD_DELAY", None)
            else:
                os.environ["AYE_STREAM_WORD_DELAY"] = old

    def test_create_streaming_callback_calls_update(self):
        display = MagicMock()
        cb = streaming_ui.create_streaming_callback(display)
        cb("abc")
        display.update.assert_called_once_with("abc", is_final=False)
