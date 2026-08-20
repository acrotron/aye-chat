"""Tests for the tool-call presenter (opencode-style one-liners + AgentBubble)."""

from rich.console import Console

from aye.model.tool_protocol import ToolCall
from aye.presenter.tool_presenter import (
    SHELL_TOOL_NAMES,
    AgentBubble,
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
        assert '✱ Glob "tests/*.py"' in out
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
        assert '✱ Web_search "httpx timeout"' in out
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


class TestAgentBubble:
    def test_empty_bubble_renders_nothing(self):
        assert AgentBubble().render() == ""

    def test_merges_narration_tools_and_answer_into_one_text(self):
        bubble = AgentBubble()
        bubble.add_narration("Ok, let me check the layout.")
        bubble.add_tool_call(ToolCall(name="glob", arguments={"pattern": "*.py"}), "")
        bubble.add_tool_call(ToolCall(name="read", arguments={"path": "main.py"}), "")
        bubble.set_answer("Found it: main.py is the entry point.")
        out = bubble.render()
        assert "Ok, let me check the layout." in out
        assert '✱ Glob "*.py"' in out
        assert "Read main.py" in out
        assert "Found it: main.py is the entry point." in out
        assert out.index("Ok, let me") < out.index("Glob")
        assert out.index("Glob") < out.index("Found it")

    def test_shell_result_includes_fenced_output(self):
        bubble = AgentBubble()
        bubble.add_shell_result(
            ToolCall(name="cmd", arguments={"command": "pytest -q"}),
            "$ pytest -q\nexit code: 0\n--- stdout ---\n2134 passed",
        )
        out = bubble.render()
        assert "cmd pytest -q" in out
        assert "```text" in out
        assert "2134 passed" in out
        assert "$ pytest -q" not in out  # command echo line is dropped

    def test_blank_narration_is_ignored(self):
        bubble = AgentBubble()
        bubble.add_narration("   ")
        assert bubble.is_empty() is True

    def test_ascii_icons_on_legacy_console(self):
        from types import SimpleNamespace

        console = SimpleNamespace(encoding="cp1252")
        bubble = AgentBubble(console=console)
        bubble.add_tool_call(ToolCall(name="glob", arguments={"pattern": "*.py"}), "")
        out = bubble.render()
        assert '* Glob "*.py"' in out
        assert "\u2731" not in out

    def test_to_renderable_keeps_tool_colours(self):
        from io import StringIO

        console = Console(force_terminal=True, file=StringIO())
        bubble = AgentBubble()
        bubble.add_tool_call(ToolCall(name="glob", arguments={"pattern": "*.py"}), "")
        bubble.add_narration("Ok, checking.")
        bubble.set_answer("Done.")
        console.print(bubble.to_renderable())
        rendered = console.file.getvalue()
        assert 'Glob "*.py"' in rendered
        assert "Ok, checking." in rendered
        assert "Done." in rendered
        assert "\x1b[" in rendered  # ANSI colours survived into the bubble

    def test_print_new_blocks_renders_incrementally(self, capsys):
        console = Console(force_terminal=False)
        bubble = AgentBubble()
        bubble.add_narration("Ok, checking.")
        bubble.print_new_blocks(console)
        assert "Ok, checking." in capsys.readouterr().out

        bubble.add_tool_call(ToolCall(name="glob", arguments={"pattern": "*.py"}), "")
        bubble.print_new_blocks(console)
        out = capsys.readouterr().out
        assert '✱ Glob "*.py"' in out
        assert "Ok, checking." not in out  # only NEW blocks are printed

        bubble.set_answer("Done.")
        bubble.print_answer(console)
        assert "Done." in capsys.readouterr().out

    def test_print_pulse_uses_ascii_dot_on_legacy_console(self, capsys):
        from types import SimpleNamespace

        bubble = AgentBubble(console=SimpleNamespace(encoding="cp1252"))
        bubble.print_pulse(Console(force_terminal=False))
        out = capsys.readouterr().out
        assert "(( o ))" in out
        assert "\u25cf" not in out
