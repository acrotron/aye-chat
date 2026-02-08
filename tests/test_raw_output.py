"""Tests for aye.presenter.raw_output module."""

import pytest
from unittest.mock import patch

from aye.presenter.raw_output import (
    print_assistant_response_raw,
    _RAW_BEGIN,
    _RAW_END,
    _NO_RESPONSE_MSG,
)


class TestPrintAssistantResponseRaw:
    """Tests for print_assistant_response_raw()."""

    def test_none_input_shows_no_response(self):
        with patch("aye.presenter.raw_output.rprint") as mock_rprint:
            print_assistant_response_raw(None)
        mock_rprint.assert_called_once_with(f"[yellow]{_NO_RESPONSE_MSG}[/]")

    def test_empty_string_shows_no_response(self):
        with patch("aye.presenter.raw_output.rprint") as mock_rprint:
            print_assistant_response_raw("")
        mock_rprint.assert_called_once_with(f"[yellow]{_NO_RESPONSE_MSG}[/]")

    def test_whitespace_only_shows_no_response(self):
        with patch("aye.presenter.raw_output.rprint") as mock_rprint:
            print_assistant_response_raw("   \t\n  ")
        mock_rprint.assert_called_once_with(f"[yellow]{_NO_RESPONSE_MSG}[/]")

    def test_normal_text_without_trailing_newline(self, capsys):
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw("hello world")
        captured = capsys.readouterr().out
        lines = captured.split("\n")
        # Expected: empty, _RAW_BEGIN, "hello world", _RAW_END, empty, trailing
        assert _RAW_BEGIN in captured
        assert _RAW_END in captured
        assert "hello world" in captured
        # Verify ordering: BEGIN before content before END
        begin_pos = captured.index(_RAW_BEGIN)
        content_pos = captured.index("hello world")
        end_pos = captured.index(_RAW_END)
        assert begin_pos < content_pos < end_pos

    def test_text_with_trailing_newline_uses_end_empty(self, capsys):
        """When text ends with '\n', print(text, end='') is used so no extra blank line."""
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw("hello\n")
        captured = capsys.readouterr().out
        assert _RAW_BEGIN in captured
        assert _RAW_END in captured
        assert "hello" in captured
        # The content should end with exactly one newline before _RAW_END
        # i.e. no double newline between content and delimiter
        between = captured.split(_RAW_BEGIN)[1].split(_RAW_END)[0]
        # between should be "\nhello\n"
        assert between == "\nhello\n"

    def test_text_without_trailing_newline_gets_newline(self, capsys):
        """When text does NOT end with '\n', print(text) adds one."""
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw("no newline")
        captured = capsys.readouterr().out
        between = captured.split(_RAW_BEGIN)[1].split(_RAW_END)[0]
        assert between == "\nno newline\n"

    def test_rich_markup_is_not_rendered(self, capsys):
        """Text containing Rich-like markup should be printed literally."""
        rich_text = "Use [bold]important[/bold] and [red]color[/red] here."
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw(rich_text)
        captured = capsys.readouterr().out
        # The raw markup tokens must appear verbatim
        assert "[bold]" in captured
        assert "[/bold]" in captured
        assert "[red]" in captured
        assert "[/red]" in captured

    def test_multiline_text(self, capsys):
        text = "line one\nline two\nline three"
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw(text)
        captured = capsys.readouterr().out
        assert "line one" in captured
        assert "line two" in captured
        assert "line three" in captured
        assert captured.index(_RAW_BEGIN) < captured.index("line one")
        assert captured.index("line three") < captured.index(_RAW_END)

    def test_no_response_path_does_not_print_delimiters(self, capsys):
        """When input is None/empty, only rprint is called, no raw delimiters."""
        with patch("aye.presenter.raw_output.rprint"):
            print_assistant_response_raw(None)
        captured = capsys.readouterr().out
        assert _RAW_BEGIN not in captured
        assert _RAW_END not in captured

    def test_rprint_not_called_for_valid_text(self):
        """When valid text is provided, rprint (Rich) should NOT be called."""
        with patch("aye.presenter.raw_output.rprint") as mock_rprint:
            print_assistant_response_raw("valid text")
        mock_rprint.assert_not_called()
