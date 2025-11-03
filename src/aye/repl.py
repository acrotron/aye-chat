# repl.py
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional
import traceback

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.key_binding import KeyBindings

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich import print as rprint

from .api import send_feedback
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

from .plugin_manager import PluginManager
from .auth import get_token, get_user_config, set_user_config

# Snapshot core utilities
from .snapshot import (
    list_snapshots,
    restore_snapshot,
    prune_snapshots,
    apply_updates
)

from .config import MODELS, DEFAULT_MODEL_ID

# Initialize plugin manager and get completer
plugin_manager = PluginManager(verbose=False)
plugin_manager.discover()

def handle_cd_command(tokens: list[str], conf) -> bool:
    """Handle 'cd' command: change directory and update conf.root. Returns True if handled."""
    import shlex
    from pathlib import Path
    if len(tokens) < 2:
        # cd without args: go to home
        target_dir = str(Path.home())
    else:
        # Join remaining tokens for paths with spaces
        target_dir = ' '.join(tokens[1:])
    try:
        old_cwd = Path.cwd()
        os.chdir(target_dir)
        conf.root = Path.cwd()
        rprint(str(conf.root))
        return True
    except Exception as e:
        rprint(f"[red]Error changing directory: {e}[/]")
        return False

def handle_model_command(session, models, conf, tokens):
    """Handle the 'model' command: display current and list available models for selection."""
    if len(tokens) > 1:
        try:
            num = int(tokens[1])
            if 1 <= num <= len(models):
                selected_id = models[num - 1]["id"]
                conf.selected_model = selected_id
                set_user_config("selected_model", selected_id)
                rprint(f"[green]Selected model: {models[num - 1]['name']}[/]")
            else:
                rprint("[red]Invalid model number.[/]")
        except ValueError:
            rprint("[red]Invalid input. Use a number.[/]")
    else:
        is_interactive = session is not None

        current_id = conf.selected_model
        current_name = next(m['name'] for m in models if m['id'] == current_id)
        rprint(f"[yellow]Currently selected:[/] {current_name}")
        rprint("")
        
        rprint("[yellow]Available models:[/]")
        for i, m in enumerate(models, 1):
            rprint(f"  {i}. {m['name']}")

        rprint("")

        if not is_interactive:
            return

        choice = session.prompt("Enter model number to select (or Enter to keep current): ").strip()
        if not choice:
            rprint("[yellow]Keeping current model.[/]")
        else:
            try:
                num = int(choice)
                if 1 <= num <= len(models):
                    selected_id = models[num - 1]["id"]
                    conf.selected_model = selected_id
                    set_user_config("selected_model", selected_id)
                    rprint(f"[green]Selected: {models[num - 1]['name']}[/]")
                else:
                    rprint("[red]Invalid number.[/]")
            except ValueError:
                rprint("[red]Invalid input.[/]")

def handle_verbose_command(tokens):
    """Handle the 'verbose' command: set or display verbose mode."""
    if len(tokens) > 1:
        val = tokens[1].lower()
        if val in ("on", "off"):
            set_user_config("verbose", val)
            rprint(f"[green]Verbose mode set to {val.title()}[/]")
        else:
            rprint("[red]Usage: verbose on|off[/]")
    else:
        current = get_user_config("verbose", "on")
        rprint(f"[yellow]Verbose mode is {current.title()}[/]")

def print_startup_header(conf):
    """Prints the session context, current model, and welcome message."""
    # Find the current model name to display it
    try:
        current_model_name = next(m['name'] for m in MODELS if m['id'] == conf.selected_model)
    except StopIteration:
        # The stored model ID is invalid, so reset to default and persist it.
        conf.selected_model = DEFAULT_MODEL_ID
        set_user_config("selected_model", DEFAULT_MODEL_ID)
        # This second lookup should always succeed if DEFAULT_MODEL_ID is valid.
        current_model_name = next((m['name'] for m in MODELS if m['id'] == DEFAULT_MODEL_ID), "Unknown")

    rprint(f"[bold cyan]Session context: {conf.file_mask}[/]")
    rprint(f"[bold cyan]Current model: {current_model_name} (use 'model' to change)[/]")
    print_welcome_message()

def collect_and_send_feedback(chat_id: int):
    """Prompts user for feedback and sends it before exiting."""
    # Use a new session for feedback to avoid using the command completer
    feedback_session = PromptSession(history=InMemoryHistory())

    # Custom keybindings to handle Ctrl+C as submit
    bindings = KeyBindings()
    @bindings.add('c-c')
    def _(event):
        """When Ctrl+C is pressed, exit the prompt and return the text."""
        event.app.exit(result=event.app.current_buffer.text)

    try:
        rprint("\n[bold cyan]Before you go, would you mind sharing some comments about your experience?")
        rprint("[bold cyan]Include your email if you are ok with us contacting you with some questions.")
        rprint("[bold cyan](Press Ctrl+C to finish. Press Enter for a new line.)")
        feedback = feedback_session.prompt("> ", multiline=True, key_bindings=bindings)

        # Send feedback only if it's not empty.
        if feedback and feedback.strip():
            send_feedback(feedback.strip(), chat_id=chat_id)
            rprint("[cyan]Thank you for your feedback! Goodbye.[/cyan]")
        else:
            rprint("[cyan]Goodbye![/cyan]")

    except EOFError:
        # User pressed Ctrl+D, which aborts the prompt.
        rprint("\n[cyan]Goodbye![/cyan]")
    except Exception:
        # If sending feedback fails or another error occurs, don't block exit.
        # The API call is silent on errors, so this is for other issues.
        rprint("\n[cyan]Goodbye![/cyan]")

def chat_repl(conf) -> None:
    # NEW: Download plugins at start of every chat session (commented out to avoid network call during REPL)
    # from .download_plugins import fetch_plugins
    # fetch_plugins()

    # Get completer from plugin manager, including built-in commands
    BUILTIN_COMMANDS = ["new", "history", "diff", "restore", "undo", "keep", "model", "verbose", "exit", "quit", ":q", "help", "cd"]
    completer_response = plugin_manager.handle_command("get_completer", {"commands": BUILTIN_COMMANDS})
    completer = completer_response["completer"] if completer_response else None

    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_style=CompleteStyle.READLINE_LIKE,
        complete_while_typing=False
    )

    if conf.file_mask is None:
        response = plugin_manager.handle_command(
            "auto_detect_mask",
            {"project_root": str(conf.root) if conf.root else "."}
        )
        conf.file_mask = response["mask"] if response and response.get("mask") else "*.py"

    # Models configuration – use DEFAULT_MODEL_ID as fallback instead of first list entry
    conf.selected_model = get_user_config("selected_model", DEFAULT_MODEL_ID)
    conf.verbose = get_user_config("verbose", "on").lower() == "on"

    print_startup_header(conf)
    if conf.verbose:
        print_help_message()
        rprint("")
        handle_model_command(None, MODELS, conf, ['model'])
    console = Console()

    # Path to store chat_id persistently during session
    chat_id_file = Path(".aye/chat_id.tmp")
    chat_id_file.parent.mkdir(parents=True, exist_ok=True)

    # Setting to -1 to initiate a new chat if no ongoing chat detected
    chat_id = -1

    # Load chat_id if exists from previous session
    if chat_id_file.exists():
        try:
            chat_id = int(chat_id_file.read_text(encoding="utf-8").strip())
        except ValueError:
            chat_id_file.unlink(missing_ok=True)  # Clear invalid file

    # Store the last user prompt for snapshot metadata
    last_prompt = None

    while True:
        try:
            prompt = session.prompt(print_prompt())
        except (EOFError, KeyboardInterrupt):
            break

        if not prompt.strip():
            continue

        # Tokenize input respecting shell‑style quoting
        import shlex
        try:
            # Use posix=False so single quotes (apostrophes) are treated as normal characters.
            tokens = shlex.split(prompt.strip(), posix=False)
        except ValueError as e:
            # shlex raises ValueError on malformed quoting – report and skip
            rprint(f"[red]Error parsing command:{e}[/]")
            continue
        if not tokens:
            continue
        original_first = tokens[0]
        lowered_first = original_first.lower()

        # Check for exit commands
        if lowered_first in {"exit", "quit", ":q"}:
            break

        # Model command
        if lowered_first == "model":
            handle_model_command(session, MODELS, conf, tokens)
            continue

        # Verbose command
        if lowered_first == "verbose":
            handle_verbose_command(tokens)
            conf.verbose = get_user_config("verbose", "on").lower() == "on"
            continue

        # Diff command (still uses original implementation)
        if lowered_first == "diff":
            from .service import handle_diff_command
            handle_diff_command(tokens[1:])
            continue

        # Snapshot‑related commands – now handled directly via snapshot.py
        if lowered_first in {"history", "restore", "keep", "undo"}:
            args = tokens[1:] if len(tokens) > 1 else []
            try:
                if lowered_first == "history":
                    snaps = list_snapshots()
                    if snaps:
                        for s in snaps:
                            rprint(s)
                    else:
                        rprint("[yellow]No snapshots found.[/]")
                elif lowered_first in {"restore", "undo"}:
                    # Determine whether the argument is an ordinal or a filename
                    ordinal = None
                    file_name = None
                    if len(args) == 1:
                        possible = args[0]
                        # If it looks like a file that exists, treat it as filename
                        if Path(possible).exists():
                            file_name = possible
                        else:
                            ordinal = possible
                    elif len(args) >= 2:
                        ordinal = args[0]
                        file_name = args[1]
                    # Call the core restore function
                    restore_snapshot(ordinal, file_name)
                    if ordinal:
                        if file_name:
                            rprint(f"[green]✅ File '{file_name}' restored to {ordinal}[/]")
                        else:
                            rprint(f"[green]✅ All files restored to {ordinal}[/]")
                    else:
                        if file_name:
                            rprint(f"[green]✅ File '{file_name}' restored to latest snapshot.[/]")
                        else:
                            rprint("[green]✅ All files restored to latest snapshot.[/]")
                elif lowered_first == "keep":
                    keep_count = int(args[0]) if args and args[0].isdigit() else 10
                    deleted = prune_snapshots(keep_count)
                    rprint(f"✅ {deleted} snapshots pruned. {keep_count} most recent kept.")
            except Exception as e:
                rprint(f"[red]Error:[/] {e}")
            continue

        # New chat command
        if lowered_first == "new":
            chat_id_file.unlink(missing_ok=True)
            chat_id = -1
            console.print("[green]✅ New chat session started.[/]")
            continue

        # Help command
        if lowered_first == "help":
            print_help_message()
            continue

        # Special handling for 'cd' command before shell delegation
        if lowered_first == "cd":
            if handle_cd_command(tokens, conf):
                continue

        # Shell commands – delegated to plugin system
        shell_response = plugin_manager.handle_command("execute_shell_command", {
            "command": original_first,
            "args": tokens[1:]
        })
        if shell_response is not None:
            if "error" in shell_response:
                rprint(f"[red]Error:[/] {shell_response['error']}")
            elif "message" in shell_response:
                # For interactive commands: print the completion message (output already handled by os.system)
                # Strike that: be silent on success
                # rprint(f"[green]{shell_response['message']}[/]")
                pass
            else:
                # Non-interactive: print stdout if present
                if shell_response.get("stdout", "").strip():
                    rprint(shell_response["stdout"])
                if shell_response.get("stderr", "").strip():
                    rprint(f"[yellow]{shell_response['stderr']}[/]")
            continue

        # Store the prompt for snapshot metadata
        last_prompt = prompt

        # Process LLM chat message
        try:
            spinner = Spinner("dots", text="[yellow]Thinking...[/]")
            with console.status(spinner) as status:
                result = process_chat_message(prompt, chat_id, conf.root, conf.file_mask, conf.selected_model, conf.verbose)
        except Exception as exc:
            if hasattr(exc, "response") and getattr(exc.response, "status_code", None) == 403:
                traceback.print_exc()
                from .ui import print_error
                print_error(
                    "[red]❌ Unauthorized:[/] the stored token is invalid or missing.\n"
                    "Log in again with `aye auth login` or set a valid "
                    "`AYE_TOKEN` environment variable.\n"
                    "Obtain your personal access token at https://ayechat.ai"
                )
            else:
                from .ui import print_error
                print_error(exc)
            continue

        # Store new chat ID if present
        new_chat_id = result.get("new_chat_id")
        if new_chat_id is not None:
            chat_id = new_chat_id
            chat_id_file.parent.mkdir(parents=True, exist_ok=True)  # Ensure .aye exists in current dir before writing
            chat_id_file.write_text(str(chat_id), encoding="utf-8")

        # Display assistant response summary
        summary = result.get("summary")
        if summary:
            print_assistant_response(summary)

        # Determine which files were actually changed
        updated_files = result.get("updated_files", [])
        updated_files = filter_unchanged_files(updated_files)
        # ---------------------------------------------------------------------
        # NEW: Normalise file paths – ensure they are relative to the REPL root
        # ---------------------------------------------------------------------
        def _make_paths_relative(files: list[dict], root: Path) -> list[dict]:
            """Strip *root* from any file_name that already starts with it.
            This prevents double‑prefixing like `src/aye/src/aye/foo.py`.
            """
            root = root.resolve()
            for f in files:
                if "file_name" not in f:
                    continue
                try:
                    p = Path(f["file_name"]).resolve()
                    if p.is_relative_to(root):
                        f["file_name"] = str(p.relative_to(root))
                except Exception:
                    # If the path cannot be resolved or Python <3.9, leave it unchanged
                    pass
            return files

        updated_files = _make_paths_relative(updated_files, conf.root)

        if not updated_files:
            print_no_files_changed(console)
        else:
            # Apply updates directly via snapshot utilities, passing the prompt
            try:
                batch_ts = apply_updates(updated_files, last_prompt)
                if batch_ts:
                    file_names = [item.get("file_name") for item in updated_files if "file_name" in item]
                    if file_names:
                        print_files_updated(console, file_names)
            except Exception as e:
                rprint(f"[red]Error applying updates:[/] {e}")

    # After the loop terminates, ask for feedback and exit.
    collect_and_send_feedback(max(0, chat_id))

if __name__ == "__main__":
    chat_repl()
