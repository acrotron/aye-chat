from rich import print as rprint
from rich.padding import Padding
from rich.console import Console
from rich.spinner import Spinner


def print_welcome_message():
    """Display the welcome message for the Aye Chat."""
    rprint("[bold cyan]Aye Chat – type `help` for available commands, `exit` or Ctrl+D to quit[/]")


def print_help_message():
    rprint("[bold]Available chat commands:[/]")
    print("  with <files> : <prompt>      - Run prompt with specified files in context, skipping code lookup.")
    print("  model                        - Select a different model. Selection will persist between sessions.")
    print("  verbose [on|off]             - Toggle verbose mode to increase or decrease chattiness (on/off, persists between sessions)")
    print("  new                          - Start a new chat session (if you want to change the subject)")
    print("  history                      - Show snapshot history")
    print("  diff <file> [snapshot_id]    - Show diff of file with the latest snapshot, or a specified snapshot")
    print("  restore, undo [id] [file]    - Revert changes to the last state, a specific snapshot `id`, or for a single `file`.")
    print("  keep [N]                     - Keep only N most recent snapshots (10 by default)")
    print("  exit, quit, Ctrl+D           - Exit the chat session")
    print("  help                         - Show this help message")
    print("")
    rprint("[yellow]By default, relevant files are found using code lookup to provide context for your prompt.[/]")


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
