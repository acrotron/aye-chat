import json
from typing import Any, Optional, Dict, Tuple, List
from pathlib import Path

from rich.console import Console
from rich import print as rprint

from aye.model.api import cli_invoke
from aye.model.models import LLMResponse, LLMSource, VectorIndexResult
from aye.presenter.ui_utils import thinking_spinner
from aye.model.source_collector import collect_sources

DEBUG = False
CONTEXT_TARGET_SIZE = 180 * 1024  # 180KB, ~40K tokens in English language
CONTEXT_HARD_LIMIT = 200 * 1024   # 200KB, hard safety limit for API payload
RELEVANCE_THRESHOLD = -1.0  # Accept all results from vector search, even with negative scores.


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

    if DEBUG and retrieved_chunks:
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
            
            if current_size + file_size > CONTEXT_HARD_LIMIT:
                if verbose:
                    rprint(f"[yellow]Skipping large file {file_path_str} ({file_size / 1024:.1f}KB) to stay within payload limits.[/]")
                continue
            
            source_files[file_path_str] = content
            current_size += file_size
            
        except Exception as e:
            if verbose:
                rprint(f"[red]Could not read file {file_path_str}: {e}[/red]")
            continue
            
    return source_files


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

    stripped_prompt = prompt.strip()
    if stripped_prompt.lower().startswith('/all') and (len(stripped_prompt) == 4 or stripped_prompt[4].isspace()):
        all_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
        return all_files, True, stripped_prompt[4:].strip()

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
        if DEBUG:
            print(f"[DEBUG] Successfully parsed assistant_response JSON")
    except json.JSONDecodeError as e:
        if DEBUG:
            print(f"[DEBUG] Failed to parse assistant_response as JSON: {e}. Treating as plain text.")
        
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
    
    with thinking_spinner(console):
        # 1. Try local model first
        local_response = plugin_manager.handle_command("local_model_invoke", {
            "prompt": prompt,
            "model_id": conf.selected_model,
            "source_files": source_files,
            "chat_id": chat_id,
            "root": conf.root
        })
        
        if local_response is not None:
            return LLMResponse(
                summary=local_response.get("summary", ""),
                updated_files=local_response.get("updated_files", []),
                chat_id=None,
                source=LLMSource.LOCAL
            )
        
        # 2. Fall back to API
        if DEBUG:
            print(f"[DEBUG] Processing chat message with chat_id={chat_id or -1}, model={conf.selected_model}")
        
        api_resp = cli_invoke(
            message=prompt,
            chat_id=chat_id or -1,
            source_files=source_files,
            model=conf.selected_model
        )
        
        if DEBUG:
            print(f"[DEBUG] Chat message processed, response keys: {api_resp.keys() if api_resp else 'None'}")

    # 3. Parse API response
    assistant_resp, new_chat_id = _parse_api_response(api_resp)
    
    return LLMResponse(
        summary=assistant_resp.get("answer_summary", ""),
        updated_files=assistant_resp.get("source_files", []),
        chat_id=new_chat_id,
        source=LLMSource.API
    )
