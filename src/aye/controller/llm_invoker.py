import json
from typing import Any, Optional, Dict, Tuple, List
from pathlib import Path

from rich.console import Console
from rich import print as rprint

from aye.model.api import cli_invoke
from aye.model.models import LLMResponse, LLMSource, VectorIndexResult
from aye.presenter.streaming_ui import StreamingResponseDisplay, create_streaming_callback
from aye.presenter.ui_utils import StoppableSpinner, DEFAULT_THINKING_MESSAGES
from aye.model.source_collector import collect_sources
from aye.model.auth import get_user_config
from aye.model.offline_llm_manager import is_offline_model
from aye.controller.util import is_truncated_json
from aye.model.config import SYSTEM_PROMPT, MODELS, DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_CONTEXT_TARGET_KB
from aye.model import telemetry

import os


def _is_debug():
    return get_user_config("debug", "off").lower() == "on"


def _get_int_env(name: str, default: int) -> int:
    """Read an environment variable as int, with a safe default."""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


RELEVANCE_THRESHOLD = -1.0

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


def _get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific model."""
    for model in MODELS:
        if model["id"] == model_id:
            return model
    return None


def _get_context_target_size(model_id: str) -> int:
    """Get the target context size for RAG queries based on model configuration."""
    env_override = os.environ.get("AYE_CONTEXT_TARGET")
    if env_override is not None:
        try:
            return int(env_override)
        except ValueError:
            pass

    model_config = _get_model_config(model_id)
    if model_config and "context_target_kb" in model_config:
        return model_config["context_target_kb"] * 1024

    return DEFAULT_CONTEXT_TARGET_KB * 1024


def _get_context_hard_limit(model_id: str) -> int:
    """Get the hard limit for context size based on model configuration."""
    model_config = _get_model_config(model_id)
    if model_config and "max_prompt_kb" in model_config:
        return model_config["max_prompt_kb"] * 1024
    return 170 * 1024


def _filter_ground_truth(files: Dict[str, str], conf: Any, verbose: bool) -> Dict[str, str]:
    """Exclude the ground truth file from source files."""
    if not hasattr(conf, 'ground_truth') or not conf.ground_truth:
        return files

    gt_content_stripped = conf.ground_truth.strip()
    files_to_remove = [
        path for path, content in files.items()
        if content.strip() == gt_content_stripped
    ]

    if not files_to_remove:
        return files

    filtered_files = files.copy()
    for path in files_to_remove:
        if verbose:
            rprint(f"[yellow]Excluding ground truth file from context: {path}[/]")
        del filtered_files[path]

    return filtered_files


def _get_rag_context_files(prompt: str, conf: Any, verbose: bool) -> Dict[str, str]:
    """Queries the vector index and packs the most relevant files into a dictionary."""
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

    unique_files_ranked = []
    seen_files = set()
    for chunk in retrieved_chunks:
        if chunk.file_path not in seen_files:
            unique_files_ranked.append(chunk.file_path)
            seen_files.add(chunk.file_path)

    context_target_size = _get_context_target_size(conf.selected_model)
    context_hard_limit = _get_context_hard_limit(conf.selected_model)

    if _is_debug():
        rprint(f"[yellow]Context target: {context_target_size / 1024:.1f}KB, hard limit: {context_hard_limit / 1024:.1f}KB[/]")

    files_with_sizes: List[Tuple[str, str, int]] = []
    current_size = 0

    for file_path_str in unique_files_ranked:
        if current_size > context_target_size:
            break

        try:
            full_path = conf.root / file_path_str
            if not full_path.is_file():
                continue

            content = full_path.read_text(encoding="utf-8")
            file_size = len(content.encode('utf-8'))

            if current_size + file_size > context_hard_limit:
                if verbose:
                    rprint(f"[yellow]Skipping large file {file_path_str} ({file_size / 1024:.1f}KB) to stay within payload limits.[/]")
                continue

            files_with_sizes.append((file_path_str, content, file_size))
            current_size += file_size

        except Exception as e:
            if verbose:
                rprint(f"[red]Could not read file {file_path_str}: {e}[/red]")
            continue

    while current_size > context_hard_limit and files_with_sizes:
        removed_path, _, removed_size = files_with_sizes.pop()
        current_size -= removed_size
        if verbose:
            rprint(f"[yellow]Trimmed {removed_path} ({removed_size / 1024:.1f}KB) to stay within hard limit.[/]")

    for file_path_str, content, _ in files_with_sizes:
        source_files[file_path_str] = content

    return source_files


def _determine_source_files(
    prompt: str, conf: Any, verbose: bool, explicit_source_files: Optional[Dict[str, str]]
) -> Tuple[Dict[str, str], bool, str]:
    """Determines the set of source files to include with the prompt."""
    if explicit_source_files is not None:
        return explicit_source_files, False, prompt

    if conf.root == Path.home():
        if verbose:
            rprint("[cyan]In home directory: skipping file scan, using empty context.[/]")
        return {}, False, prompt

    stripped_prompt = prompt.strip()
    if stripped_prompt.lower().startswith('/all') and (len(stripped_prompt) == 4 or stripped_prompt[4].isspace()):
        all_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
        all_files = _filter_ground_truth(all_files, conf, verbose)
        return all_files, True, stripped_prompt[4:].strip()

    if hasattr(conf, 'use_rag') and not conf.use_rag:
        if verbose:
            rprint("[cyan]Small project mode: including all files.[/]")
        all_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
        all_files = _filter_ground_truth(all_files, conf, verbose)
        return all_files, True, prompt

    if hasattr(conf, 'index_manager') and conf.index_manager:
        if verbose:
            rprint("[cyan]Using code lookup for context...[/]")
        rag_files = _get_rag_context_files(prompt, conf, verbose)
        rag_files = _filter_ground_truth(rag_files, conf, verbose)
        return rag_files, False, prompt

    all_project_files = collect_sources(root_dir=str(conf.root), file_mask=conf.file_mask)
    all_project_files = _filter_ground_truth(all_project_files, conf, verbose)

    total_size = sum(len(content.encode('utf-8')) for content in all_project_files.values())
    context_hard_limit = _get_context_hard_limit(conf.selected_model)

    if total_size < context_hard_limit:
        if verbose:
            rprint(f"[cyan]Project size ({total_size / 1024:.1f}KB) is small; including all files.[/]")
        return all_project_files, True, prompt

    if verbose:
        rprint("[yellow]Large project without index: using empty context.[/]")
    return {}, False, prompt


def _print_context_message(
    source_files: Dict[str, str], use_all_files: bool, explicit_source_files: Optional[Dict[str, str]], verbose: bool
):
    """Prints a message indicating which files are being included."""
    if verbose:
        if source_files:
            rprint(f"[yellow]Included with prompt: {', '.join(source_files.keys())}[/]")
        else:
            rprint("[yellow]No files found to include with prompt.[/]")
        return

    if not source_files and verbose:
        rprint("[yellow]No files found. Sending prompt without code context.[/]")
        return


def _parse_api_response(resp: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[int]]:
    """Parses the JSON response from the API."""
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

        if is_truncated_json(assistant_resp_str):
            if _is_debug():
                print("[DEBUG] Response appears to be truncated")
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
    Unified LLM invocation with streaming display and routing.
    """
    source_files, use_all_files, prompt = _determine_source_files(
        prompt, conf, verbose, explicit_source_files
    )

    _print_context_message(source_files, use_all_files, explicit_source_files, verbose)

    system_prompt = conf.ground_truth if hasattr(conf, 'ground_truth') and conf.ground_truth else SYSTEM_PROMPT

    model_config = _get_model_config(conf.selected_model)
    max_output_tokens = model_config.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS) if model_config else DEFAULT_MAX_OUTPUT_TOKENS

    # 1. Try local/offline model plugins first (no streaming UI for local models)
    local_response = plugin_manager.handle_command("local_model_invoke", {
        "prompt": prompt,
        "model_id": conf.selected_model,
        "source_files": source_files,
        "chat_id": chat_id,
        "root": conf.root,
        "system_prompt": system_prompt,
        "max_output_tokens": max_output_tokens
    })

    if local_response is not None:
        return LLMResponse(
            summary=local_response.get("summary", ""),
            updated_files=local_response.get("updated_files", []),
            chat_id=None,
            source=LLMSource.LOCAL
        )

    # 2. API call with spinner + streaming display
    if _is_debug():
        print(f"[DEBUG] Processing chat message with chat_id={chat_id or -1}, model={conf.selected_model}")

    telemetry_payload = telemetry.build_payload(top_n=20) if telemetry.is_enabled() else None

    # Create spinner - will be stopped when streaming starts
    spinner = StoppableSpinner(
        console,
        messages=DEFAULT_THINKING_MESSAGES,
        interval=15.0
    )
    
    def stop_spinner():
        """Callback to stop spinner when first content arrives."""
        spinner.stop()
    
    # Create streaming display with callback to stop spinner on first content
    streaming_display = StreamingResponseDisplay(on_first_content=stop_spinner)
    stream_callback = create_streaming_callback(streaming_display)

    try:
        # Start the spinner before the API call
        spinner.start()
        
        api_resp = cli_invoke(
            message=prompt,
            chat_id=chat_id or -1,
            source_files=source_files,
            model=conf.selected_model,
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            telemetry=telemetry_payload,
            on_stream_update=stream_callback
        )
    finally:
        # Ensure spinner is stopped (in case no streaming content was received)
        spinner.stop()
        
        # Always stop the streaming display when done
        if streaming_display.is_active():
            streaming_display.stop()

    if telemetry_payload is not None:
        telemetry.reset()

    if _is_debug():
        print(f"[DEBUG] Chat message processed, response keys: {api_resp.keys() if api_resp else 'None'}")

    # Check if we already displayed the response via streaming
    streamed_summary = bool(api_resp.get("_streamed_summary")) if isinstance(api_resp, dict) else False

    # 3. Parse API response
    assistant_resp, new_chat_id = _parse_api_response(api_resp)

    return LLMResponse(
        summary="" if streamed_summary else assistant_resp.get("answer_summary", ""),
        updated_files=assistant_resp.get("source_files", []),
        chat_id=new_chat_id,
        source=LLMSource.API
    )
