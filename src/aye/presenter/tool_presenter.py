"""Presenter for model-initiated tool calls.

File tools (read, glob, grep, write) are shown as one compact line, the way
opencode does in the terminal:

    ✱Glob "tests/test_*protocol*.py"
    →Read src/aye/controller/tool_loop.py
    ✱Grep "def foo" in src/aye/model/tools.py (7 matches)

Shell commands (bash, cmd) get the same one-liner, then their captured output
below it (truncated).

The tool loop does not print separate bubbles: every narration line and tool
entry is accumulated into an :class:`AgentBubble`, and the whole request's
activity is merged into the single assistant chat bubble together with the
final answer, the way opencode renders one agent message with tool calls
inline.
"""

import re
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from aye.model.tool_protocol import ToolCall

SHELL_TOOL_NAMES = frozenset({"bash", "cmd"})
_GLOB_TOOLS = frozenset({"glob", "grep", "web_search"})
_RESULT_PANEL_TOOLS = frozenset({"bash", "cmd"})

_GREP_COUNT_RE = re.compile(r"^\s*(\d+)\s+matches?\b", re.IGNORECASE)
_SHELL_EXCERPT_MAX = 800


def _supports_unicode(console: Optional[Console]) -> bool:
    """True when the console can print the box-drawing/glyph characters."""
    if console is None:
        return True
    encoding = (getattr(console, "encoding", "") or "").lower()
    return "utf" in encoding or encoding in ("", "utf-8")


def _icon_for(name: str, console: Optional[Console] = None) -> str:
    """Return the leading glyph opencode uses for each tool family.

    Falls back to ASCII on consoles that cannot encode the Unicode glyphs
    (e.g. legacy Windows cp1252), so tool activity never crashes on write.
    """
    if not _supports_unicode(console):
        if name in _GLOB_TOOLS:
            return "*"
        if name in SHELL_TOOL_NAMES:
            return ">"
        return "->"
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


def _line_renderable(call: ToolCall, output: str, console: Optional[Console] = None) -> Text:
    """Build the compact opencode-style line for *call* without printing."""
    icon = _icon_for(call.name, console)
    description = _describe_call(call, output)

    line = Text()
    line.append(icon, style=_line_style(call.name))
    line.append(description, style=_line_style(call.name))
    return line


def _print_call_line(call: ToolCall, output: str, console: Console) -> None:
    """Print the compact opencode-style line for *call*."""
    console.print(_line_renderable(call, output, console))


def _shell_panel(call: ToolCall, output: str) -> Panel:
    """Render a shell command's full output in a bordered panel."""
    from rich import box
    from rich.markup import escape

    return Panel(
        escape(output) or "[dim](no output)[/]",
        title=f" {escape(call.name)} ",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def _shell_excerpt(output: str) -> str:
    """Turn shell output into a compact fenced block for the chat bubble.

    Drops the leading ``$ <command>`` echo line and truncates long output.

    Args:
        output: The raw shell result text.

    Returns:
        A fenced ``text`` code block, or ``""`` when there is no output.
    """
    text = (output or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    if lines and lines[0].startswith("$ "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        return ""
    if len(body) > _SHELL_EXCERPT_MAX:
        body = body[:_SHELL_EXCERPT_MAX] + "\n... (truncated)"
    return "```text\n" + body + "\n```"


class AgentBubble:
    """Accumulate the agent's message content as a single chat bubble.

    Narration lines and tool entries are collected as plain text blocks, then
    :meth:`render` merges them with the final answer into one markdown string.
    The tool loop returns that string as the assistant summary, so the whole
    agent run appears inside the normal chat bubble — no separate tool panels.

    Args:
        console: Console the content will eventually be shown through; used to
            pick ASCII-safe glyphs on legacy Windows consoles.
    """

    def __init__(self, console: Optional[Console] = None):
        self._console = console
        self._blocks: List[str] = []
        self._answer = ""

    def add_narration(self, text: str) -> None:
        """Queue the agent's prose narration line before a tool round."""
        text = (text or "").strip()
        if text and (not self._blocks or self._blocks[-1] != text):
            self._blocks.append(text)

    def add_tool_call(self, call: ToolCall, output: str) -> None:
        """Queue a compact one-liner for *call* (non-shell tools)."""
        icon = _icon_for(call.name, self._console)
        self._blocks.append(f"{icon} {_describe_call(call, output)}")

    def add_shell_result(self, call: ToolCall, output: str) -> None:
        """Queue a shell one-liner plus its fenced output excerpt."""
        icon = _icon_for(call.name, self._console)
        line = f"{icon} {_describe_call(call, output)}"
        excerpt = _shell_excerpt(output)
        if excerpt:
            line += "\n\n" + excerpt
        self._blocks.append(line)

    def set_answer(self, text: str) -> None:
        """Set the final prose answer appended at the end of the bubble."""
        self._answer = (text or "").strip()

    def is_empty(self) -> bool:
        """True when no narration, tool line, or answer has been queued."""
        return not self._blocks and not self._answer

    def render(self) -> str:
        """Merge narration, tool entries, and the final answer into markdown."""
        blocks = list(self._blocks)
        if self._answer:
            blocks.append(self._answer)
        return "\n\n".join(b for b in blocks if b)


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

    console.print(_shell_panel(call, output))