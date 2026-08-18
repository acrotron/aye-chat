"""Presenter for model-initiated tool calls.

Renders each executed tool call the way opencode does in the terminal: a
framed panel whose title is the tool name, a line describing the arguments,
and the tool output below it (truncated to keep the chat readable).
"""

from rich import box
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel

from aye.model.tool_protocol import ToolCall, describe_call

# Hard caps on how much of a tool's output is shown in the chat. The model
# still receives the full output on the API side via format_tool_results;
# these limits only affect what the user sees.
_DISPLAY_MAX_LINES = 60
_DISPLAY_MAX_CHARS = 6000


def _truncate_output(output: str) -> str:
    """Clip *output* for display, appending a notice when anything was cut."""
    lines = output.rstrip("\r\n").splitlines()
    truncated = False

    if len(lines) > _DISPLAY_MAX_LINES:
        lines = lines[:_DISPLAY_MAX_LINES]
        truncated = True

    body = "\n".join(lines)
    if len(body) > _DISPLAY_MAX_CHARS:
        cut = body[:_DISPLAY_MAX_CHARS].rsplit("\n", 1)[0]
        body = cut
        truncated = True

    if truncated:
        body = escape(body) + "\n[dim]\u2026 (output truncated)[/]"
    else:
        body = escape(body)

    return body


def present_tool_result(
    call: ToolCall,
    output: str,
    console: Console,
) -> None:
    """Print one tool call and its result inside a framed panel.

    Args:
        call: The executed tool call.
        output: The tool's textual result.
        console: Rich console to print through.
    """
    arg_line = f"[cyan]{escape(describe_call(call))}[/]"
    body_text = _truncate_output(output) or "[dim](no output)[/]"

    panel = Panel(
        Group(arg_line, "", body_text),
        title=f" {escape(call.name)} ",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    console.print(panel)