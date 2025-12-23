"""REPL UI presenter for Aye Chat.

This module contains small, purpose-built printing helpers used by the
interactive chat (REPL) interface.

Responsibilities:
- Define a Rich `Theme` for consistent colors across help/warnings/errors.
- Provide helper functions that render:
  - welcome/help messages
  - the prompt symbol
  - an assistant response block (Markdown inside a Panel)
  - status messages after file operations

Design notes:
- Most strings use Rich markup tags like `[ui.success]...[/]`.
  These tags refer to keys in `deep_ocean_theme`.
- The assistant response is rendered as Markdown so that bullet lists, code
  blocks, headers, etc. display nicely in the terminal.
"""

from rich import box
from rich.panel import Panel
from rich import print as rprint
from rich.padding import Padding
from rich.console import Console
from rich.spinner import Spinner
from rich.theme import Theme
from rich.markdown import Markdown
from rich.table import Table

# Theme used by the REPL.
#
# This is duplicated (or mirrored) across other presenter modules so that the
# CLI and REPL look consistent.
#
# The `markdown.*` keys are used by Rich's Markdown renderer. The `ui.*` keys are
# used by our own markup in print_* helpers below.
deep_ocean_theme = Theme({
    # Markdown mappings
    "markdown.h1": "bold cornflower_blue",
    "markdown.h1.border": "bold cornflower_blue",
    "markdown.h2": "bold slate_blue1",
    "markdown.h3": "bold dodger_blue2",
    "markdown.strong": "bold light_steel_blue",
    "markdown.em": "italic slate_blue1",
    "markdown.code": "bold sky_blue3",
    "markdown.block_quote": "dim slate_blue3",
    "markdown.list": "steel_blue",
    "markdown.item": "steel_blue",
    "markdown.item.bullet": "bold yellow",  # Bullets and numbers
    "markdown.item.number": "bold yellow",  # Ordered list numbers
    "markdown.link": "underline dodger_blue3",
    "markdown.link_url": "underline dodger_blue3",

    # Custom UI mappings
    "ui.welcome": "bold cornflower_blue",
    "ui.help.header": "bold deep_sky_blue1",
    "ui.help.command": "bold sky_blue3",
    "ui.help.text": "steel_blue",
    "ui.response_symbol.name": "bold cornflower_blue",
    "ui.response_symbol.waves": "steel_blue",
    "ui.response_symbol.pulse": "bold pale_turquoise1",
    "ui.success": "bold sea_green2",
    "ui.warning": "bold khaki1",
    "ui.error": "bold indian_red1",
    "ui.border": "dim slate_blue3",
})

# Shared console used by the REPL.
#
# Using a single Console instance guarantees a consistent theme and makes it
# easier to adjust output behavior in one place.
console = Console(theme=deep_ocean_theme)


def print_welcome_message():
    """Display the welcome message for the Aye Chat REPL."""
    # Kept intentionally short; REPL should get to the prompt quickly.
    console.print("Aye Chat – type `help` for available commands, `exit` or Ctrl+D to quit", style="ui.welcome")


def print_help_message():
    """Print a compact help message listing built-in chat commands.

    The help list is kept here (instead of sprinkled throughout command code)
    so it stays easy to update and visually consistent.
    """
    console.print("Available chat commands:", style="ui.help.header")

    # Commands are rendered as a fixed-width left column to keep descriptions
    # aligned and easy to scan.
    commands = [
        ("@filename", "Include a file in your prompt inline (e.g., \"explain @main.py\"). Supports wildcards (e.g., @*.py, @src/*.js)."),
        ("model", "Select a different model. Selection will persist between sessions."),
        ("verbose [on|off]", "Toggle verbose mode to increase or decrease chattiness (on/off, persists between sessions)"),
        ("completion [readline|multi]", "Switch auto-completion style (readline or multi, persists between sessions)"),
        ("new", "Start a new chat session (if you want to change the subject)"),
        ("history", "Show snapshot history"),
        ("diff <file> [snapshot_id]", "Show diff of file with the latest snapshot, or a specified snapshot"),
        ("restore, undo [id] [file]", "Revert changes to the last state, a specific snapshot `id`, or for a single `file`."),
        ("keep [N]", "Keep only N most recent snapshots (10 by default)"),
        ("exit, quit, Ctrl+D", "Exit the chat session"),
        ("help", "Show this help message"),
    ]

    for cmd, desc in commands:
        console.print(f"  [ui.help.command]{cmd:<28}[/] - [ui.help.text]{desc}[/]")

    console.print("")
    # This line reminds users that Aye Chat does context retrieval automatically.
    console.print("By default, relevant files are found using code lookup to provide context for your prompt.", style="ui.warning")


def print_prompt():
    """Return the prompt symbol for user input.

    The caller is responsible for actually writing/reading input; this helper
    just centralizes the prompt string so it can be changed in one place.
    """
    return "(ツ» "


def print_assistant_response(summary: str):
    """Render the assistant's response.

    The response is rendered as Markdown so the assistant can return structured
    output (headers, lists, code fences) and have it display cleanly.

    Layout approach:
    - A small left-side "pulse" marker is shown to visually separate assistant
      output from the user's input.
    - A grid is used so the marker and the Markdown body align nicely.
    - The whole thing is wrapped in a Panel for a consistent bordered block.
    """
    console.print()

    # Decorative "sonar pulse" marker to hint "assistant speaking".
    # This is purely visual and intentionally small.
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]●[/] [ui.response_symbol.waves]))[/]"

    # A 2-column grid: marker + Markdown body.
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    grid.add_row(pulse, Markdown(summary))

    # Wrap the response in a rounded panel to make it stand out from surrounding
    # terminal output.
    resonse_with_layout = Panel(
        grid,
        border_style="ui.border",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )

    console.print()
    console.print(resonse_with_layout)
    console.print()


def print_no_files_changed(console_arg: Console):
    """Display message when no files were changed.

    Some call sites may pass their own Console instance (e.g. different capture
    settings). We try to use that console when possible.

    If the passed Console does not have theme information (or is not a fully
    configured Rich Console), we fall back to the global `console` defined in
    this module.
    """
    # Attempt to use the passed console, but if it lacks theme, use global.
    if not getattr(console_arg, "theme", None):
        console.print(Padding("[ui.warning]No files were changed.[/]", (0, 4, 0, 4)))
    else:
        console_arg.print(Padding("[ui.warning]No files were changed.[/]", (0, 4, 0, 4)))


def print_files_updated(console_arg: Console, file_names: list):
    """Display message about updated files.

    Args:
        console_arg: Console to print to (theme-aware when available).
        file_names: List of file paths/names that were written/updated.

    Output is padded to visually separate status messages from chat output.
    """
    text = f"[ui.success]Files updated:[/] [ui.help.text]{','.join(file_names)}[/]"
    if not getattr(console_arg, "theme", None):
        console.print(Padding(text, (0, 4, 0, 4)))
    else:
        console_arg.print(Padding(text, (0, 4, 0, 4)))


def print_error(exc: Exception):
    """Display a generic error message.

    This should be used for unexpected exceptions that reach the UI layer.
    The goal is to provide a readable message without crashing the REPL.
    """
    console.print(f"[ui.error]Error:[/] {exc}")
