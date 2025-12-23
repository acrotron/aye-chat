"""Terminal UI helpers (Rich).

This module centralizes small printing utilities for the Aye CLI.

Design goals:
- Keep Rich theming in one place so other modules don't hardcode colors.
- Provide consistent, human-friendly output for common commands.
- Keep helper functions simple: they *print* and return None (no side effects
  beyond terminal output).

Important note about styling:
- Most output uses Rich "markup" (e.g. "[ui.success]...") which refers to keys
  in the `deep_ocean_theme` Theme below.
- Some keys are under the `markdown.*` namespace because Rich's Markdown
  renderer emits those style names.
"""

from typing import List, Dict, Any, Optional

from rich.console import Console
from rich.theme import Theme

# Rich Theme used by this CLI.
#
# The theme contains two groups of style keys:
# 1) markdown.*
#    - Used when printing Rich Markdown content (headers, code blocks, lists).
# 2) ui.*
#    - Used by our own CLI output helpers for consistent, semantic styling.
#
# Keeping these style names stable lets other code say "style=ui.error" or embed
# "[ui.help.text]..." without caring about the actual colors.
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

# Single shared Console instance.
#
# This keeps output consistent across the entire CLI and avoids each module
# creating its own Console with different settings.
console = Console(theme=deep_ocean_theme)


def print_auth_status(token: Optional[str]) -> None:
    """Show authentication status.

    The CLI supports:
    - a "real" token (normal authenticated usage)
    - a demo token (prefix "aye_demo_")
    - no token (user has not logged in)

    Security/UX:
    - We only print a short prefix of the token to confirm a token exists
      without dumping secrets into terminal scrollback.
    """
    # NOTE: token may be None/empty.
    if token and not token.startswith("aye_demo_"):
        # Real token exists
        console.print("[ui.success]Authenticated[/] - [ui.help.text]Token is saved[/]")
        console.print(f"  [ui.help.text]Token: {token[:12]}...[/]")
    elif token and token.startswith("aye_demo_"):
        # Demo token
        console.print("[ui.warning]Demo Mode[/] - [ui.help.text]Using demo token[/]")
        console.print("  [ui.help.text]Run 'aye auth login' to authenticate with a real token[/]")
    else:
        # No token
        console.print("[ui.error]Not Authenticated[/] - [ui.help.text]No token saved[/]")
        console.print("  [ui.help.text]Run 'aye auth login' to authenticate[/]")


def print_snapshot_history(snapshots: List[str]) -> None:
    """Show timestamps of saved snapshots.

    The snapshot list is expected to already be formatted as displayable strings
    (e.g., snapshot IDs, timestamps, or ordinals).
    """
    # Empty list is a normal case (first run, cleaned up history, etc.).
    if not snapshots:
        console.print("[ui.warning]No snapshots found.[/]")
        return

    console.print("[ui.help.header]Snapshot History:[/]")

    # Simple one-per-line rendering keeps the output stable and easy to copy.
    for snapshot in snapshots:
        console.print(f"  [ui.help.text]{snapshot}[/]")


def print_snapshot_content(content: Optional[str]) -> None:
    """Print the contents of a specific snapshot.

    This is typically used for "show" or "cat"-style commands.

    Args:
        content: The snapshot text to print, or None when the snapshot wasn't
                 found / couldn't be loaded.
    """
    if content is not None:
        # Print raw content. Caller may pass plain text or Rich markup.
        console.print(content)
    else:
        console.print("Snapshot not found.", style="ui.error")


def print_restore_feedback(ts: Optional[str], file_name: Optional[str]) -> None:
    """Print feedback after a restore operation.

    The restore command can apply to:
    - all files (workspace restore)
    - a single file

    Args:
        ts: Snapshot identifier that was restored. If None, CLI treats it as
            "latest".
        file_name: If provided, indicates a single-file restore.
    """
    # Keep this branching simple so output is predictable.
    if ts:
        if file_name:
            console.print(f"[ui.success]✅ File '{file_name}' restored to {ts}[/]")
        else:
            console.print(f"[ui.success]✅ All files restored to {ts}[/]")
    else:
        if file_name:
            console.print(f"[ui.success]✅ File '{file_name}' restored to latest snapshot[/]")
        else:
            console.print("[ui.success]✅ All files restored to latest snapshot[/]")


def print_prune_feedback(deleted_count: int, keep: int) -> None:
    """Print feedback after a prune operation.

    Prune semantics:
    - Keep the N most recent snapshots.
    - Delete older snapshots.

    Args:
        deleted_count: How many snapshots were removed.
        keep: The user-requested number of snapshots to keep.
    """
    if deleted_count > 0:
        console.print(f"[ui.success]✅ {deleted_count} snapshots deleted. {keep} most recent snapshots kept.[/]")
    else:
        console.print("[ui.success]✅ No snapshots deleted. You have fewer than the specified keep count.[/]")


def print_cleanup_feedback(deleted_count: int, days: int) -> None:
    """Print feedback after a cleanup operation.

    Cleanup semantics:
    - Delete snapshots older than a time threshold (in days).

    Args:
        deleted_count: How many snapshots were removed.
        days: Threshold used by the command.
    """
    if deleted_count > 0:
        console.print(f"[ui.success]✅ {deleted_count} snapshots older than {days} days deleted.[/]")
    else:
        console.print(f"[ui.success]✅ No snapshots older than {days} days found.[/]")


def print_config_list(config: Dict[str, Any]) -> None:
    """List all configuration values.

    The config is printed in a stable "key: value" format, one per line.

    Args:
        config: Mapping of configuration keys to values.
    """
    if not config:
        console.print("[ui.warning]No configuration values set.[/]")
        return

    console.print("[ui.help.header]Current Configuration:[/]")

    # Iteration order is whatever the mapping provides (often insertion order).
    for key, value in config.items():
        console.print(f"  [ui.help.command]{key}[/]: [ui.help.text]{value}[/]")


def print_config_value(key: str, value: Any) -> None:
    """Print a single configuration value.

    Args:
        key: Config key the user queried.
        value: Resolved config value. If None, key was not found.
    """
    if value is None:
        console.print(f"[ui.warning]Configuration key '{key}' not found.[/]")
    else:
        console.print(f"[ui.help.command]{key}[/]: [ui.help.text]{value}[/]")


def print_generic_message(message: str, is_error: bool = False) -> None:
    """Print a generic message, optionally styled as an error.

    This is intended for simple "status" output from commands that don't need
    a dedicated helper yet.

    Args:
        message: Text to print (may include Rich markup).
        is_error: If True, use ui.error styling; otherwise ui.success.
    """
    if is_error:
        console.print(f"[ui.error]{message}[/]")
    else:
        console.print(f"[ui.success]{message}[/]")