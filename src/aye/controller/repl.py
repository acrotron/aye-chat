import os
import json
from pathlib import Path
from typing import Optional, Any
import shlex
import threading

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

DEBUG = False
plugin_manager = None # HACK: for broken test patch to work

def handle_cd_command(tokens: list[str], conf: Any) -> bool:
    """Handle 'cd' command: change directory and update conf.root."""
    if len(tokens) < 2:
        target_dir = str(Path.home())
    else:
        target_dir = ' '.join(tokens[1:])
    try:
        os.chdir(target_dir)
        conf.root = Path.cwd()
        rprint(str(conf.root))
        return True
    except Exception as e:
        print_error(e)
        return False

def handle_model_command(session: PromptSession, models: list, conf: Any, tokens: list):
    """Handle the 'model' command for model selection."""
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
        return

    current_id = conf.selected_model
    current_name = next((m['name'] for m in models if m['id'] == current_id), "Unknown")

    rprint(f"[yellow]Currently selected:[/] {current_name}\n")
    rprint("[yellow]Available models:[/]")
    for i, m in enumerate(models, 1):
        rprint(f"  {i}. {m['name']}")
    rprint("")

    if not session:
        return

    choice = session.prompt("Enter model number to select (or Enter to keep current): ").strip()
    if choice:
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

def handle_verbose_command(tokens: list):
    """Handle the 'verbose' command."""
    if len(tokens) > 1:
        val = tokens[1].lower()
        if val in ("on", "off"):
            set_user_config("verbose", val)
            rprint(f"[green]Verbose mode set to {val.title()}[/]")
        else:
            rprint("[red]Usage: verbose on|off[/]")
    else:
        current = get_user_config("verbose", "off")
        rprint(f"[yellow]Verbose mode is {current.title()}[/]")

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

def chat_repl(conf: Any) -> None:
    is_first_run = run_first_time_tutorial_if_needed()

    BUILTIN_COMMANDS = ["with", "new", "history", "diff", "restore", "undo", "keep", "model", "verbose", "exit", "quit", ":q", "help", "cd"]
    completer_response = conf.plugin_manager.handle_command("get_completer", {"commands": BUILTIN_COMMANDS})
    completer = completer_response["completer"] if completer_response else None

    session = PromptSession(history=InMemoryHistory(), completer=completer, complete_style=CompleteStyle.READLINE_LIKE, complete_while_typing=False)

    print_startup_header(conf)

    # Start background indexing if needed
    index_manager = conf.index_manager
    if index_manager.has_work():
        if conf.verbose:
            rprint("[cyan]Starting background indexing...[/]")
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

    while True:
        try:
            prompt_str = print_prompt()
            if conf.index_manager.is_indexing() and conf.verbose:
                progress = conf.index_manager.get_progress_display()
                prompt_str = f"(ツ ({progress}) » "

            prompt = session.prompt(prompt_str)

            # Handle 'with' command before tokenizing. It has its own flow.
            if prompt.strip().lower().startswith("with ") and ":" in prompt:
                try:
                    parts = prompt.split(":", 1)
                    file_list_str, new_prompt_str = parts
                    file_list_str = file_list_str.strip()[4:].strip()

                    if not file_list_str:
                        rprint("[red]Error: File list cannot be empty for 'with' command.[/red]")
                        continue
                    if not new_prompt_str.strip():
                        rprint("[red]Error: Prompt cannot be empty after the colon.[/red]")
                        continue

                    files_to_include = [f.strip() for f in file_list_str.replace(",", " ").split() if f.strip()]
                    explicit_source_files = {}
                    all_files_found = True
                    for file_name in files_to_include:
                        file_path = conf.root / file_name
                        if not file_path.is_file():
                            rprint(f"[yellow]File not found, skipping: {file_name}[/yellow]")
                            all_files_found = False
                            break
                        try:
                            explicit_source_files[file_name] = file_path.read_text(encoding="utf-8")
                        except Exception as e:
                            rprint(f"[red]Could not read file '{file_name}': {e}[/red]")
                            all_files_found = False
                            break
                    
                    if not all_files_found:
                        continue

                    llm_response = invoke_llm(
                        prompt=new_prompt_str.strip(),
                        conf=conf,
                        console=console,
                        plugin_manager=conf.plugin_manager,
                        chat_id=chat_id,
                        verbose=conf.verbose,
                        explicit_source_files=explicit_source_files
                    )
                    if llm_response:
                        new_chat_id = process_llm_response(response=llm_response, conf=conf, console=console, prompt=new_prompt_str.strip(), chat_id_file=chat_id_file if llm_response.chat_id else None)
                        if new_chat_id is not None:
                            chat_id = new_chat_id
                    else:
                        rprint("[yellow]No response from LLM.[/]")
                except Exception as exc:
                    handle_llm_error(exc)
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

        try:
            if lowered_first in {"exit", "quit", ":q"}:
                break
            elif lowered_first == "model":
                handle_model_command(session, MODELS, conf, tokens)
            elif lowered_first == "verbose":
                handle_verbose_command(tokens)
                conf.verbose = get_user_config("verbose", "off").lower() == "on"
            elif lowered_first == "diff":
                args = tokens[1:]
                if not args:
                    rprint("[red]Error:[/] No file specified for diff.")
                    continue
                path1, path2 = commands.get_diff_paths(args[0], args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
                diff_presenter.show_diff(path1, path2)
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
            elif lowered_first == "/db":
                if conf.index_manager and hasattr(conf.index_manager, 'collection'):
                    collection = conf.index_manager.collection
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
                    rprint("[red]Index manager not available.[/red]")
            else:
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
                    # This is the LLM path. Sync the index here before invoking.
                    if not index_manager.is_indexing():
                        index_manager.prepare_sync(verbose=conf.verbose)
                        if index_manager.has_work():
                            thread = threading.Thread(target=index_manager.run_sync_in_background, daemon=True)
                            thread.start()

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

    collect_and_send_feedback(max(0, chat_id))
