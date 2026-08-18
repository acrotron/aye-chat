"""Presenter for model-initiated tool calls.

File tools (read, glob, grep, write) are shown as one compact line, the way
opencode does in the terminal:

    ✱Glob "tests/test_*protocol*.py"
    →Read src/aye/controller/tool_loop.py
    ✱Grep "def foo" in src/aye/model/tools.py (7 matches)

Shell commands (bash, cmd) get the same one-liner, then their captured output
below it in a bordered panel (the output is shown in full, markup-escaped).
"""

import re
from typing import Any, Dict, Optional

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from aye.model.tool_protocol import ToolCall

SHELL_TOOL_NAMES = frozenset({"bash", "cmd"})
_GLOB_TOOLS = frozenset({"glob", "grep", "web_search"})
_RESULT_PANEL_TOOLS = frozenset({"bash", "cmd"})

_GREP_COUNT_RE = re.compile(r"^\s*(\d+)\s+matches?\b", re.IGNORECASE)


def _icon_for(name: str) -> str:
    """Return the leading glyph opencode uses for each tool family."""
    if name in _GLOB_TOOLS:
        return "\u2731"  # ✱
    if name in SHELL_TOOL_NAMES:
        return "\u23fa"  # ⏺
    return "\u2192"  # →


def _grep_count(output: str) -> Optional[str]:
    """Extract the ``(N matches)`` summary from grep output when available."""
    match = _GREP_COUNT_RE.match(output)
    if match:
        return f"({match.group(1)} matches)"
    if output.startswith("No matches"):
        return "(no matches)"
    return None


def _describe_call(call: ToolCall, output: str) -> str:
    """Render a call the way opencode does, e.g. ``Read path [limit=3]``."""
    name = call.name
    args: Dict[str, Any] = call.arguments

    if name == "glob":
        return f'Glob "{args.get("pattern", "")}"'

    if name == "web_search":
        parts = [f'Web_search "{args.get("query", "")}"']
        max_results = args.get("max_results")
        if max_results not in (None, ""):
            parts.append(f"({max_results})")
        return " ".join(parts)

    if name == "grep":
        parts = [f'Grep "{args.get("pattern", "")}"']
        include = args.get("include")
        if include:
            parts.append(f"in {include}")
        count = _grep_count(output)
        if count:
            parts.append(count)
        return " ".join(parts)

    if name == "read":
        parts = [f"Read {args.get('path', '')}"]
        options = []
        if args.get("start") not in (None, ""):
            options.append(f"start={args['start']}")
        if args.get("limit") not in (None, ""):
            options.append(f"limit={args['limit']}")
        if options:
            parts.append("[" + ", ".join(options) + "]")
        return " ".join(parts)

    if name == "write":
        return f"Write {args.get('path', '')}"

    if name in SHELL_TOOL_NAMES:
        command = str(args.get("command", "") or "").replace("\n", " ").strip()
        return f"{name} {command}"

    return f"{name} {args}"


def _line_style(name: str) -> str:
    """Colour for the one-line tool entry per family."""
    if name in _GLOB_TOOLS:
        return "bold yellow"
    if name == "read":
        return "bold green"
    if name == "write":
        return "bold magenta"
    return "bold cyan"


def _print_call_line(call: ToolCall, output: str, console: Console) -> None:
    """Print the compact opencode-style line for *call*."""
    icon = _icon_for(call.name)
    description = _describe_call(call, output)

    line = Text()
    line.append(icon, style=_line_style(call.name))
    line.append(description, style=_line_style(call.name))
    console.print(line)


def present_tool_call(call: ToolCall, console: Console) -> None:
    """Print a tool's one-liner before it runs (shell commands).

    Args:
        call: The tool call about to execute.
        console: Rich console to print through.
    """
    _print_call_line(call, "", console)


def present_tool_result(call: ToolCall, output: str, console: Console) -> None:
    """Show a finished tool call.

    File tools only get their compact line (the grep one-liner carries the
    match count). Shell commands also render their full output in a bordered
    panel under the line.

    Args:
        call: The executed tool call.
        output: The tool's textual result.
        console: Rich console to print through.
    """
    if call.name not in _RESULT_PANEL_TOOLS:
        _print_call_line(call, output, console)
        return

    panel = Panel(
        escape(output) or "[dim](no output)[/]",
        title=f" {escape(call.name)} ",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    console.print(panel)