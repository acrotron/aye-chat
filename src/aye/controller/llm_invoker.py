import json
from typing import Any, Optional, Dict, Tuple, List
from pathlib import Path

from rich.console import Console
from rich import print as rprint

from aye.model.api import cli_invoke
from aye.model.models import LLMResponse, LLMSource, VectorIndexResult
from aye.presenter.ui_utils import thinking_spinner
from aye.model.source_collector import collect_sources
from aye.model.auth import get_user_config
from aye.model.offline_llm_manager import is_offline_model
from aye.controller.util import is_truncated_json
from aye.model.config import SYSTEM_PROMPT

import os


def _is_debug():
    return get_user_config("debug", "off").lower() == "on"


def _get_int_env(name: str, default: int) -> int:
    """Read an environment variable as int, with a safe default.

    If the variable is unset or cannot be parsed as an integer, the default
    value is returned.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


CONTEXT_TARGET_SIZE = _get_int_env(
    "AYE_CONTEXT_TARGET", 150 * 1024
)  # 180KB, ~40K tokens in English language
CONTEXT_HARD_LIMIT = _get_int_env(
    "AYE_CONTEXT_HARD_LIMIT", 170 * 1024
)  # 200KB, hard safety limit for API payload
RELEVANCE_THRESHOLD = -1.0  # Accept all results from vector search, even with negative scores.

# Message shown when LLM response is truncated due to output token limits
TRUNCATED_RESPONSE_MESSAGE = (
    "It looks like my response was cut off because it exceeded the output limit. "
    "This usually happens when you ask me to generate or modify many files at once.\n\n"
    "**To fix this, please try:**\n"
    "1. Break your request into smaller parts (e.g., one file at a time)\n"
    "2. Use the `with` command to focus on specific files: `with file1.py, file2.py: your request`\n"
    "3. Ask me to work on fewer files or smaller changes in each request\n\n"
    "For example, instead of 'update all files to add logging', try:\n"
    "  `with src/main.py: add logging to this file`"
)


def _get_rag_context_files(
    prompt: str, conf: Any, verbose: bool
) -> Dict[str, str]:
    """
    Queries the vector index and packs the most relevant files into a dictionary,
    respecting context size limits.
    """
    source_files = {}
    if not hasattr(conf, 'index_manager') or not conf.index_manager:
        return source_files

    if verbose:
        rprint("[cyan]Searching for relevant context...[/]")

    retrieved_chunks: List[VectorIndexResult] = conf.index_manager.query(
        prompt, n_results=300, min_relevance=RELEVANCE_THRESHOLD
    )

    if _is_debug() and retrieved_chunks:
        rprint("[yellow]Retrieved context chunks (by relevance):[/]")
        for chunk in retrieved_chunks:
            rprint(f"  - Score: {chunk.score:.4f}, File: {chunk.file_path}")
        rprint()

    if not retrieved_chunks:
        return source_files

    # Get a ranked list of unique file paths from the sorted chunks
    unique_files_ranked = []
    seen_files = set()
    for chunk in retrieved_chunks:
        if chunk.file_path not in seen_files:
            unique_files_ranked.append(chunk.file_path)
            seen_files.add(chunk.file_path)

    # --- Context Packing Logic ---
    # Track files with their sizes for potential trimming
    files_with_sizes: List[Tuple[str, str, int]] = []  # (path, content, size)
    current_size = 0
    
    for file_path_str in unique_files_ranked:
        if current_size > CONTEXT_TARGET_SIZE:
            break
        
        try:
            full_path = conf.root / file_path_str
            if not full_path.is_file():
                continue
            
            content = full_path.read_text(encoding="utf-8")
            file_size = len(content.encode('utf-8'))
            
            # Skip individual files that are too large
            if current_size + file_size > CONTEXT_HARD_LIMIT:
                if verbose:
                    rprint(f"[yellow]Skipping large file {file_path_str} ({file_size / 1024:.1f}KB) to stay within payload limits.[/]")
                continue
            
            files_with_sizes.append((file_path_str, content, file_size))
            current_size += file_size
            
        except Exception as e:
            if verbose:
                rprint(f"[red]Could not read file {file_path_str}: {e}[/red]")
            continue
    
    # Final safety check: ensure total size is under CONTEXT_HARD_LIMIT
    # This handles edge cases where we accumulated files close to TARGET but over HARD_LIMIT
    while current_size > CONTEXT_HARD_LIMIT and files_with_sizes:
        # Remove the last (least relevant) file
        removed_path, _, removed_size = files_with_sizes.pop()
        current_size -= removed_size
        if verbose:
            rprint(f"[yellow]Trimmed {removed_path} ({removed_size / 1024:.1f}KB) to stay within hard limit.[/]")
    
    # Build the final source_files dict
    for file_path_str, content, _ in files_with_sizes:
        source_files[file_path_str] = content
            
    return source_files


def _is_large_project(conf: Any) -> bool:
    """
    Check if this is a large project that should use RAG instead of full file collection.
    
    Uses the index manager's state to determine this without doing a full file walk.
    A project is considered "large" if:
    - Index manager exists and is initialized
    - Index manager has indexed files (collection exists with documents)
    - OR async discovery was triggered (indicating 1000+ files)
    """
    if not hasattr(conf, 'index_manager') or not conf.index_manager:
        return False
    
    index_manager = conf.index_manager
    
    # If discovery is in progress or was triggered, it's a large project
    if index_manager.is_discovering:
        return True
    
    # If we have a collection with documents, check if it's substantial
    if index_manager.collection:
        try:
            count = index_manager.collection.count()
            # If we have more than ~50 indexed chunks, treat as large project
            # (small projects typically have fewer chunks)
            if count > 50:
                return True
        except Exception:
            pass
    
    # Check if we have work queued (indicates large project discovery happened)
    if index_manager._state.coarse_total > 100 or index_manager._state.refine_total > 100:
        return True
    
    return False


def _determine_source_files(
    prompt: str, conf: Any, verbose: bool, explicit_source_files: Optional[Dict[str, str]]
) -> Tuple[Dict[str, str], bool, str]:
    """
    Determines the set of source files to include with the prompt based on user commands,
    project size, or RAG.
    Returns a tuple of (source_files, use_all_files_flag, updated_prompt).
    """
    if explicit_source_files is not None:
        return explicit_source_files, False, prompt

    # Quick check: Skip expensive scanning in home directory (no indexing, empty context)
    if conf.root == Path.home():
        if verbose:
            rprint("[cyan]In home directory: skipping file scan, using empty context.[/]")
        return {}, False, prompt

    stripped_prompt = prompt.strip()
    if stripped_prompt.lower().startswith('/all') and (len(stripped_prompt) == 4 or stripped_prompt[4].isspace()):
        all_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
        return all_files, True, stripped_prompt[4:].strip()

    # For large projects, skip the expensive collect_sources() call and go straight to RAG
    # This avoids blocking the main thread with a full file walk
    if _is_large_project(conf):
        if verbose:
            rprint("[cyan]Large project detected, using code lookup for context...[/]")
        rag_files = _get_rag_context_files(prompt, conf, verbose)
        return rag_files, False, prompt

    # For small/unknown projects, do the traditional size check
    all_project_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
    total_size = sum(len(content.encode('utf-8')) for content in all_project_files.values())

    if total_size < CONTEXT_HARD_LIMIT:
        if verbose:
            rprint(f"[cyan]Project size ({total_size / 1024:.1f}KB) is small; including all files.[/]")
        return all_project_files, True, prompt

    # Default to RAG for large projects
    rag_files = _get_rag_context_files(prompt, conf, verbose)
    return rag_files, False, prompt


def _print_context_message(
    source_files: Dict[str, str], use_all_files: bool, explicit_source_files: Optional[Dict[str, str]], verbose: bool
):
    """Prints a message indicating which files are being included."""
    if verbose:
        if source_files:
            if verbose:
                rprint(f"[yellow]Included with prompt: {', '.join(source_files.keys())}[/]")
            else:
                rprint(f"[yellow]To see list of files included with prompt turn verbose on[/]")
        else:
            rprint("[yellow]No files found to include with prompt.[/]")
        return

    if not source_files and verbose:
        rprint("[yellow]No files found. Sending prompt without code context.[/]")
        return

    if verbose:
        if use_all_files:
            rprint(f"[cyan]Including all {len(source_files)} project file(s).[/]")
        elif explicit_source_files is not None:
            rprint(f"[cyan]Including {len(source_files)} specified file(s).[/]")
        else:
            rprint(f"[cyan]Found {len(source_files)} relevant file(s).[/]")


def _parse_api_response(resp: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[int]]:
    """
    Parses the JSON response from the API, handling errors and plain text fallbacks.
    Returns a tuple of (parsed_content, chat_id).
    """
    assistant_resp_str = resp.get('assistant_response')
    chat_id = resp.get("chat_id")

    if assistant_resp_str is None:
        parsed = {"answer_summary": "No response from assistant.", "source_files": []}
        return parsed, chat_id

    try:
        parsed = json.loads(assistant_resp_str)
        if _is_debug():
            print(f"[DEBUG] Successfully parsed assistant_response JSON")
    except json.JSONDecodeError as e:
        if _is_debug():
            print(f"[DEBUG] Failed to parse assistant_response as JSON: {e}. Checking for truncation.")
            print(f"[DEBUG] LLM response: {resp}")
        
        # Check if this looks like a truncated response
        if is_truncated_json(assistant_resp_str):
            if _is_debug():
                print(f"[DEBUG] Response appears to be truncated:")
                print(assistant_resp_str)
            parsed = {"answer_summary": TRUNCATED_RESPONSE_MESSAGE, "source_files": []}
            return parsed, chat_id
        
        if "error" in assistant_resp_str.lower():
            chat_title = resp.get('chat_title', 'Unknown')
            raise Exception(f"Server error in chat '{chat_title}': {assistant_resp_str}") from e

        parsed = {"answer_summary": assistant_resp_str, "source_files": []}
        
    return parsed, chat_id


def invoke_llm(
    prompt: str,
    conf: Any,
    console: Console,
    plugin_manager: Any,
    chat_id: Optional[int] = None,
    verbose: bool = False,
    explicit_source_files: Optional[Dict[str, str]] = None
) -> LLMResponse:
    """
    Unified LLM invocation with spinner and routing.
    Determines context, invokes the appropriate model (local or API), and parses the response.
    """
    source_files, use_all_files, prompt = _determine_source_files(
        prompt, conf, verbose, explicit_source_files
    )
   
    _print_context_message(source_files, use_all_files, explicit_source_files, verbose)
    
    # Get the system prompt to use (custom or default)
    system_prompt = conf.ground_truth if hasattr(conf, 'ground_truth') and conf.ground_truth else SYSTEM_PROMPT
    
    # Progressive messages for the spinner
    spinner_messages = [
        "Building prompt...",
        "Sending to LLM...",
        "Waiting for response...",
        "Still waiting...",
        "This is taking longer than usual..."
    ]
    
    with thinking_spinner(console, messages=spinner_messages, interval=15.0):
        # 1. Try local/offline model plugins first
        local_response = plugin_manager.handle_command("local_model_invoke", {
            "prompt": prompt,
            "model_id": conf.selected_model,
            "source_files": source_files,
            "chat_id": chat_id,
            "root": conf.root,
            "system_prompt": system_prompt
        })

        if local_response is not None:
            return LLMResponse(
                summary=local_response.get("summary", ""),
                updated_files=local_response.get("updated_files", []),
                chat_id=None,
                source=LLMSource.LOCAL
            )
        
        # 2. Fall back to API for non-plugin models (e.g. official OpenAI, Anthropic)
        if _is_debug():
            print(f"[DEBUG] Processing chat message with chat_id={chat_id or -1}, model={conf.selected_model}")
        
        api_resp = cli_invoke(
            message=prompt,
            chat_id=chat_id or -1,
            source_files=source_files,
            model=conf.selected_model,
            system_prompt=system_prompt
        )
        
        if _is_debug():
            print(f"[DEBUG] Chat message processed, response keys: {api_resp.keys() if api_resp else 'None'}")

    # 3. Parse API response
    assistant_resp, new_chat_id = _parse_api_response(api_resp)
    
    return LLMResponse(
        summary=assistant_resp.get("answer_summary", ""),
        updated_files=assistant_resp.get("source_files", []),
        chat_id=new_chat_id,
        source=LLMSource.API
    )

