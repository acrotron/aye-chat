"""LLM response handling - processing and applying LLM responses."""

import traceback
from pathlib import Path
from typing import Any, Optional, List, Dict

from rich import print as rprint
from rich.console import Console
from rich.padding import Padding

from aye.presenter.repl_ui import (
    print_assistant_response,
    print_no_files_changed,
    print_files_updated,
    print_error,
    set_last_assistant_response,
)
from aye.model.auth import get_user_config
from aye.presenter.diff_presenter import show_diff

from aye.model.api import ApiError
from aye.model.snapshot import apply_updates, get_diff_base_for_file
from aye.model.file_processor import make_paths_relative, filter_unchanged_files, fix_duplicated_paths
from aye.model.models import LLMResponse
from aye.model.autodiff_config import is_autodiff_enabled
from aye.model.write_validator import (
    check_files_against_ignore_patterns,
    is_strict_mode_enabled,
    format_ignored_files_warning,
)


def process_llm_response(
    response: LLMResponse,
    conf: Any,
    console: Console,
    prompt: str,
    chat_id_file: Optional[Path] = None,
) -> Optional[int]:
    """
    Process an LLM response: print summary, apply file updates, show diffs.
    
    Args:
        response: The LLM response object
        conf: Configuration object with root path
        console: Rich console for output
        prompt: The original prompt (for snapshot metadata)
        chat_id_file: Optional path to save chat_id
    
    Returns:
        New chat_id if available, None otherwise
    """
    new_chat_id = None
    if response.chat_id is not None and chat_id_file:
        new_chat_id = response.chat_id
        chat_id_file.parent.mkdir(parents=True, exist_ok=True)
        chat_id_file.write_text(str(new_chat_id), encoding="utf-8")

    # Always capture the summary for `raw` / `printraw` when present.
    # (Even if it was already printed via streaming UI.)
    if response.summary and response.summary.strip():
        set_last_assistant_response(response.summary)

        # Only print if it was not already rendered by streaming UI.
        if not getattr(response, "summary_already_printed", False):
            print_assistant_response(response.summary)

    # Process file updates
    updated_files = response.updated_files or []
    if not updated_files:
        print_no_files_changed(console)
        return new_chat_id
    
    # Fix duplicated path segments (e.g. src/src/file.txt -> src/file.txt)
    updated_files = fix_duplicated_paths(updated_files, conf.root)

    # Filter unchanged files (pass root for proper path resolution)
    updated_files = filter_unchanged_files(updated_files, conf.root)

    # Make paths relative to project root
    updated_files = make_paths_relative(updated_files, conf.root)
    
    if not updated_files:
        print_no_files_changed(console)
        return new_chat_id
    
    # Check against ignore patterns
    allowed_files, ignored_files = check_files_against_ignore_patterns(
        updated_files, conf.root
    )
    
    if ignored_files:
        strict_mode = is_strict_mode_enabled()
        warning_msg = format_ignored_files_warning(ignored_files, strict_mode)
        rprint(warning_msg)
        
        if strict_mode:
            # In strict mode, only write allowed files
            updated_files = allowed_files
            if not updated_files:
                print_no_files_changed(console)
                return new_chat_id
    
    # Apply updates - pass root so files are written to correct location
    try:
        root_path = Path(conf.root) if hasattr(conf, 'root') else Path.cwd()
        batch_id = apply_updates(updated_files, prompt, root=root_path)
    except Exception as e:
        rprint(f"[red]Error applying updates:[/] {e}")
        return new_chat_id
    
    # Print updated files
    file_names = [f.get("file_name", "unknown") for f in updated_files]
    print_files_updated(console, file_names)
    
    # Show restore tip (once per session, if user hasn't used restore before)
    _maybe_show_restore_tip(conf, console)
    
    # Auto-diff if enabled
    if is_autodiff_enabled():
        _show_autodiffs(batch_id, updated_files, conf.root)
    
    return new_chat_id


def _maybe_show_restore_tip(conf: Any, console: Console) -> None:
    """
    Show a tip about the restore command, but only:
    - Once per session
    - If the user has never used restore before (global config)
    """
    # Check global "has used restore" flag
    restore_used = get_user_config("restore_used", "off").lower() == "on"
    if restore_used:
        return
    
    # Check per-session flag
    if getattr(conf, "_restore_tip_shown", False):
        return
    
    # Show the tip
    tip_text = (
        "[dim]Tip: You can roll back these changes with [bold]restore[/bold] "
        "or [bold]undo[/bold][/dim]"
    )
    console.print(Padding(tip_text, (1, 0, 0, 0)))
    
    # Mark as shown for this session
    conf._restore_tip_shown = True


def _show_autodiffs(batch_id: str, updated_files: List[Dict[str, str]], root: Path) -> None:
    """
    Show diffs for all updated files when autodiff is enabled.
    """
    for file_dict in updated_files:
        file_name = file_dict.get("file_name")
        if not file_name:
            continue
        
        # Resolve the file path against root
        if not Path(file_name).is_absolute():
            file_path = root / file_name
        else:
            file_path = Path(file_name)
        
        # Get the snapshot reference for diffing
        diff_base = get_diff_base_for_file(batch_id, file_path)
        if diff_base is None:
            continue
        
        snapshot_ref, is_git_ref = diff_base
        
        if is_git_ref:
            # Git-based diff (future GitRefBackend)
            # For now, skip - would need git show integration
            pass
        else:
            # File-based diff
            show_diff(file_path, Path(snapshot_ref))


def handle_llm_error(exc: Exception) -> None:
    """Unified error handler for LLM invocation errors.

    Provides actionable guidance based on the specific error type rather
    than showing a generic message for all failures.
    """
    status = None

    # Extract HTTP status from ApiError or from a raw httpx response attribute
    if isinstance(exc, ApiError):
        status = exc.status_code
    elif hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        status = exc.response.status_code

    if status == 401 or status == 403:
        rprint(
            "[red]Authentication error:[/] the stored token is invalid or missing.\n"
            "Log in again with `aye auth login` or set a valid "
            "`AYE_TOKEN` environment variable.\n"
            "Obtain your personal access token at https://ayechat.ai"
        )
    elif status == 429:
        rprint(
            "[yellow]Rate limit reached:[/] too many requests in a short period.\n"
            "Please wait a moment and try again."
        )
    elif status is not None and 500 <= status <= 599:
        rprint(
            f"[red]Server error (HTTP {status}):[/] the API encountered an internal problem.\n"
            "This is not caused by your local code. Please try again shortly.\n"
            "If the problem persists, check https://ayechat.ai for service status."
        )
    elif status == 400 or status == 422:
        rprint(
            f"[red]Request error (HTTP {status}):[/] {exc}\n"
            "This may be caused by an oversized prompt or unsupported content.\n"
            "Try reducing the number of included files or simplifying your prompt."
        )
    elif isinstance(exc, TimeoutError):
        rprint(
            "[yellow]Request timed out:[/] the LLM took too long to respond.\n"
            "This can happen with large prompts. Try again or reduce context size."
        )
    else:
        print_error(exc)
