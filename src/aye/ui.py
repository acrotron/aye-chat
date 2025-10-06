from rich import print as rprint
from rich.padding import Padding
from rich.console import Console
from rich.spinner import Spinner
from rich import print as rprint


def print_welcome_message():
    """Display the welcome message for the Aye Chat."""
    rprint("[bold cyan]Aye Chat – type `help` for available commands, `exit` or Ctrl‑D to quit[/]")


def print_help_message():
    rprint("[bold]Available chat commands:[/]")
    rprint("  exit, quit               - Exit the chat session")
    rprint("  history                  - Show snapshot history")
    rprint("  restore, revert          - Restore latest snapshot")
    rprint("  diff `[file`] `[snapshot`]   - Show diff of file with snapshot")
    rprint("  keep [N]                 - Keep only N most recent snapshots (10 by default)")
    rprint("  new                      - Start a new chat session")
    rprint("  help                     - Show this help message")
    rprint("")
    #rprint("Shell commands (e.g., ls, git) are also supported without the leading slash.")
    rprint("[yellow]If the first word does not match chat or shell command, entire prompt will be sent to LLM for response[/]")
    rprint("[yellow]Multiple comma-separated file masks are supported (e.g., \"*.py,*.js\").[/]")


def print_prompt():
    """Display the prompt symbol for user input."""
    return "(ツ» "


def print_thinking_spinner(console: Console) -> Spinner:
    """Create and return a thinking spinner."""
    return Spinner("dots", text="[yellow]Thinking...[/]")


def print_assistant_response(summary: str):
    """Display the assistant's response summary."""
    rprint()
    color = "rgb(170,170,170)"
    bot_face = "-{•!•}-"
    rprint(f"[{color}]{bot_face} » {summary}[/]")
    rprint()


def print_no_files_changed(console: Console):
    """Display message when no files were changed."""
    console.print(Padding("[yellow]No files were changed.[/]", (0, 4, 0, 4)))


def print_files_updated(console: Console, file_names: list):
    """Display message about updated files."""
    console.print(Padding(f"[green]Files updated:[/] {','.join(file_names)}", (0, 4, 0, 4)))


def print_error(exc: Exception):
    """Display a generic error message."""
    rprint(f"[red]Error:[/] {exc}")
