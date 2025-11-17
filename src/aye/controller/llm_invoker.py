import json
from typing import Any, Optional, Dict
from pathlib import Path

from rich.console import Console
from rich import print as rprint

from aye.model.api import cli_invoke
from aye.model.models import LLMResponse, LLMSource
from aye.presenter.ui_utils import thinking_spinner
from aye.model.source_collector import collect_sources

DEBUG = False
CONTEXT_TARGET_SIZE = 180 * 1024  # 180KB, ~40K tokens in English language
CONTEXT_HARD_LIMIT = 200 * 1024   # 200KB, hard safety limit for API payload
RELEVANCE_THRESHOLD = -1.0  # Accept all results from vector search, even with negative scores.


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
    By default, uses RAG for context. Use '/all' prefix to include all project files.
    
    Args:
        prompt: User prompt
        conf: Configuration object with root, file_mask, and selected_model
        console: Rich console for output
        plugin_manager: Plugin manager for local model handling
        chat_id: Optional chat ID for API calls
        verbose: Whether to show verbose output
        explicit_source_files: Optional dict of source files to include, bypassing RAG.
        
    Returns:
        LLMResponse object with the result
    """
    source_files = {}
    use_all_files = False

    if explicit_source_files is not None:
        source_files = explicit_source_files
    else:
        # Check for /all command to include all project files
        stripped_prompt = prompt.strip()
        if stripped_prompt.lower().startswith('/all'):
            if len(stripped_prompt) == 4 or stripped_prompt[4].isspace():
                use_all_files = True
                prompt = stripped_prompt[4:].strip()

        if use_all_files:
            # ALL FILES MODE: Include all project files
            source_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
        else:
            # --- NEW: Decide between RAG and sending all files based on project size ---
            all_project_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
            total_size = sum(len(content.encode('utf-8')) for content in all_project_files.values())

            if total_size < CONTEXT_HARD_LIMIT:
                # Project is small enough, send all files.
                if verbose:
                    rprint(f"[cyan]Project size ({total_size / 1024:.1f}KB) is small; including all files.[/]")
                source_files = all_project_files
                use_all_files = True  # This ensures the correct message is printed later
            else:
                # DEFAULT MODE (RAG): Project is large, retrieve context using the vector index.
                if hasattr(conf, 'index_manager') and conf.index_manager:
                    rprint("[cyan]Searching for relevant context...[/]")
                    retrieved_chunks = conf.index_manager.query(
                        prompt,
                        n_results=300,
                        min_relevance=RELEVANCE_THRESHOLD
                    )

                    if DEBUG and retrieved_chunks:
                        rprint("[yellow]Retrieved context chunks (by relevance):[/]")
                        for chunk in retrieved_chunks:
                            rprint(f"  - Score: {chunk.score:.4f}, File: {chunk.file_path}")
                        rprint()

                    if retrieved_chunks:
                        # Get a ranked list of unique file paths from the sorted chunks
                        unique_files_ranked = []
                        seen_files = set()
                        for chunk in retrieved_chunks:
                            if chunk.file_path not in seen_files:
                                unique_files_ranked.append(chunk.file_path)
                                seen_files.add(chunk.file_path)

                        # --- Context Packing Logic ---
                        # Add files by relevance, filling up to the soft limit (CONTEXT_TARGET_SIZE).
                        # Skip any single file that would push the total size over the hard limit.
                        current_size = 0
                        for file_path_str in unique_files_ranked:
                            # Stop if we've already packed enough context (soft limit).
                            if current_size > CONTEXT_TARGET_SIZE:
                                break
                            
                            try:
                                full_path = conf.root / file_path_str
                                if not full_path.is_file():
                                    continue
                                
                                content = full_path.read_text(encoding="utf-8")
                                file_size = len(content.encode('utf-8'))
                                
                                # Check if adding this file would exceed the hard limit.
                                if current_size + file_size > CONTEXT_HARD_LIMIT:
                                    if verbose:
                                        rprint(f"[yellow]Skipping large file {file_path_str} ({file_size / 1024:.1f}KB) to stay within payload limits.[/]")
                                    continue # Skip this file and try the next one.
                                
                                source_files[file_path_str] = content
                                current_size += file_size
                                
                            except Exception as e:
                                if verbose:
                                    rprint(f"[red]Could not read file {file_path_str}: {e}[/red]")
                                continue
    
    if verbose:
        if source_files:
            rprint(f"[yellow]Included with prompt: {', '.join(source_files.keys())}[/]")
        else:
            rprint("[yellow]No files found to include with prompt.[/]")
    else:
        if source_files:
            if use_all_files:
                rprint(f"[cyan]Including all {len(source_files)} project file(s).[/]")
            elif explicit_source_files is not None:
                rprint(f"[cyan]Including {len(source_files)} specified file(s).[/]")
            else:
                rprint(f"[cyan]Found {len(source_files)} relevant file(s).[/]")
        else:
            rprint("[yellow]No files found. Sending prompt without code context.[/]")
    
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
            print(f"[DEBUG] Processing chat message with chat_id={chat_id or -1}, model={conf.selected_model}")
        
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
