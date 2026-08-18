"""Tests for the tool-call presenter (opencode-style one-liners + shell panel)."""

from rich.console import Console

from aye.model.tool_protocol import ToolCall
from aye.presenter.tool_presenter import (
    SHELL_TOOL_NAMES,
    _describe_call,
    present_tool_call,
    present_tool_result,
)


class TestDescribeCall:
    def test_glob_line(self):
        call = ToolCall(name="glob", arguments={"pattern": "src/**/*.py"})
        assert _describe_call(call, "") == 'Glob "src/**/*.py"'

    def test_grep_line_with_count(self):
        call = ToolCall(name="grep", arguments={"pattern": "def foo"})
        out = "7 matches for 'def foo'\na.py:3: def foo"
        assert _describe_call(call, out) == 'Grep "def foo" (7 matches)'

    def test_grep_line_with_include_and_no_matches(self):
        call = ToolCall(
            name="grep",
            arguments={"pattern": "zzz", "include": "src/aye/model/tools.py"},
        )
        out = "No matches for 'zzz' (1 file searched)"
        assert _describe_call(call, out) == (
            'Grep "zzz" in src/aye/model/tools.py (no matches)'
        )

    def test_read_line_with_options(self):
        call = ToolCall(
            name="read",
            arguments={"path": "src/aye/model/tools.py", "start": 45, "limit": 761},
        )
        assert _describe_call(call, "") == (
            "Read src/aye/model/tools.py [start=45, limit=761]"
        )

    def test_read_line_without_options(self):
        call = ToolCall(name="read", arguments={"path": "main.py"})
        assert _describe_call(call, "") == "Read main.py"

    def test_write_line(self):
        call = ToolCall(name="write", arguments={"path": "src/foo.py"})
        assert _describe_call(call, "") == "Write src/foo.py"

    def test_shell_line(self):
        call = ToolCall(name="cmd", arguments={"command": "pytest -q"})
        assert _describe_call(call, "") == "cmd pytest -q"


class TestPresentToolResult:
    def test_file_tool_prints_compact_line_only(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="glob", arguments={"pattern": "tests/*.py"})
        present_tool_result(call, "a.py\nb.py", console)
        out = capsys.readouterr().out
        assert '✱Glob "tests/*.py"' in out
        assert "a.py" not in out

    def test_shell_tool_prints_output_panel(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="cmd", arguments={"command": "pytest -q"})
        present_tool_result(call, "2134 passed", console)
        out = capsys.readouterr().out
        assert "2134 passed" in out

    def test_web_search_prints_compact_line_only(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="web_search", arguments={"query": "httpx timeout"})
        present_tool_result(
            call, "1. HTTPX\n   https://python-httpx.org/", console
        )
        out = capsys.readouterr().out
        assert '✱Web_search "httpx timeout"' in out
        assert "python-httpx.org" not in out

    def test_shell_panel_escapes_markup(self, capsys):
        console = Console(force_terminal=False)
        call = ToolCall(name="cmd", arguments={"command": "echo"})
        present_tool_result(call, "danger [red]boom[/red]", console)
        out = capsys.readouterr().out
        # Escaped markup renders back as literal text; unescaped it would be
        # swallowed by the style parser and vanish from the output.
        assert "[red]boom" in out

    def test_shell_tool_names(self):
        assert "bash" in SHELL_TOOL_NAMES
        assert "cmd" in SHELL_TOOL_NAMES