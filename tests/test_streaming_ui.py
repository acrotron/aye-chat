import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import aye.presenter.streaming_ui as streaming_ui


class TestStreamingResponseDisplay(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()
        size_mock = MagicMock()
        size_mock.width = 80
        type(self.console).size = PropertyMock(return_value=size_mock)

    def test_update_autostarts_and_calls_on_first_content_once(self):
        seen = {"count": 0}

        def on_first():
            seen["count"] += 1

        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(
                console=self.console,
                on_first_content=on_first,
            )
            d.update("Hello world")
            d.stop()

        self.assertEqual(seen["count"], 1)

    def test_update_same_content_is_noop(self):
        with patch.object(streaming_ui, 'Live') as mock_live_class:
            mock_live = MagicMock()
            mock_live_class.return_value = mock_live
            
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d._min_render_interval = 0  # Disable throttling
            d.update("Hi")
            
            initial_calls = mock_live.update.call_count
            d.update("Hi")  # Same content
            
            self.assertEqual(mock_live.update.call_count, initial_calls)
            d.stop()

    def test_update_different_content_triggers_render(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d._min_render_interval = 0
            d.update("Hello")
            d.update("Hello world")
            d.stop()

        self.assertEqual(d.content, "Hello world")

    def test_stop_finalizes(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.update("Hi")
            d.stop()

        self.assertFalse(d.is_active())

    def test_context_manager(self):
        with patch.object(streaming_ui, 'Live'):
            with streaming_ui.StreamingResponseDisplay(console=self.console) as d:
                d.update("test")
                self.assertTrue(d.is_active())
            self.assertFalse(d.is_active())

    def test_create_streaming_callback(self):
        display = MagicMock()
        cb = streaming_ui.create_streaming_callback(display)
        cb("abc")
        display.update.assert_called_once_with("abc", is_final=False)

    def test_has_received_content(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertFalse(d.has_received_content())
            d.update("test")
            self.assertTrue(d.has_received_content())
            d.stop()

    def test_content_property(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.update("first")
            self.assertEqual(d.content, "first")
            d.update("first more")
            self.assertEqual(d.content, "first more")
            d.stop()

    def test_stop_idempotent(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.update("test")
            d.stop()
            d.stop()  # Should not raise
            self.assertFalse(d.is_active())

    def test_update_after_stop_ignored(self):
        with patch.object(streaming_ui, 'Live'):
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            d.update("first")
            d.stop()
            d.update("second")
            self.assertEqual(d.content, "first")

    def test_env_var_viewport_height(self):
        old = os.environ.get("AYE_STREAM_VIEWPORT_HEIGHT")
        try:
            os.environ["AYE_STREAM_VIEWPORT_HEIGHT"] = "25"
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertEqual(d._viewport_height, 25)
        finally:
            if old is None:
                os.environ.pop("AYE_STREAM_VIEWPORT_HEIGHT", None)
            else:
                os.environ["AYE_STREAM_VIEWPORT_HEIGHT"] = old

    def test_env_var_final_markdown_off(self):
        old = os.environ.get("AYE_STREAM_FINAL_MARKDOWN")
        try:
            os.environ["AYE_STREAM_FINAL_MARKDOWN"] = "off"
            d = streaming_ui.StreamingResponseDisplay(console=self.console)
            self.assertFalse(d._final_markdown)
        finally:
            if old is None:
                os.environ.pop("AYE_STREAM_FINAL_MARKDOWN", None)
            else:
                os.environ["AYE_STREAM_FINAL_MARKDOWN"] = old


class TestGetLastNLines(unittest.TestCase):
    def test_fewer_lines_than_n(self):
        text = "line1\nline2\nline3"
        result = streaming_ui._get_last_n_lines(text, 10)
        self.assertEqual(result, text)

    def test_exact_n_lines(self):
        text = "line1\nline2\nline3"
        result = streaming_ui._get_last_n_lines(text, 3)
        self.assertEqual(result, text)

    def test_more_lines_than_n(self):
        text = "line1\nline2\nline3\nline4\nline5"
        result = streaming_ui._get_last_n_lines(text, 3)
        self.assertEqual(result, "line3\nline4\nline5")

    def test_single_line(self):
        text = "single line"
        result = streaming_ui._get_last_n_lines(text, 5)
        self.assertEqual(result, text)

    def test_empty_string(self):
        result = streaming_ui._get_last_n_lines("", 5)
        self.assertEqual(result, "")


class TestBuildViewportDisplay(unittest.TestCase):
    def test_creates_grid(self):
        from rich.table import Table
        display = streaming_ui._build_viewport_display("test content", 10)
        self.assertIsInstance(display, Table)

    def test_empty_content(self):
        display = streaming_ui._build_viewport_display("", 10)
        self.assertIsNotNone(display)

    def test_content_truncation(self):
        # Content with more lines than viewport should work
        content = "\n".join([f"line{i}" for i in range(20)])
        display = streaming_ui._build_viewport_display(content, 5)
        self.assertIsNotNone(display)


class TestBuildFinalDisplay(unittest.TestCase):
    def test_creates_grid(self):
        from rich.table import Table
        display = streaming_ui._build_final_display("test")
        self.assertIsInstance(display, Table)

    def test_empty_content(self):
        display = streaming_ui._build_final_display("")
        self.assertIsNotNone(display)

    def test_code_block(self):
        content = "```python\nprint('hi')\n```"
        display = streaming_ui._build_final_display(content)
        self.assertIsNotNone(display)


class TestCreatePulseMarker(unittest.TestCase):
    def test_creates_text(self):
        from rich.text import Text
        marker = streaming_ui._create_pulse_marker()
        self.assertIsInstance(marker, Text)

    def test_contains_symbol(self):
        marker = streaming_ui._create_pulse_marker()
        self.assertIn("●", marker.plain)


if __name__ == '__main__':
    unittest.main()
