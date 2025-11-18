from pathlib import Path
import typer
from typing import Optional

from aye.controller import commands, repl
from aye.presenter import cli_ui
from aye.presenter.diff_presenter import show_diff
from aye.model.config import load_config

# Load configuration at startup
load_config()

app = typer.Typer(help="Aye: AI‑powered coding assistant for the terminal")

# ----------------------------------------------------------------------
# Version callback
# ----------------------------------------------------------------------

def _get_package_version() -> str:
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
    version: bool = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
):
    if ctx.invoked_subcommand is None:
        typer.echo("Run 'aye --help' to see available commands.")

# ----------------------------------------------------------------------
# Auth commands
# ----------------------------------------------------------------------
auth_app = typer.Typer(help="Authentication commands")
app.add_typer(auth_app, name="auth")

@auth_app.command()
def login():
    """Configure personal access token for authenticating with the aye service."""
    try:
        commands.login_and_fetch_plugins()
    except Exception as e:
        cli_ui.print_generic_message(f"Login failed: {e}", is_error=True)

@auth_app.command()
def logout():
    """Remove the stored aye credentials."""
    commands.logout()
    cli_ui.print_generic_message("🔐 Token removed.")

@auth_app.command()
def status():
    """Show authentication status."""
    try:
        token = commands.get_auth_status_token()
        cli_ui.print_auth_status(token)
    except Exception as e:
        cli_ui.print_generic_message(f"Error checking auth status: {e}", is_error=True)

# ----------------------------------------------------------------------
# Chat (REPL) command
# ----------------------------------------------------------------------
@app.command()
def chat(
    root: Path = typer.Option(None, "--root", "-r", help="Root folder where source files are located."),
    file_mask: str = typer.Option(None, "--include", "-i", help="Include patterns for source files. Comma-separated globs."),
):
    """Start an interactive REPL."""
    # Centralized context and index preparation
    conf = commands.initialize_project_context(root, file_mask)
    repl.chat_repl(conf)

# ----------------------------------------------------------------------
# Snapshot commands
# ----------------------------------------------------------------------
snap_app = typer.Typer(help="Snapshot management commands")
app.add_typer(snap_app, name="snap")

@snap_app.command("history")
def history(file: Path = typer.Argument(None, help="File to list snapshots for")):
    """Show snapshot history for a file or all snapshots."""
    snapshots = commands.get_snapshot_history(file)
    cli_ui.print_snapshot_history(snapshots)

@snap_app.command("show")
def show_snap(file: Path = typer.Argument(..., help="File whose snapshot to show"), ordinal: str = typer.Argument(..., help="Snapshot ID (e.g., 001)")):
    """Print the contents of a specific snapshot."""
    content = commands.get_snapshot_content(file, ordinal)
    cli_ui.print_snapshot_content(content)

@snap_app.command("restore")
def restore(ordinal: str = typer.Argument(None, help="Snapshot ID to restore (default: latest)"), file_name: str = typer.Argument(None, help="Specific file to restore")):
    """Restore files from a snapshot."""
    try:
        commands.restore_from_snapshot(ordinal, file_name)
        cli_ui.print_restore_feedback(ordinal, file_name)
    except Exception as exc:
        cli_ui.print_generic_message(f"Error: {exc}", is_error=True)

@snap_app.command("keep")
def keep(num: int = typer.Option(10, "--num", "-n", help="Number of recent snapshots to keep")):
    """Delete all but the most recent N snapshots."""
    try:
        deleted_count = commands.prune_snapshots(num)
        cli_ui.print_prune_feedback(deleted_count, num)
    except Exception as e:
        cli_ui.print_generic_message(f"Error pruning snapshots: {e}", is_error=True)

@snap_app.command()
def cleanup(days: int = typer.Option(30, "--days", "-d", help="Delete snapshots older than N days")):
    """Delete snapshots older than N days."""
    try:
        deleted_count = commands.cleanup_old_snapshots(days)
        cli_ui.print_cleanup_feedback(deleted_count, days)
    except Exception as e:
        cli_ui.print_generic_message(f"Error cleaning up snapshots: {e}", is_error=True)

# ----------------------------------------------------------------------
# Config commands
# ----------------------------------------------------------------------
@app.command()
def config(
    action: str = typer.Argument(..., help="Action: list, get, set, delete"),
    key: str = typer.Argument(None, help="Configuration key"),
    value: str = typer.Argument(None, help="Configuration value (for set)"),
):
    """Manage local configuration."""
    try:
        if action == "list":
            cfg = commands.get_all_config()
            cli_ui.print_config_list(cfg)
        elif action == "get":
            if not key: raise typer.BadParameter("Key is required for get action.")
            val = commands.get_config_value(key)
            cli_ui.print_config_value(key, val)
        elif action == "set":
            if not key or value is None: raise typer.BadParameter("Key and value are required for set action.")
            commands.set_config_value(key, value)
            cli_ui.print_generic_message(f"Configuration '{key}' set.")
        elif action == "delete":
            if not key: raise typer.BadParameter("Key is required for delete action.")
            if commands.delete_config_value(key):
                cli_ui.print_generic_message(f"Configuration '{key}' deleted.")
            else:
                cli_ui.print_generic_message(f"Configuration key '{key}' not found.", is_error=True)
        else:
            raise typer.BadParameter(f"Invalid action '{action}'. Use: list, get, set, delete")
    except Exception as e:
        cli_ui.print_generic_message(str(e), is_error=True)
        raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
