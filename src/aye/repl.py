import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import CompleteStyle

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich import print as rprint

from .service import (
    process_chat_message,
    filter_unchanged_files
)

from .ui import (
    print_welcome_message,
    print_help_message,
    print_prompt,
    print_error,
    print_assistant_response,
    print_no_files_changed,
    print_files_updated
)


def print_thinking_spinner() -> Spinner:
    """Create and return a single thinking spinner."""
    return Spinner("dots", text="[yellow]Thinking...[/]")


from .plugins.manager import PluginManager
from .auth import get_token
    
# Initialize plugin manager and get completer
plugin_manager = PluginManager()
plugin_manager.discover()
    
def chat_repl(conf) -> None:
    # NEW: Download plugins at start of every chat session
    from .download_plugins import fetch_plugins
    #fetch_plugins()

    # Get completer from plugin manager
    # Get completer through plugin system
    completer_response = plugin_manager.handle_command("get_completer")
    completer = completer_response["completer"] if completer_response else None
    
    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_style=CompleteStyle.READLINE_LIKE,   # "readline" style, no menu
        complete_while_typing=False)

    if conf.file_mask is None:
        response = plugin_manager.handle_command("auto_detect_mask", {"project_root": str(conf.root) if conf.root else "."})
        conf.file_mask = response["mask"] if response and response.get("mask") else "*.py"

    rprint(f"[bold cyan]Session context: {conf.file_mask}[/]")
    print_welcome_message()
    console = Console()

    # Path to store chat_id persistently during session
    chat_id_file = Path(".aye/chat_id.tmp")
    chat_id_file.parent.mkdir(parents=True, exist_ok=True)

    # Setting to -1 to initiate a new chat if no ongoing chat detected
    chat_id = -1

    # Load chat_id if exists from previous session
    if chat_id_file.exists():
        try:
            chat_id = int(chat_id_file.read_text().strip())
        except ValueError:
            chat_id_file.unlink(missing_ok=True)  # Clear invalid file

    while True:
        try:
            prompt = session.prompt(print_prompt())
        except (EOFError, KeyboardInterrupt):
            break

        if not prompt.strip():
            continue

        # Tokenize input to check for commands
        tokens = prompt.strip().split()
        first_token = tokens[0].lower() if tokens else ""

        # Check for exit commands
        if first_token in {"/exit", "/quit", "exit", "quit", ":q", "/q"}:
            break

        if first_token in {"/diff", "diff"}:
            # Note: Diff command still uses the original implementation
            from .service import handle_diff_command
            handle_diff_command(tokens[1:])
            continue

        # Handle snapshot-related commands through plugin manager
        # Pass first token and remaining tokens to plugins
        if first_token in {"/history", "history", "/restore", "/revert", "restore", "revert", "/keep", "keep"}:
            # Extract remaining tokens as arguments
            args = tokens[1:] if len(tokens) > 1 else []
            
            # Let plugin manager handle the command
            response = plugin_manager.handle_command(first_token, {"args": args})
            
            # If plugin handled the command, continue to next prompt
            if response and response.get("handled"):
                continue
            
            # Fall through to shell command handling if not handled by plugins

        # Check for new chat command
        if first_token in {"/new", "new"}:
            chat_id_file.unlink(missing_ok=True)
            chat_id = -1
            console.print("[green]✅ New chat session started.[/]")
            continue

        # Check for help command
        if first_token in {"/help", "help"}:
            print_help_message()
            continue

        # Handle shell commands with or without forward slash
        command = first_token.lstrip('/')
        # Replace direct shell command handling with plugin system
        shell_response = plugin_manager.handle_command("execute_shell_command", {
            "command": command,
            "args": tokens[1:]
        })
        
        if shell_response is not None:
            # Plugin handled the command
            if "error" in shell_response:
                rprint(f"[red]Error:[/] {shell_response['error']}")
            else:
                if shell_response.get("stdout", "").strip():
                    rprint(shell_response["stdout"])
            continue

        # Create and display spinner
        #spinner = Spinner("dots", text="[yellow]Thinking...[/]")
        #with console.status(spinner) as status:
        if True:
            # Process the message and get results
            try:
                spinner = Spinner("dots", text="[yellow]Thinking...[/]")
                with console.status(spinner) as status:
                    result = process_chat_message(prompt, chat_id, conf.root, conf.file_mask)
            except Exception as exc:
                # If the exception is a HTTP‑error with a 403 status, handle it specially
                if hasattr(exc, "response") and getattr(exc.response, "status_code", None) == 403:
                    # 403 → unauthorized / token missing / invalid
                    from .ui import print_error
                    print_error(
                        "[red]❌ Unauthorized:[/] the stored token is invalid or missing.\n"
                        "Log in again with `aye auth login` or set a valid "
                        "`AYE_TOKEN` environment variable.\n"
                        "Obtain your personal access token at https://ayechat.ai"
                    )
                else:
                    # any other kind of error
                    from .ui import print_error
                    print_error(exc)
                continue
        
        # Extract and store new chat_id from response
        new_chat_id = result["new_chat_id"]
        if new_chat_id is not None:
            chat_id = new_chat_id
            chat_id_file.write_text(str(chat_id), encoding="utf-8")
        
        # Print results after the Live context manager has exited
        summary = result["summary"]
        print_assistant_response(summary)

        updated_files = result["updated_files"]
        
        # Filter unchanged files
        updated_files = filter_unchanged_files(updated_files)
        
        if not updated_files:
            print_no_files_changed(console)
        else:  # when updated_files is not empty
            # Use plugin manager for apply_updates
            updates_response = plugin_manager.handle_command("apply_updates", {
                "updated_files": updated_files
            })
            
            if updates_response and "batch_timestamp" in updates_response:
                batch_ts = updates_response["batch_timestamp"]
                if batch_ts:  # only show update message if files were actually written
                    file_names = [item.get("file_name") for item in updated_files if "file_name" in item]
                    if file_names:
                        print_files_updated(console, file_names)
            elif updates_response and "error" in updates_response:
                rprint(f"[red]Error applying updates:[/] {updates_response['error']}")
