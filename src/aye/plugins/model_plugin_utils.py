"""Shared utilities for model plugins."""
import json
from typing import Dict, Any, Optional
from pathlib import Path

from rich import print as rprint

from aye.controller.util import is_truncated_json


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


def get_conversation_id(chat_id: Optional[int] = None) -> str:
    """Get conversation ID for history tracking."""
    return str(chat_id) if chat_id and chat_id > 0 else "default"


def build_user_message(prompt: str, source_files: Dict[str, str]) -> str:
    """Build the full user message with source files appended (for the current API call)."""
    user_message = prompt
    if source_files:
        user_message += "\n\n--- Source files are below. ---\n"
        for file_name, content in source_files.items():
            user_message += f"\n** {file_name} **\n```\n{content}\n```\n"
    return user_message


def build_history_message(prompt: str, source_files: Dict[str, str]) -> str:
    return prompt


def create_error_response(error_msg: str, verbose: bool = False) -> Dict[str, Any]:
    """Create a standardized error response."""
    if verbose:
        rprint(f"[red]{error_msg}[/]")
    return {
        "summary": error_msg,
        "updated_files": []
    }


def parse_llm_response(generated_text: str, debug: bool = False, check_truncation: bool = False) -> Dict[str, Any]:
    """Parse LLM response text and convert to expected format.
    
    Args:
        generated_text: Raw response text from LLM
        debug: Enable debug printing
        check_truncation: If True, check for truncated JSON and return truncation message
    """
    try:
        llm_response = json.loads(generated_text)
    except json.JSONDecodeError as e:
        if debug:
            print(f"JSON decode error: {e}")
        
        if check_truncation and is_truncated_json(generated_text):
            if debug:
                print(f"[DEBUG] Response appears to be truncated:")
                print(generated_text)
            return {
                "summary": TRUNCATED_RESPONSE_MESSAGE,
                "updated_files": []
            }
        
        return {
            "summary": generated_text if generated_text else "No response",
            "updated_files": []
        }
    
    # If the JSON is valid but isn't an object (e.g. null, list, number),
    # treat it as plain text.
    if not isinstance(llm_response, dict):
        return {
            "summary": generated_text,
            "updated_files": []
        }
    
    # Some models wrap response in "properties"
    props = llm_response.get("properties")
    if not props:
        props = llm_response

    result = {
        "summary": props.get("answer_summary", ""),
        "updated_files": [
            {
                "file_name": f.get("file_name"),
                "file_content": f.get("file_content")
            }
            for f in props.get("source_files", [])
            if isinstance(f, dict)
        ]
    }

    if debug:
        print("----- returning from parse_llm_response -----")
        print(result)
    return result


def load_history(history_file: Optional[Path], verbose: bool = False, log_prefix: str = "") -> Dict[str, list]:
    """Load chat history from disk.
    
    Args:
        history_file: Path to history JSON file
        verbose: Enable verbose logging
        log_prefix: Prefix for log messages (e.g., "offline model")
    
    Returns:
        Dictionary of conversations
    """
    if not history_file:
        if verbose:
            rprint(f"[yellow]History file path not set{' for ' + log_prefix if log_prefix else ''}. Skipping load.[/]")
        return {}

    if history_file.exists():
        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            return data.get("conversations", {})
        except Exception as e:
            if verbose:
                rprint(f"[yellow]Could not load{' ' + log_prefix if log_prefix else ''} chat history: {e}[/]")
            return {}
    return {}


def save_history(history_file: Optional[Path], chat_history: Dict[str, list], verbose: bool = False, log_prefix: str = "") -> None:
    """Save chat history to disk.
    
    Args:
        history_file: Path to history JSON file
        chat_history: Dictionary of conversations to save
        verbose: Enable verbose logging
        log_prefix: Prefix for log messages (e.g., "offline model")
    """
    if not history_file:
        if verbose:
            rprint(f"[yellow]History file path not set{' for ' + log_prefix if log_prefix else ''}. Skipping save.[/]")
        return

    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"conversations": chat_history}
        history_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        if verbose:
            rprint(f"[yellow]Could not save{' ' + log_prefix if log_prefix else ''} chat history: {e}[/]")
