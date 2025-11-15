import json
from typing import Any, Optional

from rich.console import Console
from rich import print as rprint

from aye.model.source_collector import collect_sources
from aye.model.api import cli_invoke
from aye.model.models import LLMResponse, LLMSource
from aye.presenter.ui_utils import thinking_spinner

DEBUG = False


def invoke_llm(
    prompt: str,
    conf: Any,
    console: Console,
    plugin_manager: Any,
    chat_id: Optional[int] = None,
    verbose: bool = False
) -> LLMResponse:
    """
    Unified LLM invocation with spinner and routing.
    Tries local model first, falls back to API if needed.
    
    Args:
        prompt: User prompt
        conf: Configuration object with root, file_mask, and selected_model
        console: Rich console for output
        plugin_manager: Plugin manager for local model handling
        chat_id: Optional chat ID for API calls
        verbose: Whether to show verbose output
        
    Returns:
        LLMResponse object with the result
    """
    # Collect source files (Model interaction)
    source_files = collect_sources(conf.root, conf.file_mask)
    
    if verbose:
        rprint(f"[yellow]Included with prompt: {', '.join(source_files.keys())}")
    else:
        rprint("[yellow]Turn on verbose mode to see list of files included with prompt.[/]")
    
    # Use spinner for both local and API invocations (Presenter interaction)
    with thinking_spinner(console):
        # Try local model first (Controller logic)
        local_response = plugin_manager.handle_command("local_model_invoke", {
            "prompt": prompt,
            "model_id": conf.selected_model,
            "source_files": source_files,
            "chat_id": chat_id,
            "root": conf.root
        })
        
        if local_response is not None:
            # Local model handled the request
            return LLMResponse(
                summary=local_response.get("summary", ""),
                updated_files=local_response.get("updated_files", []),
                chat_id=None,
                source=LLMSource.LOCAL
            )
        
        # Fall back to API (Model interaction)
        if DEBUG:
            print(f"[DEBUG] Processing chat message with chat_id={chat_id}, model={conf.selected_model}")
        
        resp = cli_invoke(
            message=prompt,
            chat_id=chat_id or -1,
            source_files=source_files,
            model=conf.selected_model
        )
        
        if DEBUG:
            print(f"[DEBUG] Chat message processed, response keys: {resp.keys() if resp else 'None'}")
    
    # Parse the assistant response (Controller logic)
    assistant_resp_str = resp.get('assistant_response')
    
    if assistant_resp_str is None:
        # Handle case where API response is missing the field entirely
        assistant_resp = {"answer_summary": "No response from assistant.", "source_files": []}
    else:
        try:
            # Attempt to parse as JSON
            assistant_resp = json.loads(assistant_resp_str)
            if DEBUG:
                print(f"[DEBUG] Successfully parsed assistant_response JSON")
        except json.JSONDecodeError as e:
            if DEBUG:
                print(f"[DEBUG] Failed to parse assistant_response as JSON: {e}. Treating as plain text.")
            
            # Check for server-side error messages before treating as plain text
            if "error" in assistant_resp_str.lower():
                chat_title = resp.get('chat_title', 'Unknown')
                raise Exception(f"Server error in chat '{chat_title}': {assistant_resp_str}") from e

            # If not an error, treat the whole string as the summary
            assistant_resp = {
                "answer_summary": assistant_resp_str,
                "source_files": []
            }
    
    return LLMResponse(
        summary=assistant_resp.get("answer_summary", ""),
        updated_files=assistant_resp.get("source_files", []),
        chat_id=resp.get("chat_id"),
        source=LLMSource.API
    )
