# models.py
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum


class LLMSource(Enum):
    """Enumeration for LLM response sources."""
    LOCAL = "local"
    API = "api"


@dataclass
class LLMResponse:
    """
    Standardized response format for LLM interactions.
    
    Attributes:
        summary: The text summary/response from the LLM
        updated_files: List of files to be updated with their content
        chat_id: Optional chat ID (only for API responses)
        source: Whether response came from local model or API
    """
    summary: str
    updated_files: List[Dict[str, Any]]
    chat_id: Optional[int] = None
    source: LLMSource = LLMSource.API

@dataclass
class FileChanges:
    """
    Represents changes in the project files since the last scan.

    Attributes:
        added: List of file paths that are new.
        modified: List of file paths that have been modified.
        deleted: List of file paths that have been deleted.
    """
    added: List[str]
    modified: List[str]
    deleted: List[str]

@dataclass
class VectorIndexResult:
    """
    Represents a single item returned from a vector index search.

    Attributes:
        file_path: The path to the source file.
        content: The original text content of the code chunk.
        score: The similarity score (higher is better).
    """
    file_path: str
    content: str
    score: float
