from pathlib import Path
import typer

from .service import (
    handle_login,
    handle_logout,
    handle_generate_cmd,
    handle_chat,
    handle_history_cmd,
    handle_snap_show_cmd,
    handle_restore_cmd,
    handle_prune_cmd,
    handle_cleanup_cmd,
    handle_config_list,
    handle_config_set,
    handle_config_get,
    handle_config_delete,
)

from .config import load_config

# Load configuration at startup
load_config()

app = typer.Typer(help="Aye: AI‑powered coding assistant for the terminal")

# ----------------------------------------------------------------------
# Version callback (retrieved from package metadata)
# ----------------------------------------------------------------------

def _get_package_version() -> str:
    """Return the installed package version using importlib.metadata.
    Falls back to "0.0.0" if the package metadata cannot be found.
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
        return version("ayechat")
    except (ImportError, PackageNotFoundError):
        return "0.0.0"


def _version_callback(value: bool):
    if value:
        typer.echo(_get_package_version())
        raise typer.Exit()

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
):
    """Root callback to handle global options like --version.
    If no sub‑command is provided, show a short hint directing the user to the help output.
    """
    if ctx.invoked_subcommand is None:
        # No command was supplied – give a friendly hint.
        typer.echo("Run 'aye --help' to see available commands.")
    # No further action needed – commands are added below.
    return

# Create subcommands
auth_app = typer.Typer(help="Authentication commands")
app.add_typer(auth_app, name="auth")
# ----------------------------------------------------------------------
# Authentication commands
# ----------------------------------------------------------------------
@auth_app.command()
def login():
    """
    Configure personal access token for authenticating with the aye service.
    """
    handle_login()

@auth_app.command()
def logout():
    """
    Remove the stored aye credentials.
    
    Examples: \n
    aye auth logout
    """
    handle_logout()

# ----------------------------------------------------------------------
# Interactive REPL (chat) command
# ----------------------------------------------------------------------
@app.command()
def chat(
    root: Path = typer.Option(
        None, "--root", "-r", help="Root folder where source files are located."
    ),
    file_mask: str = typer.Option(
        None, "--include", "-i", help="Include patterns for source files to include into generation. Comma-separated globs are allowed."
    ),
):
    """
    Start an interactive REPL. Use exit or Ctrl‑D to leave.
    
    Examples: \n
    aye chat \n
    aye chat --root ./src \n
    aye chat --include "*.js,*.html" --root ./frontend \n
    """
    handle_chat(root, file_mask)

# ----------------------------------------------------------------------
# Snapshot commands
# ----------------------------------------------------------------------
snap_app = typer.Typer(help="Snapshot management commands (EXPERIMENTAL)")
app.add_typer(snap_app, name="snap")

@snap_app.command("history")
def history(
    file: Path = typer.Argument(None, help="File to list snapshots for")
):
    """
    Show timestamps of saved snapshots for *file* or all snapshots if no file provided.
    
    Examples: \n
    aye snap history \n
    aye snap history src/main.py \n
    """
    handle_history_cmd(file)

@snap_app.command("show")
def show(
    file: Path = typer.Argument(..., help="File whose snapshot to show"),
    ordinal: str = typer.Argument(..., help="Snapshot ID of the snapshot (e.g., 001)"),
):
    """
    Print the contents of a specific snapshot.
    
    Examples: \n
    aye snap show src/main.py 001 \n
    """
    handle_snap_show_cmd(file, ordinal)

@snap_app.command("restore")
def restore(
    ordinal: str = typer.Argument(None, help="Snapshot ID of the snapshot to restore (e.g., 001, default: latest)"),
    file_name: str = typer.Argument(None, help="Specific file to restore from the snapshot"),
):
    """
    Replace all files with the latest snapshot or specified snapshot by snapshot ID.
    If file_name is provided, only that file is restored.
    
    Examples:\n
    aye snap restore \n
    aye snap restore 001 \n
    aye snap restore 001 myfile.py \n
    """
    handle_restore_cmd(ordinal, file_name)

# ----------------------------------------------------------------------
# Snapshot cleanup/pruning commands
# ----------------------------------------------------------------------
@snap_app.command()
def keep(
    num: int = typer.Option(10, "--num", "-n", help="Number of recent snapshots to keep (default: 10)"),
):
    """
    Delete all but the most recent N snapshots.
    
    Examples: \n
    aye snap keep \n
    aye snap keep --num 5 \n
    aye snap keep -n 3 \n
    """
    handle_prune_cmd(num)

@snap_app.command()
def cleanup(
    days: int = typer.Option(30, "--days", "-d", help="Delete snapshots older than N days (default: 30)"),
):
    """
    Delete snapshots older than N days.
    
    Examples: \n
    aye snap cleanup \n
    aye snap cleanup --days 7 \n
    aye snap cleanup -d 14 \n
    """
    handle_cleanup_cmd(days)

# ----------------------------------------------------------------------
# One‑shot generation
# ----------------------------------------------------------------------
#@app.command()
def generate(
    prompt: str = typer.Argument(..., help="Prompt for the LLM"),
    mode: str = typer.Option(
        "replace",
        "--mode",
        "-m",
        help="replace | append | insert (default: replace)",
    ),
):
    """
    Send a single prompt to the backend.
    
    Examples: \n
    aye generate "Create a function that reverses a string" \n
    aye generate "Add type hints to this function" --mode append \n
    """
    handle_generate_cmd(prompt, mode)

# ----------------------------------------------------------------------
# Configuration management commands
# ----------------------------------------------------------------------
@app.command()
def config(
    action: str = typer.Argument(..., help="Action to perform: list, get, set, delete"),
    key: str = typer.Argument(None, help="Configuration key"),
    value: str = typer.Argument(None, help="Configuration value (for set action)"),
):
    """
    Manage configuration values for file masks, root directories, and other settings. (EXPERIMENTAL)
    
    Actions: \n
    - list: Show all configuration values \n
    - get: Retrieve a specific configuration value \n
    - set: Set a configuration value \n
    - delete: Remove a configuration value \n
    
    Examples: \n
    aye config list \n
    aye config get file_mask \n
    aye config set file_mask "*.py,*.js" \n
    aye config delete file_mask \n
    """
    if action == "list":
        handle_config_list()
    elif action == "get":
        if not key:
            typer.echo("[red]Error:[/] Key is required for get action.")
            raise typer.Exit(code=1)
        handle_config_get(key)
    elif action == "set":
        if not key or not value:
            typer.echo("[red]Error:[/] Key and value are required for set action.")
            raise typer.Exit(code=1)
        handle_config_set(key, value)
    elif action == "delete":
        if not key:
            typer.echo("[red]Error:[/] Key is required for delete action.")
            raise typer.Exit(code=1)
        handle_config_delete(key)
    else:
        typer.echo(f"[red]Error:[/] Invalid action '{action}'. Use: list, get, set, delete")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
