from rich import print as rprint
from typing import List, Dict, Any, Optional
from pathlib import Path


def print_auth_status(token: Optional[str]) -> None:
    """Show authentication status."""
    if token and not token.startswith("aye_demo_"):
        # Real token exists
        rprint("[green]Authenticated[/] - Token is saved")
        rprint(f"  Token: {token[:12]}...")
    elif token and token.startswith("aye_demo_"):
        # Demo token
        rprint("[yellow]Demo Mode[/] - Using demo token")
        rprint("  Run 'aye auth login' to authenticate with a real token")
    else:
        # No token
        rprint("[red]Not Authenticated[/] - No token saved")
        rprint("  Run 'aye auth login' to authenticate")


def print_snapshot_history(snapshots: List[str]) -> None:
    """Show timestamps of saved snapshots."""
    if not snapshots:
        rprint("[yellow]No snapshots found.[/]")
        return
    rprint("[bold]Snapshot History:[/]")
    for snapshot in snapshots:
        rprint(f"  {snapshot}")


def print_snapshot_content(content: Optional[str]) -> None:
    """Print the contents of a specific snapshot."""
    if content is not None:
        print(content)
    else:
        rprint("Snapshot not found.", err=True)


def print_restore_feedback(ts: Optional[str], file_name: Optional[str]) -> None:
    """Print feedback after a restore operation."""
    if ts:
        if file_name:
            rprint(f"✅ File '{file_name}' restored to {ts}")
        else:
            rprint(f"✅ All files restored to {ts}")
    else:
        if file_name:
            rprint(f"✅ File '{file_name}' restored to latest snapshot")
        else:
            rprint("✅ All files restored to latest snapshot")

def print_prune_feedback(deleted_count: int, keep: int) -> None:
    """Print feedback after a prune operation."""
    if deleted_count > 0:
        rprint(f"✅ {deleted_count} snapshots deleted. {keep} most recent snapshots kept.")
    else:
        rprint("✅ No snapshots deleted. You have fewer than the specified keep count.")


def print_cleanup_feedback(deleted_count: int, days: int) -> None:
    """Print feedback after a cleanup operation."""
    if deleted_count > 0:
        rprint(f"✅ {deleted_count} snapshots older than {days} days deleted.")
    else:
        rprint(f"✅ No snapshots older than {days} days found.")

def print_config_list(config: Dict[str, Any]) -> None:
    """List all configuration values."""
    if not config:
        rprint("[yellow]No configuration values set.[/]")
        return
    
    rprint("[bold]Current Configuration:[/]")
    for key, value in config.items():
        rprint(f"  {key}: {value}")

def print_config_value(key: str, value: Any) -> None:
    """Get a configuration value."""
    if value is None:
        rprint(f"[yellow]Configuration key '{key}' not found.[/]")
    else:
        rprint(f"{key}: {value}")

def print_generic_message(message: str, is_error: bool = False) -> None:
    """Prints a generic message, optionally styled as an error."""
    if is_error:
        rprint(f"[red]{message}[/]")
    else:
        rprint(f"[green]{message}[/]")
