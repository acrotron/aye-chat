"""Interactive REPL for Aye Chat.

This module provides the main chat loop with command dispatch,
shell integration, and LLM invocation.
"""

import json
import os
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import CompleteStyle
from rich import print as rprint
from rich.console import Console
from rich.prompt import Confirm

from aye.controller import commands
from aye.controller.command_handlers import (
    handle_autodiff_command,
    handle_blog_command,
    handle_cd_command,
    handle_completion_command,
    handle_debug_command,
    handle_llm_command,
    handle_autodiff_command,
    handle_shellcap_command,
    handle_printraw_command,
    handle_model_command,
    handle_sslverify_command,
    handle_verbose_command,
)
from aye.controller.llm_handler import handle_llm_error, process_llm_response
from aye.controller.llm_invoker import invoke_llm
from aye.controller.tutorial import run_first_time_tutorial_if_needed
from aye.model import telemetry
from aye.model.api import send_feedback
from aye.model.auth import get_user_config, set_user_config
from aye.model.config import DEFAULT_MODEL_ID, MODELS
from aye.presenter import cli_ui, diff_presenter
from aye.presenter.repl_ui import (
    print_error,
    print_help_message,
    print_prompt,
    print_welcome_message,
)
from aye.controller.shell_capture import capture_shell_result, maybe_attach_shell_result

# Legacy globals for backward compatibility with tests
DEBUG = False
plugin_manager = None  # HACK: for broken test patch to work

# Configuration keys
_TELEMETRY_OPT_IN_KEY = "telemetry_opt_in"
_FEEDBACK_OPT_IN_KEY = "feedback_opt_in"

# Telemetry prefixes
_AYE_PREFIX = "aye:"
_CMD_PREFIX = "cmd:"

# Builtin commands list (used for completer setup)
BUILTIN_COMMANDS = [
    "with", "blog", "new", "history", "diff", "restore", "undo", "keep",
    "model", "verbose", "debug", "autodiff", "shellcap", "completion", "exit", "quit",
    ":q", "help", "cd", "db", "llm", "sslverify", "printraw", "raw"
]

@dataclass
class CommandContext:
    """Shared context for command handlers."""
    conf: Any
    session: PromptSession
    console: Console
    chat_id: int = -1
    chat_id_file: Optional[Path] = None
    completion_style: str = "readline"
    
    def update_chat_id(self, new_id: Optional[int]) -> None:
        """Update chat_id if new value is provided."""
        if new_id is not None:
            self.chat_id = new_id


class CommandDispatcher:
    """Dispatches commands to their handlers using a registry pattern.
    
    This class centralizes command routing, making it easy to add new
    commands and test individual handlers in isolation.
    """
    
    def __init__(self, ctx: CommandContext):
        """Initialize dispatcher with command context.
        
        Args:
            ctx: Shared context containing session, conf, console, etc.
        """
        self._ctx = ctx
        self._handlers: Dict[str, Callable[[List[str]], Optional[bool]]] = {}
        self._exit_commands = {"exit", "quit", ":q"}
        self._register_commands()
    
    def _register_commands(self) -> None:
        """Register all builtin command handlers."""
        # Exit commands
        for cmd in self._exit_commands:
            self._handlers[cmd] = self._handle_exit
        
        # Session commands
        self._handlers["model"] = self._handle_model
        self._handlers["new"] = self._handle_new
        self._handlers["help"] = self._handle_help
        
        # Settings commands
        self._handlers["verbose"] = self._handle_verbose
        self._handlers["sslverify"] = self._handle_sslverify
        self._handlers["debug"] = self._handle_debug
        self._handlers["autodiff"] = self._handle_autodiff
        self._handlers["completion"] = self._handle_completion
        self._handlers["llm"] = self._handle_llm_settings
        
        # Snapshot commands
        self._handlers["history"] = self._handle_history
        self._handlers["diff"] = self._handle_diff
        self._handlers["restore"] = self._handle_restore
        self._handlers["undo"] = self._handle_restore  # Alias
        self._handlers["keep"] = self._handle_keep
        
        # Other commands
        self._handlers["cd"] = self._handle_cd
        self._handlers["db"] = self._handle_db
        self._handlers["blog"] = self._handle_blog
    
    def dispatch(self, command: str, tokens: List[str]) -> Optional[bool]:
        """Dispatch a command to its handler.
        
        Args:
            command: Lowercase command name
            tokens: Full token list including command
            
        Returns:
            True to exit REPL, False to continue, None if command not handled
        """
        handler = self._handlers.get(command)
        if handler:
            return handler(tokens)
        return None
    
    def _record_telemetry(self, command: str, tokens: List[str]) -> None:
        """Record telemetry for a command."""
        telemetry.record_command(command, has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
    
    # --- Exit Commands ---
    
    def _handle_exit(self, tokens: List[str]) -> bool:
        """Handle exit/quit commands."""
        self._record_telemetry(tokens[0].lower(), tokens)
        return True  # Signal exit
    
    # --- Session Commands ---
    
    def _handle_model(self, tokens: List[str]) -> bool:
        """Handle model selection command."""
        self._record_telemetry("model", tokens)
        handle_model_command(self._ctx.session, MODELS, self._ctx.conf, tokens)
        return False
    
    def _handle_new(self, tokens: List[str]) -> bool:
        """Handle new chat session command."""
        self._record_telemetry("new", tokens)
        if self._ctx.chat_id_file:
            self._ctx.chat_id_file.unlink(missing_ok=True)
        self._ctx.chat_id = -1
        self._ctx.conf.plugin_manager.handle_command("new_chat", {"root": self._ctx.conf.root})
        self._ctx.console.print("[green]✅ New chat session started.[/]")
        return False
    
    def _handle_help(self, tokens: List[str]) -> bool:
        """Handle help command."""
        self._record_telemetry("help", tokens)
        print_help_message()
        return False
    
    # --- Settings Commands ---
    
    def _handle_verbose(self, tokens: List[str]) -> bool:
        """Handle verbose setting command."""
        self._record_telemetry("verbose", tokens)
        handle_verbose_command(tokens)
        self._ctx.conf.verbose = get_user_config("verbose", "off").lower() == "on"
        return False
    
    def _handle_sslverify(self, tokens: List[str]) -> bool:
        """Handle SSL verify setting command."""
        self._record_telemetry("sslverify", tokens)
        handle_sslverify_command(tokens)
        return False
    
    def _handle_debug(self, tokens: List[str]) -> bool:
        """Handle debug setting command."""
        self._record_telemetry("debug", tokens)
        handle_debug_command(tokens)
        return False
    
    def _handle_autodiff(self, tokens: List[str]) -> bool:
        """Handle autodiff setting command."""
        self._record_telemetry("autodiff", tokens)
        handle_autodiff_command(tokens)
        return False
    
    def _handle_completion(self, tokens: List[str]) -> bool:
        """Handle completion style command."""
        self._record_telemetry("completion", tokens)
        new_style = handle_completion_command(tokens)
        if new_style:
            self._ctx.completion_style = new_style
            # Recreate completer and session with new style
            completer = _get_completer(
                self._ctx.conf.plugin_manager,
                str(self._ctx.conf.root),
                new_style
            )
            self._ctx.session = create_prompt_session(completer, new_style)
            rprint("[green]Completion style is now active.[/]")
        return False
    
    def _handle_llm_settings(self, tokens: List[str]) -> bool:
        """Handle LLM settings command."""
        self._record_telemetry("llm", tokens)
        handle_llm_command(self._ctx.session, tokens)
        return False
    
    # --- Snapshot Commands ---
    
    def _handle_history(self, tokens: List[str]) -> bool:
        """Handle snapshot history command."""
        self._record_telemetry("history", tokens)
        history_list = commands.get_snapshot_history()
        cli_ui.print_snapshot_history(history_list)
        return False
    
    def _handle_diff(self, tokens: List[str]) -> bool:
        """Handle diff command."""
        self._record_telemetry("diff", tokens)
        args = tokens[1:]
        if not args:
            rprint("[red]Error:[/] No file specified for diff.")
            return False
        path1, path2, is_stash = commands.get_diff_paths(
            args[0],
            args[1] if len(args) > 1 else None,
            args[2] if len(args) > 2 else None
        )
        diff_presenter.show_diff(path1, path2, is_stash_ref=is_stash)
        return False
    
    def _handle_restore(self, tokens: List[str]) -> bool:
        """Handle restore/undo command."""
        self._record_telemetry(tokens[0].lower(), tokens)
        args = tokens[1:] if len(tokens) > 1 else []
        ordinal = args[0] if args else None
        file_name = args[1] if len(args) > 1 else None
        commands.restore_from_snapshot(ordinal, file_name)
        cli_ui.print_restore_feedback(ordinal, file_name)
        # Persist flag to stop showing restore tip
        set_user_config("has_used_restore", "on")
        return False
    
    def _handle_keep(self, tokens: List[str]) -> bool:
        """Handle keep (prune snapshots) command."""
        self._record_telemetry("keep", tokens)
        if len(tokens) > 1:
            if not tokens[1].isdigit():
                rprint(f"[red]Error:[/] '{tokens[1]}' is not a valid number. Please provide a positive integer.")
                return False
            keep_count = int(tokens[1])
        else:
            keep_count = 10
        deleted = commands.prune_snapshots(keep_count)
        cli_ui.print_prune_feedback(deleted, keep_count)
        return False
    
    # --- Other Commands ---
    
    def _handle_cd(self, tokens: List[str]) -> bool:
        """Handle change directory command."""
        self._record_telemetry("cd", tokens)
        handle_cd_command(tokens, self._ctx.conf)
        return False
    
    def _handle_db(self, tokens: List[str]) -> bool:
        """Handle database status command."""
        self._record_telemetry("db", tokens)
        _print_db_status(self._ctx.conf)
        return False
    
    def _handle_blog(self, tokens: List[str]) -> bool:
        """Handle blog generation command."""
        self._record_telemetry("blog", tokens)
        telemetry.record_llm_prompt("LLM <blog>")
        new_chat_id = handle_blog_command(
            tokens, self._ctx.conf, self._ctx.console,
            self._ctx.chat_id, self._ctx.chat_id_file
        )
        self._ctx.update_chat_id(new_chat_id)
        return False


# =============================================================================
# Helper Functions
# =============================================================================

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


def _is_feedback_prompt_enabled() -> bool:
    """Return True if the exit feedback prompt is enabled."""
    val = get_user_config(_FEEDBACK_OPT_IN_KEY, "on")
    return str(val).lower() == "on"


def _get_completer(plugin_manager: Any, project_root: str, completion_style: str) -> Any:
    """Get completer from plugin manager."""
    response = plugin_manager.handle_command("get_completer", {
        "commands": BUILTIN_COMMANDS,
        "project_root": project_root,
        "completion_style": completion_style
    })
    return response["completer"] if response else None


def _print_db_status(conf: Any) -> None:
    """Print vector database status."""
    index_manager = getattr(conf, 'index_manager', None)
    
    if index_manager and hasattr(index_manager, 'collection') and index_manager.collection:
        collection = index_manager.collection
        count = collection.count()
        rprint("[bold cyan]Vector DB Status[/]")
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
        if not getattr(conf, 'use_rag', True):
            rprint("[yellow]Small project mode: RAG indexing is disabled.[/yellow]")
        else:
            rprint("[red]Index manager not available.[/red]")


def print_startup_header(conf: Any) -> None:
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


def collect_and_send_feedback(chat_id: int) -> None:
    """Prompts user for feedback and sends it before exiting."""
    if not _is_feedback_prompt_enabled():
        rprint("[cyan]Goodbye![/cyan]")
        return

    feedback_session = PromptSession(history=InMemoryHistory())
    bindings = KeyBindings()

    @bindings.add('c-c')
    def _(event):
        event.app.exit(result=event.app.current_buffer.text)

    feedback_text: str = ""
    try:
        rprint("\n[bold cyan]Before you go:")
        rprint()
        rprint("[bold cyan]Has Aye Chat replaced anything in your workflow?")
        rprint("[bold cyan]If yes, what? If not, what would need to change for it to?")
        rprint()
        rprint("[dim](Ctrl+C to finish.)")
        feedback = feedback_session.prompt("> ", multiline=True, key_bindings=bindings, reserve_space_for_menu=6)
        if feedback and feedback.strip():
            feedback_text = feedback.strip()
    except (EOFError, KeyboardInterrupt):
        feedback_text = ""
    except Exception:
        feedback_text = ""

    if not feedback_text:
        return

    telemetry_payload = telemetry.build_payload(top_n=20) if telemetry.is_enabled() else None
    send_feedback(feedback_text, chat_id=chat_id, telemetry=telemetry_payload)
    if telemetry_payload is not None:
        telemetry.reset()

    rprint("[cyan]Thank you for your feedback![/cyan]")


def create_key_bindings() -> KeyBindings:
    """Create custom key bindings for the prompt session."""
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, filter=completion_is_selected)
    def accept_selected_completion(event):
        """Accept selected completion on Enter."""
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state
        if complete_state and complete_state.current_completion:
            buffer.apply_completion(complete_state.current_completion)
        buffer.complete_state = None

    @bindings.add(Keys.Enter, filter=has_completions & ~completion_is_selected)
    def accept_first_completion(event):
        """Accept first completion when menu visible but none selected."""
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state
        if complete_state and complete_state.completions:
            buffer.apply_completion(complete_state.completions[0])
        buffer.complete_state = None

    return bindings


def create_prompt_session(completer: Any, completion_style: str = "readline") -> PromptSession:
    """Create a PromptSession with multi-column completion display."""
    return PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=True,
        key_bindings=create_key_bindings()
    )


def _execute_forced_shell_command(command: str, args: List[str], conf: Any) -> None:
    """Execute a shell command with force flag (bypasses command validation)."""
    telemetry.record_command(command, has_args=len(args) > 0, prefix=_CMD_PREFIX)
    shell_response = conf.plugin_manager.handle_command(
        "execute_shell_command",
        {"command": command, "args": args, "force": True}
    )
    if shell_response is not None:
        if "stdout" in shell_response or "stderr" in shell_response:
            if shell_response.get("stdout", "").strip():
                rprint(shell_response["stdout"])
            if shell_response.get("stderr", "").strip():
                rprint(f"[yellow]{shell_response['stderr']}[/]")
            if "error" in shell_response:
                rprint(f"[red]Error:[/] {shell_response['error']}")
        elif "message" in shell_response:
            rprint(shell_response["message"])

        # Capture failing command output for auto-attach to next LLM prompt
        cmd_str = " ".join([command] + args)
        capture_shell_result(conf, cmd=cmd_str, shell_response=shell_response)
    else:
        rprint("[red]Error:[/] Failed to execute shell command")


def _handle_shell_command(original_first: str, tokens: List[str], conf: Any) -> bool:
    """Try to execute input as a shell command.
    
    Returns:
        True if handled as shell command, False otherwise.
    """
    shell_response = conf.plugin_manager.handle_command(
        "execute_shell_command",
        {"command": original_first, "args": tokens[1:]}
    )
    if shell_response is not None:
        telemetry.record_command(original_first, has_args=len(tokens) > 1, prefix=_CMD_PREFIX)
        if "stdout" in shell_response or "stderr" in shell_response:
            if shell_response.get("stdout", "").strip():
                rprint(shell_response["stdout"])
            if shell_response.get("stderr", "").strip():
                rprint(f"[yellow]{shell_response['stderr']}[/]")
            if "error" in shell_response:
                rprint(f"[red]Error:[/] {shell_response['error']}")
        return True
    return False


def _handle_llm_invocation(
    prompt: str,
    ctx: CommandContext
) -> None:
    """Handle LLM invocation with optional @ file references."""
    # Check for @file references
    at_response = ctx.conf.plugin_manager.handle_command("parse_at_references", {
        "text": prompt,
        "project_root": str(ctx.conf.root)
    })

    explicit_files = None
    cleaned_prompt = prompt
    used_at = False

    if at_response and not at_response.get("error"):
        explicit_files = at_response.get("file_contents", {})
        cleaned_prompt = at_response.get("cleaned_prompt", prompt)
        used_at = bool(explicit_files)

        if ctx.conf.verbose and explicit_files:
            rprint(f"[cyan]Including {len(explicit_files)} file(s) from @ references: {', '.join(explicit_files.keys())}[/cyan]")

    # Record telemetry
    if used_at:
        telemetry.record_llm_prompt("LLM @")
    else:
        telemetry.record_llm_prompt("LLM")

    # Invoke LLM
    llm_response = invoke_llm(
        prompt=cleaned_prompt,
        conf=ctx.conf,
        console=ctx.console,
        plugin_manager=ctx.conf.plugin_manager,
        chat_id=ctx.chat_id,
        verbose=ctx.conf.verbose,
        explicit_source_files=explicit_files
    )
    
    if llm_response:
        new_chat_id = process_llm_response(
            response=llm_response,
            conf=ctx.conf,
            console=ctx.console,
            prompt=cleaned_prompt,
            chat_id_file=ctx.chat_id_file if llm_response.chat_id else None
        )
        ctx.update_chat_id(new_chat_id)
    else:
        rprint("[yellow]No response from LLM.[/]")


def _parse_input(prompt: str) -> Tuple[bool, str, List[str]]:
    """Parse user input into tokens.
    
    Returns:
        Tuple of (force_shell, cleaned_prompt, tokens)
    """
    force_shell = False
    
    # Check for '!' prefix - force shell execution
    if prompt.strip().startswith('!'):
        force_shell = True
        prompt = prompt.strip()[1:]
        if not prompt.strip():
            return force_shell, "", []
    
    if not prompt.strip():
        return force_shell, "", []
    
    tokens = shlex.split(prompt.strip(), posix=False)
    return force_shell, prompt, tokens


def _normalize_command(tokens: List[str]) -> Tuple[str, str, List[str]]:
    """Normalize command from tokens.
    
    Handles slash-prefixed commands and model number shortcuts.
    
    Returns:
        Tuple of (original_first, lowered_first, tokens)
    """
    original_first = tokens[0]
    lowered_first = tokens[0].lower()
    
    # Normalize slash-prefixed commands
    if lowered_first.startswith('/'):
        lowered_first = lowered_first[1:]
        tokens[0] = tokens[0][1:]
        original_first = tokens[0]
    
    # Model number shortcut (1-12)
    if len(tokens) == 1:
        try:
            model_num = int(tokens[0])
            if 1 <= model_num <= len(MODELS):
                tokens = ['model', str(model_num)]
                lowered_first = 'model'
                original_first = 'model'
        except ValueError:
            pass
    
    return original_first, lowered_first, tokens


def _setup_context(conf: Any) -> CommandContext:
    """Set up the command context with session and files."""
    completion_style = get_user_config("completion_style", "readline").lower()
    completer = _get_completer(conf.plugin_manager, str(conf.root), completion_style)
    session = create_prompt_session(completer, completion_style)
    
    console = Console(force_terminal=True)
    chat_id_file = Path(".aye/chat_id.tmp")
    chat_id_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing chat_id
    chat_id = -1
    if chat_id_file.exists():
        try:
            chat_id = int(chat_id_file.read_text(encoding="utf-8").strip())
        except (ValueError, TypeError):
            chat_id_file.unlink(missing_ok=True)
    
    return CommandContext(
        conf=conf,
        session=session,
        console=console,
        chat_id=chat_id,
        chat_id_file=chat_id_file,
        completion_style=completion_style
    )


def _run_startup(conf: Any, ctx: CommandContext, is_first_run: bool) -> None:
    """Run startup sequence: header, telemetry, help, model prompt."""
    print_startup_header(conf)
    telemetry.set_enabled(_prompt_for_telemetry_consent_if_needed())
    
    # Start background indexing if needed
    index_manager = getattr(conf, 'index_manager', None)
    if index_manager and index_manager.has_work():
        if conf.verbose:
            rprint("[cyan]Starting background indexing...")
        thread = threading.Thread(target=index_manager.run_sync_in_background, daemon=True)
        thread.start()
    
    # Show help in verbose mode (but not after tutorial)
    if conf.verbose:
        print_help_message()
        rprint("")
    
    # Show model prompt on first run or verbose mode
    if conf.verbose or is_first_run:
        handle_model_command(None, MODELS, conf, ['model'])


def _get_prompt_string(conf: Any) -> str:
    """Get the prompt string, including indexing progress if applicable."""
    prompt_str = print_prompt()
    index_manager = getattr(conf, 'index_manager', None)
    
    if index_manager and index_manager.is_indexing() and conf.verbose:
        progress = index_manager.get_progress_display()
        prompt_str = f"(ツ ({progress}) » "
    
    return prompt_str


# =============================================================================
# Main REPL Function
# =============================================================================

def chat_repl(conf: Any) -> None:
    """Main chat REPL loop.
    
    This function orchestrates the chat session:
    1. Setup context and dispatcher
    2. Run startup sequence
    3. Main input loop with command dispatch
    4. Cleanup and exit
    """
    is_first_run = run_first_time_tutorial_if_needed()
    ctx = _setup_context(conf)
    dispatcher = CommandDispatcher(ctx)
    
    _run_startup(conf, ctx, is_first_run)
    
    index_manager = getattr(conf, 'index_manager', None)
    
    try:
        while True:
            try:
                prompt_str = _get_prompt_string(conf)
                raw_input = ctx.session.prompt(prompt_str, reserve_space_for_menu=6)
                
                force_shell, prompt, tokens = _parse_input(raw_input)
                if not tokens:
                    continue
                
                original_first, lowered_first, tokens = _normalize_command(tokens)
                
                # Force shell execution with '!' prefix
                if force_shell:
                    _execute_forced_shell_command(original_first, tokens[1:], conf)
                    continue
                
                # Try builtin command dispatch
                result = dispatcher.dispatch(lowered_first, tokens)
                if result is True:
                    break  # Exit
                elif result is False:
                    continue  # Command handled, continue loop
                
                # Try shell command
                if _handle_shell_command(original_first, tokens, conf):
                    continue
                
                # Fall through to LLM invocation
                _handle_llm_invocation(raw_input, ctx)
                
            except (EOFError, KeyboardInterrupt):
                break
            except ValueError as e:
                print_error(e)
                continue

            original_first, lowered_first = tokens[0], tokens[0].lower()

            # If force_shell is True, execute as shell command directly and skip all other checks
            if force_shell:
                _execute_forced_shell_command(original_first, tokens[1:], conf)
                continue

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
                elif lowered_first == "sslverify":
                    telemetry.record_command("sslverify", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_sslverify_command(tokens)
                elif lowered_first == "debug":
                    telemetry.record_command("debug", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_debug_command(tokens)
                elif lowered_first == "autodiff":
                    telemetry.record_command("autodiff", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_autodiff_command(tokens)
                elif lowered_first == "shellcap":
                    telemetry.record_command("shellcap", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_shellcap_command(tokens)
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
                elif lowered_first == "llm":
                    telemetry.record_command("llm", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_llm_command(session, tokens)
                elif lowered_first == "blog":
                    telemetry.record_command("blog", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    telemetry.record_llm_prompt("LLM <blog>")
                    new_chat_id = handle_blog_command(tokens, conf, console, chat_id, chat_id_file)
                    if new_chat_id is not None:
                        chat_id = new_chat_id
                elif lowered_first in ("printraw", "raw"):
                    telemetry.record_command("printraw", has_args=False, prefix=_AYE_PREFIX)
                    handle_printraw_command()
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
                    if len(tokens) > 1:
                        if not tokens[1].isdigit():
                            rprint(f"[red]Error:[/] '{tokens[1]}' is not a valid number. Please provide a positive integer.")
                            continue
                        keep_count = int(tokens[1])
                    else:
                        keep_count = 10
                    deleted = commands.prune_snapshots(keep_count)
                    cli_ui.print_prune_feedback(deleted, keep_count)
                elif lowered_first == "new":
                    telemetry.record_command("new", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    chat_id_file.unlink(missing_ok=True)
                    chat_id = -1
                    conf.plugin_manager.handle_command("new_chat", {"root": conf.root})
                    console.print("[green]\u2705 New chat session started.[/]")
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

                        # Capture failing command output for auto-attach to next LLM prompt
                        cmd_str = " ".join([original_first] + tokens[1:])
                        capture_shell_result(conf, cmd=cmd_str, shell_response=shell_response)
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

                        # Attach pending shell failure output (one-shot) before sending to LLM
                        cleaned_prompt = maybe_attach_shell_result(conf, cleaned_prompt)

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
        if index_manager:
            index_manager.shutdown()
    
    collect_and_send_feedback(max(0, ctx.chat_id))
