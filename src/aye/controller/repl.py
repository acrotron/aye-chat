import os
import json
from pathlib import Path
from typing import Optional, Any
import shlex
import threading
import glob

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.key_binding import KeyBindings

from rich.console import Console
from rich import print as rprint

from aye.model.api import send_feedback
from aye.model.auth import get_user_config, set_user_config
from aye.model.config import MODELS, DEFAULT_MODEL_ID
from aye.presenter.repl_ui import (
    print_welcome_message,
    print_help_message,
    print_prompt,
    print_error
)
from aye.presenter import cli_ui, diff_presenter
from aye.controller.tutorial import run_first_time_tutorial_if_needed
from aye.controller.llm_invoker import invoke_llm
from aye.controller.llm_handler import process_llm_response, handle_llm_error
from aye.controller import commands
from aye.controller.command_handlers import (
    handle_cd_command,
    handle_model_command,
    handle_verbose_command,
    handle_debug_command,
    handle_completion_command,
    handle_with_command
)

DEBUG = False
plugin_manager = None # HACK: for broken test patch to work

def print_startup_header(conf: Any):
    """Prints the session context, current model, and welcome message."""
    try:
        current_model_name = next(m['name'] for m in MODELS if m['id'] == conf.selected_model)
    except StopIteration:
        conf.selected_model = DEFAULT_MODEL_ID
        set_user_config("selected_model", DEFAULT_MODEL_ID)
        current_model_name = next((m['name'] for m in MODELS if m['id'] == DEFAULT_MODEL_ID), "Unknown")

    rprint(f"[bold cyan]Session context: {conf.file_mask}[/]")
    rprint(f"[bold cyan]Current model: {current_model_name}[/]")
    print_welcome_message()

def collect_and_send_feedback(chat_id: int):
    """Prompts user for feedback and sends it before exiting."""
    feedback_session = PromptSession(history=InMemoryHistory())
    bindings = KeyBindings()
    @bindings.add('c-c')
    def _(event):
        event.app.exit(result=event.app.current_buffer.text)

    try:
        rprint("\n[bold cyan]Before you go, would you mind sharing some comments about your experience?")
        rprint("[bold cyan]Include your email if you are ok with us contacting you with some questions.")
        rprint("[bold cyan](Start typing. Press Enter for a new line. Press Ctrl+C to finish.)")
        feedback = feedback_session.prompt("> ", multiline=True, key_bindings=bindings)

        if feedback and feedback.strip():
            send_feedback(feedback.strip(), chat_id=chat_id)
            rprint("[cyan]Thank you for your feedback! Goodbye.[/cyan]")
        else:
            rprint("[cyan]Goodbye![/cyan]")
    except (EOFError, KeyboardInterrupt):
        rprint("\n[cyan]Goodbye![/cyan]")
    except Exception:
        rprint("\n[cyan]Goodbye![/cyan]")


def create_prompt_session(completer: Any) -> PromptSession:
    """
    Create a PromptSession with the configured completion style.
    
    Reads 'completion_style' from user config:
    - 'readline' (default): READLINE_LIKE style, complete_while_typing=False
    - 'multi': MULTI_COLUMN style, complete_while_typing=True
    """
    completion_style = get_user_config("completion_style", "readline").lower()
    
    if completion_style == "multi":
        return PromptSession(
            history=InMemoryHistory(),
            completer=completer,
            complete_style=CompleteStyle.MULTI_COLUMN,
            complete_while_typing=True
        )
    else:
        # Default to readline style
        return PromptSession(
            history=InMemoryHistory(),
            completer=completer,
            complete_style=CompleteStyle.READLINE_LIKE,
            complete_while_typing=False
        )


def chat_repl(conf: Any) -> None:
    is_first_run = run_first_time_tutorial_if_needed()

    BUILTIN_COMMANDS = ["with", "new", "history", "diff", "restore", "undo", "keep", "model", "verbose", "debug", "completion", "exit", "quit", ":q", "help", "cd", "db"]
    completer_response = conf.plugin_manager.handle_command("get_completer", {"commands": BUILTIN_COMMANDS})
    completer = completer_response["completer"] if completer_response else None

    session = create_prompt_session(completer)

    print_startup_header(conf)

    # Start background indexing if needed (only for large projects with index_manager)
    index_manager = getattr(conf, 'index_manager', None)
    if index_manager and index_manager.has_work():
        if conf.verbose:
            rprint("[cyan]Starting background indexing...")
        thread = threading.Thread(target=index_manager.run_sync_in_background, daemon=True)
        thread.start()

    if conf.verbose or is_first_run:
        print_help_message()
        rprint("")
        handle_model_command(None, MODELS, conf, ['model'])
    
    console = Console()
    chat_id_file = Path(".aye/chat_id.tmp")
    chat_id_file.parent.mkdir(parents=True, exist_ok=True)

    chat_id = -1
    if chat_id_file.exists():
        try:
            chat_id = int(chat_id_file.read_text(encoding="utf-8").strip())
        except (ValueError, TypeError):
            chat_id_file.unlink(missing_ok=True)

    try:
        while True:
            try:
                prompt_str = print_prompt()
                # Show indexing progress only if index_manager exists and is active
                if index_manager and index_manager.is_indexing() and conf.verbose:
                    progress = index_manager.get_progress_display()
                    prompt_str = f"(ツ ({progress}) » "

                prompt = session.prompt(prompt_str)

                # Handle 'with' command before tokenizing. It has its own flow.
                if prompt.strip().lower().startswith("with ") and ":" in prompt:
                    new_chat_id = handle_with_command(prompt, conf, console, chat_id, chat_id_file)
                    if new_chat_id is not None:
                        chat_id = new_chat_id
                    continue

                if not prompt.strip():
                    continue
                tokens = shlex.split(prompt.strip(), posix=False)
                if not tokens:
                    continue
            except (EOFError, KeyboardInterrupt):
                break
            except ValueError as e:
                print_error(e)
                continue

            original_first, lowered_first = tokens[0], tokens[0].lower()

            # Normalize slash-prefixed commands: /restore -> restore, /model -> model, etc.
            if lowered_first.startswith('/'):
                lowered_first = lowered_first[1:]  # Remove leading slash
                tokens[0] = tokens[0][1:]  # Update token as well
                original_first = tokens[0]  # Update original_first so shell commands work too

            # Check if user entered a number from 1-12 as a model selection shortcut
            if len(tokens) == 1:
                try:
                    model_num = int(tokens[0])
                    if 1 <= model_num <= len(MODELS):
                        # Convert to model command
                        tokens = ['model', str(model_num)]
                        lowered_first = 'model'
                except ValueError:
                    pass  # Not a number, continue with normal processing

            try:
                if lowered_first in {"exit", "quit", ":q"}:
                    break
                elif lowered_first == "model":
                    handle_model_command(session, MODELS, conf, tokens)
                elif lowered_first == "verbose":
                    handle_verbose_command(tokens)
                    conf.verbose = get_user_config("verbose", "off").lower() == "on"
                elif lowered_first == "debug":
                    handle_debug_command(tokens)
                elif lowered_first == "completion":
                    new_style = handle_completion_command(tokens)
                    if new_style:
                        # Recreate the session with the new completion style
                        session = create_prompt_session(completer)
                        rprint(f"[green]Completion style is now active.[/]")
                elif lowered_first == "diff":
                    args = tokens[1:]
                    if not args:
                        rprint("[red]Error:[/] No file specified for diff.")
                        continue
                    path1, path2, is_stash = commands.get_diff_paths(args[0], args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
                    diff_presenter.show_diff(path1, path2, is_stash_ref=is_stash)
                elif lowered_first == "history":
                    history_list = commands.get_snapshot_history()
                    cli_ui.print_snapshot_history(history_list)
                elif lowered_first in {"restore", "undo"}:
                    args = tokens[1:] if len(tokens) > 1 else []
                    ordinal = args[0] if args else None
                    file_name = args[1] if len(args) > 1 else None
                    commands.restore_from_snapshot(ordinal, file_name)
                    cli_ui.print_restore_feedback(ordinal, file_name)
                elif lowered_first == "keep":
                    keep_count = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 10
                    deleted = commands.prune_snapshots(keep_count)
                    cli_ui.print_prune_feedback(deleted, keep_count)
                elif lowered_first == "new":
                    chat_id_file.unlink(missing_ok=True)
                    chat_id = -1
                    conf.plugin_manager.handle_command("new_chat", {"root": conf.root})
                    console.print("[green]✅ New chat session started.[/]")
                elif lowered_first == "help":
                    print_help_message()
                elif lowered_first == "cd":
                    handle_cd_command(tokens, conf)
                elif lowered_first == "db":
                    if index_manager and hasattr(index_manager, 'collection') and index_manager.collection:
                        collection = index_manager.collection
                        count = collection.count()
                        rprint(f"[bold cyan]Vector DB Status[/]")
                        rprint(f"  Collection Name: '{collection.name}'")
                        rprint(f"  Total Indexed Chunks: {count}")

                        if count > 0:
                            rprint("\n[bold cyan]Sample of up to 5 records:[/]")
                            try:
                                peek_data = collection.peek(limit=5)
                                ids = peek_data.get('ids', [])
                                metadatas = peek_data.get('metadatas', [])
                                documents = peek_data.get('documents', [])

                                for i in range(len(ids)):
                                    doc_preview = documents[i].replace('\\n', ' ').strip()
                                    doc_preview = (doc_preview[:75] + '...') if len(doc_preview) > 75 else doc_preview
                                    rprint(f"  - [yellow]ID:[/] {ids[i]}")
                                    rprint(f"    [yellow]Metadata:[/] {json.dumps(metadatas[i])}")
                                    rprint(f"    [yellow]Content:[/] \"{doc_preview}\"")

                            except Exception as e:
                                rprint(f"[red]  Could not retrieve sample records: {e}[/red]")
                        else:
                            rprint("[yellow]  The vector index is empty.[/yellow]")
                        rprint(f"\n[bold cyan]Total Indexed Chunks: {count}[/]")
                    else:
                        if not conf.use_rag:
                            rprint("[yellow]Small project mode: RAG indexing is disabled.[/yellow]")
                        else:
                            rprint("[red]Index manager not available.[/red]")
                else:
                    # Try shell command execution first
                    shell_response = conf.plugin_manager.handle_command("execute_shell_command", {"command": original_first, "args": tokens[1:]})
                    if shell_response is not None:
                        if "stdout" in shell_response or "stderr" in shell_response:
                            if shell_response.get("stdout", "").strip():
                                rprint(shell_response["stdout"])
                            if shell_response.get("stderr", "").strip():
                                rprint(f"[yellow]{shell_response['stderr']}[/]")
                            if "error" in shell_response:
                                rprint(f"[red]Error:[/] {shell_response['error']}")
                    else:
                        # This is the LLM path.
                        # DO NOT call prepare_sync() here - it blocks the main thread!
                        # The index is already being maintained in the background.
                        # RAG queries will use whatever index state is currently available.

                        llm_response = invoke_llm(prompt=prompt, conf=conf, console=console, plugin_manager=conf.plugin_manager, chat_id=chat_id, verbose=conf.verbose)
                        if llm_response:
                            new_chat_id = process_llm_response(response=llm_response, conf=conf, console=console, prompt=prompt, chat_id_file=chat_id_file if llm_response.chat_id else None)
                            if new_chat_id is not None:
                                chat_id = new_chat_id
                        else:
                            rprint("[yellow]No response from LLM.[/]")
            except Exception as exc:
                handle_llm_error(exc)
                continue
    finally:
        # Ensure clean shutdown of the index manager (if it exists)
        if index_manager:
            index_manager.shutdown()

    collect_and_send_feedback(max(0, chat_id))
