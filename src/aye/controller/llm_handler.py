from pathlib import Path
from typing import Any, Optional

from rich import print as rprint
from rich.console import Console

from aye.presenter.repl_ui import (
    print_assistant_response,
    print_no_files_changed,
    print_files_updated,
    print_error
)
from aye.model.snapshot import apply_updates
from aye.model.file_processor import filter_unchanged_files, make_paths_relative
from aye.model.models import LLMResponse


def process_llm_response(
    response: LLMResponse,
    conf: Any,
    console: Console,
    prompt: str,
    chat_id_file: Optional[Path] = None
) -> Optional[int]:
    """
    Unified handler for LLM responses from any source (API or local model).
    Acts as a Presenter in MVP, orchestrating model and view updates.
    
    Args:
        response: Standardized LLM response
        conf: Configuration object with root path
        console: Rich console for output
        prompt: Original user prompt for snapshot metadata
        chat_id_file: Optional path to store chat ID
        
    Returns:
        New chat_id if present, None otherwise
    """
    # Store new chat ID if present (only for API responses)
    new_chat_id = None
    if response.chat_id is not None and chat_id_file:
        new_chat_id = response.chat_id
        chat_id_file.parent.mkdir(parents=True, exist_ok=True)
        chat_id_file.write_text(str(new_chat_id), encoding="utf-8")
    
    # Display assistant response summary (View update)
    if response.summary:
        print_assistant_response(response.summary)
    
    # Process file updates
    updated_files = response.updated_files
    
    # Filter unchanged files (Controller/Model logic)
    updated_files = filter_unchanged_files(updated_files)
    
    # Normalize file paths (Controller/Model logic)
    updated_files = make_paths_relative(updated_files, conf.root)
    
    if not updated_files:
        print_no_files_changed(console)
    else:
        # Apply updates to the model (Model update)
        try:
            apply_updates(updated_files, prompt)
            file_names = [item.get("file_name") for item in updated_files if "file_name" in item]
            if file_names:
                # Update the view
                print_files_updated(console, file_names)
        except Exception as e:
            rprint(f"[red]Error applying updates:[/] {e}")
    
    return new_chat_id


def handle_llm_error(exc: Exception) -> None:
    """
    Unified error handler for LLM invocation errors.
    
    Args:
        exc: The exception that occurred
    """
    import traceback
    
    if hasattr(exc, "response") and getattr(exc.response, "status_code", None) == 403:
        traceback.print_exc()
        print_error(
            Exception(
                "[red]❌ Unauthorized:[/] the stored token is invalid or missing.\n"
                "Log in again with `aye auth login` or set a valid "
                "`AYE_TOKEN` environment variable.\n"
                "Obtain your personal access token at https://ayechat.ai"
            )
        )
    else:
        print_error(exc)
