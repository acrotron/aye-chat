import os
import json
import re
from pathlib import Path
from typing import Optional, Any, List, Dict
import shlex
import threading
import glob

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES

from rich.console import Console
from rich import print as rprint
from rich.prompt import Confirm

from aye.model.api import send_feedback
from aye.model.auth import get_token, get_user_config, set_user_config
from aye.model.config import MODELS, DEFAULT_MODEL_ID
from aye.model.repl_history import (
    AyePersistentHistory,
    get_history_max_entries,
    get_repl_history_path,
    is_history_enabled,
)
from aye.model import telemetry
from aye.presenter.repl_ui import (
    print_welcome_message,
    print_help_message,
    print_prompt,
    print_error,
    print_attachment_summary,
)
from aye.presenter import cli_ui, diff_presenter
from aye.controller.tutorial import run_first_time_tutorial_if_needed
from aye.controller.llm_invoker import invoke_llm, _model_supports_images
from aye.controller.llm_handler import process_llm_response, handle_llm_error
from aye.controller import commands
from aye.controller.command_handlers import (
    handle_cd_command,
    handle_model_command,
    handle_verbose_command,
    handle_sslverify_command,
    handle_debug_command,
    handle_completion_command,
    handle_blog_command,
    handle_llm_command,
    handle_autodiff_command,
    handle_shellcap_command,
    handle_printraw_command,
    handle_paste_image_command,
    handle_clear_attachments_command,
)
from aye.controller.clipboard_attachments import (
    get_pending_clipboard_attachments,
    clear_pending_clipboard_attachments,
    add_pending_clipboard_attachment,
    pending_clipboard_attachment_count,
    make_clipboard_marker,
    strip_clipboard_markers,
)
from aye.controller.shell_capture import capture_shell_result, maybe_attach_shell_result

DEBUG = False
plugin_manager = None # HACK: for broken test patch to work


_TELEMETRY_OPT_IN_KEY = "telemetry_opt_in"
_FEEDBACK_OPT_IN_KEY = "feedback_opt_in"

# Telemetry prefixes (product decision)
_AYE_PREFIX = "aye:"
_CMD_PREFIX = "cmd:"

# Regex to detect URLs in a prompt
_URL_RE = re.compile(r'https?://[^\s]+', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Clipboard paste config helper
# ---------------------------------------------------------------------------

def _is_clipboard_paste_enabled(conf: Any = None) -> bool:
    """Return True if the experimental Ctrl+V clipboard image paste is enabled.

    Reads the ``clipboard_image_paste`` config key.  Also supports the
    ``AYE_CLIPBOARD_IMAGE_PASTE`` environment variable via the standard
    ``get_user_config`` override mechanism.

    Default is ``off``.
    """
    val = get_user_config("clipboard_image_paste", "off")
    return str(val).lower() in {"on", "true", "1", "yes"}


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def handle_url(prompt: str, plugin_manager: Any, verbose: bool = False) -> Optional[Dict[str, str]]:
    """Scan *prompt* for HTTP/HTTPS URLs and fetch each one via the plugin manager."""
    urls = _URL_RE.findall(prompt)
    if not urls:
        return None

    responses: Dict[str, str] = {}
    for idx, url in enumerate(urls):
        try:
            result = plugin_manager.handle_command("process_url", {"url": url, "verbose": verbose})
            if result and result.get("status") == "success":
                virtual_key = f"url_{idx}.txt"
                responses[virtual_key] = json.dumps(result["data"], indent=2)
        except Exception as exc:
            if verbose:
                rprint(f"[yellow]Warning: could not fetch {url}: {exc}[/]")
            continue

    return responses if responses else None


def has_url(text: str) -> bool:
    """Return True if *text* contains at least one HTTP/HTTPS URL."""
    return bool(_URL_RE.search(text))


# ---------------------------------------------------------------------------
# Telemetry / feedback helpers
# ---------------------------------------------------------------------------

def _prompt_for_telemetry_consent_if_needed() -> bool:
    """Ask once for telemetry consent and persist the decision."""
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
    rprint("  - LLM @ attachment")
    rprint("  - LLM clipboard")
    rprint("")
    rprint("[bright_black]We never collect command arguments, prompt text, filenames, file contents,")
    rprint("[bright_black]image contents, image names, MIME types, or image sizes in telemetry.[/bright_black]")

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


def _maybe_print_demo_registration_hint() -> None:
    """Print a brief startup hint when the CLI is using a demo token."""
    try:
        token = get_token()
    except Exception:
        return

    if token and token.startswith("aye_demo_"):
        rprint(
            "[yellow]You're using a demo account.[/]\n"
            "[yellow]Register at https://ayechat.ai before the beta period ends.[/]\n"
            "[yellow]Early registered users will automatically receive Beta status and additional usage after launch.[/]\n"
        )


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
        rprint("[bold cyan]Would you recommend Aye Chat to a friend or colleague?")
        rprint("[bold cyan]Why or why not?")
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



# ---------------------------------------------------------------------------
# Register terminal-specific modified Enter sequences as ControlJ (newline).
# Bare Enter keeps its default submit behavior; modified variants insert '\n'.
# ---------------------------------------------------------------------------
for _seq in ('\x1b[27;2;13~', '\x1b[13;2u'):  # Shift+Enter
    ANSI_SEQUENCES[_seq] = (Keys.ControlJ,)
for _seq in ('\x1b[27;5;13~', '\x1b[13;5u'):  # Ctrl+Enter
    ANSI_SEQUENCES[_seq] = (Keys.ControlJ,)
for _seq in ('\x1b[27;3;13~', '\x1b[13;3u'):  # Alt+Enter
    ANSI_SEQUENCES[_seq] = (Keys.ControlJ,)



def create_key_bindings(conf: Any = None) -> KeyBindings:
    """Create custom key bindings for the prompt session.

    Args:
        conf: Optional REPL config object.  When provided **and** the
            ``clipboard_image_paste`` config flag is enabled, a ``Ctrl+V``
            binding is registered that reads a clipboard image, stages it
            as a pending attachment, and inserts a safe marker into the
            prompt buffer.
    """
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, filter=completion_is_selected)
    def accept_selected_completion(event):
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state

        if complete_state and complete_state.current_completion:
            completion = complete_state.current_completion
            buffer.apply_completion(completion)

        buffer.complete_state = None

    @bindings.add(Keys.Enter, filter=has_completions & ~completion_is_selected)
    def accept_first_completion(event):
        buffer = event.app.current_buffer
        complete_state = buffer.complete_state

        if complete_state and complete_state.completions:
            first_completion = complete_state.completions[0]
            buffer.apply_completion(first_completion)

        buffer.complete_state = None

    # -----------------------------------------------------------------
    # Optional Ctrl+V clipboard image paste (Phase 5)
    # Disabled by default; enabled via clipboard_image_paste=on config.
    # -----------------------------------------------------------------
    if conf is not None and _is_clipboard_paste_enabled(conf):
        @bindings.add('c-v')
        def _handle_ctrl_v_clipboard_paste(event):
            """Read a clipboard image, stage it, and insert a marker.

            On any failure (no image, clipboard unavailable, oversized)
            the keystroke is silently consumed.  Users who need
            diagnostic feedback should use the ``paste-image`` command
            instead.

            No telemetry is recorded here; it is recorded when the
            prompt is actually submitted (Phase 3 flow).
            """
            try:
                from aye.model.clipboard_images import (
                    ClipboardImageError,
                    load_clipboard_image_attachment,
                )

                attachment = load_clipboard_image_attachment()
            except Exception:
                # Silent failure — use paste-image for diagnostics.
                return

            # Stage the attachment (accumulates).
            add_pending_clipboard_attachment(conf, attachment)

            # Build a visible marker and insert it at the cursor.
            marker = make_clipboard_marker(conf)
            buffer = event.app.current_buffer
            buffer.insert_text(f" {marker} ")

    # -----------------------------------------------------------------
    # Newline insertion via Ctrl+J (works reliably across terminals).
    # -----------------------------------------------------------------
    @bindings.add('c-j')
    def _newline_insert_ctrl_j(event):
        event.app.current_buffer.insert_text('\n')

    return bindings



def create_prompt_session(
    completer: Any,
    completion_style: str = "readline",
    conf: Any = None,
) -> PromptSession:
    """Create a PromptSession with persistent history and completion display.

    Args:
        completer: prompt_toolkit completer instance.
        completion_style: ``"readline"`` or ``"multi"``.
        conf: Optional REPL config object passed through to
            ``create_key_bindings`` for Ctrl+V registration.
    """
    key_bindings = create_key_bindings(conf)

    if is_history_enabled():
        history = AyePersistentHistory(
            path=get_repl_history_path(),
            max_entries=get_history_max_entries(),
        )
    else:
        history = InMemoryHistory()

    return PromptSession(
        history=history,
        completer=completer,
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=True,
        key_bindings=key_bindings,
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

        cmd_str = " ".join([command] + args)
        capture_shell_result(conf, cmd=cmd_str, shell_response=shell_response)
    else:
        rprint(f"[red]Error:[/] Failed to execute shell command")


def chat_repl(conf: Any) -> None:
    is_first_run = run_first_time_tutorial_if_needed()

    BUILTIN_COMMANDS = ["with", "blog", "new", "history", "diff", "restore", "undo", "keep", "model", "verbose", "debug", "autodiff", "shellcap", "completion", "exit", "quit", ":q", "help", "cd", "db", "llm", "printraw", "raw", "paste-image", "clear-attachments"]

    completion_style = get_user_config("completion_style", "readline").lower()

    completer_response = conf.plugin_manager.handle_command("get_completer", {
        "commands": BUILTIN_COMMANDS,
        "project_root": str(conf.root),
        "completion_style": completion_style
    })
    completer = completer_response["completer"] if completer_response else None

    session = create_prompt_session(completer, completion_style, conf)

    print_startup_header(conf)

    telemetry.set_enabled(_prompt_for_telemetry_consent_if_needed())

    index_manager = getattr(conf, 'index_manager', None)
    if index_manager and index_manager.has_work():
        if conf.verbose:
            rprint("[cyan]Starting background indexing...")
        thread = threading.Thread(target=index_manager.run_sync_in_background, daemon=True)
        thread.start()

    if conf.verbose:
        print_help_message()
        rprint("")

    if conf.verbose or is_first_run:
        handle_model_command(None, MODELS, conf, ['model'])

    console = Console(force_terminal=True)
    chat_id_file = Path(".aye/chat_id.tmp")
    chat_id_file.parent.mkdir(parents=True, exist_ok=True)

    chat_id = -1
    if chat_id_file.exists():
        try:
            chat_id = int(chat_id_file.read_text(encoding="utf-8").strip())
        except (ValueError, TypeError):
            chat_id_file.unlink(missing_ok=True)

    _maybe_print_demo_registration_hint()

    try:
        while True:
            try:
                # Enable xterm modifyOtherKeys mode so the terminal sends
                # distinct escape sequences for Shift+Enter, Ctrl+Enter, etc.
                # Mode 1 = modifier keys only (safe, won't break existing keys).
                output = session.app.output
                try:
                    output.write_raw("\x1b[>4;1m")
                    output.flush()
                except Exception:
                    pass  # Terminal may not support this

                prompt_str = print_prompt()
                if index_manager and index_manager.is_indexing() and conf.verbose:
                    progress = index_manager.get_progress_display()
                    prompt_str = f"(\u30c4 ({progress}) \u00bb "

                prompt = session.prompt(prompt_str, reserve_space_for_menu=6)

                # Disable modifyOtherKeys after prompt returns
                try:
                    output.write_raw("\x1b[>4;0m")
                    output.flush()
                except Exception:
                    pass

                force_shell = False
                if prompt.strip().startswith('!'):
                    force_shell = True
                    prompt = prompt.strip()[1:]
                    if not prompt.strip():
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

            if force_shell:
                _execute_forced_shell_command(original_first, tokens[1:], conf)
                continue

            if lowered_first.startswith('/'):
                lowered_first = lowered_first[1:]
                tokens[0] = tokens[0][1:]
                original_first = tokens[0]

            if len(tokens) == 1:
                try:
                    model_num = int(tokens[0])
                    if 1 <= model_num <= len(MODELS):
                        tokens = ['model', str(model_num)]
                        lowered_first = 'model'
                except ValueError:
                    pass

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
                        completer_response = conf.plugin_manager.handle_command("get_completer", {
                            "commands": BUILTIN_COMMANDS,
                            "project_root": str(conf.root),
                            "completion_style": new_style
                        })
                        completer = completer_response["completer"] if completer_response else None
                        session = create_prompt_session(completer, new_style, conf)
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
                elif lowered_first == "paste-image":
                    telemetry.record_command("paste-image", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_paste_image_command(conf)
                elif lowered_first == "clear-attachments":
                    telemetry.record_command("clear-attachments", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
                    handle_clear_attachments_command(conf)
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

                        cmd_str = " ".join([original_first] + tokens[1:])
                        capture_shell_result(conf, cmd=cmd_str, shell_response=shell_response)
                    else:
                        # --- Step 1: resolve @file references ---
                        at_response = conf.plugin_manager.handle_command("parse_at_references", {
                            "text": prompt,
                            "project_root": str(conf.root)
                        })

                        explicit_files: Optional[Dict[str, str]] = None
                        cleaned_prompt = prompt
                        used_at = False
                        at_attachments: List[Dict[str, Any]] = []

                        if at_response and not at_response.get("error"):
                            file_contents = at_response.get("file_contents", {}) or {}
                            at_attachments = at_response.get("attachments", []) or []
                            cleaned_prompt = at_response.get("cleaned_prompt", prompt)

                            # Image-only @ refs must NOT suppress normal source
                            # search; only source-file refs do (issue.md §1.2).
                            used_at = bool(file_contents)
                            if used_at:
                                explicit_files = file_contents

                            if conf.verbose and file_contents:
                                rprint(f"[cyan]Including {len(file_contents)} file(s) from @ references: {', '.join(file_contents.keys())}[/cyan]")

                            # Print a one-line summary for each attached image.
                            for att in at_attachments:
                                try:
                                    print_attachment_summary(
                                        att.get("file_name", ""),
                                        att.get("mime_type", "application/octet-stream"),
                                        int(att.get("bytes_size", 0) or 0),
                                    )
                                except Exception:
                                    pass

                            # Surface any image-load errors clearly.
                            for err in at_response.get("image_errors", []) or []:
                                rprint(f"[yellow]Image attachment error:[/] {err}")

                        # --- Step 2: merge pending clipboard attachments ---
                        clipboard_attachments = get_pending_clipboard_attachments(conf)
                        attachments: List[Dict[str, Any]] = clipboard_attachments + at_attachments

                        # --- Step 3: capability gating for image prompts ---
                        # Pending clipboard attachments are NOT cleared on
                        # rejection so the user can switch models and retry.
                        if attachments and not _model_supports_images(conf.selected_model):
                            rprint(
                                f"[red]Error:[/] The selected model '{conf.selected_model}' "
                                "does not support image input. Choose a multimodal model "
                                "or remove the image reference."
                            )
                            continue

                        # --- Step 4: resolve URLs ---
                        if has_url(cleaned_prompt):
                            url_context = handle_url(cleaned_prompt, conf.plugin_manager, verbose=conf.verbose)
                            if url_context:
                                cleaned_prompt = f"{cleaned_prompt}\n\n---\nAttached URL context:\n{url_context}\n---\n"
                                telemetry.record_command("has_url", has_args=False, prefix=_AYE_PREFIX)

                        # --- Step 5: four-way telemetry kind selection ---
                        # Priority: clipboard > @ attachment > @ source > plain LLM
                        if clipboard_attachments:
                            telemetry.record_llm_prompt("LLM clipboard")
                        elif at_attachments:
                            telemetry.record_llm_prompt("LLM @ attachment")
                        elif used_at:
                            telemetry.record_llm_prompt("LLM @")
                        else:
                            telemetry.record_llm_prompt("LLM")

                        # --- Step 6: strip clipboard markers and prepare prompt ---
                        cleaned_prompt = strip_clipboard_markers(cleaned_prompt)

                        # Attach pending shell failure output (one-shot) before sending to LLM
                        cleaned_prompt = maybe_attach_shell_result(conf, cleaned_prompt)

                        # --- Step 7: invoke LLM ---
                        llm_response = invoke_llm(
                            prompt=cleaned_prompt,
                            conf=conf,
                            console=console,
                            plugin_manager=conf.plugin_manager,
                            chat_id=chat_id,
                            verbose=conf.verbose,
                            explicit_source_files=explicit_files,
                            attachments=attachments if attachments else None,
                        )

                        # --- Step 8: clear pending clipboard attachments on success ---
                        # Only clear after invoke_llm returns successfully.
                        # If invoke_llm raises, the outer except catches it
                        # and pending clipboard attachments survive for retry.
                        if clipboard_attachments:
                            clear_pending_clipboard_attachments(conf)

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

    collect_and_send_feedback(max(0, chat_id))
