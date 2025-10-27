import json
import subprocess
import re
from rich import print as rprint
from pathlib import Path
from rich.console import Console
from types import SimpleNamespace

from typing import Optional, List, Dict

from .download_plugins import fetch_plugins
from .auth import get_token, login_flow, delete_token
from .api import cli_invoke
from .source_collector import collect_sources
from .snapshot import restore_snapshot, list_snapshots, create_snapshot, apply_updates
from .snapshot import prune_snapshots, cleanup_snapshots
from .config import get_value, set_value, delete_value, list_config
from .ui import (
    print_assistant_response,
    print_no_files_changed,
    print_files_updated,
    print_error
)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")


# Create a global console instance for diff output
_diff_console = Console(force_terminal=True, markup=False, color_system="standard")

# Authentication functions (from auth.py)
def handle_login() -> None:
    """Configure username and token for authenticating with the aye service."""
    login_flow()
    
    # Download plugins based on user's license tier
    try:
        token = get_token()
        if not token:
            rprint("[yellow]No token found - skipping plugin download[/]")
            return
        
        # Download plugins for this tier
        fetch_plugins()
        #rprint(f"[green]Premium features for {tier} tier ready.[/]")
        
    except Exception as e:
        rprint(f"[red]Error: Could not download plugins - {e}[/]")

def handle_logout() -> None:
    """Remove the stored aye credentials."""
    delete_token()
    rprint("🔐 Token removed.")

# One-shot generation function
def handle_generate_cmd(prompt: str) -> None:
    """
    Send a single prompt to the backend.
    """
    resp = cli_invoke(message=prompt)
    code = resp.get("generated_code", "")
    rprint(code)


# Chat function
def handle_chat(root: Path, file_mask: str) -> None:
    """Start an interactive REPL. Use /exit or Ctrl‑D to leave."""
    from .repl import chat_repl

    if root is None:
        root = Path.cwd()

    conf = SimpleNamespace()
    conf.root = root
    conf.file_mask = file_mask
    chat_repl(conf)

def process_repl_message(prompt: str, chat_id: Optional[int], root: Path, file_mask: str, chat_id_file: Path, console: Console) -> None:
    """Process a REPL message and handle the response."""
    # This function is now deprecated and should not be used
    # The processing logic has been moved to repl.py to fix the spinner issue
    pass


# Snapshot functions
def handle_history_cmd(file: Optional[Path]) -> None:
    """Show timestamps of saved snapshots for *file* or all snapshots if no file provided."""
    snapshots = list_snapshots(file)
    if not snapshots:
        print("No snapshots found.")
        return
    for snapshot in snapshots:
        print(snapshot)

def handle_snap_show_cmd(file: Path, ts: str) -> None:
    """Print the contents of a specific snapshot."""
    for snap_ts, snap_path in list_snapshots(file):
        if snap_ts == ts:
            print(Path(snap_path).read_text(encoding="utf-8"))
            return
    rprint("Snapshot not found.", err=True)

def handle_restore_cmd(ts: Optional[str], file_name: Optional[str] = None) -> None:
    """Replace all files with the latest snapshot or specified snapshot."""
    try:
        restore_snapshot(ts, file_name)
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
    except Exception as exc:
        rprint(f"Error: {exc}", err=True)

def _is_valid_command(command: str) -> bool:
    """Check if a command exists in the system using bash's command -v"""
    try:
        result = subprocess.run(['command', '-v', command], 
                              capture_output=True, 
                              text=True, 
                              shell=False)
        return result.returncode == 0
    except Exception:
        return False

def handle_restore_command(timestamp: Optional[str] = None, file_name: Optional[str] = None) -> None:
    """Handle the restore command logic. """
    try:
        restore_snapshot(timestamp, file_name)
        if timestamp:
            if file_name:
                rprint(f"[green]File '{file_name}' restored to {timestamp}[/]")
            else:
                rprint(f"[green]All files restored to {timestamp}[/]")
        else:
            if file_name:
                rprint(f"[green]File '{file_name}' restored to latest snapshot.[/]")
            else:
                rprint("[green]All files restored to latest snapshot.[/]")
    except Exception as e:
        rprint(f"[red]Error restoring snapshot:[/] {e}")

def handle_history_command() -> None:
    """Handle the history command logic."""
    timestamps = list_snapshots()
    if not timestamps:
        rprint("[yellow]No snapshots found.[/]")
    else:
        rprint("[bold]Snapshot History:[/]")
        for ts in timestamps:
            rprint(f"  {ts}")

def handle_diff_command(args: list[str]) -> None:
    """Handle the diff command logic according to specified cases."""
    if not args:
        rprint("[red]Error:[/] No file specified for diff.")
        return

    file_name = args[0]
    file_path = Path(file_name)
    if not file_path.exists():
        rprint(f"[red]Error:[/] File '{file_name}' does not exist.")
        return

    snapshots = list_snapshots(file_path)
    if not snapshots:
        rprint(f"[yellow]No snapshots found for file '{file_name}'.[/]")
        return

    snapshot_paths = {}
    for snap_ts, snap_path_str in snapshots:
        ordinal = snap_ts.split('_')[0]  # Extract ordinal like "001"
        full_ts = snap_ts.split('_')[1]  # Extract full timestamp like "20250916T214101"
        snapshot_paths[ordinal] = Path(snap_path_str)
        snapshot_paths[full_ts] = Path(snap_path_str)

    if len(args) == 1:
        # Case 3: Diff with most recent snapshot
        if snapshots:
            latest_snap_path = Path(snapshots[0][1])
            diff_files(file_path, latest_snap_path)
        else:
            rprint(f"[yellow]No snapshots available for '{file_name}'.[/]")

    elif len(args) == 2:
        # Case 1: Diff with specific snapshot ID
        snapshot_id = args[1]
        if snapshot_id in snapshot_paths:
            diff_files(file_path, snapshot_paths[snapshot_id])
        else:
            rprint(f"[red]Error:[/] Snapshot '{snapshot_id}' not found for file '{file_name}'.[/]")

    elif len(args) == 3:
        # Case 2: Diff between two snapshots
        snap_id1 = args[1]
        snap_id2 = args[2]
        if snap_id1 not in snapshot_paths:
            rprint(f"[red]Error:[/] Snapshot '{snap_id1}' not found for file '{file_name}'.[/]")
            return
        if snap_id2 not in snapshot_paths:
            rprint(f"[red]Error:[/] Snapshot '{snap_id2}' not found for file '{file_name}'.[/]")
            return
        diff_files(snapshot_paths[snap_id1], snapshot_paths[snap_id2])

    else:
        rprint("[red]Error:[/] Too many arguments for diff command.")

def _python_diff_files(file1: Path, file2: Path) -> None:
    """Show diff between two files using Python's difflib."""
    try:
        from difflib import unified_diff
        
        # Read file contents
        content1 = file1.read_text(encoding="utf-8").splitlines(keepends=True) if file1.exists() else []
        content2 = file2.read_text(encoding="utf-8").splitlines(keepends=True) if file2.exists() else []
        
        # Generate unified diff
        diff = unified_diff(
            content2,  # from file (snapshot)
            content1,  # to file (current)
            fromfile=str(file2),
            tofile=str(file1)
        )
        
        # Convert diff to string and print
        diff_str = ''.join(diff)
        if diff_str.strip():
            _diff_console.print(diff_str)
        else:
            rprint("[green]No differences found.[/]")
    except Exception as e:
        rprint(f"[red]Error running Python diff:[/] {e}")

def diff_files(file1: Path, file2: Path) -> None:
    """Show diff between two files using system diff command or Python fallback."""
    try:
        result = subprocess.run(
            ["diff", "--color=always", "-u", str(file2), str(file1)],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            clean_output = ANSI_RE.sub("", result.stdout)
            _diff_console.print(clean_output)
        else:
            rprint("[green]No differences found.[/]")
    except FileNotFoundError:
        # Fallback to Python's difflib if system diff is not available
        _python_diff_files(file1, file2)
    except Exception as e:
        rprint(f"[red]Error running diff:[/] {e}")

def filter_unchanged_files(updated_files: list) -> list:
    """Filter out files from updated_files list if their content hasn't changed compared to on-disk version."""
    changed_files = []
    for item in updated_files:
        file_path = Path(item["file_name"])
        new_content = item["file_content"]
        
        # If file doesn't exist on disk, consider it changed (new file)
        if not file_path.exists():
            changed_files.append(item)
            continue
            
        # Read current content and compare
        try:
            current_content = file_path.read_text(encoding="utf-8")
            if current_content != new_content:
                changed_files.append(item)
        except Exception:
            # If we can't read the file, assume it should be updated
            changed_files.append(item)
            
    return changed_files

def process_chat_message(prompt: str, chat_id: Optional[int], root: Path, file_mask: str, selected_model: Optional[str] = None, verbose: bool = False) -> Dict[str, any]:
    """Process a chat message and return the response."""
    source_files = collect_sources(root, file_mask)
    if verbose:
        rprint(f"[yellow]Included with prompt: {', '.join(source_files.keys())}")
    else:
        rprint("[yellow]Turn on verbose mode to see list of files included with prompt.[/]")
    
    resp = cli_invoke(message=prompt, chat_id=chat_id or -1, source_files=source_files, model=selected_model)
    
    assistant_resp_str = resp.get('assistant_response')
    assistant_resp = json.loads(assistant_resp_str)
    
    return {
        "response": resp,
        "assistant_response": assistant_resp,
        "new_chat_id": resp.get("chat_id"),
        "summary": assistant_resp.get("answer_summary"),
        "updated_files": assistant_resp.get("source_files", []),
        "prompt": prompt  # Include prompt for snapshot metadata
    }

# Snapshot cleanup functions
def handle_prune_cmd(keep: int = 10) -> None:
    """Delete all but the most recent N snapshots."""
    try:
        deleted_count = prune_snapshots(keep)
        if deleted_count > 0:
            rprint(f"✅ {deleted_count} snapshots deleted. {keep} most recent snapshots kept.")
        else:
            rprint("✅ No snapshots deleted. You have fewer than the specified keep count.")
    except Exception as e:
        rprint(f"[red]Error pruning snapshots:[/] {e}")

def handle_cleanup_cmd(days: int = 30) -> None:
    """Delete snapshots older than N days."""
    try:
        deleted_count = cleanup_snapshots(days)
        if deleted_count > 0:
            rprint(f"✅ {deleted_count} snapshots older than {days} days deleted.")
        else:
            rprint(f"✅ No snapshots older than {days} days found.")
    except Exception as e:
        rprint(f"[red]Error cleaning up snapshots:[/] {e}")

# Configuration management functions
def handle_config_list() -> None:
    """List all configuration values."""
    config = list_config()
    if not config:
        rprint("[yellow]No configuration values set.[/]")
        return
    
    rprint("[bold]Current Configuration:[/]")
    for key, value in config.items():
        rprint(f"  {key}: {value}")

def handle_config_set(key: str, value: str) -> None:
    """Set a configuration value."""
    # Try to parse value as JSON for proper typing
    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError:
        # If JSON parsing fails, keep as string
        parsed_value = value
    
    set_value(key, parsed_value)
    rprint(f"[green]Configuration '{key}' set to '{value}'.[/]")

def handle_config_get(key: str) -> None:
    """Get a configuration value."""
    value = get_value(key)
    if value is None:
        rprint(f"[yellow]Configuration key '{key}' not found.[/]")
    else:
        rprint(f"{key}: {value}")

def handle_config_delete(key: str) -> None:
    """Delete a configuration value."""
    if delete_value(key):
        rprint(f"[green]Configuration '{key}' deleted.[/]")
    else:
        rprint(f"[yellow]Configuration key '{key}' not found.[/]")


