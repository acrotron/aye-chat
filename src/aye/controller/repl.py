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
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import completion_is_selected, has_completions

from rich.console import Console
from rich import print as rprint
from rich.prompt import Confirm

from aye.model.api import send_feedback
from aye.model.auth import get_user_config, set_user_config
from aye.model.config import MODELS, DEFAULT_MODEL_ID
from aye.model import telemetry
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


_TELEMETRY_OPT_IN_KEY = "telemetry_opt_in"

# Telemetry prefixes (product decision)
_AYE_PREFIX = "aye:"
_CMD_PREFIX = "cmd:"


def _prompt_for_telemetry_consent_if_needed() -> bool:
    """Ask once for telemetry consent and persist the decision.

    Returns:
        True if telemetry is enabled, False otherwise.
    """
    current = get_user_config(_TELEMETRY_OPT_IN_KEY)
    if isinstance(current, str) and current.lower() in {"on", "off"}:
        return current.lower() == "on"

    rprint("\n[bold cyan]Help improve Aye Chat?[/bold cyan]\n")
    rprint("We'd like to collect [bold]very anonymized[/bold] usage telemetry:")
    rprint("  - only the command name you run (first token)")
    rprint("  - plus '<args>' if it had arguments")
    rprint("  - and 'LLM' when you send something to the AI")
    rprint("")
    rprint("Examples of what would be collected:")
    rprint("  - cmd:git <args>")
    rprint("  - aye:restore")
    rprint("  - aye:diff <args>")
    rprint("  - LLM")
    rprint("  - LLM <with>")
    rprint("  - LLM @")
    rprint("")
    rprint("[bright_black]We never collect command arguments, prompt text, filenames, or file contents in telemetry.[/bright_black]")

    try:
        allow = Confirm.ask("\nAllow anonymized telemetry?", default=True)
    except (EOFError, KeyboardInterrupt):
        allow = False

    set_user_config(_TELEMETRY_OPT_IN_KEY, "on" if allow else "off")
    return bool(allow)


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
    """Prompts user for feedback and sends it before exiting.

    Updated requirement: only send feedback (and include telemetry) if the user
    entered feedback text. If feedback is empty, do not send anything.
    """
    feedback_session = PromptSession(history=InMemoryHistory())
    bindings = KeyBindings()

    @bindings.add('c-c')
    def _(event):
        event.app.exit(result=event.app.current_buffer.text)

    feedback_text: str = ""
    try:
        rprint("\n[bold cyan]Before you go, would you mind sharing some comments about your experience?")
        rprint("[bold cyan]Include your email if you are ok with us contacting you with some questions.")
        rprint("[bold cyan](Start typing. Press Enter for a new line. Press Ctrl+C to finish.)")
        feedback = feedback_session.prompt("> ", multiline=True, key_bindings=bindings)
        if feedback and feedback.strip():
            feedback_text = feedback.strip()
    except (EOFError, KeyboardInterrupt):
        # No feedback entered
        feedback_text = ""
    except Exception:
        feedback_text = ""

    if not feedback_text:
        rprint("[cyan]Goodbye![/cyan]")
        return

    telemetry_payload = telemetry.build_payload(top_n=20) if telemetry.is_enabled() else None

    send_feedback(feedback_text, chat_id=chat_id, telemetry=telemetry_payload)
    if telemetry_payload is not None:
        telemetry.reset()

    rprint("[cyan]Thank you for your feedback! Goodbye.[/cyan]")



def create_key_bindings() -> KeyBindings:
    """
    Create custom key bindings for the prompt session.

    Key behaviors:
    - Enter when a completion is selected: Accept the selected completion
    - Enter when completion menu is visible but nothing selected: Accept first completion
    - Enter when no completion menu: Submit the input
    - Tab: Cycle through completions (default behavior)
    """
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, filter=completion_is_selected)
    def accept_selected_completion(event):
        """
        When a specific completion is selected (highlighted),
        accept it on Enter instead of submitting the input.
        """
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state

        if complete_state and complete_state.current_completion:
            # Apply the completion by inserting its text at the correct position
            completion = complete_state.current_completion
            buffer.apply_completion(completion)

        # Clear the completion state after applying
        buffer.complete_state = None

    @bindings.add(Keys.Enter, filter=has_completions & ~completion_is_selected)
    def accept_first_completion(event):
        """
        When completions are visible but none is explicitly selected,
        accept the first completion on Enter.
        """
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state

        if complete_state and complete_state.completions:
            # Get the first completion and apply it
            first_completion = complete_state.completions[0]
            buffer.apply_completion(first_completion)

        # Clear the completion state after applying
        buffer.complete_state = None

    return bindings



def create_prompt_session(completer: Any, completion_style: str = "readline") -> PromptSession:
    """
    Create a PromptSession with multi-column completion display.

    We always use MULTI_COLUMN style to ensure @ file completions display
    in a nice grid format. The 'completion_style' parameter controls whether
    non-@ completions require TAB (readline behavior) or auto-trigger (multi).

    The DynamicAutoCompleteCompleter handles the logic of when to show
    completions based on the completion_style setting:
    - 'readline': @ completions auto-trigger, others require TAB
    - 'multi': all completions auto-trigger

    Custom key bindings ensure that Enter accepts a completion when the
    menu is visible, rather than submitting the input.

    Args:
        completer: The completer instance to use
        completion_style: 'readline' or 'multi' - controls auto-trigger behavior
    """
    # Create custom key bindings for completion behavior
    key_bindings = create_key_bindings()

    # Always use MULTI_COLUMN for nice grid display of @ file completions
    # The DynamicAutoCompleteCompleter controls when completions appear
    return PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=True,
        key_bindings=key_bindings
    )



def chat_repl(conf: Any) -> None:
    is_first_run = run_first_time_tutorial_if_needed()

    BUILTIN_COMMANDS = ["with", "new", "history", "diff", "restore", "undo", "keep", "model", "verbose", "debug", "completion", "exit", "quit", ":q", "help", "cd", "db"]

    # Get the completion style setting
    completion_style = get_user_config("completion_style", "readline").lower()

    completer_response = conf.plugin_manager.handle_command("get_completer", {
        "commands": BUILTIN_COMMANDS,
        "project_root": str(conf.root),
        "completion_style": completion_style
    })
    completer = completer_response["completer"] if completer_response else None

    session = create_prompt_session(completer, completion_style)

    print_startup_header(conf)

    # Telemetry consent prompt (once) + in-memory enable/disable
    telemetry.set_enabled(_prompt_for_telemetry_consent_if_needed())

    # Start background indexing if needed (only for large projects with index_manager)
    index_manager = getattr(conf, 'index_manager', None)
    if index_manager and index_manager.has_work():
        if conf.verbose:
            rprint("[cyan]Starting background indexing...")
        thread = threading.Thread(target=index_manager.run_sync_in_background, daemon=True)
        thread.start()

    # Only auto-print help in verbose mode.
    # First run (tutorial) should not spam the help screen.
    if conf.verbose:
        print_help_message()
        rprint("")

    # Keep first-run behavior of showing model prompt, but without forcing help.
    if conf.verbose or is_first_run:
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
                    telemetry.record_llm_prompt("LLM <with>")
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
                    telemetry.record_command(lowered_first, has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    break
                elif lowered_first == "model":
                    telemetry.record_command("model", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_model_command(session, MODELS, conf, tokens)
                elif lowered_first == "verbose":
                    telemetry.record_command("verbose", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_verbose_command(tokens)
                    conf.verbose = get_user_config("verbose", "off").lower() == "on"
                elif lowered_first == "debug":
                    telemetry.record_command("debug", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_debug_command(tokens)
                elif lowered_first == "completion":
                    telemetry.record_command("completion", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    new_style = handle_completion_command(tokens)
                    if new_style:
                        # Recreate the completer with the new style setting
                        completer_response = conf.plugin_manager.handle_command("get_completer", {
                            "commands": BUILTIN_COMMANDS,
                            "project_root": str(conf.root),
                            "completion_style": new_style
                        })
                        completer = completer_response["completer"] if completer_response else None
                        # Recreate the session with the new completer
                        session = create_prompt_session(completer, new_style)
                        rprint(f"[green]Completion style is now active.[/]")
                elif lowered_first == "diff":
                    telemetry.record_command("diff", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    args = tokens[1:]
                    if not args:
                        rprint("[red]Error:[/] No file specified for diff.")
                        continue
                    path1, path2, is_stash = commands.get_diff_paths(args[0], args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
                    diff_presenter.show_diff(path1, path2, is_stash_ref=is_stash)
                elif lowered_first == "history":
                    telemetry.record_command("history", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    history_list = commands.get_snapshot_history()
                    cli_ui.print_snapshot_history(history_list)
                elif lowered_first in {"restore", "undo"}:
                    telemetry.record_command(lowered_first, has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    args = tokens[1:] if len(tokens) > 1 else []
                    ordinal = args[0] if args else None
                    file_name = args[1] if len(args) > 1 else None
                    commands.restore_from_snapshot(ordinal, file_name)
                    cli_ui.print_restore_feedback(ordinal, file_name)

                    # Persist a global flag so we stop showing the restore breadcrumb tip.
                    # NOTE: tutorial restore does NOT hit this code path.
                    set_user_config("has_used_restore", "on")
                elif lowered_first == "keep":
                    telemetry.record_command("keep", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    keep_count = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 10
                    deleted = commands.prune_snapshots(keep_count)
                    cli_ui.print_prune_feedback(deleted, keep_count)
                elif lowered_first == "new":
                    telemetry.record_command("new", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    chat_id_file.unlink(missing_ok=True)
                    chat_id = -1
                    conf.plugin_manager.handle_command("new_chat", {"root": conf.root})
                    console.print("[green]✅ New chat session started.[/]")
                elif lowered_first == "help":
                    telemetry.record_command("help", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    print_help_message()
                elif lowered_first == "cd":
                    telemetry.record_command("cd", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_cd_command(tokens, conf)
                elif lowered_first == "db":
                    telemetry.record_command("db", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
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
                        telemetry.record_command(original_first, has_args=len(tokens) > 1, prefix=_CMD_PREFIX)
                        if "stdout" in shell_response or "stderr" in shell_response:
                            if shell_response.get("stdout", "").strip():
                                rprint(shell_response["stdout"])
                            if shell_response.get("stderr", "").strip():
                                rprint(f"[yellow]{shell_response['stderr']}[/]")
                            if "error" in shell_response:
                                rprint(f"[red]Error:[/] {shell_response['error']}")
                    else:
                        # Check for @file references before invoking LLM
                        at_response = conf.plugin_manager.handle_command("parse_at_references", {
                            "text": prompt,
                            "project_root": str(conf.root)
                        })

                        explicit_files = None
                        cleaned_prompt = prompt
                        used_at = False

                        if at_response and not at_response.get("error"):
                            explicit_files = at_response.get("file_contents", {})
                            cleaned_prompt = at_response.get("cleaned_prompt", prompt)
                            used_at = bool(explicit_files)

                            if conf.verbose and explicit_files:
                                rprint(f"[cyan]Including {len(explicit_files)} file(s) from @ references: {', '.join(explicit_files.keys())}[/cyan]")

                        # This is the LLM path.
                        if used_at:
                            telemetry.record_llm_prompt("LLM @")
                        else:
                            telemetry.record_llm_prompt("LLM")

                        # DO NOT call prepare_sync() here - it blocks the main thread!
                        # The index is already being maintained in the background.
                        # RAG queries will use whatever index state is currently available.

                        llm_response = invoke_llm(
                            prompt=cleaned_prompt,
                            conf=conf,
                            console=console,
                            plugin_manager=conf.plugin_manager,
                            chat_id=chat_id,
                            verbose=conf.verbose,
                            explicit_source_files=explicit_files
                        )
                        if llm_response:
                            new_chat_id = process_llm_response(response=llm_response, conf=conf, console=console, prompt=cleaned_prompt, chat_id_file=chat_id_file if llm_response.chat_id else None)
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
