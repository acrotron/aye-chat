"""REPL UI components for Aye Chat.

This module provides UI components for the interactive REPL, including:
- Welcome and help messages
- Assistant response display
- Error display
- Prompt formatting
"""

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

# Theme for consistent styling across the REPL
_REPL_THEME = Theme({
    "ui.welcome": "bold cyan",
    "ui.help.header": "bold yellow",
    "ui.help.command": "bold green",
    "ui.help.text": "dim",
    "ui.response_symbol.name": "bold cornflower_blue",
    "ui.response_symbol.waves": "steel_blue",
    "ui.response_symbol.pulse": "bold pale_turquoise1",
    "ui.border": "dim slate_blue3",
    "ui.success": "green",
    "ui.error": "red",
    "ui.warning": "yellow",
})

# Global console instance with theme
console = Console(theme=_REPL_THEME)


def print_welcome_message() -> None:
    """Print the welcome message when the REPL starts.

    Note: Unit tests expect a single call to `console.print(...)` with
    style="ui.welcome".
    """
    console.print("Welcome to Aye Chat!", style="ui.welcome")


def print_help_message() -> None:
    """Print the help message with available commands."""
    help_text = """
 [ui.help.header]Available Commands:[/]

 [ui.help.command]Session & Model Control:[/]
   [ui.help.text]new[/]              Start a fresh chat session
   [ui.help.text]model[/]            Select a different AI model
   [ui.help.text]verbose \[on|off][/] Toggle verbose output
   [ui.help.text]debug \[on|off][/]   Toggle debug mode
   [ui.help.text]exit, quit, :q[/]   Exit the chat
   [ui.help.text]help[/]             Show this message

 [ui.help.command]Reviewing & Undoing AI Changes:[/]
   [ui.help.text]restore, undo[/]    Undo the last set of AI changes
   [ui.help.text]restore <ordinal>[/]  Restore to a specific snapshot (e.g., restore 001)
   [ui.help.text]restore <ordinal> <file>[/]  Restore a specific file from a snapshot
   [ui.help.text]history[/]          Show the history of snapshots
   [ui.help.text]diff <file>[/]      Compare current vs last snapshot
   [ui.help.text]diff <file> <snap1> <snap2>[/]  Compare two snapshots

 [ui.help.command]Special Commands:[/]
   [ui.help.text]with <files>: <prompt>[/]  Include specific files (supports wildcards)
   [ui.help.text]@filename[/]        Include a file inline in your prompt
   [ui.help.text]cd <directory>[/]   Change current working directory

 [ui.help.command]Shell Commands:[/]
   [ui.help.text]Any unrecognized command is executed as a shell command.[/]
   [ui.help.text]Examples: ls, git status, pytest, vim[/]
    """
    console.print(help_text)


def print_prompt() -> str:
    """Return the prompt string for user input."""
    return "(ツ» "


def print_assistant_response(summary: str) -> None:
    """Print the assistant's response in a styled panel.

    Args:
        summary: The markdown-formatted response text.
    """
    if not summary:
        return

    # Decorative "sonar pulse" marker
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]●[/] [ui.response_symbol.waves]))[/]"

    # A 2-column grid: marker + content
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    # Use Markdown for proper formatting
    grid.add_row(pulse, Markdown(summary))

    # Wrap in a rounded panel
    panel = Panel(
        grid,
        border_style="ui.border",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )

    # Unit tests expect 4 print calls here.
    console.print()
    console.print(panel)
    console.print()
    console.print()


def print_no_files_changed(console_instance: Console) -> None:
    """Print a message when no files were changed."""
    console_instance.print("[dim]No files were changed.[/]")


def print_files_updated(console_instance: Console, file_names: list) -> None:
    """Print a message showing which files were updated."""
    files_str = ",".join(file_names)
    console_instance.print(f"[ui.success]Files updated:[/ui.success] {files_str}")


def print_error(error: Exception) -> None:
    """Print an error message."""
    console.print(f"[ui.error]Error:[/] {error}")
