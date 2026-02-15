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
- The assistant response is rendered as Markdown so the assistant can return structured
  output (headers, lists, code fences) and have it display cleanly.
"""

from typing import Optional

from rich import box
from rich.panel import Panel
from rich.padding import Padding
from rich.console import Console
from rich.theme import Theme
from rich.markdown import Markdown
from rich.table import Table

# Updated some colors of specific elements to make those elements more legible on a dark background
deep_ocean_theme = Theme({
    # Markdown mappings
    "markdown.h1": "bold cornflower_blue",
    "markdown.h1.border": "bold cornflower_blue",
    "markdown.h2": "bold deep_sky_blue1",
    "markdown.h3": "bold turquoise2",
    "markdown.strong": "bold light_steel_blue",
    "markdown.em": "italic orchid1",
    "markdown.code": "bold sky_blue3",
    "markdown.block_quote": "dim slate_blue3",
    "markdown.list": "steel_blue",
    "markdown.item": "steel_blue",
    "markdown.item.bullet": "bold yellow",  # Bullets and numbers
    "markdown.item.number": "bold yellow",  # Ordered list numbers
    "markdown.link": "underline aquamarine1",
    "markdown.link_url": "underline aquamarine1",

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
    "ui.stall_spinner": "dim yellow",
})

# Shared console used by the REPL.
console = Console(force_terminal=True, theme=deep_ocean_theme)


# ---------------------------------------------------------------------------
# Last assistant response capture
# ---------------------------------------------------------------------------
# Stored whenever the assistant produces a summary so the
# ``printraw`` / ``raw`` command can re-emit it as plain text.
_last_assistant_response: Optional[str] = None


def set_last_assistant_response(text: Optional[str]) -> None:
    """Set the most recent assistant response text.

    This is intentionally separate from printing so we can capture summaries
    even when they were rendered elsewhere (e.g., streaming UI).
    """
    global _last_assistant_response
    _last_assistant_response = text


def get_last_assistant_response() -> Optional[str]:
    """Return the text of the most recent assistant response, or None."""
    return _last_assistant_response


def print_welcome_message():
    """Display the welcome message for the Aye Chat REPL."""
    console.print("Aye Chat \u2013 type `help` for available commands, `exit` or Ctrl+D to quit", style="ui.welcome")


def print_help_message():
    """Print a compact help message listing built-in chat commands."""
    console.print("Available chat commands:", style="ui.help.header")
    console.print()

    commands = [
        ("Snapshot & Undo", ""),
        (r"  restore, undo \[id] \[file]", "Revert changes to the last state, a specific snapshot `id`, or for a single `file`."),
        ("  history", "Show snapshot history"),
        (r"  diff <file> \[snapshot_id]", "Show diff of file with the latest snapshot, or a specified snapshot"),
        ("  keep [N]", "Keep only N most recent snapshots (10 by default)"),
        ("", ""),

        ("Prompt Context & Augmentation", ""),
        ("  @filename", "Include a file in your prompt inline (e.g., \"explain @main.py\"). Supports wildcards (e.g., @*.py, @src/*.js)."),
        (r"  shellcap \[none|fail|all]", "Shell output capture: 'none' (default), 'fail' (failing commands), or 'all' (all commands)"),
        # skills: multi-line
        ("  skills", "Apply repo-local skills from the nearest (non-ignored) `skills/` directory found by walking upward. "),
        ("", "Explicit forms: `skill:foo`, `skill foo`, `foo skill`, `skills:foo,bar` (order preserved, duplicates deduped). "),
        ("", "If you mention 'skill'/'skills' without `skill:`/`skills:`, Aye may fuzzy-match phrases like `using <X> skill`."),
        # end of skills
        ("", ""),

        ("Session & Model", ""),
        ("  new", "Start a new chat session (if you want to change the subject)"),
        ("  model", "Select a different model. Selection will persist between sessions."),
        ("  llm", "Configure OpenAI-compatible LLM endpoint (URL, key, model). Use 'llm clear' to reset."),
        ("", ""),

        ("Display & Preferences", ""),
        (r"  verbose \[on|off]", "Toggle verbose mode to increase or decrease chattiness (on/off, persists between sessions)"),
        (r"  autodiff \[on|off]", "Toggle automatic diff display after LLM file updates (off by default, persists between sessions)"),
        (r"  completion \[readline|multi]", "Switch auto-completion style (readline or multi, persists between sessions)"),
        ("", ""),

        ("Utilities", ""),
        ("  raw / printraw", "Reprint last assistant response as plain text (copy-friendly)"),
        ("  !command", "Force shell execution (e.g., \"!echo hello\")."),
        ("", ""),

        ("Exit & Help", ""),
        ("  exit, quit, Ctrl+D", "Exit the chat session"),
        ("  help", "Show this help message"),
    ]

    for cmd, desc in commands:
        #console.print(f"  [ui.help.command]{cmd:<28}[/]\t- [ui.help.text]{desc}[/]")
        sep = '-' if cmd and desc else ' '
        console.print(f"  [ui.help.command]{cmd:<28}[/]\t{sep} [ui.help.text]{desc}[/]")

    console.print("")
    console.print("By default, relevant files are found using code lookup to provide context for your prompt.", style="ui.warning")


def print_prompt():
    """Return the prompt symbol for user input."""
    return "(\u30c4\u00bb "


def print_assistant_response(summary: str):
    """Render the assistant's response as Markdown inside a styled panel."""
    set_last_assistant_response(summary)

    console.print()

    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]\u25cf[/] [ui.response_symbol.waves]))[/]"

    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    grid.add_row(pulse, Markdown(summary))

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
    """Display message when no files were changed."""
    if not getattr(console_arg, "theme", None):
        console.print(Padding("[ui.warning]No files were changed.[/]", (0, 4, 0, 4)))
    else:
        console_arg.print(Padding("[ui.warning]No files were changed.[/]", (0, 4, 0, 4)))


def print_files_updated(console_arg: Console, file_names: list):
    """Display message about updated files."""
    text = f"[ui.success]Files updated:[/] [ui.help.text]{','.join(file_names)}[/]"
    if not getattr(console_arg, "theme", None):
        console.print(Padding(text, (0, 4, 0, 4)))
    else:
        console_arg.print(Padding(text, (0, 4, 0, 4)))


def print_error(exc: Exception):
    """Display a generic error message."""
    console.print(f"[ui.error]Error:[/] {exc}")
