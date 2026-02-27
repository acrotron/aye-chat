"""LLM response handling - processing and applying LLM responses."""

import traceback
from pathlib import Path
from typing import Any, Optional, List, Dict

from rich import print as rprint
from rich.console import Console
from rich.padding import Padding

from aye.model.snapshot import apply_updates, get_diff_base_for_file
from aye.model.file_processor import make_paths_relative, filter_unchanged_files
from aye.model.models import LLMResponse
from aye.model.autodiff_config import is_autodiff_enabled
from aye.model.write_validator import (
    check_files_against_ignore_patterns,
    is_strict_mode_enabled,
    format_ignored_files_warning,
)
from aye.model.auth import get_user_config
from aye.presenter.repl_ui import (
    print_assistant_response,
    print_error,
    print_files_updated,
    print_no_files_changed,
)
from aye.presenter.diff_presenter import show_diff


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
    # Print the summary if present
    if response.summary:
        print_assistant_response(response.summary)
    
    # Handle chat_id persistence
    new_chat_id = response.chat_id
    if new_chat_id is not None and chat_id_file is not None:
        chat_id_file.write_text(str(new_chat_id))
    
    # Process file updates
    updated_files = response.updated_files or []
    if not updated_files:
        return new_chat_id
    
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
                return new_chat_id
    
    # Apply updates - pass root so files are written to correct location
    try:
        batch_id = apply_updates(updated_files, prompt, root=conf.root)
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
    """
    Handle errors from LLM invocation.
    """
    # Check for auth errors (403)
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        if exc.response.status_code == 403:
            traceback.print_exc()
            print_error(Exception("Unauthorized. Please check your API key."))
            return
    
    print_error(exc)
