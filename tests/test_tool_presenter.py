"""Tests for the tool-call result presenter."""

from rich.console import Console

from aye.model.tool_protocol import ToolCall
from aye.presenter.tool_presenter import (
    _DISPLAY_MAX_LINES,
    present_tool_result,
    _truncate_output,
)


class TestTruncateOutput:
    def test_preserves_short_output(self):
        assert _truncate_output("hello\nworld") == "hello\nworld"

    def test_caps_line_count(self):
        out = "\n".join(f"line {i}" for i in range(_DISPLAY_MAX_LINES + 20))
        result = _truncate_output(out)
        assert "(output truncated)" in result
        assert "line 0" in result
        assert f"line {_DISPLAY_MAX_LINES + 19}" not in result

    def test_escapes_rich_markup(self):
        result = _truncate_output("danger [red]boom[/red]")
        assert r"\[red]" in result

    def test_empty_output(self):
        assert _truncate_output("") == ""


class TestPresentToolResult:
    def test_prints_panel_with_call_and_output(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="grep", arguments={"pattern": "def foo"})
        present_tool_result(call, "a.py:3: def foo", console)
        out = capsys.readouterr().out
        assert "grep" in out
        assert "def foo" in out

    def test_handles_no_output(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="write", arguments={"file_name": "x.py"})
        present_tool_result(call, "", console)
        out = capsys.readouterr().out
        assert "(no output)" in out