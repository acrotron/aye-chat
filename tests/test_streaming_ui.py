import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call, PropertyMock

from rich.console import Group
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
        auto_refresh=None,
        vertical_overflow=None,
    ):
        self.initial_renderable = renderable
        self.console = console
        self.refresh_per_second = refresh_per_second
        self.transient = transient
        self.auto_refresh = auto_refresh
        self.vertical_overflow = vertical_overflow

        self.started = False
        self.stopped = False
        self.updates = []
        self.refreshes = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def update(self, renderable):
        self.updates.append(renderable)

    def refresh(self):
        self.refreshes += 1


# ------------------------------------------------------------------ #
# Helper / env-var utilities
# ------------------------------------------------------------------ #


class TestGetEnvFloat(unittest.TestCase):
    def test_returns_default_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertAlmostEqual(streaming_ui._get_env_float("NONEXISTENT_VAR", 1.5), 1.5)

    def test_returns_parsed_value(self):
        with patch.dict(os.environ, {"MY_FLOAT": "3.14"}):
            self.assertAlmostEqual(streaming_ui._get_env_float("MY_FLOAT", 0.0), 3.14)

    def test_returns_default_on_invalid_value(self):
        with patch.dict(os.environ, {"MY_FLOAT": "oops"}):
            self.assertAlmostEqual(streaming_ui._get_env_float("MY_FLOAT", 2.0), 2.0)

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"MY_FLOAT": ""}):
            self.assertAlmostEqual(streaming_ui._get_env_float("MY_FLOAT", 9.9), 9.9)


class TestGetEnvBool(unittest.TestCase):
    _TRUE_VALUES = ("1", "on", "true", "yes", "TRUE", "Yes", "ON")
    _FALSE_VALUES = ("0", "off", "false", "no", "FALSE", "No", "OFF")

    def test_true_values(self):
        for val in self._TRUE_VALUES:
            with patch.dict(os.environ, {"B": val}):
                self.assertTrue(streaming_ui._get_env_bool("B", False), msg=f"Expected True for {val!r}")

    def test_false_values(self):
        for val in self._FALSE_VALUES:
            with patch.dict(os.environ, {"B": val}):
                self.assertFalse(streaming_ui._get_env_bool("B", True), msg=f"Expected False for {val!r}")

    def test_unset_returns_default_true(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(streaming_ui._get_env_bool("UNSET", True))

    def test_unset_returns_default_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(streaming_ui._get_env_bool("UNSET", False))

    def test_unrecognised_value_returns_default(self):
        with patch.dict(os.environ, {"B": "maybe"}):
            self.assertTrue(streaming_ui._get_env_bool("B", True))
            self.assertFalse(streaming_ui._get_env_bool("B", False))

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"B": "  yes  "}):
            self.assertTrue(streaming_ui._get_env_bool("B", False))


class TestPlatformHelpers(unittest.TestCase):
    def test_is_windows_true_on_win32(self):
        with patch.object(streaming_ui.sys, 'platform', 'win32'):
            self.assertTrue(streaming_ui._is_windows())

    def test_is_windows_false_on_linux(self):
        with patch.object(streaming_ui.sys, 'platform', 'linux'):
            self.assertFalse(streaming_ui._is_windows())

    def test_is_windows_false_on_darwin(self):
        with patch.object(streaming_ui.sys, 'platform', 'darwin'):
            self.assertFalse(streaming_ui._is_windows())

    def test_windows_gets_faster_poll_interval(self):
        with patch.object(streaming_ui.sys, 'platform', 'win32'):
            interval = streaming_ui._get_poll_interval()
            self.assertEqual(interval, streaming_ui._POLL_INTERVAL_WINDOWS)
            self.assertLess(interval, streaming_ui._POLL_INTERVAL_UNIX)

    def test_unix_gets_standard_poll_interval(self):
        with patch.object(streaming_ui.sys, 'platform', 'linux'):
            interval = streaming_ui._get_poll_interval()
            self.assertEqual(interval, streaming_ui._POLL_INTERVAL_UNIX)

    def test_windows_gets_fewer_ghost_clear_lines(self):
        with patch.object(streaming_ui.sys, 'platform', 'win32'):
            lines = streaming_ui._get_ghost_clear_lines()
            self.assertEqual(lines, streaming_ui._GHOST_CLEAR_LINES_WINDOWS)
            self.assertLess(lines, streaming_ui._GHOST_CLEAR_LINES_UNIX)

    def test_unix_gets_more_ghost_clear_lines(self):
        with patch.object(streaming_ui.sys, 'platform', 'linux'):
            lines = streaming_ui._get_ghost_clear_lines()
            self.assertEqual(lines, streaming_ui._GHOST_CLEAR_LINES_UNIX)


# ------------------------------------------------------------------ #
# _split_streaming_markdown
# ------------------------------------------------------------------ #


class TestSplitStreamingMarkdown(unittest.TestCase):
    split = staticmethod(streaming_ui._split_streaming_markdown)

    def test_empty_string(self):
        self.assertEqual(self.split(""), ("", ""))

    def test_single_word_no_newline(self):
        self.assertEqual(self.split("hello"), ("", "hello"))

    def test_single_newline(self):
        prefix, tail = self.split("line1\nline2")
        self.assertEqual(prefix, "line1\n")
        self.assertEqual(tail, "line2")

    def test_paragraph_boundary(self):
        text = "para1 text\n\npara2 text"
        prefix, tail = self.split(text)
        self.assertEqual(prefix, "para1 text\n\n")
        self.assertEqual(tail, "para2 text")

    def test_unclosed_fence(self):
        text = "before\n```python\ncode here"
        prefix, tail = self.split(text)
        self.assertIn("```python", tail)
        self.assertNotIn("```python", prefix)

    def test_closed_fence(self):
        text = "before\n```python\ncode\n```\nafter stuff"
        prefix, tail = self.split(text)
        self.assertIn("```python", prefix)
        self.assertIn("```", prefix)

    def test_multiple_fences_last_unclosed(self):
        text = "A\n```\nblock1\n```\nB\n```\nblock2 still open"
        prefix, tail = self.split(text)
        self.assertIn("block2 still open", tail)

    def test_tilde_fence(self):
        text = "before\n~~~\ncode"
        prefix, tail = self.split(text)
        self.assertIn("~~~", tail)

    def test_no_newline_all_tail(self):
        self.assertEqual(self.split("no newlines here"), ("", "no newlines here"))


# ------------------------------------------------------------------ #
# _tail_content
# ------------------------------------------------------------------ #


class TestTailContent(unittest.TestCase):
    tail = staticmethod(streaming_ui._tail_content)

    def test_empty_content(self):
        result, truncated = self.tail("", 80, 10)
        self.assertEqual(result, "")
        self.assertFalse(truncated)

    def test_zero_max_lines(self):
        result, truncated = self.tail("hello", 80, 0)
        self.assertEqual(result, "hello")
        self.assertFalse(truncated)

    def test_zero_width(self):
        result, truncated = self.tail("hello", 0, 10)
        self.assertEqual(result, "hello")
        self.assertFalse(truncated)

    def test_content_fits(self):
        result, truncated = self.tail("line1\nline2\nline3", 80, 10)
        self.assertEqual(result, "line1\nline2\nline3")
        self.assertFalse(truncated)

    def test_content_truncated(self):
        lines = "\n".join(f"line{i}" for i in range(50))
        result, truncated = self.tail(lines, 80, 5)
        self.assertTrue(truncated)
        result_lines = result.split("\n")
        self.assertLessEqual(len(result_lines), 5)
        self.assertIn("line49", result)

    def test_long_lines_wrap_estimation(self):
        content = "A" * 200
        result, truncated = self.tail(content, 80, 2)
        self.assertIn("A" * 200, result)

    def test_empty_lines_count_as_one_row(self):
        content = "\n\n\n\n\nend"
        result, truncated = self.tail(content, 80, 3)
        self.assertTrue(truncated)
        self.assertIn("end", result)


# ------------------------------------------------------------------ #
# _render_streaming_markdown
# ------------------------------------------------------------------ #


class TestRenderStreamingMarkdown(unittest.TestCase):
    render = staticmethod(streaming_ui._render_streaming_markdown)

    def test_plain_text_returns_text(self):
        result = self.render("hello")
        self.assertIsInstance(result, Text)

    def test_paragraph_returns_group_with_markdown_prefix(self):
        content = "para one\n\npara two"
        result = self.render(content)
        self.assertIsInstance(result, Group)

    def test_stall_indicator_appended(self):
        result = self.render("hello", show_stall_indicator=True)
        self.assertIsInstance(result, Group)

    def test_truncation_indicator(self):
        content = "line1\n\nline2"
        result = self.render(content, is_truncated=True)
        self.assertIsInstance(result, Group)

    def test_both_stall_and_truncation(self):
        content = "line1\n\nline2"
        result = self.render(
            content,
            show_stall_indicator=True,
            is_truncated=True,
        )
        self.assertIsInstance(result, Group)

    def test_empty_content_with_stall(self):
        result = self.render("", show_stall_indicator=True)
        self.assertIsInstance(result, Text)

    def test_content_with_only_prefix(self):
        content = "para1\n\n"
        result = self.render(content)
        self.assertIsInstance(result, Markdown)


# ------------------------------------------------------------------ #
# _create_response_panel
# ------------------------------------------------------------------ #


class TestCreateResponsePanel(unittest.TestCase):
    def test_create_response_panel_markdown_when_enabled_and_content_present(self):
        panel = streaming_ui._create_response_panel("Hello", use_markdown=True)
        self.assertIsInstance(panel, Panel)
        self.assertIsInstance(panel.renderable, Table)

        grid = panel.renderable
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

    def test_streaming_markdown_mode(self):
        panel = streaming_ui._create_response_panel(
            "para1\n\npara2",
            use_markdown=True,
            streaming=True,
        )
        self.assertIsInstance(panel, Panel)
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Group)

    def test_streaming_markdown_with_stall(self):
        panel = streaming_ui._create_response_panel(
            "some content",
            use_markdown=True,
            streaming=True,
            show_stall_indicator=True,
        )
        self.assertIsInstance(panel, Panel)

    def test_streaming_markdown_with_truncation(self):
        panel = streaming_ui._create_response_panel(
            "content",
            use_markdown=True,
            streaming=True,
            is_truncated=True,
        )
        self.assertIsInstance(panel, Panel)

    def test_non_streaming_markdown_with_stall(self):
        panel = streaming_ui._create_response_panel(
            "Hello",
            use_markdown=True,
            show_stall_indicator=True,
            streaming=False,
        )
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Group)

    def test_text_mode_with_stall(self):
        panel = streaming_ui._create_response_panel(
            "Hello",
            use_markdown=False,
            show_stall_indicator=True,
        )
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Text)
        self.assertIn("waiting for more", cell.plain)

    def test_empty_content_markdown_disabled_with_stall(self):
        panel = streaming_ui._create_response_panel(
            "",
            use_markdown=False,
            show_stall_indicator=True,
        )
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Text)
        self.assertIn("waiting for more", cell.plain)

    def test_streaming_but_empty_content_falls_to_else(self):
        panel = streaming_ui._create_response_panel(
            "",
            use_markdown=True,
            streaming=True,
        )
        grid = panel.renderable
        cell = grid.columns[1]._cells[0]
        self.assertIsInstance(cell, Text)


# ------------------------------------------------------------------ #
# StreamingResponseDisplay
# ------------------------------------------------------------------ #


class TestStreamingResponseDisplay(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()
        self.console.size.height = 40
        self.console.size.width = 120
        # Mock the file attribute for ghost clearing
        self.console.file = MagicMock()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_autostarts_and_calls_on_first_content_once(self):
        seen = {"count": 0}

        def on_first():
            seen["count"] += 1

        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, streaming))
            return {"content": content, "use_markdown": use_markdown}

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
                on_first_content=on_first,
            )
            d._min_render_interval = 0
            d.update("Hello world")

        self.console.print.assert_called_once()
        self.assertTrue(d.is_active())
        self.assertEqual(seen["count"], 1)

        self.assertEqual(events[0], ("", False, False))
        self.assertIn(("Hello", True, True), events)
        self.assertIn(("Hello ", True, True), events)
        self.assertIn(("Hello world", True, True), events)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_same_content_is_noop(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, streaming))
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

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, streaming))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("Hello world")
            d.update("New")

        self.assertEqual(d.content, "New")
        self.assertIn(("New", True, True), events)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_stop_final_markdown_render_and_spacing_after(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, streaming))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("Hi")
            d.stop()

        self.assertEqual(self.console.print.call_count, 3)
        self.assertIn(("Hi", True, False), events)
        self.assertFalse(d.is_active())

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_context_manager_starts_and_stops(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, streaming))
            return (content, use_markdown)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel):
            with streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0) as d:
                self.assertTrue(d.is_active())
            self.assertFalse(d.is_active())

        self.assertGreaterEqual(self.console.print.call_count, 2)

    def test_env_var_word_delay_used_when_word_delay_none(self):
        with patch.dict(os.environ, {"AYE_STREAM_WORD_DELAY": "0.05"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=None)
            self.assertAlmostEqual(d._word_delay, 0.05)

    def test_env_var_word_delay_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"AYE_STREAM_WORD_DELAY": "not-a-float"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=None)
            self.assertAlmostEqual(d._word_delay, 0.20)

    def test_env_var_stall_threshold_used_when_none(self):
        with patch.dict(os.environ, {"AYE_STREAM_STALL_THRESHOLD": "5.5"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, stall_threshold=None)
            self.assertAlmostEqual(d._stall_threshold, 5.5)

    def test_env_var_render_interval(self):
        with patch.dict(os.environ, {"AYE_STREAM_RENDER_INTERVAL": "0.25"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertAlmostEqual(d._min_render_interval, 0.25)

    def test_env_var_tail_disabled(self):
        with patch.dict(os.environ, {"AYE_STREAM_TAIL": "off"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertFalse(d._tail_enabled)

    def test_env_var_tail_enabled(self):
        with patch.dict(os.environ, {"AYE_STREAM_TAIL": "yes"}):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertTrue(d._tail_enabled)

    def test_create_streaming_callback_calls_update(self):
        display = MagicMock()
        cb = streaming_ui.create_streaming_callback(display)
        cb("abc")
        display.update.assert_called_once_with("abc", is_final=False)

    def test_create_streaming_callback_with_is_final(self):
        display = MagicMock()
        cb = streaming_ui.create_streaming_callback(display)
        cb("final text", is_final=True)
        display.update.assert_called_once_with("final text", is_final=True)

    def test_has_received_content_false_initially(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.assertFalse(d.has_received_content())

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_has_received_content_true_after_update(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("x")
            self.assertTrue(d.has_received_content())

    def test_content_property(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.assertEqual(d.content, "")

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_content_property_after_update(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("hello world")
            self.assertEqual(d.content, "hello world")

    def test_is_active_false_initially(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.assertFalse(d.is_active())

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_start_is_idempotent(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.start()
            live1 = d._live
            d.start()
            self.assertIs(d._live, live1)
            d._stop_monitoring.set()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_stop_when_not_started_is_noop(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        d.stop()
        self.assertFalse(d.is_active())

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_double_stop_is_safe(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("data")
            d.stop()
            d.stop()
            self.assertFalse(d.is_active())

    # -------------------------------------------------- #
    # update(is_final=True)
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_is_final_stops_live_and_prints_full(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, use_markdown, show_stall_indicator, streaming, is_truncated))
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("Streaming...")
            d.update("Final content", is_final=True)

        self.assertFalse(d.is_active())
        final_events = [(e[0], e[1], e[3]) for e in events if e[0] == "Final content"]
        self.assertIn(("Final content", True, False), final_events)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_update_is_final_snaps_content(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("final!", is_final=True)
            self.assertEqual(d._animated_content, "final!")
            self.assertEqual(d.content, "final!")

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_stop_after_is_final_is_safe(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.update("done", is_final=True)
            d.stop()
            self.assertFalse(d.is_active())

    # -------------------------------------------------- #
    # animate_words
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_animate_words_newline_forces_render(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append((content, streaming, "force" if show_stall_indicator else "normal"))
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("line1\nline2")

        contents = [e[0] for e in events]
        self.assertTrue(any("\n" in c for c in contents))

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_animate_words_tabs_handled(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append(content)
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("a\tb")

        self.assertEqual(d._animated_content, "a\tb")

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_animate_words_empty_new_text_is_noop(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d.start()
            d._animate_words("")
            d._stop_monitoring.set()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_animate_words_returns_early_when_not_started(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        # Not started, so _animate_words should return early
        d._animate_words("text")
        self.assertEqual(d._animated_content, "")

    # -------------------------------------------------- #
    # _refresh_display
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_refresh_display_noop_when_no_live(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        d._live = None
        d._resize_in_progress = False
        d._refresh_display()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_refresh_display_throttled(self):
        calls = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            calls.append(content)
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 1000
            d._live = FakeLive(Text(""))
            d._animated_content = "test"
            d._last_known_width = 120  # Match console width
            d._last_known_height = 40  # Match console height

            d._refresh_display(use_markdown=True, streaming=True)
            count_after_first = len(calls)

            d._refresh_display(use_markdown=True, streaming=True)
            self.assertEqual(len(calls), count_after_first)

            d._refresh_display(use_markdown=True, streaming=True, force=True)
            self.assertEqual(len(calls), count_after_first + 1)

    # -------------------------------------------------- #
    # Resize detection via width polling
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_width_change_triggers_resize_handling(self):
        """When terminal width changes, resize handling is triggered."""
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._live = FakeLive(Text(""))
            d._animated_content = "test"
            d._min_render_interval = 0
            d._last_known_width = 120
            d._last_known_height = 40
            d._resize_in_progress = False

            # Simulate terminal width change (narrowing)
            self.console.size.width = 80

            # Mock time.sleep to avoid actual delay
            with patch.object(streaming_ui.time, "sleep"):
                d._refresh_display(use_markdown=True, streaming=True, force=True)

            # Resize should have been detected and handled
            self.assertTrue(d._resize_in_progress)
            # Width should be updated
            self.assertEqual(d._last_known_width, 80)
            # Live should be None after resize handling
            self.assertIsNone(d._live)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_same_width_allows_refresh(self):
        """When width hasn't changed, refresh proceeds normally."""
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._live = FakeLive(Text(""))
            d._animated_content = "test"
            d._min_render_interval = 0
            d._last_known_width = 120  # Same as console mock
            d._last_known_height = 40  # Same as console mock
            d._resize_in_progress = False

            d._refresh_display(use_markdown=True, streaming=True, force=True)

            self.assertGreater(d._live.refreshes, 0)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_detect_resize_returns_true_on_change(self):
        """_detect_resize returns True when size changed."""
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        d._last_known_width = 120
        d._last_known_height = 40

        self.console.size.width = 80
        result = d._detect_resize()

        self.assertTrue(result)
        self.assertEqual(d._last_known_width, 80)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_detect_resize_returns_false_when_same(self):
        """_detect_resize returns False when size is unchanged."""
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        d._last_known_width = 120
        d._last_known_height = 40

        result = d._detect_resize()

        self.assertFalse(result)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_height_change_also_triggers_resize(self):
        """_detect_resize detects height changes too."""
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        d._last_known_width = 120
        d._last_known_height = 40

        self.console.size.height = 30
        result = d._detect_resize()

        self.assertTrue(result)
        self.assertEqual(d._last_known_height, 30)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_resize_in_progress_suppresses_all_renders(self):
        """When resize is in progress, no renders happen."""
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._live = FakeLive(Text(""))
            d._animated_content = "test"
            d._min_render_interval = 0
            d._last_known_width = 120
            d._last_known_height = 40

            # Manually set the resize in progress flag
            d._resize_in_progress = True
            d._resize_cooldown_until = time.time() + 10  # Far in the future

            d._refresh_display(use_markdown=True, streaming=True, force=True)

            # No updates should have been made to Live
            self.assertEqual(len(d._live.updates), 0)
            self.assertEqual(d._live.refreshes, 0)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_resize_cooldown_expires_allows_refresh(self):
        """After cooldown expires, refresh works."""
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._animated_content = "test"
            d._min_render_interval = 0
            d._last_known_width = 120
            d._last_known_height = 40
            d._started = True

            # Resize was in progress but cooldown has expired
            d._resize_in_progress = True
            d._resize_cooldown_until = time.time() - 1  # In the past
            d._live = None  # Live was stopped during resize

            d._refresh_display(use_markdown=True, streaming=True, force=True)

            # After cooldown, a new Live should be created and refreshed
            self.assertIsNotNone(d._live)
            self.assertFalse(d._resize_in_progress)
            self.assertGreater(d._live.refreshes, 0)

    # -------------------------------------------------- #
    # Pre/Post dimension check
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_dimension_change_during_panel_build_triggers_resize(self):
        """If dimensions change between pre and post check, resize is triggered."""
        call_count = [0]
        
        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            call_count[0] += 1
            # Simulate resize happening during panel creation
            if call_count[0] == 1:
                # Change dimensions after first call (initial panel)
                pass
            elif call_count[0] == 2:
                # This is when we're building the streaming panel - simulate resize
                self.console.size.width = 80  # Was 120
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d._last_known_width = 120
            d._last_known_height = 40
            d._resize_in_progress = False
            d._live = FakeLive(Text(""))
            d._animated_content = "test"

            d._refresh_display(use_markdown=True, streaming=True, force=True)

            # Should have detected resize via post-build check
            self.assertTrue(d._resize_in_progress)
            self.assertIsNone(d._live)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_get_current_dimensions_returns_fresh_values(self):
        """_get_current_dimensions returns current console size."""
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        
        self.console.size.width = 100
        self.console.size.height = 50
        
        dims = d._get_current_dimensions()
        
        self.assertEqual(dims, (100, 50))

    # -------------------------------------------------- #
    # Tailing during streaming
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_tailing_kicks_in_for_long_content(self):
        self.console.size.height = 10
        self.console.size.width = 80

        panel_args = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            panel_args.append({"content": content, "is_truncated": is_truncated, "streaming": streaming})
            return Text(content)

        long_content = "\n".join(f"line {i}" for i in range(100))

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._tail_enabled = True
            d._min_render_interval = 0
            d._live = FakeLive(Text(""))
            d._started = True
            d._animated_content = long_content
            d._last_known_width = 80
            d._last_known_height = 10
            d._resize_in_progress = False

            d._refresh_display(use_markdown=True, streaming=True, force=True)

        streaming_renders = [a for a in panel_args if a["streaming"]]
        self.assertTrue(len(streaming_renders) > 0)
        self.assertTrue(any(r["is_truncated"] for r in streaming_renders))

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_tailing_disabled_shows_full_content(self):
        self.console.size.height = 10
        self.console.size.width = 80

        panel_args = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            panel_args.append({"content": content, "is_truncated": is_truncated})
            return Text(content)

        long_content = "\n".join(f"line {i}" for i in range(100))

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._tail_enabled = False
            d._min_render_interval = 0
            d._live = FakeLive(Text(""))
            d._started = True
            d._animated_content = long_content
            d._last_known_width = 80
            d._last_known_height = 10
            d._resize_in_progress = False

            d._refresh_display(use_markdown=True, streaming=True, force=True)

        self.assertTrue(all(not a["is_truncated"] for a in panel_args))

    # -------------------------------------------------- #
    # _compute_available_lines / _compute_inner_width
    # -------------------------------------------------- #

    def test_compute_available_lines_no_stall(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.console.size.height = 40
        result = d._compute_available_lines(show_stall=False)
        self.assertEqual(result, 36)

    def test_compute_available_lines_with_stall(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.console.size.height = 40
        result = d._compute_available_lines(show_stall=True)
        self.assertEqual(result, 34)

    def test_compute_available_lines_minimum(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.console.size.height = 5
        result = d._compute_available_lines(show_stall=True)
        self.assertEqual(result, 3)

    def test_compute_inner_width(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.console.size.width = 120
        result = d._compute_inner_width()
        self.assertEqual(result, 108)

    def test_compute_inner_width_minimum(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        self.console.size.width = 10
        result = d._compute_inner_width()
        self.assertEqual(result, 20)

    # -------------------------------------------------- #
    # _render_final_and_stop edge cases
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_render_final_and_stop_when_no_live(self):
        d = streaming_ui.StreamingResponseDisplay(console=self.console)
        d._live = None
        d._render_final_and_stop()
        self.console.print.assert_not_called()

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_render_final_and_stop_with_empty_content(self):
        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.start()
            d._animated_content = ""
            d._render_final_and_stop()

        calls = self.console.print.call_args_list
        self.assertEqual(len(calls), 2)

    # -------------------------------------------------- #
    # Stall monitor
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_monitor_stall_detects_stall(self):
        refresh_calls = []

        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
                stall_threshold=0.01,
            )
            d._min_render_interval = 0
            d.start()

            d._current_content = "some content"
            d._animated_content = "some content"
            d._is_animating = False
            d._last_receive_time = time.time() - 1.0
            d._showing_stall_indicator = False

            original_refresh = d._refresh_display

            def capture_refresh(**kwargs):
                refresh_calls.append(kwargs)
                return original_refresh(**kwargs)

            with patch.object(d, "_refresh_display", side_effect=capture_refresh):
                time.sleep(0.8)

            d._stop_monitoring.set()

        stall_refreshes = [c for c in refresh_calls if c.get("show_stall") is True]
        self.assertTrue(len(stall_refreshes) > 0)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_monitor_stall_skips_when_animating(self):
        refresh_calls = []

        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
                stall_threshold=0.01,
            )
            d._min_render_interval = 0
            d.start()

            d._current_content = "some content"
            d._animated_content = "some con"
            d._is_animating = True
            d._last_receive_time = time.time() - 1.0
            d._showing_stall_indicator = False

            with patch.object(d, "_refresh_display", side_effect=lambda **kw: refresh_calls.append(kw)):
                time.sleep(0.8)

            d._stop_monitoring.set()

        stall_refreshes = [c for c in refresh_calls if c.get("show_stall") is True]
        self.assertEqual(len(stall_refreshes), 0)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_monitor_stall_skips_when_no_content(self):
        refresh_calls = []

        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
                stall_threshold=0.01,
            )
            d._min_render_interval = 0
            d.start()

            d._current_content = ""
            d._animated_content = ""
            d._last_receive_time = time.time() - 1.0

            with patch.object(d, "_refresh_display", side_effect=lambda **kw: refresh_calls.append(kw)):
                time.sleep(0.8)

            d._stop_monitoring.set()

        self.assertEqual(len(refresh_calls), 0)

    # -------------------------------------------------- #
    # Stall indicator hidden on new content
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_stall_indicator_hidden_on_new_content(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append({"content": content, "show_stall": show_stall_indicator, "streaming": streaming})
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0,
            )
            d._min_render_interval = 0
            d.start()

            d._showing_stall_indicator = True
            d._current_content = "original"
            d._animated_content = "original"

            d.update("original more")

        non_stall = [e for e in events if e["streaming"] and not e["show_stall"]]
        self.assertTrue(len(non_stall) > 0)

    # -------------------------------------------------- #
    # word_delay > 0 calls time.sleep
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_word_delay_causes_sleep(self):
        sleep_calls = []

        def capture_sleep(duration):
            sleep_calls.append(duration)

        with patch.object(streaming_ui, "_create_response_panel", return_value=Text("")), \
             patch.object(streaming_ui.time, "sleep", side_effect=capture_sleep):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                word_delay=0.1,
            )
            d._min_render_interval = 0
            d.update("hello world")

        self.assertTrue(len(sleep_calls) >= 2)
        self.assertTrue(all(s == 0.1 for s in sleep_calls))

    # -------------------------------------------------- #
    # carriage return handling
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_carriage_return_handled(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append(content)
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("a\rb")

        self.assertEqual(d._animated_content, "a\rb")

    # -------------------------------------------------- #
    # Multiple whitespace characters
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_multiple_spaces_grouped(self):
        events = []

        def fake_panel(content, use_markdown=True, show_stall_indicator=False, streaming=False, is_truncated=False):
            events.append(content)
            return Text(content)

        with patch.object(streaming_ui, "_create_response_panel", side_effect=fake_panel), \
             patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._min_render_interval = 0
            d.update("a   b")

        self.assertEqual(d._animated_content, "a   b")
        self.assertIn("a", events)
        self.assertIn("a   ", events)
        self.assertIn("a   b", events)

    # -------------------------------------------------- #
    # Ghost clearing - platform-specific behavior
    # -------------------------------------------------- #

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_clear_potential_ghosts_writes_ansi_codes(self):
        """Test that ghost clearing writes ANSI escape codes."""
        d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
        
        d._clear_potential_ghosts()
        
        # Should have written to console.file
        self.assertTrue(self.console.file.write.called)
        self.assertTrue(self.console.file.flush.called)
        
        # Check that some ANSI codes were written
        all_writes = [str(call[0][0]) for call in self.console.file.write.call_args_list]
        combined = "".join(all_writes)
        # Should contain cursor movement and clear codes
        self.assertIn("\033[", combined)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_handle_resize_start_calls_clear_ghosts_only_when_live_exists(self):
        """Test that _handle_resize_start only clears ghosts when there was a Live."""
        with patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._live = None  # No live instance
            d._last_known_width = 120
            d._last_known_height = 40
            
            # Reset the mock to clear any previous calls
            self.console.file.write.reset_mock()
            
            d._handle_resize_start()
        
        # Should NOT have called ghost clearing since there was no Live
        self.assertFalse(self.console.file.write.called)
        # But resize should still be in progress
        self.assertTrue(d._resize_in_progress)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_handle_resize_start_clears_ghosts_when_live_exists(self):
        """Test that _handle_resize_start clears ghosts when Live exists."""
        write_calls = []
        
        def capture_write(data):
            write_calls.append(data)
        
        self.console.file.write = capture_write
        
        with patch.object(streaming_ui.time, "sleep"):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            d._live = FakeLive(Text(""))  # Has live instance
            d._last_known_width = 120
            d._last_known_height = 40
            
            d._handle_resize_start()
        
        # Should have called ghost clearing (wrote ANSI codes)
        self.assertTrue(len(write_calls) > 0)
        combined = "".join(write_calls)
        self.assertIn("\033[", combined)
        
        # Live should be None
        self.assertIsNone(d._live)
        # Resize should be in progress
        self.assertTrue(d._resize_in_progress)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_windows_uses_fewer_ghost_clear_lines(self):
        """Test that Windows clears fewer lines than Unix."""
        with patch.object(streaming_ui.sys, 'platform', 'win32'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            
            write_calls = []
            def capture_write(data):
                write_calls.append(data)
            self.console.file.write = capture_write
            
            d._clear_potential_ghosts()
            
            # Count cursor-up commands (\033[A) to verify fewer lines cleared
            combined = "".join(write_calls)
            up_commands = combined.count("\033[A")
            
            # Should be _GHOST_CLEAR_LINES_WINDOWS or less
            self.assertLessEqual(up_commands, streaming_ui._GHOST_CLEAR_LINES_WINDOWS)

    @patch.object(streaming_ui, "Live", FakeLive)
    def test_windows_minimal_cursor_repositioning(self):
        """Test that Windows moves cursor down minimally after clearing."""
        with patch.object(streaming_ui.sys, 'platform', 'win32'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console, word_delay=0)
            
            write_calls = []
            def capture_write(data):
                write_calls.append(data)
            self.console.file.write = capture_write
            
            d._clear_potential_ghosts()
            
            combined = "".join(write_calls)
            up_commands = combined.count("\033[A")
            down_commands = combined.count("\033[B")
            
            # Windows should move down very little (1-2 lines max)
            self.assertLessEqual(down_commands, 2)
            # And significantly less than the number of up commands
            self.assertLess(down_commands, up_commands)


if __name__ == "__main__":
    unittest.main()
